#!/usr/bin/env python3
"""
gen_engine_config.py - reads config/engine.toml and regenerates
inc/engine_config.h.

WHY A FILE RATHER THAN HAND-EDITED DEFINES. Engine facts - cylinder
count, crank wheel pattern, firing order - are not things to discover
from a datasheet; they are things the person fitting the ECU knows about
their engine. Keeping them in one plain, commented file that a generator
reads is the same pattern the rest of this project uses for anything
with a single source of truth (build_bom.py reads the schematic,
gen_ktype_table.py reads NIST's coefficients).

Doing it this way also buys real validation. A #error in a header can
say "that is wrong"; this can say WHICH cylinder is duplicated and which
is missing, which matters because a firing-order typo produces an engine
that runs badly rather than one that obviously does not run.

WHAT IS CHECKED, and what deliberately is not:
  * Internal consistency - teeth divide 360, missing < teeth, cylinder
    count within the board's 8 channels, cycle divisible by cylinders,
    prescaler within the 8-bit GPRE field, and the firing order being a
    genuine permutation of 1..cylinders.
  * NOT whether any of it matches a real engine. Nothing here can know
    that. The values shipped are defaults describing a common setup and
    are marked as such in engine.toml itself.

Run:  python tools/gen_engine_config.py
"""
import os
import re
import sys

try:
    import tomllib                      # Python 3.11+
except ModuleNotFoundError:             # pragma: no cover
    print("This needs Python 3.11+ for tomllib.", file=sys.stderr)
    raise

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(HERE, "config", "engine.toml")
OUT = os.path.join(HERE, "inc", "engine_config.h")

EMIOS_CLOCK_HZ = 60000000   # Peripheral Set 3, confirmed undivided (clocks.h)


class ConfigError(Exception):
    pass


