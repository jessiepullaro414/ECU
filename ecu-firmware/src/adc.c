/*
 * adc.c - see adc.h for what's verified vs. still open.
 */
#include "adc.h"
#include "ecu_pins.h"

void adc_init(uint32_t base) {
    /* Real bring-up: OWREN so a slow-to-be-read result doesn't block a
     * later conversion (Table 28-11), PWDN cleared (RESET DEFAULT is 1 -
     * the ADC is powered down until software says otherwise, Figure
     * 28-9's own reset value), MODE left at its real one-shot default
     * (0 - Table 28-11). */
    ADC_MCR(base) = ADC_MCR_OWREN;   /* PWDN=0, MODE=0, NSTART=0 */

    /* RESOLVED, a later pass (see adc.h file header for the full real
     * derivation, MPC5606B Data Sheet Rev. 5, Section 3.17): real
     * hardware settling delay, not a software busy-wait placeholder.
     * PDEDR's own real purpose ("Delay between the power-down bit reset
     * and the start of conversion") means the ADC itself now enforces
     * this real minimum before honoring the next conversion start - no
     * software wait is needed here anymore. */
    ADC_PDEDR(base) = ADC_PDED_MIN;
}

uint16_t adc_read_channel(uint32_t base, uint8_t channel) {
    /* Real NCMR-selection formula (see adc.h file header): channel n
     * lives in NCMR0 (n=0..15, bit n), NCMR1 (n=32..59, bit n-32), or
     * NCMR2 (n=64..95, bit n-64, ADC_0 only). Clear all three every
     * call so a previous channel's enable bit can't cause an extra,
     * unwanted conversion alongside this one. */
    ADC_NCMR0(base) = 0u;
    ADC_NCMR1(base) = 0u;
    ADC_NCMR2(base) = 0u;

    if (channel <= 15u) {
        ADC_NCMR0(base) = (1u << channel);
    } else if (channel >= 32u && channel <= 59u) {
        ADC_NCMR1(base) = (1u << (channel - 32u));
    } else if (channel >= 64u && channel <= 95u) {
        ADC_NCMR2(base) = (1u << (channel - 64u));
    } else {
        return 0u;   /* not a real channel number on this instance */
    }

    ADC_MCR(base) |= ADC_MCR_NSTART;   /* start one-shot conversion */

    /* Real, bounded wait-count timeout (same honest caveat as
     * clocks.h's CLOCKS_WAIT_ITERATIONS - not calibrated against a real
     * time unit, just a real, generous count at this board's confirmed
     * 60MHz core clock). A conversion that never completes returns 0,
     * same as an out-of-range channel above - a real, pre-existing
     * ambiguity (0 is also a legitimate real ADC count) this driver
     * doesn't resolve differently for a timeout; callers already need
     * to treat an unexpected 0 with suspicion. */
    uint32_t i;
    for (i = 0; i < 1000000u; i++) {
        if ((ADC_CDR(base, channel) & ADC_CDR_VALID) != 0u) {
            return (uint16_t)(ADC_CDR(base, channel) & ADC_CDR_CDATA_MASK);
        }
    }
    return 0u;   /* real timeout - conversion never completed */
}

typedef struct {
    uint16_t pin_number;
    uint8_t  channel;   /* real ADC_1 channel number - all real 0-95 */
} adc_channel_entry_t;

/* Real, verified this session against Figure 28-1's own block diagram
 * (which states the channel-number formula explicitly: ADCx_P[n] =
 * channel n, ADCx_S[n] = channel 32+n) and cross-checked pin-by-pin
 * against Table 4-1 (pages 58-63). All real sensor pins here are P[n]
 * (precision) channels wired identically to BOTH ADC_0 and ADC_1 - this
 * driver always picks ADC_1 (12-bit) for the free extra resolution -
 * EXCEPT PIN_ADC_KNOCK1, whose physical pad (PB[8]) has no P[n] mapping
 * at all, only a Standard-range one: Table 4-1 shows ADC0_S[0] (channel
 * 32) AND ADC1_S[4] (channel 36) on the SAME pin - a real, confirmed
 * case where the two instances' channel numbers for one physical pin
 * are NOT the same (unlike every P[n] pin below), so this is not a
 * copy/paste of the ADC_0 number. */
static const adc_channel_entry_t ADC_CHANNEL_TABLE[] = {
    { PIN_ADC_MAP,      0u },   /* PB[4] = ADC1_P[0] */
    { PIN_ADC_TPS,      1u },   /* PB[5] = ADC1_P[1] */
    { PIN_ADC_IAT,      2u },   /* PB[6] = ADC1_P[2] */
    { PIN_ADC_CLT,      3u },   /* PB[7] = ADC1_P[3] */
    { PIN_ADC_VBATT,    4u },   /* PD[0] = ADC1_P[4] */
    { PIN_ADC_OILP,     5u },   /* PD[1] = ADC1_P[5] */
    { PIN_ADC_FUELP,    6u },   /* PD[2] = ADC1_P[6] */
    { PIN_ADC_KNOCK2,   7u },   /* PD[3] = ADC1_P[7] */
    { PIN_ADC_APP1,     9u },   /* PD[5] = ADC1_P[9] */
    { PIN_ADC_APP2,    10u },   /* PD[6] = ADC1_P[10] */
    { PIN_ADC_TPS1,    11u },   /* PD[7] = ADC1_P[11] */
    { PIN_ADC_TPS2,    12u },   /* PD[8] = ADC1_P[12] */
    /* PIN_ADC_EGT (PD[9] = ADC1_P[13]) deliberately removed: EGT is no
     * longer an analog channel at all. It moved to the ADS1118-Q1 SPI
     * ADC (ads1118.h) so the board could use a real AEC-Q100 part, and
     * that pin is now the new device's chip select. */
    { PIN_ADC_ETC_IFB, 14u },   /* PD[10] = ADC1_P[14] */
    { PIN_ADC_KNOCK1,  36u },   /* PB[8] = ADC1_S[4] = channel 32+4 (real, NOT a P[n] pin) */
};

#define ADC_CHANNEL_COUNT (sizeof(ADC_CHANNEL_TABLE) / sizeof(ADC_CHANNEL_TABLE[0]))

int adc_channel_for_pin(uint16_t package_pin, uint32_t *base_out, uint8_t *channel_out) {
    for (unsigned i = 0; i < ADC_CHANNEL_COUNT; i++) {
        if (ADC_CHANNEL_TABLE[i].pin_number == package_pin) {
            *base_out = ADC_1_BASE;
            *channel_out = ADC_CHANNEL_TABLE[i].channel;
            return 1;
        }
    }
    return 0;
}
