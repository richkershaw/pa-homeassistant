"""What the PA put on a screen, as something an automation can point at.

A bus event carries it, but a bus event is not something the automation editor can offer you: there
is no entity to pick and no trigger to choose, so the type has to be typed by hand. This is that
entity — Home Assistant's own way of saying *something happened* — and it is what a state trigger
picks, what the entity picker lists, and what a notification can be pointed at.

Its state is **when** it last happened, `event_type` is **which kind of card** it was, and its
attributes are the payload: the image, the words that were spoken, and the satellite.

**Its entity id is `event.pa_visual`**, which is worth writing down, because that is the name a
trigger uses.

It listens to the bus rather than being called by the conversation entity, so neither has to know
about the other and either can change without the other noticing.
"""

from __future__ import annotations

import logging

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .conversation import EVENT
from .visual import KINDS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the one entity this platform is."""
    async_add_entities([PAVisualEntity(entry)])


class PAVisualEntity(EventEntity):
    """A picture the PA made, as when it last did and what it was."""

    # The kinds, because the kind is what tells one from another: an automation can then take only
    # the ones it wants — a list to the kitchen television, a draft to nowhere — while a state
    # trigger on the entity itself still fires for every one of them.
    _attr_event_types = list(KINDS)
    _attr_name = "PA Visual"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}-visual"

    async def async_added_to_hass(self) -> None:
        """Listen to the bus, which is what keeps the two halves of this independent."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT, self._heard))

    @callback
    def _heard(self, event: Event) -> None:
        """One picture, as an event on this entity."""
        kind = event.data.get("kind")
        if kind not in self.event_types:
            # A kind this build does not know — one added to the PA after this was written —
            # is not an event type here, and `_trigger_event` raises for one it does not have.
            # The bus event still carries it, for anything listening to that instead.
            _LOGGER.debug("a card of a kind this entity does not know: %s", kind)
            return
        self._trigger_event(kind, dict(event.data))
        self.async_write_ha_state()
