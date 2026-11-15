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
        silent: bool = False,
        health: object = None,
        cards: tuple[dict, ...] = (),
    ) -> None:
        self.pieces = pieces
        # Sent after `done`, as the server does: a turn that was interrupted draws nothing.
        self.cards = cards
        self.expects_reply = expects_reply
        self.conversation_id = conversation_id
        self.refuse = refuse
        self.hang_up = hang_up
        # Accept the upgrade and then say nothing at all — what a socket that is not the PA's does.
        self.silent = silent
        # A string is served as text, which is what a page that is not the PA answers with.
        self.health_body = {"status": "ok"} if health is None else health
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
        if isinstance(self.health_body, str):
            return web.Response(text=self.health_body, content_type="text/html")
        return web.json_response(self.health_body)  # type: ignore[arg-type]

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
                if self.silent:
                    # Nothing at all, and no close either: the caller has to give up on its own.
                    continue
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
                        cards=len(self.cards),
                    )
                )
                for card in self.cards:
                    await socket.send_json(
                        wire.message("CardMessage", turn_id=turn_id, card=card)
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


def test_it_says_hello_declaring_only_that_a_card_becomes_a_picture() -> None:
    """No action capability at all, and one show capability that is deliberately not `cards`.

    An action it cannot perform is the failure that costs a turn: the PA would offer one, be
    refused, and answer in words it had already spent a turn deciding on. And `cards_elsewhere`
    rather than `cards` because the difference is what the PA is *told* to do — a picture on a
    screen in the room may never be looked at, so the answer has to stand on its own instead of
    staying short for the card to carry.
    """

    async def run(stub: StubPA) -> None:
        await PAClient(stub.url, TOKEN).turn("hello").reply()
        hello = next(event for event in stub.seen if event["type"] == "hello")
        assert hello["capabilities"] == ["cards_elsewhere"]
        assert hello["device"] == "Home Assistant"

    with_pa(run)


def test_a_card_that_arrives_after_done_is_collected() -> None:
    """The cards follow `done`, which is the reason `done` says how many to expect.

    A client that returned the moment the words were done would never see one, and a client that
    waited instead would pay a guess on every turn that has none.
    """

    async def run(stub: StubPA) -> None:
        turn = PAClient(stub.url, TOKEN).turn("what is waiting on me?")
        await turn.reply()
        assert [card["kind"] for card in turn.cards] == ["list"]

    with_pa(run, cards=({"kind": "list", "rows": [{"title": "a", "reason": "b"}]},))


def test_a_turn_with_no_card_comes_back_with_none() -> None:
    async def run(stub: StubPA) -> None:
        turn = PAClient(stub.url, TOKEN).turn("hello")
        await turn.reply()
        assert turn.cards == []

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


def test_something_else_answering_health_is_refused_and_names_the_port() -> None:
    """The bug this is written from, exactly.

    A config entry was saved as `http://192.168.1.10` — no port. Port 80 is the reverse proxy, and
    OpenChamber behind it answers `/health` with 200 and JSON that *also* begins `"status": "ok"`.
    So a check on the status code passed, and every turn afterwards went to a socket that was not
    the PA's. The body is what tells them apart, and the message has to say what to do.
    """

    async def run(stub: StubPA) -> None:
        with pytest.raises(PAError) as refused:
            await PAClient(stub.url, TOKEN).health()
        assert "not the PA" in str(refused.value)
        assert "7870" in str(refused.value)

    with_pa(
        run,
        health={
            "status": "ok",
            "timestamp": "2026-11-14T20:17:57.788Z",
            "openchamberVersion": "2.7.0",
            "runtime": "web",
        },
    )


def test_a_health_route_that_is_not_json_is_refused() -> None:
    """A login page, a proxy's 404 page, anything with a 200 on it."""

    async def run(stub: StubPA) -> None:
        with pytest.raises(PAError, match="not the PA"):
            await PAClient(stub.url, TOKEN).health()

    with_pa(run, health="<html><body>Sign in</body></html>")


def test_a_socket_that_accepts_and_says_nothing_is_given_up_on() -> None:
    """The read has a timeout of its own, and that is what a wrong address needs.

    `ClientTimeout(total=…)` alone leaves the read unbounded, so this hung for ever rather than
    failing: the proxy took the upgrade, was not the PA, and Assist waited until the caller gave
    up. `total` is unset now — a turn may take as long as it takes — and only a *silence* is
    limited.
    """

    async def run(stub: StubPA) -> None:
        client = PAClient(stub.url, TOKEN, silence=1)
        with pytest.raises(PAError):
            await client.turn("hello").reply()

    with_pa(run, silent=True)


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
