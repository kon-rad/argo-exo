"""Exo deck case for the Raspberry Pi 4: a hackable shell + base, swappable GPIO hatches, a camera bracket.

Run:  uv run --no-project --with build123d --with trimesh --with matplotlib python pi4_deck_case.py
Out:  out/*.stl, out/*.3mf, out/exo-deck-case.step, out/preview-*.png

Coordinates: board space. Origin at the Pi's SD-card end (x=0) and HDMI edge (y=0); z=0 is the bottom of the case.
Pi 4 numbers are from the Raspberry Pi 4 mechanical drawing; every value marked MEASURE should be checked with
calipers on the real board and parts before the full print (print `coupon` first).
"""
import json
import math
from pathlib import Path

from build123d import (Align, Axis, Box, Compound, Cylinder, Face, Kind, Mesher, Polygon, Pos, RectangleRounded,
                       RegularPolygon, Side, Spline, Text, Wire, chamfer, export_step, export_stl, extrude)

HERE = Path(__file__).parent
OUT = HERE / "out"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
EMBLEM = HERE / "argo_emblem.json"      # traced from Areas/argo/design/argo-emblem-only.png: 3 light + 3 dark facets

# --- Raspberry Pi 4 (mechanical drawing) ---
BW, BD, PCB = 85.0, 56.0, 1.4
HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]      # M2.5
USBC_X, HDMI0_X, HDMI1_X, AUDIO_X = 11.2, 26.0, 39.5, 53.5         # along the y=0 edge
GPIO_X, GPIO_Y, GPIO_LEN = 32.5, 52.5, 51.0                        # 2x20 header centre
CSI_X, CSI_Y = 45.0, 11.5                                          # camera connector centre (MEASURE)
SOC_X, SOC_Y = 29.0, 31.0                                          # fan sits over the SoC (MEASURE)
SD_Y = 28.0                                                        # microSD slot centre, under the board
TALLEST = 16.0                                                     # USB-A stack above the PCB

# --- case ---
CL = 1.0          # gap board -> inner wall
WALL = 2.2
FLOOR = 3.0
STANDOFF = 3.0
LID = 3.0
FIT = 0.3         # base plate -> shell clearance per side
PCB_Z0 = FLOOR + STANDOFF
PCB_Z1 = PCB_Z0 + PCB
CEIL = PCB_Z1 + TALLEST + 1.1        # inside of the lid
TOP = CEIL + LID

# --- the look: a tapered, rounded carapace (walls lean in 5 degrees, soft corners, a chamfered lid edge) ---
TAPER = 5.0        # degrees the walls lean in; printed lid-down this is a gentle outward overhang, no supports
R_CORNER = 9.0     # plan-view corner radius at the bottom
EDGE = 1.5         # 45-degree chamfer round the lid (prints cleanly on the bed; a fillet there would droop)
INSET = (TOP - FLOOR) * math.tan(math.radians(TAPER))

IX0, IX1, IY0, IY1 = -CL, BW + CL, -CL, BD + CL                    # inner box
OX0, OX1, OY0, OY1 = IX0 - WALL - INSET, IX1 + WALL + INSET, IY0 - WALL - INSET, IY1 + WALL + INSET  # outer, at the bottom
LID_FLAT = (IX0 - WALL + EDGE, IX1 + WALL - EDGE, IY0 - WALL + EDGE, IY1 + WALL - EDGE)            # flat top of the lid

# --- parts ---
FAN = 25.0         # official Pi 4 Case Fan, measured by Konrad 2026-10-15
FAN_HOLE = 20.0    # centre to centre, measured (holes in 3 corners; the lid has all 4 so it fits any way round)
SELF_TAP_M3 = 2.5  # pilot for an M3 screw straight into plastic
SELF_TAP_M25 = 2.2
GRID = 10.0        # hack grid pitch on the lid and the base
STRAP = 30.0       # webbing width of the shoulder strap (Konrad's strap: 3 cm, 2026-10-15)
EAR = 15.0         # strap ear length beyond each end of the case

# --- branding, engraved into the lid ---
LABEL = "EXO ARGO"
VERSION = "V0.1"
URL = "exo.myargoquest.com"
ENGRAVE = 0.6      # text depth; the emblem's light facets go this deep, the dark ones twice as deep


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl(x, y, z0, z1, d):
    return Pos(x, y, (z0 + z1) / 2) * Cylinder(d / 2, z1 - z0)


