"""Exo deck case for the Raspberry Pi 4: a hackable shell + base, swappable GPIO hatches, a camera bracket.

Run:  uv run --no-project --with build123d --with shapely --with numpy python pi4_deck_case.py
Out:  out/*.stl, out/*.3mf, out/exo-deck-case.step, out/preview-*.png

Coordinates: board space. Origin at the Pi's SD-card end (x=0) and HDMI edge (y=0); z=0 is the bottom of the case.
Pi 4 numbers are from the Raspberry Pi 4 mechanical drawing; every value marked MEASURE should be checked with
calipers on the real board and parts before the full print (print `coupon` first).
"""
import json
from pathlib import Path

import numpy as np
from build123d import (Align, Box, Compound, Cylinder, Face, Mesher, Plane, Polygon, Pos, Rectangle, RectangleRounded,
                       RegularPolygon, Text, Vector, Wire, export_step, export_stl, extrude)
from shapely.geometry import MultiPoint, Point, box as sbox
from shapely.geometry.polygon import orient
from shapely.ops import unary_union, voronoi_diagram

HERE = Path(__file__).parent
OUT = HERE / "out"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
EMBLEM = HERE / "argo_emblem.json"      # traced from Areas/argo/design/argo-emblem-only.png: 3 light + 3 dark facets

# --- Raspberry Pi 4 (mechanical drawing) ---
BW, BD, PCB = 85.0, 56.0, 1.4
HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]      # M2.5
USBC_X, HDMI0_X, HDMI1_X, AUDIO_X = 11.2, 26.0, 39.5, 53.5         # along the y=0 edge
GPIO_X, GPIO_Y, GPIO_LEN = 32.5, 52.5, 51.0                        # 2x20 header centre
CSI_X, CSI_Y = 45.0, 11.5                                          # camera connector centre (photo IMG_7474 agrees; MEASURE)
CSI_SLOT = (4.5, 19.0)        # x, y: the connector lies ACROSS the board (long side square to the HDMI edge), so the
                              # 16 mm ribbon rises straight out of it with its width along y; 19 mm leaves 1.5 each side
SOC_X, SOC_Y = 28.0, 33.0                                          # fan sits over the SoC: heatsink centre in photos
                                                                   # IMG_7474 + IMG_7482 (ruler), 2026-10-16; calipers later
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

# --- the look: a straight-walled hull (walls square to the lid and the base, only the vertical corners rounded) ---
R_CORNER = 8.0     # plan-view corner radius; lid and base edges stay crisp
LINE = 0.8         # width of the engraved lid network (2 nozzle widths)
ETCH = 0.6         # its depth: 3 layers at 0.2 mm, printed on the bed face

IX0, IX1, IY0, IY1 = -CL, BW + CL, -CL, BD + CL                    # inner box
OX0, OX1, OY0, OY1 = IX0 - WALL, IX1 + WALL, IY0 - WALL, IY1 + WALL  # outer: the same at every height

# --- parts ---
FAN = 25.0         # official Pi 4 Case Fan, measured by Konrad 2026-10-15
FAN_HOLE = 20.0    # centre to centre, measured (holes in 3 corners; the lid has all 4 so it fits any way round)
FAN_T = 6.0        # fan frame thickness, measured by Konrad 2026-10-16
HEATSINK = 8.0     # finned SoC heatsink, about 17 x 17 mm in the photos; height NOT measured (estimate, incl. the chip)
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


def outline(inset=0.0):
    """The rounded hull footprint, shrunk by inset all round."""
    return Pos((OX0 + OX1) / 2, (OY0 + OY1) / 2) * RectangleRounded(OX1 - OX0 - 2 * inset, OY1 - OY0 - 2 * inset,
                                                                     R_CORNER - inset)


def outer(inset=0.0):
    """The straight-walled body from the base seam to the lid (inset > 0 is used to cut the wall bands)."""
    return extrude(Pos(0, 0, FLOOR) * outline(inset), amount=TOP - FLOOR)


GROOVES = (PCB_Z1 + 3.5, PCB_Z1 + 10.5)   # heights of the two segment grooves round the walls


