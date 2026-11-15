"""A card as a picture, for a screen that is not the one the answer was read on.

The PA's cards are drawn natively by its own apps — a list of rows, a day's times, a draft. Home
Assistant has no such client: a card arrives here as JSON, and the only way to put it in front of
someone in a room is to make a picture of it. So this is the third drawing of the same thing, and it
is deliberately a *uniform* one — a title, then rows — rather than an imitation of the phone's card.
A picture on a television wants to be legible at three metres, not to be identical to a phone.

The conversion from a card to a `Visual` is pure, and is where the tests are: what the picture says
is the part that can be wrong. What the drawing does with it is arithmetic.

Nothing here imports Home Assistant, so it can be exercised on its own — see `tests/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

FONT = Path(__file__).with_name("fonts") / "NotoSans-Regular.ttf"
"""The face the pictures are set in, travelling with the component. See `fonts/README.md`.

Not Pillow's own, and that is the point: `load_default(size=…)` is Aileron, which has no `£`, no em
dash, no bullet and no arrow — and the PA writes pounds and em dashes in almost every answer, so a
picture of one arrived with a missing-glyph box where the dash was.
"""

PLAIN = {
    "\u2192": "->",   # an arrow, the one visible character Noto Sans has no glyph for
    "\u00a0": " ",    # a non-breaking space, which is a space
    "\u200b": "",     # and the invisible ones, which a picture cannot draw at all
    "\u200c": "",
    "\u200d": "",
    "\ufeff": "",
}
"""Swapped before anything is drawn. A character with no glyph draws as a box, and a box is worse
than a hyphen for a dash and much worse than nothing for a zero-width space."""


def _plain(text: str) -> str:
    """`text` in characters the face can draw."""
    return "".join(PLAIN.get(char, char) for char in str(text))

# Wide enough to be readable on a television and narrow enough for the row text to wrap sensibly.
WIDTH = 880
PAD = 32
GAP = 18

# The PA's own palette, so a picture of its card looks like the PA rather than like Home Assistant.
GROUND = (22, 20, 18)
RULE = (42, 38, 34)
IVORY = (243, 238, 230)
MUTED = (168, 159, 147)
DIM = (143, 134, 122)
CLAY = (240, 185, 163)

KINDS = ("list", "schedule", "draft", "links", "files", "pending")
"""Every kind of card, in the order the server declares them (`server/cards.py`, `KINDS`).

Here rather than in the event entity, because this is where the kinds are *understood* — `visual_of`
has a branch for each — and because it has to be readable without Home Assistant, so a test can hold
it against the PA's own list and fail when the two drift.
"""

DEFAULT_TITLES = {
    "list": "Waiting on you",
    "schedule": "Today",
    "draft": "A draft",
    "links": "Found these",
    "files": "Documents",
    "pending": "Pending things",
}


@dataclass
class Visual:
    """A card as a picture: a title, then rows, and possibly a body and a footnote."""

    title: str
    # (set apart from the row, the row's own words, and what is under them). Any may be empty.
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    body: str = ""
    footnote: str = ""


def visual_of(card: dict[str, Any]) -> Visual | None:
    """One card as a `Visual`, or None when there is nothing worth picturing.

    Every kind maps to the same three fields, which is the whole point: one drawing, six small
    conversions, and a card added later is a line here rather than a new layout.
    """
    kind = card.get("kind")
    title = card.get("title") or DEFAULT_TITLES.get(kind, "From the PA")

    if kind == "list":
        rows = [(row.get("tag") or "", row.get("title", ""), row.get("reason", ""))
                for row in card.get("rows", [])]
    elif kind == "schedule":
        rows = [(item.get("time", ""), item.get("title", ""), item.get("detail") or "")
                for item in card.get("items", [])]
    elif kind == "links":
        rows = [("", item.get("title", ""), _underline(item.get("url", ""), item.get("detail")))
                for item in card.get("items", [])]
    elif kind == "files":
        rows = [("", item.get("title", ""), _underline(item.get("path", ""), item.get("detail")))
                for item in card.get("items", [])]
    elif kind == "pending":
        rows = [
            (_whose(row.get("who")), _name_of(row),
             _with_options(row.get("reason", ""), row.get("options") or []))
            for row in card.get("rows", [])
        ]
    elif kind == "draft":
        # A draft is one thing rather than a list of them, so its body goes in the body.
        subject = card.get("subject") or ""
        return Visual(
            title=title,
            rows=[("", f"To {card.get('to', '')}", subject)],
            body=card.get("body", ""),
        )
    else:
        return None

    if not rows and not card.get("footnote"):
        return None
    return Visual(title=title, rows=rows, footnote=card.get("footnote") or "")


def _whose(who: str | None) -> str:
    """The card says `you` or `them`; a picture says it the way the card draws it."""
    return {"you": "You", "them": "Them"}.get(who or "", "")


def _name_of(row: dict[str, Any]) -> str:
    """A pending row's person and where they are, as one line."""
    org = row.get("org") or ""
    title = row.get("title", "")
    return f"{title} · {org}" if org else title


