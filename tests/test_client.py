"""The PA's socket, against a stub that speaks the PA's own protocol.

The stub's messages come from `wire.py` — the PA's real models when a checkout of it is here, a
copy of the same shape when it is not — so a change to the wire that this client cannot read fails
here rather than in a kitchen.

What matters, and each has a test: the answer arrives a piece at a time and in order; `done`'s
`expects_reply` comes out, because that is what reopens a satellite's microphone; the conversation
id is the PA's, taken from `ready`, because the PA is the one that made the conversation; a bad
token is told apart from an unreachable host; and a turn that never finishes is an error rather
than a silence.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest
from aiohttp import web

import wire
from client import PAAuthError, PAClient, PAError

TOKEN = "test-token"


class StubPA:
    """A PA that answers however the test tells it to."""

    def __init__(
        self,
        *,
        pieces: tuple[str, ...] = ("One. ", "Two."),
        expects_reply: bool = False,
        conversation_id: str = "conv-1",
        refuse: str = "",
        hang_up: bool = False,
    ) -> None:
        self.pieces = pieces
        self.expects_reply = expects_reply
        self.conversation_id = conversation_id
        self.refuse = refuse
        self.hang_up = hang_up
        self.seen: list[dict] = []
        self._runner: web.AppRunner | None = None
        self.url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._health)
        app.router.add_get("/ws", self._socket)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001 - the only way to ask
        self.url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _health(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.Response(status=401)
        return web.json_response({"ok": True})

    async def _socket(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        async for message in socket:
            if message.type is not aiohttp.WSMsgType.TEXT:
                break
            event = json.loads(message.data)
            self.seen.append(event)
            if event["type"] == "hello":
                await socket.send_json(wire.message("Ready", conversation_id=self.conversation_id))
                await socket.send_json(wire.message("Recent", turns=[]))
            elif event["type"] == "final":
                turn_id = event["turn_id"]
                if self.refuse:
                    await socket.send_json(wire.message("Failure", message=self.refuse))
                    continue
                for piece in self.pieces:
                    await socket.send_json(wire.message("Delta", turn_id=turn_id, text=piece))
                if self.hang_up:
                    break
                await socket.send_json(
                    wire.message(
                        "Done",
                        turn_id=turn_id,
                        text="".join(self.pieces),
                        expects_reply=self.expects_reply,
                    )
                )
        return socket


def with_pa(run, **kwargs) -> None:
    """Start the stub, run the scenario against it, and put it away."""

    async def scenario() -> None:
        stub = StubPA(**kwargs)
        await stub.start()
        try:
            await run(stub)
        finally:
            await stub.stop()

    asyncio.run(scenario())


def test_the_answer_arrives_a_piece_at_a_time_and_in_order() -> None:
    async def run(stub: StubPA) -> None:
        client = PAClient(stub.url, TOKEN)
        turn = client.turn("what is waiting on me?")
        pieces = [piece async for piece in turn.pieces()]
        assert pieces == ["One. ", "Two."]
        assert turn.text == "One. Two."
        assert turn.conversation_id == "conv-1"

    with_pa(run)


def test_the_flag_that_reopens_a_microphone_comes_out() -> None:
    """`expects_reply` is the whole reason this client reads `done` rather than only the text."""

    async def run(stub: StubPA) -> None:
        reply = await PAClient(stub.url, TOKEN).turn("and for next week?").reply()
        assert reply.expects_reply is True
        assert reply.text == "One. Two."

    with_pa(run, expects_reply=True)


def test_an_answer_that_stands_alone_says_so() -> None:
    async def run(stub: StubPA) -> None:
        reply = await PAClient(stub.url, TOKEN).turn("morning").reply()
        assert reply.expects_reply is False

    with_pa(run)


def test_the_conversation_id_is_the_pas() -> None:
    """The PA makes the conversation, so the id on the way out is the one it answers with.

    A client that kept its own would have Home Assistant's chat log and the PA's transcript under
    two names for the same exchange.
    """

    async def run(stub: StubPA) -> None:
        reply = await PAClient(stub.url, TOKEN).turn("hello", conversation_id=None).reply()
        assert reply.conversation_id == "conv-1"

    with_pa(run)


def test_it_says_hello_with_nothing_but_words() -> None:
    """The client can neither act nor show a card, and the PA is told so by omission.

    Declaring a capability it does not have is the failure that costs a turn: the PA would offer a
    card to a satellite with no screen, be refused, and answer in words it had already spent a turn
    deciding on.
    """

    async def run(stub: StubPA) -> None:
        await PAClient(stub.url, TOKEN).turn("hello").reply()
        hello = next(event for event in stub.seen if event["type"] == "hello")
        assert hello["capabilities"] == []
        assert hello["device"] == "Home Assistant"

    with_pa(run)


def test_a_refused_token_is_told_apart_from_an_unreachable_host() -> None:
    async def run(stub: StubPA) -> None:
        with pytest.raises(PAAuthError):
            await PAClient(stub.url, "wrong").health()

    with_pa(run)


def test_a_healthy_pa_passes_health() -> None:
    async def run(stub: StubPA) -> None:
        await PAClient(stub.url, TOKEN).health()

    with_pa(run)


def test_something_that_is_not_there_is_not_a_token_problem() -> None:
    """Port 1 is not a webserver. The check must not blame the token for the network."""

    async def run() -> None:
        with pytest.raises(PAError) as refused:
            await PAClient("http://127.0.0.1:1", TOKEN).health()
        assert not isinstance(refused.value, PAAuthError)

    asyncio.run(run())


def test_the_pas_own_refusal_is_raised_with_its_reason() -> None:
    async def run(stub: StubPA) -> None:
        with pytest.raises(PAError, match="no brain is configured"):
            await PAClient(stub.url, TOKEN).turn("hello").reply()

    with_pa(run, refuse="no brain is configured")


def test_a_socket_that_closes_before_the_answer_is_an_error() -> None:
    """Silence is the one answer that must not be passed on as an answer."""

    async def run(stub: StubPA) -> None:
        with pytest.raises(PAError):
            await PAClient(stub.url, TOKEN).turn("hello").reply()

    with_pa(run, hang_up=True)


@pytest.mark.skipif(wire.protocol is None, reason="no PA checkout here to check the names against")
def test_the_type_names_are_the_pas() -> None:
    """The one thing a copy of the wire can get wrong on its own.

    Everything else about the stub comes from the PA's model itself, so the names are what is left
    to check — and they are what the client switches on, so a rename there would be read here as a
    message it does not know and skipped in silence.
    """
    for model, name in wire.TYPES.items():
        field = getattr(wire.protocol, model).model_fields["type"]
        assert field.default == name, model
