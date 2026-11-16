"""Lighting discovery and service failure behavior."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from test_integration import (
    account as account,
)
from test_integration import (
    blink as blink,
)
from test_integration import (
    custom_integrations as custom_integrations,
)


@pytest.mark.parametrize("product", ["superior", "hawk", "owl", "catalina"])
async def test_lighting_discovery(hass, account, blink, product):
    camera = blink.cameras["Test camera"]
    camera.product_type = product
    camera.async_set_floodlight = AsyncMock(return_value={"id": 1})
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("light.test_camera_floodlight")
    if product == "superior":
        assert state.state == "unknown"
        assert state.attributes["assumed_state"] is True
        for service, enabled in [("turn_on", True), ("turn_off", False)]:
            await hass.services.async_call(
                "light", service, {"entity_id": state.entity_id}, blocking=True
            )
            camera.async_set_floodlight.assert_awaited_with(enabled)
            assert hass.states.get(state.entity_id).state == (
                "on" if enabled else "off"
            )
        for rejected in [None, False, {"code": 307}, {"code": 403}]:
            camera.async_set_floodlight.return_value = rejected
            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    "light", "turn_on", {"entity_id": state.entity_id}, blocking=True
                )
            assert hass.states.get(state.entity_id).state == "off"
    else:
        assert state is None
        camera.async_set_floodlight.assert_not_awaited()
    await hass.config_entries.async_unload(account.entry_id)


async def test_missing_library_method_skips_light(hass, account, blink):
    blink.cameras["Test camera"].product_type = "superior"
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("light.test_camera_floodlight") is None
    await hass.config_entries.async_unload(account.entry_id)