def shell():
    """Walls + lid in one print (print it upside down, lid on the bed: no supports). Sits on the base's rim."""
    body = outer()
    cavity = extrude(Pos((IX0 + IX1) / 2, (IY0 + IY1) / 2, -1) * RectangleRounded(IX1 - IX0, IY1 - IY0, 3.0),
                     amount=CEIL + 1)
    s = body - cavity
    for z in GROOVES:                                                    # exoskeleton segments round the walls
        band = box(OX0 - 5, OX1 + 5, OY0 - 5, OY1 + 5, z - 0.5, z + 0.5)
        s -= band - outer(0.6)

    # HDMI edge: open-bottom notches so the shell lowers over the connectors
    w0, w1 = OY0 - 1, IY0 + 1
    s -= box(USBC_X - 6.5, USBC_X + 6.5, w0, w1, -1, PCB_Z1 + 6.5)          # USB-C power
    s -= box(HDMI0_X - 6.5, HDMI1_X + 6.5, w0, w1, -1, PCB_Z1 + 7.0)        # both micro-HDMI
    s -= box(AUDIO_X - 4.0, AUDIO_X + 4.0, w0, w1, -1, PCB_Z1 + 7.5)        # 3.5 mm audio
    # USB / Ethernet end: open, with corner pillars left
    s -= box(IX1 - 1, OX1 + 1, 0.5, BD - 0.5, -1, CEIL - 0.7)
    # SD end: card notch + hexagonal "spiracle" exhaust vents in two staggered rows, pointy side up (no supports)
    s -= box(OX0 - 1, IX0 + 1, SD_Y - 6, SD_Y + 6, -1, PCB_Z1 + 2)
    for y, z in SPIRACLES:
        s -= extrude(Plane.YZ.offset(OX0 - 1) * Pos(y, z) * RegularPolygon(1.8, 6, rotation=30), amount=WALL + 3)
    # GPIO edge: two knock-out panels (0.6 mm skin, cut through with a knife for a new cable)
    for x0 in (8, 40):
        s -= box(x0, x0 + 22, IY1 + 0.6, OY1 + 1, PCB_Z1 + 4, CEIL - 3)

    # lid: the compound eye, a honeycomb fan grille + fan screws
    for i in range(-5, 6):
        for j in range(-5, 6):
            x, y = SOC_X + i * 2.9 + (1.45 if j % 2 else 0), SOC_Y + j * 2.5
            if (x - SOC_X) ** 2 + (y - SOC_Y) ** 2 <= EYE ** 2:
                s -= extrude(Pos(x, y, CEIL - 1) * RegularPolygon(1.3, 6), amount=LID + 2)
    for x, y in fan_screws():
        s -= cyl(x, y, CEIL - 1, TOP + 1, 2.7)                           # M2 or M2.5 fan screws
    # lid: GPIO hatch opening (a 40-pin IDC plug passes) + its two screw holes
    s -= box(GPIO_X - GPIO_LEN / 2 - 3, GPIO_X + GPIO_LEN / 2 + 3, GPIO_Y - 5, IY1 - 0.2, CEIL - 1, TOP + 1)
    for x in hatch_screws():
        s -= cyl(x, GPIO_Y - 0.5, TOP - 2.8, TOP + 1, SELF_TAP_M3)
    # lid: camera ribbon slot over the CSI connector
    sw, sl = CSI_SLOT
    s -= box(CSI_X - sw / 2, CSI_X + sw / 2, CSI_Y - sl / 2, CSI_Y + sl / 2, CEIL - 1, TOP + 1)
    # 1 mm 45-degree lead-in on the outer edge so the ribbon bends over a slope, not a sharp edge
    s -= extrude(Pos(CSI_X, CSI_Y, TOP - 1.0) * Rectangle(sw, sl), amount=1.01, taper=-45)
    # lid: M3 self-tap holes (blind): two for the camera bracket, a column of three for anything else
    for x, y in CAMERA_HOLES + LID_HOLES:
        s -= cyl(x, y, TOP - 2.8, TOP + 1, SELF_TAP_M3)
    s -= branding()
    s -= network()

    # posts that clamp the board onto the base standoffs; base screws thread into them
    for x, y in HOLES:
        post = cyl(x, y, PCB_Z1, CEIL + 0.5, 5.5) - cyl(x, y, PCB_Z1 - 1, PCB_Z1 + 10, SELF_TAP_M25)
        s += post
    return s


CAMERA_HOLES = [(62.0, 3.0), (82.0, 3.0)]      # 20 mm apart, matches camera_bracket(); bracket then spans y -2..24
LID_HOLES = [(6.0, 20.0), (6.0, 30.0), (6.0, 40.0)]
EYE = 9.5                                       # grille cells sit within this radius of the fan centre
SPIRACLES = [(y, 14.4) for y in (5.0, 10.0, 15.0, 41.0, 46.0, 51.0)] + \
            [(y, 21.2) for y in (7.5, 12.5, 39.0, 48.5)]