def outer(inset=0.0):
    """The tapered rounded body, shrunk by inset all round (inset > 0 is used to cut the wall grooves)."""
    f = Pos((OX0 + OX1) / 2, (OY0 + OY1) / 2, FLOOR) * RectangleRounded(OX1 - OX0 - 2 * inset, OY1 - OY0 - 2 * inset,
                                                                         R_CORNER - inset)
    return extrude(f, amount=TOP - FLOOR, taper=TAPER)


def vein(points, width=0.9, depth=0.45):
    """An engraved channel along a smooth curve on the lid: the case's "veins"."""
    w = Wire([Spline(*points)]).offset_2d(width / 2, kind=Kind.ARC, side=Side.BOTH)
    return extrude(Pos(0, 0, TOP - depth) * Face(w), amount=depth + 1)


# veins run out from the fan "heart" round the other features (checked against lid_map.py's depth map)
VEINS = [
    [(40.5, 33.0), (46.0, 36.0), (51.0, 41.0), (55.0, 44.5)],
    [(40.5, 28.0), (46.0, 25.0), (50.0, 20.0), (54.0, 17.0)],
    [(17.5, 28.0), (12.5, 25.0), (10.0, 19.0), (11.5, 12.0), (15.0, 8.5)],
    [(29.0, 19.5), (29.5, 15.5), (27.0, 11.0), (22.0, 8.5)],
    [(17.5, 34.0), (12.5, 38.0), (10.0, 43.0), (11.0, 45.5)],
]
GROOVES = (PCB_Z1 + 3.5, PCB_Z1 + 10.5)   # heights of the two segment grooves round the walls


def shell():
    """Walls + lid in one print (print it upside down, lid on the bed: no supports). Sits on the base's rim."""
    body = outer()
    body = chamfer(body.edges().group_by(Axis.Z)[-1], length=EDGE)
    cavity = extrude(Pos((IX0 + IX1) / 2, (IY0 + IY1) / 2, -1) * RectangleRounded(IX1 - IX0, IY1 - IY0, 3.0),
                     amount=CEIL + 1)
    s = body - cavity
    for z in GROOVES:                                                    # segmented, exoskeleton-like walls
        band = box(OX0 - 5, OX1 + 5, OY0 - 5, OY1 + 5, z - 0.5, z + 0.5)
        s -= band - outer(0.6)

    # HDMI edge: open-bottom notches so the shell lowers over the connectors
    w0, w1 = OY0 - 1, IY0 + 1
    s -= box(USBC_X - 6.5, USBC_X + 6.5, w0, w1, -1, PCB_Z1 + 6.5)          # USB-C power
    s -= box(HDMI0_X - 6.5, HDMI1_X + 6.5, w0, w1, -1, PCB_Z1 + 7.0)        # both micro-HDMI
    s -= box(AUDIO_X - 4.0, AUDIO_X + 4.0, w0, w1, -1, PCB_Z1 + 7.5)        # 3.5 mm audio
    # USB / Ethernet end: open, with corner pillars left
    s -= box(IX1 - 1, OX1 + 1, 0.5, BD - 0.5, -1, CEIL - 0.7)
    # SD end: card notch + exhaust vents
    s -= box(OX0 - 1, IX0 + 1, SD_Y - 6, SD_Y + 6, -1, PCB_Z1 + 2)
    for y in (6, 11, 16, 40, 45, 50):
        s -= box(OX0 - 1, IX0 + 1, y - 1, y + 1, PCB_Z1 + 5, CEIL - 2.5)
    # GPIO edge: two knock-out panels (0.6 mm skin, cut through with a knife for a new cable)
    for x0 in (8, 40):
        s -= box(x0, x0 + 22, IY1 + 0.6, OY1 + 1, PCB_Z1 + 4, CEIL - 3)

    # lid: hexagonal fan grille (the emblem's hexagon, as a honeycomb) + fan screws
    for i in range(-5, 6):
        for j in range(-5, 6):
            x, y = SOC_X + i * 2.9 + (1.45 if j % 2 else 0), SOC_Y + j * 2.5
            if (x - SOC_X) ** 2 + (y - SOC_Y) ** 2 <= (FAN / 2 - 2.5) ** 2:
                s -= extrude(Pos(x, y, CEIL - 1) * RegularPolygon(1.3, 6), amount=LID + 2)
    for dx in (-FAN_HOLE / 2, FAN_HOLE / 2):
        for dy in (-FAN_HOLE / 2, FAN_HOLE / 2):
            s -= cyl(SOC_X + dx, SOC_Y + dy, CEIL - 1, TOP + 1, 2.7)         # M2 or M2.5 fan screws
    # lid: GPIO hatch opening (a 40-pin IDC plug passes) + its two screw holes
    s -= box(GPIO_X - GPIO_LEN / 2 - 3, GPIO_X + GPIO_LEN / 2 + 3, GPIO_Y - 5, IY1 - 0.2, CEIL - 1, TOP + 1)
    for x in hatch_screws():
        s -= cyl(x, GPIO_Y - 0.5, TOP - 2.8, TOP + 1, SELF_TAP_M3)
    # lid: camera ribbon slot over the CSI connector
    s -= box(CSI_X - 11, CSI_X + 11, CSI_Y - 2.25, CSI_Y + 2.25, CEIL - 1, TOP + 1)
    # lid: M3 self-tap holes (blind): two for the camera bracket, a column of three for anything else
    for x, y in CAMERA_HOLES + LID_HOLES:
        s -= cyl(x, y, TOP - 2.8, TOP + 1, SELF_TAP_M3)
    s -= branding()
    for pts in VEINS:
        s -= vein(pts)

    # posts that clamp the board onto the base standoffs; base screws thread into them
    for x, y in HOLES:
        post = cyl(x, y, PCB_Z1, CEIL + 0.5, 5.5) - cyl(x, y, PCB_Z1 - 1, PCB_Z1 + 10, SELF_TAP_M25)
        s += post
    return s


