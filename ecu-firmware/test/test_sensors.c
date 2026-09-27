/*
 * test_sensors.c - the analog conversion kernels.
 *
 * The thermistor cases are checked against the RETIRED clt_sensor.c /
 * iat_sensor.c arithmetic, transcribed below, so the generic table can
 * be proved equivalent to the per-sensor drivers it replaced rather
 * than only self-consistent.
 */
#include <stdio.h>
#include <stdint.h>
#include "sensor.h"

static int fails;
static void check(int ok, const char *what) {
    if (!ok) { fails++; printf("  FAIL: %s\n", what); }
}

/* --- the retired drivers, for comparison ---------------------------- */
static const struct { unsigned ohms; int tenthF; } OLD_LUT[] = {
    {100700,-400},{47600,-200},{24000,0},{12800,200},{7190,400},{4220,600},
    {2238,860},{1590,1000},{1000,1200},{653,1400},{437,1600},{300,1800},
    {210,2000},{177,2102}};
static unsigned old_res(unsigned raw, unsigned pu) {
    return (raw >= 4095u) ? 0xFFFFFFFFu : (pu * raw) / (4095u - raw);
}
static int old_tenthF(unsigned ohms) {
    if (ohms >= OLD_LUT[0].ohms) return OLD_LUT[0].tenthF;
    if (ohms <= OLD_LUT[13].ohms) return OLD_LUT[13].tenthF;
    int i = 0;
    while (i + 1 < 14 && ohms <= OLD_LUT[i + 1].ohms) i++;
    int rh = (int)OLD_LUT[i].ohms, rl = (int)OLD_LUT[i + 1].ohms;
    int tl = OLD_LUT[i].tenthF, th = OLD_LUT[i + 1].tenthF;
    return tl + ((th - tl) * (rh - (int)ohms)) / (rh - rl);
}

/* raw ADC code for a sensor output of `mv` through a 2:1 divider */
static uint16_t code(unsigned mv) {
    unsigned c = (unsigned)((4096.0 * mv * 0.5) / 3300.0 + 0.5);
    return (uint16_t)(c > 4095u ? 4095u : c);
}

int main(void) {
    /* --- thermistor: identical resistance, temperature within rounding */
    double worst = 0;
    for (unsigned raw = 200; raw <= 3900; raw += 100) {
        check(sensor_resistance_ohms(SENSOR_CLT, (uint16_t)raw)
                  == old_res(raw, 1000u), "CLT resistance matches retired driver");
        double c_new = sensor_convert(SENSOR_CLT, (uint16_t)raw) / 100.0;
        double c_old = (old_tenthF(old_res(raw, 1000u)) / 10.0 - 32.0) * 5.0 / 9.0;
        double d = c_new - c_old; if (d < 0) d = -d;
        if (d > worst) worst = d;
    }
    printf("  thermistor vs retired driver: worst %.2f C (F->C rounding)\n", worst);
    check(worst < 0.1, "thermistor within 0.1 C of the retired driver");

    /* --- linear: the signal band, and what lies outside it ---------- */
    check(sensor_convert(SENSOR_MAP, code(500)) == 10,  "MAP at 0.5 V == 10 kPa");
    check(sensor_convert(SENSOR_MAP, code(4500)) == 105, "MAP at 4.5 V == 105 kPa");
    /* A broken wire pulls the pin to ground. Before the signal band was
     * described this read as a perfectly valid 10 kPa. */
    check(sensor_convert(SENSOR_MAP, 0) == SENSOR_INVALID,
          "MAP at 0 V is a fault, not a minimum reading");
    check(sensor_convert(SENSOR_MAP, code(5000)) == SENSOR_INVALID,
          "MAP shorted to the supply is a fault");
    /* Just inside the fault margin is tolerance, not a fault. */
    check(sensor_convert(SENSOR_MAP, code(400)) == 10,
          "MAP just below band clamps rather than faulting");

    /* --- inverted channel: falls as the measured quantity rises ----- */
    check(sensor_convert(SENSOR_TPS2, code(4500)) == 0,
          "inverted TPS2 reads 0% at 4.5 V");
    check(sensor_convert(SENSOR_TPS2, code(500)) == 10000,
          "inverted TPS2 reads 100% at 0.5 V");
    check(sensor_convert(SENSOR_TPS2, code(2500)) > 4000
       && sensor_convert(SENSOR_TPS2, code(2500)) < 6000,
          "inverted TPS2 is mid-scale at 2.5 V");

    /* --- half-slope channel ----------------------------------------- */
    check(sensor_convert(SENSOR_APP2, code(500)) == 0,    "APP2 0% at 0.5 V");
    check(sensor_convert(SENSOR_APP2, code(2500)) == 10000, "APP2 100% at 2.5 V");

    /* --- voltage channel: 0 V IS valid here -------------------------- */
    check(sensor_convert(SENSOR_VBATT, 0) == 0, "VBATT reads 0 mV at 0 counts");
    {
        int32_t mv = sensor_convert(SENSOR_VBATT, 2230);
        check(mv > 13800 && mv < 14300, "VBATT ~14 V at a plausible count");
    }

    /* --- sentinels --------------------------------------------------- */
    check(sensor_resistance_ohms(SENSOR_CLT, 4095) == UINT32_MAX,
          "open thermistor returns the no-resistance sentinel");
    check(sensor_convert(SENSOR_CLT, 4095) == SENSOR_INVALID,
          "open thermistor converts to SENSOR_INVALID");
    check(sensor_resistance_ohms(SENSOR_MAP, 2000) == 0,
          "resistance on a non-thermistor returns 0");
    check(sensor_convert((sensor_id_t)99, 100) == SENSOR_INVALID,
          "out-of-range sensor id is rejected");

    printf("sensors: %s\n", fails ? "FAILURES ABOVE" : "all clean");
    return fails != 0;
}
