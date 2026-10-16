"""Exo camera pod: the Pi Camera Rev 1.3 in a hand-held, pinnable pod in the deck case's language. DRAFT: renders only
until the camera board is measured (vault note current/exo-pi-case-design.md §2.5).

A head that holds the camera and a grip below it that you hold and point; the ribbon runs down the grip and leaves
through the tail. Square walls, rounded corners, a waist between head and grip; the face carries the compound eye,
the wing network and a panel with the Argo emblem, EXO ARGO and V0.1; the grip's sides are segmented like an
abdomen; the back has a bar-pin recess on the head and a thumb rest on the grip. The back plate hooks under a lip at
the top and is held at the tail by one M2 screw that also clamps the ribbon. (Replaces the hexagonal pin of
2026-10-15, commit 884fc39.)

Run:  uv run --no-project --with build123d --with shapely --with numpy python camera_pin.py
      uv run --no-project --with trimesh --with rtree --with numpy --with pillow python camera_pin_views.py
Frame: x right and y up as seen from the front (the side the lens faces); z from the back (0) to the face (T).
The camera board's centre is (0, 0); its ribbon edge is at y = -12.
"""
import json
import math
from pathlib import Path

import numpy as np
from build123d import (Align, Box, Compound, Cone, Cylinder, Face, Mesher, Plane, Polygon, Pos, RegularPolygon,
                       RectangleRounded, Rot, Text, Vector, Wire, export_step, export_stl, extrude, mirror)
from shapely.geometry import MultiPoint, Point, box as sbox
from shapely.geometry.polygon import orient
from shapely.ops import unary_union, voronoi_diagram

HERE = Path(__file__).parent
OUT = HERE / "out"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
EMBLEM = HERE / "argo_emblem.json"
DRAFT = True          # no 3MF/STEP until the camera board is measured

# --- Pi Camera Rev 1.3 (25 x 24 mm; MEASURE every value below on the real board) ---
BOARD_W, BOARD_H, BOARD_T = 25.0, 24.0, 1.0
HOLES = [(-10.5, -2.5), (10.5, -2.5), (-10.5, 10.0), (10.5, 10.0)]   # M2, 21 x 12.5
LENS = (0.0, -2.0)
LED = (9.2, 6.0)                   # red LED, as seen from the front
LENS_H = 5.5                       # lens module height above the board's front
RIBBON_W = 16.0

# --- pod ---
WALL, FACE, PLATE, LIP, FIT = 2.0, 1.6, 2.0, 0.8, 0.2
BACK_GAP = 3.4                     # board back -> back plate: the connector and the ribbon's turn
Z_PLATE0, Z_PLATE1 = LIP, LIP + PLATE                 # 0.8 .. 2.8
Z_BOARD_BACK = Z_PLATE1 + BACK_GAP                    # 6.2
Z_BOARD_FRONT = Z_BOARD_BACK + BOARD_T                # 7.2
Z_FACE_IN = Z_BOARD_FRONT + LENS_H                    # 12.7
T = Z_FACE_IN + FACE                                  # 14.3
Z_RIBBON = Z_BOARD_BACK - 1.2                         # where the ribbon leaves the connector (MEASURE)
HEAD = (34.0, 31.0, 0.0)           # outer width, height, centre y
GRIP = (30.0, 34.0, -28.0)         # outer width, height, centre y: the part you hold (30: room for the tail screw)
WAIST_R = 6.0                      # the concave fillet between head and grip
R_CORNER = 7.0
LINE, ETCH = 0.7, 0.5
SEGMENTS = [-21.0, -26.0, -31.0, -36.0, -41.0]        # grip side grooves (y)


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl(x, y, z0, z1, d):
    return Pos(x, y, (z0 + z1) / 2) * Cylinder(d / 2, z1 - z0)


def rrect(w, h, cy, r):
    return sbox(-w / 2, cy - h / 2, w / 2, cy + h / 2).buffer(-r).buffer(r, quad_segs=16)


def outline_2d(inset=0.0):
    """Head + grip with a smooth concave waist (closing by WAIST_R), shrunk by inset."""
    shape = unary_union([rrect(HEAD[0], HEAD[1], HEAD[2], R_CORNER), rrect(GRIP[0], GRIP[1], GRIP[2], R_CORNER)])
    shape = shape.buffer(WAIST_R, quad_segs=16).buffer(-WAIST_R, quad_segs=16)
    return shape.buffer(-inset, quad_segs=16) if inset else shape


def faces_of(geom):
    polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    for p in polys:
        if p.area < 0.05:
            continue
        p = orient(p.simplify(0.02), sign=1.0)
        ring = lambda r: Wire.make_polygon([Vector(x, y, 0) for x, y in list(r.coords)[:-1]], close=True)
        yield Face(ring(p.exterior), [ring(r) for r in p.interiors])


