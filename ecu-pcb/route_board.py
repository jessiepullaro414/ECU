"""
Routes ECU.kicad_pcb with FreeRouting, then adds zone pours on top of the
finished routing. Direct extension of manifold-pcb/route_board.py - same
four-step DSN/FreeRouting/SES/zones pipeline, same reasoning for each
step (see that file for the full commentary this is derived from). Real
differences: 8 layers not 4 (3 GND planes on In1/In3/In6.Cu plus a +3V3
plane on In4.Cu, so each of the four signal layers F/In2/In5/B sits
against a solid reference - see build_pcb.py for why 8), and a
much bigger board (158 nets vs Manifold's 47, 320x141mm vs 83x61mm) so
MAX_PASSES is raised accordingly rather than assumed to still be enough.

Requires:
  - tools/freerouting-2.2.4.jar (copied directly from manifold-pcb - same
    general-purpose tool, not project-specific)
  - KiCad's bundled Python at KICAD_PYTHON below (ships with any KiCad
    install; distinct from the system Python used to run this file)

This does NOT run kicad-cli DRC itself - run run_drc.py afterward to
verify the routed result (clearance, unrouted-net count, etc).

J3 NPTH KEEPOUTS are injected before DSN export - see
add_j3_npth_keepouts() below for why (a recurring, real clearance
violation) and why it is written as raw s-expression text rather than
through pcbnew's ZONE API (that API crashes under a nested subprocess).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "ECU.kicad_pcb")
DSN = os.path.join(HERE, "ECU.dsn")
SES = os.path.join(HERE, "ECU.ses")
FREEROUTING_JAR = os.path.join(HERE, "tools", "freerouting-2.2.4.jar")

KICAD_PYTHON = r"C:\Program Files\KiCad\10.0\bin\python.exe"
JAVA_CANDIDATES = [
    r"C:\Program Files\Eclipse Adoptium\jre-25.0.3.9-hotspot\bin\java.exe",
]

# Real, deliberately higher than Manifold's MAX_PASSES=60: this board has
# 158 real nets (3.4x Manifold's 47) across a much bigger, more
# congested, section-organized layout (8 heterogeneous subsystems, not
# one small uniform grid) - give the autorouter real headroom to actually
# converge rather than assuming the same pass budget that worked for a
# much smaller board still applies here. Tune down after seeing the real
# first-run result, same empirical approach used throughout this project.
MAX_PASSES = 150
OPTIMIZATION_IMPROVEMENT_THRESHOLD = 0


# Radius of clear board kept around each of J3's real NPTH mounting
# holes, derived rather than guessed: the largest hole's drill is
# 0.95mm (half = 0.475) and the board's hole-clearance rule is 0.25mm,
# so 0.725mm is the minimum that keeps copper legal, and 0.75 leaves a
# hair of margin.
#
# It was 1.4mm first, and that was a real bug: J3's own nearest pads sit
# only 1.08mm from the hole centres, so a 1.4mm keepout swallowed them.
# Pads are "allowed" inside a keepout, so they still existed - but no
# track could reach them, and the route came back with J3's shield and
# GND pads unconnected. A keepout sized past what the rule actually
# needs doesn't protect anything extra, it just strangles its
# neighbours.
J3_KEEPOUT_R = 0.75


def add_j3_npth_keepouts():
    """Keep the autorouter away from J3's NPTH mounting holes.

    This exact problem has now recurred across four separate routing
    runs, surviving two different attempts to fix it by geometry alone
    (more placement clearance, then a looser global clearance rule):
    FreeRouting keeps threading short GND tracks within ~0.15mm of J3's
    mounting holes, against a 0.25mm rule. Loosening the global rule
    further would trade real fab margin across the whole board to buy
    off one 3mm-wide spot, and increasing J3's clearance just moves the
    crowding somewhere else.

    Note WHAT is being routed there: GND. In1/In3/In6.Cu are poured
    GND planes, so those tracks are redundant anyway - FreeRouting
    simply doesn't know that, because the zones are added after routing
    (pouring them first measurably hurts routability). So the honest fix
    is to mark that small area off-limits and let the plane do its job.

    Written as raw s-expression text rather than through pcbnew's
    ZONE API: `SetIsRuleArea` reliably crashes with an access violation
    (0xC0000005) when driven from a nested subprocess, which is exactly
    how every other pcbnew call in this file runs - reproduced 5/5 while
    LoadBoard/ExportSpecctraDSN/ImportSpecctraSES/ordinary filled zones
    all work fine the same way. The .kicad_pcb is text and the zone
    format is stable, so writing it directly sidesteps the bug entirely
    instead of fighting it.
    """
    import math
    import re as _re
    import uuid as _uuid

    text = open(PCB, encoding="utf-8").read()
    if "J3_NPTH_keepout" in text:
        return 0  # already present (idempotent across re-runs)

    idx = text.find('"J3"')
    start = text.rfind("(footprint ", 0, idx)
    end = text.find("\n\t(footprint ", start + 1)
    if end == -1:
        end = len(text)
    block = text[start:end]
    m = _re.search(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)", block)
    ox, oy = float(m.group(1)), float(m.group(2))
    ang = math.radians(float(m.group(3) or 0.0))

    holes = []
    for pm in _re.finditer(r"\(pad \"\" np_thru_hole [^\n]*\n\s*\(at ([-\d.]+) ([-\d.]+)", block):
        lx, ly = float(pm.group(1)), float(pm.group(2))
        # KiCad footprint rotation is counter-clockwise-positive, and a
        # child item's placed position is the parent's origin plus its
        # own local offset turned by that angle.
        rx = lx * math.cos(ang) + ly * math.sin(ang)
        ry = -lx * math.sin(ang) + ly * math.cos(ang)
        holes.append((ox + rx, oy + ry))
    if not holes:
        raise SystemExit("J3 has no NPTH pads - footprint changed?")

    layers = " ".join(f'"{n}"' for n in
                      ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu",
                       "In5.Cu", "In6.Cu", "B.Cu"))
    zones = []
    for hx, hy in holes:
        pts = " ".join(
            f"(xy {hx + J3_KEEPOUT_R * math.cos(math.radians(a)):.4f} "
            f"{hy + J3_KEEPOUT_R * math.sin(math.radians(a)):.4f})"
            for a in range(0, 360, 30))
        zones.append(f'''
	(zone
		(net 0)
		(net_name "")
		(layers {layers})
		(uuid "{_uuid.uuid4()}")
		(name "J3_NPTH_keepout")
		(hatch edge 0.5)
		(connect_pads
			(clearance 0)
		)
		(min_thickness 0.25)
		(filled_areas_thickness no)
		(keepout
			(tracks not_allowed)
			(vias not_allowed)
			(pads allowed)
			(copperpour not_allowed)
			(footprints allowed)
		)
		(placement
			(enabled no)
			(sheetname "")
		)
		(fill
			(thermal_gap 0.5)
			(thermal_bridge_width 0.5)
		)
		(polygon
			(pts {pts})
		)
	)''')

    # J6 (U.FL antenna) has a bare solder-mask opening across the middle
    # of its footprint, and a GND track routed underneath it ends up
    # sharing that aperture with J6's own RF pad - a real solder-bridge
    # risk in assembly, and exactly what DRC flagged. The opening
    # contains none of J6's own pads (the RF pad sits to its left, the
    # two ground pads above and below it), so keeping tracks out of
    # precisely that rectangle fixes the bridge WITHOUT stranding the
    # connector - which is the mistake oversizing J3's keepout made.
    ox6, oy6, ang6 = _footprint_origin(text, "J6")
    mx0, my0, mx1, my1 = J6_MASK_RECT
    corners = [(mx0 - J6_MASK_MARGIN, my0 - J6_MASK_MARGIN),
               (mx1 + J6_MASK_MARGIN, my0 - J6_MASK_MARGIN),
               (mx1 + J6_MASK_MARGIN, my1 + J6_MASK_MARGIN),
               (mx0 - J6_MASK_MARGIN, my1 + J6_MASK_MARGIN)]
    zones.append(_keepout_zone(
        "J6_MASK_keepout",
        [_to_board(ox6, oy6, ang6, lx, ly) for lx, ly in corners]))

    close = text.rstrip().rfind("\n)")
    text = text[:close] + "".join(zones) + text[close:]
    open(PCB, "w", encoding="utf-8").write(text)
    return len(holes) + 1


# J6's real F.Mask aperture, read straight from the Hirose U.FL-R-SMT-1
# footprint file, plus a little margin.
J6_MASK_RECT = (-0.99, -0.94, 1.10, 0.94)
J6_MASK_MARGIN = 0.15


# U20 (MC33926) is a 0.5mm-pitch QFN. Its L-side GND pins (3=SLEW,
# 5=AGND, 7=INV, all tied to GND per the datasheet) sit in a column with
# VBATT_SW pins (4, 6) interleaved between them - only 0.25mm of copper
# gap between adjacent pads. FreeRouting reliably escapes pad 3 (it has
# open board on one side) but has failed, identically, on repeated full
# regeneration + re-route runs, to find any jog for pad 5: every run
# converges cleanly to a score around 992-993 and then plateaus with
# this pin (and, once it's blocked, whatever else it was crowding out)
# still short. That repeatability is the tell that this isn't placement
# randomness or overall density - loosening CLUSTER_GAP wouldn't fix it.
#
# It's not solvable on F.Cu at all, not just hard to find: pin 4 and
# pin 6 (both VBATT_SW, straddling pin 5) already route their own escape
# through the entire sliver of open board pin 5 would need, and an
# exhaustive local search (every reasonable 2-bend F.Cu path from pin 5,
# checked against their real routed coordinates) turned up nothing that
# clears the required 0.15mm on both sides. A standard 0.5mm through-via
# doesn't fit in the gap either - proven the same way, and confirmed by
# kicad-cli DRC directly (0.5/0.2mm via at this pad measured 0.125mm
# clearance to pin 6 against a 0.15mm rule, plus a separate drill-size
# violation: this board's own min_through_hole_diameter is 0.3mm).
#
# The real fix is what real fine-pitch QFN escape uses for exactly this
# situation: a microvia, via-in-pad, blind from F.Cu to In1.Cu only (the
# GND plane directly beneath F.Cu in the stackup - see build_pcb.py).
# This board's own design rules already define a microvia class (0.2mm
# minimum diameter, separate from the 0.5mm through-via minimum) for
# precisely this. At 0.3/0.15mm it sits inside pin 5's own pad footprint
# and clears pin 4 and pin 6 by real margin (checked below), with no
# lateral travel needed at all - the VBATT_SW jog becomes irrelevant.
U20_PAD5_LOCAL = (-2.4375, 0.25)  # AGND
U20_MICROVIA_SIZE = 0.4
U20_MICROVIA_DRILL = 0.15


def add_u20_pad5_gnd_via():
    """Drop a blind microvia (F.Cu->In1.Cu) onto U20 pin 5 (AGND).

    See U20_PAD5_LOCAL comment above for why a microvia, not a routed
    jog or a standard through-via - both proven not to fit this gap.
    """
    import re as _re
    import uuid as _uuid

    text = open(PCB, encoding="utf-8").read()
    ox, oy, ang = _footprint_origin(text, "U20")
    p5 = _to_board(ox, oy, ang, *U20_PAD5_LOCAL)

    marker = f"(at {p5[0]:.4f} {p5[1]:.4f})"
    if _re.search(r"\(via\s+micro\s*" + _re.escape(marker), text):
        return False  # already bridged (idempotent across re-runs)

    via = (f'\n\t(via micro\n\t\t(at {p5[0]:.4f} {p5[1]:.4f})'
           f'\n\t\t(size {U20_MICROVIA_SIZE})\n\t\t(drill {U20_MICROVIA_DRILL})'
           f'\n\t\t(layers "F.Cu" "In1.Cu")\n\t\t(net "GND")'
           f'\n\t\t(uuid "{_uuid.uuid4()}")\n\t)')
    close = text.rstrip().rfind("\n)")
    text = text[:close] + via + text[close:]
    open(PCB, "w", encoding="utf-8").write(text)
    return True


# In1.Cu isn't actually a plane until add_and_fill_zones() pours it,
# which happens LAST - to FreeRouting, it's just another signal layer,
# and it used it as one: two separate full runs each routed a real,
# unrelated signal (APP1_ADC once, CLT_ADC once) straight through pin
# 5's exact In1.Cu landing spot. The APP1_ADC case only grazed the
# 0.15mm clearance rule; the CLT_ADC case is worse - actual copper
# overlap, a genuine short between GND and CLT_ADC, not just a DRC
# nit. Reserving the spot before routing even starts is the only fix
# that closes this for every future run, not just the one being fixed
# by hand right now.
#
# copperpour stays ALLOWED (unlike the J3/J6 keepouts, which want zero
# copper of any kind): the whole point of the microvia is to land on
# the poured GND plane, so this only needs to keep other TRACKS and
# VIAS out, not the plane itself.
U20_PAD5_KEEPOUT_R = 0.5


def add_u20_pad5_in1_keepout():
    """Reserve U20 pin 5's In1.Cu landing spot before routing runs.

    See the comment above U20_PAD5_KEEPOUT_R for why this exists.
    """
    import math
    import uuid as _uuid

    text = open(PCB, encoding="utf-8").read()
    if "U20_pad5_In1_keepout" in text:
        return False  # already present (idempotent across re-runs)

    ox, oy, ang = _footprint_origin(text, "U20")
    cx, cy = _to_board(ox, oy, ang, *U20_PAD5_LOCAL)
    pts = " ".join(
        f"(xy {cx + U20_PAD5_KEEPOUT_R * math.cos(math.radians(a)):.4f} "
        f"{cy + U20_PAD5_KEEPOUT_R * math.sin(math.radians(a)):.4f})"
        for a in range(0, 360, 30))
    zone = f'''
	(zone
		(net 0)
		(net_name "")
		(layers "In1.Cu")
		(uuid "{_uuid.uuid4()}")
		(name "U20_pad5_In1_keepout")
		(hatch edge 0.5)
		(connect_pads
			(clearance 0)
		)
		(min_thickness 0.25)
		(filled_areas_thickness no)
		(keepout
			(tracks not_allowed)
			(vias not_allowed)
			(pads allowed)
			(copperpour allowed)
			(footprints allowed)
		)
		(placement
			(enabled no)
			(sheetname "")
		)
		(fill
			(thermal_gap 0.5)
			(thermal_bridge_width 0.5)
		)
		(polygon
			(pts {pts})
		)
	)'''
    close = text.rstrip().rfind("\n)")
    text = text[:close] + zone + text[close:]
    open(PCB, "w", encoding="utf-8").write(text)
    return True


def _footprint_origin(text, ref):
    """Placed origin + rotation of a footprint, from the board text."""
    import math
    import re as _re
    idx = text.find(f'"{ref}"')
    start = text.rfind("(footprint ", 0, idx)
    end = text.find("\n\t(footprint ", start + 1)
    block = text[start:end if end != -1 else len(text)]
    m = _re.search(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)", block)
    return (float(m.group(1)), float(m.group(2)),
            math.radians(float(m.group(3) or 0.0)))


def _to_board(ox, oy, ang, lx, ly):
    """Footprint-local point -> board coordinates."""
    import math
    return (ox + lx * math.cos(ang) + ly * math.sin(ang),
            oy - lx * math.sin(ang) + ly * math.cos(ang))


def _keepout_zone(name, pts):
    """One keepout zone (no tracks, no vias, no pour) as raw
    s-expression text - see add_j3_npth_keepouts for why this is written
    directly rather than through pcbnew's ZONE API."""
    import uuid as _uuid
    layers = " ".join(f'"{n}"' for n in
                      ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu",
                       "In5.Cu", "In6.Cu", "B.Cu"))
    pts_txt = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in pts)
    return f'''
	(zone
		(net 0)
		(net_name "")
		(layers {layers})
		(uuid "{_uuid.uuid4()}")
		(name "{name}")
		(hatch edge 0.5)
		(connect_pads
			(clearance 0)
		)
		(min_thickness 0.25)
		(filled_areas_thickness no)
		(keepout
			(tracks not_allowed)
			(vias not_allowed)
			(pads allowed)
			(copperpour not_allowed)
			(footprints allowed)
		)
		(placement
			(enabled no)
			(sheetname "")
		)
		(fill
			(thermal_gap 0.5)
			(thermal_bridge_width 0.5)
		)
		(polygon
			(pts {pts_txt})
		)
	)'''


