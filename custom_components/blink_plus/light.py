"""Camera lights: Wired Floodlight lamps and the Mini 2 spotlight."""

import asyncio
import logging

from blinkpy import api
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .coordinator import blink_busy
from .entity import BlinkEntity

_LOGGER = logging.getLogger(__name__)

# Product type -> (unique-id key, entity name). Both use the same Blink command.
LIGHTS = {
    "superior": ("floodlight", "Floodlight"),
    "hawk": ("spotlight", "Spotlight"),
}
# While a live view is open Blink answers its lights request "System is busy", so the lamp is
# switched on the live session itself (transport.NativeRelay.set_light), as the Blink app does.
LIVE_COMMAND_SECONDS = 10
# Blink publishes a command's result a few seconds later; read twice to be sure.
AFTER_COMMAND_SECONDS = (6, 20)
# A lit lamp switches itself off (timer, motion ended); follow it for a while.
WHILE_ON_SECONDS = 30
WHILE_ON_CHECKS = 20
STATUS_TIMEOUT_SECONDS = 20


def light_status(config):
    """The lamp state in Blink's camera settings: True, False, or None if absent."""
    if not isinstance(config, dict):
        return None
    status = config.get("light_status")
    if status is None and isinstance(config.get("superior"), dict):
        status = config["superior"].get("light_status")
    return {"on": True, "off": False}.get(status)


async def async_setup_entry(hass, entry, async_add_entities):
    if not callable(getattr(api, "request_floodlight", None)):
        return
    async_add_entities(
        CameraLight(entry.runtime_data, camera, *LIGHTS[camera.product_type])
        for camera in entry.runtime_data.api.cameras.values()
        if camera.product_type in LIGHTS
    )


class CameraLight(BlinkEntity, LightEntity):
    """On/off lamp whose state is the one Blink reports for the camera."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator, camera, key, name):
        super().__init__(coordinator, camera, key, name)
        self._enabled = None
        self._reported = False
        self._pending = []
        self._timer = None
        self._on_checks = 0
        self._reading = False

    @property
    def is_on(self):
        return self._enabled

    @property
    def assumed_state(self):
        # Until Blink has reported the lamp, the state is the last command sent.
        return not self._reported

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # During live view the camera reports its lamp on the session; show that at once.
        self.coordinator.light_listeners.setdefault(str(self.camera.serial), set()).add(
            self._live_report
        )
        self._start_read()

    async def async_will_remove_from_hass(self):
        self._cancel()
        self.coordinator.light_listeners.get(str(self.camera.serial), set()).discard(
            self._live_report
        )
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self):
        self._start_read()
        super()._handle_coordinator_update()

    @callback
    def _live_report(self, on):
        self._reported = True
        if on is not self._enabled:
            self._enabled = on
            self.async_write_ha_state()

    def _cancel(self):
        self._pending = []
        if self._timer:
            self._timer()
            self._timer = None

    def _schedule(self, delays):
        """Replace any planned status reads with these."""
        self._cancel()
        self._pending = list(delays)
        self._arm()

    def _arm(self):
        if self._timer is None and self._pending:
            self._timer = async_call_later(
                self.hass, self._pending.pop(0), self._timer_fired
            )

    @callback
    def _timer_fired(self, _now):
        self._timer = None
        self._start_read()

    def _start_read(self):
        if not self._reading:
            self.coordinator.config_entry.async_create_task(
                self.hass, self._read_status(), eager_start=False
            )

    async def _read_status(self):
        """Read the lamp state; a failed read keeps the last known state."""
        self._reading = True
        try:
            async with asyncio.timeout(STATUS_TIMEOUT_SECONDS):
                config = await api.request_get_config(
                    self.coordinator.api,
                    self.camera.network_id,
                    self.camera.camera_id,
                    "owl",
                )
            status = light_status(config)
        except Exception as err:  # noqa: BLE001 - never fail on a status read
            # Only the class name: exception text can carry URLs or tokens.
            _LOGGER.debug("Blink light status read failed (%s)", type(err).__name__)
            status = None
        finally:
            self._reading = False
        if status is not None:
            self._reported = True
            if status is not self._enabled:
                self._enabled = status
                self.async_write_ha_state()
        if (
            self._enabled
            and not self._pending
            and self._on_checks < WHILE_ON_CHECKS
            and self.hass is not None
        ):
            self._on_checks += 1
            self._pending.append(WHILE_ON_SECONDS)
        elif not self._enabled:
            self._on_checks = 0
        if self.hass is not None:
            self._arm()

    async def _set_enabled(self, enabled):
        async def request():
            result = await api.request_floodlight(
                self.coordinator.api,
                self.camera.network_id,
                self.camera.camera_id,
                enabled,
            )
            if blink_busy(result):
                raise HomeAssistantError(
                    "Blink is busy; close live views and try again in a moment"
                )
            if isinstance(result, dict) and result.get("code", 200) != 200:
                raise HomeAssistantError("Blink did not accept the lighting request")
            return result

        relay = self.coordinator.relays.get(str(self.camera.serial))
        if relay is not None and relay.live:
            try:
                async with asyncio.timeout(LIVE_COMMAND_SECONDS):
                    await relay.set_light(enabled)
            except (RuntimeError, OSError, TimeoutError):
                # Only fixed text: exception text can carry addresses or tokens.
                raise HomeAssistantError(
                    "The camera did not confirm the light during live view; try again"
                ) from None
        else:
            await self.coordinator.command(request)
        self._enabled = enabled
        self._on_checks = 0
        self.async_write_ha_state()
        self._schedule(AFTER_COMMAND_SECONDS)

    async def async_turn_on(self, **kwargs):
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self._set_enabled(False)
