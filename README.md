# PA, as a Home Assistant conversation agent

A voice satellite answers what it recognises — a command, a skill — and hands the rest to a
conversation agent. This makes that agent [the PA](https://github.com/Leckie-Kershaw/private-pa):
`who's waiting on me?` and `what's my week look like?` are answered from the mail, the CRM and the
calendar, not by a general model.

It registers the PA as a `ConversationEntity`, so it appears wherever a conversation agent is
picked — Home Assistant's own **Voice assistants**, and **Voice Incubator's** Settings, which is
what it was written for.

> This repository is a checkout of the component that lives in the PA repository, moved here because
> HACS needs a repository of its own with `custom_components/pa/` at the root. The PA itself is
> private; nothing in this repository is a secret — no address, no token, and no code that reaches
> anything but the server you configure.

## Installing

**With HACS.** HACS → Integrations → the ⋮ menu → **Custom repositories**, add
`https://github.com/richkershaw/pa-homeassistant` as type **Integration**. Then find **PA** in
HACS and download it, and restart Home Assistant.

**By hand.** Copy `custom_components/pa/` into Home Assistant's configuration folder so that it
lands at `<config>/custom_components/pa/`, and restart.

Then **Settings → Devices & services → Add integration → PA**, with the address the PA answers on —
`http://192.168.1.10:7870`, not the socket, which is found from it — and `PA_SERVER_TOKEN` from the
PA's `server/.env`. The form checks both against the PA's `/health` before it saves, and tells a
wrong token apart from an address that is not there.

## Why not Wyoming

The obvious way to expose an outside agent is the Wyoming protocol, whose `handle` service is text
in, text out, and which needs no Home Assistant code at all. Two things rule it out.

There is **no authentication** on it: the port would be the security, and the PA can send mail and
messages as its owner.

And it cannot **stream**. `handled-start`, `handled-chunk` and `handled-stop` are in the protocol,
and Home Assistant's Wyoming conversation platform reads none of them — it reads one reply and
returns. The answer would be spoken once it was finished, and a PA turn is several seconds and often
several tool calls, which is a long silence in a kitchen.

An entity can stream, so the answer is spoken while the PA is still writing it.

## How it talks to the PA

`client.py` speaks the PA's own wire — `server/PROTOCOL.md` in the PA repository — as one more PA
client, so nothing had to be added to the server for this. A turn is: connect, `hello` naming the
conversation, `final` with the text, then `delta` pieces until `done`.

Two things on that wire are why it is worth doing this way.

**`done` carries `expects_reply`** — the PA saying it has not finished the exchange — and that
becomes the result's `continue_conversation`. Home Assistant would otherwise infer it from the text
ending in a question mark; the PA sets it for an unfinished answer too, and *"I'll come back to
you"* is the case that matters. The satellite reopens its microphone for the answer rather than
closing on a sentence that happened not to end in `?`.

**The client declares no capabilities.** So the line the PA is given about its client already says
this turn is spoken aloud with nothing to tap, and it never spends a turn offering a card to a
screen that is not there.

## What it does not do

**No home control.** The entity deliberately does not declare `CONTROL`: the PA cannot run Home
Assistant's intents, and claiming it could would route *turn the kitchen lights on* here and get
prose back. The PA can act in the world through its own tools, with its own rules about asking
first, but not by being handed a `HassTurnOn`.

**One agent for everyone.** This makes the PA *an* agent; it does not decide who gets it. Per-person
routing — a household keeping a general agent while one person's sentences come here — would be a
change in Voice Incubator rather than here, and it rests on the speaker being identified, which
happens only on a satellite running Voice Incubator's firmware. On stock firmware the app cannot
tell who is speaking, so such a setting would never fire. See the PA repository's `ha/README.md`
for what it would take and what it would cost.

## The tests

```bash
pip install aiohttp pytest
pytest -q
```

They run the client against a stub socket. When a checkout of the PA is beside this repository — or
named by `PA_REPO` — the stub's messages **are** the PA's own models, dumped to JSON, so it cannot
be wrong about the wire; without one it falls back to a copy of the same shape and the check that
the two agree is skipped rather than pretended. Development happens with both repositories side by
side.

They need no Home Assistant, which is why `client.py` imports nothing else from this integration —
keep it that way, or they stop running. A test of the entity itself would need
`pytest-homeassistant-custom-component` and is not here.

## Which Home Assistant

It uses `ChatLog.async_add_delta_content_stream`, which is what makes the streaming work, so it
needs a Home Assistant that has it. If the agent fails to answer and the traceback names that
method, the version is too old — `hacs.json` deliberately does not name a floor, because a wrong
one is worse than none.

## Licence

None yet, which means all rights reserved. Say the word if that should change.
