"""A card as a picture: what it says, and that it is drawn at all.

The conversion is the part that can be wrong — the wrong rows, a missing reason, an option list
dropped — so most of the tests are on `visual_of`, which is pure. The drawing is checked for the
properties a caller depends on: a real PNG, the width asked for, and taller as the card grows.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

import visual
import wire
from visual import Visual, _wrap, render, visual_of

LIST = {
    "kind": "list",
    "title": "Waiting on you",
    "rows": [
        {"title": "Reply to Oli", "reason": "you set it yourself", "tag": "Today"},
        {"title": "Invoices", "reason": "earned, not asked for"},
    ],
    "footnote": "The rest can wait.",
}


def test_a_list_card_becomes_a_title_and_rows() -> None:
    visual = visual_of(LIST)
    assert visual is not None
    assert visual.title == "Waiting on you"
    assert visual.rows[0] == ("Today", "Reply to Oli", "you set it yourself")
    # A row with no tag still has its place in the row, as an empty column.
    assert visual.rows[1] == ("", "Invoices", "earned, not asked for")
    assert visual.footnote == "The rest can wait."


def test_every_kind_maps_to_the_same_three_fields() -> None:
    """One drawing, six conversions. This is what keeps a seventh card a line rather than a layout."""
    schedule = visual_of({"kind": "schedule", "title": "Today",
                          "items": [{"time": "10:00", "title": "Presentation",
                                     "detail": "North Bridge House"}]})
    assert schedule is not None and schedule.rows[0] == ("10:00", "Presentation",
                                                         "North Bridge House")

    links = visual_of({"kind": "links", "items": [
        {"title": "Issue 12", "url": "https://github.com/x/y/issues/12", "detail": "the spec"}]})
    assert links is not None
    assert links.rows[0][1] == "Issue 12"
    assert links.rows[0][2] == "https://github.com/x/y/issues/12\nthe spec"

    files = visual_of({"kind": "files", "items": [
        {"title": "Scope v3", "path": "/Engagements/Prefequity/Scope v3.docx"}]})
    assert files is not None and files.rows[0][2] == "/Engagements/Prefequity/Scope v3.docx"


def test_a_draft_is_a_body_rather_than_a_list() -> None:
    """The one kind that is a thing rather than a set of them, so it is not rows."""
    visual = visual_of({"kind": "draft", "to": "oli@example.com", "subject": "Scope",
                        "body": "Here it is.\n\nRichard"})
    assert visual is not None
    assert visual.rows == [("", "To oli@example.com", "Scope")]
    assert visual.body.startswith("Here it is.")


def test_a_pending_row_says_whose_move_and_what_could_be_picked() -> None:
    """The steps cannot be pressed in a picture, and seeing them is half of what the card is for."""
    visual = visual_of({"kind": "pending", "rows": [{
        "who": "you", "title": "Thomas Barratt", "org": "Prefequity",
        "reason": "The NDA has not gone.", "ref": "rec1", "ref_kind": "commitment",
        "options": ["Send the NDA", "Snooze a week"],
    }]})
    assert visual is not None
    label, headline, detail = visual.rows[0]
    assert label == "You"
    assert headline == "Thomas Barratt · Prefequity"
    assert "The NDA has not gone." in detail
    assert "Send the NDA, Snooze a week" in detail


@pytest.mark.skipif(wire.cards is None, reason="no PA checkout here to check the kinds against")
def test_the_kinds_are_the_pas() -> None:
    """A kind added there and not here is a picture nobody can trigger on.

    The event entity's `event_types` is a closed set — Home Assistant refuses a type it was never
    told about — so a card the PA can draw and this does not know would arrive, be drawn and
    announced, and leave the entity with no event.
    """
    assert sorted(visual.KINDS) == sorted(wire.cards.KINDS), (
        "the card kinds have drifted from the PA's own list"
    )


def test_a_card_with_nothing_in_it_is_not_a_picture() -> None:
    """An empty card is not worth a file and an event; the words already said it."""
    assert visual_of({"kind": "list", "rows": []}) is None
    assert visual_of({"kind": "invented_later", "rows": [{"title": "x", "reason": "y"}]}) is None


def test_a_card_with_only_a_footnote_is_still_worth_drawing() -> None:
    visual = visual_of({"kind": "list", "rows": [], "footnote": "Nine others asked nothing of you."})
    assert visual is not None and visual.footnote


def test_the_picture_is_a_png_of_the_width_asked_for() -> None:
    visual = visual_of(LIST)
    assert visual is not None
    drawn = Image.open(BytesIO(render(visual)))
    assert drawn.format == "PNG"
    assert drawn.width == 880
    assert drawn.height > 100


def test_the_picture_is_actually_drawn_on() -> None:
    """Not a blank rectangle: the failure that would look like success from the bytes alone."""
    visual = visual_of(LIST)
    assert visual is not None
    drawn = Image.open(BytesIO(render(visual))).convert("RGB")
    assert len(drawn.getcolors(maxcolors=1_000_000) or []) > 1


def test_more_rows_make_a_taller_picture() -> None:
    one = Visual(title="T", rows=[("", "a", "")])
    three = Visual(title="T", rows=[("", "a", ""), ("", "b", ""), ("", "c", "")])
    assert len(render(three)) > len(render(one))


def test_long_words_are_wrapped_rather_than_cut() -> None:
    """A reason is up to 200 characters, so this is the ordinary case rather than the edge."""
    lines = _wrap(
        "The target's details are waiting on it, so no scope can be written at all.", visual._font(17), 300
    )
    assert len(lines) > 1
    assert " ".join(lines) == (
        "The target's details are waiting on it, so no scope can be written at all."
    )


def test_the_bundled_face_can_draw_what_the_pa_writes() -> None:
    """The reason a font is committed to this repository at all.

    Pillow's own default is Aileron, and Aileron has no `£`, no em dash, no bullet and no arrow.
    The PA writes pounds and em dashes in almost every answer — "£12,500", "— he said" — so this is
    the ordinary case and not an edge, and the first picture of one of its cards arrived with a
    missing-glyph box where the dash was.
    """
    assert visual.FONT.exists(), "the bundled face has gone missing"
    font = visual._font(22)
    notdef = bytes(font.getmask("\ue000"))  # private use: certainly not in any face
    for char in "£—–“”‘’…·•":
        assert bytes(font.getmask(char)) != notdef, f"{char!r} would draw as a box"


def test_what_the_face_cannot_draw_is_swapped_rather_than_boxed() -> None:
    """`→` is the one visible character Noto Sans has no glyph for; the rest are invisible."""
    assert visual._plain("a → b") == "a -> b"
    assert visual._plain("no\u200bbreak") == "nobreak"
    assert visual._plain("a\u00a0b") == "a b"
    # And the ordinary text the PA writes is left alone.
    assert visual._plain("£12,500 — “yes” · 96,750") == "£12,500 — “yes” · 96,750", "over-eager"
