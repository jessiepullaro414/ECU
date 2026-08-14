#!/usr/bin/env python3
"""
build_k1_footprint.py - generates a real KiCad footprint for K1, the
main power relay: Panasonic CB1a-T-P-12V.

WHY THIS EXISTS. The board's original relay (Schrack RT1-16A-FormC) was
a real, PCB-mountable part with a real bundled KiCad footprint, but only
16A-rated, while this board's own main fuse (F1) is 30A and its real
combined switched load (injectors + ignition + O2 heater + ETC H-bridge
+ VVT/idle/fuel-pump/boost drivers, all downstream of K1 on VBATT_SW)
can reach ~22-28A. Genuinely undersized, and flagged as fab-blocking in
ecu-pcb/README.md's own "Known open items" rather than hidden.

WHY A HAND-BUILT FOOTPRINT. Every bundled KiCad Relay_THT footprint was
checked programmatically for (a) real electrical pads and (b) a >=30A
rating in its own descr field. Only ONE cleared both - Zettler AZSR131,
35A - and it is an EV-charging/solar relay rated only -40 to +85C, which
is not an engine-bay part. (The two Potter&Brumfield T9A "12V30A"
footprints look right by name but both self-describe as "Dummy for Space
NO Pads" - the exact trap that already bit this project once, see
build_schematic.py's own T9AP5D52 bug comment. Confirmed here that the
SPST variant is a dummy too, not just the SPDT one.) So no bundled
footprint fits, and this one is built from the real datasheet drawing.

REAL PART, REAL SOURCE. Panasonic CB series "MINI-ISO AUTOMOTIVE RELAY",
datasheet ds_61202_en_cb (010113J), downloaded from Panasonic's own
mediap.industry.panasonic.eu and cross-checked against a second copy via
Farnell. Part number decoded from the datasheet's own ORDERING
INFORMATION and confirmed against its TYPES table's real orderable list:
    CB      series
      1a    contact arrangement = 1 Form A (SPST-NO)
            (Form A is sufficient - the old SPDT relay's NC contact was
            already wired no_connect, so nothing is lost)
      -T    heat resistant type -> the -40 to +125C rating. THIS SUFFIX
            IS NOT OPTIONAL for this board: the standard (non-T) type is
            only rated -40 to +85C, same disqualifying limit as the
            Zettler part above. An engine-bay ECU needs the T.
      -P    PC board type terminals (vs. plug-in / bracket)
      -12V  12V DC coil
    => CB1a-T-P-12V (sealed type; CB1aF-T-P-12V is the flux-resistant
       variant of the same geometry - sealed chosen for an engine bay)

REAL RATINGS (datasheet Characteristics table, genuinely read):
    Nominal switching capacity   40A @ 14V DC
    Max. carrying current        40A (14V DC, 85C, continuous, N.O.)
    Contact resistance           typ 2 mOhm
    Contact material             Ag alloy (cadmium free)
    Coil (12V)                   117mA, 103 Ohm, 1.4W, usable 10-16V
    Operate / release time       max 15ms each
    Shock                        200 m/s2 functional, 1000 m/s2 destructive
    Vibration                    10-500Hz, min 44.1 m/s2 functional
40A comfortably covers F1's own 30A main fuse, so the relay is no longer
the weakest link in the switched path - the fuse is, which is the
correct way round.

REAL, CHECKED KNOCK-ON EFFECTS on parts this change does NOT replace:
  - Coil current rises from the Schrack's ~40mA class to a real 117mA.
    Q2 (PMV230ENEA) drives the coil low-side and is rated well over 1A,
    so it is genuinely fine - checked, not assumed.
  - D2 (PMEG4010BEA, 40V/1A) freewheels that same 117mA. Also fine.
  - Datasheet states a real MIN. switching capacity of 1A @ 14V DC.
    Relevant because switching far below a relay's minimum lets contact
    films build up; this board's real switched load is many amps, so
    this is satisfied - noted because it is a genuine spec, not because
    it is a problem here.

GEOMETRY DERIVATION (the part that actually needed care). Panasonic
gives "PC board pattern (Bottom view)" with a tolerance of +/-0.1mm.
Its text layer yields the numbers but not their spatial assignment, so
the drawing was rendered as an image and read directly - the same
technique this project already uses for register diagrams - and then
cross-checked against the SEPARATE "External dimensions" bottom view on
the same page, which shows the same five terminals inside the real body
outline with the same dimension chain. Both agree.

Real dimension chain, bottom view, measuring to terminal 87:
    30  ->  87   = 17.9 mm   (full horizontal span)
    85/86 -> 87  =  8.4 mm
    => 30 -> 85/86 = 17.9 - 8.4 = 9.5 mm
    86  ->  85   = 16.8 mm vertical, i.e. +/-8.4 about the 30/87 axis
The third horizontal dim (8.0, from 87a to 87) is annotated "1 Form C
type only" and is NOT used here: 87a does not exist on a 1 Form A part.
That annotation is also what disambiguates the two near-equal 8.0/8.4
dimensions - 87a sits ~0.4mm off the 85/86 column, which only matters
for Form C.

MIRRORING - the one genuinely fatal thing to get wrong. Panasonic's
pattern is explicitly BOTTOM view; a KiCad footprint for a through-hole
part mounted on the top side is authored in TOP view. The pattern is
asymmetric (30 is 9.5mm from the coil column, 87 is 8.4mm), so getting
this backwards would not merely look odd, it would put the holes where
the terminals are not. Coordinates below are therefore mirrored in X
(x_top = 17.9 - x_bottom), then re-centred on the pad group, then
converted to KiCad's Y-down convention.

Terminal slots are 2.6 x 1.4 mm per the pattern's own "4(or5)x2.6" /
"4(or5)x1.4" callouts. Read as HOLE (not copper) dimensions, which the
external-dimensions view supports: the real blades are ~0.8mm thick, so
2.6x1.4 leaves a sane insertion clearance. Copper pads are the slot plus
a 0.5mm annular ring all round. Terminals 30/85/86 have their blades
horizontal; 87's blade runs vertical (visible in both drawings), so its
slot is rotated - a real per-terminal difference, not a uniform pattern.

HONEST, FLAGGED LIMITATION. The datasheet dimensions the terminal
positions precisely but does not (in what was extracted here) give a
clean dimension from the body edge to the pad group, and rough pixel
measurement off the drawing suggests the pads are NOT centred in the
26.0 x 22.0 mm body - roughly 6mm of body on one side and 2.3mm on the
other. Only the courtyard and silkscreen depend on that, never the
electrical fit or the hole positions. Rather than encode an uncertain
offset, the body outline is drawn centred on the pad group and the
COURTYARD is deliberately oversized (30 x 26 mm) so it still encloses
the real body whichever way the offset actually falls. Worth tightening
against Panasonic's own downloadable CAD data before fab if board area
around K1 ever gets tight.
"""
import os

