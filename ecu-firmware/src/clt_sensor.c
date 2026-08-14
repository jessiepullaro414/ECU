/*
 * clt_sensor.c - see clt_sensor.h for the full real derivation and
 * source citations. This file's own additions: the precomputed lookup
 * table and its exact derivation math (shown here, not hidden), plus
 * the two real conversion functions.
 *
 * LUT derivation (done once, at design time - this firmware has no
 * floating point anywhere, matching every other driver in this
 * codebase, so this math is NOT repeated at runtime):
 *
 * Real NTC single-exponential equation, R(T) = R_ref * exp(B*(1/T -
 * 1/T_ref)), fit LOCALLY per calibrated segment (piecewise, not one
 * global Beta - see clt_sensor.h's file header for why: the two
 * segments' own local Betas come out ~8% apart, a real sign a single
 * global Beta would NOT be exact at all 3 real manufacturer points).
 * Using DIYAutoTune's own real 3 points (-40F/100700ohm, 86F/2238ohm,
 * 210.2F/177ohm; F->K via K = (F-32)*5/9 + 273.15):
 *   T0 = 233.15K, T1 = 303.15K, T2 = 372.15K
 *   Segment 1 (T0..T1), Beta = ln(R0/R1) / (1/T0 - 1/T1)
 *            = ln(100700/2238) / (1/233.15 - 1/303.15)
 *            = 3.8066 / 0.0009903 ~= 3844 K
 *   Segment 2 (T1..T2), Beta = ln(R1/R2) / (1/T1 - 1/T2)
 *            = ln(2238/177) / (1/303.15 - 1/372.15)
 *            = 2.5372 / 0.0006116 ~= 4148 K
 * Each segment's own Beta, applied back through the same R(T) equation
 * anchored at (T1, R1=2238 ohm), reproduces the table below (rounded to
 * the nearest real ohm - these are derived/interpolated values, not
 * independently re-confirmed against further manufacturer data past
 * the 3 real anchor points, which are marked below).
 */
#include "clt_sensor.h"

typedef struct {
    uint32_t resistance_ohms;
    int16_t  temp_tenthF;
} clt_lut_entry_t;

/* Sorted by resistance DESCENDING (== temperature ascending). Rows
 * marked REAL ANCHOR are DIYAutoTune's own exact published data
 * (clt_sensor.h file header); every other row is derived via this
 * file's own Beta math above, not separately sourced. */
static const clt_lut_entry_t CLT_LUT[] = {
    { 100700u, -400 },   /* REAL ANCHOR: -40.0F */
    {  47600u, -200 },
    {  24000u,    0 },
    {  12800u,  200 },
    {   7190u,  400 },
    {   4220u,  600 },
    {   2238u,  860 },   /* REAL ANCHOR: 86.0F */
    {   1590u, 1000 },
    {   1000u, 1200 },
    {    653u, 1400 },
    {    437u, 1600 },
    {    300u, 1800 },
    {    210u, 2000 },
    {    177u, 2102 },   /* REAL ANCHOR: 210.2F */
};
#define CLT_LUT_COUNT (sizeof(CLT_LUT) / sizeof(CLT_LUT[0]))

uint32_t clt_resistance_ohms(uint16_t raw_adc) {
    if (raw_adc >= CLT_ADC_FULLSCALE) {
        return 0xFFFFFFFFu;   /* real sentinel: divider equation divides by zero here (open sensor) */
    }
    /* raw_adc = FULLSCALE * Rntc/(Rntc+Rpullup)
     *   => Rntc = Rpullup * raw_adc / (FULLSCALE - raw_adc) */
    return ((uint32_t)CLT_PULLUP_OHMS * (uint32_t)raw_adc)
         / ((uint32_t)CLT_ADC_FULLSCALE - (uint32_t)raw_adc);
}

int16_t clt_temp_tenthF(uint16_t raw_adc) {
    uint32_t r = clt_resistance_ohms(raw_adc);

    /* Real clamp at both ends of the calibrated range - see
     * clt_sensor.h's file header for why this also functions as free
     * open/short fault detection, and why this project's no-guessing
     * discipline forbids extrapolating a curve past the manufacturer's
     * real 3 published points. */
    if (r >= CLT_LUT[0].resistance_ohms) {
        return CLT_TEMP_MIN_TENTHF;
    }
    if (r <= CLT_LUT[CLT_LUT_COUNT - 1].resistance_ohms) {
        return CLT_TEMP_MAX_TENTHF;
    }

    /* Real, plain integer linear interpolation between the two
     * bracketing table rows - resistance is monotonically decreasing
     * as the index increases, so walk until r falls between row i-1
     * (higher R, colder) and row i (lower R, hotter). */
    unsigned i;
    for (i = 1; i < CLT_LUT_COUNT; i++) {
        if (r >= CLT_LUT[i].resistance_ohms) {
            uint32_t r_hi = CLT_LUT[i - 1].resistance_ohms;   /* colder, higher R */
            uint32_t r_lo = CLT_LUT[i].resistance_ohms;       /* hotter, lower R */
            int32_t  t_hi = CLT_LUT[i - 1].temp_tenthF;
            int32_t  t_lo = CLT_LUT[i].temp_tenthF;

            int32_t span_r = (int32_t)(r_hi - r_lo);
            int32_t span_t = t_lo - t_hi;
            int32_t off_r  = (int32_t)(r_hi - r);

            return (int16_t)(t_hi + (span_t * off_r) / span_r);
        }
    }

    return CLT_TEMP_MAX_TENTHF;   /* unreachable given the clamps above, real defensive fallback */
}