def slab(geom, z0, z1):
    return extrude([Pos(0, 0, z0) * f for f in faces_of(geom)], amount=z1 - z0)


Y_BOTTOM = GRIP[2] - GRIP[1] / 2                      # outer bottom of the tail, -45
Y_TOP = HEAD[2] + HEAD[1] / 2                         # outer top, 15.5
PANEL = (-9.0, 9.0, -40.5, -19.5)                     # branding panel on the grip's face
CLAMP_Y = -31.0                                       # ribbon clamp (shelf on the face, bar on the back plate)
TAIL_SCREW = (10.8, CLAMP_Y)                          # the one screw: beside the ribbon, through the clamp


def network_2d():
    r = LINE / 2
    border = outline_2d(1.6)
    eye = Point(LENS).buffer(7.6, quad_segs=32)
    rings = [Point(LED).buffer(2.2, quad_segs=12)]
    panel = sbox(PANEL[0], PANEL[2], PANEL[1], PANEL[3]).buffer(-2.5).buffer(2.5, quad_segs=8)
    frames = [border.exterior, eye.exterior, panel.exterior] + [g.exterior for g in rings]
    field = border.difference(unary_union([eye, panel] + rings))
    rng = np.random.default_rng(1235)
    seeds = [(LENS[0] + 10.0 * math.cos(a), LENS[1] + 10.0 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 9)[:-1]]
    for _ in range(4000):
        x, y = rng.uniform(-HEAD[0] / 2, HEAD[0] / 2), rng.uniform(Y_BOTTOM, Y_TOP)
        gap = 4.0 + 0.07 * max(math.hypot(x - LENS[0], y - LENS[1]) - 8, 0)
        if all(math.hypot(x - u, y - v) >= gap for u, v in seeds):
            seeds.append((x, y))
    edges = unary_union(voronoi_diagram(MultiPoint(seeds), envelope=sbox(-40, -70, 40, 40), edges=True))
    veins = edges.intersection(field)
    count = {}
    for e in getattr(edges, "geoms", []):
        for c in (e.coords[0], e.coords[-1]):
            k = (round(c[0], 3), round(c[1], 3))
            count[k] = count.get(k, 0) + 1
    inner = field.buffer(-1.3)
    nodes = [Point(k).buffer(0.7, quad_segs=8) for k, n in count.items() if n >= 3 and inner.contains(Point(k))]
    net = unary_union([veins.buffer(r, quad_segs=4)] + [f.buffer(r, quad_segs=8) for f in frames] + nodes)
    return net.buffer(0.2, quad_segs=4).buffer(-0.2, quad_segs=4)


def branding():
    """Emblem, EXO ARGO and V0.1 in the grip's face panel."""
    facets = json.loads(EMBLEM.read_text())
    cut = None
    for shade, depth in (("light", 0.5), ("dark", 1.0)):
        for poly in facets[shade]:
            pts = [(u * 9.0, -24.6 + v * 9.0) for u, v in poly]
            f = extrude(Pos(0, 0, T - depth) * Polygon(*pts, align=None), amount=depth + 1)
            cut = f if cut is None else cut + f
    for txt, y, size in (("EXO ARGO", -32.6, 3.3), ("V0.1", -37.0, 2.7)):
        while True:                                                 # shrink until it clears the panel frame
            t = Text(txt, font_size=size, font_path=FONT, align=(Align.CENTER, Align.CENTER))
            if t.bounding_box().size.X <= PANEL[1] - PANEL[0] - 4.0:
                break
            size *= 0.95
        cut += extrude(Pos(0, y, T - ETCH) * t, amount=ETCH + 1)
    return cut


