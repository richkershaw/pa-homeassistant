"""The PA's wire and its card kinds: loaded from the PA when a checkout is here, copied shapes when
it is not.

Both live in the PA repository — `server/protocol.py` and `server/cards.py` — and this is a separate
checkout, so the tests cannot simply import them. So the stub in `test_client.py` builds its messages
through `message()`:

- with a PA checkout found (beside this repository, or named by `PA_REPO`), it *is* the PA's own
  model, dumped to JSON — so the stub cannot be wrong about the wire;
- without one, it is a plain dictionary of the same shape, and the checks that the two agree are
  skipped rather than pretending to have checked.

Development happens with both repositories side by side, so those checks run where a change is made.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

TYPES = {
    # Sent by the client.
    "Hello": "hello",
    "Final": "final",
    # Sent by the PA.
    "Ready": "ready",
    "Recent": "recent",
    "Delta": "delta",
    "Done": "done",
    "CardMessage": "card",
    "Failure": "error",
}
"""Each message and the `type` it carries. Checked against the PA's models when one is here."""


def _server() -> Path | None:
    """The PA's `server/` directory, or None if no checkout can be found."""
    guesses = [
        os.environ.get("PA_REPO"),
        HERE.parent.parent / "pa",
        HERE.parent.parent / "private-pa",
    ]
    for guess in guesses:
        if not guess:
            continue
        server = Path(guess).expanduser() / "server"
        if (server / "protocol.py").exists():
            return server
    return None


def _load(name: str, path: Path) -> object | None:
    """A module from a file, or None — which any of a missing file or a missing pydantic means.

    Registered in `sys.modules` *before* it runs, which is not a detail: the protocol has
    `from __future__ import annotations`, so pydantic resolves every model's annotations later by
    looking the defining module up by name, and without this every model comes back "not fully
    defined".
    """
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - see the docstring
        return None
    return module


_server_dir = _server()

protocol = _load("pa_protocol", _server_dir / "protocol.py") if _server_dir else None
"""The PA's own models, when a checkout of it is here."""

if protocol is not None:
    # `cards.py` does `from protocol import ACTIONS`, so it has to be reachable by that name as
    # well as by the one it was loaded under.
    sys.modules.setdefault("protocol", protocol)

cards = _load("pa_cards", _server_dir / "cards.py") if _server_dir else None
"""The PA's own card vocabulary, when a checkout of it is here."""


def message(name: str, **fields: object) -> dict:
    """One message, as the PA would send it or as close as a copy can be.

    Anything left out takes the model's own default, so a caller names only what the test is about.
    """
    if protocol is not None:
        model = getattr(protocol, name)(**fields)
        return model.model_dump()
    return {"type": TYPES[name], **fields}
