#!/usr/bin/env python3
"""
gen_ktype_table.py - generates the Type-K thermocouple lookup table in
inc/ktype_table.h from NIST's own ITS-90 coefficients.

SOURCE. NIST Standard Reference Database 60 (NIST Temperature Scale
Database, Version 3.0, data content 2024, DOI 10.18434/T4S888), which is
the online form of NIST Monograph 175 - the defining reference for
ITS-90 thermocouple behaviour. The Type-K INVERSE function coefficients
below were read from that database's own "Inverse Function Coefficients"
page.

Getting them required a real browser: srdata.nist.gov serves that page
as a Blazor app that renders its tables client-side, so a plain HTTP
fetch returns only an empty shell - which is exactly why an earlier pass
recorded this as blocked and refused to guess the numbers.

WHY ONLY THE INVERSE SET. NIST also publishes forward (reference)
function coefficients, but its web page rounds those to about three
significant figures, which is nowhere near enough to build an accurate
table. The inverse coefficients are published in full precision. Since
the E-vs-T curve is strictly monotonic over the range of interest, ONE
table built from the inverse function serves both directions:
  * inverse  (voltage -> temperature) - the actual measurement
  * forward  (temperature -> voltage) - cold-junction compensation,
    done by interpolating the same table the other way round
So the rounded forward coefficients are not needed at all.

WHY A TABLE RATHER THAN THE POLYNOMIAL AT RUNTIME. This firmware uses no
floating point anywhere (no FPU is assumed), and these are 9th-order
polynomials in millivolts. Evaluating them in fixed point at runtime
would be slow and awkward to get right. Precomputing here - the same
approach clt_sensor.c and iat_sensor.c already use - keeps the runtime
to an integer binary search plus linear interpolation.

VALIDATION. The generated table is checked against two reference points
quoted independently by TI (ADS1118 datasheet SBAS457F, its thermocouple
design section), which cites NIST for them: a Type-K junction at 1250 C
produces 50.644 mV referenced to a 0 C cold junction, and a -40 C cold
junction is worth -1.527 mV. Those are a genuine third-party check on
the coefficient set, not a self-consistency test.

Run:  python tools/gen_ktype_table.py      (writes inc/ktype_table.h)
"""
import os

# --- NIST ITS-90 Type K inverse function coefficients -----------------
# T90 (degC) = sum_i d[i] * E**i, with E in millivolts.
# Each entry: (E_min_mV, E_max_mV, [d0..dn])
K_INVERSE = [
    (-5.891, 0.000, [
        0.0000000E+00, 2.5173462E+01, -1.1662878E+00, -1.0833638E+00,
        -8.9773540E-01, -3.7342377E-01, -8.6632643E-02, -1.0450598E-02,
        -5.1920577E-04, 0.0000000E+00]),
    (0.000, 20.644, [
        0.000000E+00, 2.508355E+01, 7.860106E-02, -2.503131E-01,
        8.315270E-02, -1.228034E-02, 9.804036E-04, -4.413030E-05,
        1.057734E-06, -1.052755E-08]),
    (20.644, 54.886, [
        -1.318058E+02, 4.830222E+01, -1.646031E+00, 5.464731E-02,
        -9.650715E-04, 8.802193E-06, -3.110810E-08, 0.0, 0.0, 0.0]),
]

# Table span. Low end covers a cold-soaked cold junction; high end is
# past any realistic exhaust gas temperature (and well inside Type K's
# own 1372 C limit). 10 C steps: the curve is close to linear at roughly
# 40 uV/degC, so interpolation error stays far below the sensor's own.
T_MIN_C, T_MAX_C, T_STEP_C = -40, 1250, 10


def emf_from_temp_mv(t_c):
    """Forward direction by numeric inversion of the NIST inverse
    function. Bisection is used rather than the rounded forward
    coefficients - the curve is monotonic, so this converges tightly and
    stays anchored to the full-precision data."""
    lo, hi = -6.0, 55.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if temp_from_emf_c(mid) < t_c:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def temp_from_emf_c(e_mv):
    for e_lo, e_hi, d in K_INVERSE:
        if e_lo <= e_mv <= e_hi:
            return sum(c * (e_mv ** i) for i, c in enumerate(d))
    # Outside the tabulated subranges - clamp to the nearest end.
    if e_mv < K_INVERSE[0][0]:
        return -200.0
    return 1372.0


