/*
 * table.h - bilinear lookup over an RPM x load breakpoint table.
 *
 * Both tuning tables in this firmware are the same shape - VE and spark
 * advance, each an RPM x MAP grid with its own axes - so the
 * interpolation lives here once rather than being copied per table. It
 * started life private to fuel.c; adding the spark table would have
 * made it a second copy, and a subtly different second copy is exactly
 * how two tables end up disagreeing about what "halfway between cells"
 * means.
 *
 * Cells are int16_t because spark advance can legitimately be negative
 * (retarded past TDC, which real engines do while cranking), and a
 * single signed cell type lets one implementation serve both tables.
 *
 * ALL INTEGER. No FPU is assumed anywhere in this firmware, and the
 * interpolation is signed throughout: the difference between
 * neighbouring cells goes negative wherever either curve falls off, and
 * doing that in unsigned arithmetic wraps on exactly the high-RPM cells
 * that matter most.
 *
 * Off the edge of either axis the lookup CLAMPS to the edge value
 * rather than extrapolating. Past the calibrated range there is no
 * data, only arithmetic - and an extrapolated VE runs the engine lean
 * while an extrapolated advance detonates it.
 */
#ifndef TABLE_H
#define TABLE_H

#include <stdint.h>

/* Interpolates `cells` at (x, y).
 *
 *   x_axis / nx  - column breakpoints, strictly ascending (RPM)
 *   y_axis / ny  - row breakpoints, strictly ascending (MAP)
 *   cells        - ny rows of nx entries, row-major: cells[row * nx + col]
 *
 * The generator guarantees the axes ascend and the dimensions match, so
 * this does not re-check them; it does clamp, which covers the case
 * where the engine is simply outside the mapped range. */
int32_t table2d_lookup(const uint16_t *x_axis, uint8_t nx,
                       const uint16_t *y_axis, uint8_t ny,
                       const int16_t *cells,
                       uint16_t x, uint16_t y);

#endif /* TABLE_H */
