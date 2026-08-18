#!/usr/bin/env python3
"""
build_bom.py - generates the ECU's bill of materials straight from the
real schematic, plus a standalone HTML page for jessiescars.com.

WHY GENERATED, NOT HAND-MAINTAINED. Every other artefact in this project
is regenerated from source rather than hand-edited (see
build_schematic.py / build_pcb.py / build_k1_footprint.py), for the same
reason: a hand-kept BOM drifts silently the moment a part changes, and
this board has had several real part changes late in its life (MC33810
-> L9779WD-SPI, 16A relay -> 40A Panasonic, AD8495 -> ADS1118-Q1, two
temp sensors, a pass NMOS package fix). Reference designators, values,
packages and quantities here all come from ECU.kicad_sch itself, so the
BOM cannot disagree with the board.

WHAT IS AND IS NOT VERIFIED - stated plainly, because "distributor-
verified BOM" has been this project's last open item for a while and it
would be easy to overclaim here:
  * ACTIVE parts, connectors and electromechanical parts carry REAL
    manufacturer part numbers, each one verified against the actual
    manufacturer datasheet during the design pass that introduced it -
    that provenance is in build_schematic.py's own inline comments and
    in README.md, not invented here.
  * QUALIFICATION status (AEC-Q100 grade / AEC-Q101 / AEC-Q200) is
    likewise taken from the real datasheets, including the two that were
    re-checked late: MC33926 is AEC-Q100 Grade 1 from datasheet Rev. 13
    onward, and the VDD5 pass NMOS is the automotive-grade STD20NF06LAG.
  * PASSIVES are specified parametrically - value, package, and the
    AEC-Q200 requirement - and NOT pinned to a single manufacturer part
    number. That is deliberate and is how passives are actually bought:
    an 0603 100nF X7R AEC-Q200 capacitor is a commodity chosen on
    value/package/voltage/qualification, not a unique design-critical
    device. Pinning one vendor here would add false precision.
  * LIVE PRICING AND STOCK ARE NOT INCLUDED. Nothing in this file was
    checked against a distributor's live catalogue - that needs a
    purchasing pass against real availability on the day, and stock
    moves. Two parts specifically deserve that check first: the Bosch
    CJ125 (a long-lived part but not a mainline catalogue item) and the
    FGP3040G2 ignition IGBTs (8 off, the single largest line).
  * EVERY PART IS NOW A REAL, SPECIFIC PART. The 2.4GHz balun was the
    last placeholder and is now Johanson 2450BM14G0011T-AEC, taken from
    TI's own SWRA572 application report for this exact CC26xx pairing.
    Note the suffix: the plain 2450BM14G0011 is rated only -40..+85C and
    would be out of spec on this board, and 2450BM15A0002 is a different
    part for the older CC253X-era chipsets entirely.

Run:  python build_bom.py          -> console summary + ECU_BOM.html
"""
import io
import os
import re
from collections import defaultdict

from kiutils.schematic import Schematic
from kiutils.utils import sexpr

HERE = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(HERE, "ECU.kicad_sch")
OUT_HTML = os.path.join(HERE, "ECU_BOM.html")
PCB = os.path.join(HERE, "ECU.kicad_pcb")

