"""
Generates ECU.kicad_pcb from ECU.kicad_sch: real footprints (loaded from
their actual .kicad_mod files, not reinvented) placed in non-overlapping,
section-grouped positions, with every pad assigned the net name kicad-cli's
own netlist export says it should have.

Direct extension of manifold-pcb/build_pcb.py's proven approach (same
footprint-loading, skyline bin-packing, and verification functions, reused
almost verbatim - see that file for the fully-worked original this is
derived from). The one real structural difference: Manifold packed all ~27
small parts in a SINGLE skyline against one big connector. ECU has 315
components across 8 very different subsystems (a 144-pin MCU, two 32-pin
driver ICs, 8 TO-220 IGBTs, two 64/48-pin QFN bridge chips, two 35-pos
harness connectors, etc) - packing everything into one flat skyline would
ignore the schematic's own real functional grouping and produce a board
that's needlessly hard to reason about or route. Instead: bucket every
part into one of 6 functional BLOCKS by its real schematic Y-position
(the schematic is already organized into these same 6 sections via
section_text - see build_schematic.py), skyline-pack each block
independently (reusing the identical packer functions), then arrange the
6 packed blocks in a 2-column grid. 5 connectors (J1/J3/J4/J5/J6) are
pulled out of the generic pack entirely and placed as deliberate anchors
at board edges, since they need real physical access (harness plugs,
USB-C port, BLE antenna, a bench JTAG probe) that a generic bin-packer
has no way to reason about.

What this does NOT do: route copper. Placement groups parts sensibly so a
human (or FreeRouting) has a sane starting point, but this is a
netlist-correct *unrouted* board - same state "Update PCB from Schematic"
leaves you in before you route it yourself.

Requires: ECU.kicad_sch to exist (run build_schematic.py first) and
kicad-cli to be installed (used for: netlist export as ground truth, and
`pcb upgrade` at the end to guarantee current-format output, same as
build_schematic.py does for the .kicad_sch).
"""
import os
import re
import shutil
import subprocess
import uuid as uuid_module

from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Net, Position, PageSettings
from kiutils.items.brditems import LayerToken
from kiutils.items.gritems import GrLine, GrArc

HERE = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(HERE, "ECU.kicad_sch")
PCB = os.path.join(HERE, "ECU.kicad_pcb")
KICAD_FOOTPRINTS = r"C:\Program Files\KiCad\10.0\share\kicad\footprints"
PROJECT_FOOTPRINTS = os.path.join(HERE, "footprints")


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


KICAD_CLI = find_kicad_cli()
if not KICAD_CLI:
    raise SystemExit("kicad-cli not found - needed for netlist export and pcb upgrade")

# ---------------------------------------------------------------------------
# 1. Ground truth: real ref/footprint list from the schematic, real net
#    assignments from kicad-cli's own netlist export (not re-derived from the
#    generator script's internal state, so this catches drift between the two
#    files just like the schematic's own self-checks do). Also grabs each
#    part's real schematic Y-position, used below purely to bucket it into
#    the right PCB placement block - has no other effect on the PCB itself.
# ---------------------------------------------------------------------------
from kiutils.schematic import Schematic
from kiutils.utils import sexpr

sch = Schematic.from_sexpr(sexpr.parse_sexp(open(SCH, encoding="utf-8").read()))
parts = {}  # ref -> {"footprint": "lib:name", "value": str, "uuid": str, "sch_y": float}
for inst in sch.schematicSymbols:
    ref = next(p.value for p in inst.properties if p.key == "Reference")
    if ref.startswith("#"):
        continue  # power-flag symbols aren't physical parts
    fp = next((p.value for p in inst.properties if p.key == "Footprint"), "")
    val = next((p.value for p in inst.properties if p.key == "Value"), "")
    parts[ref] = {"footprint": fp, "value": val, "uuid": inst.uuid,
                  "sch_x": inst.position.X, "sch_y": inst.position.Y}

NETLIST_PATH = os.path.join(os.environ.get("TEMP", HERE), "ecu_netlist_for_pcb.net")
result = subprocess.run([KICAD_CLI, "sch", "export", "netlist", "--format", "kicadsexpr",
                         "--output", NETLIST_PATH, SCH], capture_output=True, text=True)
if result.returncode != 0:
    raise SystemExit(f"netlist export failed: {result.stderr}")

netlist_txt = open(NETLIST_PATH, encoding="utf-8").read()
pad_net = {}  # (ref, pin) -> net_name
net_names = []
for block in re.split(r"\(net\s", netlist_txt)[1:]:
    name = re.search(r'\(name "([^"]+)"\)', block).group(1).lstrip("/")
    nodes = re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', block)
    if len(nodes) < 2:
        continue  # shouldn't happen (schematic validated earlier), skip defensively
    net_names.append(name)
    for ref, pin in nodes:
        pad_net[(ref, pin)] = name

print(f"Loaded {len(parts)} real parts and {len(net_names)} nets from the schematic/netlist.")

# ---------------------------------------------------------------------------
# 2. Footprint loading - identical to manifold-pcb's own functions.
# ---------------------------------------------------------------------------
def load_footprint(lib_colon_name):
    lib, _, name = lib_colon_name.partition(":")
    project_path = os.path.join(PROJECT_FOOTPRINTS, f"{lib}.pretty", f"{name}.kicad_mod")
    if os.path.isfile(project_path):
        path = project_path
    else:
        path = os.path.join(KICAD_FOOTPRINTS, f"{lib}.pretty", f"{name}.kicad_mod")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"footprint file not found: {path}")
    fp = Footprint.from_file(path)
    fp.libId = lib_colon_name
    return fp


def footprint_bbox(fp):
    """Bounding box (min/max X/Y) from this footprint's pads AND silkscreen/
    courtyard graphics, in its own local (unplaced) coordinate frame - see
    manifold-pcb's own version of this function for the full reasoning
    (pad-only bboxes undersell parts like right-angle THT connectors whose
    mechanical body sticks out well past their pads)."""
    xs, ys = [], []
    for pad in fp.pads:
        hw, hh = pad.size.X / 2, pad.size.Y / 2
        xs += [pad.position.X - hw, pad.position.X + hw]
        ys += [pad.position.Y - hh, pad.position.Y + hh]
    for item in fp.graphicItems:
        if hasattr(item, "start") and hasattr(item, "end"):
            xs += [item.start.X, item.end.X]
            ys += [item.start.Y, item.end.Y]
        elif hasattr(item, "coordinates"):
            xs += [p.X for p in item.coordinates]
            ys += [p.Y for p in item.coordinates]
        elif hasattr(item, "center"):
            r = ((item.end.X - item.center.X) ** 2 + (item.end.Y - item.center.Y) ** 2) ** 0.5
            xs += [item.center.X - r, item.center.X + r]
            ys += [item.center.Y - r, item.center.Y + r]
    if not xs:
        return (-2, -2, 2, 2)
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# 3. Board scaffold - 8 layers (was 6, was Manifold's 4).
#
# The move from 6 to 8 was driven by a measured routing failure, not by
# preference. On 6 layers this board has only THREE signal layers
# (F/In3/B) for 209 nets, and that turned out to be the binding
# constraint on how small the board could be: every attempt to shrink
# below ~120 mm^2/part left FreeRouting unable to finish, even though
# the components themselves only cover ~5000 mm^2 of a ~24000 mm^2
# board. The floor was never component area - it was channel space.
#
# 8 layers gives FOUR signal layers (F/In2/In5/B), a 33% increase in
# routing capacity, plus a third ground plane. Arrangement:
#
#   F.Cu    signal
#   In1.Cu  GND        <- reference for F.Cu
#   In2.Cu  signal
#   In3.Cu  GND        <- reference for In2.Cu
#   In4.Cu  +3V3       <- power plane
#   In5.Cu  signal
#   In6.Cu  GND        <- reference for In5.Cu and B.Cu
#   B.Cu    signal
#
# Every signal layer is adjacent to a solid reference plane, which is
# what this board actually needs: it carries a USB 2.0 differential
# pair, a 2.4GHz BLE feed, two CAN buses and multiple switching
# supplies. An alternative 5-signal-layer split (stacking two signal
# layers back to back) would route even more easily but puts adjacent
# signal layers with no plane between them - not worth the crosstalk on
# a board with real high-speed content and a radio.
# ---------------------------------------------------------------------------
board = Board.create_new()
for _i in range(1, 7):
    board.layers.insert(_i, LayerToken(ordinal=_i, name=f'In{_i}.Cu', type='signal'))
