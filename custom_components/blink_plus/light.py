"""Manual lighting for cameras supported by blinkpy."""

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.exceptions import HomeAssistantError

from .coordinator import blink_busy
from .entity import BlinkEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        Floodlight(entry.runtime_data, camera)
        for camera in entry.runtime_data.api.cameras.values()
        if camera.product_type == "superior"
        and callable(getattr(camera, "async_set_floodlight", None))
    )


class Floodlight(BlinkEntity, LightEntity):
    """Wired Floodlight on/off; state is the last accepted local command."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_assumed_state = True

    def __init__(self, coordinator, camera):
        super().__init__(coordinator, camera, "floodlight", "Floodlight")
        self._enabled = None

    @property
    def is_on(self):
        return self._enabled

    async def _set_enabled(self, enabled):
        async def request():
            result = await self.camera.async_set_floodlight(enabled)
            if blink_busy(result):
                raise HomeAssistantError(
                    "Blink is busy; close live views and try again in a moment"
                )
            if isinstance(result, dict) and result.get("code", 200) != 200:
                raise HomeAssistantError("Blink did not accept the lighting request")
            return result

        await self.coordinator.command(request)
        self._enabled = enabled
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self._set_enabled(False)
