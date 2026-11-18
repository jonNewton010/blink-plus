"""Protocol and data tests; run with python -m unittest discover -s tests -v."""

import asyncio
from hashlib import sha256
import importlib.util
from pathlib import Path
import ssl
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "blink_plus"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


transport = load("transport")
values = load("values")


class Writer:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed

    async def wait_closed(self):
        pass


class LiveTlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stream = SimpleNamespace(
            target=urlparse("immis://relay.example:443/live"),
            target_reader=None,
            target_writer=None,
            get_auth_header=Mock(return_value=b"session-token"),
        )

    async def test_sends_auth_only_after_verified_tls(self):
        reader = asyncio.StreamReader()
        writer = Writer()
        expected_verify_mode = ssl.CERT_REQUIRED
        event_loop_thread = threading.get_ident()
        create_default_context = ssl.create_default_context

        def load_trust_store():
            self.assertNotEqual(threading.get_ident(), event_loop_thread)
            return create_default_context()

        async def connect(host, port, *, ssl, server_hostname):
            self.assertEqual((host, port), ("relay.example", 443))
            self.assertEqual(server_hostname, host)
            self.assertEqual(ssl.verify_mode, expected_verify_mode)
            self.assertTrue(ssl.check_hostname)
            self.stream.get_auth_header.assert_not_called()
            return reader, writer

        with (
            patch.object(transport.ssl, "create_default_context", new=load_trust_store),
            patch.object(transport.asyncio, "open_connection", new=connect),
        ):
            await transport._authenticate_livestream(self.stream)

        self.assertIs(self.stream.target_reader, reader)
        self.assertIs(self.stream.target_writer, writer)
        self.assertEqual(writer.data, b"session-token")

    async def test_certificate_failure_does_not_send_auth_or_retry(self):
        connect = AsyncMock(side_effect=ssl.SSLCertVerificationError("untrusted relay"))
        with patch.object(transport.asyncio, "open_connection", new=connect):
            with self.assertRaises(ssl.SSLCertVerificationError):
                await transport._authenticate_livestream(self.stream)

        connect.assert_awaited_once()
        self.stream.get_auth_header.assert_not_called()
        self.assertIsNone(self.stream.target_writer)

    async def test_ip_relay_sends_auth_after_verifying_vendor_certificate(self):
        self.stream.target = urlparse("immis://203.0.113.1:444/live")
        reader = asyncio.StreamReader()
        writer = Writer()
        bundled_certificate = ssl.PEM_cert_to_DER_cert(
            (ROOT / "blink-immis.pem").read_text()
        )
        self.assertEqual(
            sha256(bundled_certificate).hexdigest(),
            "b1bfa71ba445f0de14172a1382db19e8848f93b850eb78bd13fdabc1be7e2481",
        )
        tls = Mock()
        tls.getpeercert.return_value = bundled_certificate
        writer.get_extra_info = Mock(return_value=tls)

        async def connect(host, port, *, ssl, server_hostname):
            self.assertEqual((host, port), ("203.0.113.1", 444))
            self.assertEqual(server_hostname, "*.immedia-semi.com")
            self.assertEqual(ssl.verify_mode, expected_verify_mode)
            self.assertEqual(ssl.get_ca_certs(binary_form=True), [bundled_certificate])
            self.stream.get_auth_header.assert_not_called()
            return reader, writer

        expected_verify_mode = ssl.CERT_REQUIRED
        with patch.object(transport.asyncio, "open_connection", new=connect):
            await transport._authenticate_livestream(self.stream)

        writer.get_extra_info.assert_called_once_with("ssl_object")
        tls.getpeercert.assert_called_once_with(binary_form=True)
        self.assertIs(self.stream.target_reader, reader)
        self.assertIs(self.stream.target_writer, writer)
        self.assertEqual(writer.data, b"session-token")

    async def test_ip_relay_rejects_a_different_leaf_even_after_tls_verification(self):
        self.stream.target = urlparse("immis://[2001:db8::1]/live")
        writer = Writer()
        tls = Mock()
        tls.getpeercert.return_value = b"different leaf certificate"
        writer.get_extra_info = Mock(return_value=tls)
        connect = AsyncMock(return_value=(asyncio.StreamReader(), writer))

        with patch.object(transport.asyncio, "open_connection", new=connect):
            with self.assertRaisesRegex(
                ssl.SSLCertVerificationError, "Unexpected Blink relay certificate"
            ):
                await transport._authenticate_livestream(self.stream)

        connect.assert_awaited_once()
        self.stream.get_auth_header.assert_not_called()
        self.assertIsNone(self.stream.target_writer)
        self.assertTrue(writer.closed)
        self.assertFalse(writer.data)

    async def test_relay_logs_verify_code_without_exception_text(self):
        error = ssl.SSLCertVerificationError("private relay details")
        error.verify_code = 62
        self.stream.feed = AsyncMock(side_effect=error)
        self.stream.recv = AsyncMock()
        self.stream.stop = Mock()
        self.stream.clients = []
        relay = transport.NativeRelay(object())
        relay.clients.add(Writer())

        with (
            patch.object(
                transport,
                "init_livestream",
                new=AsyncMock(return_value=self.stream),
            ),
            self.assertLogs(transport.__name__, level="WARNING") as logs,
        ):
            await relay._run()

        self.assertEqual(
            logs.records[0].getMessage(),
            "Blink live session ended (SSLCertVerificationError, verify_code=62)",
        )


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        opener = patch.object(
            transport,
            "init_livestream",
            new=lambda camera: camera.init_livestream(),
        )
        opener.start()
        self.addCleanup(opener.stop)

    async def test_fragmented_headers_and_payloads(self):
        reader = asyncio.StreamReader()
        writer = Writer()
        target = Writer()
        stream = SimpleNamespace(
            target_reader=reader, target_writer=target, clients=[writer]
        )
        payload = b"\x47" + b"x" * 187
        packet = b"\0" * 5 + len(payload).to_bytes(4, "big") + payload
        task = asyncio.create_task(transport.recv_packets(stream))
        for byte in packet:
            reader.feed_data(bytes([byte]))
            await asyncio.sleep(0)
        reader.feed_eof()
        await task
        self.assertEqual(writer.data, payload)
        self.assertTrue(target.closed)

    async def test_truncated_packet_is_not_forwarded(self):
        reader = asyncio.StreamReader()
        writer = Writer()
        target = Writer()
        reader.feed_data(b"\0" * 5 + (188).to_bytes(4, "big") + b"\x47")
        reader.feed_eof()
        await transport.recv_packets(
            SimpleNamespace(
                target_reader=reader, target_writer=target, clients=[writer]
            )
        )
        self.assertFalse(writer.data)
        self.assertTrue(target.closed)

    async def test_oversized_packet_rejected(self):
        reader = asyncio.StreamReader()
        target = Writer()
        reader.feed_data(b"\0" * 5 + (transport.MAX_PAYLOAD + 1).to_bytes(4, "big"))
        with self.assertRaises(ValueError):
            await transport.recv_packets(
                SimpleNamespace(target_reader=reader, target_writer=target, clients=[])
            )
        self.assertTrue(target.closed)

    async def test_accessory_is_not_video(self):
        reader = asyncio.StreamReader()
        writer = Writer()
        target = Writer()
        payload = b"\0" * 4 + bytes([99, 246, 0])
        reader.feed_data(
            b"\x15" + (2).to_bytes(4, "big") + len(payload).to_bytes(4, "big") + payload
        )
        reader.feed_eof()
        stream = SimpleNamespace(
            target_reader=reader, target_writer=target, clients=[writer]
        )
        await transport.recv_packets(stream)
        self.assertEqual(stream.position, {"pan": 99, "tilt": -10})
        self.assertFalse(writer.data)

    async def test_source_does_not_wake_camera_and_is_stable(self):
        relay = transport.NativeRelay(SimpleNamespace())
        try:
            source = await relay.source()
            self.assertEqual(source, await relay.source())
            self.assertIn("127.0.0.1", source)
            self.assertEqual(relay.starts, 0)
        finally:
            await relay.close()
        self.assertFalse(relay.handlers)
        with self.assertRaises(RuntimeError):
            await relay.source()

    async def test_startup_failure_closes_client(self):
        class Camera:
            async def init_livestream(self):
                raise OSError("secret must not reach logs")

        relay = transport.NativeRelay(Camera(), cooldown=1)
        try:
            port = int((await relay.source()).rsplit(":", 1)[1])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
            writer.close()
            await writer.wait_closed()
            self.assertEqual(relay.last_error, "OSError")
            self.assertEqual(relay.starts, 0)
        finally:
            await relay.close()

    async def test_incompatible_blinkpy_is_reported(self):
        class Camera:
            async def init_livestream(self):
                return SimpleNamespace(feed=None, stop=None)

        relay = transport.NativeRelay(Camera(), cooldown=1)
        try:
            port = int((await relay.source()).rsplit(":", 1)[1])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b"")
            writer.close()
            await writer.wait_closed()
            self.assertEqual(relay.last_error, "incompatible_blinkpy")
            self.assertEqual(relay.starts, 0)
        finally:
            await relay.close()

    async def test_lamp_reports_reach_the_relay_and_are_not_positions(self):
        reader = asyncio.StreamReader()
        seen = []
        position = b"\0" * 4 + bytes([99, 246, 0])
        reader.feed_data(
            b"\x15"
            + (0).to_bytes(4, "big")
            + bytes(4)  # session opens: lamp off, no payload
            + b"\x15"
            + (1).to_bytes(4, "big")
            + (4).to_bytes(4, "big")
            + bytes(4)  # lamp on
            + b"\x15"
            + (2).to_bytes(4, "big")
            + len(position).to_bytes(4, "big")
            + position
            + b"\x15"
            + (7).to_bytes(4, "big")
            + bytes(4)  # something else: ignored
        )
        reader.feed_eof()
        stream = SimpleNamespace(
            target_reader=reader,
            target_writer=Writer(),
            clients=[],
            light_report=seen.append,
        )
        await transport.recv_packets(stream)
        self.assertEqual(seen, [False, True])
        self.assertEqual(stream.position, {"pan": 99, "tilt": -10})

    async def test_light_command_on_the_live_session(self):
        relay = transport.NativeRelay(SimpleNamespace())
        heard = []
        relay.light_listeners.add(heard.append)
        self.assertFalse(relay.live)
        with self.assertRaises(RuntimeError):
            await relay.set_light(True)
        writer = Writer()
        relay.stream = SimpleNamespace(target_writer=writer)
        self.assertTrue(relay.live)
        relay._light_reported(False)  # what the camera says as the session opens

        async def confirm_on():
            relay._light_reported(True)

        writer.drain = confirm_on
        await relay.set_light(True)
        on = b"\x14" + (1).to_bytes(4, "big") + bytes(4)
        self.assertEqual(bytes(writer.data), on)
        self.assertIs(relay.light, True)
        self.assertEqual(heard, [False, True])

        async def confirm_off():
            relay._light_reported(False)

        writer.drain = confirm_off
        await relay.set_light(False)
        off = b"\x14" + (2).to_bytes(4, "big") + bytes(4)
        self.assertEqual(bytes(writer.data), on + off)

        # Already off: the command is sent, and no new report is waited for.
        async def silent():
            pass

        writer.drain = silent
        await asyncio.wait_for(relay.set_light(False), 1)
        self.assertEqual(bytes(writer.data), on + off + off)

    async def test_light_command_needs_the_cameras_confirmation(self):
        relay = transport.NativeRelay(SimpleNamespace())
        relay.stream = SimpleNamespace(target_writer=Writer())
        relay._light_reported(False)
        with patch.object(transport.asyncio, "wait_for", side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                await relay.set_light(True)

    async def test_talk_start_audio_stop_sequence(self):
        relay = transport.NativeRelay(SimpleNamespace())
        writer = Writer()
        relay.stream = SimpleNamespace(target_writer=writer)

        async def drain():
            relay.talk_ack.set()

        writer.drain = drain
        frame = b"\xff\xf1" + b"\x00" * 20
        await relay.start_talk()
        await relay.send_audio(frame)
        await relay.send_audio(frame)
        await relay.stop_talk()
        # 0x17 start (control seq 3), audio 0x05 seq 0 then 1, 0x17 stop (control seq 4).
        start = b"\x17" + (3).to_bytes(4, "big") + (0).to_bytes(4, "big")
        a0 = b"\x05" + (0).to_bytes(4, "big") + len(frame).to_bytes(4, "big") + frame
        a1 = b"\x05" + (1).to_bytes(4, "big") + len(frame).to_bytes(4, "big") + frame
        stop = b"\x17" + (4).to_bytes(4, "big") + (0).to_bytes(4, "big")
        self.assertEqual(bytes(writer.data), start + a0 + a1 + stop)

    async def test_talk_requires_an_open_session(self):
        relay = transport.NativeRelay(SimpleNamespace())
        with self.assertRaises(RuntimeError):
            await relay.send_audio(b"\xff\xf1\x00\x00")
        relay.stream = SimpleNamespace(target_writer=None)
        with self.assertRaises(RuntimeError):
            await relay.send_audio(b"\xff\xf1\x00\x00")

    async def test_pan_tilt_requires_active_session(self):
        relay = transport.NativeRelay(SimpleNamespace())
        with self.assertRaises(RuntimeError):
            await relay.pan_tilt("right")

    async def test_pan_tilt_packet_signed_and_inverted(self):
        relay = transport.NativeRelay(SimpleNamespace())
        writer = Writer()
        relay.stream = SimpleNamespace(
            target_writer=writer, position={"pan": 84, "tilt": -100}
        )
        await relay.pan_tilt("right")
        self.assertEqual(writer.data[:5], b"\x14\0\0\0\x03")
        self.assertEqual(writer.data[13], 69)
        self.assertEqual(writer.data[14], 156)

    async def test_two_viewers_share_one_session_and_reopen(self):
        class Stream:
            target_reader = None
            target_writer = None

            async def recv(self):
                pass

            def __init__(self):
                self.clients = []

            async def feed(self):
                while True:
                    for writer in tuple(self.clients):
                        writer.write(b"\x47" + b"x" * 187)
                        await writer.drain()
                    await asyncio.sleep(0.01)

            def stop(self):
                for writer in tuple(self.clients):
                    writer.close()

        class Camera:
            def __init__(self):
                self.calls = 0

            async def init_livestream(self):
                self.calls += 1
                return Stream()

        camera = Camera()
        relay = transport.NativeRelay(camera, cooldown=5)
        writers = []
        try:
            port = int((await relay.source()).rsplit(":", 1)[1])
            r1, w1 = await asyncio.open_connection("127.0.0.1", port)
            writers.append(w1)
            r2, w2 = await asyncio.open_connection("127.0.0.1", port)
            writers.append(w2)
            self.assertTrue(await asyncio.wait_for(r1.read(188), 1))
            self.assertTrue(await asyncio.wait_for(r2.read(188), 1))
            self.assertEqual(camera.calls, 1)
            w1.close()
            await w1.wait_closed()
            await asyncio.sleep(0.03)
            self.assertIsNotNone(relay.runner)
            w2.close()
            await w2.wait_closed()
            for _ in range(30):
                if relay.runner is None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNone(relay.runner)
            # A normal close does not start the cooldown: reopening works at once.
            r3, w3 = await asyncio.open_connection("127.0.0.1", port)
            writers.append(w3)
            self.assertTrue(await asyncio.wait_for(r3.read(188), 1))
            self.assertEqual(camera.calls, 2)
        finally:
            for writer in writers:
                writer.close()
            await relay.close()
        self.assertFalse(relay.handlers)

    async def test_session_duration_closes_viewer(self):
        class Stream:
            target_reader = None
            target_writer = None

            async def recv(self):
                pass

            def __init__(self):
                self.clients = []

            async def feed(self):
                await asyncio.sleep(10)

            def stop(self):
                for writer in self.clients:
                    writer.close()

        class Camera:
            calls = 0

            async def init_livestream(self):
                self.calls += 1
                return Stream()

        camera = Camera()
        relay = transport.NativeRelay(camera, duration=0.03, cooldown=5)
        try:
            port = int((await relay.source()).rsplit(":", 1)[1])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
            self.assertEqual(relay.last_error, "session_limit")
            writer.close()
            await writer.wait_closed()
            for _ in range(30):
                if relay.runner is None:
                    break
                await asyncio.sleep(0.01)
            # The time limit starts the cooldown: a reconnect is refused.
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
            writer.close()
            await writer.wait_closed()
            self.assertEqual(camera.calls, 1)
        finally:
            await relay.close()


class TalkReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_talk_reply_wakes_waiter(self):
        reader = asyncio.StreamReader()
        acknowledgement = asyncio.Event()
        target = Writer()
        reader.feed_data(b"\x18" + (4).to_bytes(4, "big") + bytes(4))
        reader.feed_eof()
        await transport.recv_packets(
            SimpleNamespace(
                target_reader=reader,
                target_writer=target,
                clients=[],
                talk_ack=acknowledgement,
            )
        )
        self.assertTrue(acknowledgement.is_set())

    async def test_talk_requires_a_fresh_camera_reply(self):
        relay = transport.NativeRelay(SimpleNamespace())
        relay.stream = SimpleNamespace(target_writer=Writer())
        relay.talk_ack.set()
        task = asyncio.create_task(relay.start_talk())
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertFalse(relay.talk_ack.is_set())
        relay.talk_ack.set()
        await asyncio.wait_for(task, 1)


class RepeatedTalkTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_holds_use_start_stop_control_ids_and_reset_audio(self):
        relay = transport.NativeRelay(SimpleNamespace())
        writer = Writer()
        relay.stream = SimpleNamespace(target_writer=writer)

        async def acknowledge():
            relay.talk_ack.set()

        writer.drain = acknowledge
        frame = b"test audio"
        for _ in range(3):
            await relay.start_talk()
            await relay.send_audio(frame)
            await relay.stop_talk()
        data = bytes(writer.data)
        packets = []
        while data:
            length = int.from_bytes(data[5:9], "big")
            packets.append((data[0], int.from_bytes(data[1:5], "big")))
            data = data[9 + length :]
        self.assertEqual(packets, [(0x17, 3), (0x05, 0), (0x17, 4)] * 3)


class ValueTests(unittest.TestCase):
    def test_voltage_is_hundredths(self):
        self.assertEqual(
            values.sensor_value(
                SimpleNamespace(battery_voltage=173), "battery_voltage"
            ),
            1.73,
        )

    def test_no_invented_voltage(self):
        self.assertIsNone(values.sensor_value(SimpleNamespace(), "battery_voltage"))

    def test_calibrated_temperature(self):
        self.assertEqual(
            values.sensor_value(
                SimpleNamespace(temperature=80, temperature_calibrated=82),
                "temperature",
            ),
            82,
        )

    def test_temperature_zero_is_valid(self):
        self.assertEqual(
            values.sensor_value(
                SimpleNamespace(temperature=0, temperature_calibrated=0), "temperature"
            ),
            0,
        )

    def test_battery_bars_stay_bars(self):
        self.assertEqual(
            values.sensor_value(SimpleNamespace(battery_level=3), "battery_level"), 3
        )

    def test_missing_battery_is_not_ok(self):
        self.assertIsNone(values.binary_value(SimpleNamespace(), "battery_low"))

    def test_low_battery(self):
        self.assertTrue(
            values.binary_value(SimpleNamespace(battery="low"), "battery_low")
        )
        self.assertFalse(
            values.binary_value(SimpleNamespace(battery="ok"), "battery_low")
        )

    def test_motion_false_is_supported(self):
        self.assertIs(
            values.binary_value(
                SimpleNamespace(motion_detected=False), "motion_detected"
            ),
            False,
        )

    def test_bad_timestamp_not_reported(self):
        self.assertIsNone(
            values.sensor_value(SimpleNamespace(last_record="unknown"), "last_record")
        )

    def test_timestamp_timezone(self):
        self.assertIsNotNone(
            values.sensor_value(
                SimpleNamespace(last_record="2026-11-10T12:00:00Z"), "last_record"
            ).tzinfo
        )


if __name__ == "__main__":
    unittest.main()