# Real manufacturer part numbers for every non-passive. Keyed by the
# distinctive token that appears in the schematic's own Value string, so
# this table cannot drift away from the schematic without the lookup
# simply failing loudly rather than reporting a stale part.
#   token -> (MPN, manufacturer, description, qualification)
MPN = {
    "MPC5606B":     ("MPC5606BMLU",     "NXP",         "32-bit Power Architecture engine-control MCU, 144-LQFP", "AEC-Q100"),
    "LM74700-Q1":   ("LM74700QDBVRQ1",  "TI",          "Ideal-diode controller (reverse-battery protection)", "AEC-Q100 G1"),
    "LMR33630-Q1":  ("LMR33630BRNXRQ1", "TI",          "3A synchronous buck regulator, 5V rail", "AEC-Q100 G1"),
    "TLV733P-Q1":   ("TLV73333PQDBVRQ1","TI",          "300mA LDO, 3.3V rail", "AEC-Q100 G1"),
    "L9779WD-SPI":  ("L9779WD-SPI",     "ST",          "Multifunction engine-management IC: 4x injector LSD + 4x ignition pre-driver", "Automotive"),
    "MAX9924":      ("MAX9924UAUB+",    "Analog Devices","Variable-reluctance sensor interface (crank/cam)", "AEC-Q100"),
    "CJ125":        ("CJ125",           "Bosch",       "Wideband lambda (O2) sensor interface for LSU4.x", "Automotive"),
    "TLV2372-Q1":   ("TLV2372QDRQ1",    "TI",          "Dual rail-to-rail op-amp, knock-sensor front end", "AEC-Q100 G1"),
    "TJA1043T":     ("TJA1043T/1J",     "NXP",         "High-speed CAN transceiver with wake", "AEC-Q100"),
    "FT4232HA":     ("FT4232HAQ-REEL",  "FTDI",        "Quad USB UART/MPSSE bridge (USB flashing + JTAG)", "AEC-Q100 G2"),
    "CC2640R2F-Q1": ("CC2640R2FRSMRQ1", "TI",          "Bluetooth Low Energy SoC (wireless reflash)", "AEC-Q100 G2"),
    "SN3257-Q1":    ("SN3257QPWRQ1",    "TI",          "4-channel analog switch (USB/BLE bus arbitration)", "AEC-Q100 G1"),
    "ADS1118-Q1":   ("ADS1118QDGSRQ1",  "TI",          "16-bit SPI ADC w/ PGA + on-die temp sensor, EGT thermocouple front end", "AEC-Q100 G1"),
    "MC33926":      ("MC33926PNB",      "NXP",         "5.0A throttle-control H-bridge (electronic throttle)", "AEC-Q100 G1"),

    "5KP33A":       ("5KP33A",          "Littelfuse",  "5000W TVS diode, load-dump protection", "AEC-Q101"),
    "PMEG4010BEA":  ("PMEG4010BEA",     "Nexperia",    "40V 1A Schottky, flyback clamp", "AEC-Q101"),
    "SQM40020EL":   ("SQM40020EL_GE3",  "Vishay",      "40V 100A N-MOSFET, shared reverse-battery pass element", "AEC-Q101"),
    "PMV230ENEA":   ("PMV230ENEA",      "Nexperia",    "N-MOSFET, relay-coil driver", "AEC-Q101"),
    "PMV37ENEA":    ("PMV37ENEA",       "Nexperia",    "N-MOSFET, low-side solenoid/heater driver", "AEC-Q101"),
    "FGP3040G2":    ("FGP3040G2",       "onsemi",      "400V 30A ignition IGBT (EcoSPARK II), TO-220", "Automotive"),
    "STD20NF06LAG": ("STD20NF06LAG",    "ST",          "60V 24A logic-level N-MOSFET, VDD5 linear pass element", "AEC-Q101"),

    "CB1a-T-P-12V": ("CB1a-T-P-12V",    "Panasonic",   "40A automotive PCB power relay, 12V coil, sealed, -40..+125C", "Automotive -40..125C"),
    "Mini blade":   ("KEYSTONE 3568",   "Keystone",    "Mini blade fuse holder, PCB mount (fuse element ordered separately)", "-"),
    "AMPSEAL":      ("1-776180-1",      "TE",          "AMPSEAL 35-position sealed harness connector, right-angle PCB header", "Automotive"),
    "USB-C":        ("12401610E4#2A",   "Amphenol",    "USB-C receptacle, 16-pin", "-"),
    "U.FL":         ("U.FL-R-SMT-1",    "Hirose",      "U.FL coaxial connector for external BLE antenna", "-"),
    "JTAG":         ("PinHeader 1x08",  "generic",     "2.54mm pin header, JTAG/debug (bench use)", "-"),
    "2450BM14G0011T-AEC": ("2450BM14G0011T-AEC", "Johanson Technology",
                           "2.4GHz impedance-matched balun + low-pass filter for CC26xx, replaces TI's 9-part discrete LC network",
                           "AEC-Q200, -40..+105C"),
}

