"""DRAFT, NOT FOR PRINTING (Konrad, 2026-10-16: final print files wait for the measurements in the vault note
current/brooch/exo-brooch-case-requirements.md). While DRAFT is True this writes previews only, no 3MF or STEP.

Exo brooch: a straight-walled, wing-veined case for the XIAO ESP32S3 Sense, the 0.96" OLED, four buttons,
two LEDs, the BME680, the IMU and a 503030 LiPo, on a cut perfboard. Same language as the deck case
(hardware/case/pi4_deck_case.py): square walls, rounded vertical corners, an engraved dragonfly-wing network with
nodes, a compound eye over the camera, hex spiracle vents.

Run:  uv run --no-project --with build123d --with shapely --with numpy python brooch_case.py
Out:  out/brooch-shell.3mf (print face down), out/brooch-back.3mf (print outer side down), out/brooch-keys.3mf,
      out/brooch.step, STLs.

Frame ("wearing" frame): x to the right and y up as seen from the front (a person facing the wearer); z from the
back (0, against the shirt) to the front face (T). Cavity corner (0, 0) is bottom left. Layout and the hole-by-hole
board plan: vault note current/brooch/exo-brooch-perfboard-layout.md. Values marked MEASURE are estimates (spec
sheets, or the breadboard photo IMG_7476 scaled by its 2.54 mm hole pitch) to check with calipers before printing.
"""
import json
import math
from pathlib import Path

import numpy as np
from build123d import (Align, Box, Compound, Cone, Cylinder, Face, Mesher, Plane, Pos, RectangleRounded,
                       RegularPolygon, Rot, Text, Vector, Wire, export_step, export_stl, extrude, mirror, Polygon)
from shapely.geometry import MultiPoint, Point, box as sbox
from shapely.geometry.polygon import orient
from shapely.ops import unary_union, voronoi_diagram

HERE = Path(__file__).parent
OUT = HERE / "out"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
EMBLEM = HERE.parent / "case" / "argo_emblem.json"

# --- case ---
WALL = 2.0
FACE = 1.6
PLATE = 2.0           # back plate
LIP = 0.8             # the walls run this far behind the plate; the top wall's hook lip lives here
FIT = 0.2             # back plate -> wall clearance per side
R_CORNER = 7.0        # outer plan-view corner radius
R_IN = 3.0            # cavity corner radius (every part's corner is checked against it)
LINE, ETCH = 0.7, 0.5 # face network: line width, depth (a smaller face than the deck's, so a finer line)
IW, IH = 51.0, 52.4   # cavity
T = 19.8              # total thickness, back rim to face
Z_PLATE0, Z_PLATE1 = LIP, LIP + PLATE        # back plate, 0.8 .. 2.8
Z_FACE_IN = T - FACE                         # 18.2

# --- the perfboard P (cut from a 2.54 mm universal board): 11 x 18 holes, components on its front ---
P_COLS, P_ROWS, PITCH = 11, 18, 2.54
P_X0, P_Y0 = 1.0, 4.8
P_W, P_H = P_COLS * PITCH, P_ROWS * PITCH    # 27.94 x 45.72
P_FRONT = Z_FACE_IN - 6.0                    # 12.2: leaves room for the OLED stack and the key flanges
H_BTN = 5.0           # 6x6 tactile switch height incl. plunger (MEASURE: #57 is an assorted bag)


def hole(c, r):
    """Centre of perfboard hole (column c, row r); fractional values are between holes."""
    return P_X0 + PITCH / 2 + PITCH * c, P_Y0 + PITCH / 2 + PITCH * r


DRAFT = True
# three keys (exo-brooch-controls, 2026-10-16): MARK and TALK side by side for the Private pinch, APPROVE below and
# furthest from the edge
KEYS = {"MARK": hole(1.5, 5), "TALK": hole(5.5, 5), "APPROVE": hole(5.5, 1)}
LEDS = {"STATUS": hole(9.5, 1), "REC": hole(9.5, 5)}
OLED_PINS_X = hole(5.5, 17)[0]               # 4-pin header in holes c4..c7, row 17
OLED_W, OLED_H = 27.3, 27.8                  # module (photo + typical 0.96" module; MEASURE)
OLED_TOP = hole(0, 17)[1] + 1.6              # header 1.6 mm from the module's top edge (MEASURE)
OLED_WIN = (24.0, 13.0)                      # window over the 21.7 x 10.9 active area
OLED_WIN_C = (OLED_PINS_X, OLED_TOP - OLED_H / 2 + 1.0)   # active area a little above the module centre (MEASURE)

