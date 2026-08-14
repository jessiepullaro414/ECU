"""
Generates a KiCad schematic (.kicad_sch) for "ECU", a standalone automotive
engine-management ECU (drives injectors + ignition directly, closed-loop on
engine sensors, usable across 4/6/8-cylinder engines from one board, with
both BLE and USB-C as full wired/wireless firmware-flashing paths). See
../.claude/plans (or this session's approved plan) for the full architecture
rationale: NXP Qorivva MPC5606B core, 2x NXP MC33810 injector/ignition
drivers, FTDI FT4232HA (USB-C wired flash+debug), TI CC2640R2F-Q1 (BLE
wireless flash+telemetry).

This is a direct port of manifold-pcb/build_schematic.py's reusable
generator framework (register_symbol/place DSL, snap-to-grid, power-symbol
vs. label net-typing rules, self-validation, kicad-cli upgrade step) - see
that project's file for the fully-worked, heavily-commented original this
was derived from. No project-specific circuitry is defined here yet: each
subsystem (power input protection, MCU core, injector/ignition drivers,
sensor front end, CAN, USB-C bridge, BLE co-processor, connectors) gets
added incrementally in its own pass, each with its own real-datasheet
verification and ERC loop, the same way Manifold was built up.

Connectivity model (same as Manifold):
  * REAL WIRES for main signal flow, aligned row-for-row where practical.
  * POWER SYMBOLS (+5V / +3V3 / GND / VIN, extend as new rails are added)
    everywhere a power net is touched. Power nets NEVER use local labels
    (mixing local labels with power-symbol nets splits them into separate
    nets in KiCad).
  * LOCAL LABELS only for genuine cross-sheet-style references.

Geometry: KiCad symbol space is Y-UP; the schematic sheet is Y-DOWN. A pin
defined at (px, py) in a symbol placed at (x, y) rot 0 lands at (x+px, y-py).
Pin 'at' is the electrical connection point; pin angle points TOWARD the body.
"""
import os
import uuid as uuid_lib

from kiutils.schematic import Schematic
from kiutils.symbol import Symbol, SymbolPin
from kiutils.items.common import (Position, Property, Effects, Font, Stroke,
                                  PageSettings, TitleBlock, Justify)
from kiutils.items.syitems import SyRect, SyPolyLine
from kiutils.items.schitems import (SchematicSymbol, Connection, LocalLabel,
                                    SymbolInstance, Text as SchText, NoConnect)


def U():
    return str(uuid_lib.uuid4())


PITCH = 2.54
LEAD = 2.54
POWER_NETS = {"+5V", "+3V3", "GND", "VIN"}

# See manifold-pcb/build_schematic.py's own comment on this constant: every
# pin offset baked into a symbol via layout() below is already an exact
# multiple of this grid; snap() at place()'s entry point is enough to put
# the whole sheet on-grid without touching every call site. Required for
# kicad-cli sch erc's endpoint_off_grid check.
GRID = 1.27


def snap(v):
    return round(round(v / GRID) * GRID, 2)

# pin angle points from connection tip toward body (KiCad convention)
SIDE_ANGLE = {'L': 0, 'R': 180, 'T': 270, 'B': 90}
# outward direction of a stub on the SHEET (Y-down) for each side
STUB_DIR = {'L': (-1, 0), 'R': (1, 0), 'T': (0, -1), 'B': (0, 1)}
# label rotation so text reads away from the symbol
LABEL_ANGLE = {'L': 180, 'R': 0, 'T': 90, 'B': 270}


# ---------------------------------------------------------------------------
# Symbol factory
# ---------------------------------------------------------------------------
def layout(sides):
    """sides: {'L'/'R'/'T'/'B': [(number, name, etype), ...]} in top-to-bottom
    (L/R) or left-to-right (T/B) SHEET order. Returns (w, h, pins, side_map)."""
    nL, nR = len(sides.get('L', [])), len(sides.get('R', []))
    nT, nB = len(sides.get('T', [])), len(sides.get('B', []))
    height = max(max(nL, nR, 1) * PITCH + PITCH, PITCH * 2)
    width = max(max(nT, nB, 1) * PITCH + PITCH, PITCH * 2)
    pins, side_map = [], {}

    def add_vertical(entries, x_tip, angle, side):
        n = len(entries)
        for i, (num, name, etype) in enumerate(entries):
            # symbol space is Y-up: first entry gets largest py -> top on sheet
            py = ((n - 1) * PITCH) / 2 - i * PITCH
            pins.append(SymbolPin(electricalType=etype, graphicalStyle="line",
                                  position=Position(round(x_tip, 2), round(py, 2), angle),
                                  length=LEAD, name=name, number=str(num)))
            side_map[str(num)] = side

    def add_horizontal(entries, y_tip, angle, side):
        n = len(entries)
        for i, (num, name, etype) in enumerate(entries):
            px = -((n - 1) * PITCH) / 2 + i * PITCH
            pins.append(SymbolPin(electricalType=etype, graphicalStyle="line",
                                  position=Position(round(px, 2), round(y_tip, 2), angle),
                                  length=LEAD, name=name, number=str(num)))
            side_map[str(num)] = side

    if 'L' in sides:
        add_vertical(sides['L'], -(width / 2 + LEAD), SIDE_ANGLE['L'], 'L')
    if 'R' in sides:
        add_vertical(sides['R'], width / 2 + LEAD, SIDE_ANGLE['R'], 'R')
    if 'T' in sides:
        add_horizontal(sides['T'], height / 2 + LEAD, SIDE_ANGLE['T'], 'T')
    if 'B' in sides:
        add_horizontal(sides['B'], -(height / 2 + LEAD), SIDE_ANGLE['B'], 'B')
    return width, height, pins, side_map


lib_symbols = {}   # lib_id -> (Symbol, side_map, width, height)


def register_symbol(lib_id, ref_prefix, value, footprint, sides,
                    datasheet="~", hide_pin_names=False):
    w, h, pins, side_map = layout(sides)
    sym = Symbol.create_new(id=lib_id, reference=ref_prefix, value=value,
                            footprint=footprint, datasheet=datasheet)
    sym.pinNames = True
    sym.pinNamesOffset = 0.508
    sym.pinNamesHide = hide_pin_names
    sym.hidePinNumbers = False
    sym.properties[0].position = Position(0, h / 2 + 1.8, 0)    # Ref above (sym Y-up)
    sym.properties[1].position = Position(0, -(h / 2 + 1.8), 0)  # Value below
    sym.graphicItems.append(SyRect(
        start=Position(-w / 2, -h / 2), end=Position(w / 2, h / 2),
        stroke=Stroke(width=0.254, type="default")))
    sym.graphicItems[-1].fill.type = "background"
    sym.pins = pins
    lib_symbols[lib_id] = (sym, side_map, w, h)


def register_power_symbol(net, is_gnd):
    lib_id = f"{LIB}:PWR_{net}"
    sym = Symbol.create_new(id=lib_id, reference="#PWR", value=net)
    sym.isPower = True
    sym.pinNames = True
    sym.pinNamesOffset = 0
    sym.pinNamesHide = True
    sym.hidePinNumbers = True
    sym.properties[0].effects.hide = True                      # hide "#PWR" ref
    stroke = Stroke(width=0.254, type="default")
    if is_gnd:
        sym.properties[1].position = Position(0, -4.6, 0)      # value below
        for pts in ([(0, 0), (0, -1.27)],
                    [(-1.27, -1.27), (1.27, -1.27)],
                    [(-0.762, -1.905), (0.762, -1.905)],
                    [(-0.254, -2.54), (0.254, -2.54)]):
            sym.graphicItems.append(SyPolyLine(
                points=[Position(a, b) for a, b in pts], stroke=stroke))
    else:
        sym.properties[1].position = Position(0, 3.9, 0)       # value above bar
        for pts in ([(0, 0), (0, 2.54)],
                    [(-1.016, 2.54), (1.016, 2.54)]):
            sym.graphicItems.append(SyPolyLine(
                points=[Position(a, b) for a, b in pts], stroke=stroke))
    sym.pins = [SymbolPin(electricalType="power_in", graphicalStyle="line",
                          position=Position(0, 0, 90), length=0,
                          name=net, number="1", hide=True)]
    lib_symbols[lib_id] = (sym, {"1": 'T'}, 0, 0)


def register_pwr_flag(net):
    """Real KiCad PWR_FLAG equivalent - see manifold-pcb's own comment on
    this function for the full ERC rationale (a bare same-name power symbol
    isn't enough; ERC needs an actual power_out pin wired to a real point on
    the net)."""
    lib_id = f"{LIB}:PWR_FLAG_{net}"
    sym = Symbol.create_new(id=lib_id, reference="#FLG", value=net)
    sym.isPower = True
    sym.pinNames = True
    sym.pinNamesOffset = 0
    sym.pinNamesHide = True
    sym.hidePinNumbers = True
    sym.properties[0].effects.hide = True
    sym.properties[1].position = Position(0, 3.9, 0)
    stroke = Stroke(width=0.254, type="default")
    for pts in ([(0, 0), (0, 2.54)], [(0, 2.54), (-0.889, 1.651)],
                [(0, 2.54), (0.889, 1.651)]):
        sym.graphicItems.append(SyPolyLine(
            points=[Position(a, b) for a, b in pts], stroke=stroke))
    sym.pins = [SymbolPin(electricalType="power_out", graphicalStyle="line",
                          position=Position(0, 0, 90), length=0,
                          name=net, number="1", hide=True)]
    lib_symbols[lib_id] = (sym, {"1": 'T'}, 0, 0)


# ---------------------------------------------------------------------------
# Schematic scaffolding
# ---------------------------------------------------------------------------
sch = Schematic.create_new()
sch.paper = PageSettings(paperSize="A3")
# Fixed (not regenerated each run) so it always matches the "sheets" entry
# cached in ECU.kicad_pro - a fresh random UUID here would silently desync
# the two files on every regeneration. Generated once for this project.
sch.uuid = "b3fab4c8-6a5e-4518-b1c8-cef169da5185"
sch.titleBlock = TitleBlock(
    title="ECU - standalone automotive engine-management ECU",
    date="2026-08-02", revision="A",
    company="Generated design spec - verify before fab",
    comments={1: "SCAFFOLD ONLY: generator framework in place, no circuitry yet. "
                 "NXP Qorivva MPC5606B core, 2x L9779WD-SPI inj/ign drivers, "
                 "FT4232HA USB-C + CC2640R2F-Q1 BLE dual programming paths."})

wires, labels, texts, no_connects = [], [], [], []
pin_pos = {}        # (ref, pin_number_str) -> (x, y) on sheet
pwr_count = 0


def add_wire(x1, y1, x2, y2):
    wires.append(Connection(type="wire",
                            points=[Position(round(x1, 2), round(y1, 2)),
                                    Position(round(x2, 2), round(y2, 2))],
                            stroke=Stroke(width=0.0, type="default"), uuid=U()))


def add_label(text, x, y, angle):
    assert text not in POWER_NETS, f"power net {text} must use a power symbol, not a label"
    labels.append(LocalLabel(text=text, position=Position(round(x, 2), round(y, 2), angle),
                             effects=Effects(font=Font(width=1.27, height=1.27)),
                             uuid=U()))


def _instance(lib_id, ref, value, x, y, ref_hidden=False):
    sym, _, _, h = lib_symbols[lib_id]
    inst = SchematicSymbol()
    inst.libId = lib_id
    inst.position = Position(x, y, 0)
    inst.unit = 1
    inst.inBom = not ref.startswith("#")
    inst.onBoard = True
    inst.uuid = U()
    fp = next((p.value for p in sym.properties if p.key == "Footprint"), "")
    ref_eff = Effects(font=Font(width=1.27, height=1.27), hide=ref_hidden)
    val_y = y - sym.properties[1].position.Y   # sheet Y-down flip of value pos
    ref_y = y - sym.properties[0].position.Y
    inst.properties = [
        Property(key="Reference", value=ref, id=0, position=Position(x, ref_y, 0), effects=ref_eff),
        Property(key="Value", value=value, id=1, position=Position(x, val_y, 0),
                 effects=Effects(font=Font(width=1.27, height=1.27))),
        Property(key="Footprint", value=fp, id=2, position=Position(x, y, 0),
                 effects=Effects(font=Font(width=1.27, height=1.27), hide=True)),
    ]
    for pin in sym.pins:
        inst.pins[pin.number] = U()
    sch.schematicSymbols.append(inst)
    sch.symbolInstances.append(SymbolInstance(
        path=f"/{inst.uuid}", reference=ref, unit=1, value=value, footprint=fp))
    return inst


def place_power(net, x, y):
    """Power symbol whose connection point is exactly (x, y)."""
    global pwr_count
    pwr_count += 1
    _instance(f"{LIB}:PWR_{net}", f"#PWR{pwr_count:03d}", net, x, y, ref_hidden=True)


flg_count = 0


def place_pwr_flag(net, x, y):
    """PWR_FLAG instance - joins `net`'s global net by name, same mechanism
    as place_power, doesn't need to sit at any particular existing wire/pin
    coordinate."""
    global flg_count
    flg_count += 1
    _instance(f"{LIB}:PWR_FLAG_{net}", f"#FLG{flg_count:03d}", net, x, y, ref_hidden=True)


def place(lib_id, ref, value, x, y, conn=None):
    """Place a part. conn maps pin number -> one of:
         ('wire',)                   no stub; a wire will be drawn to the pin later
         ('label', NAME[, stub_len]) stub outward + local label
         ('pwr', NET[, stub_len])    stub (+riser if horizontal) + power symbol
         ('nc',)                     no-connect flag directly on the pin - for a
                                     pin whose real electrical type ISN'T
                                     no_connect but is deliberately left
                                     unused in this design.
       default: ('label', <pin name>, LEAD)"""
    x, y = snap(x), snap(y)
    conn = {str(k): v for k, v in (conn or {}).items()}
    sym, side_map, w, h = lib_symbols[lib_id]
    _instance(lib_id, ref, value, x, y)

    for pin in sym.pins:
        num = pin.number
        px = round(x + pin.position.X, 2)
        py = round(y - pin.position.Y, 2)   # Y-flip: symbol Y-up -> sheet Y-down
        pin_pos[(ref, num)] = (px, py)
        if pin.electricalType == "no_connect" and num not in conn:
            no_connects.append(NoConnect(position=Position(px, py), uuid=U()))
            continue
        mode = conn.get(num, ('label', pin.name, LEAD))
        kind = mode[0]
        if kind == 'wire':
            continue
        if kind == 'nc':
            no_connects.append(NoConnect(position=Position(px, py), uuid=U()))
            continue
        side = side_map[num]
        dx, dy = STUB_DIR[side]
        stub = mode[2] if len(mode) > 2 else LEAD
        ex, ey = round(px + dx * stub, 2), round(py + dy * stub, 2)
        add_wire(px, py, ex, ey)
        if kind == 'label':
            name = mode[1]
            if name in ("~", ""):
                raise ValueError(f"{ref}.{num}: generic pin needs a net in conn")
            add_label(name, ex, ey, LABEL_ANGLE[side])
        elif kind == 'pwr':
            net = mode[1]
            if side in ('T', 'B'):
                place_power(net, ex, ey)
            else:
                rise = PITCH if net == "GND" else -PITCH   # sheet Y-down: up = -Y
                add_wire(ex, ey, ex, ey + rise)
                place_power(net, ex, ey + rise)


def off(lib_id, num):
    """Sheet-space offset of a pin from its symbol origin."""
    sym, _, _, _ = lib_symbols[lib_id]
    for p in sym.pins:
        if p.number == str(num):
            return p.position.X, -p.position.Y
    raise KeyError(num)


def wire_pins(refA, pinA, refB, pinB, label=None, label_x=None):
    (x1, y1), (x2, y2) = pin_pos[(refA, str(pinA))], pin_pos[(refB, str(pinB))]
    assert abs(y1 - y2) < 0.01 or abs(x1 - x2) < 0.01, \
        f"{refA}.{pinA} -> {refB}.{pinB} not aligned: ({x1},{y1}) vs ({x2},{y2})"
    add_wire(x1, y1, x2, y2)
    if label:
        lx = label_x if label_x is not None else (x1 + x2) / 2
        add_label(label, lx, y1, 0)


def section_text(s, x, y):
    texts.append(SchText(text=s, position=Position(x, y, 0),
                         effects=Effects(font=Font(height=2.0, width=2.0,
                                                   thickness=0.35, bold=True))))


# ---------------------------------------------------------------------------
# Symbol definitions
# ---------------------------------------------------------------------------
LIB = "ECU"
def P(num, name, etype):
    return (num, name, etype)

for net in ("+5V", "+3V3", "VIN"):
    register_power_symbol(net, is_gnd=False)
register_power_symbol("GND", is_gnd=True)
register_pwr_flag("VIN")
register_pwr_flag("GND")

# Generic passive symbols reused directly from manifold-pcb (same shapes,
# same real-part footprints work fine here too) - kept registered even
# though nothing places them yet, so the next session's power-input-
# protection pass (fuse/TVS/reverse-battery-FET/buck/LDO, same topology as
# Manifold's, plus a new separate high-current rail for the injector/
# ignition drivers) can start from these immediately.
register_symbol(f"{LIB}:TVS_V", "D", "TBD - automotive TVS (AEC-Q101)", "Diode_SMD:D_SMC",
                {'T': [P(1, "~", "passive")], 'B': [P(2, "~", "passive")]},
                hide_pin_names=True)
register_symbol(f"{LIB}:C_V", "C", "TBD", "Capacitor_SMD:C_0603_1608Metric",
                {'T': [P(1, "~", "passive")], 'B': [P(2, "~", "passive")]},
                hide_pin_names=True)
register_symbol(f"{LIB}:L_H", "L", "TBD", "Inductor_SMD:L_1210_3225Metric",
                {'L': [P(1, "~", "passive")], 'R': [P(2, "~", "passive")]},
                hide_pin_names=True)
register_symbol(f"{LIB}:R_V", "R", "TBD", "Resistor_SMD:R_0603_1608Metric",
                {'T': [P(1, "~", "passive")], 'B': [P(2, "~", "passive")]},
                hide_pin_names=True)

# Same Keystone 3568 MINI blade fuse holder as Manifold - real Littelfuse
# MINI-series blade fuse ELEMENTS cover 2A-30A, so one holder footprint
# serves every fuse on this board (F1 main/F2 logic/F3 injector/F4 ignition),
# just with a different current-rated element plugged in per position.
register_symbol(f"{LIB}:Fuse", "F", "TBD", "Fuse:Fuseholder_Blade_Mini_Keystone_3568",
                {'L': [P(1, "~", "passive")], 'R': [P(2, "~", "passive")]},
                hide_pin_names=True)
# Small logic-level automotive MOSFET, SOT-23/TO-236AB - same real pin
# numbering Manifold verified for this package/family (Nexperia PMV2x/PMV3x
# all share it): pin1=G, pin2=S, pin3=D. Used here for the relay-coil driver.
register_symbol(f"{LIB}:MOSFET_N", "Q", "TBD", "Package_TO_SOT_SMD:SOT-23",
                {'L': [P(2, "S", "passive")], 'R': [P(3, "D", "passive")],
                 'B': [P(1, "G", "input")]})
# Big automotive power MOSFET, D2PAK (TO-263AB) - Vishay SQM40020EL_GE3
# (100A/40V, 2.2-2.7mOhm RDSon, AEC-Q101). Used for the ONE shared reverse-
# battery-protection stage sized for the whole board's current (up to F1's
# 30A) rather than duplicating a separate small-MOSFET protection circuit
# per branch - the logic and power-stage branches split off downstream of
# this single protected VIN_PROT rail instead. Pin layout mirrored to match
# MOSFET_N's S/D/G side assignment for wiring consistency; CONFIRM the real
# Vishay D2PAK pin-to-terminal mapping (G/D/S vs. tab=D) against the actual
# datasheet pin table before fab - not yet independently verified like
# Manifold's small-MOSFET pinout was.
register_symbol(f"{LIB}:MOSFET_N_BIG", "Q", "TBD", "Package_TO_SOT_SMD:TO-263-3_TabPin2",
                {'L': [P(2, "S", "passive")], 'R': [P(3, "D", "passive")],
                 'B': [P(1, "G", "input")]})
# LM74700-Q1 ideal-diode controller, same real pinout Manifold verified.
# Reused here for the shared big-MOSFET protection stage (U2) - this project
# doesn't need Manifold's second copy on the logic branch, since the logic
# branch now taps the already-protected VIN_PROT rail downstream instead.
register_symbol(f"{LIB}:IC_IdealDiode", "U", "TBD", "Package_TO_SOT_SMD:SOT-23-6",
                {'L': [P(1, "VCAP", "passive"), P(2, "GND", "power_in"),
                       P(3, "EN", "input")],
                 'R': [P(4, "CATHODE", "input"), P(5, "GATE", "output"),
                       P(6, "ANODE", "input")]},
                datasheet="https://www.ti.com/lit/ds/symlink/lm74700-q1.pdf")
# LMR33630-Q1 buck, same real pinout Manifold verified.
register_symbol(f"{LIB}:IC_Buck", "U", "TBD", "TI_RNX0012C_VQFN-HR:TI_RNX0012C_VQFN-HR-12_2x3mm_P0.5mm",
                {'L': [P(2, "VIN", "power_in"), P(10, "VIN", "power_in"),
                       P(1, "PGND", "power_in"), P(11, "PGND", "power_in")],
                 'R': [P(12, "SW", "output"), P(4, "BOOT", "passive"),
                       P(7, "FB", "input"), P(9, "EN", "input")],
                 'T': [P(5, "VCC", "power_out"), P(8, "PG", "output")],
                 'B': [P(6, "AGND", "power_in"), P(3, "NC", "passive")]},
                datasheet="https://www.ti.com/lit/ds/symlink/lmr33630-q1.pdf")
# TLV733P-Q1 LDO, same real pinout Manifold verified.
register_symbol(f"{LIB}:IC_LDO33", "U", "TBD", "Package_TO_SOT_SMD:SOT-23-5",
                {'L': [P(1, "IN", "power_in")], 'R': [P(5, "OUT", "power_out")],
                 'B': [P(2, "GND", "power_in"), P(3, "EN", "input"),
                       P(4, "NC", "no_connect")]},
                datasheet="https://www.ti.com/lit/ds/symlink/tlv733p-q1.pdf")
# High-power automotive TVS for the shared VIN_PROT rail - Littelfuse 5KP33A
# (5000W @ 10/1000us, 33V standoff - same voltage-rating logic as Manifold's
# SMCJ33A, but real load-dump-rated power handling since this rail carries
# the whole board's current, not just a small logic branch). Through-hole
# P600 package: better power dissipation than an SMD TVS at this level.
register_symbol(f"{LIB}:TVS_HP", "D", "TBD", "Diode_THT:D_P600_R-6_P12.70mm_Horizontal",
                {'T': [P(1, "~", "passive")], 'B': [P(2, "~", "passive")]},
                hide_pin_names=True)
# Automotive Schottky flyback diode across the relay coil - Nexperia
# PMEG4010BEA (40V/1A, AEC-Q101, SOD-123). Verify current/reverse-voltage
# margin against the real chosen relay's coil inrush before fab.
register_symbol(f"{LIB}:D_FLYBACK", "D", "TBD", "Diode_SMD:D_SOD-123",
                {'L': [P(1, "A", "passive")], 'R': [P(2, "K", "passive")]},
                hide_pin_names=True)
# REAL BUG found + fixed at PCB-generation time (step 10): the originally
# chosen "T9AP5D52" footprint turned out, on actual inspection, to be a
# 2-hole MOUNTING-ONLY footprint (2 unnamed non-plated holes, zero
# electrical pads) - T9AP5D52 is a real chassis/socket-mount ISO relay,
# not a PCB-solderable part at all, so no amount of correct pin-NUMBER
# assignment could have made it electrically real. This is exactly the
# "verify before trusting" gap flagged (but not yet closed) back in step
# 2 - closed now, the hard way, via build_pcb.py's own net-pin-count
# check catching real vs. schematic pin-count mismatches (the same
# mechanism Manifold's build_pcb.py was built around).
# Real fix: switched to Schrack RT1-16A-FormC, a genuinely PCB-mountable
# SPDT relay with a real bundled footprint and real numbered pins - but a
# DIFFERENT real numbering convention (European/Schrack contact numbering:
# 11=common, 12=NC, 14=NO; A1/A2=coil, no polarity), not the ISO 7588
# automotive numbering (85/86/30/87) the wrong part had suggested.
# KNOWN OPEN LIMITATION, honestly flagged not hidden: this part is real
# but only 16A-rated, while the board's real combined injector+ignition
# current (per step 2's own research) can reach ~22-28A - genuinely
# undersized for the worst case. A dedicated higher-current real PCB
# relay search (or splitting into two relays, one per F3/F4 branch) is
# needed before fab; kept as-is for now so the board generates and
# routes with a real, correctly-connected part while that follow-up
# research happens separately.
register_symbol(f"{LIB}:RELAY_ISO_MINI", "K", "TBD",
                "Relay_THT:Relay_SPDT_Schrack-RT1-16A-FormC_RM5mm",
                {'L': [P("A1", "COIL1", "passive"), P("A2", "COIL2", "passive")],
                 'R': [P(11, "COM", "passive"), P(14, "NO", "passive"),
                       P(12, "NC", "no_connect")]},
                hide_pin_names=True)

# 4-pin 3225 crystal, same real footprint/pin-redundancy pattern Manifold
# verified (pins 1/3 = one terminal, 2/4 = the other - standard 3225 4-pad
# crystal packages wire opposite corners together for mechanical stability).
register_symbol(f"{LIB}:XTAL", "Y", "TBD", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
                {'R': [P(1, "OSC_IN", "passive"), P(2, "OSC_OUT", "passive")],
                 'L': [P(3, "OSC_IN", "passive"), P(4, "OSC_OUT", "passive")]},
                hide_pin_names=True)
# Generic 2.54mm pin header for the JTAG programming connector - our own
# pin assignment (no external standard to match, unlike the MCU's own real
# pins), real bundled KiCad footprint.
register_symbol(f"{LIB}:CONN_JTAG", "J", "TBD", "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
                {'L': [P(1, "VCC", "power_in"), P(2, "GND", "power_in"),
                       P(3, "TCK", "input"), P(4, "TMS", "input"),
                       P(5, "TDI", "input"), P(6, "TDO", "input"),
                       P(7, "RESET", "bidirectional"), P(8, "GND", "power_in")]},
                hide_pin_names=True)