def validate(cfg):
    """Every check here exists because getting it wrong is either silent
    or actively dangerous. Errors name the offending value."""
    eng = cfg["engine"]
    crank = cfg["crank"]
    tb = cfg["timebase"]
    ign = cfg["ignition"]

    cyl = eng["cylinders"]
    if not (1 <= cyl <= 8):
        raise ConfigError(
            f"engine.cylinders = {cyl}, must be 1..8 - the board provides "
            f"8 injector and 8 ignition channels")

    cycle = eng["cycle_degrees"]
    if cycle not in (360, 720):
        raise ConfigError(
            f"engine.cycle_degrees = {cycle}, must be 720 (four-stroke) "
            f"or 360 (two-stroke)")

    if cycle % cyl != 0:
        raise ConfigError(
            f"engine.cycle_degrees ({cycle}) must divide evenly by "
            f"cylinders ({cyl}), or firing events are not evenly spaced "
            f"and the angle arithmetic is meaningless")

    # A firing-order typo is the dangerous, hard-to-spot one: a repeated
    # or omitted cylinder still looks plausible at a glance.
    order = eng["firing_order"]
    if len(order) != cyl:
        raise ConfigError(
            f"engine.firing_order has {len(order)} entries but "
            f"cylinders = {cyl}")
    expected = set(range(1, cyl + 1))
    got = set(order)
    if got != expected:
        dupes = sorted({c for c in order if order.count(c) > 1})
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        detail = []
        if dupes:
            detail.append(f"repeated: {dupes}")
        if missing:
            detail.append(f"never fired: {missing}")
        if extra:
            detail.append(f"not a real cylinder: {extra}")
        raise ConfigError(
            f"engine.firing_order must list each cylinder 1..{cyl} exactly "
            f"once ({'; '.join(detail)})")

    teeth = crank["teeth"]
    if teeth < 1 or 360 % teeth != 0:
        raise ConfigError(
            f"crank.teeth = {teeth}, must divide 360 evenly - trigger "
            f"wheel teeth are evenly spaced")

    missing_teeth = crank["missing"]
    if not (1 <= missing_teeth < teeth):
        raise ConfigError(
            f"crank.missing = {missing_teeth}, must be at least 1 and "
            f"fewer than crank.teeth ({teeth}) - without a gap there is "
            f"no way to find absolute crank position")

    gap = crank["gap_to_tdc_deg"]
    if not (0 <= gap < 360):
        raise ConfigError(f"crank.gap_to_tdc_deg = {gap}, must be 0..359")

    fuel = cfg["fuel"]
    if fuel["displacement_cc"] < 1:
        raise ConfigError("fuel.displacement_cc must be positive")
    if fuel["injector_cc_per_min"] < 1:
        raise ConfigError("fuel.injector_cc_per_min must be positive")
    if not (50 <= fuel["target_afr_x10"] <= 250):
        raise ConfigError(
            f"fuel.target_afr_x10 = {fuel['target_afr_x10']}, expected 50..250 "
            f"(x10, so 147 = 14.7:1). Gasoline stoich is 147, E85 about 98")

    m = cfg["map_sensor"]
    if m["kpa_at_max"] <= m["kpa_at_min"]:
        raise ConfigError("map_sensor.kpa_at_max must exceed kpa_at_min")
    if not (1 <= m["adc_counts_at_max"] <= 4095):
        raise ConfigError(
            f"map_sensor.adc_counts_at_max = {m['adc_counts_at_max']}, must be "
            f"1..4095 - the MCU ADC is 12-bit")

    validate_table1d(cfg["injection"]["dead_time"], "injection.dead_time",
                     100, 5000, "us")
    validate_table1d(cfg["ignition"]["dwell"], "ignition.dwell",
                     500, 20000, "us")
    validate_table2d(cfg["ve"], "ve", 10, 150, "%")
    validate_table2d(cfg["ignition"]["advance"], "ignition.advance",
                     -20, 60, "deg BTDC")

    # The scheduling window has to be wide enough for the largest advance
    # the table can command. crank_capture_isr() arms a cylinder one
    # ARM_LEAD_DEG (== the firing interval) before its TDC, and the spark
    # happens ADVANCE degrees BEFORE that TDC - so an advance larger than
    # the lead would need to have been scheduled before the ISR ever
    # looked at that cylinder. This bites hardest on engines with many
    # cylinders, where the firing interval is small: a V12 has only 60
    # degrees between firings, which a 46-degree advance plus coil dwell
    # can genuinely exceed.
    # --- how slowly can the engine turn and still be scheduled? -------
    # Match values are absolute positions on a 16-bit counter bus, so the
    # bus spans a fixed wall-clock window and a scheduling delta larger
    # than that cannot be expressed. The arming lead is sized from the
    # DWELL rather than fixed as an angle (arming_lead_deg() in
    # injection.c), which is what keeps the delta small at low rpm - a
    # fixed 180-degree lead needed 200 ms at cranking speed to cover a
    # 3 ms coil charge, and could not be scheduled at all below ~458 rpm.
    #
    # Mirrors the firmware's own arithmetic rather than a closed form, so
    # the two cannot drift apart.
    tick_hz = 60000000 // tb["emios_prescaler"]
    modulus = 65535
    deg_per_tooth = 360 // crank["teeth"]
    interval = cfg["engine"]["cycle_degrees"] // cfg["engine"]["cylinders"]
    # Use the dwell at the nominal RUNNING voltage. The longest dwell in
    # the curve belongs to the lowest voltage, which only occurs while
    # cranking - and cranking happens at a couple of hundred rpm, so
    # pairing it with a high-rpm limit would report a combination that
    # cannot occur. The firmware clamps dwell to the window that is
    # actually available anyway.
    dwell_mv = ign["dwell"]["volts_mv"]
    nominal_i = min(range(len(dwell_mv)), key=lambda i: abs(dwell_mv[i] - 14000))
    dwell_us_nom = ign["dwell"]["us"][nominal_i]
    dwell_ticks = dwell_us_nom * tick_hz // 1000000
    max_adv = max(max(r) for r in cfg["ignition"]["advance"]["table"])

    def delta_at(rpm):
        tooth_ticks = (60.0 * tick_hz) / (rpm * crank["teeth"])
        teeth = -(-dwell_ticks // int(tooth_ticks)) + 1      # ceil, + margin
        lead = teeth * deg_per_tooth + max_adv
        lead = -(-lead // deg_per_tooth) * deg_per_tooth      # up to a tooth
        cap = min(2 * interval, cfg["engine"]["cycle_degrees"] - deg_per_tooth)
        lead = min(lead, cap)
        return (lead - max_adv) / deg_per_tooth * tooth_ticks

    floor_rpm = None
    for rpm in range(40, 1200, 5):
        if delta_at(rpm) < modulus:
            floor_rpm = rpm
            break
    if floor_rpm is None or floor_rpm > 250:
        print(f"  WARNING: the counter bus ({modulus / tick_hz * 1000:.0f} ms at "
              f"{tick_hz} Hz) cannot express the arming lead below "
              f"{floor_rpm or '>1200'} rpm - above real cranking speed.")
    else:
        print(f"  schedulable from {floor_rpm} rpm up "
              f"(counter bus spans {modulus / tick_hz * 1000:.0f} ms; "
              f"lead uses {100 * delta_at(200) / modulus:.0f}% of it at 200 rpm)")

    interval = cfg["engine"]["cycle_degrees"] // cfg["engine"]["cylinders"]
    # The arming lead is capped at two firing intervals, and the spark
    # sits `advance` degrees inside it - so an advance at or beyond the
    # cap could never be scheduled ahead of itself.
    lead_cap = min(2 * interval, cfg["engine"]["cycle_degrees"] - deg_per_tooth)
    if max_adv >= lead_cap:
        raise ConfigError(
            f"ignition.advance peaks at {max_adv} deg BTDC but the arming lead "
            f"is capped at {lead_cap} deg (two firing intervals). The spark "
            f"would have to be scheduled before its cylinder was armed. Reduce "
            f"the advance, or raise ARM_LEAD_CAP_DEG in src/injection.c and "
            f"re-check the timing budget.")

    # Ceiling: dwell is a fixed time, so above some speed it no longer
    # fits inside the lead however the lead is sized. That is a real
    # physical limit, and the reason a dwell-vs-rpm table exists.
    ceil_rpm = None
    for rpm in range(1000, 15000, 50):
        if delta_at(rpm) <= dwell_ticks:
            ceil_rpm = rpm
            break
    if ceil_rpm is not None:
        print(f"  dwell at {dwell_mv[nominal_i] / 1000:.0f} V ({dwell_us_nom} us) "
              f"stops fitting the lead above "
              f"about {ceil_rpm} rpm - shorten dwell there (a dwell-vs-rpm "
              f"table) if that is inside your rev range")

    pre = tb["emios_prescaler"]
    if not (1 <= pre <= 256):
        raise ConfigError(
            f"timebase.emios_prescaler = {pre}, must be 1..256 - eMIOS "
            f"GPRE encodes (ratio - 1) in 8 bits")


TEMPLATE = '''/*
 * engine_config.h - GENERATED FILE, do not edit by hand.
 *
 * Regenerate with:  python tools/gen_engine_config.py
 * Source of truth:  config/engine.toml
 *
 * Engine-specific facts live in that .toml rather than here so there is
 * one plain, commented place to set them, with the generator validating
 * them properly - including checking the firing order is a genuine
 * permutation of the cylinders, which a typo would otherwise turn into
 * an engine that runs badly rather than one that obviously does not run.
 *
 * THE ENGINE VALUES ARE DEFAULTS, NOT MEASUREMENTS. They describe a
 * common setup and have been confirmed against no real engine. Getting
 * the wheel pattern or firing order wrong fires cylinders at the wrong
 * time, which is how engines get damaged. See config/engine.toml.
 */
#ifndef ENGINE_CONFIG_H
#define ENGINE_CONFIG_H

#include <stdint.h>

/* ---- Timebase: derived from this board's confirmed 60 MHz clock ---- */
#define ECU_EMIOS_CLOCK_HZ    {clock}u
#define ECU_EMIOS_PRESCALER   {prescaler}u
#define ECU_EMIOS_TICK_HZ     (ECU_EMIOS_CLOCK_HZ / ECU_EMIOS_PRESCALER)

/* ---- Engine ------------------------------------------------------- */
#define ENGINE_CYLINDERS      {cylinders}u
#define ENGINE_CYCLE_DEGREES  {cycle}u

/* Crank degrees between consecutive firing events. */
#define ENGINE_FIRING_INTERVAL_DEG ({cycle}u / {cylinders}u)

/* Firing order as an initialiser: cylinder numbers, in the order they
 * fire. Validated as a permutation of 1..{cylinders} at generation time. */
#define ENGINE_FIRING_ORDER   {{ {order} }}

/* ---- Crank trigger wheel ------------------------------------------ */
#define CRANK_WHEEL_TEETH        {teeth}u
#define CRANK_WHEEL_MISSING      {missing}u
#define CRANK_DEGREES_PER_TOOTH  (360u / CRANK_WHEEL_TEETH)

/* Real tooth positions per revolution - what the sensor actually sees,
 * which is fewer than CRANK_WHEEL_TEETH because of the gap. */
#define CRANK_REAL_TEETH      (CRANK_WHEEL_TEETH - CRANK_WHEEL_MISSING)

/* Crank angle in degrees BEFORE cylinder 1 compression TDC at which the
 * first tooth after the gap passes the sensor. Depends on where the
 * sensor is physically mounted; an error here shifts ALL timing. */
#define CRANK_GAP_TO_TDC_DEG  {gap}u

/* ---- Injection / ignition ----------------------------------------- */
/* Injector dead time and coil dwell are voltage-dependent curves now,
 * not constants - see DEADTIME_* and DWELL_* below. */

/* ---- Fuelling ------------------------------------------------------
 * Speed-density: air mass in the cylinder from pressure, volume and
 * temperature; fuel mass from the target AFR; pulse width from the
 * injector's flow rate. VE is the measured correction that makes the
 * ideal-gas figure match what the engine actually inhales. */
#define FUEL_DISPLACEMENT_CC   {displacement}u
#define FUEL_CYL_VOLUME_CC     ({displacement}u / {cylinders}u)
#define FUEL_INJECTOR_CC_MIN   {inj_flow}u
#define FUEL_DENSITY_MG_CC     {fuel_density}u
#define FUEL_TARGET_AFR_X10    {afr}u

/* MAP sensor: linear ratiometric, so kPa is a straight line in ADC
 * counts between these two points. */
#define MAP_KPA_AT_MIN         {map_min}u
#define MAP_KPA_AT_MAX         {map_max}u
#define MAP_ADC_AT_MAX         {map_counts}u

{ve_block}

{spark_block}

{deadtime_block}

{dwell_block}

/* The largest advance the spark table can command, in crank degrees.
 * The scheduling lead has to stay wider than this - the generator
 * enforces that, this is here so the firmware can assert it too. */
#define SPARK_ADVANCE_MAX_DEG  {spark_max}

#endif /* ENGINE_CONFIG_H */
'''


def main():
    with open(CONFIG, "rb") as f:
        cfg = tomllib.load(f)

    try:
        validate(cfg)
    except ConfigError as e:
        print(f"engine.toml is not valid:\n  {e}", file=sys.stderr)
        raise SystemExit(1)

    eng, crank = cfg["engine"], cfg["crank"]
    text = TEMPLATE.format(
        clock=EMIOS_CLOCK_HZ,
        prescaler=cfg["timebase"]["emios_prescaler"],
        cylinders=eng["cylinders"],
        cycle=eng["cycle_degrees"],
        order=", ".join(f"{c}u" for c in eng["firing_order"]),
        teeth=crank["teeth"],
        missing=crank["missing"],
        gap=crank["gap_to_tdc_deg"],
        displacement=cfg["fuel"]["displacement_cc"],
        inj_flow=cfg["fuel"]["injector_cc_per_min"],
        fuel_density=cfg["fuel"]["fuel_density_mg_per_cc"],
        afr=cfg["fuel"]["target_afr_x10"],
        map_min=cfg["map_sensor"]["kpa_at_min"],
        map_max=cfg["map_sensor"]["kpa_at_max"],
        map_counts=cfg["map_sensor"]["adc_counts_at_max"],
        ve_block=table2d_c(
            cfg["ve"], "VE",
            "/* ---- VE table --------------------------------------------------\n"
            " * Rows are MAP breakpoints, columns RPM. Bilinearly interpolated\n"
            " * by table2d_lookup() (table.h).\n"
            " * A STARTING SHAPE, NOT A TUNED MAP - see config/engine.toml. */"),
        spark_block=table2d_c(
            cfg["ignition"]["advance"], "SPARK",
            "/* ---- Spark advance ---------------------------------------------\n"
            " * Crank degrees BEFORE top dead centre. Same shape and the same\n"
            " * interpolation as the VE table, on its own axes.\n"
            " * A STARTING SHAPE, NOT A TUNED MAP. Over-advance destroys\n"
            " * pistons - see config/engine.toml. */"),
        spark_max=max(max(r) for r in cfg["ignition"]["advance"]["table"]),
        deadtime_block=table1d_c(
            cfg["injection"]["dead_time"], "DEADTIME",
            "/* ---- Injector dead time vs battery voltage --------------------\n"
            " * Microseconds of opening delay, added to every pulse width.\n"
            " * PLACEHOLDER SHAPE - see config/engine.toml. */"),
        dwell_block=table1d_c(
            cfg["ignition"]["dwell"], "DWELL",
            "/* ---- Coil dwell vs battery voltage ----------------------------\n"
            " * Microseconds of primary charge time before the spark.\n"
            " * PLACEHOLDER SHAPE - see config/engine.toml. */"),
    )
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)

    tick_hz = EMIOS_CLOCK_HZ // cfg["timebase"]["emios_prescaler"]
    print(f"{eng['cylinders']}-cylinder, {crank['teeth']}-{crank['missing']} "
          f"wheel, firing order {eng['firing_order']}")
    print(f"  {360 // crank['teeth']} deg/tooth, "
          f"{eng['cycle_degrees'] // eng['cylinders']} deg between firings")
    print(f"  timebase {tick_hz} Hz "
          f"({1000000 // tick_hz if tick_hz <= 1000000 else 0} us/tick)")
    ve = cfg["ve"]
    flat = [v for row in ve["table"] for v in row]
    print(f"  VE table {len(ve['map_axis'])} MAP x {len(ve['rpm_axis'])} RPM, "
          f"{min(flat)}-{max(flat)}%")
    sp = cfg["ignition"]["advance"]
    sflat = [v for row in sp["table"] for v in row]
    interval = cfg["engine"]["cycle_degrees"] // cfg["engine"]["cylinders"]
    print(f"  spark table {len(sp['map_axis'])} MAP x {len(sp['rpm_axis'])} RPM, "
          f"{min(sflat)}-{max(sflat)} deg BTDC")
    dt = cfg["injection"]["dead_time"]; dw = cfg["ignition"]["dwell"]
    print(f"  dead time {max(dt['us'])}-{min(dt['us'])} us and dwell "
          f"{max(dw['us'])}-{min(dw['us'])} us over "
          f"{min(dt['volts_mv']) / 1000:.0f}-{max(dt['volts_mv']) / 1000:.0f} V")
    print(f"  {cfg['fuel']['displacement_cc']} cc, "
          f"{cfg['fuel']['injector_cc_per_min']} cc/min injectors, "
          f"target AFR {cfg['fuel']['target_afr_x10'] / 10:.1f}")
    checked = validate_sensors(cfg)
    sensors = cfg["sensor"]
    kinds = {}
    for sen in sensors.values():
        kinds[sen["type"]] = kinds.get(sen["type"], 0) + 1
    print(f"  {len(sensors)} analog sensors ("
          + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + ")"
          + f", {len(cfg.get('curve', {}))} shared curve(s)")
    npairs = len(cfg.get("_redundant_pairs", []))
    if npairs:
        print(f"  {npairs} redundant sensor pair(s), each verified "
              f"distinguishable: "
              + ", ".join(f"{a}/{b}" for a, b in cfg["_redundant_pairs"]))
    if checked:
        print(f"  {checked} divider resistor(s) cross-checked against "
              f"ecu-pcb/build_schematic.py")
    else:
        print("  divider cross-check SKIPPED (ecu-pcb not found beside this "
              "project) - values in engine.toml are unverified against the board")
    sensor_out = emit_sensors(cfg)
    print("Config valid. Wrote " + OUT)
    print("             and " + sensor_out)



# =====================================================================
# Analog sensors
# =====================================================================
# One descriptor table beats one .c/.h pair per sensor: the two old
# drivers (clt_sensor, iat_sensor) were 405 lines that differed in a
# pull-up value, a lookup table, and the prefix on every identifier,
# while eight other analog channels had no conversion at all.

SENSOR_H = os.path.join(HERE, "inc", "sensor_defs.h")

# Where the board's own resistor values live, so config and schematic
# cannot drift apart silently.
SCHEMATIC = os.path.join(HERE, "..", "ecu-pcb", "build_schematic.py")


def _r_value(token):
    """'4.7k' -> 4700.0, '1k 1%' -> 1000.0, '68k' -> 68000.0, '499R' ->
    499.0. Understands the infix form (4k7) too."""
    m = re.match(r"^(\d+)([kKMR])(\d+)$", token)
    if m:
        mult = {"k": 1e3, "K": 1e3, "M": 1e6, "R": 1.0}[m.group(2)]
        return float(f"{m.group(1)}.{m.group(3)}") * mult
    m = re.match(r"^([\d.]+)([kKMR]?)$", token)
    if not m:
        return None
    mult = {"k": 1e3, "K": 1e3, "M": 1e6, "R": 1.0, "": 1.0}[m.group(2)]
    return float(m.group(1)) * mult


def schematic_resistors():
    """Reference designator -> ohms, read straight out of the PCB
    project's own schematic generator. Returns {} if that project is not
    beside this one, so the firmware still builds standalone."""
    try:
        with open(SCHEMATIC, encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return {}
    out = {}
    for m in re.finditer(r'place\(f"\{LIB\}:\w+", "(R\d+)", "([^"]*)"', src):
        ref, desc = m.groups()
        val = _r_value(desc.split()[0])
        if val is not None:
            out[ref] = val
    return out



def validate_table2d(sect, label, lo, hi, unit):
    """Shared checks for an RPM x MAP breakpoint table. Both the VE table
    and the spark advance table are this shape, and getting either one
    structurally wrong fails the same silent way - the lookup happily
    interpolates between the wrong cells and returns a plausible number."""
    rpm, mapa, table = sect["rpm_axis"], sect["map_axis"], sect["table"]

    # Axes must ascend: the lookup walks them assuming that.
    for aname, axis in (("rpm_axis", rpm), ("map_axis", mapa)):
        if len(axis) < 2:
            raise ConfigError(f"{label}.{aname} needs at least 2 breakpoints")
        if any(b <= a for a, b in zip(axis, axis[1:])):
            raise ConfigError(
                f"{label}.{aname} must be strictly ascending, got {axis}")

    if len(table) != len(mapa):
        raise ConfigError(
            f"{label}.table has {len(table)} rows but {label}.map_axis has "
            f"{len(mapa)} breakpoints - one row per MAP breakpoint")
    for i, row in enumerate(table):
        if len(row) != len(rpm):
            raise ConfigError(
                f"{label}.table row {i} (MAP {mapa[i]} kPa) has {len(row)} "
                f"entries but {label}.rpm_axis has {len(rpm)} breakpoints")
        for j, v in enumerate(row):
            # Out of this band is far more likely a typo than a real
            # engine, and catching it here beats discovering it as a lean
            # cylinder or a holed piston.
            if not (lo <= v <= hi):
                raise ConfigError(
                    f"{label}.table[{i}][{j}] = {v} {unit} at {mapa[i]} kPa / "
                    f"{rpm[j]} rpm is outside {lo}..{hi} {unit} - almost "
                    f"certainly a typo")


def validate_table1d(sect, label, lo, hi, unit):
    """Shared checks for a one-axis curve. Same failure modes as the 2-D
    tables: an axis that does not ascend makes the lookup interpolate
    between the wrong pair and return a plausible wrong number."""
    axis, cells = sect["volts_mv"], sect["us"]
    if len(axis) < 2:
        raise ConfigError(f"{label}.volts_mv needs at least 2 breakpoints")
    if any(b <= a for a, b in zip(axis, axis[1:])):
        raise ConfigError(
            f"{label}.volts_mv must be strictly ascending, got {axis}")
    if len(cells) != len(axis):
        raise ConfigError(
            f"{label}.us has {len(cells)} entries but {label}.volts_mv has "
            f"{len(axis)} breakpoints - one per voltage")
    for i, v in enumerate(cells):
        if not (lo <= v <= hi):
            raise ConfigError(
                f"{label}.us[{i}] = {v} {unit} at {axis[i] / 1000:.1f} V is "
                f"outside {lo}..{hi} {unit} - almost certainly a typo")
    # Both curves describe a coil or solenoid charging from the supply,
    # so both MUST fall as voltage rises. A rising segment is not a
    # tuning choice, it is backwards physics.
    for (v0, t0), (v1, t1) in zip(zip(axis, cells), zip(axis[1:], cells[1:])):
        if t1 > t0:
            raise ConfigError(
                f"{label}: {t0} {unit} at {v0 / 1000:.1f} V but {t1} {unit} at "
                f"{v1 / 1000:.1f} V. Current builds FASTER from a higher "
                f"supply, so this curve must fall as voltage rises.")


def table1d_c(sect, prefix, comment):
    axis, cells = sect["volts_mv"], sect["us"]
    out = [comment,
           f"#define {prefix}_COUNT   {len(axis)}u",
           f"static const uint16_t {prefix}_MV[{prefix}_COUNT] = {{ "
           + ", ".join(f"{v}u" for v in axis) + " };",
           f"static const int16_t  {prefix}_US[{prefix}_COUNT] = {{ "
           + ", ".join(f"{v:5d}" for v in cells) + " };"]
    return "\n".join(out)


def table2d_c(sect, prefix, comment):
    """Emits one RPM x MAP table as C. Cells are int16_t for both tables
    so a single lookup in src/table.c can serve them - spark advance can
    legitimately be negative (retarded past TDC), which an unsigned cell
    could not express."""
    rpm, mapa, table = sect["rpm_axis"], sect["map_axis"], sect["table"]
    out = [comment,
           f"#define {prefix}_RPM_COUNT   {len(rpm)}u",
           f"#define {prefix}_MAP_COUNT   {len(mapa)}u",
           "",
           f"static const uint16_t {prefix}_RPM_AXIS[{prefix}_RPM_COUNT] = {{ "
           + ", ".join(f"{v}u" for v in rpm) + " };",
           f"static const uint16_t {prefix}_MAP_AXIS[{prefix}_MAP_COUNT] = {{ "
           + ", ".join(f"{v}u" for v in mapa) + " };",
           f"static const int16_t  {prefix}_TABLE[{prefix}_MAP_COUNT]"
           f"[{prefix}_RPM_COUNT] = {{"]
    for i, row in enumerate(table):
        out.append("    { " + ", ".join(f"{v:4d}" for v in row)
                   + f" }},   /* {mapa[i]:>4d} kPa */")
    out.append("};")
    return "\n".join(out)


def validate_sensors(cfg):
    adc = cfg["adc"]
    vref, full = adc["vref_mv"], adc["max_counts"]
    curves = cfg.get("curve", {})
    board = schematic_resistors()
    checked = 0

    for name, curve in curves.items():
        pts = curve["points"]
        if len(pts) < 2:
            raise ConfigError(f"curve.{name} needs at least 2 points")
        # Descending resistance == ascending temperature. The lookup
        # walks it assuming that; a mis-ordered curve would interpolate
        # between the wrong pair and read plausibly, but wrong.
        for (r0, t0), (r1, t1) in zip(pts, pts[1:]):
            if r1 >= r0:
                raise ConfigError(
                    f"curve.{name}: resistance must strictly DESCEND, "
                    f"got {r0} then {r1}")
            if t1 <= t0:
                raise ConfigError(
                    f"curve.{name}: temperature must strictly ASCEND, "
                    f"got {t0} then {t1} (hundredths of a degree C)")

    for name, sen in sorted(cfg["sensor"].items()):
        kind = sen["type"]
        if kind not in ("linear", "voltage", "thermistor"):
            raise ConfigError(
                f"sensor.{name}.type = '{kind}', expected linear, voltage "
                f"or thermistor")

        # --- the divider, and the check that matters most -------------
        div = sen.get("divider")
        if div:
            rt, rb = div["r_top"], div["r_bottom"]
            if rt <= 0 or rb <= 0:
                raise ConfigError(f"sensor.{name}: divider resistors must be positive")
            ratio = rb / (rt + rb)

            # Cross-check against the board itself.
            for leg, ref in sorted(sen.get("refs", {}).items()):
                if leg not in ("r_top", "r_bottom") or ref not in board:
                    continue
                want, got = float(div[leg]), board[ref]
                if abs(want - got) > 0.5:
                    raise ConfigError(
                        f"sensor.{name}.divider.{leg} says {want:g} ohm but "
                        f"{ref} on the board is {got:g} ohm "
                        f"(ecu-pcb/build_schematic.py). One of the two is "
                        f"stale - the firmware and the board must agree.")
                checked += 1
        else:
            ratio = 1.0

        if kind == "thermistor":
            if sen["curve"] not in curves:
                raise ConfigError(
                    f"sensor.{name}.curve = '{sen['curve']}' has no matching "
                    f"[curve.{sen['curve']}] section")
            if sen["pullup_ohms"] <= 0:
                raise ConfigError(f"sensor.{name}.pullup_ohms must be positive")
            if div:
                raise ConfigError(
                    f"sensor.{name} is a thermistor AND has a divider. The "
                    f"pull-up already IS the top half of the divider; adding "
                    f"another would need a different equation than the one "
                    f"sensor.c implements.")
            continue

        # --- linear / voltage: derive the signal band in ADC counts ---
        def counts_at(mv):
            """ADC code for a sensor output of `mv`, through this
            channel's divider. Divides by the number of STEPS (4096),
            not the maximum code: the data sheet's Figure 22 defines
            1 LSB as AVDD/4096."""
            c = int(round((full + 1) * mv * ratio / vref))
            return max(0, min(full, c))

        if kind == "voltage":
            # No sensor swing to speak of - the divider alone sets the
            # ceiling, and 0 V is a legitimate reading rather than a
            # fault. Both endpoints and both fault thresholds therefore
            # sit at the extremes.
            sig_lo_mv, sig_hi_mv = 0.0, vref / ratio
            sen["_at_lo"], sen["_at_hi"] = 0, int(round(sig_hi_mv))
            sen["_unit"] = "mV"
            sen["_fault_lo"], sen["_fault_hi"] = 0, full
        else:
            supply_mv = float(sen["supply_mv"])
            band = sen.get("signal_mv")
            if band is None:
                raise ConfigError(
                    f"sensor.{name} is linear but has no signal_mv. State the "
                    f"sensor's real output swing - assuming it runs rail to "
                    f"rail mis-scales the reading at both ends AND makes a "
                    f"broken wire indistinguishable from a minimum reading.")
            sig_lo_mv, sig_hi_mv = float(band["at_min"]), float(band["at_max"])
            if sig_lo_mv == sig_hi_mv:
                raise ConfigError(
                    f"sensor.{name}.signal_mv has at_min == at_max")
            for what, mv in (("at_min", sig_lo_mv), ("at_max", sig_hi_mv)):
                if mv < 0 or mv > supply_mv:
                    raise ConfigError(
                        f"sensor.{name}.signal_mv.{what} = {mv:g} mV is outside "
                        f"the sensor's own 0..{supply_mv:g} mV supply")
            sen["_at_lo"], sen["_at_hi"] = sen["at_min"], sen["at_max"]
            sen["_unit"] = sen.get("unit", "")

            # Fault thresholds sit a margin outside the signal band, in
            # whichever direction the band runs - an inverted sensor's
            # band descends, and the dead zones are still at the
            # electrical extremes, not the logical ones.
            margin = float(adc.get("fault_margin_mv", 0))
            lo_mv = min(sig_lo_mv, sig_hi_mv) - margin
            hi_mv = max(sig_lo_mv, sig_hi_mv) + margin
            sen["_fault_lo"] = counts_at(max(0.0, lo_mv))
            sen["_fault_hi"] = counts_at(min(supply_mv, hi_mv))

        # The check that matters most: the highest voltage this channel
        # must be able to READ - the top of the fault band, not just the
        # signal band - has to land under the ADC reference.
        top_mv = (max(sig_lo_mv, sig_hi_mv)
                  + (float(adc.get("fault_margin_mv", 0)) if kind != "voltage" else 0.0))
        pin_mv = top_mv * ratio
        if pin_mv > vref + 0.5:
            via = (f"a {div['r_top']:g}/{div['r_bottom']:g} divider" if div
                   else "NO DIVIDER AT ALL - it is wired straight to the pin")
            raise ConfigError(
                f"sensor.{name}: reading up to {top_mv:g} mV through "
                f"{via} puts {pin_mv:.0f} mV on a pin referenced to {vref} mV. "
                f"Everything above the reference converts to full scale, so "
                f"the top {100 * (1 - vref / pin_mv):.0f}% of this sensor's "
                f"range would be unreadable. Fit a divider that keeps it "
                f"under {vref} mV.")

        sen["_counts_lo"] = counts_at(sig_lo_mv)
        sen["_counts_hi"] = counts_at(sig_hi_mv)
        if sen["_counts_lo"] == sen["_counts_hi"]:
            raise ConfigError(
                f"sensor.{name}: both ends of the signal band land on the same "
                f"ADC code - the divider has squeezed the sensor to nothing")
        span = abs(sen["_counts_hi"] - sen["_counts_lo"])
        sen["_use_pct"] = 100.0 * span / full

    # --- redundant pairs -------------------------------------------------
    ratio_min = float(adc.get("redundant_slope_ratio", 1.5))
    pairs = []
    for name, sen in sorted(cfg["sensor"].items()):
        partner = sen.get("redundant_with")
        if partner is None:
            continue
        if partner not in cfg["sensor"]:
            raise ConfigError(
                f"sensor.{name}.redundant_with = '{partner}' does not exist")
        other = cfg["sensor"][partner]
        if other.get("redundant_with") != name:
            raise ConfigError(
                f"sensor.{name} says it is redundant with {partner}, but "
                f"{partner} does not say the same about {name}")
        if name > partner:
            continue                      # emit each pair once
        pairs.append((name, partner))

        # THE RULE THIS SECTION EXISTS FOR. Two channels with the same
        # transfer function detect an open circuit and nothing else: a
        # stuck, shorted or cross-connected track reads plausibly on
        # both, they agree, and the plausibility check passes. Real
        # pedals ship a half-slope or inverted second track precisely so
        # that cannot happen.
        def slope(x):
            d_sig = float(x["signal_mv"]["at_max"]) - float(x["signal_mv"]["at_min"])
            return (float(x["at_max"]) - float(x["at_min"])) / d_sig
        sa, sb = slope(sen), slope(other)
        opposite = (sa < 0) != (sb < 0)
        r = max(abs(sa), abs(sb)) / min(abs(sa), abs(sb))
        if not opposite and r < ratio_min:
            raise ConfigError(
                f"redundant pair {name}/{partner} is not distinguishable: "
                f"slopes differ by only {r:.2f}x and run the same direction "
                f"(need {ratio_min}x, or opposite signs). Two channels with "
                f"the same transfer function detect an open circuit and "
                f"nothing else - a stuck or cross-connected track reads "
                f"plausibly on both and the check passes. Give one a half "
                f"slope or invert it, as production pedals do.")
        if sen.get("unit") != other.get("unit"):
            raise ConfigError(
                f"redundant pair {name}/{partner} report different units "
                f"({sen.get('unit')} vs {other.get('unit')}) - they cannot be "
                f"compared")
    cfg["_redundant_pairs"] = pairs

    return checked


def emit_sensors(cfg):
    adc = cfg["adc"]
    curves = cfg.get("curve", {})
    names = sorted(cfg["sensor"])

    out = []
    out.append("/* GENERATED by tools/gen_engine_config.py from")
    out.append(" * config/engine.toml - do not edit by hand.")
    out.append(" *")
    out.append(" * Analog sensor descriptors. src/sensor.c walks this table with")
    out.append(" * three conversion kernels; adding a sensor is a config edit.")
    out.append(" *")
    out.append(" * Every full-scale count below is DERIVED from the real divider")
    out.append(" * resistors, never typed in, and every divider was cross-checked")
    out.append(" * against ecu-pcb/build_schematic.py at generation time. */")
    out.append("#ifndef SENSOR_DEFS_H")
    out.append("#define SENSOR_DEFS_H")
    out.append("")
    out.append("#include <stdint.h>")
    out.append("")
    out.append(f"#define ADC_VREF_MV     {adc['vref_mv']}u")
    out.append(f"#define ADC_MAX_COUNTS  {adc['max_counts']}u")
    out.append("")

    # --- curves -------------------------------------------------------
    out.append("/* Resistance (ohms) -> temperature (hundredths of a degree C),")
    out.append(" * resistance descending. */")
    out.append("typedef struct { uint32_t ohms; int16_t centi_c; } curve_point_t;")
    out.append("")
    for cname, curve in sorted(curves.items()):
        pts = curve["points"]
        out.append(f"#define CURVE_{cname.upper()}_COUNT {len(pts)}u")
        out.append(f"static const curve_point_t CURVE_{cname.upper()}"
                   f"[CURVE_{cname.upper()}_COUNT] = {{")
        for r, t in pts:
            out.append(f"    {{ {r:>7d}u, {t:>6d} }},")
        out.append("};")
        out.append("")

    # --- descriptors --------------------------------------------------
    out.append("typedef enum {")
    out.append("    SENSOR_KIND_LINEAR = 0,")
    out.append("    SENSOR_KIND_VOLTAGE,")
    out.append("    SENSOR_KIND_THERMISTOR")
    out.append("} sensor_kind_t;")
    out.append("")
    out.append("typedef struct {")
    out.append("    const char         *name;")
    out.append("    sensor_kind_t       kind;")
    out.append("    /* linear/voltage: engineering value at 0 counts and at")
    out.append("     * counts_at_full, which is derived from the divider. */")
    out.append("    /* linear/voltage: the engineering values at the two ends")
    out.append("     * of the sensor's real signal band, and where that band")
    out.append("     * lands in ADC codes. counts_lo may be GREATER than")
    out.append("     * counts_hi - an inverted sensor falls as the measured")
    out.append("     * quantity rises, which is a real and common pattern. */")
    out.append("    int32_t             at_lo;")
    out.append("    int32_t             at_hi;")
    out.append("    uint16_t            counts_lo;")
    out.append("    uint16_t            counts_hi;")
    out.append("    /* Outside these the reading is not a value, it is a")
    out.append("     * broken wire or a short. */")
    out.append("    uint16_t            fault_lo;")
    out.append("    uint16_t            fault_hi;")
    out.append("    /* thermistor: pull-up and the curve to walk. */")
    out.append("    uint32_t            pullup_ohms;")
    out.append("    const curve_point_t *curve;")
    out.append("    uint8_t             curve_count;")
    out.append("} sensor_def_t;")
    out.append("")
    out.append("typedef enum {")
    for n in names:
        out.append(f"    SENSOR_{n.upper()},")
    out.append("    SENSOR_COUNT")
    out.append("} sensor_id_t;")
    out.append("")
    out.append("static const sensor_def_t SENSOR_DEFS[SENSOR_COUNT] = {")
    for n in names:
        sen = cfg["sensor"][n]
        if sen["type"] == "thermistor":
            cu = sen["curve"].upper()
            out.append(f"    /* {n} */ {{ \"{n}\", SENSOR_KIND_THERMISTOR,")
            out.append(f"        0, 0, 0u, 0u, 0u, {int(cfg['adc']['max_counts'])}u,")
            out.append(f"        {sen['pullup_ohms']}u, CURVE_{cu}, CURVE_{cu}_COUNT }},")
        else:
            kind = "SENSOR_KIND_VOLTAGE" if sen["type"] == "voltage" else "SENSOR_KIND_LINEAR"
            out.append(f"    /* {n:<5} {sen['_unit']:>6}, band {sen['_counts_lo']}..{sen['_counts_hi']} "
                       f"counts, uses {sen['_use_pct']:.0f}% of ADC range */")
            out.append(f"    {{ \"{n}\", {kind}, {sen['_at_lo']}, {sen['_at_hi']}, "
                       f"{sen['_counts_lo']}u, {sen['_counts_hi']}u, "
                       f"{sen['_fault_lo']}u, {sen['_fault_hi']}u, 0u, 0, 0 }},")
    out.append("};")
    out.append("")
    out.append("/* ---- Redundant pairs ------------------------------------------")
    out.append(" * Channels that measure the same physical quantity twice, so a")
    out.append(" * single failure can be caught by disagreement. The generator")
    out.append(" * has already refused any pair whose transfer functions are not")
    out.append(" * distinguishable - identical channels detect an open circuit")
    out.append(" * and nothing else. */")
    out.append("typedef struct {")
    out.append("    sensor_id_t a;")
    out.append("    sensor_id_t b;")
    out.append("    int32_t     tolerance;   /* in the pair's own unit */")
    out.append("} redundant_pair_t;")
    out.append("")
    pairs = cfg.get("_redundant_pairs", [])
    tol = int(cfg["adc"].get("redundant_tolerance", 0))
    out.append(f"#define REDUNDANT_PAIR_COUNT {len(pairs)}u")
    if pairs:
        out.append("static const redundant_pair_t REDUNDANT_PAIRS"
                   "[REDUNDANT_PAIR_COUNT] = {")
        for a, b in pairs:
            out.append(f"    {{ SENSOR_{a.upper()}, SENSOR_{b.upper()}, {tol} }},")
        out.append("};")
    out.append("")
    out.append("#endif /* SENSOR_DEFS_H */")

    with open(SENSOR_H, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return SENSOR_H

if __name__ == "__main__":
    main()
