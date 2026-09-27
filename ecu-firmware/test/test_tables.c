/*
 * test_tables.c - the tuning tables and the fuel calculation.
 *
 * The headline check is the last one: the integer speed-density path
 * against the same physics computed in floating point. Everything this
 * firmware does with fuel rests on that arithmetic, and a 64-bit
 * intermediate that was NOT optional lives inside it - at 100 kPa on a
 * 712 cc cylinder the numerator passes 2e10 before the divide, and a
 * wrapped air mass produces a pulse width that looks entirely
 * plausible.
 */
#include <stdio.h>
#include <stdint.h>
#include "fuel.h"
#include "ignition.h"
#include "sensor.h"
#include "engine_config.h"

static int fails;
static void check(int ok, const char *what) {
    if (!ok) { fails++; printf("  FAIL: %s\n", what); }
}

int main(void) {
    /* --- every table cell must round-trip exactly ------------------- */
    for (unsigned m = 0; m < VE_MAP_COUNT; m++)
        for (unsigned r = 0; r < VE_RPM_COUNT; r++)
            check(fuel_ve_lookup(VE_RPM_AXIS[r], VE_MAP_AXIS[m])
                      == VE_TABLE[m][r], "VE cell round-trip");

    for (unsigned m = 0; m < SPARK_MAP_COUNT; m++)
        for (unsigned r = 0; r < SPARK_RPM_COUNT; r++)
            check(ignition_advance_deg(SPARK_RPM_AXIS[r], SPARK_MAP_AXIS[m])
                      == SPARK_TABLE[m][r], "spark cell round-trip");

    for (unsigned i = 0; i < DWELL_COUNT; i++)
        check(ignition_dwell_us(DWELL_MV[i]) == DWELL_US[i],
              "dwell curve round-trip");

    /* --- interpolation and clamping --------------------------------- */
    check(fuel_ve_lookup(2500, 60) == 73, "VE midpoint 2500/60 == 73");
    check(ignition_advance_deg(2500, 60) == 31, "spark midpoint 2500/60 == 31");
    check(ignition_advance_deg(100, 10) == SPARK_TABLE[0][0], "spark low corner");
    check(ignition_advance_deg(9000, 250)
              == SPARK_TABLE[SPARK_MAP_COUNT - 1][SPARK_RPM_COUNT - 1],
          "spark high corner");

    /* --- the spark table's physical shape --------------------------- */
    for (unsigned m = 0; m < SPARK_MAP_COUNT; m++)
        for (unsigned r = 0; r + 1 < SPARK_RPM_COUNT; r++)
            check(SPARK_TABLE[m][r + 1] >= SPARK_TABLE[m][r],
                  "advance must rise with rpm");
    for (unsigned r = 0; r < SPARK_RPM_COUNT; r++)
        for (unsigned m = 0; m + 1 < SPARK_MAP_COUNT; m++)
            check(SPARK_TABLE[m + 1][r] <= SPARK_TABLE[m][r],
                  "advance must fall with load");

    /* --- the angle wrap, which cylinder 1 hits every cycle ---------- */
    /* Cylinder 1 sits at angle 0, so its spark must roll back to the
     * top of the cycle rather than going negative. */
    check(ignition_spark_angle(0, 26) == ENGINE_CYCLE_DEGREES - 26,
          "spark angle wraps below zero");
    check(ignition_spark_angle(0, -10) == 10, "negative advance retards past TDC");

    /* --- speed-density against floating-point physics --------------- */
    {
        double T = 313.15, V = (double)FUEL_DISPLACEMENT_CC / ENGINE_CYLINDERS;
        double ve = fuel_ve_lookup(3000, 100) / 100.0;
        double m_air  = (100e3 * V * 1e-6) / (287.05 * T) * ve;
        double m_fuel = m_air / (FUEL_TARGET_AFR_X10 / 10.0);
        double flow   = FUEL_INJECTOR_CC_MIN * (double)FUEL_DENSITY_MG_CC / 60.0e6;
        double want   = m_fuel / flow * 1e6;
        uint32_t got  = fuel_pulse_width_us(3000, 100, 4000);
        double err    = 100.0 * (got - want) / want;
        printf("  speed-density 3000 rpm / 100 kPa / 40 C: integer %u us vs "
               "float %.0f us (%.2f%%)\n", got, want, err);
        check(err < 0.5 && err > -0.5, "integer fuel path within 0.5% of physics");
    }

    /* --- IAT floor: a disconnected sensor must not command a huge pulse */
    {
        uint32_t cold = fuel_pulse_width_us(3000, 100, -27300);  /* -273 C */
        uint32_t ref  = fuel_pulse_width_us(3000, 100, -5000);   /* -50 C  */
        check(cold == ref, "impossible cold clamps to the -50 C floor");
    }

    printf("tables: %s\n", fails ? "FAILURES ABOVE" : "all clean");
    return fails != 0;
}
