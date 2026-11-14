"""Adding the PA: where it is, and the token that gets in.

The form is checked against the PA's own health route before it is saved, because both ways of
being wrong — a mistyped address and a stale token — are otherwise only discovered when someone
asks the kitchen a question and gets silence.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL

from .client import PAClient, PAAuthError, PAError
from .const import DOMAIN

STEP = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_TOKEN): str,
    }
)


class PAConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per PA server."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()

            client = PAClient(url, user_input[CONF_TOKEN].strip())
            try:
                await client.health()
            except PAAuthError:
                errors["base"] = "invalid_auth"
            except PAError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="PA", data={CONF_URL: url, CONF_TOKEN: client.token}
                )
        return self.async_show_form(step_id="user", data_schema=STEP, errors=errors)