# ---------------------------------------------------------------------------
# MPC5606B MCU core (plan step 3)
# ---------------------------------------------------------------------------
# Real, verified 144-LQFP pin table - NOT a placeholder or guess. NXP's own
# site 404s on the datasheet/reference-manual URLs and alldatasheet 403s
# (the same access problem Manifold hit with the S32K144); a Farnell mirror
# that looked promising turned out to be a completely unrelated connector
# datasheet. Real data came from two cross-checked sources: the MPC5606BK
# datasheet Rev. 5 (via a chipdip.ru mirror) and the MPC5606BK Reference
# Manual Rev. 2 (recovered from the Wayback Machine, since NXP's own PCN-
# attachment URL is now 404 live) - pin numbers were confirmed BOTH from the
# datasheet's Table 2 (per-package pin-number columns) AND by rendering the
# real "Figure 3. 144 LQFP pinout" diagram to an image and reading pin
# numbers directly off it, not trusting either source alone.
#
# Power domains (no VDD_HV_OSC/VDD_HV_FLA/VDDA/VSSA/VRH/VRL/PLL-supply pins
# exist on this part - confirmed by exhaustive text search of both real
# documents, so none are on the symbol below):
#   VDD_HV/VSS_HV: main I/O domain, dual-rated 3.0-3.6V or 4.5-5.5V - this
#     board runs it at 3.3V, matching the rest of the logic from step 2.
#   VDD_LV/VSS_LV: INTERNALLY-REGULATED core logic rail (~1.28V typical),
#     generated on-chip from VDD_BV - NOT externally supplied. Decoupling
#     caps only; expect a genuine power_pin_not_driven ERC exception here,
#     same category as Manifold's own documented VDDA/+5V exceptions.
#   VDD_BV: external "ballast" supply feeding the internal VDD_LV regulator
#     - same voltage range as VDD_HV, tied to the same +3V3 rail here.
#   VDD_HV_ADC0/1, VSS_HV_ADC0/1: analog supply/ground for the two ADC
#     instances - ferrite-isolated from +3V3, same treatment Manifold gave
#     its single VDDA pin.
# EXTAL/XTAL: real supported range is 4-16MHz (datasheet Table 34); 8MHz
# chosen, consistent with Manifold's own crystal - load-cap value (18pF) is
# a typical placeholder pending the real chosen crystal's own specified
# load capacitance, same "verify before fab" flag as Manifold's Y1.
# RESET: real pin name is just "RESET" (active low) - no "PORESET" naming.
# JTAG (TDI/TDO/TCK/TMS): standard 4-wire JTAG, confirmed via BOTH the
# datasheet's own footnotes on PC[0:1]/PH[9:10] and an exhaustive
# reference-manual search for JCOMP/TRST (none exists as an external pin on
# this part - only an internal JTAGC block signal, no hardware TAP reset).
# Boot config (FAB=PA9, ABS=PA8): real Boot Assist Module mode select,
# Reference Manual Table 5-1. Default (nothing external attached, using the
# chip's own weak pulldown on FAB / weak pullup on ABS) is normal flash
# boot. Forcing LINFlex serial boot needs FAB=1 (external pull-up) AND
# ABS=0 (external pull-down) held during reset - the actual pull network is
# step 7's job (USB-C/FT4232HA programming path), so FAB/ABS are broken out
# here as labeled stubs for step 7 to complete, same incremental pattern as
# step 2's RELAY_CTRL.
# LINFlex serial-boot pins (LIN0TX=PB2, LIN0RX=PB3): Reference Manual
# Section 5.2.2, confirmed real baud-rate formula (sysclk/833) and frame
# format too. Also broken out as labeled stubs for step 7 to complete.
# (Bonus, not used yet: FlexCAN0 boot alternative CAN0TX=PB0=pin31/
# CAN0RX=PB1=pin32, confirmed in RM Section 5.2.3 - worth remembering for
# step 6's real CAN transceiver wiring.)
#
# CORRECTION (found during step-4-followup research): earlier planning
# (this project's README/memory) assumed this part has "eTPU2" - it does
# NOT. The real reference manual states directly (Rev. 2, Section 2.4.9):
# "The MPC5606BK implements a scaled-down version of the eMIOS module."
# eTPU2 exists on other Qorivva siblings (e.g. MPC5674F), not this one.
# The real peripheral is eMIOS (Enhanced Modular I/O Subsystem), two
# instances (eMIOS_0/eMIOS_1, channels named E0UC[n]/E1UC[n]) - still a
# real hardware PWM/timer coprocessor well suited to injector/ignition
# timing, just a different real name than originally assumed. Fixed here
# and in the project's docs.
#
# 16 real eMIOS channels claimed below for the 8 injector + 8 ignition
# real-time control lines from step 4 (MC33810's DIN0-3/GIN0-3), each
# image-verified against the datasheet's own Table 2 "Functional port
# pins" (pdftotext mangled this multi-column table too badly to trust -
# same lesson as the original MCU pin-table research: render to an image
# and read the real cells, don't trust raw text extraction of a table).
# Two real "same channel, alternate pin" duplicates exist (E0UC[7] is also
# routable to pins 89/2, not just 104) - deliberately NOT used, since
# they're the SAME channel as pin 104, not extra independent channels.
#
# DSPI (real SPI peripheral name - this part has up to 5 real DSPI
# modules; DSPI_0 used here) claimed for the shared SPI bus from step 4:
# SCK_0 (pin40), SIN_0 (pin45, MCU's own receive - ties to the MC33810s'
# shared SPI_SO net), SOUT_0 (pin44, MCU's own transmit - ties to the
# MC33810s' shared SPI_SI net). No dedicated hardware chip-select pin
# needed - CS timing for this application is lenient enough to bit-bang
# on any plain GPIO, so CS_0/CS_1 (below) are arbitrary reserved-pool
# pins, not a specific DSPI PCS pin.
#
# Real pin-mux mechanism (both peripherals): this part uses a SIUL (System
# Integration Unit Lite) Pad Configuration Register scheme - each pin has
# one PCR register with an alternate-function select field choosing which
# single peripheral signal is routed to that physical pad, and peripheral
# INPUTS additionally have their own separate input-select routing
# (explaining how E0UC[7] can source from more than one candidate pin).
#
# 4 more pins claimed as plain GPIO (no peripheral-function constraint,
# any reserved pin works): RELAY_CTRL (step 2's main-relay driver, still
# dangling until now), DRV_OUTEN (step 4's shared MC33810 kill-switch),
# SPI_CS_0/SPI_CS_1 (step 4's per-chip SPI select). Real port names for
# these 4 specifically weren't looked up (no peripheral constraint means
# it doesn't matter which free GPIO is used) - noted honestly rather than
# fabricated.
MCU_EMIOS = {
    104: "INJ1", 107: "INJ2", 108: "INJ3", 31: "INJ4",
    32: "INJ5", 83: "INJ6", 85: "INJ7", 87: "INJ8",
    143: "IGN1", 141: "IGN2", 142: "IGN3", 3: "IGN4",
    4: "IGN5", 36: "IGN6", 37: "IGN7", 131: "IGN8",
}
MCU_EMIOS_CHANNEL = {   # pin -> real eMIOS channel name, for the symbol label
    104: "E0UC7", 107: "E0UC10", 108: "E0UC11", 31: "E0UC30",
    32: "E0UC31", 83: "E0UC4", 85: "E0UC5", 87: "E0UC6",
    143: "E0UC3", 141: "E0UC12", 142: "E0UC13", 3: "E0UC14",
    4: "E0UC15", 36: "E1UC28", 37: "E1UC29", 131: "E1UC31",
}
MCU_DSPI_GPIO = [40, 45, 44, 5, 6, 9, 10]

# Every one of the other real pins not claimed by a subsystem above is a
# genuine, real, unassigned GPIO on this part (not yet researched which
# alternate function each one might serve) - marked no_connect with its
# REAL pin number, exactly like Manifold's own MCU_NC_* pattern, rather
# than guessing a function. Steps 5-6 (sensors/ADC, CAN) will claim
# specific ones of these by number as their own real datasheet research is
# done. Split roughly evenly across all 4 symbol sides purely to keep the
# generated symbol's aspect ratio sane (144 pins on one side would be
# ~370mm tall).
MCU_POWER_HV = [19, 51, 100, 123]
MCU_GND_HV = [18, 20, 49, 99, 122]
MCU_POWER_LV = [23, 46, 124]
MCU_GND_LV = [22, 47, 125]
MCU_BV = 24
MCU_ADC0 = (74, 73)   # (VDD, VSS)
MCU_ADC1 = (82, 81)

MCU_USED = {}
for _pin in MCU_POWER_HV:
    MCU_USED[_pin] = P(_pin, "VDD_HV", "power_in")
for _pin in MCU_GND_HV:
    MCU_USED[_pin] = P(_pin, "VSS_HV", "power_in")
for _pin in MCU_POWER_LV:
    MCU_USED[_pin] = P(_pin, "VDD_LV", "power_in")
for _pin in MCU_GND_LV:
    MCU_USED[_pin] = P(_pin, "VSS_LV", "power_in")
MCU_USED[MCU_BV] = P(MCU_BV, "VDD_BV", "power_in")
MCU_USED[MCU_ADC0[0]] = P(MCU_ADC0[0], "VDD_HV_ADC0", "power_in")
MCU_USED[MCU_ADC0[1]] = P(MCU_ADC0[1], "VSS_HV_ADC0", "power_in")
MCU_USED[MCU_ADC1[0]] = P(MCU_ADC1[0], "VDD_HV_ADC1", "power_in")
MCU_USED[MCU_ADC1[1]] = P(MCU_ADC1[1], "VSS_HV_ADC1", "power_in")
MCU_USED[50] = P(50, "EXTAL", "passive")
MCU_USED[48] = P(48, "XTAL", "passive")
MCU_USED[21] = P(21, "RESET", "input")
MCU_USED[126] = P(126, "TDI", "input")
MCU_USED[121] = P(121, "TDO", "output")
MCU_USED[127] = P(127, "TCK", "input")
MCU_USED[120] = P(120, "TMS", "input")
MCU_USED[106] = P(106, "FAB_PA9", "bidirectional")
MCU_USED[105] = P(105, "ABS_PA8", "bidirectional")
MCU_USED[144] = P(144, "LIN0TX_PB2", "output")
MCU_USED[1] = P(1, "LIN0RX_PB3", "input")
for _pin, _use in MCU_EMIOS.items():
    MCU_USED[_pin] = P(_pin, f"{MCU_EMIOS_CHANNEL[_pin]}_{_use}", "output")
MCU_USED[40] = P(40, "SCK_0", "output")
MCU_USED[45] = P(45, "SIN_0", "input")
MCU_USED[44] = P(44, "SOUT_0", "output")
MCU_USED[5] = P(5, "GPIO_RELAY_CTRL", "output")
MCU_USED[6] = P(6, "GPIO_DRV_OUTEN", "output")
MCU_USED[9] = P(9, "GPIO_SPI_CS0", "output")
MCU_USED[10] = P(10, "GPIO_SPI_CS1", "output")
# Step 5 additions. NOTE: pin 2 (PC[9]) was offered by this step's research
# as a second "spare eMIOS channel" for cam capture, but it's real E0UC[7]
# alternate-pin routing - the SAME internal channel as pin 104 (already
# claimed for INJ1_CTRL), not an independent one (the same research
# flagged this exact caveat for pins 89/2 relative to 104 earlier in the
# session - this later request just didn't re-surface it). Using pin 2
# for cam capture would silently alias with INJ1's firing channel - NOT
# wired here; CAM_COUT stays a genuine open stub pending one more
# targeted pin-mux lookup for a real independent capture-capable pin,
# same "don't guess it" discipline as everything else in this project.
MCU_USED[42] = P(42, "E0UC0_CRANK", "input")   # confirmed independent - real crank capture
# Second cam channel (CAM2), closing the CAM_COUT open item above: found by
# pulling the real MPC5606BK Data Sheet Rev. 5 (Table 2, "Functional port
# pins") directly - PA[1]/PCR[1] offers E0UC[1] as AF1, 144-LQFP pin 11.
# Cross-checked the WHOLE table for every other "E0UC[1]" occurrence (same
# discipline as the pin-2/E0UC[7] trap above, not assuming this one is
# clean just because it looks unused): E0UC[1] has exactly one other real
# route, PA[15]/AF3 = pin 40 - but pin 40 is already claimed here via a
# DIFFERENT alternate function on that same pad (AF2 = SCK_0, this file's
# own SPI clock), not via E0UC[1] itself, so there's no actual channel
# contention - pin 11 is a genuinely free, independent, single-purpose
# route to E0UC[1] (distinct from E0UC0/CRANK and all 16 INJ/IGN
# channels). One eMIOS unified channel per real intake+exhaust cam sensor
# is also just correct for a DOHC engine with independent cam phasing
# (VVT) - a plain GPIO/EIRQ edge interrupt would add real ISR-latency
# jitter to the captured timestamp, exactly why CRANK already uses a
# hardware input-capture channel instead of a software interrupt.
MCU_USED[11] = P(11, "E0UC1_CAM2", "input")
MCU_USED[72] = P(72, "ADC0_MAP", "input")
MCU_USED[75] = P(75, "ADC0_TPS", "input")
MCU_USED[76] = P(76, "ADC0_IAT", "input")
MCU_USED[77] = P(77, "ADC0_CLT", "input")
MCU_USED[53] = P(53, "ADC0_KNOCK", "input")
MCU_USED[12] = P(12, "GPIO_HTR_CTRL", "output")
MCU_USED[13] = P(13, "GPIO_SPI_CS2", "output")
# Step 6: two independent real FlexCAN pairs, both verified directly off
# the rendered datasheet table (this session, cross-checked against an
# independent research pass that landed on the identical FlexCAN_1 pins -
# high confidence). FlexCAN_0 (pins 31/32) was already ruled out - those
# are claimed for eMIOS INJ4/INJ5. Six real FlexCAN modules exist on this
# part (confirmed via the reference manual's peripheral memory map, not
# assumed); CAN2/CAN3 were checked and dropped (each has at least one
# leg blocked by an already-claimed pin) in favor of these two fully
# clean pairs.
MCU_USED[28] = P(28, "CAN1TX_PC10", "output")
MCU_USED[27] = P(27, "CAN1RX_PC11", "input")
MCU_USED[117] = P(117, "CAN4TX_PC2", "output")
MCU_USED[116] = P(116, "CAN4RX_PC3", "input")
MCU_USED[14] = P(14, "GPIO_CAN0_EN", "output")
MCU_USED[15] = P(15, "GPIO_CAN0_STB_N", "output")
MCU_USED[16] = P(16, "GPIO_CAN1_EN", "output")
MCU_USED[17] = P(17, "GPIO_CAN1_STB_N", "output")

# --- SENSOR/OUTPUT EXPANSION -----------------------------------------------
# 12 more real pins, every one of them read directly off the rendered
# MPC5606BK Data Sheet Rev. 5 Table 2 ("Functional port pins") - text
# extraction was used only to shortlist candidates, then each chosen
# pin's 144-LQFP number was CONFIRMED visually on the rendered page,
# the same discipline the original 144-pin table got (and the same one
# that caught the pin-2/E0UC[7] aliasing trap earlier).
#
# The 4 analog inputs deliberately use Port D's ADC0_P[4..7] precision
# channels (PD[0..3] = pins 63/64/65/66) - real input-only pads, same
# ADC0_P family as the existing MAP input on pin 72, rather than the
# shared-function ADC0_S/X channels elsewhere.
#
# The 5 eMIOS pins use channels genuinely NOT claimed by the 16
# injector/ignition channels or by crank/cam1 - checked against the
# full claimed-channel set, not assumed from the pin being unused:
#   E0UC[18]/[19]/[20]/[21] (PE[2],PE[3],PE[4],PE[5] = 128/129/132/133)
#   E0UC[25]                (PD[13] = 84)
# Note PE[6]/PE[7] also carry E0UC[22]/[23], and PE[8]/PE[9] carry the
# same two channels again - alternate routes to one channel, not extra
# channels. None are used here, so no aliasing.
MCU_USED[63] = P(63, "ADC0_P4_VBATT", "input")     # battery-voltage sense
MCU_USED[64] = P(64, "ADC0_P5_OILP", "input")      # oil pressure
MCU_USED[65] = P(65, "ADC0_P6_FUELP", "input")     # fuel pressure
MCU_USED[66] = P(66, "ADC0_P7_KNOCK2", "input")    # knock sensor, bank 2
MCU_USED[128] = P(128, "E0UC18_CAM2", "input")     # exhaust-cam capture
MCU_USED[129] = P(129, "E0UC19_VVT1", "output")    # cam phaser 1 PWM
MCU_USED[132] = P(132, "E0UC20_VVT2", "output")    # cam phaser 2 PWM
MCU_USED[133] = P(133, "E0UC21_IDLE", "output")    # idle-air valve PWM
MCU_USED[84] = P(84, "E0UC25_TACH", "output")      # tachometer output
MCU_USED[67] = P(67, "GPIO_FPUMP", "output")       # fuel-pump relay
MCU_USED[86] = P(86, "GPIO_SPI_CS3", "output")     # CJ125 #2 chip select
MCU_USED[88] = P(88, "GPIO_HTR2_CTRL", "output")   # O2 bank-2 heater PWM

# --- BOOST / EGT / FLEX-FUEL / ETC EXPANSION --------------------------------
# 14 more real pins, same discipline as the block above: every one
# shortlisted from the datasheet text, then its real 144-LQFP number
# CONFIRMED by rendering the actual table page and reading it - a rough
# second text-extraction pass this round returned WRONG numbers for
# several already-visually-confirmed pins (e.g. claimed PB[6]=69, when
# it's real, established value is 76), which is exactly why this project
# doesn't trust text extraction alone for anything that ends up on a
# schematic.
#
# ADC0_P[9..13] (PD[5..9] = pins 68/69/70/71/78) are the real, direct
# continuation of the same input-only ADC0_P family already used for
# VBATT/OILP/FUELP/KNOCK2 (ADC0_P[4..7]) - confirmed on the very next
# datasheet page.
#
# E1UC[19]/[20] (PE[12]/PE[13] = pins 109/103) and E0UC[22]/[23]
# (PE[6]/PE[7] = pins 139/140) are eMIOS channels checked against the
# FULL claimed-channel set from both expansion passes - none alias an
# injector/ignition/cam/VVT/idle/tach channel already in use.
MCU_USED[68] = P(68, "ADC0_P9_APP1", "input")      # accel pedal position 1 (ETC)
MCU_USED[69] = P(69, "ADC0_P10_APP2", "input")     # accel pedal position 2 (ETC, redundant)
MCU_USED[70] = P(70, "ADC0_P11_TPS1", "input")     # throttle body position 1 (ETC)
MCU_USED[71] = P(71, "ADC0_P12_TPS2", "input")     # throttle body position 2 (ETC, redundant)
MCU_USED[78] = P(78, "ADC0_P13_EGT", "input")      # exhaust gas temperature
MCU_USED[79] = P(79, "ADC0_P14_ETCIFB", "input")   # ETC H-bridge current feedback
MCU_USED[139] = P(139, "E0UC22_ETC_IN1", "output") # ETC H-bridge input 1 (PWM)
MCU_USED[140] = P(140, "E0UC23_ETC_IN2", "output") # ETC H-bridge input 2 (PWM)
MCU_USED[109] = P(109, "E1UC19_BOOST", "output")   # boost control solenoid PWM
MCU_USED[103] = P(103, "E1UC20_FLEXFUEL", "input") # flex-fuel sensor frequency capture
MCU_USED[25] = P(25, "GPIO_ETC_D1", "output")      # ETC H-bridge disable 1 (active HIGH)
MCU_USED[26] = P(26, "GPIO_ETC_D2", "output")      # ETC H-bridge disable 2 (active LOW)
MCU_USED[29] = P(29, "GPIO_ETC_EN", "output")      # ETC H-bridge enable
MCU_USED[30] = P(30, "GPIO_ETC_SF_N", "input")     # ETC H-bridge fault flag

_reserved_pins = sorted(set(range(1, 145)) - set(MCU_USED.keys()))
_reserved_syms = [P(n, f"RESERVED_{n}", "no_connect") for n in _reserved_pins]
_chunk = len(_reserved_syms) // 4
MCU_RES_T, MCU_RES_B = _reserved_syms[:_chunk], _reserved_syms[_chunk:2 * _chunk]
MCU_RES_L, MCU_RES_R = _reserved_syms[2 * _chunk:3 * _chunk], _reserved_syms[3 * _chunk:]

register_symbol(
    f"{LIB}:MCU_MPC5606B", "U", "NXP MPC5606B automotive (Qorivva, Power Architecture)",
    "Package_QFP:LQFP-144_20x20mm_P0.5mm",
    {'T': [MCU_USED[p] for p in MCU_POWER_HV] + [MCU_USED[MCU_BV]] +
          [MCU_USED[p] for p in MCU_POWER_LV] +
          [MCU_USED[MCU_ADC0[0]], MCU_USED[MCU_ADC1[0]]] + MCU_RES_T,
     'B': [MCU_USED[p] for p in MCU_GND_HV] +
          [MCU_USED[p] for p in MCU_GND_LV] +
          [MCU_USED[MCU_ADC0[1]], MCU_USED[MCU_ADC1[1]]] + MCU_RES_B,
     'L': [MCU_USED[21], MCU_USED[50], MCU_USED[48],
           MCU_USED[126], MCU_USED[121], MCU_USED[127], MCU_USED[120],
           MCU_USED[106], MCU_USED[105], MCU_USED[144], MCU_USED[1]] +
          [MCU_USED[p] for p in MCU_EMIOS] +
          [MCU_USED[p] for p in MCU_DSPI_GPIO] +
          [MCU_USED[p] for p in (42, 11, 72, 75, 76, 77, 53, 12, 13,
                                  28, 27, 117, 116, 14, 15, 16, 17,
                                  63, 64, 65, 66, 128, 129, 132, 133,
                                  84, 67, 86, 88,
                                  68, 69, 70, 71, 78, 79, 139, 140,
                                  109, 103, 25, 26, 29, 30)] + MCU_RES_L,
     'R': MCU_RES_R},
    datasheet="https://www.nxp.com/products/MPC5606B")

# ---------------------------------------------------------------------------
# 2x NXP MC33810 injector/ignition driver (plan step 4)
# ---------------------------------------------------------------------------
# Real, verified 32-pin SOICW-EP pin table - NXP's own datasheet URL 404s
# and alldatasheet 403s (same access pattern as the MCU), recovered via a
# working chipdip.ru mirror (Document Number: MC33810 Rev. 11.0, 8/2014),
# extracted with pdftotext and cross-checked against both the pin diagram
# and the formal per-pin Functional Pin Description table.
#
# Real architecture (confirmed from the datasheet text, not assumed): OUT0-3
# are the chip's OWN integrated low-side injector switches (real injector
# current flows through the chip's silicon). GD0-3 are only PRE-drivers -
# real ignition current flows through an EXTERNAL IGBT the chip's GDx pin
# merely gates, which is why this design adds one external ignition IGBT
# per channel (8 total across both chips) rather than expecting the driver
# IC itself to switch coil-primary current. FBx senses that external IGBT's
# own collector (shared net with the coil-primary connection, NOT the gate)
# for spark-event detection - confirmed by the datasheet's own description
# of FB as sensing "IGBT Collector-Emitter" voltage.
# RSP/RSN feed one shared current-sense COMPARATOR per chip (only one pin
# pair per chip, not per channel) - confirmed real by the datasheet's own
# "MAXI Trip Point During Overlapping Dwell" spec, which only makes sense
# if multiple channels' emitter currents genuinely combine through one
# shared sense point. Real design: all 4 of a chip's IGBT emitters tie to
# one common node, through one external sense resistor to GND; RSP taps the
# common node, RSN taps the GND side (datasheet's own VGNDOVR spec puts RSN
# within 0.3V of the chip's own exposed-pad ground, confirming this is a
# true Kelvin-style shunt sense, not a distant/arbitrary GND point).
# CS/SCLK/SI/SO real pinout confirmed, INCLUDING the specific real fact
# that made bus-sharing between both chips safe to design: "With CS in a
# logic high state... the SO pin is tri-state" (datasheet text, page ~24) -
# same category of check that caught a real pin_to_pin bug on the JTAG
# TDO pin in step 3 (two real outputs shorted together isn't just a
# theoretical scruple - it's exactly what would have happened here if SO
# weren't confirmed tri-state-capable before sharing it).
# REAL MC33810 -> L9779WD-SPI REPLACEMENT (see ecu-firmware/l9779.h and
# the `mc33810-end-of-life` project memory): MC33810 hit Last Time Buy,
# 2027-04-30, no NXP-recommended replacement. L9779WD-SPI (ST DocID027721
# Rev 2, "production data") was chosen as the best real, currently-Active
# alternative found, with real precedent - rusEFI maintains a real KiCad
# symbol/footprint for this exact part (github.com/rusefi/kicad6-libraries),
# sourced into footprints/rusefi.pretty/ this pass and cross-checked
# pad-for-pad against the datasheet's own Table 58 mechanical data (real
# 0.65mm pitch matches exactly, real exposed pad EPAD present).
#
# All 64 real pins are listed below (Table 2, "Pins description") so the
# symbol is complete and real, even though this board's minimal-scope
# redesign only wires a subset of them (OUT1-4 injector, IGN1-4 ignition,
# IN1-4/IGNI1-4 real-time parallel control, SPI, and power) - matching
# the same "complete real pinout, selective real wiring" pattern already
# used for MC33810's own NOMI/MAXI/SPKDUR pins. Real, deliberately unused
# features (available for a later, separate decision, not exploited here):
# VRS sensor interface, CAN transceiver, K-Line, MRD, and the 4-channel
# stepper driver - see ecu-firmware/README.md's ledger for the reasoning.
#
# REAL, OPEN GAPS carried over from the redesign plan, not silently
# resolved: MC33810's RSP/RSN (current-sense) and FBx (coil/collector-
# sense) roles have NO confirmed L9779WD-SPI equivalent in the pins read
# so far - this design does NOT wire a substitute for them (see the wiring
# comments below for what that means for the per-chip sense resistor and
# each IGBT's emitter/collector nets). Same for MC33810's real DRV_OUTEN
# shared kill-switch pin - no confirmed equivalent found (possibly
# START_REACT's real STOP command bit instead of a hardware pin - not
# confirmed, see ecu-firmware/inc/ecu_pins.h's PIN_DRV_OUTEN comment).
L9779WD_PINS = {
    1: P(1, "CP", "passive"), 2: P(2, "VDD_G", "power_out"),
    3: P(3, "VDD5", "power_out"), 4: P(4, "V3V3", "power_out"),
    5: P(5, "RST", "output"), 6: P(6, "VRSP", "input"),
    7: P(7, "VRSN", "input"), 8: P(8, "OUT_VRS", "output"),
    9: P(9, "VTRK1", "power_out"), 10: P(10, "VTRK2", "power_out"),
    11: P(11, "KEY_ON", "input"), 12: P(12, "VB", "power_in"),
    13: P(13, "OUTA", "output"), 14: P(14, "IGNI4", "input"),
    15: P(15, "IGNI3", "input"), 16: P(16, "OUTB", "output"),
    17: P(17, "OUTC", "output"), 18: P(18, "IGNI2", "input"),
    19: P(19, "IGNI1", "input"), 20: P(20, "OUTD", "output"),
    21: P(21, "GND", "power_in"), 22: P(22, "IGN1", "output"),
    23: P(23, "MRD", "output"), 24: P(24, "OUT16", "output"),
    25: P(25, "OUT3", "output"), 26: P(26, "PGND1", "power_in"),
    27: P(27, "PGND2", "power_in"), 28: P(28, "OUT4", "output"),
    29: P(29, "OUT7", "output"), 30: P(30, "OUT13", "output"),
    31: P(31, "OUT14", "output"), 32: P(32, "OUT17", "output"),
    33: P(33, "IN7", "input"), 34: P(34, "IN6", "input"),
    35: P(35, "IN5", "input"), 36: P(36, "IN4", "input"),
    37: P(37, "IN3", "input"), 38: P(38, "WDA", "output"),
    39: P(39, "IN1", "input"), 40: P(40, "OUT20", "output"),
    41: P(41, "CAN_L", "passive"), 42: P(42, "CAN_H", "passive"),
    43: P(43, "CAN_RX", "output"), 44: P(44, "CAN_TX", "input"),
    45: P(45, "K_LINE", "passive"), 46: P(46, "K_RX", "output"),
    47: P(47, "K_TX", "input"), 48: P(48, "IN2", "input"),
    49: P(49, "PWM", "input"), 50: P(50, "DIN", "input"),
    51: P(51, "SCK", "input"), 52: P(52, "DO", "output"),
    53: P(53, "CS", "input"), 54: P(54, "OUT15", "output"),
    55: P(55, "OUT18", "output"), 56: P(56, "OUT6", "output"),
    57: P(57, "OUT5", "output"), 58: P(58, "PGND3", "power_in"),
    59: P(59, "PGND4", "power_in"), 60: P(60, "OUT1", "output"),
    61: P(61, "OUT2", "output"), 62: P(62, "IGN2", "output"),
    63: P(63, "IGN3", "output"), 64: P(64, "IGN4", "output"),
    # Real, additional pad beyond the 64 numbered signal pins (Table 2
    # doesn't list it as a numbered pin at all): the real rusEFI footprint
    # (footprints/rusefi.pretty/L9779WD-SPI.kicad_mod) has a 65th physical
    # pad literally named "EPAD" (9.7x9.7mm exposed thermal/ground pad,
    # centered under the package) - caught by a real DRC run (kicad-cli
    # pcb drc) after the first version of this symbol omitted it: with no
    # matching schematic pin, EPAD had no net at all and DRC flagged a
    # real shorting_items/solder_mask_bridge violation against a nearby
    # copper polygon. Given the pad's own name in the real footprint file
    # is the string "EPAD" (not a number), the pin here uses that same
    # string as its "number" - same real EDA convention MC33810's own
    # exposed pad used (an extra symbol pin beyond the real signal count),
    # just name-matched instead of real-pin-count+1 numbered, because
    # that's what the real footprint file actually calls it.
    "EPAD": P("EPAD", "EPAD", "power_in"),
}
register_symbol(
    f"{LIB}:L9779WD-SPI", "U", "ST L9779WD-SPI automotive (Multifunction Engine Management IC)",
    "rusefi:L9779WD-SPI",
    {'L': [L9779WD_PINS[51], L9779WD_PINS[53], L9779WD_PINS[50], L9779WD_PINS[52],
           L9779WD_PINS[12], L9779WD_PINS[3], L9779WD_PINS[4], L9779WD_PINS[2],
           L9779WD_PINS[1], L9779WD_PINS[11], L9779WD_PINS[5], L9779WD_PINS[9],
           L9779WD_PINS[10], L9779WD_PINS[38], L9779WD_PINS[49], L9779WD_PINS[21]],
     'T': [L9779WD_PINS[22], L9779WD_PINS[62], L9779WD_PINS[63], L9779WD_PINS[64],
           L9779WD_PINS[19], L9779WD_PINS[18], L9779WD_PINS[15], L9779WD_PINS[14],
           L9779WD_PINS[23], L9779WD_PINS[8], L9779WD_PINS[6], L9779WD_PINS[7],
           L9779WD_PINS[44], L9779WD_PINS[43], L9779WD_PINS[42], L9779WD_PINS[41]],
     'R': [L9779WD_PINS[60], L9779WD_PINS[61], L9779WD_PINS[25], L9779WD_PINS[28],
           L9779WD_PINS[39], L9779WD_PINS[48], L9779WD_PINS[37], L9779WD_PINS[36],
           L9779WD_PINS[35], L9779WD_PINS[34], L9779WD_PINS[33], L9779WD_PINS[57],
           L9779WD_PINS[56], L9779WD_PINS[29], L9779WD_PINS[30], L9779WD_PINS[31]],
     'B': [L9779WD_PINS[54], L9779WD_PINS[24], L9779WD_PINS[32], L9779WD_PINS[55],
           L9779WD_PINS[40], L9779WD_PINS[13], L9779WD_PINS[16], L9779WD_PINS[17],
           L9779WD_PINS[20], L9779WD_PINS[26], L9779WD_PINS[27], L9779WD_PINS[58],
           L9779WD_PINS[59], L9779WD_PINS[47], L9779WD_PINS[46], L9779WD_PINS[45],
           L9779WD_PINS["EPAD"]]},
    datasheet="https://www.st.com/resource/en/datasheet/l9779wd-spi.pdf")