def _with_options(reason: str, options: list[str]) -> str:
    """The steps that could be picked, said rather than drawn as pills.

    They cannot be pressed in a picture, but seeing them is half of what the card is for — the
    automation's job is to show what the PA offered, not to take an answer back through a
    television.
    """
    if not options:
        return reason
    return f"{reason}\n{', '.join(options)}"


def _underline(first: str, second: str | None) -> str:
    """A path or an address, with whatever is said about it after."""
    return f"{first}\n{second}" if second else first


def render(visual: Visual, width: int = WIDTH) -> bytes:
    """The picture, as PNG bytes."""
    title_font = _font(30)
    head_font = _font(22)
    label_font = _font(15)
    detail_font = _font(17)

    inner = width - 2 * PAD
    blocks: list[tuple[str, Any, int]] = []
    y = PAD

    if visual.title:
        lines = _wrap(visual.title, title_font, inner)
        blocks.append(("title", lines, y))
        y += _lines_height(lines, title_font) + GAP + 6

    for index, (label, headline, detail) in enumerate(visual.rows):
        if index:
            blocks.append(("rule", None, y))
            y += GAP
        # The label sits in a column of its own only when there is one, so a card of times reads
        # as a table and a card of documents does not have a hole down its left side.
        room = 0
        lines = _wrap(headline, head_font, inner - 0)
        if label:
            room = int(label_font.getlength(label)) + 16
            lines = _wrap(headline, head_font, inner - room)
        blocks.append(("row", (label, lines, room), y))
        y += _lines_height(lines, head_font)
        if detail:
            under = _wrap(detail, detail_font, inner - room)
            blocks.append(("detail", (room, under), y + 4))
            y += _lines_height(under, detail_font) + 4
        y += 6

    if visual.body:
        y += 6
        body = _wrap(visual.body, detail_font, inner)
        blocks.append(("body", body, y))
        y += _lines_height(body, detail_font)

    if visual.footnote:
        y += GAP
        note = _wrap(visual.footnote, detail_font, inner)
        blocks.append(("footnote", note, y))
        y += _lines_height(note, detail_font)

    image = Image.new("RGB", (width, y + PAD), GROUND)
    draw = ImageDraw.Draw(image)
    for kind, content, at in blocks:
        if kind == "title":
            draw.multiline_text((PAD, at), "\n".join(content), font=title_font, fill=IVORY,
                                spacing=_SPACING)
        elif kind == "rule":
            draw.line([(PAD, at), (width - PAD, at)], fill=RULE)
        elif kind == "row":
            label, lines, room = content
            if label:
                # On the headline's baseline and not its own top: a smaller face set from the same
                # `y` sits above the words it belongs to, which reads as a mistake at a glance.
                drop = head_font.getmetrics()[0] - label_font.getmetrics()[0]
                draw.text((PAD, at + drop), label, font=label_font, fill=CLAY)
            draw.multiline_text((PAD + room, at), "\n".join(lines), font=head_font, fill=IVORY,
                                spacing=_SPACING)
        elif kind == "detail":
            room, lines = content
            draw.multiline_text((PAD + room, at), "\n".join(lines), font=detail_font, fill=MUTED,
                                spacing=_SPACING)
        elif kind == "body":
            draw.multiline_text((PAD, at), "\n".join(content), font=detail_font, fill=IVORY,
                                spacing=_SPACING)
        elif kind == "footnote":
            draw.multiline_text((PAD, at), "\n".join(content), font=detail_font, fill=DIM,
                                spacing=_SPACING)

    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


_SPACING = 6


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """The face at that size, falling back to Pillow's own if the file is not beside us.

    The fallback is for a component half-copied into place, where a picture in the wrong face is
    better than a traceback in the middle of an answer. The substitutions hold either way, so the
    em dashes stay dashes.
    """
    if FONT.exists():
        return ImageFont.truetype(str(FONT), size)
    return ImageFont.load_default(size=size)


def _wrap(text: str, font: Any, width: int) -> list[str]:
    """`text` in lines that fit, breaking on words. A single word too long is left long."""
    lines: list[str] = []
    for paragraph in _plain(text).split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if line and font.getlength(candidate) > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return lines or [""]


def _lines_height(lines: list[str], font: Any) -> int:
    """How tall that many lines are, from the font's own metrics."""
    height = int(font.size * 1.25) if hasattr(font, "size") else 16
    return height * len(lines) + _SPACING * (len(lines) - 1)
