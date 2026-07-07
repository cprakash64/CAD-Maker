"""Regenerate the 3 committed structured sketch fixtures (run: python this.py).

Kept in the repo so the binary PNG fixtures used by
test_drawing_sketch_reconstruction.py are reproducible.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

DATA = Path(__file__).resolve().parent
W = 3


def rect_plate():
    img = Image.new("RGB", (900, 640), "white"); d = ImageDraw.Draw(img)
    d.rounded_rectangle((80, 80, 820, 560), radius=60, outline="black", width=W)
    for cx, cy in ((190, 190), (710, 190), (190, 450), (710, 450)):
        d.ellipse((cx-40, cy-40, cx+40, cy+40), outline="black", width=W)
        d.ellipse((cx-18, cy-18, cx+18, cy+18), outline="black", width=W)
    d.ellipse((400, 150, 500, 250), outline="black", width=W)
    d.rectangle((400, 380, 500, 470), outline="black", width=W)
    img.save(DATA / "rect_rounded_plate_counterbore_slot.png")


def bracket():
    """Vertical bracket: rounded top, top concentric boss+hole, 2 lower holes,
    inner rectangular cutout."""
    img = Image.new("RGB", (520, 760), "white"); d = ImageDraw.Draw(img)
    # rounded-top body
    d.rounded_rectangle((120, 90, 400, 680), radius=70, outline="black", width=W)
    # top concentric boss + hole
    d.ellipse((260-70, 200-70, 260+70, 200+70), outline="black", width=W)
    d.ellipse((260-30, 200-30, 260+30, 200+30), outline="black", width=W)
    # two lower holes
    d.ellipse((190-24, 560-24, 190+24, 560+24), outline="black", width=W)
    d.ellipse((330-24, 560-24, 330+24, 560+24), outline="black", width=W)
    # inner rectangular cutout (middle)
    d.rectangle((210, 340, 310, 470), outline="black", width=W)
    img.save(DATA / "vertical_bracket_boss_holes_cutout.png")


def diamond():
    """Symmetric diamond/oval plate: center hole, 2 end holes, 2 curved arc slots."""
    img = Image.new("RGB", (900, 560), "white"); d = ImageDraw.Draw(img)
    cyc = 280
    # oval/diamond outer (capsule-ish)
    d.rounded_rectangle((60, 90, 840, 470), radius=150, outline="black", width=W)
    # center hole
    d.ellipse((450-40, cyc-40, 450+40, cyc+40), outline="black", width=W)
    # two end holes
    d.ellipse((150-26, cyc-26, 150+26, cyc+26), outline="black", width=W)
    d.ellipse((750-26, cyc-26, 750+26, cyc+26), outline="black", width=W)
    # two arc slots: closed annular sectors around the plate centre, one opening
    # to the right (-50..50 deg) and one to the left (130..230 deg).
    cxc, r_out, r_in = 450, 130, 104
    for a0d, a1d in ((-52, 52), (128, 232)):
        a0, a1 = math.radians(a0d), math.radians(a1d)
        outer = [(cxc + r_out*math.cos(a0 + (a1-a0)*t/60), cyc + r_out*math.sin(a0 + (a1-a0)*t/60)) for t in range(61)]
        inner = [(cxc + r_in*math.cos(a1 - (a1-a0)*t/60), cyc + r_in*math.sin(a1 - (a1-a0)*t/60)) for t in range(61)]
        d.line(outer + inner + [outer[0]], fill="black", width=W)
    img.save(DATA / "symmetric_diamond_plate_arc_slots.png")


def vertical_bracket_concentric_cutouts():
    """Full vertical bracket (the field drawing): rounded body, base strip, a TOP
    concentric group (3 rings Ø8.2/Ø4.8/Ø3.6), an upper semicircular cutout, a
    central rectangular cutout, and TWO lower counterbored holes. The base strip
    is an internal full-width line that used to split the flood interior and drop
    the top group + rectangle — this fixture pins that no longer happens."""
    img = Image.new("RGB", (560, 860), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((120, 70, 440, 770), radius=90, outline="black", width=W)
    d.line((120, 720, 440, 720), fill="black", width=W)          # base strip / step
    cx, cy = 280, 190                                            # top concentric group
    for r in (70, 41, 31):                                       # Ø8.2 / Ø4.8 / Ø3.6
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline="black", width=W)
    d.arc((210, 300, 350, 420), start=180, end=360, fill="black", width=W)
    d.line((210, 360, 350, 360), fill="black", width=W)          # upper semicircle chord
    d.rectangle((235, 470, 325, 590), outline="black", width=W)  # central rectangular cutout
    for lx in (205, 355):                                        # two lower counterbores
        d.ellipse((lx - 30, 660 - 30, lx + 30, 660 + 30), outline="black", width=W)
        d.ellipse((lx - 14, 660 - 14, lx + 14, 660 + 14), outline="black", width=W)
    img.save(DATA / "vertical_bracket_concentric_cutouts.png")


if __name__ == "__main__":
    # Regenerate the committed sketch fixtures (run from the backend/ dir).
    rect_plate()
    bracket()
    diamond()
    vertical_bracket_concentric_cutouts()
    print("wrote rect_rounded_plate_counterbore_slot.png, "
          "vertical_bracket_boss_holes_cutout.png, "
          "symmetric_diamond_plate_arc_slots.png, "
          "vertical_bracket_concentric_cutouts.png")