GND_PLANE_LAYERS = ("In1.Cu", "In3.Cu", "In6.Cu")
POWER_PLANE_LAYER = "In4.Cu"
SIGNAL_LAYERS = ("F.Cu", "In2.Cu", "In5.Cu", "B.Cu")
print(f"Stackup: 8 layers - {len(SIGNAL_LAYERS)} signal ({'/'.join(SIGNAL_LAYERS)}), "
      f"{len(GND_PLANE_LAYERS)} GND planes, 1 power plane ({POWER_PLANE_LAYER}). "
      f"Real net count: {len(net_names)}.")

net_registry = {}  # name -> number
def net_number(name):
    if name not in net_registry:
        n = len(net_registry) + 1
        net_registry[name] = n
        board.nets.append(Net(number=n, name=name))
    return net_registry[name]


# ---------------------------------------------------------------------------
# 4. Placement: 6 functional blocks (bucketed by each part's real schematic
#    Y-position - the schematic is already organized into these same
#    sections, see build_schematic.py's section_text calls), each packed
#    independently with the identical skyline algorithm manifold-pcb
#    proved out, then arranged in a 2-column grid. 5 connectors are
#    deliberate anchors, not part of any block's generic pack - see the
#    module docstring for why.
# ---------------------------------------------------------------------------
MARGIN = 2.0

ANCHOR_REFS = {"J1", "J3", "J4", "J5", "J6"}

# (block name, sch_y upper bound EXCLUSIVE, target pack width mm)
# Widths are a first real estimate based on each block's actual real part
# mix (e.g. INJ_IGN needs to fit 8 TO-220 IGBTs + 2 32-pin SOIC side by
# side, MCU needs to fit a 20x20mm 144-LQFP) - confirmed/adjusted against
# this script's own printed used_w/used_h on the first real run, same
# empirical-tuning approach Manifold used for its own pack_width.
BLOCK_DEFS = [
    ("POWER", 330.0, 110.0),
    ("MCU", 670.0, 100.0),
    ("INJ_IGN", 1360.0, 170.0),
    ("SENSOR", 1670.0, 150.0),
    ("CAN", 1940.0, 90.0),
    ("PROGRAMMING", 2230.0, 110.0),
]


def block_for(ref):
    y = parts[ref]["sch_y"]
    for name, upper, _ in BLOCK_DEFS:
        if y < upper:
            return name
    return "PROGRAMMING"  # shouldn't happen, defensive fallback


blocks = {name: [] for name, _, _ in BLOCK_DEFS}
for ref in parts:
    if ref in ANCHOR_REFS:
        continue
    blocks[block_for(ref)].append(ref)

for name, _, _ in BLOCK_DEFS:
    print(f"  block {name}: {len(blocks[name])} parts")


def skyline_pack(refs, max_width, margin, sort_key=None, initial_skyline=None):
    """Skyline bottom-left bin-packing - IDENTICAL algorithm to
    manifold-pcb/build_pcb.py's own function (see that file for the full,
    heavily-commented derivation of every design choice here). Reused
    verbatim, not reinvented: parts largest-first, tried both unrotated
    and rotated 90deg, placed wherever it yields the lowest resulting top
    edge."""
    sized = []
    for ref in refs:
        fp = load_footprint(parts[ref]["footprint"])
        x0, y0, x1, y1 = footprint_bbox(fp)
        sized.append((ref, x1 - x0, y1 - y0, x0, y0, x1, y1))
    sized.sort(key=sort_key or (lambda t: t[1] * t[2]), reverse=True)

    skyline = initial_skyline if initial_skyline is not None else [(0.0, max_width, 0.0)]

    def profile_height(x, w):
        h = 0.0
        for sx, sw, sh in skyline:
            if sx + sw <= x + 1e-9 or sx >= x + w - 1e-9:
                continue
            h = max(h, sh)
        return h

    def best_position(w):
        best = None
        candidates = set()
        for sx, sw, sh in skyline:
            candidates.add(sx)
            candidates.add(sx + sw - w)
        for x in candidates:
            if x < -1e-9 or x + w > max_width + 1e-9:
                continue
            y = profile_height(x, w)
            if best is None or (y, x) < (best[0], best[1]):
                best = (y, x)
        return best

    def update_skyline(x, w, top):
        x_end = x + w
        segs = []
        for sx, sw, sh in skyline:
            s_end = sx + sw
            if s_end <= x + 1e-9 or sx >= x_end - 1e-9:
                segs.append((sx, sw, sh))
                continue
            if sx < x:
                segs.append((sx, x - sx, sh))
            if s_end > x_end:
                segs.append((x_end, s_end - x_end, sh))
        segs.append((x, w, top))
        segs.sort(key=lambda t: t[0])
        merged = []
        for seg in segs:
            if merged and abs(merged[-1][0] + merged[-1][1] - seg[0]) < 1e-6 \
                    and abs(merged[-1][2] - seg[2]) < 1e-6:
                merged[-1] = (merged[-1][0], merged[-1][1] + seg[1], merged[-1][2])
            else:
                merged.append(seg)
        return merged

    placed = {}
    rotated = set()
    for ref, w, h, x0, y0, x1, y1 in sized:
        options = []
        pos0 = best_position(w + margin)
        if pos0 is not None:
            y, x = pos0
            options.append((y + h + margin, x, False))
        pos90 = best_position(h + margin)
        if pos90 is not None:
            y, x = pos90
            options.append((y + w + margin, x, True))
        if not options:
            raise RuntimeError(f"skyline_pack: {ref} ({w:.1f}x{h:.1f}mm) doesn't "
                                f"fit in max_width={max_width:.1f}mm even alone")
        options.sort(key=lambda o: (o[0], o[1]))
        top, x, is_rot = options[0]
        if is_rot:
            rw, rh = h + margin, w + margin
            skyline = update_skyline(x, rw, top)
            placed[ref] = (x - y0, (top - rh) + x1)
            rotated.add(ref)
        else:
            rw, rh = w + margin, h + margin
            skyline = update_skyline(x, rw, top)
            placed[ref] = (x - x0, (top - rh) - y0)

    used_w, used_h = 0.0, 0.0
    for ref, w, h, x0, y0, x1, y1 in sized:
        px, py = placed[ref]
        if ref in rotated:
            used_w = max(used_w, px + y1)
            used_h = max(used_h, py - x0)
        else:
            used_w = max(used_w, px + x1)
            used_h = max(used_h, py + y1)
    return placed, rotated, used_w, used_h


def best_skyline_pack(refs, max_width, margin, initial_skyline=None, quiet=False):
    """Tries several orderings, keeps whichever packs shortest - identical
    to manifold-pcb's own function (plus a quiet flag, since this now
    runs once per passive CLUSTER and would otherwise print ~40 lines
    per block)."""
    strategies = {
        "area-desc": lambda t: t[1] * t[2],
        "max-side-desc": lambda t: max(t[1], t[2]),
        "height-desc": lambda t: t[2],
        "width-desc": lambda t: t[1],
        "perimeter-desc": lambda t: t[1] + t[2],
    }
    best_name, best_result = None, None
    for name, key in strategies.items():
        result = skyline_pack(refs, max_width, margin, sort_key=key, initial_skyline=initial_skyline)
        used_w, used_h = result[2], result[3]
        if best_result is None or (used_h, used_w) < (best_result[3], best_result[2]):
            best_name, best_result = name, result
    if not quiet:
        print(f"  skyline pack ({len(refs)} parts): tried {len(strategies)} orderings, "
              f"best was '{best_name}' ({best_result[2]:.1f}x{best_result[3]:.1f}mm used)")
    return best_result


