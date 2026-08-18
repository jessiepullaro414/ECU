/*
 * fuel.h - speed-density fuelling: how long to hold the injector open.
 *
 * THE CALCULATION. Work out the mass of air trapped in the cylinder,
 * divide by the target air/fuel ratio to get the mass of fuel it wants,
 * then divide by how fast the injector delivers fuel:
 *
 *   m_air  = (MAP * V_cyl * VE) / (R_air * T_intake)     [ideal gas law]
 *   m_fuel = m_air / AFR
 *   t_open = m_fuel / injector_flow
 *
 * Everything except VE is physics or a datasheet number. VE - volumetric
 * efficiency, the fraction of the cylinder that actually fills - is the
 * measured correction that makes the ideal-gas figure match what the
 * engine really inhales, and it is the whole reason a tuned map exists.
 * It varies with RPM and load, which is why VE_TABLE is two-dimensional.
 *
 * WHY SPEED-DENSITY. This board measures manifold pressure, not air
 * mass, so air mass has to be inferred. That is the standard approach
 * for a MAP-based ECU and it is why the intake air temperature sensor
 * matters as much as it does: air density is inversely proportional to
 * absolute temperature, so ignoring IAT means over-fuelling a hot engine
 * by several percent.
 *
 * ALL INTEGER MATHS. No FPU is assumed anywhere in this firmware. The
 * intermediate air mass is held in micrograms in a 64-bit value, because
 * the natural expression overflows 32 bits well before it reaches a
 * sensible answer - a real trap, checked rather than assumed.
 *
 * WHAT IS NOT HERE, deliberately:
 *   - Warm-up enrichment from CLT, acceleration enrichment from TPS rate
 *     of change, and closed-loop trim from the wideband O2 controllers.
 *     Each is real and each needs a running engine to calibrate, so
 *     inventing curves for them now would be guessing dressed as
 *     configuration.
 *   - Any battery-voltage correction of injector dead time. The board
 *     has the VBATT channel precisely for this; the single dead-time
 *     figure in config/engine.toml is a placeholder for that table.
 */
#ifndef FUEL_H
#define FUEL_H

#include <stdint.h>

/* Converts a raw MAP ADC count into kPa using the sensor's configured
 * linear transfer function. Clamps rather than extrapolating past the
 * calibrated ends - an out-of-range reading means a disconnected or
 * failed sensor, not a real pressure. */
uint16_t fuel_map_kpa_from_adc(uint16_t adc_counts);

/* Bilinear VE lookup, returning volumetric efficiency in percent.
 * Interpolates between the four surrounding table cells; clamps to the
 * edge values outside the axes, which is what should happen when the
 * engine is somewhere the map does not describe. */
uint8_t fuel_ve_lookup(uint16_t rpm, uint16_t map_kpa);

/* The whole calculation: injector open time in microseconds for one
 * cylinder, EXCLUDING dead time (injection_arm_cylinder() adds that, so
 * that every path benefits from it rather than each caller remembering).
 *
 * iat_centiC is intake air temperature in hundredths of a degree C -
 * the same unit clt_sensor.h/iat_sensor.h already work in. */
uint32_t fuel_pulse_width_us(uint16_t rpm, uint16_t map_kpa, int32_t iat_centiC);

#endif /* FUEL_H */
