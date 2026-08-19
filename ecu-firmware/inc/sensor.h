/*
 * sensor.h - one table-driven analog sensor module.
 *
 * WHY THIS REPLACED PER-SENSOR DRIVERS. clt_sensor.{c,h} and
 * iat_sensor.{c,h} were 405 lines that differed in three things: a
 * pull-up value, a lookup table, and the prefix on every identifier.
 * Meanwhile eight other analog channels (TPS, OILP, FUELP, APP1/APP2,
 * TPS1/TPS2, VBATT) had no conversion at all and were still raw ADC
 * counts in the main loop - each of which would have wanted its own
 * file pair under that pattern. The pattern did not scale, so the
 * per-sensor part moved into config/engine.toml and what is left here
 * is three conversion kernels that read a descriptor table.
 *
 * Adding a sensor is now a config edit, not a new .c/.h pair.
 *
 * WHAT THE CONFIG BUYS BEYOND TIDINESS - the real reason to do this:
 *
 *   1. The divider stops being a magic number. Each channel names the
 *      real board resistors in front of it and the generator computes
 *      where full scale lands in ADC counts. That figure used to be
 *      typed in by hand and would silently go stale the moment the
 *      board changed.
 *
 *   2. The generator cross-checks those resistors against
 *      ecu-pcb/build_schematic.py, so the firmware and the board cannot
 *      disagree about the hardware without the build saying so.
 *
 *   3. It refuses any divider whose full scale sits above the ADC
 *      reference. That is not hypothetical: this board shipped eight
 *      5 V sensors into a 3.3 V ADC with no divider at all, which made
 *      MAP stop reading at ~73 kPa of its 105 kPa range. That specific
 *      mistake can no longer be described in the config.
 *
 *   4. One unit convention. The old drivers worked in tenths of a
 *      degree FAHRENHEIT while the EGT path worked in hundredths of a
 *      degree CELSIUS, which put a unit conversion in the middle of the
 *      fuel calculation. Everything here is centi-Celsius.
 *
 * WHAT IS DELIBERATELY NOT IN THE TABLE. EGT is a thermocouple behind
 * an SPI ADC needing cold-junction compensation and a NIST ITS-90
 * polynomial (ads1118.h); knock is a raw AC signal wanting a windowed
 * transform, not a scalar conversion. Forcing either in would make this
 * table lie about what it can do, so both stay bespoke.
 *
 * ALL INTEGER. No FPU is assumed anywhere in this firmware.
 */
#ifndef SENSOR_H
#define SENSOR_H

#include <stdint.h>
#include "sensor_defs.h"

/* Sentinel for a reading that could not be produced - an open circuit
 * on a thermistor channel, or an out-of-range curve lookup. Distinct
 * from any real value so a caller can tell "no reading" from "cold".
 * Callers that must have a number should substitute their own limp-home
 * default rather than treat this as one. */
#define SENSOR_INVALID  INT32_MIN

/* Converts a raw ADC count for `id` into that channel's engineering
 * unit, as declared in config/engine.toml:
 *
 *   linear      -> the configured unit (kPa, hundredths of a percent)
 *   voltage     -> millivolts at the divider input
 *   thermistor  -> hundredths of a degree C
 *
 * Out-of-range inputs clamp to the ends of the configured range rather
 * than extrapolating, for the same reason the VE table clamps: past the
 * calibrated range there is no data, only arithmetic. */
int32_t sensor_convert(sensor_id_t id, uint16_t raw_adc);

/* Thermistor resistance in ohms from a raw count, exposed because it is
 * genuinely useful for diagnosing a sensor or harness fault separately
 * from the temperature it implies. Returns 0 for a non-thermistor
 * channel, and UINT32_MAX for an open circuit (raw at full scale, where
 * the divider equation divides by zero) - a real sentinel, not a
 * wrapped value. */
uint32_t sensor_resistance_ohms(sensor_id_t id, uint16_t raw_adc);

/* The channel's declared name, for diagnostics and CAN telemetry. */
const char *sensor_name(sensor_id_t id);

#endif /* SENSOR_H */