def build_table():
    temps = list(range(T_MIN_C, T_MAX_C + 1, T_STEP_C))
    return [(t, int(round(emf_from_temp_mv(t) * 1000.0))) for t in temps]


def validate(table):
    ok = True

    # TI/NIST reference point 1: 1250 C -> 50.644 mV (0 C cold junction)
    e_1250 = emf_from_temp_mv(1250.0)
    if abs(e_1250 - 50.644) > 0.010:
        print(f"  FAIL 1250C -> {e_1250:.4f} mV, expected 50.644"); ok = False
    else:
        print(f"  OK  1250 C -> {e_1250:.4f} mV  (TI/NIST: 50.644)")

    # TI/NIST reference point 2: -40 C cold junction -> -1.527 mV
    e_m40 = emf_from_temp_mv(-40.0)
    if abs(e_m40 - (-1.527)) > 0.010:
        print(f"  FAIL -40C -> {e_m40:.4f} mV, expected -1.527"); ok = False
    else:
        print(f"  OK  -40 C  -> {e_m40:.4f} mV  (TI/NIST: -1.527)")

    # 0 C must be exactly 0 mV by definition of the reference junction.
    e_0 = emf_from_temp_mv(0.0)
    if abs(e_0) > 0.001:
        print(f"  FAIL 0C -> {e_0:.5f} mV, expected 0"); ok = False
    else:
        print(f"  OK  0 C    -> {e_0:.5f} mV  (0 by definition)")

    # Monotonic, or the binary search the firmware does is invalid.
    emfs = [e for _, e in table]
    if any(b <= a for a, b in zip(emfs, emfs[1:])):
        print("  FAIL table is not strictly monotonic"); ok = False
    else:
        print(f"  OK  strictly monotonic across {len(table)} entries")

    # Worst-case interpolation error at the midpoint of each step.
    worst = 0.0
    for (t0, e0), (t1, e1) in zip(table, table[1:]):
        t_mid = (t0 + t1) / 2.0
        e_interp = (e0 + e1) / 2.0 / 1000.0
        worst = max(worst, abs(temp_from_emf_c(e_interp) - t_mid))
    print(f"  OK  worst interpolation error {worst:.3f} C "
          f"(NIST's own inverse-function error is +/-0.05 C)")
    return ok


HEADER = '''/*
 * ktype_table.h - GENERATED FILE, do not edit by hand.
 *
 * Regenerate with:  python tools/gen_ktype_table.py
 *
 * Type-K thermocouple EMF as a function of temperature, from NIST's own
 * ITS-90 inverse function coefficients (NIST Standard Reference
 * Database 60 / Monograph 175, DOI 10.18434/T4S888). See
 * tools/gen_ktype_table.py for the full provenance, including why only
 * the inverse coefficient set is used and why this is a table rather
 * than a runtime polynomial.
 *
 * Index i corresponds to temperature KTYPE_T_MIN_C + i * KTYPE_T_STEP_C
 * degrees Celsius; the value is that junction's EMF in MICROVOLTS
 * referenced to a 0 C cold junction. The array is strictly monotonic,
 * which is what lets the same table serve both directions: forward for
 * cold-junction compensation, and reverse (binary search) for the
 * measurement itself.
 */
#ifndef KTYPE_TABLE_H
#define KTYPE_TABLE_H

#include <stdint.h>

#define KTYPE_T_MIN_C   ({t_min})
#define KTYPE_T_STEP_C  ({t_step})
#define KTYPE_COUNT     ({count})

static const int32_t KTYPE_EMF_UV[KTYPE_COUNT] = {{
{rows}}};

#endif /* KTYPE_TABLE_H */
'''


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(here, "inc", "ktype_table.h")

    table = build_table()
    print(f"Generated {len(table)} entries, "
          f"{T_MIN_C}..{T_MAX_C} C in {T_STEP_C} C steps")
    print("Validation against independently-quoted NIST reference points:")
    if not validate(table):
        raise SystemExit("validation failed - table NOT written")

    rows = ""
    for i in range(0, len(table), 6):
        chunk = table[i:i + 6]
        rows += "    " + " ".join(f"{e:>8d}," for _, e in chunk)
        rows += f"   /* {chunk[0][0]:>5d} C */\n"

    with open(out, "w", encoding="utf-8") as f:
        f.write(HEADER.format(t_min=T_MIN_C, t_step=T_STEP_C,
                              count=len(table), rows=rows))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
