/*
 * fuel.c - see fuel.h for the physics and what is deliberately absent.
 */
#include "fuel.h"
#include "engine_config.h"

/* Air's specific gas constant is 287.05 J/(kg K). The calculation wants
 * air mass in micrograms from kPa, cc and Kelvin:
 *
 *   m[g] = (P[Pa] * V[m3]) / (R * T[K])
 *        = (MAP[kPa]*1e3 * V[cc]*1e-6) / (287.05 * T)
 *
 * so m[ug] = MAP * V * 1e6 / (287.05 * T), and 1e6/287.05 = 3483.7.
 * Rounded to 3484, which is a 0.01% error - far below the accuracy of
 * any of the inputs feeding it. */
#define AIR_UG_PER_KPA_CC_K   3484u

/* 0 C in Kelvin, x100 to match the centi-degree inputs. */
#define KELVIN_OFFSET_CENTI   27315

uint16_t fuel_map_kpa_from_adc(uint16_t adc_counts) {
    if (adc_counts >= MAP_ADC_AT_MAX) {
        return (uint16_t)MAP_KPA_AT_MAX;
    }
    /* Linear between the two calibrated points. The sensor's minimum is
     * at 0 counts by definition of a ratiometric output. */
    uint32_t span = (uint32_t)MAP_KPA_AT_MAX - (uint32_t)MAP_KPA_AT_MIN;
    return (uint16_t)((uint32_t)MAP_KPA_AT_MIN
                      + (span * (uint32_t)adc_counts) / MAP_ADC_AT_MAX);
}

/* Finds the axis cell containing `value` and how far into it we are,
 * scaled to 0..FRAC_ONE. Returns the lower index; `frac` gets the
 * position within that interval. Clamping at both ends is deliberate:
 * running off the edge of the map should hold the edge value, not
 * extrapolate a VE the engine was never measured at. */
#define FRAC_ONE  256

static uint8_t axis_lookup(const uint16_t *axis, uint8_t count,
                           uint16_t value, int32_t *frac) {
    if (value <= axis[0]) {
        *frac = 0;
        return 0u;
    }
    if (value >= axis[count - 1u]) {
        *frac = 0;
        return (uint8_t)(count - 1u);
    }
    uint8_t i = 0u;
    while (((uint8_t)(i + 1u) < count) && (value >= axis[i + 1u])) {
        i++;
    }
    int32_t lo = (int32_t)axis[i];
    int32_t hi = (int32_t)axis[i + 1u];
    *frac = (((int32_t)value - lo) * FRAC_ONE) / (hi - lo);
    return i;
}

/* One step of linear interpolation between two table cells. Signed
 * throughout: the difference between neighbouring cells is negative
 * wherever the VE curve falls off, and doing this in unsigned would
 * wrap on exactly the high-RPM cells that matter most. */
static int32_t lerp(int32_t a, int32_t b, int32_t frac) {
    return a + (((b - a) * frac) / FRAC_ONE);
}

uint8_t fuel_ve_lookup(uint16_t rpm, uint16_t map_kpa) {
    int32_t rf, mf;
    uint8_t ri = axis_lookup(VE_RPM_AXIS, (uint8_t)VE_RPM_COUNT, rpm, &rf);
    uint8_t mi = axis_lookup(VE_MAP_AXIS, (uint8_t)VE_MAP_COUNT, map_kpa, &mf);

    /* At the top of an axis the lookup returns the last index and a zero
     * fraction, so pairing it with itself interpolates to that same
     * value - no special case needed past this clamp. */
    uint8_t ri2 = ((uint8_t)(ri + 1u) < VE_RPM_COUNT) ? (uint8_t)(ri + 1u) : ri;
    uint8_t mi2 = ((uint8_t)(mi + 1u) < VE_MAP_COUNT) ? (uint8_t)(mi + 1u) : mi;

    /* Bilinear: interpolate along RPM on both MAP rows, then between
     * those two results along MAP. */
    int32_t low  = lerp((int32_t)VE_TABLE[mi][ri],  (int32_t)VE_TABLE[mi][ri2],  rf);
    int32_t high = lerp((int32_t)VE_TABLE[mi2][ri], (int32_t)VE_TABLE[mi2][ri2], rf);
    return (uint8_t)lerp(low, high, mf);
}

uint32_t fuel_pulse_width_us(uint16_t rpm, uint16_t map_kpa, int32_t iat_centiC) {
    uint32_t ve = fuel_ve_lookup(rpm, map_kpa);

    /* Absolute temperature. Guard the bottom: a disconnected IAT sensor
     * reading impossibly cold would otherwise divide by something near
     * zero and command an enormous pulse. 223.15 K is -50 C, below any
     * real intake temperature but still safely non-zero. */
    int32_t t_centiK = iat_centiC + KELVIN_OFFSET_CENTI;
    if (t_centiK < 22315) {
        t_centiK = 22315;
    }

    /* Air mass in micrograms. 64-bit is not caution here, it is
     * necessary: at 100 kPa with a 712 cc cylinder the numerator passes
     * 2e10 before the divide, which a 32-bit value would have silently
     * wrapped - and a wrapped air mass produces a plausible-looking but
     * badly wrong pulse width.
     * The centi-degree scaling and the VE percentage both contribute a
     * factor of 100 and they cancel exactly, so neither appears below -
     * written out rather than left implicit:
     *   m_ug = MAP * V * 3484 * (VE/100) / (t_centiK/100)
     *        = MAP * V * 3484 * VE / t_centiK
     */
    uint64_t num = (uint64_t)map_kpa * FUEL_CYL_VOLUME_CC
                 * AIR_UG_PER_KPA_CC_K * ve;
    uint32_t air_ug = (uint32_t)(num / (uint64_t)t_centiK);

    /* Fuel mass from the target ratio (stored x10). */
    uint32_t fuel_ug = (uint32_t)(((uint64_t)air_ug * 10u) / FUEL_TARGET_AFR_X10);

    /* Injector delivery rate in micrograms per microsecond:
     *   cc/min * mg/cc  ->  mg/min, /60 -> mg/s, and 1 mg/s == 1 ug/ms,
     * so ug/us = (cc_min * mg_cc) / 60000. Computed at full precision
     * rather than pre-divided, to avoid throwing away resolution on
     * small injectors. */
    uint64_t flow_ug_per_us_x1000 =
        ((uint64_t)FUEL_INJECTOR_CC_MIN * FUEL_DENSITY_MG_CC * 1000u) / 60000u;
    if (flow_ug_per_us_x1000 == 0u) {
        return 0u;   /* misconfigured injector; refuse rather than divide by zero */
    }

    return (uint32_t)(((uint64_t)fuel_ug * 1000u) / flow_ug_per_us_x1000);
}