# --- XIAO ESP32S3 + Sense stack, camera facing out, USB-C up ---
XIAO_W, XIAO_L, XIAO_D = 17.8, 21.0, 15.0    # Seeed spec; D = camera front to shield back (MEASURE)
XIAO_X0 = IW - 1.0 - XIAO_W                  # 32.2
XIAO_Y1 = IH - 1.0                           # top edge; the USB-C stub runs into the top wall notch
XIAO_C = (XIAO_X0 + XIAO_W / 2, XIAO_Y1 - XIAO_L / 2)
LENS = (XIAO_C[0], XIAO_C[1] - 1.0)          # camera lens centre (MEASURE on the replacement Sense board)
MIC = (XIAO_C[0] + 5.5, XIAO_C[1] - 7.5)     # PDM mic port (MEASURE)
USB_Z = Z_PLATE1 + 1.8                       # USB-C centre height; the plug body is 10 x 6 (the deck cable)

# --- BME680 pocket (front, under the XIAO) and the IMU (behind it) ---
POCKET = (XIAO_X0, IW, P_Y0, 17.0)           # x0, x1, y0, y1
POCKET_Z0 = Z_FACE_IN - 7.4                  # pocket depth from the face (board + header)
IMU = (XIAO_X0 + 0.2, XIAO_X0 + 17.6, P_Y0, P_Y0 + 25.6)   # 17.4 x 25.6 incl. connector (photo; MEASURE)
LIPO = (P_X0, P_X0 + 30.6, P_Y0, P_Y0 + 30.6)              # 503030 cell, 5 mm
BOSSES = [(8.0, 2.4), (43.0, 2.4)]           # back plate screws, M2 x 6 self-tap, in the bottom strip
SWITCH = (25.0, 4.2)                         # slide switch: slot in the bottom wall at this x, z (switch body in the
                                             # bottom strip between the bosses; MEASURE the switch)


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl(x, y, z0, z1, d):
    return Pos(x, y, (z0 + z1) / 2) * Cylinder(d / 2, z1 - z0)


def outline(inset=0.0, r=None):
    r = R_CORNER - inset if r is None else r
    return Pos(IW / 2, IH / 2) * RectangleRounded(IW + 2 * WALL - 2 * inset, IH + 2 * WALL - 2 * inset, r)


def cavity(inset=0.0):
    return Pos(IW / 2, IH / 2) * RectangleRounded(IW - 2 * inset, IH - 2 * inset, R_IN - inset)


def check_layout():
    """Every rectangle inside the cavity, corners included."""
    rects = {"P": (P_X0, P_X0 + P_W, P_Y0, P_Y0 + P_H),
             "OLED": (OLED_PINS_X - OLED_W / 2, OLED_PINS_X + OLED_W / 2, OLED_TOP - OLED_H, OLED_TOP),
             "XIAO": (XIAO_X0, XIAO_X0 + XIAO_W, XIAO_Y1 - XIAO_L, XIAO_Y1), "IMU": IMU, "LIPO": LIPO}
    cav = sbox(0, 0, IW, IH).buffer(-R_IN).buffer(R_IN, quad_segs=32)
    for name, (x0, x1, y0, y1) in rects.items():
        r = sbox(x0, y0, x1, y1)
        assert cav.contains(r), f"{name} leaves the cavity"
    for x, y in BOSSES:
        for name in ("P", "LIPO", "IMU"):
            x0, x1, y0, y1 = rects[name]
            assert not Point(x, y).buffer(2.2).intersects(sbox(x0, y0, x1, y1)), f"boss hits {name}"
    assert rects["LIPO"][1] < rects["IMU"][0], "LiPo and IMU overlap"
    assert rects["IMU"][3] <= rects["XIAO"][2] + 1e-6, "IMU runs into the XIAO"


def faces_of(geom):
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    for p in polys:
        if p.area < 0.05:
            continue
        p = orient(p, sign=1.0)
        ring = lambda r: Wire.make_polygon([Vector(x, y, 0) for x, y in list(r.coords)[:-1]], close=True)
        yield Face(ring(p.exterior), [ring(r) for r in p.interiors])


