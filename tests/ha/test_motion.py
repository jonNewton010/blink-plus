"""Motion follows Blink's clip list, not blinkpy's one-refresh flag."""

from datetime import datetime, timedelta, timezone

from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import async_fire_time_changed
from test_integration import (
    account as account,
)
from test_integration import (
    blink as blink,
)
from test_integration import (
    custom_integrations as custom_integrations,
)

from custom_components.blink_plus.binary_sensor import MOTION_HOLD_SECONDS
from custom_components.blink_plus.coordinator import newest_clips

MOTION = "binary_sensor.test_camera_motion"


def clip(created_at, name="Test camera", **more):
    return {"device_name": name, "created_at": created_at, **more}


def listed(blink, *rows):
    blink.clip_list.return_value = {"media": list(rows)}


async def started(hass, account, blink, *rows):
    listed(blink, *rows)
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    return account.runtime_data


async def poll(hass, coordinator):
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    return hass.states.get(MOTION).state


async def later(hass, seconds):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def test_newest_clip_per_camera_ignores_bad_rows():
    found = newest_clips(
        [
            clip("2026-01-01T10:00:00+00:00"),
            clip("2026-01-01T10:20:00Z"),
            clip("2026-01-01T10:10:00"),
            clip("2026-01-01T11:00:00+00:00", deleted=True),
            clip("not a time"),
            clip("2026-01-01T09:00:00+00:00", name="Other"),
            {"created_at": "2026-01-01T12:00:00+00:00"},
            "junk",
        ]
    )
    assert found == {
        "Test camera": datetime(2026, 1, 1, 10, 20, tzinfo=timezone.utc),
        "Other": datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
    }
    assert newest_clips(None) == {}


async def test_clip_listed_before_startup_is_not_motion(hass, account, blink):
    coordinator = await started(hass, account, blink, clip("2026-01-01T10:00:00+00:00"))
    assert hass.states.get(MOTION).state == "off"
    assert await poll(hass, coordinator) == "off"


async def test_new_clip_is_motion_without_the_library_flag(hass, account, blink):
    coordinator = await started(hass, account, blink, clip("2026-01-01T10:00:00+00:00"))
    # blinkpy's flag stays off: a second refresh cleared it, or Blink listed the clip late.
    listed(blink, clip("2026-01-01T10:20:00+00:00"), clip("2026-01-01T10:00:00+00:00"))
    assert await poll(hass, coordinator) == "on"
    # The same clip again neither ends nor extends it.
    await later(hass, 30)
    assert await poll(hass, coordinator) == "on"
    await later(hass, MOTION_HOLD_SECONDS + 1)
    assert hass.states.get(MOTION).state == "off"
    assert await poll(hass, coordinator) == "off"
    # An older clip turning up, or another camera's clip, is not motion here.
    listed(
        blink,
        clip("2026-01-01T10:05:00+00:00"),
        clip("2026-01-01T10:30:00+00:00", name="Other"),
    )
    assert await poll(hass, coordinator) == "off"


async def test_first_clip_after_startup_is_motion(hass, account, blink):
    coordinator = await started(hass, account, blink)
    listed(blink, clip("2026-01-01T10:20:00+00:00"))
    assert await poll(hass, coordinator) == "on"


async def test_failed_clip_request_keeps_the_poll_working(hass, account, blink):
    coordinator = await started(hass, account, blink, clip("2026-01-01T10:00:00+00:00"))
    blink.clip_list.side_effect = TimeoutError
    assert await poll(hass, coordinator) == "off"
    assert coordinator.last_update_success
    blink.clip_list.side_effect = None
    blink.clip_list.return_value = None
    assert await poll(hass, coordinator) == "off"
    listed(blink, clip("2026-01-01T10:20:00+00:00"))
    assert await poll(hass, coordinator) == "on"


async def test_library_flag_still_counts(hass, account, blink):
    coordinator = await started(hass, account, blink)
    camera = blink.cameras["Test camera"]
    camera.motion_detected = True
    assert await poll(hass, coordinator) == "on"
    camera.motion_detected = False
    assert await poll(hass, coordinator) == "off"


async def test_command_checks_clips_too(hass, account, blink):
    coordinator = await started(hass, account, blink)
    listed(blink, clip("2026-01-01T10:20:00+00:00"))
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.test_camera_motion_detection"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(MOTION).state == "on"
    assert coordinator.clip_times["Test camera"].minute == 20


async def test_unload_cancels_the_pending_release(hass, account, blink):
    coordinator = await started(hass, account, blink)
    listed(blink, clip("2026-01-01T10:20:00+00:00"))
    assert await poll(hass, coordinator) == "on"
    assert await hass.config_entries.async_unload(account.entry_id)
    await later(hass, MOTION_HOLD_SECONDS + 1)
