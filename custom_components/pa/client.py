"""The PA's own socket, spoken the way one of its clients speaks it.

The PA has a wire of its own — `server/PROTOCOL.md` in the PA repository — and this speaks that
rather than inventing a second one for Home Assistant. A turn is: connect, say `hello` naming the
conversation, send `final` with the text, then read `delta` pieces until `done`.

`done` carries `expects_reply`, which is the PA saying it has not finished the exchange: it asked
something, or it is waiting on an answer. That is the whole reason a satellite should reopen its
microphone afterwards, and it is why the answer is read here rather than being reduced to a string
at the door.

Nothing in this module imports Home Assistant, **or anything else in this integration**, so it can
be exercised against a stub socket with no Home Assistant installed — and `ha/tests/` does exactly
that, building the stub's messages out of the PA's own protocol module so the two cannot drift
apart quietly. Keep it that way: a relative import added here is a test that stops running.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import aiohttp

# The path the PA serves its socket on (`server/config.py`, `WS_PATH`). Fixed rather than
# configurable: it is part of the PA's own contract, and a second place to get it wrong buys
# nothing.
WS_PATH = "/ws"

# How long the PA may say *nothing at all* before the turn is given up on. Not a limit on the turn:
# the PA writes as it goes and says it is working while it uses a tool, so a real turn sends
# something every few seconds even when it is slow. This is the length of a silence that means the
# socket is not the PA's.
DEFAULT_SILENCE = 120


class PAError(RuntimeError):
    """The PA could not be asked, or refused."""


class PAAuthError(PAError):
    """The PA is there, and the token is wrong.

    Told apart from the rest because it is the only failure the person setting it up can fix by
    typing something different, and saying "could not connect" for a bad token sends them to look
    at the network.
    """


@dataclass
class Reply:
    """What a turn came to."""

    text: str
    expects_reply: bool
    conversation_id: str | None


class PAClient:
    """A PA server: whether it is there, and how to open a turn with it."""

    def __init__(self, url: str, token: str, device: str = "Home Assistant",
                 silence: int = DEFAULT_SILENCE) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.device = device
        self.silence = silence

    @property
    def socket_url(self) -> str:
        """Where the conversation is held, from the address the rest is reached on."""
        base = self.url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return base + WS_PATH

    async def health(self, session: aiohttp.ClientSession | None = None) -> None:
        """Raise unless the PA answers its health route with this token.

        **The check is on the body, not only on the status code**, and that is not belt and braces.
        A status of 200 only says *something* is there: on an address with no port on it, that
        something is the reverse proxy, and OpenChamber behind it answers `/health` with 200 and
        JSON that also begins `"status": "ok"`. A form that stopped there saved
        `http://192.168.1.10` — no port, not the PA — and every turn afterwards went to a socket
        that was not there, which is the failure this is written from.

        The PA's body is exactly `{"status": "ok"}`. Anything else is something else answering, and
        the message says so and says what to do about it.

        A `401` and a connection refused are told apart, because only one of them is a reason to
        re-type the token.
        """
        owned = session is None
        chosen = session or aiohttp.ClientSession()
        try:
            async with chosen.get(f"{self.url}/health", headers=self._headers()) as response:
                if response.status == 401:
                    raise PAAuthError("the PA refused that token")
                if response.status != 200:
                    raise PAError(f"the PA answered {response.status} on /health")
                try:
                    # `content_type=None` so a page served as text/html is parsed and fails here,
                    # rather than failing inside aiohttp as a client error that reads like a
                    # network problem. A login page at the address is not the network's fault.
                    body = await response.json(content_type=None)
                except ValueError as err:
                    raise PAError(_not_the_pa(self.url, "it did not answer with JSON")) from err
                if body != {"status": "ok"}:
                    raise PAError(_not_the_pa(self.url, f"it said {_brief(body)}"))
        except aiohttp.ClientError as err:
            raise PAError(f"could not reach the PA at {self.url}: {err}") from err
        finally:
            if owned:
                await chosen.close()

    def turn(self, text: str, conversation_id: str | None = None) -> PATurn:
        """One exchange with the PA, its answer a piece at a time."""
        return PATurn(self, text, conversation_id)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


class PATurn:
    """One exchange with the PA. Read `pieces()` to the end, then read the rest.

    `expects_reply` and `conversation_id` are only meaningful once the pieces have run out: the
    first is in the last message and the second is in the first.
    """

    def __init__(self, client: PAClient, text: str, conversation_id: str | None = None) -> None:
        self._client = client
        self._text = text
        self.conversation_id = conversation_id
        self.text = ""
        self.expects_reply = False

    async def pieces(self) -> AsyncIterator[str]:
        """The answer as the PA writes it, then nothing.

        Raises `PAError` rather than ending quietly when the socket goes away before the answer
        does, because a satellite that has been left in silence should be told why.
        """
        turn_id = uuid4().hex
        # `total` is deliberately unset: a turn is allowed to take as long as it takes, because the
        # PA is a model with tools in front of it. Only a *silence* is limited, and it is limited
        # below with `wait_for` rather than here — aiohttp does not apply `sock_read` to a
        # WebSocket's reads, which is why a wrong address hung for ever instead of failing: the
        # reverse proxy took the upgrade, was not the PA, and the read never timed out.
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(
                    self._client.socket_url, headers=self._client._headers(), heartbeat=30
                ) as socket:
                    await socket.send_json({
                        "type": "hello",
                        "device": self._client.device,
                        # Nothing: this client can neither act nor show a card. That is the truth,
                        # and the PA is told it in as many words — it writes the turn off as one
                        # that must be answered aloud, with nothing to tap, and does not offer a
                        # card or an action it would be refused.
                        "capabilities": [],
                        "conversation_id": self.conversation_id,
                    })
                    await socket.send_json({"type": "final", "turn_id": turn_id, "text": self._text})

                    while True:
                        try:
                            message = await asyncio.wait_for(
                                socket.receive(), timeout=self._client.silence
                            )
                        except TimeoutError as err:
                            raise PAError(
                                f"the PA said nothing for {self._client.silence}s, so that socket "
                                "is not answering like the PA's — check the address has its port"
                            ) from err

                        if message.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
                        # Anything that is not text — a ping, a pong — is the transport's business
                        # and not ours, and must not end the turn.
                        if message.type is not aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            event = json.loads(message.data)
                        except ValueError:
                            continue
                        kind = event.get("type")

                        if kind == "ready":
                            # The conversation was made if it did not exist, so its id is the
                            # PA's answer to "which conversation is this" and not ours.
                            self.conversation_id = event.get("conversation_id") or self.conversation_id
                        elif kind == "delta" and event.get("turn_id") == turn_id:
                            piece = event.get("text") or ""
                            if piece:
                                self.text += piece
                                yield piece
                        elif kind == "done" and event.get("turn_id") == turn_id:
                            # The whole text is authoritative — it is what the transcript holds.
                            self.text = event.get("text") or self.text
                            self.expects_reply = bool(event.get("expects_reply"))
                            return
                        elif kind == "cancelled" and event.get("turn_id") == turn_id:
                            raise PAError("the PA dropped that answer")
                        elif kind == "error":
                            raise PAError(event.get("message") or "the PA refused the turn")
        except aiohttp.ClientError as err:
            raise PAError(f"could not reach the PA at {self._client.socket_url}: {err}") from err

        # Only reached when the socket closed before the answer was done: the turn returned from
        # inside the loop otherwise. Silent would be the wrong answer — the caller asked a question
        # and nothing said why there is no reply to it.
        raise PAError("the PA closed the connection before it answered")

    async def reply(self) -> Reply:
        """All of it, once it has all come."""
        async for _ in self.pieces():
            pass
        return Reply(self.text, self.expects_reply, self.conversation_id)


def _not_the_pa(url: str, why: str) -> str:
    """Said when something else answers the address.

    The fix is nearly always the port, so the message says it rather than leaving someone to work
    out that a 200 came from the wrong program.
    """
    return (
        f"{url}/health is not the PA — {why}. If that is this machine, the address needs its port: "
        "usually 7870, as in http://192.168.1.10:7870."
    )


def _brief(body: object) -> str:
    """A body in a few words, for a message someone has to read and act on."""
    text = body if isinstance(body, str) else json.dumps(body)
    return text[:80] + ("…" if len(text) > 80 else "")