# --- pack each block's ANCHOR parts only (real ICs/connectors/crystals/
#     relay/TO-220s - anything big) via the tight skyline pack. Small
#     FILLER parts (0603 R/C, SOT-23 transistors, small diodes) are
#     deliberately NOT folded into each block's own rectangle here -
#     see the GLOBAL fill pass below (after all 6 blocks + J1 are
#     arranged) for why.
#
# REAL LESSON from the first version of this script (which DID fold
# fillers into each block's rectangle before handing block sizes to the
# outer arranger): baking fillers in first inflates every block into a
# bigger, irregular rectangle before the 6 blocks ever get arranged
# against each other - and bin-packing 6 large, differently-shaped
# rectangles side by side is exactly what leaves big, real, USABLE dead
# space between them (visible and flagged directly: a rendered board
# with large empty rectangles boxed in red between blocks). A human
# laying out a board doesn't build each subsystem as a sealed box either
# - they place the chips first, see what room is actually left
# BOARD-WIDE, then thread passives into whichever gap is closest,
# including gaps that straddle two subsystems' territory. So: pack only
# the big anchors per block (small, tight rectangles), arrange THOSE
# against each other first, and only after that's final, thread every
# filler into the nearest real gap anywhere on the board - not confined
# to its own block's rectangle.
ANCHOR_AREA_THRESHOLD = 20.0  # mm^2 - real dividing line between "big part"
                               # (144-LQFP=400mm^2, TO-220~120mm^2, 32-pin
                               # SOIC~85mm^2, SOT-23~10mm^2 falls below) and
                               # "small part" (0603~1mm^2, SOD-123~3mm^2)


# PERFORMANCE fix found once the interior-gap-preferring search (below)
# started actually running a full search out to max_radius for parts
# with no nearby interior gap: every candidate point during that search
# was calling load_footprint() - a real file read + s-expression parse
# - for the SAME footprint file over and over (many refs share one
# footprint, e.g. dozens of 0603 resistors), making an already O(radius)
# search also O(disk I/O) per candidate. bbox is a pure function of
# which footprint FILE a part uses, not of the specific ref, so it's
# safe to cache by footprint name - unlike load_footprint()'s own
# return value, which callers elsewhere mutate per-instance (position,
# nets) and must stay uncached.
_bbox_cache = {}


def cached_bbox(footprint_name):
    if footprint_name not in _bbox_cache:
        _bbox_cache[footprint_name] = footprint_bbox(load_footprint(footprint_name))
    return _bbox_cache[footprint_name]


def part_area(ref):
    x0, y0, x1, y1 = cached_bbox(parts[ref]["footprint"])
    return (x1 - x0) * (y1 - y0)


COMMON_NETS = {"GND", "+3V3", "+5V", "VIN", "VIN_PROT", "VBATT_SW",
               "VBATT_INJ", "VBATT_IGN"}


def ref_nets(ref):
    return {name for (r, p), name in pad_net.items() if r == ref}


def find_home_anchor(filler_ref, anchor_refs):
    """Which anchor does this filler belong to?

    First choice is real net sharing: a cap between an IC's own labeled
    supply net and GND shares that net with exactly that IC; a gate
    resistor shares the gate net with its transistor.

    Second choice is SCHEMATIC PROXIMITY, and it matters more than it
    sounds. A plain +5V-to-GND decoupling cap touches nothing but common
    power rails, so net analysis alone can say nothing about it - yet
    those are precisely the parts that most need to sit next to their
    chip, since a decoupling cap placed far from the pin it decouples
    isn't decoupling anything. The schematic already encodes the answer:
    build_schematic.py deliberately draws each IC's decouplers right
    beside it, so nearest-symbol-in-the-schematic recovers the intent
    that the netlist genuinely cannot express. Before this fallback
    existed, 34 of the SENSOR block's 91 parts fell through as
    unattached and got dumped in one anonymous pile."""
    fn = ref_nets(filler_ref) - COMMON_NETS
    if fn:
        best, best_score = None, 0
        for a in anchor_refs:
            score = len(fn & ref_nets(a))
            if score > best_score:
                best, best_score = a, score
        if best is not None:
            return best
    if not anchor_refs:
        return None
    fx, fy = parts[filler_ref]["sch_x"], parts[filler_ref]["sch_y"]
    return min(anchor_refs,
               key=lambda a: (parts[a]["sch_x"] - fx) ** 2
                             + (parts[a]["sch_y"] - fy) ** 2)


def rotate_bbox(x0, y0, x1, y1, angle):
    """Rotate a local (unplaced) bounding box by a multiple of 90deg
    about its own origin. angle=90 is the exact transform this file
    already used (and had verified via DRC) for skyline-packed parts;
    180/270 are that same transform composed with itself 2x/3x, not a
    separate derivation - real rotation composition, not a guess (J3,
    the USB-C connector, needs a real 180 deg flip so its plug faces
    OUT of the board for real from-outside-the-enclosure access,
    the first case this file has needed anything other than 0/90)."""
    if angle == 90:
        return y0, -x1, y1, -x0
    if angle == 180:
        return -x1, -y1, -x0, -y0
    if angle == 270:
        return -y1, x0, -y0, x1
    return x0, y0, x1, y1


def fp_box_at(ref, x, y, angle, margin):
    x0, y0, x1, y1 = cached_bbox(parts[ref]["footprint"])
    x0, y0, x1, y1 = rotate_bbox(x0, y0, x1, y1, angle)
    return (x + x0 - margin, y + y0 - margin, x + x1 + margin, y + y1 + margin)


def boxes_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def pack_rects(items, max_width, gap, initial_skyline=None, keep_order=False):
    """Generic bottom-left skyline packer for plain rectangles - the same
    proven algorithm as skyline_pack, minus the footprint/rotation
    handling. Used at BOTH levels of the placement hierarchy now:
    passive clusters into a block, and blocks into the board."""
    wh = {n: (w, h) for n, w, h in items}
    if keep_order:
        order = list(items)
    else:
        order = sorted(items, key=lambda t: t[1] * t[2], reverse=True)
    skyline = list(initial_skyline) if initial_skyline else [(0.0, max_width, 0.0)]

    def profile_height(x, w):
        h = 0.0
        for sx, sw, sh in skyline:
            if sx + sw <= x + 1e-9 or sx >= x + w - 1e-9:
                continue
            h = max(h, sh)
        return h

    def best_position(w):
        best = None
        cands = {sx for sx, sw, sh in skyline} | {sx + sw - w for sx, sw, sh in skyline}
        for x in cands:
            if x < -1e-9 or x + w > max_width + 1e-9:
                continue
            y = profile_height(x, w)
            if best is None or (y, x) < (best[0], best[1]):
                best = (y, x)
        return best

    def update_skyline(x, w, top):
        x_end = x + w
        segs = []
        for sx, sw, sh in skyline:
            s_end = sx + sw
            if s_end <= x + 1e-9 or sx >= x_end - 1e-9:
                segs.append((sx, sw, sh))
                continue
            if sx < x:
                segs.append((sx, x - sx, sh))
            if s_end > x_end:
                segs.append((x_end, s_end - x_end, sh))
        segs.append((x, w, top))
        segs.sort(key=lambda t: t[0])
        merged = []
        for seg in segs:
            if merged and abs(merged[-1][0] + merged[-1][1] - seg[0]) < 1e-6                     and abs(merged[-1][2] - seg[2]) < 1e-6:
                merged[-1] = (merged[-1][0], merged[-1][1] + seg[1], merged[-1][2])
            else:
                merged.append(seg)
        return merged

    origins = {}
    for name, w, h in order:
        pos = best_position(w + gap)
        if pos is None:
            return None
        y, x = pos
        skyline = update_skyline(x, w + gap, y + h + gap)
        origins[name] = (x, y)
    used_w = max(x + wh[n][0] for n, (x, y) in origins.items())
    used_h = max(y + wh[n][1] for n, (x, y) in origins.items())
    return origins, used_w, used_h


