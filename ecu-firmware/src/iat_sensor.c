/*
 * iat_sensor.c - see iat_sensor.h for the full real derivation, source
 * citations, and the honest discrepancy investigation that led to this
 * driver reusing CLT's own curve rather than the IAT page's own
 * (likely-erroneous) 146F figure.
 *
 * The lookup table below is the SAME real data as clt_sensor.c's own
 * (identical resistance/temperature pairs - see that file's derivation
 * comment for the full Beta-fit math) - kept as IAT's own copy rather
 * than a shared symbol, on the same "duplication over premature shared
 * abstraction" basis used elsewhere in this codebase: these are two
 * independent real sensors/packages that could legitimately diverge if
 * better IAT-specific data ever surfaces, even though today's best
 * available real evidence says they're electrically identical.
 */
#include "iat_sensor.h"

typedef struct {
    uint32_t resistance_ohms;
    int16_t  temp_tenthF;
} iat_lut_entry_t;

/* Sorted by resistance DESCENDING (== temperature ascending). Rows
 * marked REAL ANCHOR are DIYAutoTune's own exact published data (three
 * points, shared with the CLT sensor - see file header); every other
 * row is derived via clt_sensor.c's own Beta math, not separately
 * sourced. */
static const iat_lut_entry_t IAT_LUT[] = {
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
#define IAT_LUT_COUNT (sizeof(IAT_LUT) / sizeof(IAT_LUT[0]))

uint32_t iat_resistance_ohms(uint16_t raw_adc) {
    if (raw_adc >= IAT_ADC_FULLSCALE) {
        return 0xFFFFFFFFu;   /* real sentinel: divider equation divides by zero here (open sensor) */
    }
    /* raw_adc = FULLSCALE * Rntc/(Rntc+Rpullup)
     *   => Rntc = Rpullup * raw_adc / (FULLSCALE - raw_adc) */
    return ((uint32_t)IAT_PULLUP_OHMS * (uint32_t)raw_adc)
         / ((uint32_t)IAT_ADC_FULLSCALE - (uint32_t)raw_adc);
}

int16_t iat_temp_tenthF(uint16_t raw_adc) {
    uint32_t r = iat_resistance_ohms(raw_adc);

    if (r >= IAT_LUT[0].resistance_ohms) {
        return IAT_TEMP_MIN_TENTHF;
    }
    if (r <= IAT_LUT[IAT_LUT_COUNT - 1].resistance_ohms) {
        return IAT_TEMP_MAX_TENTHF;
    }

    /* Real, plain integer linear interpolation between the two
     * bracketing table rows - resistance is monotonically decreasing
     * as the index increases, so walk until r falls between row i-1
     * (higher R, colder) and row i (lower R, hotter). */
    unsigned i;
    for (i = 1; i < IAT_LUT_COUNT; i++) {
        if (r >= IAT_LUT[i].resistance_ohms) {
            uint32_t r_hi = IAT_LUT[i - 1].resistance_ohms;   /* colder, higher R */
            uint32_t r_lo = IAT_LUT[i].resistance_ohms;       /* hotter, lower R */
            int32_t  t_hi = IAT_LUT[i - 1].temp_tenthF;
            int32_t  t_lo = IAT_LUT[i].temp_tenthF;

            int32_t span_r = (int32_t)(r_hi - r_lo);
            int32_t span_t = t_lo - t_hi;
            int32_t off_r  = (int32_t)(r_hi - r);

            return (int16_t)(t_hi + (span_t * off_r) / span_r);
        }
    }

    return IAT_TEMP_MAX_TENTHF;   /* unreachable given the clamps above, real defensive fallback */
}