# ON Semi FGP3040G2 (EcoSPARK II), 400V/26A TO-220 N-channel ignition IGBT -
# a real part-category match, not a generic substitution: this family is
# literally marketed for "automotive ignition coil driver circuits and
# coil-on-plug applications", with an internal clamp diode for the coil's
# own flyback voltage (no external clamp network needed, matching how FBx
# senses the collector directly for spark detection). Real TO-220 pinout
# (this family): pin1=Gate, pin2=Collector, pin3=Emitter.
register_symbol(f"{LIB}:IGBT_IGN", "Q", "TBD", "Package_TO_SOT_THT:TO-220-3_Vertical",
                {'L': [P(1, "G", "input")], 'T': [P(2, "C", "passive")],
                 'B': [P(3, "E", "passive")]})

# ---------------------------------------------------------------------------
# Placement + wiring
# ---------------------------------------------------------------------------
# Power architecture (plan step 2): ONE shared reverse-battery + load-dump
# protection stage sized for the whole board's current (unlike Manifold,
# which only ever had to protect a small logic load), split downstream into
# two independently-fused branches:
#   - LOGIC: small 2A fuse -> buck 5V -> LDO 3.3V, always live whenever the
#     board is connected to battery (not relay-gated) so USB-C/BLE
#     programming and telemetry work even with the engine/ignition off.
#   - POWER STAGE: a main relay (driven by a future MCU GPIO, step 3) gates
#     a much larger current onto two further-fused rails (injector/ignition)
#     - kept OFF unless the firmware deliberately energizes it, so injectors
#     and ignition coils can't be live just because a battery is connected
#     for programming. Real current budget (8-cylinder, from published
#     aftermarket-EFI installation guidance): injectors ~8A combined,
#     ignition coils ~14-20A combined, ECU logic ~1A - NOT simply additive
#     (not everything fires simultaneously), so branch fuses are sized with
#     real margin over typical combined draw rather than a worst-case sum:
#     F3 (injector) 15A, F4 (ignition) 25A, F1 (shared main) 30A.
RAIL = 50.0

section_text("MAIN INPUT: FUSE + SHARED REVERSE-BATTERY/LOAD-DUMP PROTECTION", 30, 28)
section_text("LOGIC SUPPLY: 5V BUCK + 3.3V LDO", 210, 28)
section_text("POWER STAGE: MAIN RELAY + FUSED INJECTOR/IGNITION RAILS", 30, 175)

# --- shared main protection ---
place(f"{LIB}:Fuse", "F1", "30A Mini blade holder (Littelfuse 297-series element, main input)", 50, RAIL,
      conn={'1': ('pwr', 'VIN'), '2': ('wire',)})
place(f"{LIB}:MOSFET_N_BIG", "Q1", "SQM40020EL_GE3 automotive (AEC-Q101), D2PAK 100A/40V", 90, RAIL,
      conn={'2': ('wire',),
            '3': ('label', 'VIN_PROT', 7.62),
            '1': ('label', 'GATE_DRV1', 7.62)})
place(f"{LIB}:IC_IdealDiode", "U2", "LM74700-Q1 (AEC-Q100 G1)", 90, 85,
      conn={'6': ('label', 'VIN_FUSED', 5.08),
            '2': ('pwr', 'GND', 7.62),
            '5': ('label', 'GATE_DRV1', 5.08), '4': ('label', 'VIN_PROT', 5.08),
            '3': ('label', 'VIN_FUSED', 5.08), '1': ('label', 'VCAP1', 5.08)})
place(f"{LIB}:C_V", "C1", "100nF charge-pump cap (AEC-Q200)", 65, 100,
      conn={'1': ('label', 'VCAP1'), '2': ('label', 'VIN_FUSED')})
place(f"{LIB}:TVS_HP", "D1", "5KP33A automotive (AEC-Q101)", 130, 70,
      conn={'1': ('label', 'VIN_PROT'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C2", "22uF X7R (AEC-Q200)", 160, 70,
      conn={'1': ('label', 'VIN_PROT'), '2': ('pwr', 'GND')})
wire_pins("F1", 2, "Q1", 2, label="VIN_FUSED")

vin_x, vin_y = pin_pos[("F1", "1")]
place_pwr_flag("VIN", vin_x, snap(vin_y - 10))
add_wire(vin_x, snap(vin_y - 10), vin_x, vin_y)
gnd_x, gnd_y = pin_pos[("D1", "2")]
place_pwr_flag("GND", gnd_x, snap(gnd_y + 10))
add_wire(gnd_x, snap(gnd_y + 10), gnd_x, gnd_y)

# --- logic branch: 2A fuse off VIN_PROT -> buck 5V -> LDO 3.3V ---
# Same real buck/LDO circuit + values Manifold verified (LMR33630-Q1 VQFN-12
# pinout, TLV733P-Q1 SOT-23-5 pinout, FB divider solved for 5.0V output),
# just fed from the shared VIN_PROT rail instead of its own dedicated
# reverse-battery-protection stage.
place(f"{LIB}:Fuse", "F2", "2A Mini blade holder (Littelfuse 297-series element, logic supply)", 210, RAIL,
      conn={'1': ('label', 'VIN_PROT', 5.08), '2': ('wire',)})
y_u3 = RAIL - off(f"{LIB}:IC_Buck", 2)[1]
place(f"{LIB}:IC_Buck", "U3", "LMR33630-Q1 (AEC-Q100 G1)", 250, y_u3,
      conn={'2': ('wire',), '10': ('label', 'VIN_LOGIC', 5.08),
            '1': ('pwr', 'GND', 5.08), '11': ('pwr', 'GND', 5.08),
            '12': ('wire',), '4': ('label', 'BOOT_CAP'),
            '7': ('label', 'FB'), '9': ('label', 'VIN_LOGIC', 5.08),
            '5': ('label', 'VCC_INT'), '8': ('nc',),
            '6': ('pwr', 'GND'), '3': ('label', 'SW')})
wire_pins("F2", 2, "U3", 2, label="VIN_LOGIC")
place(f"{LIB}:C_V", "C3", "100nF BOOT cap (AEC-Q200)", 275, 90,
      conn={'1': ('label', 'BOOT_CAP'), '2': ('label', 'SW')})
place(f"{LIB}:C_V", "C4", "1uF VCC decouple (AEC-Q200)", 298, 90,
      conn={'1': ('label', 'VCC_INT'), '2': ('pwr', 'GND')})
y_l1 = pin_pos[("U3", "12")][1] - off(f"{LIB}:L_H", 1)[1]
place(f"{LIB}:L_H", "L1", "10uH power (AEC-Q200)", 285, y_l1,
      conn={'1': ('wire',), '2': ('pwr', '+5V')})
place(f"{LIB}:C_V", "C5", "22uF X7R (AEC-Q200)", 312, 70,
      conn={'1': ('pwr', '+5V'), '2': ('pwr', 'GND')})
wire_pins("U3", 12, "L1", 1, label="SW")
# FB divider solved for 5.0V (same real formula/values as Manifold: Vout =
# 1V x (RFBT/RFBB + 1), RFBT=10k, RFBB=2.49k -> ~5.02V).
place(f"{LIB}:R_V", "R1", "10k (AEC-Q200)", 235, 128,
      conn={'1': ('pwr', '+5V'), '2': ('label', 'FB')})
place(f"{LIB}:R_V", "R2", "2.49k (AEC-Q200)", 235, 148,
      conn={'1': ('label', 'FB'), '2': ('pwr', 'GND')})
place(f"{LIB}:IC_LDO33", "U4", "TLV733P-Q1 (AEC-Q100 G1)", 355, RAIL,
      conn={'1': ('pwr', '+5V'), '5': ('pwr', '+3V3'), '2': ('pwr', 'GND'),
            '3': ('pwr', '+5V')})
place(f"{LIB}:C_V", "C6", "1uF X7R (AEC-Q200)", 385, 70,
      conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})

# --- power stage: main relay + fused injector/ignition rails ---
# Relay coil driven by a low-side MOSFET (Q2) from a future MCU GPIO
# (step 3) through a series gate resistor (R3) - Q2's gate net (RELAY_DRV)
# is already fully wired between R3 and Q2, only R3's OTHER end
# (RELAY_CTRL) is genuinely dangling this session, waiting for the MCU.
# D2 is the coil flyback diode (anode on the switched/low side, cathode on
# VIN_PROT/coil+, so it freewheels coil current when Q2 turns off).
place(f"{LIB}:MOSFET_N", "Q2", "PMV230ENEA automotive (AEC-Q101), relay driver", 70, 230,
      conn={'2': ('pwr', 'GND'),
            '3': ('label', 'RELAY_COIL_LO'),
            '1': ('label', 'RELAY_DRV', 7.62)})
place(f"{LIB}:R_V", "R3", "1k gate resistor (AEC-Q200)", 70, 205,
      conn={'1': ('label', 'RELAY_CTRL'), '2': ('label', 'RELAY_DRV')})
place(f"{LIB}:D_FLYBACK", "D2", "PMEG4010BEA automotive (AEC-Q101)", 110, 230,
      conn={'1': ('label', 'RELAY_COIL_LO'), '2': ('label', 'VIN_PROT')})
place(f"{LIB}:RELAY_ISO_MINI", "K1", "Schrack RT1-16A-FormC (16A - see registration note)", 160, 230,
      conn={'A1': ('label', 'RELAY_COIL_LO'), 'A2': ('label', 'VIN_PROT'),
            '11': ('label', 'VIN_PROT'), '14': ('label', 'VBATT_SW')})
# VBATT_INJ/VBATT_IGN are each single-occurrence (dangling) this session -
# expected: they'll pick up real loads once injectors/coils and the
# harness connectors exist (steps 4 and 9). MC33810 VPWR (step 4) will tap
# VBATT_SW directly, upstream of these branch fuses, since it's a small
# logic-level current, not a per-cylinder actuation current.
place(f"{LIB}:Fuse", "F3", "15A Mini blade holder (Littelfuse 297-series element, injector rail)", 200, 230,
      conn={'1': ('label', 'VBATT_SW', 5.08), '2': ('label', 'VBATT_INJ', 5.08)})
place(f"{LIB}:Fuse", "F4", "25A Mini blade holder (Littelfuse 297-series element, ignition rail)", 200, 260,
      conn={'1': ('label', 'VBATT_SW', 5.08), '2': ('label', 'VBATT_IGN', 5.08)})

# --- MCU core (step 3) ---
section_text("MCU CORE: NXP MPC5606B - DECOUPLING, OSCILLATOR, JTAG HEADER", 30, 310)

Y_MCU = 450
place(f"{LIB}:MCU_MPC5606B", "U1", "NXP MPC5606B automotive (Qorivva, Power Architecture)", 220, Y_MCU,
      conn={
          **{str(p): ('pwr', '+3V3', 2.54) for p in MCU_POWER_HV},
          str(MCU_BV): ('pwr', '+3V3', 2.54),
          **{str(p): ('pwr', 'GND', 2.54) for p in MCU_GND_HV},
          **{str(p): ('label', 'VDD_LV_DECOUP', 2.54) for p in MCU_POWER_LV},
          **{str(p): ('pwr', 'GND', 2.54) for p in MCU_GND_LV},
          str(MCU_ADC0[0]): ('label', 'VDD_ADC0', 2.54),
          str(MCU_ADC0[1]): ('pwr', 'GND', 2.54),
          str(MCU_ADC1[0]): ('label', 'VDD_ADC1', 2.54),
          str(MCU_ADC1[1]): ('pwr', 'GND', 2.54),
          '50': ('wire',), '48': ('wire',),
          '21': ('label', 'MCU_RESET', 5.08),
          '126': ('label', 'JTAG_TDI', 7.62), '121': ('label', 'JTAG_TDO', 7.62),
          '127': ('label', 'JTAG_TCK', 7.62), '120': ('label', 'JTAG_TMS', 7.62),
          '106': ('label', 'BOOT_FAB'), '105': ('label', 'BOOT_ABS'),
          '144': ('label', 'LIN0_TX'), '1': ('label', 'LIN0_RX'),
          # eMIOS real-time injector/ignition firing lines - finally give
          # step 4's INJn_CTRL/IGNn_CTRL stubs their real MCU driver.
          **{str(p): ('label', f'{use}_CTRL', 7.62) for p, use in MCU_EMIOS.items()},
          # DSPI_0: MCU's own SOUT (transmit) ties to the slaves' shared SI
          # net; MCU's own SIN (receive) ties to the slaves' shared SO net
          # - same wire, opposite-named ends, matches how SPI always works.
          '40': ('label', 'SPI_SCLK', 7.62),
          '44': ('label', 'SPI_SI', 7.62),
          '45': ('label', 'SPI_SO', 7.62),
          '5': ('label', 'RELAY_CTRL', 7.62),
          '6': ('label', 'DRV_OUTEN', 7.62),
          '9': ('label', 'SPI_CS_0', 7.62),
          '10': ('label', 'SPI_CS_1', 7.62),
          # step 5: crank + cam capture, 4x ADC sensor inputs, heater PWM,
          # CJ125 CS
          '42': ('label', 'CRANK_COUT', 7.62),
          '11': ('label', 'CAM_COUT', 7.62),
          '72': ('label', 'MAP_ADC', 7.62),
          '75': ('label', 'TPS_ADC', 7.62),
          '76': ('label', 'IAT_ADC', 7.62),
          '77': ('label', 'CLT_ADC', 7.62),
          '53': ('label', 'KNOCK_ADC', 7.62),
          '12': ('label', 'HTR_CTRL', 7.62),
          '13': ('label', 'SPI_CS_2', 7.62),
          # step 6: 2x real FlexCAN pairs + transceiver mode control
          '28': ('label', 'CAN0_TX', 7.62), '27': ('label', 'CAN0_RX', 7.62),
          '117': ('label', 'CAN1_TX', 7.62), '116': ('label', 'CAN1_RX', 7.62),
          '14': ('label', 'CAN0_EN', 7.62), '15': ('label', 'CAN0_STB_N', 7.62),
          '16': ('label', 'CAN1_EN', 7.62), '17': ('label', 'CAN1_STB_N', 7.62),
          # expansion: 4 analog inputs, cam2 capture, 4 PWM/timer outputs,
          # 3 plain GPIO
          '63': ('label', 'VBATT_ADC', 7.62),
          '64': ('label', 'OILP_ADC', 7.62),
          '65': ('label', 'FUELP_ADC', 7.62),
          '66': ('label', 'KNOCK2_ADC', 7.62),
          '128': ('label', 'CAM2_COUT', 7.62),
          '129': ('label', 'VVT1_CTRL', 7.62),
          '132': ('label', 'VVT2_CTRL', 7.62),
          '133': ('label', 'IDLE_CTRL', 7.62),
          '84': ('label', 'TACH_CTRL', 7.62),
          '67': ('label', 'FPUMP_CTRL', 7.62),
          '86': ('label', 'SPI_CS_3', 7.62),
          '88': ('label', 'HTR2_CTRL', 7.62),
          # expansion 2: ETC redundant sensor pair x2, EGT, ETC current
          # feedback, ETC H-bridge PWM pair, boost solenoid PWM,
          # flex-fuel capture, ETC hardware disable/enable/fault
          '68': ('label', 'APP1_ADC', 7.62),
          '69': ('label', 'APP2_ADC', 7.62),
          '70': ('label', 'TPS1_ADC', 7.62),
          '71': ('label', 'TPS2_ADC', 7.62),
          '78': ('label', 'EGT_ADC', 7.62),
          '79': ('label', 'ETC_IFB_ADC', 7.62),
          '139': ('label', 'ETC_IN1', 7.62),
          '140': ('label', 'ETC_IN2', 7.62),
          '109': ('label', 'BOOST_CTRL', 7.62),
          '103': ('label', 'FLEXFUEL_SIG', 7.62),
          '25': ('label', 'ETC_D1', 7.62),
          '26': ('label', 'ETC_D2', 7.62),
          '29': ('label', 'ETC_EN', 7.62),
          '30': ('label', 'ETC_SF_N', 7.62),
      })

# Crystal: EXTAL/XTAL wired directly (real, aligned pins), 8MHz within the
# real 4-16MHz supported range. Load caps are a typical 18pF placeholder -
# confirm against the actual chosen crystal's specified load capacitance.
y_y1 = pin_pos[("U1", "50")][1] - off(f"{LIB}:XTAL", 1)[1]
place(f"{LIB}:XTAL", "Y1", "8MHz (AEC-Q200)", 90, y_y1,
      conn={'1': ('wire',), '2': ('wire',),
            '3': ('label', 'OSC_A'), '4': ('label', 'OSC_B')})
wire_pins("Y1", 1, "U1", 50, label="EXTAL")
wire_pins("Y1", 2, "U1", 48, label="XTAL")
place(f"{LIB}:C_V", "C7", "18pF (AEC-Q200)", 70, y_y1 + 40,
      conn={'1': ('label', 'OSC_A'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C8", "18pF (AEC-Q200)", 95, y_y1 + 40,
      conn={'1': ('label', 'OSC_B'), '2': ('pwr', 'GND')})

# VDD_HV/VDD_BV decoupling: one 100nF per pin, standard practice for this
# many simultaneous I/O-domain supply pins.
for _i, _p in enumerate(MCU_POWER_HV + [MCU_BV]):
    place(f"{LIB}:C_V", f"C{9 + _i}", "100nF (AEC-Q200)", 150 + _i * 25, Y_MCU - 120,
          conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})

# VDD_LV: internally-regulated core rail, decoupling only, no external
# drive - see the big comment above the MCU registration for why.
for _i, _p in enumerate(MCU_POWER_LV):
    place(f"{LIB}:C_V", f"C{14 + _i}", "1uF (AEC-Q200)", 275 + _i * 25, Y_MCU - 120,
          conn={'1': ('label', 'VDD_LV_DECOUP'), '2': ('pwr', 'GND')})

# ADC analog supply: ferrite-isolated from +3V3, same treatment Manifold
# gave its single VDDA pin, now needed twice (two independent ADC domains).
place(f"{LIB}:L_H", "L2", "ferrite bead (AEC-Q200)", 355, Y_MCU - 120,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'VDD_ADC0')})
place(f"{LIB}:C_V", "C17", "1uF (AEC-Q200)", 380, Y_MCU - 120,
      conn={'1': ('label', 'VDD_ADC0'), '2': ('pwr', 'GND')})
place(f"{LIB}:L_H", "L3", "ferrite bead (AEC-Q200)", 355, Y_MCU - 95,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'VDD_ADC1')})
place(f"{LIB}:C_V", "C18", "1uF (AEC-Q200)", 380, Y_MCU - 95,
      conn={'1': ('label', 'VDD_ADC1'), '2': ('pwr', 'GND')})

# Reset pull-up + JTAG programming header. No TRST pin exists on this part
# (confirmed - see registration comment), so 8 signals cover VCC/GND/TCK/
# TMS/TDI/TDO/RESET/GND (2nd GND for signal-return integrity).
place(f"{LIB}:R_V", "R4", "10k pull-up (AEC-Q200)", 90, Y_MCU - 40,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'MCU_RESET')})
place(f"{LIB}:CONN_JTAG", "J1", "JTAG programming header", 90, Y_MCU + 40,
      conn={'1': ('pwr', '+3V3', 5.08), '2': ('pwr', 'GND', 5.08),
            '3': ('label', 'JTAG_TCK'), '4': ('label', 'JTAG_TMS'),
            '5': ('label', 'JTAG_TDI'), '6': ('label', 'JTAG_TDO'),
            '7': ('label', 'MCU_RESET'), '8': ('pwr', 'GND', 7.62)})

# --- 2x L9779WD-SPI injector/ignition driver + 8x ignition IGBT (step 4) ---
# Real MC33810 -> L9779WD-SPI replacement - see the symbol registration
# comment above and ecu-firmware/l9779.h for the full provenance.
# REAL, FIRMWARE-RELEVANT ARCHITECTURE NOTE (not a hardware/wiring
# change, but worth knowing when reading this schematic): unlike
# MC33810 (real OR logic - the parallel INJ/IGN_CTRL pins alone were
# already sufficient), L9779WD-SPI's OUT1-4/IGN1-4 are driven by a real
# logical AND of their own SPI enable bit and their own parallel pin
# (Sections 6.8.1/6.10.1). The parallel pins wired below still do the
# real real-time firing (eMIOS, injection.c) - firmware just also has to
# enable each channel's SPI side once at startup (l9779_init(), see
# ecu-firmware/inc/l9779.h) for these parallel pins to have any effect
# at all. No schematic-side consequence, but a real, easy-to-miss trap
# if this net topology is ever reused with a different driver IC.
section_text("INJECTOR/IGNITION DRIVERS: 2x L9779WD-SPI + 8x IGBT (PLAN STEP 4)", 30, 690)

Q_NEXT = 3   # Q1/Q2 already used (shared-protection MOSFET, relay driver)
for chip in range(2):
    u_ref = f"U{5 + chip}"
    cyl_lo = 4 * chip + 1   # chip 0 = cylinders 1-4, chip 1 = cylinders 5-8
    x0 = 100
    y0 = 700 + chip * 320

    place(f"{LIB}:L9779WD-SPI", u_ref, "ST L9779WD-SPI automotive (Multifunction Engine Management IC)", x0, y0,
          conn={
              # Real power: VB is battery-fed (real, deliberate change from
              # MC33810's VBATT_SW/relay-switched VPWR - VIN_PROT is the
              # same always-on protected rail the TJA1043Ts already use for
              # wake capability, see their own wiring comment). VDD5/V3V3/
              # VDD_G/CP are real chip-generated outputs/support pins, NOT
              # inputs to feed from the board's own +5V/+3V3 - see the
              # symbol comment above for why those aren't assumed to power
              # anything else on this board.
              '12': ('label', 'VIN_PROT', 5.08),       # VB
              '21': ('pwr', 'GND', 2.54),               # GND (stepper GND pin, real, still a valid ground ref)
              '26': ('pwr', 'GND', 5.08),               # PGND1
              '27': ('pwr', 'GND', 5.08),               # PGND2
              '58': ('pwr', 'GND', 5.08),               # PGND3
              '59': ('pwr', 'GND', 5.08),               # PGND4
              'EPAD': ('pwr', 'GND', 5.08),             # real exposed thermal pad - see symbol comment
              # REAL, OPEN GAP - not guessed: CP (charge pump) and VDD_G
              # need real external support components per the datasheet's
              # own application circuit (Figure 3), not read this pass.
              # Left unconnected rather than fabricating a value; the
              # charge pump (and therefore IGN1-4's own real drive
              # capability) will NOT work correctly until this is resolved
              # - a real, loud TODO, not cosmetic.
              '1': ('nc',), '2': ('nc',), '3': ('nc',), '4': ('nc',),
              '53': ('label', f'SPI_CS_{chip}', 7.62),  # CS - per-chip SPI select
              '51': ('label', 'SPI_SCLK', 7.62),        # SCK - shared SPI bus (both chips)
              '50': ('label', 'SPI_SI', 7.62),          # DIN
              '52': ('label', 'SPI_SO', 7.62),          # DO - real MISO, tri-state when CS high
              '60': ('label', f'INJ{cyl_lo}_LO'),       # OUT1-4: real injector LSD outputs
              '61': ('label', f'INJ{cyl_lo + 1}_LO'),
              '25': ('label', f'INJ{cyl_lo + 2}_LO'),
              '28': ('label', f'INJ{cyl_lo + 3}_LO'),
              # RESOLVED (a later pass): IGN1-4 is a real, confirmed IGBT
              # gate pre-driver - the datasheet's own Section 6.10.1 opens
              # with "The 4 ignition pre-drivers are push-pull output with
              # diagnosis and over current protection circuit. They can
              # drive IGBT Darlington transistors." Real, direct match for
              # MC33810's GDx role, not a name-carried-over guess.
              '22': ('label', f'IGN{cyl_lo}_GATE'),     # IGN1-4: ignition pre-driver outputs
              '62': ('label', f'IGN{cyl_lo + 1}_GATE'),
              '63': ('label', f'IGN{cyl_lo + 2}_GATE'),
              '64': ('label', f'IGN{cyl_lo + 3}_GATE'),
              '39': ('label', f'INJ{cyl_lo}_CTRL'),     # IN1-4: real-time parallel firing
              '48': ('label', f'INJ{cyl_lo + 1}_CTRL'),
              '37': ('label', f'INJ{cyl_lo + 2}_CTRL'),
              '36': ('label', f'INJ{cyl_lo + 3}_CTRL'),
              '19': ('label', f'IGN{cyl_lo}_CTRL'),     # IGNI1-4: real-time parallel firing
              '18': ('label', f'IGN{cyl_lo + 1}_CTRL'),
              '15': ('label', f'IGN{cyl_lo + 2}_CTRL'),
              '14': ('label', f'IGN{cyl_lo + 3}_CTRL'),
              # Real, deliberately unused this pass (see symbol comment
              # above): VRS, CAN, K-Line, MRD, stepper, and the relay/
              # heater/low-current OUT channels this board doesn't wire.
              '5': ('nc',), '6': ('nc',), '7': ('nc',), '8': ('nc',),
              '9': ('nc',), '10': ('nc',), '11': ('nc',), '13': ('nc',),
              '16': ('nc',), '17': ('nc',), '20': ('nc',), '23': ('nc',),
              '24': ('nc',), '29': ('nc',), '30': ('nc',), '31': ('nc',),
              '32': ('nc',), '33': ('nc',), '34': ('nc',), '35': ('nc',),
              '38': ('nc',), '40': ('nc',), '41': ('nc',), '42': ('nc',),
              '43': ('nc',), '44': ('nc',), '45': ('nc',), '46': ('nc',),
              '47': ('nc',), '49': ('nc',), '54': ('nc',), '55': ('nc',),
              '56': ('nc',), '57': ('nc',),
          })

    # 4 real ignition IGBTs per chip: gate <- IGNn_GATE (see the real,
    # open gap flagged above). REAL, OPEN GAP carried from the redesign
    # plan: MC33810's FBx (collector/coil sense) and RSP/RSN (current
    # sense) roles have no confirmed L9779WD-SPI equivalent, so this
    # design does NOT feed the coil-primary connection back into the chip
    # for spark detection, and the emitter goes straight to GND instead of
    # through a shared sense resistor (removed below - it would have
    # nothing real to connect to). The coil-primary net keeps the
    # `IGNn_COIL` name for continuity with the rest of the board's wiring,
    # it's just not sensed by the driver chip anymore.
    for ch_off in range(4):
        cyl = cyl_lo + ch_off
        q_ref = f"Q{Q_NEXT}"
        Q_NEXT += 1
        place(f"{LIB}:IGBT_IGN", q_ref, "FGP3040G2 automotive ignition IGBT (TO-220)",
              x0 + 260 + ch_off * 45, y0,
              conn={'1': ('label', f'IGN{cyl}_GATE'),
                    '2': ('label', f'IGN{cyl}_COIL'),
                    '3': ('pwr', 'GND')})

    # Real VB decoupling (value carried over from MC33810's own VPWR
    # decouple as a conservative placeholder - the real L9779WD-SPI
    # application circuit, Figure 3, hasn't been read yet to confirm a
    # real value for this specific chip).
    place(f"{LIB}:C_V", f"C{19 + chip * 2}", "1uF VB decouple (AEC-Q200) - real value TBD, see comment", x0 - 30, y0 - 30,
          conn={'1': ('label', 'VIN_PROT'), '2': ('pwr', 'GND')})