CAMERA_HOLES = [(62.0, 3.0), (82.0, 3.0)]      # 20 mm apart, matches camera_bracket(); bracket then spans y -2..24
LID_HOLES = [(6.0, 20.0), (6.0, 30.0), (6.0, 40.0)]


def text_cut(txt, x, y, max_w, size, depth, align=Align.MIN):
    """Engrave txt (left edge, or centre with Align.CENTER, at x; centred on y) into the lid top, shrinking the
    font until it fits max_w."""
    while True:
        t = Text(txt, font_size=size, font_path=FONT, align=(align, Align.CENTER))
        if t.bounding_box().size.X <= max_w or size < 2.5:
            break
        size *= 0.95
    return extrude(Pos(x, y, TOP - depth) * t, amount=depth + 1)


def emblem_cut(x, y, height):
    """The Argo emblem: light facets ENGRAVE deep, dark facets 2 x ENGRAVE, so the pinwheel reads in one colour."""
    facets = json.loads(EMBLEM.read_text())
    cut = None
    for shade, depth in (("light", ENGRAVE), ("dark", 2 * ENGRAVE)):
        for poly in facets[shade]:
            pts = [(x + u * height, y + v * height) for u, v in poly]
            f = extrude(Pos(0, 0, TOP - depth) * Polygon(*pts, align=None), amount=depth + 1)
            cut = f if cut is None else cut + f
    return cut


def branding():
    """Emblem top right with "EXO ARGO" and "V0.1" on two lines under it, the URL along the HDMI edge (the camera bracket covers the
    bottom right)."""
    c = emblem_cut(72.0, 40.0, 12.0)
    c += text_cut(LABEL, 72.0, 31.0, 27.0, 4.6, ENGRAVE, align=Align.CENTER)
    c += text_cut(VERSION, 72.0, 26.6, 27.0, 3.6, ENGRAVE, align=Align.CENTER)
    c += text_cut(URL, 0.0, 3.0, 54.0, 4.2, ENGRAVE)
    return c


def hatch_screws():
    return [GPIO_X - GPIO_LEN / 2 - 6.5, GPIO_X + GPIO_LEN / 2 + 6.5]


def base():
    """Floor plate the shell sits on, with a rim that locates the shell, standoffs, and a strap ear at each end:
    the shoulder strap feeds down through one ear, runs under the case and up through the other. Screws go up from
    below through the Pi into the shell posts."""
    b = extrude(Pos((OX0 + OX1) / 2, (OY0 + OY1) / 2, 0) * RectangleRounded(OX1 - OX0, OY1 - OY0, R_CORNER),
                amount=FLOOR)
    rim = box(IX0 + FIT, IX1 - FIT, IY0 + FIT, IY1 - FIT, FLOOR, FLOOR + 1.5)
    rim -= box(IX0 + FIT + 1.0, IX1 - FIT - 1.0, IY0 + FIT + 1.0, IY1 - FIT - 1.0, FLOOR - 1, FLOOR + 3)  # 1.0: not tangent to the standoffs
    rim -= box(IX0 - 1, IX0 + 3, SD_Y - 6, SD_Y + 6, FLOOR - 1, FLOOR + 3)   # SD card path
    b += rim
    w = STRAP + 10.0
    for x0, x1 in ((OX0 - EAR, OX0 + 8.0), (OX1 - 8.0, OX1 + EAR)):     # rounded lugs, like a watch's
        b += extrude(Pos((x0 + x1) / 2, BD / 2, 0) * RectangleRounded(x1 - x0, w, 6.0), amount=FLOOR)
    for xs in (OX0 - EAR + 4.0, OX1 + EAR - 4.0 - 4.5):                  # slots, 4.5 mm for thick webbing
        b -= extrude(Pos(xs + 2.25, BD / 2, -1) * RectangleRounded(4.5, STRAP + 1.5, 2.0), amount=FLOOR + 2)
    for x, y in HOLES:
        b += cyl(x, y, FLOOR, PCB_Z0, 6.0)
        b -= cyl(x, y, -1, PCB_Z0 + 1, 2.8)
        b -= cyl(x, y, -1, 1.6, 5.2)                                     # counterbore for the M2.5 head
    for x in (10.0, 30.0, 50.0, 70.0):                                   # underside hack grid (clip, strap plate)
        for y in (10.0, 30.0, 50.0):
            b -= cyl(x, y, -1, FLOOR - 0.6, SELF_TAP_M3)
    return b


