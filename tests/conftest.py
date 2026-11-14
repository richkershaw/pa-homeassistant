"""Where the tests import from.

`custom_components/pa/` itself, so that `client` is imported as a plain module and the integration's
`__init__.py` is never run — which would drag all of Home Assistant in behind it. That is deliberate
rather than a trick: **`client.py` imports nothing else from this integration**, on purpose, so the
part that speaks to the PA can be exercised against a stub socket with no Home Assistant installed.
A test of the entity itself would need `pytest-homeassistant-custom-component`, and is not here.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "pa"))
