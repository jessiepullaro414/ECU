/*
 * ignition.c - see ignition.h for why spark advance exists and what is
 * deliberately absent.
 */
#include "ignition.h"
#include "engine_config.h"
#include "table.h"

int16_t ignition_advance_deg(uint16_t rpm, uint16_t map_kpa) {
    int32_t adv = table2d_lookup(SPARK_RPM_AXIS, (uint8_t)SPARK_RPM_COUNT,
                                 SPARK_MAP_AXIS, (uint8_t)SPARK_MAP_COUNT,
                                 &SPARK_TABLE[0][0], rpm, map_kpa);

    /* Belt and braces against a table that somehow got past the
     * generator's own band check: the scheduling lead is sized for
     * SPARK_ADVANCE_MAX_DEG, and an advance beyond it would be
     * scheduled into the previous cylinder's window. Clamping here
     * costs nothing and keeps a bad table from becoming a bad spark. */
    if (adv > SPARK_ADVANCE_MAX_DEG) {
        adv = SPARK_ADVANCE_MAX_DEG;
    }
    return (int16_t)adv;
}

uint16_t ignition_spark_angle(uint16_t tdc_angle_deg, int16_t advance_deg) {
    /* Advance moves the event EARLIER, so it subtracts from TDC. Add a
     * full cycle before taking the remainder so a cylinder whose TDC
     * sits near zero rolls back to the top of the cycle rather than
     * going negative - cylinder 1 is at angle 0 in this firmware's own
     * firing-order walk, so this is the common case, not the corner
     * case. Negative advance (retard) works through the same path
     * without a special branch. */
    int32_t angle = (int32_t)tdc_angle_deg - (int32_t)advance_deg;
    angle %= (int32_t)ENGINE_CYCLE_DEGREES;
    if (angle < 0) {
        angle += (int32_t)ENGINE_CYCLE_DEGREES;
    }
    return (uint16_t)angle;
}
