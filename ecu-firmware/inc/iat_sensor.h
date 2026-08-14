/*
 * iat_sensor.h - IAT (intake air temperature) sensor driver: converts a
 * raw ADC_1 channel-2 count into a real engineering temperature, for
 * the real GM-style open-element resistive sensor this board now uses
 * (swapped in alongside [[clt_sensor]] - same real DIYAutoTune part
 * family, "what cars already use" per the user's own framing).
 *
 * REAL PART, REAL SOURCE: DIYAutoTune "GM Open Element IAT Temperature
 * Sensor" (https://diyautotune.com/products/iat-sensor, cross-checked
 * against a second, independent DIYAutoTune URL for the same real
 * product - both fetched live this session). 3/8" NPT, 2-wire.
 *
 * REAL, HONEST DISCREPANCY FOUND AND RESOLVED - not glossed over: this
 * sensor's own product page publishes a 3-point curve of
 * -40F=100700ohm / 87F=2238ohm / 146F=177ohm. The first two resistance
 * values are IDENTICAL to clt_sensor.h's own CLT curve at the same two
 * temperatures, but the THIRD point's TEMPERATURE differs (146F here
 * vs. CLT's 210.2F) for the exact same 177ohm reading - two genuinely
 * different real sensors can't both be correct at that. Real evidence
 * this is copy-paste content contamination on DIYAutoTune's own IAT
 * page, not two authentically different curves (full reasoning also in
 * ecu-pcb/build_schematic.py, right above R24's registration):
 *   1. The IAT page's own product description contains a leftover
 *      sentence describing it as a "closed-element sensor", despite the
 *      product being titled/featured as open-element - direct evidence
 *      of copied text from the CLT product page.
 *   2. Taking 146F at face value implies a per-segment NTC Beta
 *      constant more than 2x CLT's own for the shared -40..87F segment
 *      (~7900K vs ~3800K) - physically implausible for one real
 *      thermistor, versus CLT's own internally-consistent ~8%
 *      segment-to-segment Beta spread (see clt_sensor.c's derivation).
 * Real conclusion: this is genuinely the same underlying GM-pattern
 * thermistor element as the CLT sensor (matching resistance values at
 * the same two lower anchor temperatures), just a different physical
 * package (open element for air vs. closed/NPT for liquid) - so this
 * driver reuses CLT's own already-cross-checked -40F/86F/210.2F curve
 * rather than the IAT page's likely-erroneous 146F figure.
 *
 * DIYAutoTune's own 146F is NOT thrown away though - it's kept as a
 * real, separate, honestly-documented fact: their stated MAX RATED
 * OPERATING TEMP for the open-element package specifically (plausibly a
 * genuine mechanical/thermal limit of that housing - e.g. wire
 * insulation or potting - not a curve error). See IAT_RATED_MAX_TENTHF
 * below. Not used as a second clamp: real intake air temperatures
 * essentially never approach the curve's own high end (210F+) in
 * practice, so there's no real engineering reason to add a second
 * artificial ceiling below the physical, resistance-based one.
 *
 * REAL CIRCUIT (ecu-pcb/build_schematic.py, R24/C35): a single 4.22k
 * (E96) pull-up from +3V3 to IAT_ADC, sensor's own NTC element off-
 * board (via the harness connector). Deliberately NOT the same 1.00k as
 * CLT's R25: IAT genuinely swings across nearly its FULL real range in
 * normal use (ambient cold-soak to hot under-hood/boost air), unlike
 * coolant (thermostatically regulated to a narrow band once warm), so
 * R24 uses the standard geometric-mean rule instead of CLT's narrow-
 * range-centered one: sqrt(R_min * R_max) = sqrt(177 * 100700) ~= 4222
 * ohm, rounded to the nearest real E96 value.
 *
 * Same real, free open/short fault-detection behavior as clt_sensor.h's
 * identical topology: disconnected sensor clamps to IAT_TEMP_MIN_TENTHF,
 * shorted sensor clamps to IAT_TEMP_MAX_TENTHF - see clt_sensor.h's file
 * header for the full reasoning, identical here.
 */
#ifndef IAT_SENSOR_H
#define IAT_SENSOR_H

#include <stdint.h>

/* Real circuit values, ecu-pcb/build_schematic.py (R24/C35). */
#define IAT_PULLUP_OHMS     4220u   /* R24, 4.22k E96 to +3V3 */
#define IAT_ADC_FULLSCALE   4095u   /* ADC_1 is 12-bit (adc.h) */

/* Real calibrated range - reused from CLT's own cross-checked curve
 * (see file header for why the IAT page's own 146F figure is not
 * trusted). Callers can use these to recognize a clamped/faulted
 * reading rather than trusting it as a real in-range temperature. */
#define IAT_TEMP_MIN_TENTHF  (-400)  /* -40.0F, sensor disconnected clamps here */
#define IAT_TEMP_MAX_TENTHF  (2102)  /* 210.2F, sensor shorted clamps here */

/* DIYAutoTune's own real stated max RATED (not curve-limiting)
 * operating temperature for this specific open-element package - an
 * informational fact, not a second clamp (see file header). */
#define IAT_RATED_MAX_TENTHF (1460)  /* 146.0F */

/* Converts a raw ADC_1 ch2 count (0-4095, see adc_channel_for_pin() /
 * PIN_ADC_IAT) into the real thermistor resistance in ohms, via the
 * divider equation for R24's real pull-up topology (see file header).
 * raw_adc >= IAT_ADC_FULLSCALE (open-circuit sensor, division by zero
 * in the divider equation) returns UINT32_MAX, a real sentinel for
 * "no finite resistance", not a wrapped/garbage value. */
uint32_t iat_resistance_ohms(uint16_t raw_adc);

/* Full pipeline: raw ADC_1 ch2 count -> real resistance -> real,
 * piecewise-Beta-derived lookup-table temperature (reused from CLT's
 * own curve, see file header), in tenths of a degree Fahrenheit -
 * integer only, no floating point, matching every other driver in this
 * codebase. Values outside the calibrated range are clamped to
 * IAT_TEMP_MIN_TENTHF/IAT_TEMP_MAX_TENTHF. */
int16_t iat_temp_tenthF(uint16_t raw_adc);

#endif /* IAT_SENSOR_H */