def shell():
    """Face + walls, printed face down."""
    s = slab(outline_2d(), 0, T) - slab(outline_2d(WALL), -1, Z_FACE_IN)
    for z in (4.5, 9.5):                                           # exoskeleton bands round the walls
        s -= slab(outline_2d(), z - 0.5, z + 0.5) - slab(outline_2d(0.5), z - 1, z + 1)
    for y in SEGMENTS:                                             # abdomen segments down the grip's sides
        for sx in (-1, 1):
            xo = sx * GRIP[0] / 2                                  # the grip's outer side face
            s -= box(min(xo - sx * 0.6, xo + sx * 1.0), max(xo - sx * 0.6, xo + sx * 1.0), y - 0.5, y + 0.5, 1.5, T - 1.5)
    # the eye: window, 45-degree iris, 12-hex compound ring
    lx, ly = LENS
    s -= cyl(lx, ly, Z_FACE_IN - 1, T + 1, 7.0)
    s -= Pos(lx, ly, T - 1.3) * Cone(3.3, 4.6, 1.31)
    for k in range(12):
        a = math.radians(k * 30 + 15)
        s -= extrude(Pos(lx + 5.6 * math.cos(a), ly + 5.6 * math.sin(a), T - 0.5) * RegularPolygon(0.75, 6), amount=1)
    s -= cyl(*LED, Z_FACE_IN - 1, T + 1, 1.8)
    s -= branding()
    s -= extrude([Pos(0, 0, T - ETCH) * f for f in faces_of(network_2d())], amount=ETCH + 1)
    # inside: board bosses, each with a locating peg through the camera's 2.2 mm hole (no screws in the head)
    for x, y in HOLES:
        s += cyl(x, y, Z_BOARD_FRONT, Z_FACE_IN + 0.01, 4.2)
        s += cyl(x, y, Z_BOARD_BACK + 0.1, Z_BOARD_FRONT + 0.01, 1.7)
    # ribbon: a shelf it rests on, and the exit slot in the tail with a rounded lead-in
    s += box(-RIBBON_W / 2 - 1.5, RIBBON_W / 2 + 1.5, CLAMP_Y - 2.0, CLAMP_Y + 2.0, Z_RIBBON, Z_FACE_IN + 0.01)
    s -= box(-RIBBON_W / 2 - 1.0, RIBBON_W / 2 + 1.0, Y_BOTTOM - 1, Y_BOTTOM + WALL + 0.5, Z_RIBBON - 1.2, Z_RIBBON + 0.6)
    # back: the top lip the plate hooks under; the tail screw's boss, fused to the wall beside the ribbon
    lip = outline_2d(WALL - 0.01).intersection(sbox(-8, Y_TOP - WALL - 1.0, 8, Y_TOP))
    s += slab(lip, 0, LIP)
    sx, sy = TAIL_SCREW
    s += cyl(sx, sy, Z_PLATE1, Z_FACE_IN + 0.01, 3.8) - cyl(sx, sy, Z_PLATE1 - 1, Z_PLATE1 + 6, 1.6)
    return s


def back_part():
    """Back plate: spacers that press the board, the ribbon clamp bar, the spring tongue with its barb, the bar-pin
    recess on the head and a thumb rest on the grip."""
    plate_2d = outline_2d(WALL + FIT)
    p = slab(plate_2d, Z_PLATE0, Z_PLATE1)
    for x, y in HOLES:                                              # spacers press the board onto its pegs
        p += cyl(x, y, Z_PLATE1 - 0.01, Z_BOARD_BACK, 4.2)
        p -= cyl(x, y, Z_BOARD_BACK - 1.2, Z_BOARD_BACK + 1, 2.3)   # takes the face's peg
    p += box(-RIBBON_W / 2 - 1.5, RIBBON_W / 2 + 1.5, CLAMP_Y - 2.0, CLAMP_Y + 2.0, Z_PLATE1 - 0.01, Z_RIBBON - 0.4)
    sx, sy = TAIL_SCREW                                             # the one screw, M2 x 6
    p -= cyl(sx, sy, Z_PLATE0 - 1, Z_PLATE1 + 1, 2.4)
    p -= cyl(sx, sy, Z_PLATE0 - 1, Z_PLATE0 + 1.0, 3.6)              # head counterbore (M2 pan head)
    p -= box(-14.0, 14.0, 3.0, 9.0, Z_PLATE0 - 1, Z_PLATE0 + 0.8)   # bar pin recess, 28 x 6
    p -= Pos(0, -21.0, Z_PLATE0 - 1) * Cylinder(5.5, 1.8, align=(Align.CENTER, Align.CENTER, Align.MIN))  # thumb rest, 0.8 deep
    return p


def main():
    OUT.mkdir(exist_ok=True)
    sh, bk = shell(), back_part()
    for name, part in (("camera-pod-shell", sh), ("camera-pod-back", bk)):
        assert part.is_valid, name
        bb = part.bounding_box()
        print(f"{name:17s} {bb.size.X:5.1f} x {bb.size.Y:5.1f} x {bb.size.Z:5.1f} mm  volume {part.volume / 1000:5.1f} cm3")
    export_stl(sh, str(OUT / "camera-pod-shell-assembled.stl"))     # previews
    export_stl(bk, str(OUT / "camera-pod-back-assembled.stl"))
    if DRAFT:
        print("DRAFT: no 3MF or STEP written; set DRAFT = False once the camera board is measured")
    else:
        for name, part in (("camera-pod-shell", Pos(0, 0, T) * (Rot(180, 0, 0) * sh)),
                           ("camera-pod-back", Pos(0, 0, -Z_PLATE0) * bk)):
            m = Mesher()
            m.add_shape(part)
            m.write(str(OUT / f"{name}.3mf"))
        export_step(Compound([sh, bk]), str(OUT / "camera-pod.step"))
    print(f"pod {HEAD[0]:.0f} x {Y_TOP - Y_BOTTOM:.1f} x {T:.1f} mm (grip {GRIP[0]:.0f} wide); one M2 x 6 screw at the tail")


if __name__ == "__main__":
    main()