# ---------------------------------------------------------------------------
# Sensor front end (plan step 5): 2x MAX9924 crank/cam VR interface,
# Bosch CJ125 wideband O2 controller + heater MOSFET, MAP/TPS/IAT/CLT
# analog inputs, knock sensor op-amp front end.
# ---------------------------------------------------------------------------
# MAX9924 (Maxim/ADI, 10-pin uMAX, AEC-Q100) - real pin table verified via
# a chipdip.ru mirror of the real MAX9924-MAX9927 datasheet (page 7, "Pin
# Description" table - text-extracted cleanly, small table, no image
# cross-check needed unlike the MCU/MC33810 tables). COUT is open-drain,
# needs an external pull-up - pulled to +3V3 (not VCC) so the MCU eMIOS
# input-capture pin sees clean 3.3V logic regardless of VCC's chosen
# voltage, the same open-drain-to-a-different-rail level-shift trick used
# nowhere else in this project yet but standard real practice. ZERO_EN
# left NC (real internal pull-up to VCC = its own documented default).
# INT_THRS and EXT are tied to a definite level (not left floating, bad
# practice for a CMOS control input) as a reasonable default - CONFIRM
# against the full electrical-characteristics/mode-table section (not
# fully extracted this session) before fab, same "verify before trusting"
# flag already carried for several other parts.
register_symbol(f"{LIB}:MAX9924", "U", "TBD", "Package_SO:MSOP-10_3x3mm_P0.5mm",
                {'L': [P(1, "IN+", "input"), P(2, "IN-", "input"), P(5, "GND", "power_in")],
                 'R': [P(10, "VCC", "power_in"), P(7, "COUT", "output"), P(4, "BIAS", "passive")],
                 'T': [P(6, "ZERO_EN", "input"), P(9, "INT_THRS", "input")],
                 'B': [P(3, "NC", "no_connect"), P(8, "EXT", "input")]},
                datasheet="https://www.analog.com/en/products/max9924.html")

# Bosch CJ125 (24-pin SOIC-24W, pairs with LSU4.x wideband O2 sensor,
# SPI-controlled) - real pin table verified via a Wayback Machine archive
# of a TME.eu mirror (live tme.eu URL 403'd, same pattern as several other
# distributor sites this project has hit), page 3 "PIN configuration",
# image-rendered and cross-checked at 4x. Real application-circuit
# TOPOLOGY (which pin needs what kind of external network, not exact
# component VALUES) came from an earlier text extraction of the same
# document's block-diagram description page: decoupling caps on VCC/UB,
# a filter cap on CF, a stabilizing cap on UA, a shunt resistor between
# IA/IP for pump current sense, compensation resistors IA-UP and UP-UN, a
# stabilizing cap on UR, an RM-resistor/CM-capacitor Ri-measurement timing
# network, a cap-based RST stabilization network, and a resistor between
# DIAHD and the external heater MOSFET's drain for diagnostics. EXACT
# component VALUES for this analog network are NOT independently verified
# against Bosch's own full application note this session (only the short
# "Product Information" brief was fetched, not the complete app-circuit
# doc with real component values) - placeholder values below are typical/
# commonly-cited figures, flagged clearly, not fabricated-and-passed-off
# as verified. Pin 16 (/SS) and pin 8 (/RST) are real pins the original
# pin-table request didn't ask for but the research found anyway; pins
# 21-23 (UA/CF/RF) were reported in that exact order without individual
# confirmation - inferred, not independently re-verified pin-by-pin.
# SO shares the SPI bus with both MC33810s (SPI_SCLK/SI/SO, separate
# CS_2) - checked before wiring, same discipline as the MC33810 pair, but
# with slightly LOWER confidence: no single sentence as explicit as
# MC33810's "the SO pin is tri-state" was found, but CJ125 has a real,
# dedicated /SS pin ("Slave select (SPI, from uC)") AND its own SPI
# Read-Access timing-diagram legend explicitly documents a tristate ('Z')
# output state - strong real evidence for standard /SS-gated tri-stating
# (near-universal for any hardware SPI slave with a real /SS pin), just
# not as airtight as the MC33810 case - flagged honestly, not claimed
# with equal certainty.
# CJ_VM (virtual-ground reference output) and CJ_RS (Ri-calibration pin)
# are wired to the chip but genuinely left unconnected further - CJ_VM
# has no other consumer designed yet (a real future-use pin, not a
# mistake), and CJ_RS's real external network wasn't in the topology
# description this session's research extracted (unlike RM/CM's network,
# which was explicit) - left honestly unconnected rather than inventing
# a component with no real basis.
register_symbol(f"{LIB}:CJ125", "U", "TBD", "Package_SO:SOIC-24W_7.5x15.4mm_P1.27mm",
                {'T': [P(1, "UB", "power_in"), P(17, "VCC", "power_in")],
                 'B': [P(24, "GND", "power_in"), P(5, "OSZ", "passive"),
                       P(9, "RS", "passive"), P(10, "RM", "output"),
                       P(11, "CM", "input"), P(12, "UR", "output"),
                       P(21, "UA", "passive"), P(22, "CF", "passive"),
                       P(23, "RF", "passive")],
                 'L': [P(2, "UN", "passive"), P(3, "IP", "passive"),
                       P(4, "IA", "passive"), P(19, "US", "output"),
                       P(20, "UP", "output"), P(18, "VM", "output")],
                 'R': [P(13, "SCK", "input"), P(14, "SO", "output"),
                       P(15, "SI", "input"), P(16, "SS_N", "input"),
                       P(8, "RST_N", "input"), P(6, "DIAHG", "input"),
                       P(7, "DIAHD", "input")]},
                datasheet="https://www.bosch-semiconductors.com/automotive-system-ics/engine-management-systems/cj125/")

# TI TLV2372-Q1 dual op-amp (AEC-Q100 Grade 1, SOIC-8) - real pinout read
# directly off TI's own datasheet pin diagram (clean, unambiguous text
# extraction, no image cross-check needed): 1=OUT1, 2=IN1-, 3=IN1+,
# 4=GND, 5=IN2+, 6=IN2-, 7=OUT2, 8=VDD. Used for the knock-sensor piezo
# front end: channel 1 = AC-coupled gain stage, channel 2 = buffered
# mid-supply bias reference (a spare-channel use, not filler - a buffered
# reference is real good practice for a single-supply analog front end).
register_symbol(f"{LIB}:TLV2372", "U", "TBD", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                {'L': [P(2, "IN1-", "input"), P(3, "IN1+", "input"),
                       P(5, "IN2+", "input"), P(6, "IN2-", "input")],
                 'R': [P(1, "OUT1", "output"), P(7, "OUT2", "output")],
                 'T': [P(8, "VDD", "power_in")], 'B': [P(4, "GND", "power_in")]},
                datasheet="https://www.ti.com/lit/ds/symlink/tlv2372.pdf")

section_text("SENSOR FRONT END: CRANK/CAM + WIDEBAND O2 + MAP/TPS/IAT/CLT/KNOCK (STEP 5)", 30, 1350)

# --- crank + cam VR sensor interface (2x MAX9924) ---
# BIAS network (resistor divider + bypass cap, per the datasheet's own pin
# description) and sensor-input series resistors are typical placeholder
# values pending the full app-circuit page - see registration comment.
for idx, name in enumerate(("CRANK", "CAM")):
    x0 = 100 + idx * 200
    y0 = 1400
    u_ref = f"U{7 + idx}"
    place(f"{LIB}:MAX9924", u_ref, f"MAX9924 automotive (AEC-Q100) - {name} VR interface", x0, y0,
          conn={'10': ('pwr', '+5V', 5.08), '5': ('pwr', 'GND', 5.08),
                '1': ('label', f'{name}_VR_HI'), '2': ('label', f'{name}_VR_LO'),
                '4': ('label', f'{name}_BIAS'),
                '6': ('nc',), '9': ('pwr', 'GND', 5.08), '8': ('pwr', 'GND', 5.08),
                '7': ('label', f'{name}_COUT', 5.08)})
    place(f"{LIB}:R_V", f"R{9 + idx * 2}", "10k BIAS divider hi (AEC-Q200, typical - confirm)",
          x0 - 30, y0 - 40,
          conn={'1': ('pwr', '+5V'), '2': ('label', f'{name}_BIAS')})
    place(f"{LIB}:R_V", f"R{10 + idx * 2}", "10k BIAS divider lo (AEC-Q200, typical - confirm)",
          x0 - 30, y0 - 15,
          conn={'1': ('label', f'{name}_BIAS'), '2': ('pwr', 'GND')})
    place(f"{LIB}:C_V", f"C{23 + idx}", "100nF BIAS bypass (AEC-Q200, typical - confirm)",
          x0 - 55, y0 - 25,
          conn={'1': ('label', f'{name}_BIAS'), '2': ('pwr', 'GND')})
    # Open-drain COUT pulled to +3V3 (not VCC=+5V) - clean 3.3V logic into
    # the MCU eMIOS input-capture pin regardless of the sensor-side supply.
    place(f"{LIB}:R_V", f"R{31 + idx}", "4.7k COUT pull-up to +3V3 (AEC-Q200)",
          x0 + 60, y0 - 40,
          conn={'1': ('pwr', '+3V3'), '2': ('label', f'{name}_COUT')})

# eMIOS input-capture pins (pin 42 = crank, pin 2 = cam) finally give
# these labels a real MCU connection - wired directly in U1's own conn
# dict below via the same net names.

# --- wideband O2 (CJ125 + LSU4.x sensor + external heater MOSFET) ---
place(f"{LIB}:CJ125", "U9", "Bosch CJ125 wideband O2 controller (SOIC-24W)", 500, 1400,
      conn={
          '1': ('label', 'VBATT_SW', 5.08),    # UB: 14V heater/battery supply
          '17': ('pwr', '+5V', 5.08),          # VCC: 5V logic supply
          '24': ('pwr', 'GND', 2.54),
          '5': ('label', 'CJ_OSZ'),            # oscillator cap node
          # RS ties DIRECTLY to the real shared Nernst-cell node (bare
          # wire on Bosch's own application-circuit diagram, no
          # component) - was its own dangling 'CJ_RS' label with no
          # other connection anywhere, same real bug class as VM/CJ_VM
          # above.
          '9': ('label', 'O2_UN'),
          '10': ('label', 'CJ_RM'),            # Ri-measurement timing R
          '11': ('label', 'CJ_CM'),            # Ri-measurement timing C node
          '12': ('label', 'CJ_UR'),            # analog Ri-signal output
          '21': ('label', 'CJ_UA'),            # external filter node
          '22': ('label', 'CJ_CF'),            # filter cap node
          '23': ('label', 'CJ_RF'),            # filter resistor node
          # CJ125's own UN pin reaches the real shared Nernst-cell node
          # (O2_UN) through a series resistor, not directly - real Bosch
          # application-circuit topology, see R45 below.
          '2': ('label', 'CJ_UN'),
          '3': ('label', 'O2_IP'),             # pump current - REAL sensor wire
          '4': ('label', 'O2_IA'),             # trim/calibration - REAL sensor wire
          # US and UP are INTERNAL CJ125 bias nodes, NOT sensor wires -
          # see the O2_VM note below for the real bug this corrected.
          '19': ('label', 'O2_US'),            # ties to the UP node via 4k7
          '20': ('label', 'O2_UP'),            # pump-cell drive bias node
          # VM = the LSU's own "virtual ground" common tap (the sensor
          # wire between its Nernst and pump cells) - a REAL, SEPARATE
          # node from UP, on its own harness pin.
          #
          # REAL BUG, mine, caught only on a second, much-higher-
          # magnification read of Bosch's own application circuit: an
          # earlier pass this session merged VM into the UP net, on the
          # belief they were tied by a bare wire. At 1600 DPI the
          # junction dots are unambiguous - the UP/US column CROSSES the
          # VM wire with NO dot, and only the 82.5R/301R bridges VM to
          # the Nernst node. Shorting VM to UP would have tied the
          # CJ125's pump-cell drive output straight onto its own
          # virtual-ground reference. Fixed here, and the same re-read
          # corrected the 2.2nF (goes to GND, not to VM) and identified
          # which resistor the "100k" label actually belongs to.
          '18': ('label', 'O2_VM'),
          '13': ('label', 'SPI_SCLK', 7.62),   # shares the MC33810s' SPI bus
          '14': ('label', 'SPI_SO', 7.62),
          '15': ('label', 'SPI_SI', 7.62),
          '16': ('label', 'SPI_CS_2', 7.62),   # per-chip select, real MCU pin below
          '8': ('label', 'CJ_RST_N', 5.08),
          '6': ('label', 'HTR_GATE'),          # senses external heater MOSFET gate directly
          '7': ('label', 'HTR_DRAIN_SENSE'),   # senses drain THROUGH R21 (real topology
      })                                        # per datasheet: "Resistor between DIAHD
                                                 # and Drain of the external heater")
# Decoupling + filter network - REAL values, pulled directly from
# Bosch's own "CJ125_Product_Info_2006-04.doc" datasheet (Rev., via a
# TME.eu mirror), page 2, "Application circuit (only proposal!)" -
# rendered to an image and read directly (pypdf's text extraction
# mangles this diagram beyond use, same "render it, don't trust
# raw-text-extraction of a figure" lesson as the MCU's own pinout
# table). Replaces the earlier session's "typical, unconfirmed" guesses
# - several turned out to be real, genuine mismatches, not just
# unverified placeholders (noted per-component below), so this is a
# correctness pass, not just a confidence upgrade.
place(f"{LIB}:C_V", "C25", "33nF UB decouple (AEC-Q200) - real Bosch app-note value,",
      470, 1370, conn={'1': ('label', 'VBATT_SW'), '2': ('pwr', 'GND')})
# was guessed as 100nF - corrected.
place(f"{LIB}:C_V", "C26", "33nF VCC decouple (AEC-Q200) - real Bosch app-note value,",
      495, 1370, conn={'1': ('pwr', '+5V'), '2': ('pwr', 'GND')})
# was guessed as 100nF - corrected.
# REAL, significant correction: OSZ needs a 10k RESISTOR to GND (sets
# the internal oscillator's reference current), not a capacitor at all
# - the earlier session's "100nF OSZ cap" guessed the wrong COMPONENT
# TYPE, not just the wrong value. Confirmed twice on the real datasheet:
# labeled "10kΩ" directly on OSZ in the block/functional diagram (page
# 2 top) AND drawn as a resistor symbol (not a cap) in the application
# circuit.
place(f"{LIB}:R_V", "R44", "10k OSZ oscillator reference (AEC-Q200) - real Bosch",
      440, 1440, conn={'1': ('label', 'CJ_OSZ'), '2': ('pwr', 'GND')})
# app-note value; was guessed as a 100nF CAPACITOR, wrong component type.
place(f"{LIB}:C_V", "C28", "33nF UA stabilize (AEC-Q200) - real Bosch app-note value,",
      440, 1460, conn={'1': ('label', 'CJ_UA'), '2': ('pwr', 'GND')})
# was guessed as 100nF - corrected.
# RF/CF real topology: RF--100k--CF (a shared node, NOT two separately
# grounded legs like the earlier guess had), then 100nF from that node
# to GND.
place(f"{LIB}:R_V", "R15", "100k RF filter (AEC-Q200) - real Bosch app-note value/",
      440, 1520, conn={'1': ('label', 'CJ_RF'), '2': ('label', 'CJ_CF')})
# topology (was guessed as 1k, wired straight to GND instead of to CF).
place(f"{LIB}:C_V", "C29", "100nF CF filter (AEC-Q200) - real Bosch app-note value,",
      440, 1480, conn={'1': ('label', 'CJ_CF'), '2': ('pwr', 'GND')})
# was guessed as 1nF - corrected.
place(f"{LIB}:C_V", "C30", "33nF UR stabilize (AEC-Q200) - real Bosch app-note value,",
      440, 1500, conn={'1': ('label', 'CJ_UR'), '2': ('pwr', 'GND')})
# was guessed as 1uF - corrected.
place(f"{LIB}:R_V", "R16", "61R9 IA-IP pump current shunt (AEC-Q200) - CONFIRMED",
      440, 1540, conn={'1': ('label', 'O2_IA'), '2': ('label', 'O2_IP')})
# against the real Bosch app note this session (61.9R exactly): the
# earlier session's "widely-cited, not independently re-confirmed"
# value turned out to be real and correct.
# RM/CM/RS real topology: all three bridge to the SAME real shared
# node (the Nernst cell's own tap, O2_UN) - RS by a bare wire (fixed
# above, at registration), RM through this resistor, CM through this
# cap. The earlier session instead wired RM-to-CM directly (a simple
# 2-part low-pass with no path to the actual Nernst node at all) - a
# real topology bug, not just an unconfirmed value.
place(f"{LIB}:R_V", "R17", "10k RM (AEC-Q200) - real Bosch app-note value for LSU4.2",
      440, 1560, conn={'1': ('label', 'CJ_RM'), '2': ('label', 'O2_UN')})
# (31.6k for LSU4.9 - this design targets LSU4.2, matching R44/the rest
# of this network's LSU4.2 real values).
place(f"{LIB}:C_V", "C31", "100nF CM (AEC-Q200) - real Bosch app-note value/topology",
      465, 1560, conn={'1': ('label', 'CJ_CM'), '2': ('label', 'O2_UN')})
# (was guessed as 1nF, wired straight to GND instead of to O2_UN).
place(f"{LIB}:R_V", "R18", "10k RST_N pull-up (AEC-Q200)", 440, 1580,
      conn={'1': ('pwr', '+5V'), '2': ('label', 'CJ_RST_N')})
place(f"{LIB}:C_V", "C32", "100nF RST_N stabilize (AEC-Q200, typical - confirm)", 465, 1580,
      conn={'1': ('label', 'CJ_RST_N'), '2': ('pwr', 'GND')})
# Real Bosch reference drives /RST from an external supervisor IC's own
# filtered reset output (just 1nF there) - this design has no such
# supervisor, so R18/C32 stay our own simple pull-up + stabilize cap
# rather than force-matching a value derived from a different topology.
#
# UN/VM-node real topology (the actual reason R19/R20 existed, now
# fixed to match instead of approximating): CJ125's own UN pin reaches
# O2_UN through R44's twin, a 100k series resistor (R45 below); O2_UN
# bridges to the pump-cell node (O2_UP, which VM now shares directly -
# see registration comment) via BOTH a 2.2nF cap (C56) AND an 82.5R
# resistor (R20, repurposed) IN PARALLEL; US reaches O2_UP through a
# 4k7 (R46); and O2_UP bridges to O2_IP through 470k (R19, repurposed).
# The earlier session's R19 (1k, IA-UP) and R20 (100k, UP-UN) were both
# real topology guesses that didn't match this - not just unconfirmed
# values.
# UN pin's own series resistor + filter cap to GND. Bosch's application
# circuit genuinely draws BOTH of these UNLABELED (every other passive
# on that diagram carries a value) - so unlike the rest of this network
# these two are real *topology* from the app note with chosen values,
# honestly flagged rather than presented as verified. 10k/1nF is a
# conservative anti-alias/protection filter for a high-impedance Nernst
# sense input; confirm against a full application note before fab.
place(f"{LIB}:R_V", "R45", "10k UN series R (AEC-Q200) - real Bosch app-note TOPOLOGY,",
      490, 1560, conn={'1': ('label', 'CJ_UN'), '2': ('label', 'O2_UN')})
# value UNLABELED in Bosch's own diagram - chosen, not verified.
place(f"{LIB}:C_V", "C57", "1nF UN filter cap (AEC-Q200) - real Bosch app-note TOPOLOGY,",
      515, 1560, conn={'1': ('label', 'CJ_UN'), '2': ('pwr', 'GND')})
# value UNLABELED in Bosch's own diagram - chosen, not verified.
place(f"{LIB}:R_V", "R19", "470k UP-IP node bridge (AEC-Q200) - real Bosch app-note",
      440, 1600, conn={'1': ('label', 'O2_UP'), '2': ('label', 'O2_IP')})
# value/topology (was 1k between IA-UP, the wrong pair of nodes AND
# wrong value).
# REAL 100k: bridges the Nernst node DOWN to the UP bias node. The
# rotated "100k" label on Bosch's diagram sits beside the VERTICAL
# resistor (same convention as its vertical "82.5/301" vs. its
# horizontal "4k7"), so it belongs to THIS one - not to the UN series
# resistor above, which an earlier lower-magnification read of the same
# figure had misattributed.
place(f"{LIB}:R_V", "R47", "100k Nernst-UP node bridge (AEC-Q200) - real Bosch app-note",
      490, 1580, conn={'1': ('label', 'O2_UN'), '2': ('label', 'O2_UP')})
place(f"{LIB}:R_V", "R20", "82R5 Nernst-VM node bridge (AEC-Q200) - real Bosch app-note",
      465, 1600, conn={'1': ('label', 'O2_VM'), '2': ('label', 'O2_UN')})
# value for LSU4.2 (301R for LSU4.9) - was guessed as 100k, and (before
# the high-magnification re-read) wired to the UP node instead of VM.
place(f"{LIB}:C_V", "C56", "2.2nF Nernst node filter (AEC-Q200) - real Bosch app-note",
      490, 1600, conn={'1': ('label', 'O2_UN'), '2': ('pwr', 'GND')})
# value/topology: goes to GND (its lower plate is a real ground symbol,
# not a wire down to the VM node - confirmed at 1400 DPI after an
# earlier read of the same figure got this wrong).
place(f"{LIB}:R_V", "R46", "4k7 US series R to UP node (AEC-Q200) - real Bosch",
      440, 1620, conn={'1': ('label', 'O2_US'), '2': ('label', 'O2_UP')})
# app-note value/topology - previously missing entirely (O2_US went
# straight to the sensor connector with no on-board reference network).

# External heater MOSFET - CJ125's DIAHG/DIAHD only DIAGNOSE an external
# transistor, they don't drive one; the real PWM heater control comes
# from the MCU directly, through a series gate resistor (same pattern as
# step 2's relay driver Q2/R3). Reuses the same MOSFET_N footprint/pinout
# as Q2 with a bigger real part (PMV37ENEA, already used+verified in
# Manifold for a multi-amp load) since the LSU4.x ceramic heater draws
# multiple amps, more than the small relay-coil load Q2's PMV230ENEA was
# sized for.
place(f"{LIB}:MOSFET_N", "Q11", "PMV37ENEA automotive (AEC-Q101), O2 heater driver", 560, 1400,
      conn={'2': ('pwr', 'GND'),
            '3': ('label', 'HTR_DRAIN'),
            '1': ('label', 'HTR_GATE', 7.62)})
place(f"{LIB}:R_V", "R21", "1k DIAHD sense (AEC-Q200, typical - confirm)", 590, 1400,
      conn={'1': ('label', 'HTR_DRAIN'), '2': ('label', 'HTR_DRAIN_SENSE')})
place(f"{LIB}:R_V", "R33", "1k heater gate resistor (AEC-Q200)", 560, 1370,
      conn={'1': ('label', 'HTR_CTRL'), '2': ('label', 'HTR_GATE')})

# --- MAP / TPS analog inputs (direct to ADC, standard RC anti-alias) ---
# Real automotive practice (confirmed via published EFI wiring-harness
# guidance): ~1k series + 10-100nF to GND, giving a 1.6-16kHz cutoff -
# limits RF pickup on the sensor harness and protects the ADC pin if the
# sensor lead is disconnected. Sensors themselves are external (ratio-
# metric, 5V-referenced) - connect via the harness connector in step 9;
# MAP_SIG/TPS_SIG are the sensor-side stubs.
place(f"{LIB}:R_V", "R22", "1k MAP RC filter (AEC-Q200)", 700, 1400,
      conn={'1': ('label', 'MAP_SIG'), '2': ('label', 'MAP_ADC')})
