/*
 * fuel.c - see fuel.h for the physics and what is deliberately absent.
 */
#include "fuel.h"
#include "engine_config.h"
#include "table.h"

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

uint8_t fuel_ve_lookup(uint16_t rpm, uint16_t map_kpa) {
    /* The bilinear interpolation used to live here. It moved to
     * table.c when the spark advance table arrived needing exactly the
     * same lookup - a second copy would have been a second chance for
     * the two tables to disagree about what "halfway between cells"
     * means. Host-verified identical before and after the move. */
    int32_t ve = table2d_lookup(VE_RPM_AXIS, (uint8_t)VE_RPM_COUNT,
                                VE_MAP_AXIS, (uint8_t)VE_MAP_COUNT,
                                &VE_TABLE[0][0], rpm, map_kpa);
    return (uint8_t)ve;
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
