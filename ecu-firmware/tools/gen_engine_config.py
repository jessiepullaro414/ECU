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

    ve = cfg["ve"]
    rpm, mapa, table = ve["rpm_axis"], ve["map_axis"], ve["table"]

    # Axes must ascend: the lookup walks them assuming that, and a
    # mis-ordered axis would silently interpolate between the wrong cells.
    for name, axis in (("rpm_axis", rpm), ("map_axis", mapa)):
        if len(axis) < 2:
            raise ConfigError(f"ve.{name} needs at least 2 breakpoints")
        if any(b <= a for a, b in zip(axis, axis[1:])):
            raise ConfigError(
                f"ve.{name} must be strictly ascending, got {axis}")

    if len(table) != len(mapa):
        raise ConfigError(
            f"ve.table has {len(table)} rows but ve.map_axis has "
            f"{len(mapa)} breakpoints - one row per MAP breakpoint")
    for i, row in enumerate(table):
        if len(row) != len(rpm):
            raise ConfigError(
                f"ve.table row {i} (MAP {mapa[i]} kPa) has {len(row)} entries "
                f"but ve.rpm_axis has {len(rpm)} breakpoints")
        for j, v in enumerate(row):
            # A VE outside this range is far more likely a typo than a
            # real engine. Catching it here beats discovering it as a
            # lean cylinder at load.
            if not (10 <= v <= 150):
                raise ConfigError(
                    f"ve.table[{i}][{j}] = {v}% at {mapa[i]} kPa / {rpm[j]} rpm "
                    f"is outside 10..150% - almost certainly a typo")

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
#define INJECTOR_DEAD_TIME_US {dead_time}u
#define IGNITION_DWELL_US     {dwell}u

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

/* ---- VE table ------------------------------------------------------
 * Rows are MAP breakpoints, columns RPM. Bilinearly interpolated.
 * A STARTING SHAPE, NOT A TUNED MAP - see config/engine.toml. */
#define VE_RPM_COUNT   {rpm_count}u
#define VE_MAP_COUNT   {map_count}u

static const uint16_t VE_RPM_AXIS[VE_RPM_COUNT] = {{ {rpm_axis} }};
static const uint16_t VE_MAP_AXIS[VE_MAP_COUNT] = {{ {map_axis} }};
static const uint8_t  VE_TABLE[VE_MAP_COUNT][VE_RPM_COUNT] = {{
{ve_rows}}};

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
        dead_time=cfg["injection"]["dead_time_us"],
        dwell=cfg["ignition"]["dwell_us"],
        displacement=cfg["fuel"]["displacement_cc"],
        inj_flow=cfg["fuel"]["injector_cc_per_min"],
        fuel_density=cfg["fuel"]["fuel_density_mg_per_cc"],
        afr=cfg["fuel"]["target_afr_x10"],
        map_min=cfg["map_sensor"]["kpa_at_min"],
        map_max=cfg["map_sensor"]["kpa_at_max"],
        map_counts=cfg["map_sensor"]["adc_counts_at_max"],
        rpm_count=len(cfg["ve"]["rpm_axis"]),
        map_count=len(cfg["ve"]["map_axis"]),
        rpm_axis=", ".join(f"{v}u" for v in cfg["ve"]["rpm_axis"]),
        map_axis=", ".join(f"{v}u" for v in cfg["ve"]["map_axis"]),
        ve_rows="".join(
            "    { " + ", ".join(f"{v:3d}u" for v in row) + " },"
            + f"   /* {cfg['ve']['map_axis'][i]:>4d} kPa */\n"
            for i, row in enumerate(cfg["ve"]["table"])),
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
    print(f"  {cfg['fuel']['displacement_cc']} cc, "
          f"{cfg['fuel']['injector_cc_per_min']} cc/min injectors, "
          f"target AFR {cfg['fuel']['target_afr_x10'] / 10:.1f}")
    print("Config valid. Wrote " + OUT)


if __name__ == "__main__":
    main()
