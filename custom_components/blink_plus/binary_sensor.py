"""Motion, low battery and connectivity where reported."""

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from .entity import BlinkEntity
from .values import binary_value

TYPES = {
    "motion_detected": ("Motion", BinarySensorDeviceClass.MOTION),
    "battery_low": ("Battery", BinarySensorDeviceClass.BATTERY),
    "online": ("Connectivity", BinarySensorDeviceClass.CONNECTIVITY),
}
# Motion stays on this long after Blink lists a new clip for the camera.
MOTION_HOLD_SECONDS = 120


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    def discover():
        entities = []
        for camera in coordinator.api.cameras.values():
            for key in TYPES:
                identity = (camera.serial, key)
                if identity not in known and binary_value(camera, key) is not None:
                    known.add(identity)
                    kind = (
                        BlinkMotionSensor
                        if key == "motion_detected"
                        else BlinkBinarySensor
                    )
                    entities.append(kind(coordinator, camera, key))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class BlinkBinarySensor(BlinkEntity, BinarySensorEntity):
    def __init__(self, coordinator, camera, key):
        name, device_class = TYPES[key]
        super().__init__(coordinator, camera, key, name)
        self.key = key
        self._attr_device_class = device_class

    @property
    def is_on(self):
        return binary_value(self.camera, self.key)


class BlinkMotionSensor(BlinkBinarySensor):
    """On for a while whenever Blink lists a newer clip for the camera.

    blinkpy's own motion flag only covers a clip that started after the previous
    refresh, and the next refresh clears it. A snapshot or any other command
    refreshes twice before Home Assistant is told, so the flag was usually gone
    again before it could be seen, and a clip Blink listed late never set it.
    The coordinator keeps each camera's newest clip time instead.
    """

    def __init__(self, coordinator, camera, key):
        super().__init__(coordinator, camera, key)
        # Clips already listed at startup are not new motion.
        self._clip = coordinator.clip_times.get(camera.name)
        self._held = False
        self._release = None

    @property
    def is_on(self):
        return self._held or bool(binary_value(self.camera, self.key))

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_release)

    def _cancel_release(self):
        if self._release:
            self._release()
            self._release = None

    @callback
    def _clear(self, _now):
        self._release = None
        self._held = False
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self):
        clip = self.coordinator.clip_times.get(self.camera.name)
        if clip is not None and (self._clip is None or clip > self._clip):
            self._clip = clip
            self._held = True
            self._cancel_release()
            self._release = async_call_later(
                self.hass, MOTION_HOLD_SECONDS, self._clear
            )
        super()._handle_coordinator_update()