# Fuse elements are a separate orderable line from their holders.
FUSE_ELEMENTS = [
    ("Littelfuse 0297030", "Littelfuse", "30A Mini blade fuse element (main input)", 1, "F1"),
    ("Littelfuse 0297002", "Littelfuse", "2A Mini blade fuse element (logic supply)", 1, "F2"),
    ("Littelfuse 0297015", "Littelfuse", "15A Mini blade fuse element (injector rail)", 1, "F3"),
    ("Littelfuse 0297025", "Littelfuse", "25A Mini blade fuse element (ignition rail)", 1, "F4"),
]

PASSIVE_PREFIXES = ("R", "C", "L", "FB", "Y")


def board_size_mm():
    """Real board outline size, measured from the PCB's own Edge.Cuts
    geometry. Derived rather than hardcoded for the same reason the rest
    of this file is generated: the board has been resized by nearly
    every part change in this project's late life, and a number typed
    into the page would be stale within one commit. Returns a display
    string, or None if the PCB has not been generated yet."""
    try:
        pcb = io.open(PCB, encoding="utf-8").read()
    except OSError:
        return None
    xs, ys = [], []
    # Edge.Cuts segments carry explicit start/end coordinates.
    for m in re.finditer(
            r"\(gr_line\s*\(start ([-\d.]+) ([-\d.]+)\)\s*\(end ([-\d.]+) ([-\d.]+)\)"
            r"(?:(?!\(gr_line).)*?Edge\.Cuts", pcb, re.S):
        xs += [float(m.group(1)), float(m.group(3))]
        ys += [float(m.group(2)), float(m.group(4))]
    if not xs:
        return None
    return f"{max(xs) - min(xs):.1f} &times; {max(ys) - min(ys):.1f}"


def load_parts():
    sch = Schematic.from_sexpr(sexpr.parse_sexp(open(SCH, encoding="utf-8").read()))
    parts = []
    for inst in sch.schematicSymbols:
        ref = next(p.value for p in inst.properties if p.key == "Reference")
        if ref.startswith("#"):
            continue          # power-flag symbols are not physical parts
        val = next((p.value for p in inst.properties if p.key == "Value"), "")
        fp = next((p.value for p in inst.properties if p.key == "Footprint"), "")
        parts.append({"ref": ref, "value": val, "package": fp.split(":")[-1]})
    return parts


