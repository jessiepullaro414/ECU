/*
 * clt_sensor.h - CLT (coolant temperature) sensor driver: converts a raw
 * ADC_1 channel-3 count into a real engineering temperature, for the
 * real GM-style closed-element resistive sending unit this board now
 * uses (swapped in to match [[thermo-pcb]]'s sibling engine-temperature
 * sensor - same real part family, "what cars already use" per the user's
 * own framing, rather than a generic/undocumented NTC placeholder).
 *
 * REAL PART, REAL SOURCE: DIYAutoTune "GM Closed Element CLT/Oil
 * Temperature Sensor" (https://diyautotune.com/products/clt-sensor,
 * fetched live this session - not from memory/recall). Real, closed-
 * element GM-style thermistor, 3/8" NPT, 2-wire (signal + ground,
 * isolated - no case-ground return needed). The manufacturer publishes
 * exactly THREE real calibration points, no more - this is the complete
 * real data available, not an incomplete excerpt:
 *     -40.0 F (233.15 K)  ->  100700 ohm
 *      86.0 F (303.15 K)  ->    2238 ohm
 *     210.2 F (372.15 K)  ->     177 ohm
 * Same exact part/curve as thermo-pcb's own engine-temperature sender
 * (see the thermo-pcb memory's "Real sensor swap" entry) - reused
 * verbatim, not re-derived, since it's genuinely the same real sensor.
 *
 * REAL CIRCUIT (ecu-pcb/build_schematic.py, R25/C36): a single 1.00k
 * (E96) pull-up from +3V3 to CLT_ADC, sensor's own NTC element off-board
 * (via the harness connector) completing the divider to GND, 100nF
 * smoothing to GND at the ADC node. R25=1.00k is not a generic/typical
 * value - it's the exact same real, deliberate sizing thermo-pcb's own
 * R12 uses for this exact sensor: chosen to center ADC resolution on
 * the sensor's real 86-210.2F ENGINE-OPERATING range (2238-177 ohm)
 * rather than the -40F cold-start extreme (100700 ohm), since the
 * engine spends effectively all its running life in the former, not the
 * latter. Pull-up rail is +3V3, not +5V, because this MCU's real ADC
 * reference IS +3V3 (VDD_HV_ADC1 - see ecu-pcb/build_schematic.py's own
 * "Power domains" note: this part has no separate VRH/VRL pins, the ADC
 * domain just IS the 3.3V VDD_HV rail) - the ORIGINAL generic "2.2k CLT
 * pull-up... typical" placeholder this replaced was wired to +5V, a
 * real latent over-voltage bug on a 3.3V-domain ADC pin at cold
 * temperatures (high NTC resistance pulls the node close to the full
 * rail) that this real redesign also fixes as a direct consequence, not
 * a separate, unrelated patch.
 *
 * Same real self-diagnosing fault behavior thermo-pcb's identical
 * topology already gets for free (no extra parts needed): sensor
 * disconnected -> node pulled to +3V3 through R25 -> reads as colder
 * than the coldest real calibration point (clamped, see
 * clt_temp_tenthF()'s own comment); sensor shorted -> node reads ~0V ->
 * reads as hotter than the hottest real calibration point (also
 * clamped). Both are real, impossible-in-normal-operation values a
 * caller can treat as a fault flag, not just a reading.
 *
 * REAL MATH, not a guessed table: only 3 real data points exist (see
 * above), so a single global NTC Beta constant across the whole -40 to
 * 210.2F span would be a real approximation error (checked: the
 * two segments' own local Betas differ by ~8%, see clt_sensor.c's own
 * derivation comment - a real NTC device genuinely isn't a perfect
 * single-exponential over a 250F span). Standard real automotive
 * practice (matching how production ECU firmware - e.g. MegaSquirt -
 * actually handles thermistor calibration) is a piecewise model: fit a
 * local Beta to each real calibrated segment (-40..86F and 86..210.2F),
 * each exact at its own two real endpoints, then precompute a
 * resistance/temperature lookup table from that piecewise model at
 * design time (done once, in clt_sensor.c's own derivation comment - not
 * computed at runtime, since this project's firmware uses no floating
 * point anywhere, matching every other driver here). Runtime is a plain
 * integer linear interpolation between adjacent real table rows -
 * standard, cheap, and exact at all 3 real manufacturer-published
 * anchor points (they're table rows, not just guide points).
 *
 * Real, honest limitation: the lookup table's non-anchor rows are
 * derived (via the local-Beta math above), not independently confirmed
 * against further real manufacturer data - the manufacturer's page
 * genuinely doesn't publish more than the 3 points above. Values
 * outside the calibrated range (below 177 ohm / above 100700 ohm) are
 * CLAMPED to the nearest real calibration point rather than
 * extrapolated - this project's own no-guessing discipline applies here
 * too: no real data exists past -40F or past 210.2F, so none is
 * fabricated.
 */
#ifndef CLT_SENSOR_H
#define CLT_SENSOR_H

#include <stdint.h>

/* Real circuit values, ecu-pcb/build_schematic.py (R25/C36). */
#define CLT_PULLUP_OHMS     1000u   /* R25, 1.00k E96 to +3V3 */
#define CLT_ADC_FULLSCALE   4095u   /* ADC_1 is 12-bit (adc.h) */

/* Real calibrated range (DIYAutoTune's own published 3 points - see
 * file header). Callers can use these to recognize a clamped/faulted
 * reading rather than trusting it as a real in-range temperature. */
#define CLT_TEMP_MIN_TENTHF  (-400)  /* -40.0F, sensor disconnected clamps here */
#define CLT_TEMP_MAX_TENTHF  (2102)  /* 210.2F, sensor shorted clamps here */

/* Converts a raw ADC_1 ch3 count (0-4095, see adc_channel_for_pin() /
 * PIN_ADC_CLT) into the real thermistor resistance in ohms, via the
 * divider equation for R25's real pull-up topology (see file header).
 * raw_adc >= CLT_ADC_FULLSCALE (open-circuit sensor, division by zero
 * in the divider equation) returns UINT32_MAX, a real sentinel for
 * "no finite resistance", not a wrapped/garbage value. */
uint32_t clt_resistance_ohms(uint16_t raw_adc);

/* Full pipeline: raw ADC_1 ch3 count -> real resistance -> real,
 * piecewise-Beta-derived lookup-table temperature, in tenths of a
 * degree Fahrenheit (e.g. 1800 = 180.0F) - integer only, no floating
 * point, matching every other driver in this codebase. Values outside
 * the sensor's real -40..210.2F calibrated range are clamped to
 * CLT_TEMP_MIN_TENTHF/CLT_TEMP_MAX_TENTHF (see file header for why this
 * doubles as free open/short fault detection). */
int16_t clt_temp_tenthF(uint16_t raw_adc);

#endif /* CLT_SENSOR_H */
