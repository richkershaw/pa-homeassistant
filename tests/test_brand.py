"""The integration's icon: the app's own artwork, at the sizes Home Assistant looks for.

Home Assistant serves an integration's brand images out of a `brand/` directory inside the
integration (`components/brands`, and `has_branding` on the loaded integration), which is why they
are here rather than beside the app — the same blob, and not a second drawing of it.

A test, because this is the kind of file that goes missing in a move and is noticed only as a grey
placeholder in the interface.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

BRAND = Path(__file__).resolve().parent.parent / "custom_components" / "pa" / "brand"

WANTED = {
    "icon.png": (256, "RGB"),
    "icon@2x.png": (512, "RGB"),
    "dark_icon.png": (256, "RGBA"),
    "dark_icon@2x.png": (512, "RGBA"),
}
"""The sizes Home Assistant's own conventions ask for, and the mode each cut should be in."""


@pytest.mark.parametrize("name", sorted(WANTED))
def test_an_icon_is_here_at_the_size_the_conventions_ask_for(name: str) -> None:
    path = BRAND / name
    assert path.exists(), f"{name} is missing from {BRAND}"

    size, mode = WANTED[name]
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == (size, size)
        # The light cut is opaque and the dark one is not — which is the point of a dark cut: the
        # system's own tile shows through rather than a background of ours.
        assert image.mode == mode, f"{name} should be {mode}"


def test_the_dark_icon_is_drawn_rather_than_copied() -> None:
    """A dark cut that was only a copy of the light one would look like a mistake on a dark screen."""
    assert (BRAND / "icon.png").read_bytes() != (BRAND / "dark_icon.png").read_bytes()
