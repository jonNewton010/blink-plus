"""Lighting discovery, reported state and service failure behavior."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
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

from custom_components.blink_plus import light as module

NAMES = {"superior": "floodlight", "hawk": "spotlight"}


@pytest.fixture
def cloud():
    """Blink's lighting calls; the lamp reports whatever was last commanded."""
    lamp = {"status": "off"}

    async def command(blink, network, camera_id, enable):
        lamp["status"] = "on" if enable else "off"
        return {"id": 1, "command": "accessory_lights_on"}

    async def config(blink, network, camera_id, product_type):
        return {"light_status": lamp["status"]}

    with (
        patch.object(
            module.api, "request_floodlight", AsyncMock(side_effect=command)
        ) as request,
        patch.object(
            module.api, "request_get_config", AsyncMock(side_effect=config)
        ) as read,
    ):
        yield lamp, request, read


def camera_of(blink, product):
    camera = blink.cameras["Test camera"]
    camera.product_type = product
    camera.network_id = 11
    camera.camera_id = 22
    return camera


async def later(hass, seconds):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def test_light_status_parsing():
    assert module.light_status({"light_status": "on"}) is True
    assert module.light_status({"light_status": "off"}) is False
    assert module.light_status({"superior": {"light_status": "on"}}) is True
    for absent in (None, [], {}, {"light_status": "dim"}, {"superior": None}):
        assert module.light_status(absent) is None


@pytest.mark.parametrize("product", ["superior", "hawk", "owl", "catalina"])
async def test_lighting_discovery(hass, account, blink, cloud, product):
    lamp, request, read = cloud
    camera_of(blink, product)
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    lights = hass.states.async_entity_ids("light")
    if product in NAMES:
        entity_id = f"light.test_camera_{NAMES[product]}"
        assert lights == [entity_id]
        state = hass.states.get(entity_id)
        # Blink's own report, not an assumption.
        assert state.state == "off"
        assert not state.attributes.get("assumed_state")
        read.assert_awaited_with(blink, 11, 22, "owl")
        for service, enabled in [("turn_on", True), ("turn_off", False)]:
            await hass.services.async_call(
                "light", service, {"entity_id": entity_id}, blocking=True
            )
            request.assert_awaited_with(blink, 11, 22, enabled)
            assert hass.states.get(entity_id).state == ("on" if enabled else "off")
        request.side_effect = None
        for rejected in [None, False, {"code": 307}, {"code": 403}]:
            request.return_value = rejected
            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    "light", "turn_on", {"entity_id": entity_id}, blocking=True
                )
            assert hass.states.get(entity_id).state == "off"
    else:
        assert lights == []
        request.assert_not_awaited()
        read.assert_not_awaited()
    await hass.config_entries.async_unload(account.entry_id)


async def test_light_follows_blink_when_it_switches_itself_off(
    hass, account, blink, cloud
):
    lamp, request, read = cloud
    camera_of(blink, "superior")
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    entity_id = "light.test_camera_floodlight"
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    assert hass.states.get(entity_id).state == "on"
    await later(hass, 7)
    await later(hass, 21)
    assert hass.states.get(entity_id).state == "on"
    # The lamp's own timer ends it; nothing was commanded from Home Assistant.
    lamp["status"] = "off"
    await later(hass, 31)
    assert hass.states.get(entity_id).state == "off"
    before = read.await_count
    await later(hass, 120)
    assert read.await_count == before  # no more follow-up reads once it is off
    await hass.config_entries.async_unload(account.entry_id)


async def test_light_turned_on_elsewhere_is_seen_on_the_next_poll(
    hass, account, blink, cloud
):
    lamp, request, read = cloud
    camera_of(blink, "hawk")
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    entity_id = "light.test_camera_spotlight"
    assert hass.states.get(entity_id).state == "off"
    lamp["status"] = "on"  # motion, or the Blink app
    await account.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"
    request.assert_not_awaited()
    await hass.config_entries.async_unload(account.entry_id)


async def test_follow_up_reads_stop_after_the_limit(hass, account, blink, cloud):
    lamp, request, read = cloud
    lamp["status"] = "on"  # a lamp set to stay on
    camera_of(blink, "superior")
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    for _ in range(module.WHILE_ON_CHECKS + 5):
        await later(hass, module.WHILE_ON_SECONDS + 1)
    assert read.await_count == module.WHILE_ON_CHECKS + 1
    assert hass.states.get("light.test_camera_floodlight").state == "on"
    await hass.config_entries.async_unload(account.entry_id)


@pytest.mark.parametrize("failure", [None, {}, TimeoutError(), RuntimeError("secret")])
async def test_failed_status_read_keeps_state_and_falls_back(
    hass, account, blink, cloud, failure, caplog
):
    lamp, request, read = cloud
    read.side_effect = failure if isinstance(failure, Exception) else None
    read.return_value = failure
    camera_of(blink, "superior")
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    entity_id = "light.test_camera_floodlight"
    state = hass.states.get(entity_id)
    assert state.state == "unknown"
    assert state.attributes["assumed_state"] is True
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    await later(hass, 7)
    state = hass.states.get(entity_id)
    assert state.state == "on"  # the last accepted command
    assert state.attributes["assumed_state"] is True
    assert "secret" not in caplog.text
    await hass.config_entries.async_unload(account.entry_id)


async def test_light_uses_the_live_session_while_live_view_is_open(
    hass, account, blink, cloud
):
    lamp, request, read = cloud
    camera_of(blink, "hawk")
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    entity_id = "light.test_camera_spotlight"
    relay = account.runtime_data.relay(blink.cameras["Test camera"])
    sent = []

    async def set_light(on):
        sent.append(on)
        relay._light_reported(on)  # the camera confirms on the session

    with (
        patch.object(type(relay), "live", property(lambda self: True)),
        patch.object(relay, "set_light", side_effect=set_light),
    ):
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": entity_id}, blocking=True
        )
        assert hass.states.get(entity_id).state == "on"
        # Blink would have answered its normal request with "System is busy".
        request.assert_not_awaited()
        # The camera switching its own lamp shows at once, without a poll.
        relay._light_reported(False)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "off"
        assert not hass.states.get(entity_id).attributes.get("assumed_state")

        async def refuse(on):
            raise RuntimeError("immis://secret-address did not confirm")

        relay.set_light.side_effect = refuse
        with pytest.raises(HomeAssistantError) as raised:
            await hass.services.async_call(
                "light", "turn_on", {"entity_id": entity_id}, blocking=True
            )
        assert "secret" not in str(raised.value)
        assert hass.states.get(entity_id).state == "off"
    assert sent == [True]
    request.assert_not_awaited()
    await hass.config_entries.async_unload(account.entry_id)
    assert not relay.light_listeners


async def test_missing_library_method_skips_light(hass, account, blink):
    camera_of(blink, "superior")
    with patch.object(module.api, "request_floodlight", None):
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
    assert hass.states.async_entity_ids("light") == []
    await hass.config_entries.async_unload(account.entry_id)