def best_pack_rects(items, gap, width_guesses, initial_skyline_fn=None,
                    keep_order=False):
    """Try several target widths, keep whichever packs smallest by area."""
    best = None
    for wg in width_guesses:
        widest = max(w for _, w, _ in items)
        mw = max(wg, widest)
        r = pack_rects(items, mw, gap,
                       initial_skyline_fn(mw) if initial_skyline_fn else None,
                       keep_order=keep_order)
        if r is None:
            continue
        origins, uw, uh = r
        if best is None or (uw * uh) < (best[1] * best[2]):
            best = (origins, uw, uh)
    return best


# --- CLUSTER-BASED placement -------------------------------------------------
# REAL BUG, spotted immediately by eye on the rendered board ("the
# caps/resistors look crazy, this can't be right") and completely
# correct: the previous approach packed all the big anchor parts TIGHT
# first and only then went looking for somewhere to put each passive.
# But a tight anchor pack leaves no room BETWEEN the anchors - so every
# passive got pushed out to whatever hole existed somewhere else on the
# board, ending up tens of mm from the chip it belongs to, with a long
# trace back. The result looked like confetti, and no real board is laid
# out that way: a decoupling cap belongs within a few mm of the pin it
# decouples, not across the board from it.
#
# The fix is to stop treating "big parts" and "small parts" as two
# separate placement problems. Each anchor and the passives that are
# electrically ITS (via the same real net-sharing analysis as before)
# are packed together as ONE unit - a cluster - and it is the CLUSTERS
# that then get arranged inside a block, and the blocks inside the
# board. Passives physically cannot drift away from their chip now,
# because they are packed into the same rectangle as it before that
# rectangle is ever placed. This also deleted the whole spiral
# gap-search and its follow-up compaction pass (~120 lines): with
# clustering they solve a problem that no longer exists.
CLUSTER_PAD = 12.0    # extra width beyond the anchor for its passives to flank it


def cluster_width(anchor_ref):
    x0, _, x1, _ = cached_bbox(parts[anchor_ref]["footprint"])
    return max((x1 - x0) + CLUSTER_PAD, 16.0)


# --- Build every cluster, from every block, into ONE flat list -------------
# REAL dead-space fix (user: "we should still be trying to fill all that
# space... you should be able to shrink this board a lot"). The previous
# version packed clusters into a rectangle PER BLOCK, then packed those 6
# block rectangles onto the board. That is two levels of rectangle
# packing, and each level rounds its contents up to a bounding box - so
# the board paid for wasted space twice over, which is exactly what the
# leftover slabs in the corners were. Blocks earn their keep as a
# GROUPING concept (they decide what clusters exist, and clusters are
# what keep a subsystem's parts together), but there is no reason for a
# block to also be a rigid rectangle on the board. So: build the
# clusters per block as before, then throw them all into a single
# board-level pack. One level of packing, one level of rounding.
def build_clusters():
    """All clusters for the whole board, as {name: (rel, rotated, w, h)}."""
    out = {}
    for name, _, width in BLOCK_DEFS:
        refs = blocks[name]
        if not refs:
            continue
        anchor_refs = [r for r in refs if part_area(r) >= ANCHOR_AREA_THRESHOLD]
        filler_refs = [r for r in refs if part_area(r) < ANCHOR_AREA_THRESHOLD]

        if not anchor_refs:
            out[f"{name}@flat"] = best_skyline_pack(
                refs, max_width=width, margin=MARGIN, quiet=True)
            continue

        # Each filler joins the cluster of whichever anchor it actually
        # shares a real (non-power) net with - or, failing that, the one
        # nearest it in the schematic (see find_home_anchor).
        groups = {a: [] for a in anchor_refs}
        orphans = []
        for f in filler_refs:
            home = find_home_anchor(f, anchor_refs)
            (groups[home] if home is not None else orphans).append(f)

        # Chips that TALK TO EACH OTHER share a cluster too. Clustering an
        # anchor with its own passives fixed the confetti, but nothing
        # made two ICs that are wired together end up near each other,
        # since cluster order in the packer is by area. That bit for real:
        # a route failed to connect FT_RXD because the arbitration switch
        # (U15) and USB bridge (U13) landed 28mm apart with the BLE chip
        # between them.
        parent = {a: a for a in anchor_refs}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(anchor_refs):
            for b in anchor_refs[i + 1:]:
                if len((ref_nets(a) - COMMON_NETS)
                       & (ref_nets(b) - COMMON_NETS)) >= ANCHOR_LINK_NETS:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[ra] = rb

        merged = {}
        for a in anchor_refs:
            merged.setdefault(find(a), []).append(a)

        for root, members in merged.items():
            cparts = []
            for a in members:
                cparts.append(a)
                cparts.extend(groups[a])
            out[f"{name}@{root}"] = pack_cluster(
                cparts, sum(cluster_width(a) for a in members))
        if orphans:
            # Parts touching only common power rails have no single owning
            # chip - one shared cluster per block rather than forcing them
            # onto an anchor they aren't really associated with.
            out[f"{name}@shared"] = best_skyline_pack(
                orphans, max_width=max(30.0, width / 3.0), margin=MARGIN, quiet=True)
    return out


ANCHOR_LINK_NETS = 2


def pack_cluster(cparts, base_w):
    """Pack one cluster, trying several aspect ratios and keeping the
    smallest. Without this a merged multi-chip cluster comes out as one
    long thin row (width defaulting to the sum of its members'), which
    then tessellates badly against everything else."""
    floor = max(min(cached_bbox(parts[r]["footprint"])[2]
                    - cached_bbox(parts[r]["footprint"])[0],
                    cached_bbox(parts[r]["footprint"])[3]
                    - cached_bbox(parts[r]["footprint"])[1])
                for r in cparts) + 2 * MARGIN
    # REAL BUG, and a nasty one because every individual step looked
    # right: picking purely the smallest-AREA pack lets a cluster settle
    # on an absurd aspect ratio. The two-CJ125 sensor cluster came out
    # 13.2 x 100.3mm - a 100mm sliver - because that happened to beat a
    # squarer arrangement on raw area by a few percent. One such sliver
    # then sets the height of the ENTIRE board: every board-width
    # candidate from 110mm to 160mm returned the identical 104.3mm
    # height, because nothing could pack shorter than that one cluster.
    # Area is the wrong objective for a cluster; a cluster has to
    # TESSELLATE with others, so shape matters as much as size. Prefer
    # the smallest area among reasonably-proportioned packs, and only
    # fall back to raw minimum area if nothing is well-proportioned.
    MAX_ASPECT = 2.5
    cands = []
    for f in (0.3, 0.45, 0.6, 0.75, 0.9, 1.1):
        r = best_skyline_pack(cparts, max_width=max(base_w * f, floor),
                              margin=MARGIN, quiet=True)
        cands.append(r)
    def aspect(r):
        lo, hi = min(r[2], r[3]), max(r[2], r[3])
        return hi / lo if lo > 1e-6 else 1e9
    ok = [r for r in cands if aspect(r) <= MAX_ASPECT]
    return min(ok or cands, key=lambda r: r[2] * r[3])


all_clusters = build_clusters()
print(f"Built {len(all_clusters)} clusters covering "
      f"{sum(len(c[0]) for c in all_clusters.values())} parts.")

# --- arrange EVERY cluster in one shared skyline ---------------------------
# One level of packing instead of two (see build_clusters above). J1 (the
# bench JTAG header) rides along as its own one-part cluster rather than
# being positioned by hand - the packer guarantees no collision, manual
# coordinate guessing doesn't, which is exactly how an earlier version
# managed to drop J1 on top of C3/K1.
# Routing channels between clusters. A ROUTABILITY knob, not a cosmetic
# one, and it was set the hard way: at 2.5 the single-level cluster pack
# produced a genuinely tight 166x91mm board (76 mm^2/part) that
# FreeRouting then could NOT route - it burned 3400+ CPU-seconds without
# finishing, against ~200 for a normal complete run, because there were
# no channels left to route through. For reference the previous board
# routed cleanly in ~3 minutes at 125 mm^2/part, and Manifold sits at
# 169. The value here was found by binary-searching against the ONLY
# test that matters - whether FreeRouting actually finishes:
#    76 mm^2/part  thrashed, 3400+ CPU-s, never finished
#    95 mm^2/part  thrashed, never finished
#   106 mm^2/part  finished but left 6 nets unrouted
#   121 mm^2/part  clean
# Those figures are all for the ORIGINAL 6-layer stackup, which
# gave only 3 signal layers. The board is now 8 layers / 4 signal
# layers (+33% routing capacity), so the floor moves down and this
# value is being re-searched against the same test.
# The parts themselves only cover ~5000 mm^2 of the ~24000 mm^2
# board, so the floor here is NOT component area - it is the
# channel space 209 nets need on a 6-layer stackup. Shrinking past
# that just produces a board nobody can route. A placement that
# cannot be routed is not a smaller board, it is a broken one.
CLUSTER_GAP = 5.5

