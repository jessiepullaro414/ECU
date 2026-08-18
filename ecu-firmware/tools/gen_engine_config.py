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
    print("Config valid. Wrote " + OUT)


if __name__ == "__main__":
    main()
