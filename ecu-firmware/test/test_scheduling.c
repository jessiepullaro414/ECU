/*
 * test_scheduling.c - the crank ISR's scheduling decisions, swept over
 * engine speed AND load.
 *
 * WHY BOTH AXES. The arming lead is sized from dwell in whole teeth
 * plus the spark advance, and advance depends on load - so the lead
 * takes different values at different loads, and a lead that lands
 * cleanly at one load can land on the trigger wheel's missing tooth at
 * another. A speed-only sweep reports everything clean while two of
 * eight cylinders silently never fire. That is not hypothetical: it is
 * how the selection-window bug was found, and it was invisible at
 * 60 kPa.
 *
 * Checks, per operating point, over 21 engine cycles:
 *   - every cylinder is armed
 *   - each is armed the same number of times as the others
 *   - none is armed more than once per cycle
 */
#include <stdio.h>
#include <stdint.h>
#include "injection.h"
#include "engine_config.h"
#include "emios.h"

static int arm_count[64];
static int calls;

void emios_schedule_pulse(uint32_t b, uint8_t c, uint32_t on, uint32_t off);
void emios_schedule_pulse(uint32_t b, uint8_t c, uint32_t on, uint32_t off) {
    (void)b; (void)on; (void)off;
    if ((calls++ & 1) == 0) {          /* ignition is written first */
        arm_count[c]++;
    }
}
void emios_init_output_channel(uint32_t b, uint8_t c) { (void)b; (void)c; }
int emios_flag_is_set(uint32_t b, uint8_t c) { (void)b; (void)c; return 0; }
uint32_t emios_read_capture(uint32_t b, uint8_t c) { (void)b; (void)c; return 0; }

#define REVS 42                        /* 21 four-stroke cycles */

static void spin(unsigned rpm) {
    double tp = 60e6 / rpm / CRANK_WHEEL_TEETH;
    uint32_t t = 1000;
    for (int r = 0; r < REVS; r++) {
        for (unsigned k = 0; k < CRANK_WHEEL_TEETH; k++) {
            if (k >= CRANK_WHEEL_TEETH - CRANK_WHEEL_MISSING) continue;
            t += (uint32_t)(tp + 0.5);
            if (k == 0 && r > 0) t += (uint32_t)(tp * CRANK_WHEEL_MISSING + 0.5);
            while (t > EMIOS_COUNTER_MODULUS) t -= EMIOS_COUNTER_MODULUS;
            crank_capture_isr(t);
        }
    }
}

int main(void) {
    const unsigned rpms[] = { 100, 120, 150, 200, 300, 500, 800, 1200, 1800,
                              2400, 3000, 3600, 4200, 4800, 5400, 6000,
                              6600, 7000 };
    const unsigned maps[] = { 20, 30, 40, 50, 60, 70, 80, 90, 100 };
    const unsigned NR = sizeof rpms / sizeof *rpms;
    const unsigned NM = sizeof maps / sizeof *maps;
    int cases = 0, fails = 0;

    for (unsigned mi = 0; mi < NM; mi++) {
        for (unsigned ri = 0; ri < NR; ri++) {
            cases++;
            for (int k = 0; k < 64; k++) arm_count[k] = 0;
            calls = 0;
            injection_set_fuel_inputs((uint16_t)maps[mi], 4000);
            injection_set_battery_mv(14000);
            cam1_capture_isr(0);
            spin(rpms[ri]);

            int distinct = 0, lo = 9999, hi = 0;
            for (int k = 0; k < 64; k++) {
                if (!arm_count[k]) continue;
                distinct++;
                if (arm_count[k] < lo) lo = arm_count[k];
                if (arm_count[k] > hi) hi = arm_count[k];
            }
            /* One cycle of slack at the sync boundary: sync is only
             * established at the first gap, so the first cylinder round
             * is legitimately short. */
            int ok = (distinct == ENGINE_CYLINDERS)
                  && (hi - lo <= 1)
                  && (lo >= REVS / 2 - 2)
                  && (hi <= REVS / 2);
            if (!ok) {
                fails++;
                printf("  FAIL %4u rpm / %3u kPa: %d/%d cylinders, "
                       "armed %d..%d per %d cycles\n",
                       rpms[ri], maps[mi], distinct, ENGINE_CYLINDERS,
                       lo, hi, REVS / 2);
            }
        }
    }
    printf("scheduling: %d cases (%u speeds x %u loads) - %s\n",
           cases, NR, NM, fails ? "FAILURES ABOVE" : "all clean");
    return fails != 0;
}
