"""The PA, as a conversation agent Home Assistant can ask.

One entry per PA server. It is a service rather than a device: the PA is a program on the network
with a token, and there is nothing to discover about it beyond whether it answers.

This exists because the PA is not a Home Assistant conversation agent on its own — it has its own
socket and its own clients — and an agent is what a voice assistant asks when nothing else
understood what it heard.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant

from .client import PAClient
from .const import DOMAIN

PLATFORMS = [Platform.CONVERSATION, Platform.EVENT]

__all__ = ["DOMAIN", "async_setup_entry", "async_unload_entry"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remember how to reach the PA, and give Home Assistant the agent."""
    entry.runtime_data = PAClient(entry.data[CONF_URL], entry.data[CONF_TOKEN])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Let go of the agent. Nothing is held open to the PA between turns."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