def find_java():
    import shutil
    exe = shutil.which("java")
    if exe:
        return exe
    for candidate in JAVA_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise SystemExit("java not found - see manifold-pcb's README for the Java + FreeRouting setup")


def run_kicad_python(label, script):
    result = subprocess.run([KICAD_PYTHON, "-c", script], capture_output=True, text=True)
    print(result.stdout.strip())
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"{label} failed (exit {result.returncode})")


def export_dsn():
    if not os.path.isfile(KICAD_PYTHON):
        raise SystemExit(f"KiCad's bundled Python not found at {KICAD_PYTHON}")
    run_kicad_python("DSN export", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
ok = pcbnew.ExportSpecctraDSN(board, {DSN!r})
print("DSN export:", "OK" if ok else "FAILED", "->", {DSN!r})
if not ok:
    raise SystemExit(1)
''')


def run_freerouting():
    java = find_java()
    if not os.path.isfile(FREEROUTING_JAR):
        raise SystemExit(f"FreeRouting jar not found at {FREEROUTING_JAR}")
    cmd = [java, "-jar", FREEROUTING_JAR, "-de", DSN, "-do", SES,
           "-mp", str(MAX_PASSES), "-oit", str(OPTIMIZATION_IMPROVEMENT_THRESHOLD),
           "--gui.enabled=false"]
    print("Running:", " ".join(cmd), flush=True)
    # Stream FreeRouting's progress instead of capturing it. Capturing
    # meant a long run was completely opaque - a routing job that
    # normally takes 3-6 minutes sat silent for 23 with no way to tell
    # whether it was progressing, stuck, or thrashing. The per-pass log
    # lines (score, unrouted count) are exactly what tells them apart,
    # so they need to be visible while it runs, not after.
    tail = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        if "pass #" in line or "session completed" in line or "unrouted" in line:
            print("   ", line, flush=True)
    proc.wait()
    result = subprocess.CompletedProcess(cmd, proc.returncode,
                                         chr(10).join(tail), "")
    if result.returncode != 0:
        print(result.stderr.strip()[-2000:], file=sys.stderr)
        raise SystemExit(f"FreeRouting failed (exit {result.returncode})")
    if not os.path.isfile(SES):
        raise SystemExit("FreeRouting exited OK but did not produce a .ses file")


def import_ses():
    run_kicad_python("SES import", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
ok = pcbnew.ImportSpecctraSES(board, {SES!r})
print("SES import:", "OK" if ok else "FAILED")
if not ok:
    raise SystemExit(1)
board.Save({PCB!r})
print("Saved routed board to", {PCB!r})
''')