_j1_fp = load_footprint(parts["J1"]["footprint"])
_j1x0, _j1y0, _j1x1, _j1y1 = footprint_bbox(_j1_fp)
all_clusters["J1@edge"] = ({"J1": (-_j1x0, -_j1y0)}, set(),
                           _j1x1 - _j1x0, _j1y1 - _j1y0)

# The connector strip along the bottom is ~158mm wide whatever the rest
# of the board does, so widths below it buy nothing and widths above it
# cost real estate on every board. Score candidates on the REAL total
# (parts area + that strip), not on the parts area alone.
_amp_fp = load_footprint(parts["J4"]["footprint"])
_ax0, _ay0, _ax1, _ay1 = footprint_bbox(_amp_fp)
ANCHOR_GAP = 3.0
AMPSEAL_PCB_EDGE_Y = 13.5
CONN_STRIP_W = (_ax1 - _ax0) * 2 + ANCHOR_GAP
CONN_STRIP_H = ANCHOR_GAP + AMPSEAL_PCB_EDGE_Y

def anchor_size(ref):
    fp = load_footprint(parts[ref]["footprint"])
    x0, y0, x1, y1 = footprint_bbox(fp)
    return fp, x0, y0, x1, y1


J3_J6_GAP = 10.0
# Real F.Fab front face of the Amphenol USB-C receptacle (signal
# pads at the REAR, local Y -5.02/-3.32; front shield legs +2.84).
USB_C_FACE_OFFSET = 5.23

# Reserve only the CORNER J3/J6 actually occupy, not a full-width band.
# They have to sit on the top edge (a USB port and an antenna lead have
# to reach the outside world), and previously they were simply placed
# above everything else - which cost a full board-width strip ~26mm tall
# for two parts spanning ~30mm. Seeding the packer's skyline with just
# their own footprint lets every other cluster pack up alongside them
# instead, and the strip disappears.
# Board edge margin. Hoisted here because the cluster packer's own
# initial skyline has to know it: J3's mating face sits ON the top
# edge (zero margin - that's the point of an edge connector), but
# every ordinary part still needs its normal margin, so clusters
# start one BOARD_MARGIN below that line rather than at it.
BOARD_MARGIN = 4.0
J3_TOP_GAP = 2.0
_j3fp, _j3x0, _j3y0, _j3x1, _j3y1 = anchor_size("J3")
_j6fp, _j6x0, _j6y0, _j6x1, _j6y1 = anchor_size("J6")
_j3rx0, _j3ry0, _j3rx1, _j3ry1 = rotate_bbox(_j3x0, _j3y0, _j3x1, _j3y1, 180)
TOP_RESERVE_W = (_j3rx1 - _j3rx0) + J3_J6_GAP + (_j6x1 - _j6x0) + CLUSTER_GAP
TOP_RESERVE_H = max(USB_C_FACE_OFFSET + _j3ry1,
                    BOARD_MARGIN + (_j6y1 - _j6y0)) + J3_TOP_GAP


def _top_reserve_skyline(max_width):
    if TOP_RESERVE_W >= max_width:
        return [(0.0, max_width, TOP_RESERVE_H)]
    return [(0.0, TOP_RESERVE_W, TOP_RESERVE_H),
            (TOP_RESERVE_W, max_width - TOP_RESERVE_W, BOARD_MARGIN)]


# Order clusters by SUBSYSTEM, then largest-first within it, and make
# the packer honour that order instead of re-sorting globally by area.
#
# This matters for routability, not looks. Flattening the old
# per-block rectangles removed the double space-rounding (good), but it
# also removed the only thing keeping a subsystem's clusters near each
# other: a pure largest-first global sort interleaves clusters from
# unrelated blocks, so nets that used to be local suddenly span the
# board. That is very likely what left FreeRouting unable to finish
# even after density was relaxed - the board got harder to route in a
# way no area number shows.
_block_rank = {name: i for i, (name, _, _) in enumerate(BLOCK_DEFS)}
_items = sorted(
    [(cn, c[2], c[3]) for cn, c in all_clusters.items()],
    key=lambda t: (_block_rank.get(t[0].split("@")[0], len(_block_rank)),
                   -(t[1] * t[2])))
_best, _best_score = None, None
for _wg in (CONN_STRIP_W * f for f in
            (0.62, 0.7, 0.8, 0.9, 1.0, 1.02, 1.1, 1.25, 1.4)):
    _r = best_pack_rects(_items, CLUSTER_GAP, (_wg,),
                         initial_skyline_fn=_top_reserve_skyline,
                         keep_order=True)
    if _r is None:
        continue
    _origins, _uw, _uh = _r
    _score = max(_uw, CONN_STRIP_W) * (_uh + CONN_STRIP_H)
    if _best_score is None or _score < _best_score:
        _best, _best_score = _r, _score
cluster_origins, grid_width, grid_height = _best
print(f"Cluster pack: {grid_width:.1f} x {grid_height:.1f} mm "
      f"({len(all_clusters)} clusters); with the {CONN_STRIP_W:.1f}mm "
      f"connector strip -> ~{_best_score:.0f} mm^2")


