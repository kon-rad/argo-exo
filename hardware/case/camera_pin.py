"""Exo camera pin: a hexagonal, carapace-style case that turns the Pi Camera Rev 1.3 into a pin worn on the strap or
a shirt, joined to the deck by its ribbon.

Run:  uv run --no-project --with build123d python camera_pin.py
Out:  out/camera-pin-face.3mf (print face down), out/camera-pin-back.3mf (print outer side down), STLs, a STEP.

Frame: origin at the camera board's centre, x along the ribbon edge, y away from it (the ribbon edge is at
y = -12), z = 0 on the outer face. Board numbers come from the photo IMG_7471 (2026-10-15) scaled to the 25 mm
edge and the official camera drawing; the ones marked MEASURE need calipers before printing.
"""
import math
from pathlib import Path

from build123d import (Axis, Box, Compound, Cone, Cylinder, Face, Kind, Mesher, Pos, RegularPolygon, Rot, Side, Spline, Wire,
                       chamfer, export_step, export_stl, extrude, fillet)

OUT = Path(__file__).parent / "out"

# --- Pi Camera Rev 1.3 (25 x 24 mm; the ribbon connector is on the back at one 25 mm edge) ---
BOARD_W, BOARD_H, BOARD_T = 25.0, 24.0, 1.0
HOLES = [(-10.5, -2.5), (10.5, -2.5), (-10.5, 10.0), (10.5, 10.0)]  # M2, 21 x 12.5 (MEASURE)
LENS = (0.0, -2.0)            # lens centre (photo: 12.5 along the edge, 10 from the ribbon edge)
LED = (-9.2, 6.0)             # red LED D1 (photo estimate, MEASURE); model x is mirrored vs the view from outside
LENS_H = 5.5                  # lens module height above the board front (MEASURE)

# --- pin ---
WALL = 2.0
FACE = 1.6
BACK_GAP = 3.4                # board back -> back plate: the ribbon connector and the ribbon bend
PLATE = 2.0
TAPER = 6.0                   # walls lean out from the face, so the face is the smallest outline
R_IN = 18.3                   # hexagon inradius of the cavity: clears every board corner by about 1.5 mm
CORNER = 3.0
RIBBON_GAP = 0.4              # clamp gap for the ribbon + one layer of Kapton (tune after a test fit)

Z_FACE_IN = FACE
Z_BOARD_FRONT = Z_FACE_IN + LENS_H
Z_BOARD_BACK = Z_BOARD_FRONT + BOARD_T
Z_PLATE = Z_BOARD_BACK + BACK_GAP     # top of the walls = underside of the back plate
Z_TOP = Z_PLATE + PLATE
Z_RIBBON = Z_BOARD_BACK + 1.2         # where the ribbon leaves the connector (MEASURE)
INSET = (Z_PLATE) * math.tan(math.radians(TAPER))


def box(x0, x1, y0, y1, z0, z1):
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def cyl(x, y, z0, z1, d):
    return Pos(x, y, (z0 + z1) / 2) * Cylinder(d / 2, z1 - z0)


def hexagon(inradius, corner):
    return fillet(RegularPolygon(inradius * 2 / math.sqrt(3), 6).vertices(), radius=corner)


def vein(points, width=0.8, depth=0.45):
    w = Wire([Spline(*points)]).offset_2d(width / 2, kind=Kind.ARC, side=Side.BOTH)
    return extrude(Face(w), amount=depth)                       # from z = 0 up into the face


def face_part():
    """The face, walls, board bosses and the ribbon shelf. Print face down; the walls lean outwards 6 degrees."""
    outer = extrude(hexagon(R_IN + WALL, CORNER + WALL), amount=Z_PLATE, taper=-TAPER)
    outer = chamfer(outer.edges().group_by(Axis.Z)[0], length=1.0)    # the bed-side edge round the face
    p = outer - extrude(Pos(0, 0, Z_FACE_IN) * hexagon(R_IN, CORNER), amount=Z_PLATE)

    # the eye: lens window with a 45-degree iris, an engraved ring, six swirling veins (the emblem's pinwheel)
    lx, ly = LENS
    p -= cyl(lx, ly, -1, Z_FACE_IN + 1, 7.0)
    p -= Pos(lx, ly, 0.6) * Cone(4.85, 3.3, 1.3)                         # 45-degree iris round the window
    ring = cyl(lx, ly, -1, 0.5, 13.2) - cyl(lx, ly, -2, 1, 12.0)
    p -= ring
    for k in range(6):
        a0 = math.radians(k * 60)
        pts = [(lx + r * math.cos(a0 + math.radians(s)), ly + r * math.sin(a0 + math.radians(s)))
               for r, s in ((6.3, 0), (10.0, 9), (13.0, 16), (15.5, 20))]   # starts inside the ring: no tangency
        p -= vein(pts)
    p -= cyl(*LED, -1, Z_FACE_IN + 1, 1.8)                             # the camera's red LED shows through

    # bosses the board screws into (M2 self-tap), from the face to the board's front
    for x, y in HOLES:
        p += cyl(x, y, Z_FACE_IN - 0.01, Z_BOARD_FRONT, 4.2) - cyl(x, y, Z_FACE_IN + 0.8, Z_BOARD_FRONT + 1, 1.8)

    # ribbon: a shelf it rests on, then out through a slot in the flat wall on the ribbon side
    p += box(-10, 10, -R_IN - 0.1, -12.6, Z_FACE_IN - 0.01, Z_RIBBON)
    p -= box(-9.25, 9.25, -R_IN - WALL - INSET - 2, -R_IN + 0.5, Z_RIBBON - 0.3, Z_RIBBON + 1.3)
    return p


def back_part():
    """Back plate with board spacers, the ribbon clamp bar and a recess for a brooch bar pin. Built in place,
    exported flipped (outer side down) for printing."""
    top = hexagon(R_IN + WALL + INSET, CORNER + WALL + INSET)
    p = extrude(Pos(0, 0, Z_PLATE) * top, amount=PLATE)
    for x, y in HOLES:
        p += cyl(x, y, Z_BOARD_BACK, Z_PLATE + 0.01, 4.2)
        p -= cyl(x, y, Z_BOARD_BACK - 1, Z_TOP + 1, 2.4)
        p -= cyl(x, y, Z_TOP - 1.2, Z_TOP + 1, 4.2)                    # M2 head counterbore
    p += box(-10, 10, -R_IN + 0.3, -13.0, Z_RIBBON + RIBBON_GAP, Z_PLATE + 0.01)   # clamp bar
    p -= box(-18, 18, 1.0, 7.0, Z_TOP - 0.8, Z_TOP + 1)                 # bar pin recess, above the centre
    return p


def main():
    OUT.mkdir(exist_ok=True)
    face, back = face_part(), back_part()
    back_print = Pos(0, 0, Z_TOP) * (Rot(180, 0, 0) * back)
    for name, part in (("camera-pin-face", face), ("camera-pin-back", back_print)):
        assert part.is_valid, name
        export_stl(part, str(OUT / f"{name}.stl"))
        m = Mesher()
        m.add_shape(part)
        m.write(str(OUT / f"{name}.3mf"))
        bb = part.bounding_box()
        print(f"{name:16s} {bb.size.X:5.1f} x {bb.size.Y:5.1f} x {bb.size.Z:5.1f} mm  volume {part.volume / 1000:5.1f} cm3")
    export_step(Compound([face, back]), str(OUT / "camera-pin.step"))
    print(f"pin thickness {Z_TOP:.1f} mm; screws M2 x 10 from the back")


if __name__ == "__main__":
    main()