FP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "footprints", "panasonic.pretty")
FP_NAME = "Panasonic_CB1a-T-P_Relay_40A"

# --- real geometry, derived above (mm) -------------------------------
SPAN_X = 17.9        # terminal 30 -> terminal 87
COIL_FROM_87 = 8.4   # terminal 85/86 column -> terminal 87
HALF_Y = 8.4         # 16.8 / 2, terminal 86 (up) / 85 (down)
SLOT_LONG = 2.6
SLOT_SHORT = 1.4
RING = 0.5           # annular copper ring added around each slot

BODY_X, BODY_Y = 26.0, 22.0
CRTYD_X, CRTYD_Y = 30.0, 26.0   # deliberately oversized, see docstring

# Bottom-view X positions measured from terminal 30, then mirrored to
# top view and re-centred on the pad group.
_bv = {"30": 0.0, "85": SPAN_X - COIL_FROM_87, "86": SPAN_X - COIL_FROM_87,
       "87": SPAN_X}
_centre = SPAN_X / 2.0
PADS = {}
for name, x_bv in _bv.items():
    x = (SPAN_X - x_bv) - _centre          # mirror, then centre
    y = {"86": -HALF_Y, "85": +HALF_Y}.get(name, 0.0)   # KiCad Y is down
    # 87's blade runs vertical; 30/85/86 run horizontal
    if name == "87":
        drill_w, drill_h = SLOT_SHORT, SLOT_LONG
    else:
        drill_w, drill_h = SLOT_LONG, SLOT_SHORT
    PADS[name] = (round(x, 3), round(y, 3), drill_w, drill_h)