# --- backfill real 2D holes the skyline pack structurally can't see --------
# REAL dead-space bug, found by RENDERING the board and looking at it
# (user: "I think the stuff in the black box can be moved into the red
# boxes"), same "a picture catches what every automated check missed"
# pattern as this project's earlier placement bugs. The keep_order pack
# above is a 1D skyline: at every x it tracks a single current height and
# can only build UPWARD from it. That is fundamentally blind to a 2D
# hole formed between two neighbouring columns that happen to reach
# different heights - the hole is real and empty, but no later item can
# ever be placed "into" it, only stacked on top of whichever column it
# falls under. That is exactly what stranded a handful of small SENSOR
# clusters and J1 off the right edge, in their own tall column, while
# real ~30x30mm holes sat unfilled next to J3 and above J5 - not a
# packing-order bug, a structural limitation of skyline packing itself.
#
# Fix: after the ordered pack, grid the packed area, repeatedly find the
# single largest empty rectangle (classic maximal-rectangle-in-histogram
# scan), and try to drop the cluster currently farthest from the packed
# area's centroid into it - repeat until nothing more fits. This never
# grows the board (candidates are bounded to the existing grid_width x
# grid_height) and never touches J3/J4/J5/J6 (they are not part of
# cluster_origins at all - placed separately, below).
def backfill_gaps(origins, dims, gap, max_w, max_h, reserve_fn, res=1.0):
    cols = int(max_w / res) + 1
    rows = int(max_h / res) + 1
    occ = [bytearray(cols) for _ in range(rows)]

    def mark(ox, oy, w, h, val):
        i0 = max(0, int((ox - gap / 2) / res))
        i1 = min(cols, int((ox + w + gap / 2) / res) + 1)
        j0 = max(0, int((oy - gap / 2) / res))
        j1 = min(rows, int((oy + h + gap / 2) / res) + 1)
        for j in range(j0, j1):
            row = occ[j]
            for i in range(i0, i1):
                row[i] = val

    # Same top-corner reservation the main pack itself respects (J3/J6's
    # eventual spot) - without this, backfill would think it's free.
    for rx, rw, rh in reserve_fn(max_w):
        mark(rx, 0.0, rw, rh, 1)

    for cn, (ox, oy) in origins.items():
        w, h = dims[cn]
        mark(ox, oy, w, h, 1)

    def largest_free_rect():
        heights = [0] * cols
        best = (0, 0, 0, 0, 0)
        for j in range(rows):
            row = occ[j]
            for i in range(cols):
                heights[i] = 0 if row[i] else heights[i] + 1
            stack = []
            i = 0
            while i <= cols:
                hgt = heights[i] if i < cols else 0
                if not stack or hgt >= heights[stack[-1]]:
                    stack.append(i)
                    i += 1
                else:
                    top = stack.pop()
                    width = i if not stack else i - stack[-1] - 1
                    area = heights[top] * width
                    if area > best[0]:
                        x0 = (stack[-1] + 1) if stack else 0
                        best = (area, x0, j - heights[top] + 1, i - 1, j)
        return best

    cx = sum(o[0] for o in origins.values()) / len(origins)
    cy = sum(o[1] for o in origins.values()) / len(origins)

    def stranded_order():
        return sorted(origins.keys(), key=lambda cn: -(
            (origins[cn][0] - cx) ** 2 + (origins[cn][1] - cy) ** 2))

    # For each hole (largest first), pick whichever still-stranded cluster
    # fits it BEST (smallest leftover area) rather than trying clusters in
    # a fixed order against whatever happens to be the current largest
    # hole - a fixed order can skip a perfectly good small cluster/hole
    # match just because a bigger cluster was tried against that hole
    # first and didn't fit. Repeat until nothing more fits anywhere.
    moved = 0
    placed_this_round = set()
    while True:
        _, i0, j0, i1, j1 = largest_free_rect()
        fw, fh = (i1 - i0 + 1) * res, (j1 - j0 + 1) * res
        if fw <= 0 or fh <= 0:
            break
        best_cn, best_leftover = None, None
        for cn in stranded_order():
            if cn in placed_this_round:
                continue
            w, h = dims[cn]
            if w + gap > fw or h + gap > fh:
                continue
            leftover = fw * fh - (w + gap) * (h + gap)
            if best_leftover is None or leftover < best_leftover:
                best_cn, best_leftover = cn, leftover
        if best_cn is None:
            break
        w, h = dims[best_cn]
        ox, oy = origins[best_cn]
        mark(ox, oy, w, h, 0)
        nx, ny = i0 * res + gap / 2, j0 * res + gap / 2
        origins[best_cn] = (nx, ny)
        mark(nx, ny, w, h, 1)
        placed_this_round.add(best_cn)
        moved += 1
    return moved


_cluster_dims = {cn: (c[2], c[3]) for cn, c in all_clusters.items()}
_moved = backfill_gaps(cluster_origins, _cluster_dims, CLUSTER_GAP,
                       grid_width, grid_height, _top_reserve_skyline)
grid_width = max(grid_width, max(ox + _cluster_dims[cn][0]
                                 for cn, (ox, oy) in cluster_origins.items()))
grid_height = max(grid_height, max(oy + _cluster_dims[cn][1]
                                   for cn, (ox, oy) in cluster_origins.items()))
print(f"Gap backfill: {_moved} cluster(s) relocated into empty board area")

placed = {}  # ref -> (x, y) in a shared, not-yet-page-centered board frame
for _cn, (_ox, _oy) in cluster_origins.items():
    for _r, (_rx, _ry) in all_clusters[_cn][0].items():
        placed[_r] = (_ox + _rx, _oy + _ry)

rotated_refs = set()
for _c in all_clusters.values():
    rotated_refs |= _c[1]

# ---------------------------------------------------------------------------
# 4b. Anchor connectors - placed deliberately, not via the generic packer.
#     J4/J5 (AMPSEAL harness connectors) go along the BOTTOM edge (real
#     harness-plug access from outside an enclosure, same convention
#     Manifold used for its own J1). J3 (USB-C) and J6 (U.FL antenna) go
#     along the TOP edge (both also need real enclosure-boundary access).
#     J1 (JTAG header, bench/dev-only - doesn't need final-enclosure edge
#     access) sits just below the MCU block, same "connector right after
#     its block's pack" convention Manifold used.
#
#     Placed HERE, before the global filler fill below, not after: the
#     filler pass needs every anchor's REAL final position - including
#     these 4 edge connectors - in its occupied-space list, or fillers
#     can (and did, on the first run of this reordering) get threaded
#     into board edge space that J3/J4/J5/J6 were about to claim.
# ---------------------------------------------------------------------------
# REAL BUG found via DRC (not eyeballing) once fillers started actually
# using board-wide gaps: with BOARD_MARGIN exactly equal to
# CORNER_RADIUS (3.0mm each), a filler that happens to be the single
# part defining BOTH the extreme content X and extreme content Y at
# once sits precisely where the rounded corner arc is tangent to the
# content box's own corner - the arc curves inward FROM there, so its
# real clearance to that part's pad can be ~0mm even though the part is
# a full BOARD_MARGIN back from each straight edge. Never came up before
# this filler rework because no single part used to occupy both extreme
# corners simultaneously; letting fillers spill into genuine board-wide
# gaps makes that coincidence real, not just theoretical. Fixed by
# giving the margin 1mm of headroom over the corner radius, so even a
# part sitting at the exact content-box corner keeps real clearance to
# the arc.


# J1 already placed above, as part of the same block-level skyline pack.

# Bottom edge: J4 then J5, left to right, below the packed block area.
# REAL PCB-EDGE fix (same lesson Manifold learned building its own J1):
# TE AMPSEAL 776180-1 is a real edge/panel-mount connector whose mating
# shroud is DESIGNED to overhang past the board into free air - its real
# footprint carries an actual vendor "PCB EDGE" fabrication marker (a
# genuine reference line on F.Fab, confirmed at local Y=13.5 - same exact
# footprint file copied directly from manifold-pcb, so the same real
# value applies here without re-deriving it). Sizing the board to the
# connector's FULL mechanical envelope (as the first version of this
# script did) would put solid PCB material under the connector's real
# overhang, where a real board should just stop - exactly the ~25mm-too-
# tall bug Manifold hit and fixed the same way. board_bottom_edge below
# is used for the board OUTLINE, not footprint_bbox()'s full envelope
# (which stays used everywhere else - courtyard/overlap checks still
# need the true mechanical extent).
content_bottom = grid_height
j4_fp, j4x0, j4y0, j4x1, j4y1 = anchor_size("J4")
j5_fp, j5x0, j5y0, j5x1, j5y1 = anchor_size("J5")
j4_y = content_bottom + ANCHOR_GAP
placed["J4"] = (0.0 - j4x0, j4_y - j4y0)
j4_width = j4x1 - j4x0
j5_x = j4_width + ANCHOR_GAP
placed["J5"] = (j5_x - j5x0, j4_y - j5y0)
# Both J4 and J5 use the identical real footprint, placed at the same
# j4_y baseline, so their real PCB-EDGE line lands at the same absolute
# Y - no trailing "+BOARD_MARGIN" here, same reasoning as Manifold's
# j1_board_height: the marker line IS the true edge already.
board_bottom_edge = (j4_y - j4y0) + AMPSEAL_PCB_EDGE_Y

# Top edge: J3 (USB-C) and J6 (U.FL), above the block area, right-hand
# side (above the PROGRAMMING block, where both bridge ICs physically
# are, keeping USB/antenna traces short).
j3_fp, j3x0, j3y0, j3x1, j3y1 = anchor_size("J3")
j6_fp, j6x0, j6y0, j6x1, j6y1 = anchor_size("J6")
# J3/J6 drop into the corner the cluster packer already reserved for
# them (see _top_reserve_skyline above), rather than being stacked above
# everything. Previously they sat in their own band across the whole
# board width - ~26mm of height spent on two parts about 30mm wide,
# which is precisely the dead space this pass set out to remove.
#
# J3 is rotated 180deg from the footprint's default: at 0deg the plug
# opening faces INTO the board, backwards for a part whose whole purpose
# is being plugged in from outside an enclosure.
J3_ANGLE = 180
ANCHOR_ANGLE = {"J1": 0, "J3": J3_ANGLE, "J4": 0, "J5": 0, "J6": 0}
j3rx0, j3ry0, j3rx1, j3ry1 = rotate_bbox(j3x0, j3y0, j3x1, j3y1, J3_ANGLE)
# Placed so the receptacle's own mating face lands exactly on y=0, which
# IS the board's top edge (see board_top_edge below).
placed["J3"] = (0.0 - j3rx0, USB_C_FACE_OFFSET)
# J6 is a U.FL antenna lead, not an edge-mount part - the coax
# routes out to a bulkhead connector - so unlike J3 it takes the
# ordinary board margin instead of sitting on the edge line.
placed["J6"] = ((j3rx1 - j3rx0) + J3_J6_GAP - j6x0, BOARD_MARGIN - j6y0)

