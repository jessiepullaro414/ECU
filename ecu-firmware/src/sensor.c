/*
 * sensor.c - the three conversion kernels. See sensor.h for why this
 * replaced the per-sensor drivers.
 */
#include "sensor.h"

/* Guards every public entry point. The enum makes an out-of-range id
 * hard to produce, but this module is reached from the main loop with
 * values that ultimately came from configuration, and reading past the
 * end of a const table would give a plausible-looking wrong number
 * rather than an obvious failure. */
static const sensor_def_t *def_for(sensor_id_t id) {
    if ((int)id < 0 || (int)id >= (int)SENSOR_COUNT) {
        return 0;
    }
    return &SENSOR_DEFS[id];
}

const char *sensor_name(sensor_id_t id) {
    const sensor_def_t *d = def_for(id);
    return d ? d->name : "?";
}

uint32_t sensor_resistance_ohms(sensor_id_t id, uint16_t raw_adc) {
    const sensor_def_t *d = def_for(id);
    if (d == 0 || d->kind != SENSOR_KIND_THERMISTOR) {
        return 0u;
    }
    /* The NTC element is the BOTTOM half of a divider against a fixed
     * pull-up to the same rail the ADC references, so the rail cancels
     * out entirely and this is a pure ratio of counts:
     *
     *   V_pin = Vrail * Rntc / (Rntc + Rpu)
     *   raw   = MAX * Rntc / (Rntc + Rpu)
     *   Rntc  = Rpu * raw / (MAX - raw)
     *
     * That cancellation is why a thermistor channel needs no divider
     * entry and no reference voltage in its descriptor. */
    if (raw_adc >= ADC_MAX_COUNTS) {
        return UINT32_MAX;      /* open circuit: no finite resistance */
    }
    return (d->pullup_ohms * (uint32_t)raw_adc)
         / (ADC_MAX_COUNTS - (uint32_t)raw_adc);
}

/* Walks a curve sorted by DESCENDING resistance (ascending temperature)
 * and interpolates linearly between the two bracketing points.
 *
 * Linear-in-resistance rather than in log(R) is deliberate: a real
 * log/Beta interpolation is more faithful to the physics between two
 * widely-spaced points, but the shipped curve is dense enough that the
 * difference is small, and it would need either floating point or a log
 * table. The curve's own points came from Beta math in the first place
 * (see config/engine.toml), so the accuracy lives in the point spacing,
 * not in the interpolator. */
static int32_t curve_lookup(const curve_point_t *curve, uint8_t count,
                            uint32_t ohms) {
    if (count == 0u) {
        return SENSOR_INVALID;
    }
    /* Off either end, hold the end value. Colder than the curve's first
     * point or hotter than its last means the sensor is outside the
     * range anyone characterised, and inventing a number past that is
     * how a disconnected sensor becomes a confident wrong reading. */
    if (ohms >= curve[0].ohms) {
        return curve[0].centi_c;
    }
    if (ohms <= curve[count - 1u].ohms) {
        return curve[count - 1u].centi_c;
    }
    uint8_t i = 0u;
    while ((i + 1u) < count && ohms <= curve[i + 1u].ohms) {
        i++;
    }
    /* curve[i].ohms > ohms > curve[i+1].ohms, and temperature rises as
     * resistance falls, so the fraction runs from the high-resistance
     * end toward the low. */
    int32_t r_hi = (int32_t)curve[i].ohms;
    int32_t r_lo = (int32_t)curve[i + 1u].ohms;
    int32_t t_lo = curve[i].centi_c;
    int32_t t_hi = curve[i + 1u].centi_c;
    int32_t span = r_hi - r_lo;
    if (span <= 0) {
        return t_lo;            /* generator forbids this; belt and braces */
    }
    return t_lo + (((t_hi - t_lo) * (r_hi - (int32_t)ohms)) / span);
}

int32_t sensor_convert(sensor_id_t id, uint16_t raw_adc) {
    const sensor_def_t *d = def_for(id);
    if (d == 0) {
        return SENSOR_INVALID;
    }

    if (d->kind == SENSOR_KIND_THERMISTOR) {
        uint32_t ohms = sensor_resistance_ohms(id, raw_adc);
        if (ohms == UINT32_MAX) {
            return SENSOR_INVALID;      /* open circuit */
        }
        return curve_lookup(d->curve, d->curve_count, ohms);
    }

    /* linear and voltage share this path; the difference is only where
     * the band came from. */

    /* OUT OF BAND IS A FAULT, NOT A READING. A working sensor never
     * leaves its signal band by more than tolerance, so anything past
     * the fault thresholds is a broken wire, a short to ground, or a
     * short to the supply. This is the whole reason a ratiometric
     * sensor is specified to swing 0.5-4.5 V rather than rail to rail -
     * the dead zones at each end exist so those failures are
     * distinguishable from a legitimate minimum or maximum. Before the
     * signal band was described, 0 V read as a valid lowest value and a
     * severed MAP wire looked exactly like a healthy 10 kPa. */
    if (raw_adc < d->fault_lo || raw_adc > d->fault_hi) {
        return SENSOR_INVALID;
    }

    int32_t lo = (int32_t)d->counts_lo;
    int32_t hi = (int32_t)d->counts_hi;
    int32_t c  = (int32_t)raw_adc;

    /* Clamp into the band. lo may be GREATER than hi: an inverted
     * sensor falls as the measured quantity rises, which is a real
     * pattern and the strongest form of redundancy for a second
     * channel, so the ordering cannot be assumed either way. */
    if (lo <= hi) {
        if (c < lo) { c = lo; } else if (c > hi) { c = hi; }
    } else {
        if (c > lo) { c = lo; } else if (c < hi) { c = hi; }
    }

    int32_t span = hi - lo;             /* negative when inverted */
    if (span == 0) {
        return SENSOR_INVALID;          /* generator forbids it */
    }
    /* 64-bit intermediate: at_hi reaches 25740 for the battery channel
     * and the count difference reaches 4095, which fits 32 bits, but a
     * channel declared over a wider range would not - and it costs
     * nothing here. */
    return d->at_lo + (int32_t)(((int64_t)(d->at_hi - d->at_lo)
                                 * (int64_t)(c - lo)) / (int64_t)span);
}
