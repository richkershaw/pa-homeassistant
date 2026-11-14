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
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import ulid as ulid_util

from .client import PAClient, PAError, PATurn

_LOGGER = logging.getLogger(__name__)


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
        return _result(
            user_input,
            turn.conversation_id or conversation_id,
            turn.text,
            turn.expects_reply,
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