# REAL BUG, caught by eye on the rendered board ("j3 needs to be on the
# edge to be usable") and confirmed numerically: the board's top edge
# had ended up ~23mm ABOVE J3, leaving the USB-C receptacle stranded in
# the middle of the board where no cable could ever reach it. Cause: the
# top edge was derived from whatever content happened to sit highest,
# and passives had drifted above J3 - so the outline simply grew past
# it. J4/J5 never had this problem because the bottom edge is pinned to
# their real PCB-EDGE marker instead of to content.
#
# Fix: give the top edge the same treatment. This receptacle's own
# mating face is its F.Fab front at local Y=+5.23 (verified by reading
# the real Amphenol footprint file: the 24 signal pads sit at the REAR,
# local Y=-5.02/-3.32, and the front shield legs at +2.84, so +5.23 is
# the opening). At J3_ANGLE=180 local +Y maps to board -Y, so that face
# lands at J3's placed Y minus the offset, and the board stops exactly
# there - flush with the opening, the way a real edge-mount USB port has
# to be to accept a plug.
board_top_edge = placed["J3"][1] - USB_C_FACE_OFFSET

# ---------------------------------------------------------------------------
# 4c. Overall board extent + page centering.
# ---------------------------------------------------------------------------
# REAL BUG found while wiring up J3's 180deg rotation (not eyeballing):
# this extent computation used each ref's plain, UNROTATED bbox no
# matter what - it never accounted for the 90deg rotation the skyline
# packer already applies to some anchors/fillers for a tighter fit, let
# alone J3's new 180. Harmless before now only because every part that
# happened to land at 90deg was small/near-square enough for the error
# to stay inside the existing margin - J3 is a real, asymmetric
# connector sitting at the board's own extreme top edge (defines
# content_y0 directly), so getting its rotated extent wrong here would
# either clip real copper or silently oversize the board. Fixed with a
# single per-ref angle lookup (final_angle) feeding the same rotate_bbox
# used everywhere else, instead of assuming angle=0.
def final_angle(ref):
    if ref in ANCHOR_ANGLE:
        return ANCHOR_ANGLE[ref]
    return 90 if ref in rotated_refs else 0


def placed_bbox(ref):
    x0, y0, x1, y1 = cached_bbox(parts[ref]["footprint"])
    x0, y0, x1, y1 = rotate_bbox(x0, y0, x1, y1, final_angle(ref))
    px, py = placed[ref]
    return px + x0, py + y0, px + x1, py + y1


# Three connectors define real board EDGES rather than merely sitting
# inside them: J4/J5 at the bottom (their vendor PCB-EDGE marker) and J3
# at the top (its mating face). Their own mechanical envelopes are
# deliberately excluded from the content extent in that direction, since
# each is DESIGNED to sit flush with - or overhang - the edge. Every
# other part, in every other direction, gets the normal BOARD_MARGIN.
EDGE_REFS = ("J3", "J4", "J5")
all_x = [placed_bbox(r)[0] for r in placed] + [placed_bbox(r)[2] for r in placed]
ys_top = [placed_bbox(r)[1] for r in placed if r not in EDGE_REFS]
ys_bot = [placed_bbox(r)[3] for r in placed if r not in EDGE_REFS]
content_x0, content_x1 = min(all_x), max(all_x)

board_x0 = content_x0 - BOARD_MARGIN
board_x1 = content_x1 + BOARD_MARGIN
# Each edge is whichever is further out: the edge-defining connector's
# own face, or ordinary content plus its margin. A max()/min() rather
# than an unconditional override, so a stray part can never end up
# outside the outline.
board_y0 = min(board_top_edge, min(ys_top) - BOARD_MARGIN)
board_y1 = max(board_bottom_edge, max(ys_bot) + BOARD_MARGIN)

content_y0, content_y1 = board_y0, board_y1
board_width = board_x1 - board_x0
board_height = board_y1 - board_y0

# A2 landscape (594x420mm) - this board is far bigger than Manifold's
# (real consequence of 315 real components across 8 subsystems vs. ~30),
# A4/A3 would be too small to hold it with any page margin at all.
PAGE_W, PAGE_H = 594.0, 420.0
board.paper = PageSettings(paperSize="A2")
BOARD_OFFSET_X = round((PAGE_W - board_width) / 2 - content_x0, 2)
BOARD_OFFSET_Y = round((PAGE_H - board_height) / 2 - content_y0, 2)
placed = {ref: (round(x + BOARD_OFFSET_X, 2), round(y + BOARD_OFFSET_Y, 2))
          for ref, (x, y) in placed.items()}

print(f"Board outline target: {board_width:.1f} x {board_height:.1f} mm")

# ---------------------------------------------------------------------------
# 5. Build footprint instances: real part, real pads, real nets, real
#    position. Same core logic as manifold-pcb's own script (Reference
#    silkscreen offset, pad-angle fix for rotated parts, net assignment
#    from the real netlist) - see that file for the full reasoning behind
#    each piece, reused here rather than re-derived.
# ---------------------------------------------------------------------------
ref_label_pos = {}  # ref -> local (dx, dy) for the Reference silkscreen text
for ref, info in parts.items():
    fp = load_footprint(info["footprint"])
    x, y = placed[ref]
    angle = final_angle(ref)
    fp.position = Position(round(x, 3), round(y, 3), angle)
    fp.path = f"/{info['uuid']}"
    # Real, confirmed bug (manifold-pcb): a rotated footprint's pads get
    # their POSITION rotated correctly but keep their unrotated SHAPE
    # unless the pad's own angle is set to match - invisible on symmetric/
    # near-square pads, a real DRC failure on asymmetric fine-pitch ones
    # (this board has plenty: the 144-LQFP, both QFN bridge chips, the
    # 0.5mm-pitch MC33810s).
    if angle:
        for pad in fp.pads:
            pad.position.angle = angle
    lx0, ly0, lx1, ly1 = footprint_bbox(fp)
    if angle == 90:
        ref_label_pos[ref] = (round(lx1 + 0.5, 2), 0.0)
    elif angle == 180:
        ref_label_pos[ref] = (0.0, round(ly1 + 0.5, 2))
    else:
        ref_label_pos[ref] = (0.0, round(ly0 - 0.5, 2))
    for item in fp.graphicItems:
        if getattr(item, "type", None) == "reference":
            item.text = ref
        elif getattr(item, "type", None) == "value":
            item.text = info["value"]
    fp.properties["Reference"] = ref
    fp.properties["Value"] = info["value"]
    fp.properties.pop("KiLib_Generator", None)
    unmatched = []
    for pad in fp.pads:
        # str() the pad number: most bundled footprints use quoted pad
        # numbers (parsed as str), but real downloaded/copied connector
        # footprints (the AMPSEAL parts here, same as Manifold's J1) use
        # KiCad's legacy unquoted syntax, parsed as a Python int otherwise.
        key = (ref, str(pad.number))
        if key in pad_net:
            name = pad_net[key]
            pad.net = Net(number=net_number(name), name=name)
        else:
            unmatched.append(pad.number)
    if unmatched:
        print(f"  {ref}: {len(unmatched)} pad(s) with no schematic net "
              f"(spare/mechanical, e.g. an AMPSEAL cavity marked NC): {unmatched}")
    board.footprints.append(fp)

