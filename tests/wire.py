"""The PA's wire: its own models when a checkout of the PA is here, a copy of them when it is not.

The protocol lives in the PA repository — `server/protocol.py`, with `server/PROTOCOL.md` beside it —
and this is a separate checkout, so the tests cannot simply import it. So the stub in
`test_client.py` builds its messages through `message()`:

- with a PA checkout found (beside this repository, or named by `PA_REPO`), it *is* the PA's own
  model, dumped to JSON — so the stub cannot be wrong about the wire;
- without one, it is a plain dictionary of the same shape, and `test_the_type_names_are_the_pas`
  is skipped rather than pretending to have checked.

Development happens with both repositories side by side, so the check runs where a change is made.
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
    "Failure": "error",
}
"""Each message and the `type` it carries. Checked against the PA's models when one is here."""


def _find() -> object | None:
    """The PA's protocol module, or None if no checkout can be found.

    Any failure counts as not found: the file may be missing, or pydantic may not be installed in
    whatever is running these tests, and neither is a reason to fail a test about the client.
    """
    guesses = [
        os.environ.get("PA_REPO"),
        HERE.parent.parent / "pa",
        HERE.parent.parent / "private-pa",
    ]
    for guess in guesses:
        if not guess:
            continue
        server = Path(guess).expanduser() / "server"
        path = server / "protocol.py"
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("pa_protocol", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            # In `sys.modules` *before* it runs, which is not a detail: the protocol has
            # `from __future__ import annotations`, so pydantic resolves every model's annotations
            # later, by looking the defining module up by name. Without this, every model comes back
            # "not fully defined".
            sys.modules["pa_protocol"] = module
            spec.loader.exec_module(module)
        except Exception:  # noqa: BLE001 - see the docstring
            return None
        return module
    return None


protocol = _find()
"""The PA's own models, when a checkout of it is here."""


def message(name: str, **fields: object) -> dict:
    """One message, as the PA would send it or as close as a copy can be.

    Anything left out takes the model's own default, so a caller names only what the test is about.
    """
    if protocol is not None:
        model = getattr(protocol, name)(**fields)
        return model.model_dump()
    return {"type": TYPES[name], **fields}