PANEL = (58.0, 86.2, 23.0, 46.4)                # branding panel frame on the lid: x0, x1, y0, y1


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
    """Emblem in the framed panel top right with "EXO ARGO" and "V0.1" on two lines under it; the URL is a hull
    marking on the HDMI-side wall, above the upper band (the camera slot crosses the lid's HDMI edge)."""
    cx = (PANEL[0] + PANEL[1]) / 2
    c = emblem_cut(cx, 39.4, 11.0)
    c += text_cut(LABEL, cx, 31.0, 24.0, 4.6, ENGRAVE, align=Align.CENTER)
    c += text_cut(VERSION, cx, 26.6, 24.0, 3.6, ENGRAVE, align=Align.CENTER)
    url = Text(URL, font_size=4.2, font_path=FONT, align=(Align.CENTER, Align.CENTER))
    c += extrude(Plane.XZ.offset(-(OY0 + ENGRAVE)) * Pos((OX0 + OX1) / 2, (GROOVES[1] + 0.5 + TOP) / 2) * url, amount=2)
    return c


def faces_of(geom):
    """shapely (Multi)Polygon -> build123d faces on z = 0."""
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    for p in polys:
        if p.area < 0.05:
            continue
        p = orient(p, sign=1.0)                     # counter-clockwise outside: the face normal points up
        ring = lambda r: Wire.make_polygon([Vector(x, y, 0) for x, y in list(r.coords)[:-1]], close=True)
        yield Face(ring(p.exterior), [ring(r) for r in p.interiors])


def network_2d():
    """The lid's engraved network, in 2D: a dragonfly-wing vein net (Voronoi cells, small round the fan and larger
    towards the edges) with a node at every junction, framed by a hull line round the lid, a ring round the fan eye,
    rings round every screw hole, a docking frame round the camera slot and a panel round the branding."""
    r = LINE / 2
    border = sbox(OX0 + 2, OY0 + 2, OX1 - 2, OY1 - 2).buffer(-(R_CORNER - 2)).buffer(R_CORNER - 2, quad_segs=16)
    eye = Point(SOC_X, SOC_Y).buffer(11.8, quad_segs=32)
    rings = [Point(x, y).buffer(2.3, quad_segs=16) for x, y in
             fan_screws() + CAMERA_HOLES + LID_HOLES]
    sw, sl = CSI_SLOT
    lead = sbox(CSI_X - sw / 2 - 1, CSI_Y - sl / 2 - 1, CSI_X + sw / 2 + 1, CSI_Y + sl / 2 + 1)   # slot + lead-in
    dock = lead.buffer(1.6, quad_segs=8)
    dock = unary_union([dock] + [g for g in rings if g.intersects(dock)])   # the frame wraps the nearby fan screw
    panel = sbox(PANEL[0], PANEL[2], PANEL[1], PANEL[3]).buffer(-2.5).buffer(2.5)
    frames = [border.exterior, eye.exterior, dock.exterior, panel.exterior] + [g.exterior for g in rings]

    # no veins inside the frames, near the GPIO opening or its screws
    keep_out = unary_union([eye, dock, panel] + rings + [
        sbox(GPIO_X - GPIO_LEN / 2 - 4, GPIO_Y - 6, GPIO_X + GPIO_LEN / 2 + 4, OY1 + 1)] + [Point(x, GPIO_Y - 0.5).buffer(2.3) for x in hatch_screws()])
    field = border.difference(keep_out)

    # seeds: a rosette round the eye, then dart-throwing with spacing that grows away from it
    rng = np.random.default_rng(2049)
    seeds = [(SOC_X + 15.5 * np.cos(a), SOC_Y + 15.5 * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 13)[:-1]]
    for _ in range(6000):
        x, y = rng.uniform(OX0, OX1), rng.uniform(OY0, OY1)
        d = np.hypot(x - SOC_X, y - SOC_Y)
        gap = 5.0 + 0.09 * max(d - 12, 0)
        if all(np.hypot(x - u, y - v) >= gap for u, v in seeds):
            seeds.append((x, y))
    edges = voronoi_diagram(MultiPoint(seeds), envelope=sbox(OX0 - 20, OY0 - 20, OX1 + 20, OY1 + 20), edges=True)
    veins = unary_union(edges).intersection(field)

    # nodes where three veins meet, away from the field's edge
    inner = field.buffer(-1.6)
    pts = {}
    for e in getattr(unary_union(edges), "geoms", []):
        for c in (e.coords[0], e.coords[-1]):
            k = (round(c[0], 3), round(c[1], 3))
            pts[k] = pts.get(k, 0) + 1
    nodes = [Point(k).buffer(0.85, quad_segs=8) for k, n in pts.items() if n >= 3 and inner.contains(Point(k))]

    net = unary_union([veins.buffer(r, quad_segs=4)] + [f.buffer(r, quad_segs=8) for f in frames] + nodes)
    net = net.buffer(0.2, quad_segs=4).buffer(-0.2, quad_segs=4)            # close slivers between lines
    # nothing under the GPIO hatch plate
    hatch_zone = sbox(GPIO_X - GPIO_LEN / 2 - 10.5, GPIO_Y - 6.7, GPIO_X + GPIO_LEN / 2 + 10.5, OY1 + 1)
    return net.difference(hatch_zone).simplify(0.03)


