from test_integration import (
    account as account,
    blink as blink,
    custom_integrations as custom_integrations,
)

"""The card loads by itself and the sidebar page follows the accounts' settings."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from custom_components.blink_plus import panel

FRONTEND = Path(panel.__file__).parent / "frontend"


def with_frontend(hass):
    """Pretend Home Assistant's frontend is running and capture what is registered with it."""
    frontend = MagicMock()
    hass.config.components.add("frontend")
    return patch.object(panel, "_frontend", return_value=frontend), frontend


async def test_nothing_is_registered_without_the_frontend(hass, account, blink):
    assert "frontend" not in hass.config.components
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    assert await panel.async_register_frontend(hass) is False
    assert not hass.data.get(panel.DATA_PANEL)
    await hass.config_entries.async_unload(account.entry_id)


async def test_card_is_loaded_on_every_dashboard_with_its_version(hass):
    patcher, frontend = with_frontend(hass)
    with patcher:
        assert await panel.async_register_frontend(hass) is True
    (called_hass, url), _ = frontend.add_extra_js_url.call_args
    assert called_hass is hass
    assert url.startswith("/blink_plus/frontend/blink-plus-camera-card.js?v=")
    assert url.split("?v=")[1]
    assert (FRONTEND / "blink-plus-camera-card.js").is_file()


async def test_sidebar_page_appears_with_an_account_and_goes_with_it(
    hass, account, blink
):
    patcher, frontend = with_frontend(hass)
    with patcher:
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
        frontend.async_register_built_in_panel.assert_called_once()
        kwargs = frontend.async_register_built_in_panel.call_args.kwargs
        assert kwargs["component_name"] == "custom"
        assert kwargs["frontend_url_path"] == "blink-plus"
        assert kwargs["require_admin"] is False
        custom = kwargs["config"]["_panel_custom"]
        assert custom["name"] == "blink-plus-panel"
        assert custom["module_url"].startswith(
            "/blink_plus/frontend/blink-plus-panel.js?v="
        )
        assert custom["embed_iframe"] is False and custom["trust_external"] is False
        assert (FRONTEND / "blink-plus-panel.js").is_file()

        await hass.config_entries.async_unload(account.entry_id)
        await hass.async_block_till_done()
    frontend.async_remove_panel.assert_called_once_with(hass, "blink-plus")
    assert hass.data[panel.DATA_PANEL] is False


async def test_sidebar_page_can_be_switched_off(hass, account, blink):
    patcher, frontend = with_frontend(hass)
    with patcher:
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
        assert frontend.async_register_built_in_panel.call_count == 1
        # Changing an option reloads the account.
        hass.config_entries.async_update_entry(
            account, options={"sidebar_panel": False}
        )
        await hass.async_block_till_done()
        assert frontend.async_remove_panel.call_count == 1
        assert frontend.async_register_built_in_panel.call_count == 1
        assert hass.data[panel.DATA_PANEL] is False

        hass.config_entries.async_update_entry(account, options={"sidebar_panel": True})
        await hass.async_block_till_done()
        assert frontend.async_register_built_in_panel.call_count == 2
        await hass.config_entries.async_unload(account.entry_id)


async def test_sidebar_option_is_offered_and_saved(hass, account, blink):
    from homeassistant.data_entry_flow import FlowResultType

    await hass.config_entries.async_setup(account.entry_id)
    result = await hass.config_entries.options.async_init(account.entry_id)
    assert any(str(key) == "sidebar_panel" for key in result["data_schema"].schema)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"sidebar_panel": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert account.options["sidebar_panel"] is False
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(account.entry_id)


def test_frontend_files_are_shipped_and_wired_together():
    card = (FRONTEND / "blink-plus-camera-card.js").read_text(encoding="utf-8")
    page = (FRONTEND / "blink-plus-panel.js").read_text(encoding="utf-8")
    assert 'customElements.define("blink-plus-camera-card"' in card
    assert 'customElements.define("blink-plus-panel"' in page
    # The card plays through its own bundled player and Home Assistant's documented stream call,
    # not through Home Assistant's internal camera player.
    assert (FRONTEND / "hls.min.js").stat().st_size > 100_000
    assert '"/blink_plus/frontend/hls.min.js"' in card
    assert 'type: "camera/stream"' in card
    for internal in ("ha-camera-stream", "hui-image", "ha-hls-player"):
        assert internal not in card and internal not in page
    # One automatic load brings push-to-talk along; the page never starts video by itself.
    assert 'import("./blink-plus-ptt.js")' in card
    assert "autoplay: false" in page
    # A viewer can end a live view by hand.
    assert '"Stop live view"' in card and "end()" in card
    # Classic-script syntax checks in CI reject static imports.
    for source in (card, page):
        assert not any(
            line.startswith("import ") and "(" not in line
            for line in source.splitlines()
        )
