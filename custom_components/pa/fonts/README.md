# The font the pictures are set in

`NotoSans-Regular.ttf` is Noto Sans Regular, from
[notofonts/noto-fonts](https://github.com/notofonts/noto-fonts) at `hinted/ttf/NotoSans/`, under the
SIL Open Font License 1.1 — `OFL.txt` is beside it, because the licence requires it to travel with
the font.

**Why it is here rather than using what Pillow already has.** `ImageFont.load_default(size=…)` is
Aileron, and Aileron has no `£`, no em dash, no bullet and no arrow. The PA writes pounds and em
dashes in almost every answer — *"£12,500"*, *"— he said"* — so this is the ordinary case and not an
edge: the first picture of one of its cards arrived with a missing-glyph box where the dash was, and
a picture whose whole job is to be read from across a room cannot have those in it.

Of the characters the PA uses, only `→` is still missing from Noto Sans, and `visual.py` swaps it.
