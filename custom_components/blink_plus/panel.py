"""Blink Plus dashboard code, available without any manual setup.

Home Assistant normally needs a dashboard Resource for every custom card. This module instead
asks the frontend to load the integration's card on every page, and adds a ready-made
"Blink Plus" page to the sidebar. Both need the frontend integration, which every normal
installation has; without it (some test setups) they are skipped.
"""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.loader import async_get_integration

from .const import DEFAULTS, DOMAIN

FRONTEND_BASE = "/blink_plus/frontend"
PANEL_PATH = "blink-plus"
PANEL_ELEMENT = "blink-plus-panel"
DATA_PANEL = f"{DOMAIN}_sidebar_panel"


def _frontend(hass):
    """Home Assistant's frontend integration, or None when it is not running."""
    if "frontend" not in hass.config.components:
        return None
    from homeassistant.components import frontend

    return frontend


async def _version(hass):
    return str((await async_get_integration(hass, DOMAIN)).version)


async def async_register_frontend(hass):
    """Load the camera card (which loads push-to-talk) on every dashboard."""
    frontend = _frontend(hass)
    if frontend is None:
        return False
    # The version in the address makes browsers fetch the new file after an update.
    frontend.add_extra_js_url(
        hass, f"{FRONTEND_BASE}/blink-plus-camera-card.js?v={await _version(hass)}"
    )
    return True


def panel_wanted(hass, skip=None):
    """Whether any working account has the sidebar page switched on."""
    return any(
        entry is not skip
        and not entry.disabled_by
        and entry.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
        and {**DEFAULTS, **entry.options}["sidebar_panel"]
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


async def async_update_panel(hass, skip=None):
    """Add or remove the sidebar page to match the accounts' settings."""
    frontend = _frontend(hass)
    if frontend is None:
        return
    wanted = panel_wanted(hass, skip)
    shown = hass.data.get(DATA_PANEL, False)
    if wanted and not shown:
        frontend.async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Blink Plus",
            sidebar_icon="mdi:cctv",
            frontend_url_path=PANEL_PATH,
            config={
                "_panel_custom": {
                    "name": PANEL_ELEMENT,
                    "module_url": f"{FRONTEND_BASE}/blink-plus-panel.js?v={await _version(hass)}",
                    "embed_iframe": False,
                    "trust_external": False,
                }
            },
            require_admin=False,
        )
        hass.data[DATA_PANEL] = True
    elif shown and not wanted:
        frontend.async_remove_panel(hass, PANEL_PATH)
        hass.data[DATA_PANEL] = False