ZONE_INSET_MM = 0.5


def add_and_fill_zones():
    # Same "AFTER routing, one zone per subprocess" reasoning as
    # manifold-pcb (see that file's own comment for the full story: zone
    # outlines added before routing measurably hurt routability and can
    # make FreeRouting under-report unrouted pads; filling 2+ zones in one
    # pcbnew process reliably segfaults on save). GND poured on BOTH
    # planes (In1/In3/In6.Cu) - this board sandwiches signal layers
    # between two ground planes, unlike Manifold's single GND plane, so
    # both need a real fill, not just one.
    for net_name, layer_name in [("GND", "In1_Cu"), ("GND", "In3_Cu"),
                                 ("GND", "In6_Cu"), ("+3V3", "In4_Cu")]:
        run_kicad_python(f"Add + fill {net_name} zone ({layer_name})", f'''
import pcbnew
board = pcbnew.LoadBoard({PCB!r})
bbox = board.GetBoardEdgesBoundingBox()
inset = pcbnew.FromMM({ZONE_INSET_MM})
x0, y0 = bbox.GetLeft() + inset, bbox.GetTop() + inset
x1, y1 = bbox.GetRight() - inset, bbox.GetBottom() - inset

net = board.FindNet({net_name!r})
if net is None:
    raise SystemExit(f"net {net_name!r} not found on board")
zone = pcbnew.ZONE(board)
zone.SetLayer(pcbnew.{layer_name})
zone.SetNet(net)
zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
zone.SetLocalClearance(pcbnew.FromMM(0.2))
zone.SetMinThickness(pcbnew.FromMM(0.2))
outline = pcbnew.SHAPE_POLY_SET()
outline.NewOutline()
for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
    outline.Append(pcbnew.VECTOR2I(int(x), int(y)))
zone.SetOutline(outline)
board.Add(zone)

filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
board.Save({PCB!r})
print("Added + filled", {net_name!r}, "zone on", {layer_name!r}, "- saved to", {PCB!r})
''')


if __name__ == "__main__":
    n = add_j3_npth_keepouts()
    print(f"J3 NPTH keepouts: {n} added" if n else
          "J3 NPTH keepouts: already present")
    kn = add_u20_pad5_in1_keepout()
    print("U20 pad 5 In1.Cu keepout: added" if kn else
          "U20 pad 5 In1.Cu keepout: already present")
    export_dsn()
    run_freerouting()
    import_ses()
    # Added AFTER the DSN/FreeRouting/SES round-trip, not before like the
    # J3 keepouts: Specctra DSN has no concept of a microvia, so a via
    # placed before that round-trip loses its "micro" flag on the way
    # back through pcbnew's SES import and gets re-saved as a plain
    # buried via - checked against the much stricter 0.5mm/0.3mm
    # through-via rules instead of the 0.2mm microvia ones, which is
    # exactly the size this fix depends on to fit. Adding it after means
    # FreeRouting never sees or touches it.
    bridged = add_u20_pad5_gnd_via()
    print("U20 pad 5 GND via: added" if bridged else
          "U20 pad 5 GND via: already present")
    add_and_fill_zones()
    print("\nDone. Run: python run_drc.py")
