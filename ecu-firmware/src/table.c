/*
 * table.c - see table.h. Lifted from fuel.c's own VE lookup, which was
 * host-verified against independently computed floating-point results
 * before it moved here.
 */
#include "table.h"

/* Interpolation fraction scale. A power of two so the divides below are
 * shifts, which matters in an ISR path on a 60 MHz core with no
 * hardware divider worth relying on. */
#define FRAC_ONE  256

/* Finds the axis cell containing `value` and how far into it we are,
 * scaled to 0..FRAC_ONE. Returns the lower index; `frac` gets the
 * position within that interval. At or past either end the fraction is
 * zero and the index is the end cell, so the caller's interpolation
 * naturally returns that cell's value with no special case. */
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

/* One step of linear interpolation between two cells. */
static int32_t lerp(int32_t a, int32_t b, int32_t frac) {
    return a + (((b - a) * frac) / FRAC_ONE);
}

int32_t table2d_lookup(const uint16_t *x_axis, uint8_t nx,
                       const uint16_t *y_axis, uint8_t ny,
                       const int16_t *cells,
                       uint16_t x, uint16_t y) {
    int32_t xf, yf;
    uint8_t xi = axis_lookup(x_axis, nx, x, &xf);
    uint8_t yi = axis_lookup(y_axis, ny, y, &yf);

    /* At the top of an axis the lookup returns the last index and a zero
     * fraction, so pairing that index with itself interpolates to the
     * same value - the clamp needs no separate branch here. */
    uint8_t xi2 = ((uint8_t)(xi + 1u) < nx) ? (uint8_t)(xi + 1u) : xi;
    uint8_t yi2 = ((uint8_t)(yi + 1u) < ny) ? (uint8_t)(yi + 1u) : yi;

    /* Bilinear: interpolate along x on both rows, then between those two
     * results along y. */
    int32_t low  = lerp(cells[(uint16_t)yi  * nx + xi],
                        cells[(uint16_t)yi  * nx + xi2], xf);
    int32_t high = lerp(cells[(uint16_t)yi2 * nx + xi],
                        cells[(uint16_t)yi2 * nx + xi2], xf);
    return lerp(low, high, yf);
}