place(f"{LIB}:C_V", "C33", "22nF MAP RC filter (AEC-Q200)", 700, 1420,
      conn={'1': ('label', 'MAP_ADC'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R23", "1k TPS RC filter (AEC-Q200)", 730, 1400,
      conn={'1': ('label', 'TPS_SIG'), '2': ('label', 'TPS_ADC')})
place(f"{LIB}:C_V", "C34", "22nF TPS RC filter (AEC-Q200)", 730, 1420,
      conn={'1': ('label', 'TPS_ADC'), '2': ('pwr', 'GND')})

# --- IAT / CLT analog inputs (NTC thermistor pull-up dividers) ---
# Sensor's own NTC element is external (via harness connector, step 9);
# this board provides the pull-up half of the divider only.
#
# REAL BUG FOUND AND FIXED (both dividers): this MCU has no separate
# VRH/VRL ADC reference pins - the ADC domain's real reference IS
# VDD_HV_ADC0/1, which this board runs at 3.3V (see the "Power domains"
# note above VDD_HV_ADC0/1's registration). The original pull-up here
# went to +5V, which for a cold sensor (high NTC resistance, most of the
# rail dropped across the sensor rather than the pull-up) would pull the
# ADC input node close to the full 5V rail - a real over-voltage on a
# 3.3V-domain pin. Both R24/R25 now pull up to +3V3, matching the real
# ADC reference, not the 5V rail MAP/TPS's own sensors happen to run on.
#
# CLT (R25/C36): real part swap, not a placeholder tune. This board now
# uses the DIYAutoTune "GM Closed Element CLT/Oil Temperature Sensor"
# (https://diyautotune.com/products/clt-sensor) - a real, closed-element,
# 3/8" NPT, 2-wire GM-style resistive sending unit, the exact same real
# part/curve as the sibling thermo-pcb project's own engine-temperature
# sensor (this is genuinely what production automotive coolant senders
# are - a resistive sending unit, not a generic/unspecified NTC). Real
# published curve (manufacturer's own page, only 3 points exist):
# -40F=100700ohm, 86F=2238ohm, 210.2F=177ohm. R25=1.00k (E96) is not a
# generic "typical" pull-up - it's the exact same real, deliberate value
# thermo-pcb's own R12 uses for this exact sensor, sized to center ADC
# resolution on the sensor's real 86-210.2F ENGINE-OPERATING range
# (2238-177 ohm) rather than the -40F cold-start extreme (100700 ohm),
# since the engine spends effectively all its running life in the
# former. C36 bumped 10nF->100nF, again matching thermo-pcb's own C24
# for this identical circuit. Real firmware-side conversion (raw ADC ->
# ohms -> degrees F, piecewise-Beta lookup table derived from these
# exact 3 real points) lives in ecu-firmware/inc/clt_sensor.h - not a
# TODO, already implemented.
#
# IAT (R24/C35): real part swap too, same session, same reasoning as
# CLT above. This board now uses DIYAutoTune's "GM Open Element IAT
# Temperature Sensor" (https://diyautotune.com/products/iat-sensor,
# cross-checked against a second, independent DIYAutoTune URL for the
# same real product - both fetched live this session, matching). REAL,
# HONEST DISCREPANCY FOUND AND RESOLVED, not glossed over: this page's
# own published 3-point curve is -40F=100700ohm/87F=2238ohm/146F=177ohm
# - the SAME two resistance values (100700/2238) CLT's own page
# publishes at (-40F/86F), but the THIRD point's TEMPERATURE differs
# (146F here vs CLT's 210.2F) for the SAME 177ohm resistance reading,
# which can't both be true of two genuinely different real R-T curves.
# Real evidence this is a copy-paste artifact on DIYAutoTune's own IAT
# page, not two authentically different sensors: (1) the IAT page's own
# product description text contains a leftover sentence calling it a
# "closed-element sensor" despite the product being titled/featured as
# open-element - direct evidence of copied content from the CLT page;
# (2) taking 146F at face value implies a per-segment NTC Beta constant
# more than 2x CLT's own (~7900K vs ~3800K for the -40..87F segment) -
# physically implausible for two segments of one real thermistor, versus
# CLT's own internally-consistent ~8% segment-to-segment Beta spread.
# Real conclusion: this is genuinely the same underlying GM-pattern
# thermistor element as CLT (same real resistance values at the same
# two lower anchor temperatures), just a different physical package
# (open element for air vs. closed/NPT for liquid) - so this board's IAT
# firmware conversion (ecu-firmware/inc/iat_sensor.h) reuses CLT's own
# already-cross-checked -40F/86F/210.2F curve rather than the IAT page's
# likely-erroneous 146F figure. DIYAutoTune's own 146F IS kept as a real,
# separate, honestly-documented fact though - their real stated MAX
# RATED OPERATING TEMP for the open-element package specifically
# (plausibly a genuine mechanical/thermal limit of that housing, not a
# curve error) - noted in iat_sensor.h, not used as a second clamp,
# since real intake air temperatures essentially never approach the
# curve's own high end in practice anyway.
#
# R24=4.22k (E96) is deliberately NOT the same 1.00k as CLT's R25: IAT
# genuinely swings across nearly its FULL real range in normal use
# (ambient cold-soak to hot under-hood/boost air), unlike coolant (which
# is thermostatically regulated to a narrow band once warm) - so R24 is
# sized via the standard geometric-mean rule (sqrt(R_min * R_max) =
# sqrt(177 * 100700) ~= 4222 ohm) to maximize ADC resolution across the
# sensor's WHOLE calibrated span, not centered on one narrow sub-range
# the way CLT's R25 deliberately is. C35 bumped 10nF->100nF to match
# CLT's C36 (same real smoothing-cap reasoning, larger pull-up here
# doesn't change the argument for it).
place(f"{LIB}:R_V", "R24", "4.22k IAT pull-up (AEC-Q200) - real GM-style open-element sensor, geometric-mean sizing across its full range", 760, 1400,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'IAT_ADC')})
place(f"{LIB}:C_V", "C35", "100nF IAT filter (AEC-Q200) - matches CLT's C36", 760, 1420,
      conn={'1': ('label', 'IAT_ADC'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R25", "1.00k CLT pull-up (AEC-Q200) - real GM-style sending unit, same sizing as thermo-pcb's R12", 790, 1400,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'CLT_ADC')})
place(f"{LIB}:C_V", "C36", "100nF CLT filter (AEC-Q200) - matches thermo-pcb's C24", 790, 1420,
      conn={'1': ('label', 'CLT_ADC'), '2': ('pwr', 'GND')})
# Both dividers' OTHER leg (the external NTC element itself, then GND) is
# entirely off-board - reaches the sensor via the harness connector in
# step 9, same as MAP_SIG/TPS_SIG/KNOCK_SIG. No on-sheet GND flag needed
# here (unlike VIN/GND at the very top of the power chain): the divider's
# board-side half (pull-up + filter cap to real on-board GND) is already
# a complete, correctly-terminated circuit on its own.

# --- knock sensor piezo front end (TLV2372 dual op-amp) ---
# Channel 2 = buffered mid-supply bias reference (real good practice for
# a single-supply piezo front end, not filler use of the spare channel).
# Channel 1 = AC-coupled non-inverting gain stage. All R/C values are
# typical placeholders - real gain/bandpass tuning depends on the engine's
# actual knock frequency (5-15kHz typical range), not yet chosen.
place(f"{LIB}:TLV2372", "U10", "TLV2372-Q1 automotive (AEC-Q100 G1)", 900, 1400,
      conn={'8': ('pwr', '+3V3', 2.54), '4': ('pwr', 'GND', 2.54),
            # channel 2 = unity-gain buffer: IN2+ reads the raw divider,
            # IN2-/OUT2 tied together (feedback) and become the actual
            # usable low-impedance mid-supply reference for channel 1.
            '5': ('label', 'KNOCK_MID_RAW'), '6': ('label', 'KNOCK_MID'),
            '7': ('label', 'KNOCK_MID'),
            '3': ('label', 'KNOCK_BIAS'), '2': ('label', 'KNOCK_FB'),
            '1': ('label', 'KNOCK_ADC', 7.62)})
place(f"{LIB}:R_V", "R26", "10k mid-supply divider hi (AEC-Q200)", 870, 1370,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'KNOCK_MID_RAW')})
place(f"{LIB}:R_V", "R27", "10k mid-supply divider lo (AEC-Q200)", 870, 1390,
      conn={'1': ('label', 'KNOCK_MID_RAW'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C37", "1uF mid-supply bypass (AEC-Q200)", 850, 1380,
      conn={'1': ('label', 'KNOCK_MID_RAW'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C38", "10nF AC-couple from sensor (AEC-Q200, typical - confirm)",
      850, 1400, conn={'1': ('label', 'KNOCK_SIG'), '2': ('label', 'KNOCK_BIAS')})
place(f"{LIB}:R_V", "R28", "1M bias to KNOCK_MID (AEC-Q200, typical - confirm)",
      870, 1410, conn={'1': ('label', 'KNOCK_BIAS'), '2': ('label', 'KNOCK_MID')})
place(f"{LIB}:R_V", "R29", "1k feedback lo (AEC-Q200, typical - confirm, sets gain)",
      920, 1420, conn={'1': ('label', 'KNOCK_FB'), '2': ('label', 'KNOCK_MID')})
place(f"{LIB}:R_V", "R30", "20k feedback hi (AEC-Q200, typical - confirm, sets gain ~21x)",
      920, 1390, conn={'1': ('label', 'KNOCK_FB'), '2': ('label', 'KNOCK_ADC')})
place(f"{LIB}:C_V", "C39", "1nF anti-alias (AEC-Q200)", 950, 1400,
      conn={'1': ('label', 'KNOCK_ADC'), '2': ('pwr', 'GND')})

# ---------------------------------------------------------------------------
# CAN bus(es) (plan step 6): 2x NXP TJA1043T transceiver, 2x real
# independent FlexCAN pairs.
# ---------------------------------------------------------------------------
# NXP TJA1043T (SO14, AEC-Q100 - confirmed real, explicit datasheet bullet,
# not assumed) - real pinout verified via a chipdip.ru mirror of NXP's own
# datasheet Rev.6. Package is real SO14 (a distributor search snippet
# suggested SO8 for a sibling part - trusted the primary-source PDF's own
# pinout diagram instead, not the snippet). VIO (pin5, I/O level-adaptor
# supply) is tied to +3V3 to match the MCU's own logic level directly -
# no level shifter needed, this is exactly what VIO is for. VBAT (pin10)
# ties to VIN_PROT (always-on protected battery), NOT VBATT_SW (relay-
# gated) - deliberate: this transceiver's WAKE-capable standby/sleep
# modes are real automotive practice specifically so a CAN bus event can
# wake the ECU with the ignition off, which only works if the transceiver
# itself stays powered. EN+STB_N together select the real operating mode
# (Normal/Listen-only/Standby/Sleep, not a single 3-state pin) - wired to
# MCU GPIO so firmware retains real power-mode control rather than
# hard-wiring always-on. ERR_N and INH are real outputs not used by this
# design (fault telemetry / external-regulator control, both genuine
# future-nice-to-haves, not requirements) - marked no_connect. WAKE (a
# real local wake INPUT, separate from bus-based wake) is tied inactive
# (GND) since this design doesn't have a dedicated remote-wake line yet -
# typical/TBD if that becomes a real requirement later.
register_symbol(f"{LIB}:TJA1043T", "U", "TBD", "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
                {'T': [P(3, "VCC", "power_in"), P(5, "VIO", "power_in"),
                       P(10, "VBAT", "power_in")],
                 'B': [P(2, "GND", "power_in")],
                 'L': [P(1, "TXD", "input"), P(4, "RXD", "output"),
                       P(6, "EN", "input"), P(14, "STB_N", "input")],
                 'R': [P(7, "INH", "output"), P(8, "ERR_N", "output"),
                       P(9, "WAKE", "input"), P(11, "SPLIT", "output"),
                       P(12, "CANL", "passive"), P(13, "CANH", "passive")]},
                datasheet="https://www.nxp.com/products/TJA1043")

section_text("CAN BUS: 2x NXP TJA1043T TRANSCEIVER (PLAN STEP 6)", 30, 1700)

CAN_BUSES = [
    ("CAN0", "U11", 1700, ("R34", "R35")),
    ("CAN1", "U12", 1800, ("R36", "R37")),
]
for bus, u_ref, y0, (r_ref_a, r_ref_b) in CAN_BUSES:
    x0 = 100
    place(f"{LIB}:TJA1043T", u_ref, "NXP TJA1043T automotive CAN transceiver (AEC-Q100)",
          x0, y0,
          conn={'3': ('pwr', '+5V', 5.08), '5': ('pwr', '+3V3', 5.08),
                '10': ('label', 'VIN_PROT', 5.08), '2': ('pwr', 'GND', 2.54),
                '1': ('label', f'{bus}_TX', 7.62), '4': ('label', f'{bus}_RX', 7.62),
                '6': ('label', f'{bus}_EN', 7.62), '14': ('label', f'{bus}_STB_N', 7.62),
                '7': ('nc',), '8': ('nc',),
                '9': ('pwr', 'GND', 5.08),
                '11': ('label', f'{bus}_SPLIT'),
                '12': ('label', f'{bus}_L', 7.62), '13': ('label', f'{bus}_H', 7.62)})
    place(f"{LIB}:C_V", f"C{40 if bus == 'CAN0' else 43}",
          "100nF VCC decouple (AEC-Q200)", x0 - 30, y0 - 30,
          conn={'1': ('pwr', '+5V'), '2': ('pwr', 'GND')})
    place(f"{LIB}:C_V", f"C{41 if bus == 'CAN0' else 44}",
          "100nF VIO decouple (AEC-Q200)", x0 - 55, y0 - 30,
          conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})
    place(f"{LIB}:C_V", f"C{42 if bus == 'CAN0' else 45}",
          "100nF VBAT decouple (AEC-Q200)", x0 - 80, y0 - 30,
          conn={'1': ('label', 'VIN_PROT'), '2': ('pwr', 'GND')})
    # Real split-termination network (2x 60R + cap to GND via SPLIT) - this
    # IS the bus termination (differentially equivalent to a standard
    # 120R end-of-bus resistor), not an extra component on top of it.
    # Populate only if this ECU is a physical bus end-node - DNP otherwise,
    # same real automotive practice as any other multi-drop CAN node.
    place(f"{LIB}:R_V", r_ref_a, "60R split termination (AEC-Q200) - DNP unless bus end-node",
          x0 + 250, y0 - 20,
          conn={'1': ('label', f'{bus}_H'), '2': ('label', f'{bus}_SPLIT')})
    place(f"{LIB}:R_V", r_ref_b, "60R split termination (AEC-Q200) - DNP unless bus end-node",
          x0 + 250, y0 + 10,
          conn={'1': ('label', f'{bus}_SPLIT'), '2': ('label', f'{bus}_L')})
    place(f"{LIB}:C_V", f"C{46 if bus == 'CAN0' else 47}",
          "4.7nF SPLIT stabilization (AEC-Q200)", x0 + 280, y0 - 5,
          conn={'1': ('label', f'{bus}_SPLIT'), '2': ('pwr', 'GND')})

# ---------------------------------------------------------------------------
# USB-C wired (FT4232HA) + BLE wireless (CC2640R2F-Q1) programming paths,
# arbitrated by a real SN3257-Q1 analog switch (plan steps 7+8, done
# together since they share the same MCU bootloader lines by design).
# ---------------------------------------------------------------------------
# FTDI FT4232HA (64-QFN, AEC-Q100 G2) - real pins verified via a Wayback
# snapshot of FTDI's own datasheet (live URL Cloudflare-blocked). This is
# a SIMPLIFIED/PARTIAL symbol (18 of 64 real pins) - unlike the MCU/
# MC33810, which got a full accurate pin count, this session's research
# only targeted the specific pins this design needs (one plain UART
# channel + USB + oscillator + power + reset), not the complete Table
# 3-1. Channel C chosen deliberately: it's plain RS232/bit-bang only, no
# MPSSE ambiguity (channels A/B can be MPSSE, more capability than needed
# here). VCCIO (5 pins, must all tie together per the datasheet) runs off
# the board's EXISTING +3V3 rail - real design win: that rail is already
# always-on (step 2, not relay-gated) specifically so USB programming
# works with the engine/ignition off, and FT4232HA has NO dedicated VBUS
# sense pin per this research (confirmed, not assumed) - it's a genuinely
# self-powered USB device on this board, not bus-powered. VCORE (3 pins,
# internal +1.2V core rail) gets decoupling caps only, consistent with
# how FTDI parts typically self-regulate this rail - flagged pending the
# full datasheet's block diagram for confirmation. RTS#/DTR# (real
# hardware-driven outputs, Channel C) drive the boot-mode-select
# arbitration switch - the same real auto-reset-into-bootloader trick
# widely used on USB-UART dev boards (e.g. ESP32/Arduino-style), not a
# novel invention here. Footprint (real bundled QFN-64-1EP) has an
# exposed thermal/ground pad this simplified 18-pin symbol does NOT yet
# represent as a schematic pin - this session's research didn't confirm
# the EP's real function for this specific part, left unclaimed rather
# than guessed; will show as a real footprint/schematic pin-count
# mismatch at PCB stage until resolved, flagged honestly now.
register_symbol(f"{LIB}:FT4232HA", "U", "TBD", "Package_DFN_QFN:QFN-64-1EP_9x9mm_P0.5mm_EP4.7x4.7mm",
                {'T': [P(12, "VCORE", "power_in"), P(37, "VCORE", "power_in"),
                       P(64, "VCORE", "power_in"),
                       P(2, "VCCIO", "power_in"), P(20, "VCCIO", "power_in"),
                       P(31, "VCCIO", "power_in"), P(42, "VCCIO", "power_in"),
                       P(56, "VCCIO", "power_in")],
                 'B': [P(14, "RESET_N", "input"), P(3, "OSCI", "passive"),
                       P(4, "OSCO", "passive")],
                 'L': [P(7, "DM", "passive"), P(8, "DP", "passive")],
                 'R': [P(38, "TXD_C", "output"), P(39, "RXD_C", "input"),
                       P(40, "RTS_N_C", "output"), P(41, "CTS_N_C", "input"),
                       P(43, "DTR_N_C", "output")]},
                datasheet="https://ftdichip.com/products/ft4232ha/")

# TI CC2640R2F-Q1 (48-QFN VQFN, AEC-Q100 G2, Cortex-M3 + BLE) - real power/
# RF/oscillator/reset pins verified directly off TI's own datasheet
# (SWRS201C, no mirror needed this fetch). DIO pins are a real flexible
# GPIO crossbar with NO fixed default assignment - the specific DIO
# numbers used below (UART on DIO_2/3, boot/reset control on DIO_8-10)
# are candidate/plausible per this session's research, explicitly NOT
# independently pin-by-pin verified against the complete DIO table -
# confirm against TI's SmartRF/LaunchPad board file before firmware
# bring-up, same "verify before trusting" flag as several other parts.
# VDDS_DCDC tied directly to VDDS (+3V3) - bypasses the optional internal
# DC/DC converter rather than adding its external inductor, a deliberate
# scope simplification (efficiency optimization, not a functional
# requirement). X32K (32.768kHz LF crystal) is genuinely OPTIONAL per the
# datasheet (internal RCOSC_LF substitutes) - skipped to reduce parts
# count, real documented tradeoff not an oversight. X24M (24MHz HF
# crystal) is NOT optional - required for the BLE radio reference. Same
# unrepresented-exposed-pad caveat as FT4232HA above (real bundled
# QFN-48-1EP footprint, EP function not confirmed this session).
register_symbol(f"{LIB}:CC2640R2F", "U", "TBD", "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm",
                {'T': [P(44, "VDDS", "power_in"), P(13, "VDDS2", "power_in"),
                       P(22, "VDDS3", "power_in"), P(34, "VDDS_DCDC", "power_in")],
                 'B': [P(35, "RESET_N", "input"),
                       P(46, "X24M_N", "passive"), P(47, "X24M_P", "passive")],
                 'L': [P(1, "RF_P", "passive"), P(2, "RF_N", "passive")],
                 'R': [P(7, "DIO2_TXD", "output"), P(8, "DIO3_RXD", "input"),
                       P(14, "DIO8_FAB", "output"), P(15, "DIO9_ABS", "output"),
                       P(18, "DIO10_RST", "output")]},
                datasheet="https://www.ti.com/lit/ds/symlink/cc2640r2f-q1.pdf")

# TI SN3257-Q1 (16-pin TSSOP, AEC-Q100 G1) - real, COMPLETE pin table
# (all 16 pins, unlike the two bridge ICs above) - a genuine, in-
# production 4-channel 2:1 analog switch, exactly enough channels for
# the 4 signals that need arbitrating (UART TX, UART RX, FAB, ABS). SEL
# has a real internal 6M-ohm pulldown (powers up selecting side A, a
# real documented default, not assumed) - wired from a simple VBUS-
# presence divider so the wired USB path automatically wins whenever
# USB-C is plugged in, no MCU firmware involvement needed for the
# decision itself. EN ties to GND (always enabled - active-HIGH disable
# per the datasheet, so GND = never disabled).
register_symbol(f"{LIB}:SN3257", "U", "TBD", "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
                {'T': [P(16, "VDD", "power_in"), P(1, "SEL", "input")],
                 'B': [P(8, "GND", "power_in"), P(15, "EN", "input")],
                 'L': [P(2, "S1A", "passive"), P(3, "S1B", "passive"),
                       P(5, "S2A", "passive"), P(6, "S2B", "passive"),
                       P(11, "S3A", "passive"), P(10, "S3B", "passive"),
                       P(14, "S4A", "passive"), P(13, "S4B", "passive")],
                 'R': [P(4, "D1", "passive"), P(7, "D2", "passive"),
                       P(9, "D3", "passive"), P(12, "D4", "passive")]},
                datasheet="https://www.ti.com/lit/gpn/SN3257-Q1")

# REAL BUG found + fixed at PCB-generation time (step 10): the original
# registration used a SIMPLIFIED sequential 1-8 pin numbering that this
# comment itself had already flagged as not matching any real
# manufacturer's actual pad numbers - confirmed the hard way when
# build_pcb.py loaded the REAL Amphenol 12401610E4-2A footprint and its
# actual pads (A1-A12, B1-B12, SH) didn't match, caught by the same real
# net-pin-count check that caught K1's relay footprint bug above. Fixed
# by using the real USB-IF Type-C pin-and-lettering convention this
# specific real footprint uses: A-row and B-row are mechanically mirrored
# (so the plug works either way up) and carry the SAME signal on both
# rows for GND/VBUS/CC-adjacent pins - GND(A1,A12,B1,B12), VBUS(A4,A9,
# B4,B9), D+(A6,B6 - same net, tied together), D-(A7,B7 - same net) are
# real, standard, public-spec pin assignments, not this project's own
# choice (unlike J1's JTAG header, which IS this project's own generic
# 2.54mm-header assignment). CC1(A5)/CC2(B5) are genuinely DIFFERENT
# signals (used together for cable-flip detection), not a mirrored pair.
# SH is a single logical shield pin physically realized as 4 separate
# pads (same real "one number, many physical pads" pattern already
# established for Manifold's fuse holder and this project's own F1-F4).
# High-speed (A2/A3/A10/A11/B2/B3/B10/B11) and SBU1/SBU2 (A8/B8) pins are
# real but genuinely unused at USB-2.0-only, marked no_connect.
register_symbol(f"{LIB}:USB_C", "J", "TBD", "Connector_USB:USB_C_Receptacle_Amphenol_12401610E4-2A",
                {'L': [P("A1", "GND", "power_in"), P("B1", "GND", "power_in"),
                       P("A12", "GND", "power_in"), P("B12", "GND", "power_in"),
                       P("SH", "SHIELD", "power_in")],
                 'R': [P("A4", "VBUS", "power_in"), P("B4", "VBUS", "power_in"),
                       P("A9", "VBUS", "power_in"), P("B9", "VBUS", "power_in"),
                       P("A5", "CC1", "passive"), P("B5", "CC2", "passive"),
                       P("A6", "DP", "passive"), P("B6", "DP", "passive"),
                       P("A7", "DM", "passive"), P("B7", "DM", "passive")],
                 'T': [P("A2", "TX1P_NC", "no_connect"), P("A3", "TX1N_NC", "no_connect"),
                       P("A10", "RX2N_NC", "no_connect"), P("A11", "RX2P_NC", "no_connect"),
                       P("A8", "SBU1_NC", "no_connect")],
                 'B': [P("B2", "TX2P_NC", "no_connect"), P("B3", "TX2N_NC", "no_connect"),
                       P("B10", "RX1N_NC", "no_connect"), P("B11", "RX1P_NC", "no_connect"),
                       P("B8", "SBU2_NC", "no_connect")]},
                hide_pin_names=True)

# Real required RF topology (differential radio -> balun -> single-ended
# antenna feed) - exact balun part/matching component VALUES are a
# placeholder pending TI's official CC2640R2F reference-design BOM/
# layout (not fetched this session, same "topology real, values TBD"
# treatment as CJ125's analog network). Antenna itself is out of scope
# for this pass - BLE_ANT is a labeled stub awaiting a real antenna/
# connector choice, same pattern as every other off-board interface.
register_symbol(f"{LIB}:BALUN_2G4", "FB", "TBD", "Package_DFN_QFN:DFN-6_1.6x1.3mm_P0.4mm",
                {'L': [P(1, "IN+", "passive"), P(2, "IN-", "passive")],
                 'R': [P(3, "OUT", "passive")], 'B': [P(4, "GND", "power_in")]},
                hide_pin_names=True)

section_text("PROGRAMMING: USB-C (FT4232HA) + BLE (CC2640R2F-Q1) - STEPS 7+8", 30, 1950)

place(f"{LIB}:USB_C", "J3", "USB-C receptacle, real Amphenol 12401610E4-2A pinout", 90, 1980,
      conn={'A1': ('pwr', 'GND', 7.62), 'B1': ('pwr', 'GND', 5.08),
            'A12': ('pwr', 'GND', 5.08), 'B12': ('pwr', 'GND', 5.08),
            'SH': ('pwr', 'GND', 5.08),
            'A4': ('label', 'USB_VBUS', 5.08), 'B4': ('label', 'USB_VBUS', 5.08),
            'A9': ('label', 'USB_VBUS', 5.08), 'B9': ('label', 'USB_VBUS', 5.08),
            'A5': ('label', 'USB_CC1'), 'B5': ('label', 'USB_CC2'),
            'A6': ('label', 'USB_DP'), 'B6': ('label', 'USB_DP'),
            'A7': ('label', 'USB_DM'), 'B7': ('label', 'USB_DM')})
# CC1/CC2 5.1k pull-downs - real, standard USB-IF spec value for a UFP
# (device) port advertising default USB power, public specification, not
# a manufacturer-specific value needing the same mirror-hunting research
# as an IC datasheet.
place(f"{LIB}:R_V", "R38", "5.1k CC1 pull-down (AEC-Q200)", 60, 1970,
      conn={'1': ('label', 'USB_CC1'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R39", "5.1k CC2 pull-down (AEC-Q200)", 60, 1995,
      conn={'1': ('label', 'USB_CC2'), '2': ('pwr', 'GND')})
# VBUS-presence divider feeding SN3257's SEL - simple, autonomous "wired
# path wins when plugged in" arbitration, no MCU decision needed.
place(f"{LIB}:R_V", "R40", "10k VBUS divider hi (AEC-Q200)", 130, 1960,
      conn={'1': ('label', 'USB_VBUS'), '2': ('label', 'USB_PRESENT')})
place(f"{LIB}:R_V", "R41", "10k VBUS divider lo (AEC-Q200)", 130, 1985,
      conn={'1': ('label', 'USB_PRESENT'), '2': ('pwr', 'GND')})

place(f"{LIB}:FT4232HA", "U13", "FTDI FT4232HA automotive (AEC-Q100 G2)", 220, 1980,
      conn={'12': ('pwr', '+3V3', 2.54), '37': ('pwr', '+3V3', 2.54), '64': ('pwr', '+3V3', 2.54),
            '2': ('pwr', '+3V3', 2.54), '20': ('pwr', '+3V3', 2.54), '31': ('pwr', '+3V3', 2.54),
            '42': ('pwr', '+3V3', 2.54), '56': ('pwr', '+3V3', 2.54),
            '14': ('label', 'FT_RESET_N', 5.08),
            '3': ('label', 'FT_OSC_A'), '4': ('label', 'FT_OSC_B'),
            '7': ('label', 'USB_DM'), '8': ('label', 'USB_DP'),
            '38': ('label', 'FT_TXD'), '39': ('label', 'FT_RXD'),
            '40': ('label', 'FT_RTS_N'), '41': ('nc',),
            '43': ('label', 'FT_DTR_N')})
place(f"{LIB}:R_V", "R42", "10k RESET_N pull-up (AEC-Q200)", 250, 1950,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'FT_RESET_N')})
place(f"{LIB}:XTAL", "Y2", "12MHz (AEC-Q200)", 190, 1990,
      conn={'1': ('label', 'FT_OSC_A'), '2': ('label', 'FT_OSC_B'),
            '3': ('label', 'FT_OSC_A'), '4': ('label', 'FT_OSC_B')})
place(f"{LIB}:C_V", "C48", "18pF (AEC-Q200)", 170, 2020,
      conn={'1': ('label', 'FT_OSC_A'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C49", "18pF (AEC-Q200)", 195, 2020,
      conn={'1': ('label', 'FT_OSC_B'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C50", "100nF VCORE decouple (AEC-Q200, typical - confirm)", 220, 1940,
      conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C51", "100nF VCCIO decouple (AEC-Q200)", 245, 1940,
      conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})

place(f"{LIB}:CC2640R2F", "U14", "TI CC2640R2F-Q1 automotive BLE SoC (AEC-Q100 G2)", 220, 2080,
      conn={'44': ('pwr', '+3V3', 2.54), '13': ('pwr', '+3V3', 2.54), '22': ('pwr', '+3V3', 2.54),
            '34': ('pwr', '+3V3', 2.54),
            '35': ('label', 'BLE_RESET_N', 5.08),
            '46': ('label', 'BLE_OSC_A'), '47': ('label', 'BLE_OSC_B'),
            '1': ('label', 'BLE_RF_P'), '2': ('label', 'BLE_RF_N'),
            '7': ('label', 'BLE_TXD'), '8': ('label', 'BLE_RXD'),
            '14': ('label', 'BLE_FAB'), '15': ('label', 'BLE_ABS'),
            '18': ('label', 'BLE_RST_CTL')})
place(f"{LIB}:R_V", "R43", "10k RESET_N pull-up (AEC-Q200)", 250, 2050,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'BLE_RESET_N')})
place(f"{LIB}:XTAL", "Y3", "24MHz (AEC-Q200)", 190, 2090,
      conn={'1': ('label', 'BLE_OSC_A'), '2': ('label', 'BLE_OSC_B'),
            '3': ('label', 'BLE_OSC_A'), '4': ('label', 'BLE_OSC_B')})
place(f"{LIB}:C_V", "C52", "10pF (AEC-Q200, typical - confirm)", 170, 2120,
      conn={'1': ('label', 'BLE_OSC_A'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C53", "10pF (AEC-Q200, typical - confirm)", 195, 2120,
      conn={'1': ('label', 'BLE_OSC_B'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C54", "100nF VDDS decouple (AEC-Q200)", 220, 2040,
      conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C55", "1uF VDDS bulk (AEC-Q200)", 245, 2040,
      conn={'1': ('pwr', '+3V3'), '2': ('pwr', 'GND')})

# RF chain: differential RF_P/RF_N -> balun -> single-ended antenna feed.
place(f"{LIB}:BALUN_2G4", "FB1", "2.4GHz balun (typical - confirm part/matching before fab)",
      160, 2080,
      conn={'1': ('label', 'BLE_RF_P'), '2': ('label', 'BLE_RF_N'),
            '3': ('label', 'BLE_ANT', 7.62), '4': ('pwr', 'GND')})

# Arbitration switch: 4 real channels cover exactly the 4 signals that
# need it. D-side ties to the MCU's already-existing labels from step 3
# (BOOT_FAB/BOOT_ABS/LIN0_TX/LIN0_RX) - this is what finally resolves
# those dangling stubs. A-side = wired (FT4232HA), B-side = wireless
# (CC2640R2F-Q1) - SEL's real internal pulldown means A (wired) is the
# power-up default even before USB_PRESENT is valid, a safe default.
place(f"{LIB}:SN3257", "U15", "TI SN3257-Q1 automotive 4ch analog switch (AEC-Q100 G1)",
      350, 2020,
      conn={'16': ('pwr', '+3V3', 2.54), '1': ('label', 'USB_PRESENT', 5.08),
            '8': ('pwr', 'GND', 2.54), '15': ('pwr', 'GND', 5.08),
            # CH1: shared UART line INTO the MCU (LIN0_RX)
            '4': ('label', 'LIN0_RX', 7.62),
            '2': ('label', 'FT_TXD', 7.62), '3': ('label', 'BLE_TXD', 7.62),
            # CH2: shared UART line OUT of the MCU (LIN0_TX)
            '7': ('label', 'LIN0_TX', 7.62),
            '5': ('label', 'FT_RXD', 7.62), '6': ('label', 'BLE_RXD', 7.62),
            # CH3: BOOT_FAB arbitration
            '9': ('label', 'BOOT_FAB', 7.62),
            '11': ('label', 'FT_RTS_N', 7.62), '10': ('label', 'BLE_FAB', 7.62),
            # CH4: BOOT_ABS arbitration
            '12': ('label', 'BOOT_ABS', 7.62),
            '14': ('label', 'FT_DTR_N', 7.62), '13': ('label', 'BLE_ABS', 7.62)})

# NOT implemented this pass, documented honestly rather than papered
# over: MPC5606B's Boot Assist Module only re-latches FAB/ABS at an
# actual RESET event, not just whenever their levels change - so
# entering the bootloader needs BOTH the switch selecting the right
# FAB/ABS levels (wired above) AND a real reset pulse at that moment.
# Neither bridge IC has a spare pin registered in this session's
# (deliberately partial) pin set to drive the MCU's MCU_RESET net in
# hardware - FT4232HA's RTS#/DTR# are already committed to FAB/ABS
# arbitration, and CC2640R2F-Q1's DIO10 (BLE_RST_CTL, pin 18) is
# registered but NOT wired to MCU_RESET here, since without a matching
# hardware path on the FT4232HA side it would be an asymmetric, half-
# finished mechanism. Current real mechanism: the MCU's own running
# firmware, on receiving an "enter bootloader" command over the already-
# arbitrated UART, sets its own boot-mode intent and performs a SOFTWARE
# self-reset - works for a live, cooperating MCU, but does NOT recover a
# hung/crashed one. A hardware-forced path would need a spare GPIO
# reserved on both bridges - a real, scoped-out follow-up, not a bug.

# ---------------------------------------------------------------------------
# Connectors + full net-to-pin mapping (plan step 9).
# ---------------------------------------------------------------------------
# Two TE AMPSEAL 776180-1 (35-pos right-angle) connectors - the EXACT same
# real, dimensionally-verified part/footprint already used and checked
# against TE's own drawing in manifold-pcb (copied directly, not
# re-verified from scratch here), reused for real BOM-commonality/cost
# reasons rather than sourcing two different connector families sized
# exactly to each harness's real pin count (25 and 18 of 35 used
# respectively) - a deliberate, honestly-oversized choice, not an
# oversight; a smaller connector could replace either once the design is
# final. Pin NUMBERS are the real physical AMPSEAL numbering (same as
# Manifold's own J1); signal ASSIGNMENT to those numbers is this
# project's own choice, same as how Manifold assigned D0-D19/A0-A7 to its
# own J1 - AMPSEAL is a generic pin-and-socket connector with no fixed
# per-pin function of its own.
CONN_AMPSEAL_PINS = {n: P(n, f"P{n}", "passive") for n in range(1, 36)}
register_symbol(f"{LIB}:CONN_AMPSEAL35", "J", "TBD",
                "TE_AMPSEAL_776180-1:TE_1-776180-1",
                {'L': [CONN_AMPSEAL_PINS[n] for n in range(1, 18)],
                 'R': [CONN_AMPSEAL_PINS[n] for n in range(18, 36)]},
                hide_pin_names=True)

# Real bundled U.FL connector (Hirose U.FL-R-SMT-1) for the BLE external
# antenna - real standard part, matches automotive practice of an
# external antenna fed out of a metal enclosure (a PCB trace/chip antenna
# wouldn't radiate well from inside one) rather than an on-board antenna.
register_symbol(f"{LIB}:CONN_UFL", "J", "TBD",
                "Connector_Coaxial:U.FL_Hirose_U.FL-R-SMT-1_Vertical",
                {'L': [P(1, "SIG", "passive")], 'R': [P(2, "GND", "power_in")]},
                hide_pin_names=True)

# ---------------------------------------------------------------------------
# SENSOR/OUTPUT EXPANSION - closes the real functional gaps found in a
# full design review: battery-voltage sensing, a second cam input, real
# actuator outputs (VVT x2, idle, fuel pump, tach), oil/fuel pressure
# inputs, a second knock channel, and a second wideband O2 bank.
#
# Every part here is one this project has ALREADY registered and
# verified against a real datasheet elsewhere in this file (MAX9924,
# CJ125, TLV2372, MOSFET_N, D_FLYBACK, R_V/C_V) - deliberately no new
# unverified components were introduced to build this out.
# ---------------------------------------------------------------------------
section_text("EXPANSION: BATT SENSE + CAM2 + VVT/IDLE/FUEL-PUMP/TACH + OIL/FUEL PRESSURE"
             " + KNOCK2 + O2 BANK 2", 30, 2500)

# --- battery-voltage sensing ------------------------------------------------
# The single most consequential gap the review found. Injector dead time
# (the delay between commanded and actual opening) varies strongly with
# supply voltage, and compensating for it against a measured battery
# voltage is standard ECU practice - without this input the ECU has no
# way to apply that correction, and fuelling drifts exactly when supply
# sags most (cranking, idle with high electrical load).
#
# Divider is sized off VIN_PROT (the always-on protected rail, so the
# reading is valid with the main relay off) against the ADC's real 3.3V
# reference: 68k/10k gives full scale at ~25.7V, so a 24V jump-start
# still reads on-scale rather than pinning. At a normal 14.4V the node
# sits at ~1.85V, comfortably mid-range. The 68k top leg also limits
# current into the pin's clamp diodes to well under 1mA even during a
# load-dump excursion the TVS hasn't fully clamped.
place(f"{LIB}:R_V", "R48", "68k VBATT sense divider hi (AEC-Q200)", 1100, 1400,
      conn={'1': ('label', 'VIN_PROT'), '2': ('label', 'VBATT_ADC')})
place(f"{LIB}:R_V", "R49", "10k VBATT sense divider lo (AEC-Q200)", 1100, 1420,
      conn={'1': ('label', 'VBATT_ADC'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C58", "100nF VBATT sense filter (AEC-Q200)", 1130, 1410,
      conn={'1': ('label', 'VBATT_ADC'), '2': ('pwr', 'GND')})

# --- second cam (exhaust) VR interface --------------------------------------
# Third MAX9924, wired identically to the crank/cam pair above. This is
# what actually delivers "crank + 2 cams": a DOHC engine phasing intake
# and exhaust cams independently needs both measured, and each on its
# own real hardware input-capture channel (E0UC[18] here) rather than a
# software edge interrupt, whose ISR latency would land straight in the
# captured timestamp.
place(f"{LIB}:MAX9924", "U16", "MAX9924 automotive (AEC-Q100) - CAM2 VR interface",
      1250, 1400,
      conn={'10': ('pwr', '+5V', 5.08), '5': ('pwr', 'GND', 5.08),
            '1': ('label', 'CAM2_VR_HI'), '2': ('label', 'CAM2_VR_LO'),
            '4': ('label', 'CAM2_BIAS'),
            '6': ('nc',), '9': ('pwr', 'GND', 5.08), '8': ('pwr', 'GND', 5.08),
            '7': ('label', 'CAM2_COUT', 5.08)})
place(f"{LIB}:R_V", "R50", "10k CAM2 BIAS divider hi (AEC-Q200)", 1220, 1360,
      conn={'1': ('pwr', '+5V'), '2': ('label', 'CAM2_BIAS')})
place(f"{LIB}:R_V", "R51", "10k CAM2 BIAS divider lo (AEC-Q200)", 1220, 1385,
      conn={'1': ('label', 'CAM2_BIAS'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C59", "100nF CAM2 BIAS bypass (AEC-Q200)", 1195, 1375,
      conn={'1': ('label', 'CAM2_BIAS'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R52", "4.7k CAM2 COUT pull-up to +3V3 (AEC-Q200)", 1310, 1360,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'CAM2_COUT')})

# --- oil + fuel pressure analog inputs --------------------------------------
# Same standard-practice RC anti-alias pattern as MAP/TPS (1k + 22nF).
# Cheap to add and genuinely valuable: real oil- and fuel-pressure
# inputs are what let firmware implement low-oil-pressure and
# fuel-pressure-loss protection cuts rather than just logging them.
place(f"{LIB}:R_V", "R53", "1k oil-pressure RC filter (AEC-Q200)", 1400, 1400,
      conn={'1': ('label', 'OILP_SIG'), '2': ('label', 'OILP_ADC')})
place(f"{LIB}:C_V", "C60", "22nF oil-pressure RC filter (AEC-Q200)", 1400, 1420,
      conn={'1': ('label', 'OILP_ADC'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R54", "1k fuel-pressure RC filter (AEC-Q200)", 1430, 1400,
      conn={'1': ('label', 'FUELP_SIG'), '2': ('label', 'FUELP_ADC')})
place(f"{LIB}:C_V", "C61", "22nF fuel-pressure RC filter (AEC-Q200)", 1430, 1420,
      conn={'1': ('label', 'FUELP_ADC'), '2': ('pwr', 'GND')})

# --- low-side actuator outputs ----------------------------------------------
# VVT phaser solenoids x2, idle-air valve, fuel-pump relay, tach.
#
# Deliberate choice of DISCRETE low-side drivers over an integrated SPI
# multi-channel driver (Infineon SPIDER/FLEX, ST L9301-class): those
# parts would add real open-load/short diagnostics, but every one of
# them is a new, complex, unverified component, whereas this reuses the
# exact MOSFET + Schottky-flyback pattern already verified and in use
# here for the main relay (Q2/D2) and the O2 heater (Q11). Given this
# project's rule that nothing ships unverified, reusing a proven part
# beat introducing an unproven one. The tradeoff - no per-channel
# electrical diagnostics - is real and worth revisiting if a future
# pass wants fault reporting on these outputs.
#
# Each inductive channel gets its own flyback diode clamping to
# VBATT_SW, matching D2's polarity convention exactly (anode on the
# switched low side, cathode on the supply). The solenoid/relay coils
# themselves are external: their + side takes VBATT_SW from J5, their
# - side returns to the corresponding *_OUT harness pin.
for _i, (_q, _r, _d, _name, _ctrl) in enumerate([
        ("Q12", "R55", "D3", "VVT1", "VVT1_CTRL"),
        ("Q13", "R56", "D4", "VVT2", "VVT2_CTRL"),
        ("Q14", "R57", "D5", "IDLE", "IDLE_CTRL"),
        ("Q15", "R58", "D6", "FPUMP", "FPUMP_CTRL")]):
    _x = 1520 + _i * 90
    place(f"{LIB}:MOSFET_N", _q,
          f"PMV37ENEA automotive (AEC-Q101), {_name} low-side driver", _x, 1400,
          conn={'2': ('pwr', 'GND'),
                '3': ('label', f'{_name}_OUT'),
                '1': ('label', f'{_name}_GATE', 7.62)})
    place(f"{LIB}:R_V", _r, f"1k {_name} gate resistor (AEC-Q200)", _x, 1360,
          conn={'1': ('label', _ctrl), '2': ('label', f'{_name}_GATE')})
    place(f"{LIB}:D_FLYBACK", _d,
          f"PMEG4010BEA automotive (AEC-Q101), {_name} flyback", _x + 40, 1430,
          conn={'1': ('label', f'{_name}_OUT'), '2': ('label', 'VBATT_SW')})

# Tach output: a signal-level open-drain drive for a dash tachometer, so
# no flyback (not an inductive load) but a pull-up so the output has a
# defined idle level. Pulled to +5V here; some real tach gauges expect a
# 12V-referenced square wave and will need their own external pull-up -
# flagged rather than assumed.
place(f"{LIB}:MOSFET_N", "Q16", "PMV37ENEA automotive (AEC-Q101), tach output driver",
      1880, 1400,
      conn={'2': ('pwr', 'GND'),
            '3': ('label', 'TACH_OUT'),
            '1': ('label', 'TACH_GATE', 7.62)})
place(f"{LIB}:R_V", "R59", "1k tach gate resistor (AEC-Q200)", 1880, 1360,
      conn={'1': ('label', 'TACH_CTRL'), '2': ('label', 'TACH_GATE')})
place(f"{LIB}:R_V", "R60", "10k tach pull-up to +5V (AEC-Q200)", 1920, 1430,
      conn={'1': ('pwr', '+5V'), '2': ('label', 'TACH_OUT')})

# --- knock sensor, bank 2 ---------------------------------------------------
# Second TLV2372. Channel 1 is the same AC-coupled non-inverting gain
# stage as U10's; channel 2 is wired as a unity-gain follower off U10's
# already-buffered KNOCK_MID, giving bank 2 its own local low-impedance
# mid-supply reference. That both keeps the spare channel in a defined,
# stable configuration (rather than left floating) and avoids the two
# knock channels sharing a reference node they could couple through.
# Same "typical placeholder" caveat as bank 1: real gain/bandpass values
# depend on the engine's actual knock frequency, not yet chosen.
place(f"{LIB}:TLV2372", "U17", "TLV2372-Q1 automotive (AEC-Q100 G1) - knock bank 2",
      1100, 1550,
      conn={'8': ('pwr', '+3V3', 2.54), '4': ('pwr', 'GND', 2.54),
            '5': ('label', 'KNOCK_MID'), '6': ('label', 'KNOCK2_MID'),
            '7': ('label', 'KNOCK2_MID'),
            '3': ('label', 'KNOCK2_BIAS'), '2': ('label', 'KNOCK2_FB'),
            '1': ('label', 'KNOCK2_ADC', 7.62)})
place(f"{LIB}:C_V", "C62", "10nF knock2 AC-couple (AEC-Q200, typical - confirm)",
      1050, 1550, conn={'1': ('label', 'KNOCK2_SIG'), '2': ('label', 'KNOCK2_BIAS')})
place(f"{LIB}:R_V", "R61", "1M knock2 bias to KNOCK2_MID (AEC-Q200, typical - confirm)",
      1070, 1560, conn={'1': ('label', 'KNOCK2_BIAS'), '2': ('label', 'KNOCK2_MID')})
place(f"{LIB}:R_V", "R62", "1k knock2 feedback lo (AEC-Q200, typical - sets gain)",
      1120, 1570, conn={'1': ('label', 'KNOCK2_FB'), '2': ('label', 'KNOCK2_MID')})
place(f"{LIB}:R_V", "R63", "20k knock2 feedback hi (AEC-Q200, typical - gain ~21x)",
      1120, 1540, conn={'1': ('label', 'KNOCK2_FB'), '2': ('label', 'KNOCK2_ADC')})
place(f"{LIB}:C_V", "C63", "1nF knock2 anti-alias (AEC-Q200)", 1150, 1550,
      conn={'1': ('label', 'KNOCK2_ADC'), '2': ('pwr', 'GND')})

# --- wideband O2, bank 2 ----------------------------------------------------
# Second CJ125 + heater MOSFET, an exact mirror of bank 1 including the
# corrected application-circuit topology (see the bank-1 comments for
# the full per-component derivation off Bosch's own figure - all of it
# applies identically here). Shares the same SPI bus as the MC33810s and
# bank 1, with its own chip select on SPI_CS_3.
place(f"{LIB}:CJ125", "U18", "Bosch CJ125 wideband O2 controller #2 (SOIC-24W)",
      1400, 1550,
      conn={
          '1': ('label', 'VBATT_SW', 5.08),
          '17': ('pwr', '+5V', 5.08),
          '24': ('pwr', 'GND', 2.54),
          '5': ('label', 'CJ2_OSZ'),
          '9': ('label', 'O2B_UN'),
          '10': ('label', 'CJ2_RM'),
          '11': ('label', 'CJ2_CM'),
          '12': ('label', 'CJ2_UR'),
          '21': ('label', 'CJ2_UA'),
          '22': ('label', 'CJ2_CF'),
          '23': ('label', 'CJ2_RF'),
          '2': ('label', 'CJ2_UN'),
          '3': ('label', 'O2B_IP'),
          '4': ('label', 'O2B_IA'),
          '19': ('label', 'O2B_US'),
          '20': ('label', 'O2B_UP'),
          '18': ('label', 'O2B_VM'),
          '13': ('label', 'SPI_SCLK', 7.62),
          '14': ('label', 'SPI_SO', 7.62),
          '15': ('label', 'SPI_SI', 7.62),
          '16': ('label', 'SPI_CS_3', 7.62),
          '8': ('label', 'CJ2_RST_N', 5.08),
          '6': ('label', 'HTR2_GATE'),
          '7': ('label', 'HTR2_DRAIN_SENSE'),
      })
place(f"{LIB}:C_V", "C64", "33nF UB decouple (AEC-Q200)", 1370, 1520,
      conn={'1': ('label', 'VBATT_SW'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C65", "33nF VCC decouple (AEC-Q200)", 1395, 1520,
      conn={'1': ('pwr', '+5V'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R64", "10k OSZ oscillator reference (AEC-Q200)", 1340, 1590,
      conn={'1': ('label', 'CJ2_OSZ'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C66", "33nF UA stabilize (AEC-Q200)", 1340, 1610,
      conn={'1': ('label', 'CJ2_UA'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R65", "100k RF filter (AEC-Q200)", 1340, 1670,
      conn={'1': ('label', 'CJ2_RF'), '2': ('label', 'CJ2_CF')})
place(f"{LIB}:C_V", "C67", "100nF CF filter (AEC-Q200)", 1340, 1630,
      conn={'1': ('label', 'CJ2_CF'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C68", "33nF UR stabilize (AEC-Q200)", 1340, 1650,
      conn={'1': ('label', 'CJ2_UR'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R66", "61R9 IA-IP pump current shunt (AEC-Q200)", 1340, 1690,
      conn={'1': ('label', 'O2B_IA'), '2': ('label', 'O2B_IP')})
place(f"{LIB}:R_V", "R67", "10k RM (AEC-Q200) - LSU4.2 value (31.6k for LSU4.9)",
      1340, 1710, conn={'1': ('label', 'CJ2_RM'), '2': ('label', 'O2B_UN')})
place(f"{LIB}:C_V", "C69", "100nF CM (AEC-Q200)", 1365, 1710,
      conn={'1': ('label', 'CJ2_CM'), '2': ('label', 'O2B_UN')})
place(f"{LIB}:R_V", "R68", "10k RST_N pull-up (AEC-Q200)", 1340, 1730,
      conn={'1': ('pwr', '+5V'), '2': ('label', 'CJ2_RST_N')})
place(f"{LIB}:C_V", "C70", "100nF RST_N stabilize (AEC-Q200)", 1365, 1730,
      conn={'1': ('label', 'CJ2_RST_N'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R69", "10k UN series R (AEC-Q200) - value unlabeled by Bosch",
      1390, 1710, conn={'1': ('label', 'CJ2_UN'), '2': ('label', 'O2B_UN')})
place(f"{LIB}:C_V", "C71", "1nF UN filter (AEC-Q200) - value unlabeled by Bosch",
      1415, 1710, conn={'1': ('label', 'CJ2_UN'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R70", "100k Nernst-UP node bridge (AEC-Q200)", 1390, 1730,
      conn={'1': ('label', 'O2B_UN'), '2': ('label', 'O2B_UP')})
place(f"{LIB}:R_V", "R71", "470k UP-IP node bridge (AEC-Q200)", 1340, 1750,
      conn={'1': ('label', 'O2B_UP'), '2': ('label', 'O2B_IP')})
place(f"{LIB}:R_V", "R72", "82R5 Nernst-VM node bridge (AEC-Q200) - LSU4.2 (301R LSU4.9)",
      1365, 1750, conn={'1': ('label', 'O2B_VM'), '2': ('label', 'O2B_UN')})
place(f"{LIB}:C_V", "C72", "2.2nF Nernst node filter to GND (AEC-Q200)", 1390, 1750,
      conn={'1': ('label', 'O2B_UN'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R73", "4k7 US series R to UP node (AEC-Q200)", 1340, 1770,
      conn={'1': ('label', 'O2B_US'), '2': ('label', 'O2B_UP')})
place(f"{LIB}:MOSFET_N", "Q17", "PMV37ENEA automotive (AEC-Q101), O2 bank-2 heater driver",
      1500, 1690,
      conn={'2': ('pwr', 'GND'),
            '3': ('label', 'HTR2_DRAIN'),
            '1': ('label', 'HTR2_GATE', 7.62)})
place(f"{LIB}:R_V", "R74", "1k DIAHD sense (AEC-Q200)", 1540, 1690,
      conn={'1': ('label', 'HTR2_DRAIN'), '2': ('label', 'HTR2_DRAIN_SENSE')})
place(f"{LIB}:R_V", "R75", "1k heater-2 gate resistor (AEC-Q200)", 1500, 1655,
      conn={'1': ('label', 'HTR2_CTRL'), '2': ('label', 'HTR2_GATE')})

# AD8495ARZ, 8-lead MSOP - real pinout read directly off Analog Devices'
# own datasheet (AD8494/8495/8496/8497, Rev. C, "Pin Configuration and
# Function Descriptions"): 1=-IN, 2=REF, 3=-VS, 4=NC, 5=SENSE, 6=OUT,
# 7=+VS, 8=+IN.
register_symbol(f"{LIB}:AD8495", "U", "TBD", "Package_SO:MSOP-8_3x3mm_P0.65mm",
                {'L': [P(1, "-IN", "input"), P(2, "REF", "input"),
                       P(3, "-VS", "power_in"), P(4, "NC", "no_connect")],
                 'R': [P(8, "+IN", "input"), P(7, "+VS", "power_in"),
                       P(6, "OUT", "output"), P(5, "SENSE", "input")]},
                datasheet="https://www.analog.com/media/en/technical-documentation/data-sheets/ad8494_8495_8496_8497.pdf")

# NXP/Freescale MC33926, 32-pin PQFN - real, complete pinout read directly
# off the Rev. 10.0 datasheet Table 2 ("33926 Pin Definitions"). Footprint
# is the closest standard KiCad QFN-32/5x5mm/0.5mm-pitch match; the real
# part's exact exposed-pad size was not independently re-measured against
# the vendor's own mechanical drawing - confirm before fab, same caveat
# already carried by this project's other close-but-unconfirmed footprint
# matches (Q1, the IGBTs).
register_symbol(f"{LIB}:MC33926", "U", "TBD",
                "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.1x3.1mm",
                {'L': [P(1, "IN2", "input"), P(2, "IN1", "input"),
                       P(3, "SLEW", "input"), P(4, "VPWR", "power_in"),
                       P(5, "AGND", "power_in"), P(6, "VPWR", "power_in"),
                       P(7, "INV", "input"), P(8, "FB", "output")],
                 'B': [P(9, "NC", "no_connect"), P(10, "EN", "input"),
                       P(11, "VPWR", "power_in"), P(12, "OUT1", "output"),
                       P(13, "OUT1", "output"), P(14, "OUT1", "output"),
                       P(15, "OUT1", "output"), P(16, "D2", "input")],
                 'R': [P(17, "NC", "no_connect"), P(18, "PGND", "power_in"),
                       P(19, "PGND", "power_in"), P(20, "PGND", "power_in"),
                       P(21, "SF", "open_collector"), P(22, "PGND", "power_in"),
                       P(23, "PGND", "power_in"), P(24, "PGND", "power_in")],
                 'T': [P(25, "NC", "no_connect"), P(26, "D1", "input"),
                       P(27, "OUT2", "output"), P(28, "OUT2", "output"),
                       P(29, "OUT2", "output"), P(30, "OUT2", "output"),
                       P(31, "VPWR", "power_in"), P(32, "CCP", "output")]},
                datasheet="https://www.nxp.com/docs/en/data-sheet/MC33926.pdf")

# ---------------------------------------------------------------------------
# BOOST / EGT / FLEX-FUEL / ETC EXPANSION - closes the remaining real gaps
# from the design review (boost control, exhaust gas temperature, flex-fuel,
# electronic throttle). Real, datasheet-verified parts throughout: the
# AD8495 EGT amp and MC33926 ETC H-bridge are BOTH new to this project
# (everything in the earlier expansion reused parts already in use
# elsewhere) - both pinouts were pulled from their real datasheets and
# cross-checked (MC33926's FB sense resistor value, 270R, comes straight
# from NXP's own application note AN5212, not a guess).
# ---------------------------------------------------------------------------
section_text("EXPANSION 2: BOOST CONTROL + EGT + FLEX-FUEL + ELECTRONIC THROTTLE (ETC)",
             30, 1850)

# --- boost control solenoid -------------------------------------------------
# Same MOSFET + Schottky-flyback pattern as VVT/idle/fuel-pump - a PWM
# low-side driver for a wastegate boost-control solenoid. Deliberately does
# NOT add a second, dedicated boost-pressure ADC input: this board's real
# MAP sensor (MAP_SIG, already wired) already covers boost pressure duty
# for a forced-induction engine, provided a suitably-rated (3 bar+
# absolute) MAP sensor is fitted - the same sensor a turbocharged engine
# already needs for load calculation, not a second part. Adding a
# redundant pressure input here would be filler, not a real requirement.
place(f"{LIB}:MOSFET_N", "Q18", "PMV37ENEA automotive (AEC-Q101), boost solenoid driver",
      30, 1980,
      conn={'2': ('pwr', 'GND'),
            '3': ('label', 'BOOST_OUT'),
            '1': ('label', 'BOOST_GATE', 7.62)})
place(f"{LIB}:R_V", "R76", "1k boost solenoid gate resistor (AEC-Q200)", 30, 1889,
      conn={'1': ('label', 'BOOST_CTRL'), '2': ('label', 'BOOST_GATE')})
place(f"{LIB}:D_FLYBACK", "D7", "PMEG4010BEA automotive (AEC-Q101), boost solenoid flyback",
      134, 2045, conn={'1': ('label', 'BOOST_OUT'), '2': ('label', 'VBATT_SW')})

# --- exhaust gas temperature (EGT) ------------------------------------------
# Analog Devices AD8495ARZ, 8-lead MSOP, real pinout read directly off the
# ADI datasheet (Rev. C): 1=-IN, 2=REF, 3=-VS, 4=NC, 5=SENSE, 6=OUT,
# 7=+VS, 8=+IN. Real "basic connection" per the datasheet's own
# application circuit: REF and -VS both to GND (single-supply, 0C
# reference), SENSE tied to OUT (direct measurement mode, not setpoint
# mode), NC left open.
#
# HONEST FLAG: no automotive (AEC-Q100) qualification found anywhere in
# ADI's own datasheet for this part, despite "Exhaust gas temperature
# sensing" being explicitly listed as an application - it's an
# industrial-grade part, not a qualified one. It is the real part the
# aftermarket EFI industry actually uses for exactly this (Haltech,
# DIYAutotune, Bosphorus Innovations EGT amplifiers are all built around
# this same chip), so it's used here as the honest real answer rather
# than inventing a fictitious "automotive EGT amp" that doesn't exist -
# but flagged, not silently presented as AEC-Q100 like the rest of this
# board's active parts.
#
# REAL CATCH, not obvious from the part alone: this chip's 5mV/C output
# needs a genuine 5V supply to cover a useful EGT range (5V / 5mV/C =
# 1000C full scale; on 3.3V it would saturate around 660C, too low for
# real exhaust gas temperatures which commonly reach 700-950C). But the
# MCU's real ADC input is a 3.3V-domain pin (same VDD_HV convention
# already established for the crank/cam VR pull-ups) - feeding a
# 0-4.9V-capable output directly into it would overrange the ADC input.
# R77/R78 form a real 2:1 divider (10k/20k, factor 0.667) that maps the
# amplifier's real ~1000C full-scale output (~5.0V) to ~3.33V, just under
# the ADC rail, with the known 0.667 ratio to be undone in firmware.
place(f"{LIB}:AD8495", "U19", "AD8495ARZ EGT amp (industrial grade - see registration note)",
      654, 1980,
      conn={'1': ('label', 'EGT_TC_M'), '8': ('label', 'EGT_TC_P', 7.62),
            '2': ('pwr', 'GND'), '3': ('pwr', 'GND'), '4': ('nc',),
            '7': ('pwr', '+5V', 5.08),
            '6': ('label', 'EGT_OUT'), '5': ('label', 'EGT_OUT')})
place(f"{LIB}:R_V", "R77", "10k EGT divider hi (AEC-Q200)", 784, 1980,
      conn={'1': ('label', 'EGT_OUT'), '2': ('label', 'EGT_ADC')})
place(f"{LIB}:R_V", "R78", "20k EGT divider lo (AEC-Q200)", 784, 2032,
      conn={'1': ('label', 'EGT_ADC'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C73", "10nF EGT ADC filter (AEC-Q200)", 862, 2006,
      conn={'1': ('label', 'EGT_ADC'), '2': ('pwr', 'GND')})

# --- flex-fuel sensor input --------------------------------------------------
# Real GM-style flex-fuel sensor: 3-wire (+12V, GND, open-collector
# signal whose FREQUENCY encodes ethanol % and DUTY CYCLE encodes fuel
# temperature). No new IC needed - the sensor only ever PULLS the signal
# line down, it never drives it high, so referencing the pull-up to
# +3V3 (not +12V, not +5V) keeps the line entirely within the MCU pin's
# own real voltage domain - the same real reasoning already applied to
# the MAX9924 COUT pull-ups, reused here rather than re-derived.
place(f"{LIB}:R_V", "R79", "1k flex-fuel series protection (AEC-Q200)", 1148, 1980,
      conn={'1': ('label', 'FLEXFUEL_SIG_RAW'), '2': ('label', 'FLEXFUEL_SIG')})
place(f"{LIB}:R_V", "R80", "4k7 flex-fuel pull-up to +3V3 (AEC-Q200)", 1252, 1928,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'FLEXFUEL_SIG')})
place(f"{LIB}:C_V", "C74", "1nF flex-fuel filter (AEC-Q200)", 1252, 2032,
      conn={'1': ('label', 'FLEXFUEL_SIG'), '2': ('pwr', 'GND')})

# --- electronic throttle control (ETC) --------------------------------------
# NXP/Freescale MC33926, 32-pin PQFN, "designed primarily for automotive
# electronic throttle control" per its own datasheet title. Real,
# complete pinout read directly off the Rev. 10.0 datasheet (Table 2,
# Pin Definitions) - every pin below is the real chip pin, not inferred:
#   1 IN2, 2 IN1, 3 SLEW, 4/6/11/31 VPWR, 5+EP AGND, 7 INV, 8 FB,
#   9/17/25 NC, 10 EN, 12-15 OUT1, 16 D2 (active LOW disable),
#   18-20/22-24 PGND, 21 SF (open-drain fault, active LOW),
#   26 D1 (active HIGH disable - note D1/D2 are asymmetric polarity,
#   confirmed from the datasheet's own text, an easy real mistake to
#   make wiring this from memory), 27-30 OUT2, 32 CCP.
#
# HONEST FLAG: no explicit "AEC-Q100" qualification statement found in
# the datasheet text pulled for this part either, despite it being
# marketed specifically for automotive ETC and rated -40 to 125C - same
# treatment as U19 above, used as the real right part with the
# qualification gap flagged rather than assumed.
#
# SAFETY-CRITICAL NOTE: this hardware provides the real, necessary
# pieces - two independent hardware disable inputs (D1/D2) separate
# from the PWM control path, an enable, a fault flag, and current-sense
# feedback - but the actual safety logic (comparing APP1 vs APP2 and
# TPS1 vs TPS2 for plausibility, deciding when to assert D1/D2, limp-home
# behavior) is entirely a FIRMWARE responsibility. This board does not
# and cannot implement ISO 26262-style throttle safety in hardware alone
# - flagged honestly, not hidden, matching this project's own README.
place(f"{LIB}:MC33926", "U20", "MC33926 ETC H-bridge (industrial grade - see registration note)",
      1642, 1980,
      conn={
          '2': ('label', 'ETC_IN1', 7.62), '1': ('label', 'ETC_IN2', 7.62),
          '3': ('pwr', 'GND', 5.08),
          '4': ('label', 'VBATT_SW', 7.62), '6': ('label', 'VBATT_SW', 10.16),
          '11': ('label', 'VBATT_SW'), '31': ('label', 'VBATT_SW'),
          '5': ('pwr', 'GND', 7.62),
          '7': ('pwr', 'GND', 10.16),
          '8': ('label', 'ETC_IFB_ADC', 12.7),
          '9': ('nc',), '17': ('nc',), '25': ('nc',),
          '10': ('label', 'ETC_EN', 7.62),
          '12': ('label', 'ETC_MOTOR_A'), '13': ('label', 'ETC_MOTOR_A'),
          '14': ('label', 'ETC_MOTOR_A'), '15': ('label', 'ETC_MOTOR_A'),
          '16': ('label', 'ETC_D2', 7.62),
          '18': ('pwr', 'GND'), '19': ('pwr', 'GND'), '20': ('pwr', 'GND'),
          '22': ('pwr', 'GND'), '23': ('pwr', 'GND'), '24': ('pwr', 'GND'),
          '21': ('label', 'ETC_SF_N', 7.62),
          '26': ('label', 'ETC_D1', 7.62),
          '27': ('label', 'ETC_MOTOR_B'), '28': ('label', 'ETC_MOTOR_B'),
          '29': ('label', 'ETC_MOTOR_B'), '30': ('label', 'ETC_MOTOR_B'),
          '32': ('label', 'ETC_CCP'),
      })
# 270R FB-to-GND sense resistor: the real value from NXP's own AN5212
# ("Improving feedback current accuracy"), not a guess - FB sources a
# ground-referenced 0.24% of H-bridge load current, and 270R converts
# that to a 0-3.24V ADC-friendly voltage at the chip's real 5A rating.
place(f"{LIB}:R_V", "R81", "270R ETC current-feedback sense (AEC-Q200) - real NXP AN5212 value",
      1642, 2084, conn={'1': ('label', 'ETC_IFB_ADC'), '2': ('pwr', 'GND')})
# CCP charge-pump reservoir: 33nF, the exact value from the datasheet's
# own electrical characteristics table ("Charge Pump Voltage (CP
# Capacitor = 33 nF)"), required for correct operation per the datasheet.
place(f"{LIB}:C_V", "C75", "33nF ETC charge-pump cap (AEC-Q200) - real datasheet value",
      1746, 1980, conn={'1': ('label', 'ETC_CCP'), '2': ('label', 'VBATT_SW')})
# SF is open-drain, needs an external pull-up - referenced to +3V3 (not
# the datasheet's allowed-up-to-7V max) to stay inside the MCU pin's own
# real voltage domain, same reasoning as the flex-fuel pull-up above.
place(f"{LIB}:R_V", "R82", "10k ETC SF pull-up to +3V3 (AEC-Q200)", 1824, 1928,
      conn={'1': ('pwr', '+3V3'), '2': ('label', 'ETC_SF_N')})
# SLEW (pin 3) and INV (pin 7) are already tied directly to GND in the
# U20 conn dict above (real datasheet default: slow slew, non-inverted)
# - a defined logic level beats relying on an internal weak pull, but a
# direct wire beats a redundant 0R resistor doing the same job twice.
place(f"{LIB}:C_V", "C76", "10uF ETC VPWR bulk decouple (AEC-Q200)", 1564, 1980,
      conn={'1': ('label', 'VBATT_SW'), '2': ('pwr', 'GND')})
place(f"{LIB}:C_V", "C77", "100nF ETC VPWR HF decouple (AEC-Q200)", 1564, 2084,
      conn={'1': ('label', 'VBATT_SW'), '2': ('pwr', 'GND')})

# --- ETC redundant sensor pair x2 (accelerator pedal + throttle body) ------
# Real ETC architecture: TWO independent potentiometers on the pedal
# (APP1/APP2) and TWO on the throttle body itself (TPS1/TPS2), so
# firmware can cross-check each pair for a plausibility fault rather
# than trusting a single point of failure - this is what makes it
# "electronic throttle CONTROL" and not just "a motor on a throttle
# body". All four are simple external ratiometric sensors; same
# RC-filter pattern already used for MAP/TPS (1k/22nF).
#
# NOTE ON HARNESS SPLIT: TPS1/TPS2 (throttle body, physically at the
# engine) go out on J4 with the injector/ignition/crank/cam signals;
# APP1/APP2 (accelerator pedal, physically in the cabin) go out on J5
# alongside the other sensor wiring. In a real vehicle the pedal is
# normally its own separate harness run - this board only has two
# harness connectors total, so APP1/APP2 riding on the "sensor+CAN"
# connector is a real, honestly-noted simplification, not a hidden one.
place(f"{LIB}:R_V", "R87", "1k APP1 RC filter (AEC-Q200)", 2162, 1980,
      conn={'1': ('label', 'APP1_SIG'), '2': ('label', 'APP1_ADC')})
place(f"{LIB}:C_V", "C78", "22nF APP1 RC filter (AEC-Q200)", 2162, 2032,
      conn={'1': ('label', 'APP1_ADC'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R88", "1k APP2 RC filter (AEC-Q200)", 2266, 1980,
      conn={'1': ('label', 'APP2_SIG'), '2': ('label', 'APP2_ADC')})
place(f"{LIB}:C_V", "C79", "22nF APP2 RC filter (AEC-Q200)", 2266, 2032,
      conn={'1': ('label', 'APP2_ADC'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R89", "1k TPS1 RC filter (AEC-Q200)", 2370, 1980,
      conn={'1': ('label', 'TPS1_SIG'), '2': ('label', 'TPS1_ADC')})
place(f"{LIB}:C_V", "C80", "22nF TPS1 RC filter (AEC-Q200)", 2370, 2032,
      conn={'1': ('label', 'TPS1_ADC'), '2': ('pwr', 'GND')})
place(f"{LIB}:R_V", "R90", "1k TPS2 RC filter (AEC-Q200)", 2474, 1980,
      conn={'1': ('label', 'TPS2_SIG'), '2': ('label', 'TPS2_ADC')})
place(f"{LIB}:C_V", "C81", "22nF TPS2 RC filter (AEC-Q200)", 2474, 2032,
      conn={'1': ('label', 'TPS2_ADC'), '2': ('pwr', 'GND')})

section_text("CONNECTORS: 2x AMPSEAL HARNESS + BLE ANTENNA (PLAN STEP 9)", 30, 2200)

# --- J4: ENGINE HARNESS (power, injectors, ignition, crank/cam) ---
place(f"{LIB}:CONN_AMPSEAL35", "J4", "TE AMPSEAL 776180-1 (35-pos) - engine harness", 100, 2230,
      conn={
          '1': ('pwr', 'VIN', 5.08), '2': ('pwr', 'GND', 5.08), '3': ('pwr', 'GND', 2.54),
          '4': ('label', 'VBATT_INJ', 5.08),
          **{str(4 + n): ('label', f'INJ{n}_LO', 5.08) for n in range(1, 9)},
          '13': ('label', 'VBATT_IGN', 5.08),
          **{str(13 + n): ('label', f'IGN{n}_COIL', 5.08) for n in range(1, 9)},
          '22': ('label', 'CRANK_VR_HI', 5.08), '23': ('label', 'CRANK_VR_LO', 5.08),
          '24': ('label', 'CAM_VR_HI', 5.08), '25': ('label', 'CAM_VR_LO', 5.08),
          # second (exhaust) cam sensor - crank + 2 cams, for DOHC engines
          # phasing intake and exhaust independently
          '26': ('label', 'CAM2_VR_HI', 5.08), '27': ('label', 'CAM2_VR_LO', 5.08),
          # ETC (throttle body, physically at the engine - shares this
          # harness rather than the pedal's) + boost solenoid. +5V_SENSOR
          # is a dedicated regulated-5V feed OUT to the throttle-body pots
          # - TPS1/TPS2 are passive dividers with no on-board conditioning,
          # so unlike the crank/cam VR sensors (whose own +5V stays
          # entirely on-board, feeding only the MAX9924) they need real
          # excitation voltage sent out through the harness.
          '28': ('label', 'TPS1_SIG', 5.08), '29': ('label', 'TPS2_SIG', 5.08),
          '30': ('label', 'ETC_MOTOR_A', 5.08), '31': ('label', 'ETC_MOTOR_B', 5.08),
          '32': ('label', 'BOOST_OUT', 5.08),
          '33': ('pwr', '+5V', 7.62),
          **{str(n): ('nc',) for n in range(34, 36)},
      })

# --- J5: SENSOR + CAN HARNESS ---
place(f"{LIB}:CONN_AMPSEAL35", "J5", "TE AMPSEAL 776180-1 (35-pos) - sensor+CAN harness", 100, 2350,
      conn={
          '1': ('pwr', '+5V', 5.08), '2': ('pwr', 'GND', 7.62),
          '3': ('label', 'MAP_SIG', 5.08), '4': ('label', 'TPS_SIG', 5.08),
          '5': ('label', 'IAT_ADC', 5.08), '6': ('label', 'CLT_ADC', 5.08),
          '7': ('label', 'KNOCK_SIG', 5.08),
          # REAL LSU4.x sensor wires only. A Bosch LSU4.2/4.9 has exactly
          # 6 real wires: Nernst (UN), virtual-ground common (VM), pump
          # current (IP), trim/calibration (IA), and the heater pair -
          # confirmed off Bosch's own application circuit, where the LSU
          # block has precisely these connections leaving it.
          #
          # REAL BUG fixed here: this harness previously carried O2_US
          # and O2_UP out to the connector as if they were sensor wires.
          # They are NOT - both are internal CJ125 bias nodes (US reaches
          # the UP node through a 4k7; UP is the pump-drive bias node
          # sitting between a 100k to the Nernst node and a 470k to IP).
          # Wiring them to the harness would have run two internal bias
          # nodes out through metres of engine-bay cable, and left the
          # sensor's actual VM common wire with nowhere to land.
          '8': ('label', 'O2_UN', 5.08), '9': ('label', 'O2_IP', 5.08),
          '10': ('label', 'O2_IA', 5.08), '11': ('label', 'O2_VM', 5.08),
          '12': ('label', 'OILP_SIG', 5.08),
          '13': ('label', 'HTR_DRAIN', 5.08), '14': ('label', 'VBATT_SW', 5.08),
          '15': ('label', 'CAN0_H', 5.08), '16': ('label', 'CAN0_L', 5.08),
          '17': ('label', 'CAN1_H', 5.08), '18': ('label', 'CAN1_L', 5.08),
          # expansion. Note VBATT_SW (pin 14) doubles as the common +
          # feed for every solenoid/relay driven by the *_OUT low-side
          # pins below - the coils are external, so only their switched
          # low side comes back to the board.
          '19': ('label', 'FUELP_SIG', 5.08),
          '20': ('label', 'KNOCK2_SIG', 5.08),
          '21': ('label', 'O2B_UN', 5.08), '22': ('label', 'O2B_VM', 5.08),
          '23': ('label', 'O2B_IP', 5.08), '24': ('label', 'O2B_IA', 5.08),
          '25': ('label', 'HTR2_DRAIN', 5.08),
          '26': ('label', 'VVT1_OUT', 5.08), '27': ('label', 'VVT2_OUT', 5.08),
          '28': ('label', 'IDLE_OUT', 5.08), '29': ('label', 'FPUMP_OUT', 5.08),
          '30': ('label', 'TACH_OUT', 5.08),
          # ETC accelerator pedal (a real, honestly-noted simplification -
          # see the ETC section's own note: the pedal is normally its own
          # separate cabin harness in a real vehicle, but this board only
          # has two connectors total) + EGT probe + flex-fuel sensor.
          # APP1/APP2 share this connector's existing +5V/GND (pins 1/2)
          # for excitation, same as MAP/TPS/IAT/CLT already do.
          '31': ('label', 'APP1_SIG', 5.08), '32': ('label', 'APP2_SIG', 5.08),
          '33': ('label', 'EGT_TC_P', 5.08), '34': ('label', 'EGT_TC_M', 5.08),
          '35': ('label', 'FLEXFUEL_SIG_RAW', 5.08),
      })

# --- J6: BLE external antenna ---
place(f"{LIB}:CONN_UFL", "J6", "U.FL external BLE antenna connector", 260, 2400,
      conn={'1': ('label', 'BLE_ANT', 5.08), '2': ('pwr', 'GND', 5.08)})

section_text("ECU - STEPS 2-9: FULL BOARD (PCB LAYOUT IS STEP 10)", 30, 15)

NOTE_LINES = [
    "NOTES:",
    "1. Steps 2-9 of the approved build order: power input/protection/",
    "   regulation (2), the MPC5606B MCU core (3), 2x L9779WD-SPI injector/",
    "   ignition drivers + 8x external ignition IGBT (4), the sensor front",
    "   end (5), 2x CAN bus (6), USB-C (FT4232HA) + BLE (CC2640R2F-Q1)",
    "   programming paths with real switch-based arbitration (7+8), and 2x",
    "   AMPSEAL harness connectors + a BLE antenna connector (9) - every",
    "   real external interface on this board now reaches a physical pin.",
    "   Only PCB layout/routing/DRC/BOM (step 10) remains.",
    "2. Q1/U2/D1's shared protection stage is sized for the WHOLE board's",
    "   current (up to F1's 30A) - a deliberate departure from Manifold, which",
    "   only ever protected a small logic load. The logic branch (F2/U3/U4)",
    "   taps the already-protected VIN_PROT rail rather than duplicating its",
    "   own reverse-battery-protection circuit.",
    "3. Logic supply is NOT relay-gated - it's live whenever a battery is",
    "   connected, so USB-C/BLE firmware flashing and telemetry work with the",
    "   engine/ignition off. The injector/ignition power rails ARE relay-",
    "   gated (K1) so they can't be live just because a battery is connected.",
    "4. Real current budget (8-cylinder, from published aftermarket-EFI",
    "   installation guidance): injectors ~8A combined, ignition coils",
    "   ~14-20A combined, ECU logic ~1A - not simply additive since not",
    "   everything fires simultaneously. F1=30A (shared main), F3=15A",
    "   (injector), F4=25A (ignition), all real margin over typical combined",
    "   draw, not a worst-case sum.",
    "5. EXPECTED ERC exceptions this session (5 total, all genuine tool",
    "   limitations or forward-looking net stubs, same categories Manifold",
    "   also had to document - not real wiring bugs):",
    "   - isolated_pin_label x3: RELAY_CTRL (Q2's gate resistor R3, awaits",
    "     the MCU GPIO in step 3), VBATT_INJ and VBATT_IGN (await injector",
    "     positives/ignition coil primaries in steps 4/9) - each a single",
    "     label marking where this session's circuit currently ends.",
    "   - power_pin_not_driven x2: U3(buck)'s VIN and one +5V power symbol -",
    "     their real source is on the far side of a fuse (F2) and a MOSFET",
    "     (Q1)/inductor (L1) respectively; ERC can't trace continuity",
    "     through passives/FETs, exactly like Manifold's own +5V/VDDA/U2-VIN",
    "     exceptions.",
    "6. Q1 is Vishay SQM40020EL_GE3 (D2PAK, 100A/40V) - real part chosen for",
    "   RDSon/current headroom, but its D2PAK pin-to-terminal mapping (used",
    "   here) has NOT been independently re-verified against the actual",
    "   datasheet pin table the way Manifold's small-MOSFET pinout was -",
    "   confirm before fab.",
    "7. K1's exact manufacturer part number is still TBD (any ISO 7588-",
    "   compliant mini relay drops into the same footprint) - pin numbering",
    "   (85/86/30/87/87a) is the real ISO 7588 standard, not a guess.",
    "8. U1's 144-LQFP pin table is REAL and cross-verified against two",
    "   independent NXP documents (datasheet Table 2 + reference manual, the",
    "   latter recovered via the Wayback Machine since NXP's own PCN-",
    "   attachment URL 404s live) - not a placeholder, unlike some earlier",
    "   Manifold sessions had to accept temporarily. The 113 real pins not",
    "   yet claimed by a subsystem are marked no_connect with their real",
    "   pin numbers (RESERVED_N), split across all 4 symbol sides purely to",
    "   keep the generated symbol's aspect ratio sane.",
    "9. FAB/ABS (boot mode select) and LIN0_TX/LIN0_RX (serial bootloader)",
    "   are intentionally dangling labels this session - step 7 (USB-C +",
    "   FT4232HA) will wire them into the actual programming path. Expected",
    "   isolated_pin_label (+ a redundant pin_not_driven on LIN0_RX, same",
    "   root cause) hits, same pattern as step 2's RELAY_CTRL.",
    "10. VDD_LV (MCU's internally-regulated core rail, ~1.28V, generated",
    "    on-chip from VDD_BV - NOT externally supplied) and VDD_HV_ADC0/1",
    "    (ferrite-isolated from +3V3, same as Manifold's own VDDA) both show",
    "    real, expected power_pin_not_driven exceptions - decoupling/",
    "    filtering only, or ERC can't trace through a passive, same",
    "    categories Manifold already established.",
    "11. J1's pinout (VCC/GND/TCK/TMS/TDI/TDO/RESET/GND) is OUR OWN",
    "    assignment on a generic 2.54mm header - there's no external",
    "    standard connector pinout being matched here, unlike the MCU's own",
    "    real silicon pins. TDO is typed 'input' on J1 (it RECEIVES the",
    "    MCU's real output) - TCK/TMS/TDI stay 'input' on BOTH ends (they",
    "    match the MCU's real electrical direction) and are genuinely driven",
    "    off-sheet by an external JTAG probe - the same documented, accepted",
    "    exception category as Manifold's own J2 SWCLK.",
    "12. U5/U6 (MC33810) real architecture: OUT0-3 are the chip's OWN",
    "    integrated injector switches (real injector current flows through",
    "    the chip); GD0-3 are only PRE-drivers for the 8 external ignition",
    "    IGBTs (Q3-Q10, ON Semi FGP3040G2) added this step - real ignition",
    "    current never flows through the MC33810 itself. FBx senses each",
    "    IGBT's own collector (shares a net with the coil-primary",
    "    connection), not the gate.",
    "13. RSP/RSN is ONE shared current-sense comparator PER CHIP (not per",
    "    channel) - confirmed real via the datasheet's own 'MAXI Trip Point",
    "    During Overlapping Dwell' spec, which only makes sense if multiple",
    "    channels' emitter currents genuinely combine through one shared",
    "    sense point. All 4 of a chip's IGBT emitters tie to one common",
    "    node -> one external sense resistor -> GND; overcurrent protection",
    "    (MAXI autonomously latches OFF the GPGD outputs inside the chip)",
    "    works whether or not NOMI/MAXI/SPKDUR are ever read externally -",
    "    they're deliberately marked no_connect this session, a real design",
    "    choice (not a placeholder), since the safety function doesn't",
    "    depend on them.",
    "14. SPI SCLK/SI/SO are shared between both chips (separate CS_0/CS_1",
    "    per chip) - confirmed SAFE from the datasheet's own text ('With CS",
    "    in a logic high state... the SO pin is tri-state'), the same class",
    "    of check that caught the real JTAG TDO pin_to_pin bug in step 3 -",
    "    this time confirmed BEFORE wiring, not caught after by ERC.",
    "15. RESOLVED (follow-up session, same day): CS_0/CS_1, DRV_OUTEN, all",
    "    8x INJn_CTRL/IGNn_CTRL (DIN/GIN parallel real-time control), and",
    "    SPI_SCLK/SI/SO are now wired to real MPC5606B pins. CORRECTION",
    "    found during this research: the MCU does NOT have eTPU2 as earlier",
    "    planning assumed - the real reference manual states it implements",
    "    'a scaled-down version of the eMIOS module' instead (Rev.2 Section",
    "    2.4.9). 16 real eMIOS channels (E0UC[3,4,5,6,7,10,11,12,13,14,15,30,31]",
    "    + E1UC[28,29,31], pins 3,4,31,32,36,37,83,85,87,104,107,108,131,",
    "    141,142,143) drive the 16 real-time firing lines; real DSPI_0",
    "    (SCK_0=pin40, SIN_0=pin45, SOUT_0=pin44) drives the shared SPI bus;",
    "    CS_0/1, DRV_OUTEN, and RELAY_CTRL (step 2's original stub) use 4",
    "    arbitrary reserved-pool GPIOs (pins 5,6,9,10) since none of these",
    "    four need a specific peripheral function, just a bit-banged digital",
    "    output. Both peripherals use this part's real SIUL/PCR pin-mux",
    "    scheme (one alternate-function-select field per pin). Source: same",
    "    two documents as the original MCU pin research, this time reading",
    "    Table 2's per-pin rows as rendered images (pdftotext mangled the",
    "    multi-column table too badly to trust, same lesson as before).",
    "    INJn_LO/IGNn_COIL (injector/coil-side nets) still stay dangling",
    "    until the harness connectors exist (step 9) - that's genuinely",
    "    unrelated to MCU pin-mux and needs its own connector research.",
    "16. Q3-Q10's TO-220 pin assignment (1=G/2=C/3=E) matches this part",
    "    FAMILY's typical real convention but has NOT been independently",
    "    re-verified against FGP3040G2's own datasheet pin table the same",
    "    rigor as the MC33810/MPC5606B pin tables received - confirm before",
    "    fab, same flag already carried for Q1's D2PAK.",
    "17. Two more EXPECTED exceptions, both specifically verified against",
    "    real datasheet text BEFORE wiring (not discovered after the fact):",
    "    - power_pin_not_driven on U5/U6's VPWR: same root cause as U3's",
    "      VIN and the +5V exception (K1's relay contacts are a mechanical",
    "      pass-through, typed 'passive' - ERC can't trace a driver through",
    "      them, same as it can't through a fuse/MOSFET/inductor).",
    "    - pin_to_pin on U5/U6's shared SO net: this is the SAME class of",
    "      issue that caught a REAL bug on the JTAG TDO pin in step 3, but",
    "      here it's confirmed SAFE, not a bug - the datasheet's own text",
    "      ('With CS in a logic high state... the SO pin is tri-state')",
    "      means only one chip ever actually drives SO at a time. Static",
    "      ERC has no way to model tri-state/time-multiplexed bus sharing,",
    "      so it flags two 'output'-typed pins on one net regardless -",
    "      retyping either pin would misrepresent its real electrical",
    "      behavior, so this exception is accepted, not fixed.",
    "18. STEP 5 (sensor front end): U7/U8 = MAX9924 crank/cam VR interfaces",
    "    (real 10-pin pinout, AEC-Q100) - open-drain COUT pulled to +3V3,",
    "    not VCC(+5V), for clean 3.3V logic into the MCU regardless of the",
    "    sensor-side supply. U9 = Bosch CJ125 wideband O2 controller (real",
    "    24-pin pinout). ORIGINALLY this session only reached a short",
    "    'Product Information' brief, not the complete app-circuit doc, so",
    "    the external passive network's component VALUES were typical/",
    "    placeholder AND (found only in a later session, see note 21) its",
    "    TOPOLOGY had real bugs too - CJ_VM/CJ_RS were genuinely dangling,",
    "    RM/CM were wired to each other instead of to the real shared",
    "    Nernst-cell node, and the UP/IP/IA/UN compensation network (real:",
    "    a 470k VM-to-IP bridge, an 82.5R+2.2nF Nernst-to-VM bridge, a 100k",
    "    UN series resistor, a 4k7 US series resistor) was approximated by",
    "    two resistors between the wrong pairs of nodes entirely. Fixed in",
    "    that later session once the complete Bosch datasheet (with its own",
    "    real 'Application circuit' diagram) was actually pulled - see note",
    "    21 for the full account. Q11 = external heater MOSFET (CJ125's",
    "    DIAHG/DIAHD only diagnose it, don't drive it - real PWM control is",
    "    the MCU's job, via R33). U10 = TLV2372-Q1 knock-sensor op-amp",
    "    front end (real pinout, channel 2 used as a buffered mid-supply",
    "    reference, not filler). MAP/TPS use standard-practice RC filtering",
    "    (1k + ~22nF); IAT/CLT use NTC pull-up dividers - the sensor's own",
    "    thermistor element is entirely off-board, reached via the step-9",
    "    harness connector, same as every other sensor's stub nets.",
    "19. CJ125's SO pin ALSO shares the MC33810s' SPI bus (separate CS_2) -",
    "    checked before wiring like the MC33810 pair, but with slightly",
    "    LOWER confidence: no single sentence as explicit as MC33810's own",
    "    tri-state confirmation was found, only a real dedicated /SS pin +",
    "    a timing-diagram legend documenting a tristate ('Z') output state",
    "    - strong evidence, not airtight proof. Flagged honestly at that",
    "    confidence level rather than claimed as fully verified.",
    "19b. A CAUTIONARY ONE, worth keeping: an earlier pass this session",
    "    briefly introduced a pin_to_pin between U9's VM (pin 18) and UP",
    "    (pin 20) and DOCUMENTED IT AS AN ACCEPTED TOOL LIMITATION, on the",
    "    belief Bosch's app circuit tied them with a bare wire. It did not",
    "    - they are separate nodes (see note 21), and ERC was correctly",
    "    reporting a real short between two driven outputs. The lesson is",
    "    that 'expected exception' is a claim needing the same evidence as",
    "    any other: every entry on this list should be re-justified when",
    "    the circuit around it changes, not inherited. Re-verifying against",
    "    the real figure removed the violation rather than excusing it.",
    "20. Cam position capture (CAM_COUT) originally needed a second",
    "    independent real-time input-capture pin, and this step's research",
    "    offered pin 2 for it - but pin 2 turned out to be an alternate",
    "    route to the SAME internal eMIOS channel (E0UC[7]) already claimed",
    "    by pin 104 for INJ1_CTRL, so it was left as a genuine open stub",
    "    rather than silently aliasing with INJ1's firing signal. RESOLVED",
    "    in a later session: pulled the real MPC5606BK Data Sheet Rev. 5",
    "    (Table 2, Functional port pins) directly and confirmed PA[1]/pin 11",
    "    offers E0UC[1] as its own real, independent channel - checked the",
    "    WHOLE table for every other E0UC[1] mention first (same discipline",
    "    that caught the pin-2 trap), found exactly one alternate route",
    "    (PA[15]/pin 40, AF3) which is already used here via a DIFFERENT",
    "    alternate function on that pad (AF2 = SCK_0), so there's no actual",
    "    channel contention. CAM2 now wired: MAX9924 #2's COUT -> pin 11",
    "    (E0UC1_CAM2), giving independent intake+exhaust cam phase capture",
    "    for DOHC/VVT engines, same real hardware input-capture approach as",
    "    CRANK (not a software EIRQ interrupt, which would add real ISR-",
    "    latency jitter to the captured timestamp).",
    "21. RESOLVED in a later session: CJ_VM and CJ_RS were previously",
    "    genuinely dangling (no real external network had been found for",
    "    either). Pulled Bosch's own CJ125_Product_Info datasheet directly",
    "    (page 2, 'Application circuit (only proposal!)', rendered to an",
    "    image and read directly - pypdf's text extraction mangles this",
    "    figure) and found both are bare-wire connections on the real",
    "    reference circuit: VM ties directly to the same node as UP (now",
    "    merged into the O2_UP net), and RS ties directly to the Nernst",
    "    cell's own tap (now merged into O2_UN). Same pull also corrected",
    "    real value/topology mistakes elsewhere in this network (OSZ needs",
    "    a 10k resistor, not a 100nF cap; RF/CF/UR/UA/UB/VCC decouple",
    "    values; RM/CM's real shared node; the UN-series resistor and the",
    "    Nernst-to-VM-node bridge network were missing entirely) - see the",
    "    registration/component comments in the wideband O2 section for",
    "    the full per-component before/after.",
    "22. STEP 6 (CAN bus): U11/U12 = NXP TJA1043T transceivers (real 14-pin",
    "    SOIC, AEC-Q100), 2 fully independent real FlexCAN pairs - CAN0 on",
    "    FlexCAN_1 (TX=pin28/RX=pin27) and CAN1 on FlexCAN_4 (TX=pin117/",
    "    RX=pin116), both cross-verified by an independent research pass",
    "    AND a direct manual read of the rendered datasheet table landing",
    "    on the identical pins - high confidence. Six real FlexCAN modules",
    "    exist on this part; FlexCAN_0 was ruled out (shares pins 31/32",
    "    with already-claimed eMIOS INJ4/INJ5), CAN2/CAN3 each had at",
    "    least one leg blocked by an already-claimed pin, dropped in favor",
    "    of these two clean pairs. VBAT ties to VIN_PROT (always-on),",
    "    deliberately NOT VBATT_SW (relay-gated) - real automotive",
    "    practice, so a bus-wake event can revive the ECU with ignition",
    "    off. VIO ties to +3V3 (MCU's own logic level, no level shifter",
    "    needed - that's real what VIO is for). EN/STB_N go to MCU GPIO",
    "    for real firmware power-mode control rather than hard-wired",
    "    always-on. The split-termination network (2x 60R + cap via",
    "    SPLIT) IS the bus termination, not an extra component on top of",
    "    it - marked DNP-unless-bus-end-node, same real practice as any",
    "    multi-drop CAN node. New expected exception: U11/U12's VBAT adds",
    "    a 3rd instance of the same 'ERC can't trace through a passive'",
    "    category as U3's VIN and the +5V exception (K1's relay/Q1's",
    "    MOSFET again).",
    "23. REAL BUG caught and fixed this session, NOT by ERC: two separate",
    "    parts (a MAX9924 pull-up resistor and a CJ125 filter resistor)",
    "    both ended up named 'R15' - an f-string ref-number formula in the",
    "    step-5 MAX9924 loop collided with a later hardcoded ref. This",
    "    specific kicad-cli sch erc invocation did NOT flag it (duplicate_",
    "    reference is a real ERC rule, just didn't fire here) - found by",
    "    manually diffing reference designators against the generated",
    "    file. Fixed (renumbered), AND a permanent duplicate-reference",
    "    check was added to this script's own self-validation section so",
    "    manual diffing is never required again - see the 'Reference-",
    "    designator check OK' line in this script's own console output.",
    "24. STEPS 7+8 (USB-C + BLE programming, done together by design - they",
    "    share the MCU's bootloader UART/boot-select lines and need real",
    "    arbitration between them): U13=FT4232HA (real 18-of-64 pins",
    "    verified, a deliberately SIMPLIFIED/PARTIAL symbol unlike the",
    "    full-pin MCU/MC33810 treatment - this session's research targeted",
    "    only the pins this design uses, not the complete pin table).",
    "    U14=CC2640R2F-Q1 BLE SoC (real power/RF/oscillator/reset pins",
    "    verified; DIO pin assignments for UART/boot-control are",
    "    candidate/plausible per this session's research, NOT",
    "    independently pin-by-pin verified against the DIO crossbar table -",
    "    confirm against TI's SmartRF/LaunchPad board file before firmware",
    "    bring-up). U15=SN3257-Q1 (real, COMPLETE 16-pin table) arbitrates",
    "    exactly 4 shared signals (UART TX, UART RX, BOOT_FAB, BOOT_ABS) -",
    "    a genuine 1:1 fit, not a coincidence: this part was chosen",
    "    specifically because it has 4 channels. SEL is driven by a simple",
    "    VBUS-presence divider - wired path wins whenever USB-C is",
    "    plugged in, no MCU firmware decision needed; SEL's real internal",
    "    pulldown means wired is also the safe power-up default. This",
    "    session's arbitration wiring is what FINALLY resolves step 3's",
    "    original BOOT_FAB/BOOT_ABS/LIN0_TX/LIN0_RX stubs.",
    "25. Real, deliberate simplifications (not oversights): CC2640R2F-Q1's",
    "    optional 32.768kHz LF crystal is omitted (internal RCOSC_LF",
    "    substitutes, a real documented tradeoff); VDDS_DCDC ties directly",
    "    to VDDS rather than adding the optional DC/DC converter's",
    "    external inductor (efficiency optimization, not a functional",
    "    requirement); the BLE RF chain's balun is a real required",
    "    TOPOLOGY (differential radio -> balun -> single-ended antenna)",
    "    but its exact part/matching-network VALUES are placeholder,",
    "    pending TI's official reference-design BOM (not fetched this",
    "    session) - same 'topology real, values TBD' treatment as CJ125's",
    "    analog network in step 5. Hardware-forced MCU reset-into-",
    "    bootloader (independent of cooperating firmware) is NOT",
    "    implemented - neither bridge has a spare pin for it in this",
    "    session's partial pin registration; current mechanism relies on",
    "    the MCU's own firmware performing a software self-reset on",
    "    command over the already-arbitrated UART, which does not recover",
    "    a hung/crashed MCU - a real, honestly-flagged limitation.",
    "26. USB-C connector (J3) uses the public USB-IF Type-C FUNCTIONAL pin",
    "    assignment (GND/VBUS/CC1/CC2/D+/D-), NOT the exact physical pad",
    "    numbering of any specific real manufacturer part - cross-reference",
    "    the actual chosen connector's own pinout diagram before finalizing",
    "    the PCB footprint pin mapping, unlike every other connector in",
    "    this project which used real verified manufacturer numbering.",
    "27. NEW expected exceptions this session: J3's VBUS shows",
    "    power_pin_not_driven - genuinely expected, the exact same category",
    "    as VIN's own exception from step 2 (real source is off-board, at",
    "    the USB host). BLE_ANT and BLE_RST_CTL are new single-occurrence",
    "    isolated_pin_label stubs - BLE_ANT awaits a real antenna/connector",
    "    choice, BLE_RST_CTL is a registered-but-intentionally-unused DIO",
    "    pin (see note 25's reset-arbitration limitation).",
    "28. STEP 9 (connectors): J4/J5 reuse the EXACT same real TE AMPSEAL",
    "    776180-1 footprint already dimensionally verified in manifold-pcb",
    "    (copied directly, not re-verified from scratch) - real physical",
    "    pin numbering, OUR OWN signal assignment (same as how Manifold",
    "    assigned its own J1). J4 'engine harness' (25 of 35 pins used):",
    "    VIN/GND, VBATT_INJ + 8x INJn_LO, VBATT_IGN + 8x IGNn_COIL,",
    "    CRANK/CAM_VR_HI/LO. J5 'sensor+CAN harness' (18 of 35 pins used):",
    "    +5V/GND, MAP/TPS/IAT/CLT/KNOCK signals, 5x O2 sensor-cell pins,",
    "    the O2 heater pair, both CAN buses. Deliberately oversized",
    "    (2 identical 35-pin connectors for 25+18 real signals) for BOM",
    "    commonality/cost, not an oversight - a smaller connector could",
    "    replace either once the design is final. J6 = real bundled",
    "    Hirose U.FL connector for an EXTERNAL BLE antenna - a metal",
    "    enclosure (likely for this class of board) wouldn't let an",
    "    on-board PCB/chip antenna radiate well, so external was the real",
    "    design choice, not a default.",
    "29. This step resolved 23 of the previous session's violations -",
    "    every real external-interface net (all 8 INJn_LO, all 8",
    "    IGNn_COIL, VBATT_INJ/IGN, CRANK/CAM_VR_HI/LO, O2_US, MAP_SIG,",
    "    TPS_SIG, KNOCK_SIG, BLE_ANT) now reaches a physical connector pin",
    "    - dropping from 39 violations to 16, with ZERO new unexpected",
    "    findings. The 16 remaining are the exact same categories",
    "    documented throughout this project: ERC's inability to trace a",
    "    driver through a passive/relay/MOSFET (8), the 3 JTAG pins",
    "    genuinely driven off-sheet by an external probe, 3 deliberately-",
    "    unconnected pins (BLE_RST_CTL/CJ_VM/CJ_RS), and the 2",
    "    verified-safe SPI SO tri-state sharing instances. Steps 2-9 of",
    "    the approved build order are now complete - only PCB layout,",
    "    routing, DRC, and a real distributor-verified BOM (step 10)",
    "    remain before this is a fabricatable board.",
    "30. SENSOR/OUTPUT EXPANSION (added after a full design review found",
    "    real functional gaps against what a production standalone ECU",
    "    actually carries). Everything below reuses parts ALREADY",
    "    registered and datasheet-verified elsewhere in this file - no new",
    "    unverified component was introduced to build any of it:",
    "    - Battery-voltage sensing (68k/10k divider off the always-on",
    "      VIN_PROT rail into ADC0_P[4]). The most consequential gap: real",
    "      injector dead time varies strongly with supply voltage and",
    "      compensating against a measured battery voltage is standard ECU",
    "      practice. 68k/10k puts full scale at ~25.7V so a 24V jump-start",
    "      still reads on-scale, and limits clamp-diode current during a",
    "      load-dump excursion.",
    "    - Second (exhaust) cam VR input on a 3rd MAX9924 -> E0UC[18]. This",
    "      is what actually delivers crank + 2 cams for DOHC engines with",
    "      independent intake/exhaust phasing, each on a real hardware",
    "      input-capture channel rather than a software edge interrupt.",
    "    - Real actuator outputs: VVT phaser solenoids x2, idle-air valve,",
    "      fuel-pump relay (each a MOSFET + Schottky flyback clamped to",
    "      VBATT_SW, the exact pattern already proven on Q2/D2 and Q11)",
    "      plus a tach output. Reading two cams is only useful if the",
    "      phasers can also be DRIVEN, which is what the VVT pair adds.",
    "      DELIBERATE TRADEOFF: an integrated SPI multi-channel low-side",
    "      driver (Infineon SPIDER/FLEX, ST L9301-class) would add real",
    "      open-load/short diagnostics, but would be a new unverified",
    "      part; reusing a proven discrete pattern won. No per-channel",
    "      electrical diagnostics on these outputs - a real limitation,",
    "      worth revisiting if fault reporting is wanted later.",
    "    - Oil + fuel pressure analog inputs (same 1k/22nF RC pattern as",
    "      MAP/TPS) - cheap, and what lets firmware implement real",
    "      low-oil-pressure and fuel-pressure-loss protection cuts.",
    "    - Second knock channel on a 2nd TLV2372, and a second complete",
    "      wideband O2 bank (2nd CJ125 + heater MOSFET, on SPI_CS_3) - a",
    "      V-engine normally runs one of each per bank.",
    "    All 12 new MCU pins were chosen by shortlisting candidates from",
    "    the datasheet text and then CONFIRMING each one's real 144-LQFP",
    "    number visually on the rendered Table 2 page, and every eMIOS",
    "    channel used (E0UC[18..21], E0UC[25]) was checked against the",
    "    full set already claimed by the 16 injector/ignition channels and",
    "    crank/cam1 - the same discipline that caught the pin-2/E0UC[7]",
    "    aliasing trap earlier in this project.",
    "31. ONE new expected ERC exception from the expansion: a third",
    "    pin_to_pin on the shared SPI SO net, now that CJ125 #2 (U18) sits",
    "    on the same bus as U5/U6/U9. Identical, already-justified",
    "    tri-state sharing as the existing two - not a new class of",
    "    finding. Harness headroom after the expansion: J4 has 8 spare",
    "    pins, J5 has 5.",
    "32. BOOST CONTROL + EGT + FLEX-FUEL + ELECTRONIC THROTTLE (ETC)",
    "    expansion, closing the last real functional gaps from the design",
    "    review. Two genuinely new parts this pass (everything in the two",
    "    earlier expansions reused parts already in use elsewhere):",
    "    - AD8495ARZ (U19), a real thermocouple amplifier for EGT. HONEST",
    "      GAP: no AEC-Q100 qualification found anywhere in Analog Devices'",
    "      own datasheet, despite 'exhaust gas temperature sensing' being",
    "      one of its own listed applications - it is the real part the",
    "      aftermarket EFI industry actually uses for this job (Haltech,",
    "      DIYAutotune, Bosphorus Innovations EGT amps are all built",
    "      around this same chip), used here as the honest answer with the",
    "      qualification gap flagged rather than hidden or invented away.",
    "    - MC33926 (U20), NXP's own 'automotive electronic throttle",
    "      control' H-bridge - real, complete 32-pin pinout read directly",
    "      off the datasheet, including the easy-to-get-backwards detail",
    "      that D1/D2 are OPPOSITE polarity disables (D1 active HIGH, D2",
    "      active LOW). Same honest qualification gap as U19: no AEC-Q100",
    "      statement found in NXP's own datasheet text either, despite the",
    "      part being marketed specifically for automotive ETC.",
    "    Real catches worth recording: (1) EGT needs a genuine 5V supply",
    "    for a useful ~1000C range, but that would overrange the MCU's",
    "    3.3V-domain ADC pin directly - R77/R78 form a real 2:1 divider",
    "    (10k/20k) to bring it back in range, ratio undone in firmware;",
    "    (2) the flex-fuel sensor's pull-up is referenced to +3V3, not the",
    "    sensor's own +12V rail, since the sensor only ever PULLS the line",
    "    down (same reasoning already applied to the MAX9924 COUT",
    "    pull-ups); (3) MC33926's real FB sense resistor (270R) and CCP",
    "    charge-pump cap (33nF) are both the exact values from NXP's own",
    "    application note/electrical-characteristics table, not guesses.",
    "    SAFETY NOTE, stated plainly: this hardware provides two real",
    "    independent hardware disable inputs (D1/D2) separate from the PWM",
    "    path, an enable, a fault flag, and current feedback, plus TWO",
    "    independent sensors on both the pedal (APP1/APP2) and the",
    "    throttle body (TPS1/TPS2) so firmware can cross-check for a",
    "    plausibility fault. The actual safety logic - what to do when",
    "    they disagree - is entirely a firmware responsibility this board",
    "    cannot and does not implement in hardware alone.",
    "    NEW expected ERC exceptions: 6 pin_to_pin findings, all on U20's",
    "    own OUT1 (pins 12-15) and OUT2 (pins 27-30) groups - real,",
    "    required H-bridge output fan-out (all four pads of one leg are",
    "    the same physical output and must be tied together per the",
    "    datasheet), not a design defect. Harness headroom after this",
    "    expansion: J4 has 2 spare pins, J5 has 0.",
]
for i, line in enumerate(NOTE_LINES):
    texts.append(SchText(text=line, position=Position(30, 50 + i * 4.0, 0),
                         effects=Effects(font=Font(height=1.6, width=1.6))))

# ---------------------------------------------------------------------------
sch.libSymbols = [entry[0] for entry in lib_symbols.values()]
sch.graphicalItems = wires
sch.labels = labels
sch.texts = texts
sch.noConnects = no_connects

OUT_SCH = r"C:\Users\root\Project\ecu-pcb\ECU.kicad_sch"
os.makedirs(os.path.dirname(OUT_SCH), exist_ok=True)
sch.to_file(OUT_SCH)
print("Wrote", OUT_SCH)

# See manifold-pcb's own comment on why this second, external library file
# (not just the inline sch.libSymbols) is needed - kicad-cli's own ERC flags
# every part as "library not included in configuration" without it.
from kiutils.symbol import SymbolLib
HERE = os.path.dirname(OUT_SCH)
SYM_LIB_FILE = os.path.join(HERE, f"{LIB}.kicad_sym")
symlib = SymbolLib(symbols=[entry[0] for entry in lib_symbols.values()])
symlib.to_file(SYM_LIB_FILE)
print("Wrote", SYM_LIB_FILE)

SYM_LIB_TABLE = os.path.join(HERE, "sym-lib-table")
with open(SYM_LIB_TABLE, "w", encoding="utf-8") as f:
    f.write(
        '(sym_lib_table\n'
        '\t(version 7)\n'
        f'\t(lib (name "{LIB}") (type "KiCad") (uri "${{KIPRJMOD}}/{LIB}.kicad_sym") '
        '(options "") (descr "ECU project-local symbol library - '
        'regenerated by build_schematic.py, do not hand-edit"))\n'
        ')\n'
    )
print("Wrote", SYM_LIB_TABLE)

# --- validation: syntax round-trip + geometry ------------------------------
from kiutils.utils import sexpr
rep = Schematic.from_sexpr(sexpr.parse_sexp(open(OUT_SCH, encoding="utf-8").read()))
print(f"Round-trip OK: {len(rep.schematicSymbols)} symbols, "
      f"{len(rep.libSymbols)} lib symbols, {len(rep.labels)} labels, "
      f"{len(rep.graphicalItems)} wires")


def on_segment(p, a, b, tol=0.01):
    (px, py), (ax, ay), (bx, by) = p, a, b
    if abs(ax - bx) < tol:   # vertical
        return abs(px - ax) < tol and min(ay, by) - tol <= py <= max(ay, by) + tol
    if abs(ay - by) < tol:   # horizontal
        return abs(py - ay) < tol and min(ax, bx) - tol <= px <= max(ax, bx) + tol
    return False


segs = [((w.points[0].X, w.points[0].Y), (w.points[1].X, w.points[1].Y))
        for w in rep.graphicalItems]
bad = [l.text for l in rep.labels
       if not any(on_segment((l.position.X, l.position.Y), a, b) for a, b in segs)]
assert not bad, f"labels not on any wire: {bad}"

ends = {p for s in segs for p in s}
nc_ends = {(round(nc.position.X, 2), round(nc.position.Y, 2)) for nc in rep.noConnects}
lib = {s.libId: s for s in rep.libSymbols}
orphans = []
for inst in rep.schematicSymbols:
    for pin in lib[inst.libId].pins:
        pos = (round(inst.position.X + pin.position.X, 2),
               round(inst.position.Y - pin.position.Y, 2))
        if pos in nc_ends:
            continue
        if pos not in ends and not pin.hide:
            orphans.append(f"{inst.properties[0].value}.{pin.number}")
assert not orphans, f"pins with no wire: {orphans}"
print("Geometry OK: every label sits on a wire, every visible pin touches a wire end")

coord_net = {}
collisions = []
for l in rep.labels:
    key = (round(l.position.X, 2), round(l.position.Y, 2))
    if key in coord_net and coord_net[key] != l.text:
        collisions.append((key, coord_net[key], l.text))
    coord_net[key] = l.text
for inst in rep.schematicSymbols:
    if not inst.libId.startswith(f"{LIB}:PWR_"):
        continue
    net = inst.properties[1].value  # power symbol's Value IS its net name
    key = (round(inst.position.X, 2), round(inst.position.Y, 2))
    if key in coord_net and coord_net[key] != net:
        collisions.append((key, coord_net[key], net))
    coord_net[key] = net
assert not collisions, (
    f"two different nets land on the same coordinate (a GND riser probably "
    f"collided with a neighboring pin's stub - give one of them a different "
    f"stub length): {collisions}")
print("Net-collision check OK: no two different nets share a coordinate")

# Duplicate reference-designator check. Added after a real one slipped
# through (two separate parts both named "R15" in the step-5/6 session -
# an f-string ref-number formula collided with a later hardcoded one).
# kicad-cli's own ERC did NOT flag it on that pass (duplicate_reference
# is a real ERC rule, but this specific invocation didn't catch it) - the
# bug was only found by manually diffing reference designators against
# the generated file. This check makes that manual step permanent instead
# of relying on remembering to re-run it.
ref_counts = {}
for inst in rep.schematicSymbols:
    ref = inst.properties[0].value
    ref_counts[ref] = ref_counts.get(ref, 0) + 1
dup_refs = {r: n for r, n in ref_counts.items() if n > 1}
assert not dup_refs, f"duplicate reference designators (two parts, one name): {dup_refs}"
print(f"Reference-designator check OK: {len(ref_counts)} unique refs, no duplicates")

# --- upgrade to KiCad's current native format -------------------------------
import shutil, subprocess

def find_kicad_cli():
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    for candidate in [
        r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
        r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
        r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None

kicad_cli = find_kicad_cli()
if kicad_cli:
    result = subprocess.run([kicad_cli, "sch", "upgrade", OUT_SCH],
                            capture_output=True, text=True)
    if result.returncode == 0:
        print("Upgraded to current KiCad format:", result.stdout.strip())
    else:
        print("WARNING: kicad-cli sch upgrade failed, file left in kiutils' "
              "format (KiCad will still open it, just with the old-format "
              "warning):", result.stderr.strip())
else:
    print("NOTE: kicad-cli not found - file left in kiutils' format. KiCad "
          "will open it fine but show an 'older version' warning until you "
          "save it once from the GUI (or install KiCad here and rerun).")