def hatch(kind):
    """GPIO hatch plates: blank, ribbon (a 40-way flat cable passes), open (the IDC plug passes)."""
    x0, x1 = GPIO_X - GPIO_LEN / 2 - 10, GPIO_X + GPIO_LEN / 2 + 10
    y0, y1 = GPIO_Y - 6.2, LID_FLAT[3] + 0.3                          # clears the fan screw heads; ends at the lid edge
    h = box(x0, x1, y0, y1, 0, 2.0)
    for x in hatch_screws():
        h -= cyl(x, GPIO_Y - 0.5, -1, 3, 3.4)
    if kind == "ribbon":
        h -= box(GPIO_X - 26.5, GPIO_X + 26.5, GPIO_Y - 1.8, GPIO_Y + 0.8, -1, 3)
    elif kind == "open":
        h -= box(GPIO_X - GPIO_LEN / 2 - 3, GPIO_X + GPIO_LEN / 2 + 3, GPIO_Y - 5, IY1 - 0.2, -1, 3)
    return h


def camera_bracket():
    """Flat bracket for the Pi Camera Rev 1.3 (25 x 24 mm, M2 holes 21 x 12.5): two M3 screws into the lid grid."""
    p = box(-15, 15, -9, 17, 0, 2.5)
    for x in (-10.0, 10.0):
        p -= cyl(x, -4, -1, 3.5, 3.4)                                    # 20 mm = two grid steps
    for x in (-10.5, 10.5):
        for y in (2.0, 14.5):
            p += cyl(x, y, 2.5, 5.5, 4.2)
            p -= cyl(x, y, 1.0, 6.0, 1.8)                                # M2 self-tap
    return p


def coupon():
    """20-minute fit test: a Pi hole post, a USB-C notch, a grid hole, an M2 camera boss."""
    c = box(0, 40, 0, 20, 0, 3)
    c += cyl(6, 10, 3, 6, 6.0) - cyl(6, 10, -1, 7, 2.8)
    c += box(14, 32, 16, 20, 3, 14) - box(16.5, 29.5, 15, 21, 2, 12.5)
    c -= cyl(36, 10, 0.4, 4, SELF_TAP_M3)
    c += cyl(36, 4, 3, 6, 4.2) - cyl(36, 4, 1, 7, 1.8)
    return c


def parts():
    return {
        "shell": shell(),
        "base": base(),
        "hatch-blank": hatch("blank"),
        "hatch-ribbon": hatch("ribbon"),
        "hatch-open": hatch("open"),
        "camera-bracket": camera_bracket(),
        "coupon": coupon(),
    }


def main():
    OUT.mkdir(exist_ok=True)
    ps = parts()
    for name, p in ps.items():
        assert p.is_valid, f"{name} is not a valid solid"
        export_stl(p, str(OUT / f"{name}.stl"))
        m = Mesher()
        m.add_shape(p)
        m.write(str(OUT / f"{name}.3mf"))
        bb = p.bounding_box()
        print(f"{name:15s} {bb.size.X:6.1f} x {bb.size.Y:5.1f} x {bb.size.Z:5.1f} mm  volume {p.volume / 1000:6.1f} cm3")
    export_step(Compound([ps["shell"], ps["base"]]), str(OUT / "exo-deck-case.step"))
    print(f"inside height {CEIL - PCB_Z1:.1f} mm above the board; outer {OX1 - OX0:.1f} x {OY1 - OY0:.1f} x {TOP:.1f} mm")


if __name__ == "__main__":
    main()