def network():
    faces = list(faces_of(network_2d()))
    return extrude([Pos(0, 0, TOP - ETCH) * f for f in faces], amount=ETCH + 1)


def fan_screws():
    return [(SOC_X + dx, SOC_Y + dy) for dx in (-FAN_HOLE / 2, FAN_HOLE / 2) for dy in (-FAN_HOLE / 2, FAN_HOLE / 2)]


def hatch_screws():
    return [GPIO_X - GPIO_LEN / 2 - 6.5, GPIO_X + GPIO_LEN / 2 + 6.5]


def base():
    """Floor plate the shell sits on, with a rim that locates the shell, standoffs, and a strap ear at each end:
    the shoulder strap feeds down through one ear, runs under the case and up through the other. Screws go up from
    below through the Pi into the shell posts."""
    b = extrude(outline(), amount=FLOOR)
    cx, cy = (IX0 + IX1) / 2, (IY0 + IY1) / 2
    rim = extrude(Pos(cx, cy, FLOOR) * RectangleRounded(IX1 - IX0 - 2 * FIT, IY1 - IY0 - 2 * FIT, 3.0 - FIT), amount=1.5)
    rim -= extrude(Pos(cx, cy, FLOOR - 1) * RectangleRounded(IX1 - IX0 - 2 * FIT - 2.0, IY1 - IY0 - 2 * FIT - 2.0,
                                                              2.0 - FIT), amount=4)   # 1.0 walls: not tangent to the standoffs
    rim -= box(IX0 - 1, IX0 + 3, SD_Y - 6, SD_Y + 6, FLOOR - 1, FLOOR + 3)   # SD card path
    b += rim
    w = STRAP + 10.0
    for sgn, xe in ((-1, OX0), (1, OX1)):                                # lugs with a faceted, hull-like nose
        tip, root, c = xe + sgn * EAR, xe - sgn * 8.0, 5.0
        pts = [(root, BD / 2 - w / 2), (tip - sgn * c, BD / 2 - w / 2), (tip, BD / 2 - w / 2 + c),
               (tip, BD / 2 + w / 2 - c), (tip - sgn * c, BD / 2 + w / 2), (root, BD / 2 + w / 2)]
        pts = pts if sgn > 0 else pts[::-1]                              # counter-clockwise, so it extrudes up
        b += extrude(Polygon(*pts, align=None), amount=FLOOR)
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
    y0, y1 = GPIO_Y - 6.2, OY1                                        # clears the fan screw heads; ends at the lid edge
    h = box(x0, x1, y0, y1, 0, 2.0) & extrude(outline(), amount=2.0)  # follows the hull's rounded corner
    for x in hatch_screws():
        h -= cyl(x, GPIO_Y - 0.5, -1, 3, 3.4)
    if kind == "ribbon":
        h -= box(GPIO_X - 26.5, GPIO_X + 26.5, GPIO_Y - 1.8, GPIO_Y + 0.8, -1, 3)
    elif kind == "open":
        h -= box(GPIO_X - GPIO_LEN / 2 - 3, GPIO_X + GPIO_LEN / 2 + 3, GPIO_Y - 5, IY1 - 0.2, -1, 3)
    return h


def camera_bracket():
    """Flat bracket for the Pi Camera Rev 1.3 (25 x 24 mm, M2 holes 21 x 12.5): two M3 screws into the lid grid."""
    p = extrude(Pos(0, 4, 0) * RectangleRounded(30, 26, 6.0), amount=2.5)
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
    gap = CEIL - FAN_T - (PCB_Z1 + HEATSINK)
    print(f"fan under the lid: {FAN_T} mm fan over a {HEATSINK} mm heatsink leaves {gap:.1f} mm" + ("" if gap >= 1 else "  << TOO TIGHT"))


if __name__ == "__main__":
    main()