def ref_sort_key(ref):
    m = re.match(r"^([A-Z]+)(\d+)$", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


def collapse_refs(refs):
    """R1, R2, R3, R7 -> 'R1-R3, R7' - how a real BOM lists designators."""
    refs = sorted(refs, key=ref_sort_key)
    out, run = [], []

    def flush():
        if not run:
            return
        if len(run) >= 3:
            out.append(f"{run[0]}-{run[-1]}")
        else:
            out.extend(run)
    for r in refs:
        if run:
            pa, na = ref_sort_key(run[-1])
            pb, nb = ref_sort_key(r)
            if pa == pb and nb == na + 1:
                run.append(r)
                continue
            flush()
            run = []
        run.append(r)
    flush()
    return ", ".join(out)


def value_token(value):
    """Leading token of a passive's value string: '100nF charge-pump cap
    (AEC-Q200)' -> '100nF'."""
    return value.split()[0] if value.split() else "?"


def tolerance_token(value):
    """A tolerance written into a passive's value string, e.g.
    '4.7k 1% MAP divider hi (AEC-Q200)' -> '1%'. Returns None when the
    schematic does not call one out.

    This exists because a tolerance is a REAL ORDERING REQUIREMENT, not
    a comment. The divider pairs on the 5V sensor inputs are specified
    1% because a divider RATIO error is a direct gain error on the
    reading; if that does not reach the BOM, the specification is
    decorative and a purchaser buys 5% parts. Parts that say nothing
    keep the unqualified default they always had."""
    m = re.search(r"\b(\d+(?:\.\d+)?)\s?%", value)
    return f"{m.group(1)}%" if m else None


# Multipliers for parsing a passive value token into a real number, so
# two spellings of one value can be recognised as the same orderable
# part. "R" is the ohm marker in infix notation (82R5 = 82.5 ohm).
_VALUE_MULT = {"k": 1e3, "K": 1e3, "M": 1e6, "m": 1e-3,
               "u": 1e-6, "n": 1e-9, "p": 1e-12, "R": 1.0, "": 1.0}


def parse_value(token):
    """Numeric value of a passive's value token, or None if it isn't a
    plain value. Handles both the decimal form (4.7k, 1M, 499R) and the
    infix form electronics uses to survive smudged print (4k7 = 4.7k,
    82R5 = 82.5R, 1M0 = 1M)."""
    m = re.match(r"^(\d+)([kKMRunp])(\d+)$", token)
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * _VALUE_MULT[m.group(2)]
    m = re.match(r"^([\d.]+)\s*([kKMmunpR]?)$", token)
    if m:
        return float(m.group(1)) * _VALUE_MULT.get(m.group(2), 1.0)
    return None


def check_value_spellings(lines):
    """Fail if one electrical value reaches the BOM as two order lines
    purely because it was spelled two ways.

    This is a REAL purchasing bug, not tidiness: '4k7' and '4.7k' are
    the same resistor, so splitting them means ordering two reels of one
    part - and it drifts back in silently every time a value is
    transcribed from a vendor's schematic in that vendor's notation.

    A declared tolerance is NOT a collision. A 1% part is a genuinely
    different orderable part from a 5% one, so '1k 1%' is allowed to sit
    on its own line beside '1k' - that split is the point, not a fault.
    """
    seen = defaultdict(list)
    for _cat, label, _mfr, _desc, package, _qty, refs, _qual in lines:
        parts_ = label.split()
        if len(parts_) < 2:
            continue
        value = parse_value(parts_[0])
        if value is None:
            continue
        tol = next((t for t in parts_[1:] if t.endswith("%")), None)
        seen[(parts_[-1], value, tol, package)].append((label, refs))

    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    if clashes:
        detail = "; ".join(
            f"{k[1]:g} ({k[0]}) split across " + " and ".join(f"'{lb}'" for lb, _ in v)
            for k, v in clashes.items())
        raise AssertionError(
            f"BOM has {len(clashes)} value(s) split across order lines by "
            f"spelling alone: {detail}. Normalise the value token in "
            f"build_schematic.py; if the split is a real tolerance "
            f"difference, write it as e.g. '1k 1%' so it is explicit.")
    print(f"Value-spelling check OK: no value split across two order lines "
          f"({len(seen)} distinct passive line identities)")


def build():
    parts = load_parts()
    lines = []          # (category, mpn, mfr, desc, package, qty, refs, qual)

    # --- non-passives: match each part to its real MPN by token --------
    matched = set()
    groups = defaultdict(list)
    for p in parts:
        prefix = re.match(r"^([A-Z]+)", p["ref"]).group(1)
        if prefix in PASSIVE_PREFIXES and prefix != "FB":
            continue
        hit = next((tok for tok in MPN if tok in p["value"]), None)
        if hit is None:
            continue
        matched.add(p["ref"])
        groups[(hit, p["package"])].append(p["ref"])

    for (tok, package), refs in groups.items():
        mpn, mfr, desc, qual = MPN[tok]
        cat = "Connectors" if tok in ("AMPSEAL", "USB-C", "U.FL", "JTAG") else \
              "Electromechanical" if tok in ("CB1a-T-P-12V", "Mini blade") else \
              "Semiconductors"
        lines.append((cat, mpn, mfr, desc, package, len(refs), collapse_refs(refs), qual))

    # --- fuse elements (ordered separately from their holders) ---------
    # These are real, separately-orderable items but NOT separate board
    # placements - the element drops into the holder that already counts
    # as F1-F4. Tagged so the coverage check below can tell the two
    # notions apart; the check caught this double-count on first run.
    for mpn, mfr, desc, qty, ref in FUSE_ELEMENTS:
        lines.append(("Electromechanical", mpn, mfr, desc, "Mini blade (element)", qty, ref, "-"))

    # --- passives: grouped by real value + package ---------------------
    pgroups = defaultdict(list)
    for p in parts:
        prefix = re.match(r"^([A-Z]+)", p["ref"]).group(1)
        if p["ref"] in matched:
            continue
        if prefix not in PASSIVE_PREFIXES:
            continue
        pgroups[(prefix, value_token(p["value"]), tolerance_token(p["value"]),
                 p["package"])].append(p["ref"])

    kind = {"R": ("Resistor", "thick film, AEC-Q200"),
            "C": ("Capacitor", "X7R/X5R ceramic, AEC-Q200"),
            "L": ("Inductor", "AEC-Q200"),
            "FB": ("Ferrite bead", "AEC-Q200"),
            "Y": ("Crystal", "AEC-Q200")}
    for (prefix, val, tol, package), refs in pgroups.items():
        name, note = kind.get(prefix, ("Part", ""))
        # A called-out tolerance becomes part of the orderable identity,
        # so a 1% line cannot be silently filled with 5% stock.
        label = f"{val} {tol} {name}" if tol else f"{val} {name}"
        desc = (f"{name} {val}, {tol} tolerance - {note}" if tol
                else f"{name} {val} - {note}")
        lines.append(("Passives", label, "(any qualified)",
                      desc, package, len(refs),
                      collapse_refs(refs), "AEC-Q200"))

    order = {"Semiconductors": 0, "Electromechanical": 1, "Connectors": 2, "Passives": 3}
    lines.sort(key=lambda r: (order[r[0]], -r[5], r[1]))
    return parts, lines


def main():
    parts, lines = build()
    # Consumables (fuse elements) are orderable but occupy no board
    # placement of their own - excluded from the coverage arithmetic.
    placements = sum(r[5] for r in lines if r[4] != "Mini blade (element)")
    print(f"BOM: {len(lines)} orderable line items covering {placements} placements "
          f"(schematic has {len(parts)} real parts)")
    by_cat = defaultdict(int)
    for r in lines:
        by_cat[r[0]] += 1
    for cat, n in sorted(by_cat.items()):
        print(f"  {cat}: {n} line items")
    assert placements == len(parts), \
        f"BOM covers {placements} placements but schematic has {len(parts)} parts"
    print("Coverage check OK: every schematic part appears on exactly one BOM line")
    check_value_spellings(lines)

    html = render_html(lines, parts)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {OUT_HTML}")


def render_html(lines, parts):
    from html import escape
    rows = []
    current = None
    for cat, mpn, mfr, desc, package, qty, refs, qual in lines:
        if cat != current:
            current = cat
            rows.append(f'<tr class="cat"><td colspan="7">{escape(cat)}</td></tr>')
        qcls = "q-yes" if qual not in ("-", "TBD") else ("q-tbd" if qual == "TBD" else "q-na")
        rows.append(
            "<tr>"
            f'<td class="mpn">{escape(mpn)}</td>'
            f"<td>{escape(mfr)}</td>"
            f'<td class="desc">{escape(desc)}</td>'
            f'<td class="pkg">{escape(package)}</td>'
            f'<td class="qty">{qty}</td>'
            f'<td class="refs">{escape(refs)}</td>'
            f'<td><span class="{qcls}">{escape(qual)}</span></td>'
            "</tr>")
    table = "\n".join(rows)
    total_lines = len(lines)
    total_parts = len(parts)
    board = board_size_mm() or "see repo"
    return HTML_TEMPLATE.replace("{{TABLE}}", table) \
                        .replace("{{LINES}}", str(total_lines)) \
                        .replace("{{PARTS}}", str(total_parts)) \
                        .replace("{{BOARD}}", board)


HTML_TEMPLATE = r"""<title>ECU Bill of Materials</title>
<style>
  /* Light palette is the base; both dark paths redefine only tokens. */
  :root{
    --paper:#fbfaf8; --panel:#ffffff; --ink:#1a1d21; --muted:#6b6f76;
    --rule:#e4e1dc; --rule-soft:#efedea;
    --accent:#1d4e89; --brass:#8a6a24;
    --ok:#2f6b45; --ok-bg:#e9f2ec;
    --warn:#8a5a00; --warn-bg:#f8efdd;
    --none:#6b6f76; --none-bg:#eeecea;
    --shadow:0 1px 2px rgba(26,29,33,.05);
  }
  @media (prefers-color-scheme:dark){
    :root:not([data-theme="light"]){
      --paper:#14161a; --panel:#1b1e23; --ink:#e9e7e3; --muted:#9aa0a8;
      --rule:#2c3037; --rule-soft:#23272d;
      --accent:#7fb0ec; --brass:#d3ab5c;
      --ok:#77d7a2; --ok-bg:#16301f;
      --warn:#efbe73; --warn-bg:#33260f;
      --none:#9aa0a8; --none-bg:#24282e;
      --shadow:none;
    }
  }
  :root[data-theme="dark"]{
    --paper:#14161a; --panel:#1b1e23; --ink:#e9e7e3; --muted:#9aa0a8;
    --rule:#2c3037; --rule-soft:#23272d;
    --accent:#7fb0ec; --brass:#d3ab5c;
    --ok:#77d7a2; --ok-bg:#16301f;
    --warn:#efbe73; --warn-bg:#33260f;
    --none:#9aa0a8; --none-bg:#24282e;
    --shadow:none;
  }

  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{
    margin:0; background:var(--paper); color:var(--ink);
    font:16px/1.65 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }
  .wrap{max-width:1120px; margin:0 auto; padding:52px 22px 80px;
        display:flex; flex-direction:column; gap:30px}

  .eyebrow{margin:0; font-size:11.5px; font-weight:700; letter-spacing:.18em;
           text-transform:uppercase; color:var(--brass)}
  h1{font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
     font-weight:600; font-size:clamp(32px,5vw,50px); line-height:1.08;
     letter-spacing:-.015em; margin:10px 0 0; text-wrap:balance}
  .standfirst{margin:14px 0 0; color:var(--muted); max-width:64ch; font-size:17px}
  .rule{height:1px; background:var(--rule); border:0; margin:0}

  .figures{display:flex; flex-wrap:wrap; border:1px solid var(--rule);
           border-radius:4px; background:var(--panel); box-shadow:var(--shadow); overflow:hidden}
  .fig{flex:1 1 150px; padding:16px 20px; border-right:1px solid var(--rule-soft)}
  .fig:last-child{border-right:0}
  .fig b{display:block; font-size:26px; line-height:1.15; letter-spacing:-.02em;
         font-variant-numeric:tabular-nums;
         font-family:"Iowan Old Style",Palatino,Georgia,serif; font-weight:600}
  .fig span{display:block; margin-top:3px; font-size:11.5px; letter-spacing:.08em;
            text-transform:uppercase; color:var(--muted)}

  .note{border-left:2px solid var(--accent); padding:2px 0 2px 20px;
        display:flex; flex-direction:column; gap:10px}
  .note h2{font-family:"Iowan Old Style",Palatino,Georgia,serif;
           font-size:19px; font-weight:600; margin:0; letter-spacing:-.01em}
  .note p{margin:0; font-size:15px; color:var(--muted); max-width:70ch}
  .note strong{color:var(--ink); font-weight:600}

  .tablewrap{border:1px solid var(--rule); border-radius:4px; background:var(--panel);
             box-shadow:var(--shadow); overflow-x:auto}
  table{border-collapse:collapse; width:100%; min-width:920px; font-size:14px}
  thead th{position:sticky; top:0; z-index:1; background:var(--panel);
           text-align:left; font-size:10.5px; font-weight:700; letter-spacing:.12em;
           text-transform:uppercase; color:var(--muted);
           padding:15px 16px 11px; border-bottom:1px solid var(--rule); white-space:nowrap}
  tbody td{padding:11px 16px; border-bottom:1px solid var(--rule-soft); vertical-align:top}
  tbody tr:last-child td{border-bottom:0}
  tr.cat td{background:var(--paper); color:var(--brass);
            font-size:10.5px; font-weight:700; letter-spacing:.16em; text-transform:uppercase;
            padding:11px 16px; border-bottom:1px solid var(--rule); border-top:1px solid var(--rule)}
  .mpn,.pkg,.refs{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
  .mpn{font-size:13px; font-weight:600; white-space:nowrap}
  .desc{color:var(--muted); min-width:260px}
  .pkg{font-size:12px; color:var(--muted); white-space:nowrap}
  .qty{text-align:right; font-variant-numeric:tabular-nums; font-weight:700; white-space:nowrap}
  .refs{font-size:12px; color:var(--muted)}
  .q-yes,.q-tbd,.q-na{display:inline-block; padding:2px 9px; border-radius:3px;
                      font-size:11px; font-weight:700; letter-spacing:.03em; white-space:nowrap}
  .q-yes{color:var(--ok); background:var(--ok-bg)}
  .q-tbd{color:var(--warn); background:var(--warn-bg)}
  .q-na{color:var(--none); background:var(--none-bg)}

  footer{color:var(--muted); font-size:14px; max-width:72ch}
  footer p{margin:0}
  footer code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
              font-size:13px; color:var(--ink)}
  a{color:var(--accent)}
  a:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
  @media (prefers-reduced-motion:reduce){*{animation:none !important; transition:none !important}}
  @media (max-width:640px){
    .wrap{padding:32px 15px 60px; gap:24px}
    .fig{flex-basis:50%; border-bottom:1px solid var(--rule-soft)}
  }
</style>

<div class="wrap">
  <header>
    <p class="eyebrow">Jessie&rsquo;s Cars &middot; Engine Control Unit</p>
    <h1>ECU Bill of Materials</h1>
    <p class="standfirst">Every part on the standalone engine-control unit &mdash; an
    NXP MPC5606B board driving eight injectors and eight ignition coils, with wideband
    O<sub>2</sub>, knock sensing, electronic throttle, dual CAN, and firmware flashing
    over both USB and Bluetooth.</p>
  </header>

  <div class="figures">
    <div class="fig"><b>{{LINES}}</b><span>Line items</span></div>
    <div class="fig"><b>{{PARTS}}</b><span>Placements</span></div>
    <div class="fig"><b>8</b><span>Copper layers</span></div>
    <div class="fig"><b>{{BOARD}}</b><span>Board, mm</span></div>
  </div>

  <div class="note">
    <h2>How to read this list</h2>
    <p><strong>Active parts, connectors and electromechanical parts</strong> carry real
    manufacturer part numbers, each verified against the manufacturer&rsquo;s own datasheet
    when it was designed in. Qualification is quoted from those datasheets.</p>
    <p><strong>Passives are specified parametrically</strong> &mdash; value, package and the
    AEC-Q200 requirement &mdash; rather than pinned to one vendor. That is how passives are
    actually bought: an 0603 100&nbsp;nF X7R AEC-Q200 capacitor is a commodity chosen on
    value, size, voltage and qualification.</p>
    <p><strong>Pricing and stock are not included.</strong> Nothing here was checked against
    a distributor&rsquo;s live catalogue, and availability moves. Two lines are worth confirming
    first: the Bosch CJ125, long-lived but not a mainline catalogue part, and the eight
    FGP3040G2 ignition IGBTs &mdash; the largest single line.</p>
    <p><strong>Watch the suffixes on the balun.</strong> <code>2450BM14G0011T-AEC</code> is
    the AEC-Q200 part rated &minus;40 to +105&nbsp;°C. The plain <code>2450BM14G0011</code>
    stops at +85&nbsp;°C and would be out of spec on this board, and
    <code>2450BM15A0002</code> is a different part for older CC253x-era chipsets. Ordering
    the wrong one is an easy and expensive mistake.</p>
  </div>

  <div class="tablewrap">
    <table>
      <thead>
        <tr>
          <th scope="col">Part number</th><th scope="col">Manufacturer</th>
          <th scope="col">Description</th><th scope="col">Package</th>
          <th scope="col">Qty</th><th scope="col">Designators</th>
          <th scope="col">Qualification</th>
        </tr>
      </thead>
      <tbody>
{{TABLE}}
      </tbody>
    </table>
  </div>

  <hr class="rule">

  <footer>
    <p>Generated directly from the project schematic by <code>build_bom.py</code>, so
    quantities and designators cannot drift from the board. Every line is now a real,
    specific part &mdash; the 2.4&nbsp;GHz balun was the last placeholder and is resolved.
    What remains before ordering is a purchasing pass for live pricing and availability,
    and hand-layout of the RF section, which cannot be machine-routed.</p>
  </footer>
</div>
"""


if __name__ == "__main__":
    main()
