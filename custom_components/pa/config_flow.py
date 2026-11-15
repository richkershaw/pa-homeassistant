"""Adding the PA: where it is, and the token that gets in.

The form is checked against the PA's own health route before it is saved, because both ways of
being wrong — a mistyped address and a stale token — are otherwise only discovered when someone
asks the kitchen a question and gets silence.

**And it is checked properly, on the body.** A status of 200 only says something is there: an
address with no port on it reaches the reverse proxy, and OpenChamber behind it answers `/health`
with 200 and JSON that also begins `"status": "ok"`. A form that stopped at the status code saved
exactly that on 14 November, and every turn afterwards went to a socket that was not the PA's. The
client reads the body; this is only where the answer surfaces.

Correcting it is the same form and the same check, which is why `reconfigure` is here. Asking
someone to delete the integration and add it again to fix a missing port is asking them to lose the
identity of the thing they were fixing.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import PAClient, PAAuthError, PAError
from .const import DOMAIN

STEP = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_TOKEN): str,
    }
)


def _address(user_input: dict[str, Any]) -> str:
    """The address as given, without a trailing slash for the socket path to double up on."""
    return user_input[CONF_URL].strip().rstrip("/")


def _token(user_input: dict[str, Any]) -> str:
    """The token as given. Blank means what it already has, on a reconfigure."""
    return (user_input.get(CONF_TOKEN) or "").strip()


class PAConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per PA server."""

    VERSION = 1

    async def _refusal(self, url: str, token: str) -> dict[str, str]:
        """What is wrong with these, in the words the form has for it. Empty means nothing is."""
        try:
            await PAClient(url, token).health(async_get_clientsession(self.hass))
        except PAAuthError:
            return {"base": "invalid_auth"}
        except PAError:
            return {"base": "cannot_connect"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url, token = _address(user_input), _token(user_input)
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()
            errors = await self._refusal(url, token)
            if not errors:
                return self.async_create_entry(title="PA", data={CONF_URL: url, CONF_TOKEN: token})
        return self.async_show_form(step_id="user", data_schema=STEP, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Correct the address or the token, keeping the entry — and its conversation.

        The token may be left blank, which keeps the one it already has: a mistyped port is the
        mistake this form makes, and making someone re-paste a token to fix it is how a small
        correction turns into a lost one.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = _address(user_input)
            token = _token(user_input) or entry.data[CONF_TOKEN]
            errors = await self._refusal(url, token)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=url,
                    data_updates={CONF_URL: url, CONF_TOKEN: token},
                )
        known = vol.Schema(
            {
                vol.Required(CONF_URL, default=entry.data.get(CONF_URL, "")): str,
                vol.Optional(CONF_TOKEN, default=""): str,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=known, errors=errors)
