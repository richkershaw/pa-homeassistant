"""The PA, answering a voice assistant's questions.

Two ways in, and the difference between them is the whole reason this integration exists.

`_async_handle_message` streams. It hands each piece of the PA's answer to the chat log as it
arrives, and everything downstream — the app's conversation fallback, Assist itself — reads those
deltas as they come, so the answer is spoken while the PA is still writing it. A PA turn is several
seconds and often several tool calls, so without this the satellite is silent for all of it.

`async_process` is the plain request-and-reply the platform asks for. It is what runs for anything
that wants the whole answer at once, and it is kept honest rather than being a stub.

Neither declares `CONTROL`. The PA can act in the world through its own tools, but it cannot run a
`HassTurnOn`, and claiming it could would have Home Assistant route house commands here and get
prose back.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import ulid as ulid_util

from .client import PAClient, PAError, PATurn
from .visual import Visual, render, visual_of

_LOGGER = logging.getLogger(__name__)

EVENT = "pa_visual"
"""Fired when the PA put something visual beside an answer.

A card cannot be shown here — Home Assistant has no card client — so it is turned into a picture and
handed on as a file, and this is the bus event that says so. What an automation does with it is its
own business: the one it was written for sends the picture to whichever television is on in the
room, so the answer is seen as well as heard.
"""

EVENT_FOLDER = "pa"
"""Where the pictures go under Home Assistant's `www`, served at `/local/pa/`.

Ours, and named here so it can be cleared: nothing else writes there, and the newest few are kept
while the rest are pruned by age.
"""

KEEP_PICTURES = 20
"""How many to keep. A picture is a moment, and nobody goes looking for last week's."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the one agent this entry is."""
    async_add_entities([PAConversationEntity(entry)])


def _write(folder: str, visual: Visual, name: str) -> Path:
    """Draw the picture and put it where Home Assistant serves `/local` from.

    Runs in an executor — drawing is arithmetic on a bitmap, not something to hold the event loop
    for — and does not touch Home Assistant at all, which is why it is a module function.
    """
    where = Path(folder)
    where.mkdir(parents=True, exist_ok=True)
    path = where / name
    path.write_bytes(render(visual))
    _prune(where)
    return path


def _prune(where: Path, keep: int = KEEP_PICTURES) -> None:
    """Leave the newest few, so a year of answers is not a folder nobody will ever clear."""
    pictures = sorted(where.glob("*.png"), key=lambda one: one.stat().st_mtime, reverse=True)
    for stale in pictures[keep:]:
        stale.unlink(missing_ok=True)


def _picture_name(conversation_id: str, number: int) -> str:
    """A name that says when, so the folder sorts by time and the newest is obvious."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"pa-{stamp}-{conversation_id[:8]}-{number}.png"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the one agent this entry is."""
    async_add_entities([PAConversationEntity(entry)])


class PAConversationEntity(conversation.ConversationEntity):
    """The PA, as an agent to ask."""

    _attr_name = "PA"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._client: PAClient = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}-conversation"

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """English, which is what the PA's prompt and its tools are written for.

        Not `MATCH_ALL`: saying it speaks any language would have Home Assistant send Welsh and
        get English back, which reads as the assistant ignoring the question rather than as a
        limit.
        """
        return ["en"]

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Answer all at once, for anything that asks that way."""
        conversation_id = user_input.conversation_id or ulid_util.ulid_now()
        turn = self._client.turn(user_input.text, conversation_id)
        try:
            reply = await turn.reply()
        except PAError as err:
            _LOGGER.warning("the PA could not answer: %s", err)
            return _failed(user_input, conversation_id, str(err))
        if turn.cards:
            await self._publish(turn, user_input, conversation_id)
        return _result(
            user_input, reply.conversation_id or conversation_id, reply.text, reply.expects_reply
        )

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Answer into the chat log, a piece at a time."""
        conversation_id = user_input.conversation_id or ulid_util.ulid_now()
        turn = self._client.turn(user_input.text, conversation_id)
        try:
            async for _ in chat_log.async_add_delta_content_stream(self.entity_id, _deltas(turn)):
                pass
        except PAError as err:
            _LOGGER.warning("the PA could not answer: %s", err)
            return _failed(user_input, conversation_id, str(err))
        if turn.cards:
            # After the words, and before the result: the answer is already complete, and a picture
            # that cannot be drawn must not cost the answer it belongs to.
            await self._publish(turn, user_input, conversation_id)
        return _result(
            user_input,
            turn.conversation_id or conversation_id,
            turn.text,
            turn.expects_reply,
        )

    async def _publish(
        self,
        turn: PATurn,
        user_input: conversation.ConversationInput,
        conversation_id: str,
    ) -> None:
        """Turn each card into a picture and say on the bus that there is one.

        The picture is the only way a card reaches a room: it arrives here as JSON, and this makes a
        file of it — in `www/pa`, which Home Assistant serves at `/local/pa/`, so the address is one
        the app and a television already know, from anywhere.

        Nothing here is allowed to fail the turn. A card that cannot be drawn or written is logged
        and skipped, because the answer it belongs to has already been given and is the point.
        """
        for number, card in enumerate(turn.cards):
            visual = visual_of(card)
            if visual is None:
                continue
            try:
                written = await self.hass.async_add_executor_job(
                    _write,
                    self.hass.config.path("www", EVENT_FOLDER),
                    visual,
                    _picture_name(conversation_id, number),
                )
            except OSError as err:
                _LOGGER.warning("could not write the PA's picture: %s", err)
                continue
            self.hass.bus.async_fire(
                EVENT,
                {
                    "image": f"/local/{EVENT_FOLDER}/{written.name}",
                    "kind": card.get("kind", ""),
                    "title": visual.title,
                    # The words the answer was spoken with, so a notification can carry both
                    # without the automation going back to the conversation for them.
                    "spoken": turn.text,
                    "conversation_id": conversation_id,
                    # Which satellite asked, so an automation can find the room. None for a typed
                    # turn in Assist, which is the honest answer: it was said in no room.
                    "device_id": user_input.device_id,
                },
            )


async def _deltas(turn: PATurn) -> AsyncIterator[dict[str, Any]]:
    """The PA's pieces as the chat log's deltas.

    Every one is assistant content: the PA does its own work behind its own socket and writes only
    the answer, so there is no tool call or thinking here to tell apart. The role is on each piece
    because that is what tells `async_add_delta_content_stream` to join them into one message
    rather than starting a new one.
    """
    async for piece in turn.pieces():
        yield {"role": "assistant", "content": piece}


def _result(
    user_input: conversation.ConversationInput,
    conversation_id: str,
    speech: str,
    expects_reply: bool,
) -> conversation.ConversationResult:
    """What the agent answered.

    `continue_conversation` is the PA's own flag rather than the question mark Home Assistant would
    infer from the text. The PA sets it for an unfinished answer as well as for a question — "I'll
    come back to you" is the case that matters — and that is exactly when the satellite should keep
    its microphone open instead of closing on a sentence that happened not to end in "?".
    """
    response = intent.IntentResponse(language=user_input.language)
    response.async_set_speech(speech)
    return conversation.ConversationResult(
        response=response,
        conversation_id=conversation_id,
        continue_conversation=expects_reply,
    )


def _failed(
    user_input: conversation.ConversationInput, conversation_id: str, message: str
) -> conversation.ConversationResult:
    """Nothing was answered, and a reason for it."""
    response = intent.IntentResponse(language=user_input.language)
    response.async_set_error(intent.IntentResponseErrorCode.FAILED_TO_HANDLE, message)
    return conversation.ConversationResult(response=response, conversation_id=conversation_id)