def build():
    hx, hy = BODY_X / 2.0, BODY_Y / 2.0
    cx, cy = CRTYD_X / 2.0, CRTYD_Y / 2.0
    L = []
    L.append(f'(footprint "{FP_NAME}" (version 20211014) (generator build_k1_footprint.py)')
    L.append('  (layer "F.Cu")')
    L.append('  (tedit 0)')
    L.append(f'  (descr "Panasonic CB1a-T-P-12V automotive PCB relay, 1 Form A (SPST-NO), '
             f'40A 14VDC, 12V coil, sealed, heat resistant -40..+125C. '
             f'Generated by build_k1_footprint.py from datasheet ds_61202_en_cb.")')
    L.append('  (tags "relay automotive power SPST 40A Panasonic CB")')
    L.append('  (attr through_hole)')
    L.append('  (fp_text reference "K**" (at 0 -15) (layer "F.SilkS")')
    L.append('    (effects (font (size 1 1) (thickness 0.15))))')
    L.append(f'  (fp_text value "{FP_NAME}" (at 0 15) (layer "F.Fab")')
    L.append('    (effects (font (size 1 1) (thickness 0.15))))')

    # body outline on silkscreen + fab
    for layer, width in (("F.SilkS", 0.12), ("F.Fab", 0.1)):
        L.append(f'  (fp_rect (start {-hx} {-hy}) (end {hx} {hy}) '
                 f'(layer "{layer}") (width {width}) (fill none))')
    # courtyard, deliberately oversized - see docstring
    L.append(f'  (fp_rect (start {-cx} {-cy}) (end {cx} {cy}) '
             f'(layer "F.CrtYd") (width 0.05) (fill none))')

    for name, (x, y, dw, dh) in PADS.items():
        sw, sh = dw + 2 * RING, dh + 2 * RING
        L.append(f'  (pad "{name}" thru_hole oval (at {x} {y}) '
                 f'(size {round(sw,3)} {round(sh,3)}) (drill oval {dw} {dh}) '
                 f'(layers "*.Cu" "*.Mask"))')
    L.append(')')
    return "\n".join(L) + "\n"


def main():
    os.makedirs(FP_DIR, exist_ok=True)
    path = os.path.join(FP_DIR, FP_NAME + ".kicad_mod")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build())
    print(f"Wrote {path}")
    print(f"  {len(PADS)} real pads (1 Form A: no 87a):")
    for name, (x, y, dw, dh) in sorted(PADS.items()):
        print(f"    {name:>3}  at ({x:>6.2f}, {y:>5.2f})  slot {dw} x {dh}")
    # real self-check: the derived spans must reproduce the datasheet's
    # own dimension chain, not just look plausible
    xs = {n: p[0] for n, p in PADS.items()}
    assert abs(abs(xs["30"] - xs["87"]) - SPAN_X) < 1e-6, "30->87 span wrong"
    assert abs(abs(xs["85"] - xs["87"]) - COIL_FROM_87) < 1e-6, "85->87 span wrong"
    assert abs(PADS["86"][1] - PADS["85"][1]) == 2 * HALF_Y, "86->85 span wrong"
    assert xs["85"] == xs["86"], "coil terminals must share a column"
    print("  Self-check OK: 30->87 = 17.9, 85/86->87 = 8.4, 86->85 = 16.8")


if __name__ == "__main__":
    main()