def network_2d():
    """The face's wing network: Voronoi veins with nodes, seeded round the camera eye, framed by a hull line, the
    OLED visor, the eye ring, rings round every key, LED and the mic, and a frame round the vent cluster."""
    r = LINE / 2
    border = sbox(-WALL + 1.6, -WALL + 1.6, IW + WALL - 1.6, IH + WALL - 1.6).buffer(-(R_CORNER - 1.6)) \
        .buffer(R_CORNER - 1.6, quad_segs=16)
    w, h = OLED_WIN
    visor = sbox(OLED_WIN_C[0] - w / 2 - 1.8, OLED_WIN_C[1] - h / 2 - 1.8,
                 OLED_WIN_C[0] + w / 2 + 1.8, OLED_WIN_C[1] + h / 2 + 1.8).buffer(-2).buffer(2, quad_segs=8)
    eye = Point(LENS).buffer(7.6, quad_segs=32)
    rings = [Point(p).buffer(5.2, quad_segs=16) for p in KEYS.values()] + \
            [Point(p).buffer(2.9, quad_segs=16) for p in LEDS.values()] + [Point(MIC).buffer(1.9, quad_segs=12)]
    vents = sbox(POCKET[0] + 1.2, POCKET[2] + 0.8, POCKET[1] - 1.2, POCKET[3] - 1.4).buffer(-1.5).buffer(1.5)
    frames = [border.exterior, visor.exterior, eye.exterior, vents.exterior] + [g.exterior for g in rings]
    field = border.difference(unary_union([visor, eye, vents] + rings))

    rng = np.random.default_rng(1015)
    seeds = [(LENS[0] + 10.0 * math.cos(a), LENS[1] + 10.0 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 10)[:-1]]
    for _ in range(4000):
        x, y = rng.uniform(-WALL, IW + WALL), rng.uniform(-WALL, IH + WALL)
        gap = 4.2 + 0.08 * max(math.hypot(x - LENS[0], y - LENS[1]) - 8, 0)
        if all(math.hypot(x - u, y - v) >= gap for u, v in seeds):
            seeds.append((x, y))
    edges = unary_union(voronoi_diagram(MultiPoint(seeds), envelope=sbox(-30, -30, IW + 30, IH + 30), edges=True))
    veins = edges.intersection(field)
    count = {}
    for e in getattr(edges, "geoms", []):
        for c in (e.coords[0], e.coords[-1]):
            k = (round(c[0], 3), round(c[1], 3))
            count[k] = count.get(k, 0) + 1
    inner = field.buffer(-1.4)
    nodes = [Point(k).buffer(0.75, quad_segs=8) for k, n in count.items() if n >= 3 and inner.contains(Point(k))]
    net = unary_union([veins.buffer(r, quad_segs=4)] + [f.buffer(r, quad_segs=8) for f in frames] + nodes)
    return net.buffer(0.2, quad_segs=4).buffer(-0.2, quad_segs=4).simplify(0.03)


def spiracles():
    """Point-up hexagon vents for the BME680 pocket: a cluster in the face, a row in the bottom and right walls."""
    cut = None
    x0, x1, y0, y1 = POCKET
    pts = [(x, y) for j, y in enumerate(np.arange(y0 + 3.0, y1 - 2.0, 3.2))
           for x in np.arange(x0 + 3.0 + (1.6 if j % 2 else 0), x1 - 2.4, 3.4)]
    for x, y in pts:
        v = extrude(Pos(x, y, Z_FACE_IN - 1) * RegularPolygon(1.15, 6, rotation=30), amount=FACE + 2)
        cut = v if cut is None else cut + v
    zc = (POCKET_Z0 + Z_FACE_IN) / 2
    for x in np.arange(x0 + 3.0, x1 - 2.0, 3.6):                      # bottom wall
        cut += extrude(Plane.XZ.offset(1.0) * Pos(x, zc) * RegularPolygon(1.3, 6, rotation=30), amount=WALL + 2)
    for y in np.arange(y0 + 2.5, y1 - 2.5, 3.6):                      # right wall
        cut += extrude(Plane.YZ.offset(IW - 1.0) * Pos(y, zc) * RegularPolygon(1.3, 6, rotation=30), amount=WALL + 2)
    return cut


