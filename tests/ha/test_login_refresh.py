from test_integration import (
    blink as blink,
    custom_integrations as custom_integrations,
)

"""A Blink outage is retried; only a token Blink refuses asks for a new login."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientConnectionError
from blinkpy.auth import TokenRefreshFailed
import pytest
from pytest_homeassistant.common import MockConfigEntry
from homeassistant.config_entries import ConfigEntryState

from custom_components.blink_plus import coordinator as module

DOMAIN = "blink_plus"
HARDWARE_ID = "0F0E0D0C-0B0A-4908-8706-050403020100"
REFRESH = "custom_components.blink_plus.coordinator.refresh_tokens"


@pytest.fixture
def account(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="test@example.com",
        unique_id="test@example.com",
        data={
            "username": "test@example.com",
            "token": "old-access",
            "refresh_token": "old-refresh",
            "hardware_id": HARDWARE_ID,
            "host": "rest-test.immedia-semi.com",
            "region_id": "test",
            "account_id": 1,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def stopped(blink):
    """A Blink client that has not been set up yet, as after a restart."""
    blink.available = False
    blink.last_refresh = None
    blink.refresh_rate = 300
    blink.setup_post_verify = AsyncMock(return_value=True)
    return blink


def reauth_flows(hass):
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"].get("source") == "reauth"
    ]


def renewed(auth):
    auth.token = "new-access"
    auth.refresh_token = "new-refresh"
    auth.expires_in = 3600
    auth.expiration_date = time.time() + 3600


def response(status, body=None):
    return SimpleNamespace(
        status=status, json=AsyncMock(return_value=body), release=MagicMock()
    )


def auth_with(reply):
    post = AsyncMock(side_effect=reply if isinstance(reply, Exception) else None)
    if not isinstance(reply, Exception):
        post.return_value = reply
    return SimpleNamespace(
        refresh_token="old-refresh",
        hardware_id=HARDWARE_ID,
        token="old-access",
        expires_in=None,
        expiration_date=None,
        session=SimpleNamespace(post=post),
    )


async def test_refresh_stores_new_tokens():
    auth = auth_with(
        response(
            200,
            {"access_token": "new", "refresh_token": "next", "expires_in": 7200},
        )
    )
    before = time.time()
    await module.refresh_tokens(auth)
    assert (auth.token, auth.refresh_token, auth.expires_in) == ("new", "next", 7200)
    assert auth.expiration_date >= before + 7200
    sent = auth.session.post.call_args
    assert "refresh_token=old-refresh" in sent.kwargs["data"]
    assert "password" not in sent.kwargs["data"]


async def test_refresh_keeps_the_old_refresh_token_when_none_is_returned():
    auth = auth_with(response(200, {"access_token": "new"}))
    await module.refresh_tokens(auth)
    assert (auth.token, auth.refresh_token, auth.expires_in) == (
        "new",
        "old-refresh",
        3600,
    )


@pytest.mark.parametrize("status", [400, 401])
async def test_refused_token_is_rejected(status):
    auth = auth_with(response(status))
    with pytest.raises(module.TokenRejected):
        await module.refresh_tokens(auth)
    assert auth.token == "old-access"


@pytest.mark.parametrize(
    "reply",
    [
        response(500),
        response(503),
        response(429),
        response(200, {"unexpected": "body"}),
        response(200, "not a mapping"),
        ClientConnectionError("secret-bearing text"),
        TimeoutError(),
    ],
)
async def test_outage_is_not_a_rejected_token(reply):
    auth = auth_with(reply)
    with pytest.raises(module.BlinkUnreachable) as raised:
        await module.refresh_tokens(auth)
    assert "secret" not in str(raised.value)
    assert (auth.token, auth.refresh_token) == ("old-access", "old-refresh")


async def test_restart_uses_own_refresh_and_saves_tokens(hass, account, stopped):
    with patch(REFRESH, new=AsyncMock(side_effect=renewed)) as refresh:
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
    refresh.assert_awaited_once()
    stopped.start.assert_not_called()
    stopped.setup_urls.assert_called_once()
    stopped.setup_post_verify.assert_awaited_once()
    assert account.state is ConfigEntryState.LOADED
    assert account.data["token"] == "new-access"
    assert account.data["refresh_token"] == "new-refresh"
    assert account.data["hardware_id"] == HARDWARE_ID
    assert "password" not in account.data
    await hass.config_entries.async_unload(account.entry_id)


async def test_outage_during_restart_retries_without_reauth(hass, account, stopped):
    with patch(REFRESH, new=AsyncMock(side_effect=module.BlinkUnreachable("HTTP 503"))):
        assert not await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
    assert account.state is ConfigEntryState.SETUP_RETRY
    assert not reauth_flows(hass)
    stopped.start.assert_not_called()
    assert account.data["refresh_token"] == "old-refresh"


async def test_refused_token_during_restart_asks_for_login(hass, account, stopped):
    with patch(REFRESH, new=AsyncMock(side_effect=module.TokenRejected)):
        assert not await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
    assert account.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass)) == 1
    stopped.start.assert_not_called()


async def test_failed_setup_after_refresh_retries(hass, account, stopped):
    stopped.setup_post_verify.return_value = False
    with patch(REFRESH, new=AsyncMock(side_effect=renewed)):
        assert not await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
    assert account.state is ConfigEntryState.SETUP_RETRY
    assert not reauth_flows(hass)


async def test_no_response_during_setup_retries(hass, account, stopped):
    # blinkpy returns no response on a connection error and then fails on it.
    stopped.get_homescreen.side_effect = AttributeError
    with patch(REFRESH, new=AsyncMock(side_effect=renewed)):
        assert not await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
    assert account.state is ConfigEntryState.SETUP_RETRY
    assert not reauth_flows(hass)


async def test_entry_without_refresh_token_uses_blinkpy_startup(hass, stopped):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="old@example.com",
        unique_id="old@example.com",
        data={"username": "old@example.com", "token": "not-real"},
    )
    entry.add_to_hass(hass)
    with patch(REFRESH, new=AsyncMock()) as refresh:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    refresh.assert_not_called()
    stopped.start.assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_poll_refreshes_a_token_near_expiry_first(hass, account, stopped):
    with patch(REFRESH, new=AsyncMock(side_effect=renewed)) as refresh:
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
        stopped.available = True
        coordinator = account.runtime_data
        refresh.reset_mock()

        await coordinator.async_refresh()
        refresh.assert_not_called()  # an hour left
        assert stopped.refresh.await_count == 1

        coordinator.api.auth.expiration_date = time.time() + 200
        order = []
        refresh.side_effect = lambda auth: (order.append("tokens"), renewed(auth))[1]
        stopped.refresh.side_effect = lambda *a, **k: order.append("data")
        await coordinator.async_refresh()
    assert order == ["tokens", "data"]
    assert coordinator.last_update_success
    await hass.config_entries.async_unload(account.entry_id)


async def test_outage_while_running_retries_without_reauth(hass, account, stopped):
    with patch(REFRESH, new=AsyncMock(side_effect=renewed)) as refresh:
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
        stopped.available = True
        coordinator = account.runtime_data

        coordinator.api.auth.expiration_date = time.time() + 30
        refresh.side_effect = module.BlinkUnreachable("TimeoutError")
        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        assert not reauth_flows(hass)

        # blinkpy's own refresh path reports an outage as TokenRefreshFailed.
        refresh.side_effect = renewed
        stopped.refresh.side_effect = TokenRefreshFailed
        await coordinator.async_refresh()
        assert not coordinator.last_update_success
        assert not reauth_flows(hass)

        stopped.refresh.side_effect = None
        await coordinator.async_refresh()
        assert coordinator.last_update_success
    assert account.state is ConfigEntryState.LOADED
    await hass.config_entries.async_unload(account.entry_id)


async def test_refused_token_while_running_asks_for_login(hass, account, stopped):
    with patch(REFRESH, new=AsyncMock(side_effect=renewed)) as refresh:
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
        stopped.available = True
        coordinator = account.runtime_data
        coordinator.api.auth.expiration_date = time.time() + 30
        refresh.side_effect = module.TokenRejected
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert len(reauth_flows(hass)) == 1
    await hass.config_entries.async_unload(account.entry_id)


async def test_token_refresh_failure_in_a_command_does_not_start_reauth(
    hass, account, stopped
):
    from homeassistant.exceptions import HomeAssistantError

    with patch(REFRESH, new=AsyncMock(side_effect=renewed)):
        assert await hass.config_entries.async_setup(account.entry_id)
        await hass.async_block_till_done()
        with pytest.raises(HomeAssistantError):
            await account.runtime_data.command(
                AsyncMock(side_effect=TokenRefreshFailed)
            )
        await hass.async_block_till_done()
    assert not reauth_flows(hass)
    account.runtime_data.auth_error(TokenRefreshFailed())
    assert not reauth_flows(hass)
    await hass.config_entries.async_unload(account.entry_id)