print(f"Placed {len(board.footprints)} real footprints.")

# ---------------------------------------------------------------------------
# 6. Board outline: simple rounded rectangle on Edge.Cuts.
# ---------------------------------------------------------------------------
# REAL BUG found via DRC (not eyeballing): this used to be
# `BOARD_OFFSET_X + content_x0` with no margin subtracted, which put the
# board's own left/top edges at EXACTLY the same coordinate as the
# leftmost/topmost real part - board_width/height already budgeted
# 2*BOARD_MARGIN of extra space, but ALL of it silently landed on the
# right/bottom edge only, leaving zero clearance on the left/top (caught
# as 39 real copper_edge_clearance DRC violations, all against parts
# sitting flush on those two edges - not a coincidence once traced back
# to this formula). Fixed by explicitly subtracting BOARD_MARGIN here so
# it's split symmetrically, matching what board_width/height already
# assume.
# board_x0/board_y0 already include their own margin where a margin
# applies (and deliberately don't, where an edge-mount connector's face
# IS the edge), so no further adjustment here.
ox, oy = (BOARD_OFFSET_X + board_x0, BOARD_OFFSET_Y + board_y0)
ex, ey = ox + board_width, oy + board_height
CORNER_RADIUS = 3.0
R = CORNER_RADIUS
K = R * (1 - 2 ** -0.5)


def _arc(start, mid, end):
    board.graphicItems.append(GrArc(
        start=Position(round(start[0], 3), round(start[1], 3)),
        mid=Position(round(mid[0], 3), round(mid[1], 3)),
        end=Position(round(end[0], 3), round(end[1], 3)),
        layer="Edge.Cuts", width=0.1))


def _line(p1, p2):
    board.graphicItems.append(GrLine(
        start=Position(round(p1[0], 3), round(p1[1], 3)),
        end=Position(round(p2[0], 3), round(p2[1], 3)),
        layer="Edge.Cuts", width=0.1))


_line((ox + R, oy), (ex - R, oy))
_arc((ex - R, oy), (ex - K, oy + K), (ex, oy + R))
_line((ex, oy + R), (ex, ey - R))
_arc((ex, ey - R), (ex - K, ey - K), (ex - R, ey))
_line((ex - R, ey), (ox + R, ey))
_arc((ox + R, ey), (ox + K, ey - K), (ox, ey - R))
_line((ox, ey - R), (ox, oy + R))
_arc((ox, oy + R), (ox + K, oy + K), (ox + R, oy))

print(f"Board outline: {ex - ox:.1f} x {ey - oy:.1f} mm, "
      f"{len(board.footprints)} footprints, {len(net_registry)} nets")

# ---------------------------------------------------------------------------
# 7. Verification on the IN-MEMORY board (before writing/upgrading): overlap
#    check and net pin-count check - same reasoning as manifold-pcb (kiutils'
#    reader can't re-parse the abbreviated "(net 0)" token real KiCad writes
#    for unconnected pads after `pcb upgrade`, so the in-memory board is the
#    authoritative source for these checks, not a re-parse of the file).
# ---------------------------------------------------------------------------
assert len(board.footprints) == len(parts), \
    f"footprint count mismatch: {len(board.footprints)} vs {len(parts)} parts"

boxes = []
for fp in board.footprints:
    x0, y0, x1, y1 = footprint_bbox(fp)
    # REAL BUG found while wiring up J3's 180deg rotation: this only
    # ever special-cased angle==90, same gap 4c's own extent
    # computation had - any part at 180/270 (now: J3) would have been
    # overlap-checked against its WRONG, unrotated box. Fixed by
    # reusing the same rotate_bbox every other rotation-aware spot in
    # this file already relies on, instead of a hardcoded 90 check.
    x0, y0, x1, y1 = rotate_bbox(x0, y0, x1, y1, fp.position.angle)
    boxes.append((fp.path, fp.position.X + x0, fp.position.Y + y0,
                 fp.position.X + x1, fp.position.Y + y1))
overlaps = []
for i in range(len(boxes)):
    for j in range(i + 1, len(boxes)):
        _, ax0, ay0, ax1, ay1 = boxes[i]
        _, bx0, by0, bx1, by1 = boxes[j]
        if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
            overlaps.append((boxes[i][0], boxes[j][0]))
assert not overlaps, f"overlapping footprint bounding boxes: {overlaps}"
print("Placement OK: no overlapping footprint bounding boxes")

pcb_net_pins = {}
seen_logical_pins = set()
for fp in board.footprints:
    ref = fp.properties.get("Reference")
    for pad in fp.pads:
        if pad.net and pad.net.name:
            # Some real footprints (e.g. F1-F4's Keystone 3568 Mini blade
            # fuse holders, same part Manifold used) have TWO physical pads
            # sharing one pad NUMBER per terminal (redundant solder joints)
            # - one logical connection, same as the schematic's one pin.
            logical_pin = (ref, pad.number)
            if logical_pin in seen_logical_pins:
                continue
            seen_logical_pins.add(logical_pin)
            pcb_net_pins.setdefault(pad.net.name, 0)
            pcb_net_pins[pad.net.name] += 1
sch_net_pins = {}
for (ref, pin), name in pad_net.items():
    sch_net_pins[name] = sch_net_pins.get(name, 0) + 1
mismatches = {n: (sch_net_pins[n], pcb_net_pins.get(n, 0)) for n in sch_net_pins
              if sch_net_pins[n] != pcb_net_pins.get(n, 0)}
assert not mismatches, f"net pin-count mismatches (schematic vs PCB): {mismatches}"
print(f"Net check OK: all {len(sch_net_pins)} nets have matching pin "
      f"counts between schematic and PCB")

board.to_file(PCB)
print("Wrote", PCB)

# Same bare-Reference-property patch as manifold-pcb: kiutils'
# Footprint.properties has no field for a property's own position, so
# to_file() writes a bare `(property "Reference" "REF")` with the
# position stripped - KiCad's own `pcb upgrade` then fills in ITS default
# (dead center on the part, on its own pads) unless patched first.
text = open(PCB, encoding="utf-8").read()
for ref, (dx, dy) in ref_label_pos.items():
    old = f'(property "Reference" "{ref}")'
    new = (f'(property "Reference" "{ref}" (at {dx} {dy} 0) (layer "F.SilkS") '
           f'(effects (font (size 0.8 0.8) (thickness 0.12))))')
    count = text.count(old)
    assert count == 1, f"expected exactly 1 bare Reference property for {ref}, found {count}"
    text = text.replace(old, new, 1)
open(PCB, "w", encoding="utf-8").write(text)
print(f"Repositioned {len(ref_label_pos)} Reference labels clear of their own footprints")

# ---------------------------------------------------------------------------
# 8. Upgrade to current KiCad format, then run a real DRC via kicad-cli.
# ---------------------------------------------------------------------------
result = subprocess.run([KICAD_CLI, "pcb", "upgrade", PCB], capture_output=True, text=True)
print("Upgraded to current KiCad format:" if result.returncode == 0 else "WARNING: upgrade failed:",
      (result.stdout or result.stderr).strip())

drc_path = os.path.join(os.environ.get("TEMP", HERE), "ecu_pcb_drc.json")
result = subprocess.run([KICAD_CLI, "pcb", "drc", "--format", "json",
                         "--output", drc_path, "--exit-code-violations", PCB],
                        capture_output=True, text=True)
import json
drc = json.load(open(drc_path, encoding="utf-8"))
violations = drc.get("violations", [])
by_type = {}
for v in violations:
    t = v.get("type", "unknown")
    by_type[t] = by_type.get(t, 0) + 1
print("DRC violation summary (unrouted board - 'unconnected_items' is EXPECTED "
      "for every net, everything else is worth a look):")
for t, count in sorted(by_type.items()):
    print(f"  {t}: {count}")
unexpected = {t: c for t, c in by_type.items() if t != "unconnected_items"}
if unexpected:
    print("NOTE: non-routing DRC findings present, see", drc_path, "for details:", unexpected)
else:
    print("No unexpected DRC findings (only unrouted-net warnings, as expected).")