def shell():
    """Face + walls in one print, face down on the bed: no supports (the hook lip is a 1 mm bridge)."""
    s = extrude(outline(), amount=T) - extrude(Pos(0, 0, -1) * cavity(), amount=Z_FACE_IN + 1)
    for z in (7.0, 12.0):                                               # exoskeleton bands round the walls
        s -= box(-WALL - 5, IW + WALL + 5, -WALL - 5, IH + WALL + 5, z - 0.5, z + 0.5) - extrude(
            Pos(0, 0, z - 1) * outline(0.5), amount=2)

    # the eye: lens window, 45-degree iris, compound-eye ring of small hexes round it
    lx, ly = LENS
    s -= cyl(lx, ly, Z_FACE_IN - 1, T + 1, 7.0)
    s -= Pos(lx, ly, T - 1.3) * Cone(3.3, 4.6, 1.31)
    for k in range(12):
        a = math.radians(k * 30 + 15)
        s -= extrude(Pos(lx + 5.6 * math.cos(a), ly + 5.6 * math.sin(a), T - 0.5) * RegularPolygon(0.75, 6),
                     amount=1)
    s -= cyl(*MIC, Z_FACE_IN - 1, T + 1, 1.2)
    # the OLED visor: window with a 45-degree bevel outward
    w, h = OLED_WIN
    s -= box(OLED_WIN_C[0] - w / 2, OLED_WIN_C[0] + w / 2, OLED_WIN_C[1] - h / 2, OLED_WIN_C[1] + h / 2,
             Z_FACE_IN - 1, T + 1)
    s -= extrude(Pos(*OLED_WIN_C, T - 0.8) * RectangleRounded(w, h, 0.5), amount=0.81, taper=-45)
    # keys and LEDs
    for x, y in KEYS.values():
        s -= cyl(x, y, Z_FACE_IN - 1, T + 1, 7.4)
    for x, y in LEDS.values():
        s -= cyl(x, y, Z_FACE_IN - 1, T + 1, 3.2)
    s -= spiracles()
    s -= extrude([Pos(0, 0, T - ETCH) * f for f in faces_of(network_2d())], amount=ETCH + 1)

    # inside: OLED pads, posts that hold the perfboard, the BME pocket walls, the XIAO stop, the back plate bosses
    for dx in (-1, 1):
        for dy in (-1, 1):
            px, py = OLED_WIN_C[0] + dx * (w / 2 + 1.6), OLED_WIN_C[1] + dy * (h / 2 + 1.6)
            s += box(px - 1, px + 1, py - 1, py + 1, Z_FACE_IN - 0.6, Z_FACE_IN + 0.01)
    for x in ((KEYS["MARK"][0] + KEYS["TALK"][0]) / 2, LEDS["REC"][0]):
        s += cyl(x, (KEYS["APPROVE"][1] + KEYS["MARK"][1]) / 2, P_FRONT, Z_FACE_IN + 0.01, 3.0)
    x0, x1, y0, y1 = POCKET
    s += box(x0 - 1.0, x1 + 0.01, y1, y1 + 1.2, POCKET_Z0, Z_FACE_IN + 0.01)       # roof between pocket and XIAO
    s += box(x0 - 1.0, x0, y0, y1 + 1.2, POCKET_Z0, Z_FACE_IN + 0.01)              # wall toward the LEDs
    xb = XIAO_Y1 - XIAO_L                                                       # a rib the XIAO's bottom edge sits on
    s += box(XIAO_X0 + 2, XIAO_X0 + XIAO_W - 2, xb - 1.2, xb, Z_FACE_IN - 6, Z_FACE_IN + 0.01)
    for x, y in BOSSES:
        s += cyl(x, y, Z_PLATE1, Z_FACE_IN + 0.01, 4.4) - cyl(x, y, Z_PLATE1 - 1, Z_FACE_IN - 1.5, 1.8)
    # back: the hook lip on the top wall (left of the USB notch), the USB-C notch, the switch slot
    s += box(3.5, XIAO_X0 - 1.0, IH - 1.0, IH + 0.01, 0, LIP)
    s -= box(XIAO_C[0] - 6.0, XIAO_C[0] + 6.0, IH - 1, IH + WALL + 1, -1, USB_Z + 3.5)
    sx, sz = SWITCH
    s -= box(sx - 3.5, sx + 3.5, -WALL - 1, 0.5, sz - 1.3, sz + 1.3)
    return s


