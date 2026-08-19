/*
 * ignition.h - spark advance: how far before top dead centre to fire.
 *
 * WHY THIS EXISTS. Before this file, every cylinder fired exactly AT
 * TDC - injection.c took the cylinder's nominal TDC angle straight from
 * the firing order and never applied any advance. Combustion takes real
 * time, so the charge has to be lit before TDC for peak cylinder
 * pressure to arrive just after it, where it can actually push. Firing
 * at TDC makes no useful power and pushes the engine toward detonation.
 *
 * The advance table lives in config/engine.toml on its own RPM x MAP
 * axes - independent of the VE table, because fuel and spark are tuned
 * against different breakpoints in every real ECU - and is interpolated
 * by the same table2d_lookup() the VE table uses (table.h).
 *
 * WHAT IS DELIBERATELY NOT HERE, and each needs a running engine:
 *   - Knock retard. This board has two real knock sensor channels and
 *     the whole point of them is pulling advance when detonation is
 *     detected. That needs a windowed transform synchronised to the
 *     crank, not a scalar reading, and a threshold characterised on the
 *     actual engine.
 *   - Coolant-based advance correction (a cold engine tolerates more).
 *   - Idle stabilisation, where advance is trimmed against RPM error to
 *     hold a target idle.
 * Inventing curves for any of these now would be guessing dressed as
 * configuration.
 */
#ifndef IGNITION_H
#define IGNITION_H

#include <stdint.h>

/* Spark advance in whole crank degrees BEFORE top dead centre, for the
 * current operating point. Clamps to the table's edge values outside
 * the mapped range rather than extrapolating - an extrapolated advance
 * is how pistons get holed.
 *
 * Can legitimately return a negative value (retarded past TDC) if the
 * table is configured that way, which real engines do while cranking. */
int16_t ignition_advance_deg(uint16_t rpm, uint16_t map_kpa);

/* The crank angle at which a cylinder's spark should occur, given that
 * cylinder's own TDC angle within the engine cycle. This is the value
 * injection.c needs in order to schedule the event, and it exists as
 * its own function because the wrap is easy to get wrong: subtracting
 * advance from a TDC near zero must roll back around the cycle, not go
 * negative.
 *
 * Both angles are in crank degrees, 0..ENGINE_CYCLE_DEGREES-1. */
uint16_t ignition_spark_angle(uint16_t tdc_angle_deg, int16_t advance_deg);

#endif /* IGNITION_H */