def back_part():
    """Back plate: hooks under the top lip, two M2 screws at the bottom, bar-pin recess, the Argo marks."""
    p = extrude(Pos(0, 0, Z_PLATE0) * cavity(FIT), amount=PLATE)
    p -= box(XIAO_C[0] - 6.0, XIAO_C[0] + 6.0, IH - 3, IH + 1, Z_PLATE0 - 1, Z_PLATE1 + 1)   # USB-C notch
    for x, y in BOSSES:
        p -= cyl(x, y, Z_PLATE0 - 1, Z_PLATE1 + 1, 2.4)
        p -= cyl(x, y, Z_PLATE0 - 1, Z_PLATE0 + 1.2, 4.2)                   # head counterbore
    p -= box(IW / 2 - 18, IW / 2 + 18, 33.0, 39.0, Z_PLATE0 - 1, Z_PLATE0 + 0.8)   # bar pin recess, above centre
    p -= back_marks()
    return p


def back_marks():
    """Emblem, EXO ARGO, V0.1 and the URL on the outer side of the back plate, mirrored so they read from behind."""
    facets = json.loads(EMBLEM.read_text())
    cut = None
    cx, cy, size = IW / 2, 24.0, 9.0
    for shade, depth in (("light", 0.5), ("dark", 0.9)):
        for poly in facets[shade]:
            pts = [(cx + u * size, cy + v * size) for u, v in poly]
            f = extrude(Pos(0, 0, Z_PLATE0 - 1) * Polygon(*pts, align=None), amount=1 + depth)
            cut = f if cut is None else cut + f
    for txt, y, size in (("EXO ARGO", 15.8, 3.6), ("V0.1", 12.0, 2.8), ("exo.myargoquest.com", 7.4, 2.9)):
        t = Text(txt, font_size=size, font_path=FONT, align=(Align.CENTER, Align.CENTER))
        cut += extrude(Pos(cx, y, Z_PLATE0 - 1) * t, amount=1.5)
    return mirror(cut, about=Plane.YZ.offset(cx))        # read correctly from behind


def keys():
    """Three key caps, printed flange down: 7 mm stem, 9 mm flange; TALK gets a raised dot to find by feel."""
    stem = T + 1.0 - (P_FRONT + H_BTN) - 0.8          # from the flange top to 1 mm proud of the face
    out = None
    for i, name in enumerate(KEYS):
        k = Pos(i * 12, 0, 0) * (Cylinder(4.5, 0.8, align=(Align.CENTER, Align.CENTER, Align.MIN)) +
                                 Pos(0, 0, 0.8) * Cylinder(3.5, stem, align=(Align.CENTER, Align.CENTER, Align.MIN)))
        if name == "TALK":
            k += Pos(i * 12, 0, 0.8 + stem) * Cylinder(0.9, 0.6, align=(Align.CENTER, Align.CENTER, Align.MIN))
        out = k if out is None else out + k
    return out


def main():
    check_layout()
    OUT.mkdir(exist_ok=True)
    sh, bk, ks = shell(), back_part(), keys()
    prints = {
        "brooch-shell": Pos(0, 0, T) * (Rot(180, 0, 0) * sh),        # face on the bed
        "brooch-back": Pos(0, 0, -Z_PLATE0) * bk,                     # outer side on the bed
        "brooch-keys": ks,
    }
    for name, part in prints.items():
        assert part.is_valid, name
        bb = part.bounding_box()
        print(f"{name:13s} {bb.size.X:5.1f} x {bb.size.Y:5.1f} x {bb.size.Z:5.1f} mm  volume {part.volume / 1000:5.1f} cm3")
        if not DRAFT:
            export_stl(part, str(OUT / f"{name}.stl"))
            m = Mesher()
            m.add_shape(part)
            m.write(str(OUT / f"{name}.3mf"))
    export_stl(sh, str(OUT / "brooch-shell-assembled.stl"))          # previews only (views.py)
    export_stl(bk, str(OUT / "brooch-back-assembled.stl"))
    if DRAFT:
        print("DRAFT: no 3MF or STEP written; set DRAFT = False once the measurements are in")
    else:
        export_step(Compound([sh, bk]), str(OUT / "brooch.step"))
    print(f"outer {IW + 2 * WALL:.1f} x {IH + 2 * WALL:.1f} x {T:.1f} mm; perfboard {P_COLS} x {P_ROWS} holes "
          f"({P_W:.1f} x {P_H:.1f} mm); key stem {T + 1.0 - (P_FRONT + H_BTN) - 0.8:.1f} mm")


if __name__ == "__main__":
    main()
