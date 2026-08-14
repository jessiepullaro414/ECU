/*
 * siul2.c - real package-pin -> (PCR index, alternate function) table
 * for every pin this board actually uses, plus pinmux_init() to
 * configure all of them.
 *
 * Real, verified this session against the actual NXP MPC5606BK
 * Reference Manual Table 4-1 "Functional port pins" (pages 55-74) -
 * every row below was matched by real 144-LQFP package pin number
 * against that table's own PCR/alternate-function columns, extracted
 * via positioned-text coordinates (not the flat text extraction, which
 * shuffles this table's columns) and cross-checked against each pin's
 * expected real signal name already established in ecu_pins.h (itself
 * sourced from ../ecu-pcb/build_schematic.py's verified MCU_USED/
 * MCU_EMIOS dicts). Every one of the 66 real pins matched its expected
 * function name exactly once - no ambiguous or guessed entries here.
 * (62 from the original pin-mux pass; 4 more - the CAN0/1 TX/RX pins -
 * added once the FlexCAN driver pass needed them.)
 *
 * Real, confirmed port-to-PCR-block formula (empirically observed
 * across all 66 real entries, not assumed in advance): PCR index =
 * (port letter offset, A=0..G=6) * 16 + pin number within that port.
 * e.g. PC[4] = PCR[32 + 4] = PCR[36]. Only confirmed for ports A-G
 * (every port this board's real pins actually use) - not extended to
 * H without direct evidence.
 *
 * Four pins (MAP/TPS/IAT/CLT - PB[4:7], PCR[20:23]) are dedicated
 * analog-only pads (pad type "I" per Table 20-11) - confirmed directly
 * from Table 4-1 that AF0 on these carries no signal at all ("-"),
 * unlike every other pin's AF0=GPIO. They're configured via PCR's APC
 * bit only, no alternate-function selection.
 */
#include "ecu_pins.h"
#include "siul2.h"

typedef struct {
    uint16_t pin_number;   /* real 144-LQFP package pin, matches ecu_pins.h */
    uint8_t  pcr_index;    /* real SIUL2 PCR array index */
    uint8_t  af;           /* alternate function to select (PCR_AF()) - 0 for GPIO/analog-only */
    uint8_t  is_output;    /* 1 = this signal drives out of the MCU, 0 = input */
    uint8_t  is_analog;    /* 1 = dedicated analog pad (APC, no AF) */
} pinmux_entry_t;

/* clang-format off */
static const pinmux_entry_t PINMUX_TABLE[] = {
    /* pin  PCR   AF  out  analog   -- signal (see ecu_pins.h) */
    { 104,   7,   1,  1, 0 },  /* INJ1_CTRL  = PA[7]  AF1 E0UC[7]  */
    { 107,  10,   1,  1, 0 },  /* INJ2_CTRL  = PA[10] AF1 E0UC[10] */
    { 108,  11,   1,  1, 0 },  /* INJ3_CTRL  = PA[11] AF1 E0UC[11] */
    {  31,  16,   2,  1, 0 },  /* INJ4_CTRL  = PB[0]  AF2 E0UC[30] */
    {  32,  17,   2,  1, 0 },  /* INJ5_CTRL  = PB[1]  AF2 E0UC[31] */
    {  83,  28,   1,  1, 0 },  /* INJ6_CTRL  = PB[12] AF1 E0UC[4]  */
    {  85,  29,   1,  1, 0 },  /* INJ7_CTRL  = PB[13] AF1 E0UC[5]  */
    {  87,  30,   1,  1, 0 },  /* INJ8_CTRL  = PB[14] AF1 E0UC[6]  */
    { 143,  40,   2,  1, 0 },  /* IGN1_CTRL  = PC[8]  AF2 E0UC[3]  */
    { 141,  44,   1,  1, 0 },  /* IGN2_CTRL  = PC[12] AF1 E0UC[12] */
    { 142,  45,   1,  1, 0 },  /* IGN3_CTRL  = PC[13] AF1 E0UC[13] */
    {   3,  46,   1,  1, 0 },  /* IGN4_CTRL  = PC[14] AF1 E0UC[14] */
    {   4,  47,   1,  1, 0 },  /* IGN5_CTRL  = PC[15] AF1 E0UC[15] */
    {  36,  38,   2,  1, 0 },  /* IGN6_CTRL  = PC[6]  AF2 E1UC[28] */
    {  37,  39,   2,  1, 0 },  /* IGN7_CTRL  = PC[7]  AF2 E1UC[29] */
    { 131,  36,   1,  1, 0 },  /* IGN8_CTRL  = PC[4]  AF1 E1UC[31] */

    {  42,  14,   3,  0, 0 },  /* CRANK_CAPTURE = PA[14] AF3 E0UC[0]  */
    {  11,   1,   1,  0, 0 },  /* CAM1_CAPTURE  = PA[1]  AF1 E0UC[1]  */
    { 128,  66,   1,  0, 0 },  /* CAM2_CAPTURE  = PE[2]  AF1 E0UC[18] */

    { 129,  67,   1,  1, 0 },  /* VVT1_PWM  = PE[3] AF1 E0UC[19] */
    { 132,  68,   1,  1, 0 },  /* VVT2_PWM  = PE[4] AF1 E0UC[20] */
    { 133,  69,   1,  1, 0 },  /* IDLE_PWM  = PE[5] AF1 E0UC[21] */
    {  84,  61,   2,  1, 0 },  /* TACH_OUT  = PD[13] AF2 E0UC[25] */
    { 109,  76,   2,  1, 0 },  /* BOOST_PWM = PE[12] AF2 E1UC[19] */

    { 139,  70,   1,  1, 0 },  /* ETC_IN1 = PE[6] AF1 E0UC[22] */
    { 140,  71,   1,  1, 0 },  /* ETC_IN2 = PE[7] AF1 E0UC[23] */
    { 103,  77,   2,  0, 0 },  /* FLEXFUEL_CAPTURE = PE[13] AF2 E1UC[20] */

    /* Plain GPIO (AF0), direction per ecu_pins.h's real usage */
    {   5, 101,   0,  1, 0 },  /* RELAY_CTRL  = PG[5] */
    {   6, 100,   0,  1, 0 },  /* DRV_OUTEN   = PG[4] */
    {   9,   2,   0,  1, 0 },  /* SPI_CS_INJ0 = PA[2] */
    {  10,  64,   0,  1, 0 },  /* SPI_CS_INJ1 = PE[0] */
    {  12,  65,   0,  1, 0 },  /* HTR_CTRL    = PE[1] */
    {  13,  72,   0,  1, 0 },  /* SPI_CS_O2A  = PE[8] */
    {  14,  73,   0,  1, 0 },  /* CAN0_EN     = PE[9] */
    {  15,  74,   0,  1, 0 },  /* CAN0_STB_N  = PE[10] */
    {  16,   0,   0,  1, 0 },  /* CAN1_EN     = PA[0] */
    {  17,  75,   0,  1, 0 },  /* CAN1_STB_N  = PE[11] */

    /* CAN bus - real, visually confirmed (Table 4-1, page 61). RX pins
     * are the same "dedicated input, no AF slot" pattern as SPI_SIN
     * above (PC[11]/PC[3] each list CAN1RX/CAN4RX as an extra row
     * beyond AF0-3, not an AF value) - both just need AF0/GPIO + IBE. */
    {  28,  42,   1,  1, 0 },  /* CAN0_TX = PC[10] AF1 CAN1TX (FlexCAN_1) */
    {  27,  43,   0,  0, 0 },  /* CAN0_RX = PC[11] AF0 (input) - CAN1RX, not AF-selected */
    { 117,  34,   2,  1, 0 },  /* CAN1_TX = PC[2]  AF2 CAN4TX (FlexCAN_4) */
    { 116,  35,   0,  0, 0 },  /* CAN1_RX = PC[3]  AF0 (input) - CAN4RX, not AF-selected */

    {  25, 105,   0,  1, 0 },  /* ETC_D1      = PG[9] */
    {  26, 104,   0,  1, 0 },  /* ETC_D2      = PG[8] */
    {  29, 103,   0,  1, 0 },  /* ETC_EN      = PG[7] */
    {  30, 102,   0,  0, 0 },  /* ETC_SF_N    = PG[6] (input) */
    {  40,  15,   2,  1, 0 },  /* SPI_SCK     = PA[15] AF2 SCK_0  (DSPI_0) */
    {  44,  13,   1,  1, 0 },  /* SPI_SOUT    = PA[13] AF1 SOUT_0 (DSPI_0) */
    {  45,  12,   0,  0, 0 },  /* SPI_SIN     = PA[12] AF0 (input) - SIN_0 is a
                                 * dedicated input path on this pin, not an
                                 * AF-selected function (confirmed: Table 4-1's
                                 * own AF1 slot for PA[12] is blank/unused; SIN_0
                                 * is listed as an extra row alongside EIRQ[17],
                                 * same pattern - DSPI_0 reads this pin's input
                                 * buffer directly once IBE is set, regardless of
                                 * AF value) */
    {  67,  52,   0,  1, 0 },  /* FPUMP_CTRL  = PD[4] */
    {  86,  62,   0,  1, 0 },  /* SPI_CS_O2B  = PD[14] */
    {  88,  63,   0,  1, 0 },  /* HTR2_CTRL   = PD[15] */

    /* Dedicated analog pads (pad type I) - APC only, no AF selection.
     * ecu_pins.h's other ADC pins (VBATT/OILP/FUELP/KNOCK2/APP1/APP2/
     * TPS1/TPS2/EGT/ETC_IFB, all on Port D) showed a real GPIO[n]
     * AF0 in Table 4-1 (pad type S/M, not I) - they're ordinary
     * multiplexed pins with an analog capability layered on via APC,
     * same PCR mechanism, just also usable as GPIO unlike MAP/TPS/
     * IAT/CLT. All handled identically below (APC set, AF left at 0). */
    {  53,  24,   0,  0, 1 },  /* ADC_KNOCK1 = PB[8] */
    {  63,  48,   0,  0, 1 },  /* ADC_VBATT  = PD[0] */
    {  64,  49,   0,  0, 1 },  /* ADC_OILP   = PD[1] */
    {  65,  50,   0,  0, 1 },  /* ADC_FUELP  = PD[2] */
    {  66,  51,   0,  0, 1 },  /* ADC_KNOCK2 = PD[3] */
    {  68,  53,   0,  0, 1 },  /* ADC_APP1   = PD[5] */
    {  69,  54,   0,  0, 1 },  /* ADC_APP2   = PD[6] */
    {  70,  55,   0,  0, 1 },  /* ADC_TPS1   = PD[7] */
    {  71,  56,   0,  0, 1 },  /* ADC_TPS2   = PD[8] */
    {  72,  20,   0,  0, 1 },  /* ADC_MAP    = PB[4] (analog-only pad) */
    {  75,  21,   0,  0, 1 },  /* ADC_TPS    = PB[5] (analog-only pad) */
    {  76,  22,   0,  0, 1 },  /* ADC_IAT    = PB[6] (analog-only pad) */
    {  77,  23,   0,  0, 1 },  /* ADC_CLT    = PB[7] (analog-only pad) */
    /* PD[9] is no longer an analog input - EGT moved to the ADS1118-Q1
     * SPI ADC (ads1118.h), and this pin became that device's chip
     * select. Configured as a plain GPIO OUTPUT (OBE=1, APC=0), which
     * this same table's own note above confirms is valid: the Port D
     * analog pins are ordinary multiplexed pads with analog layered on
     * via APC, "just also usable as GPIO". Listed with the other chip
     * selects rather than here would be tidier, but it is kept in place
     * so the pin's history stays visible. */
    {  78,  57,   0,  1, 0 },  /* SPI_CS_EGT = PD[9] (was ADC_EGT) */
    {  79,  58,   0,  0, 1 },  /* ADC_ETC_IFB= PD[10] */
};
/* clang-format on */

#define PINMUX_COUNT (sizeof(PINMUX_TABLE) / sizeof(PINMUX_TABLE[0]))

void pinmux_init(void) {
    for (unsigned i = 0; i < PINMUX_COUNT; i++) {
        const pinmux_entry_t *e = &PINMUX_TABLE[i];
        uint16_t cfg = 0;
        if (e->is_analog) {
            cfg |= PCR_APC;   /* enable analog input path; AF stays 0 */
        } else {
            cfg |= PCR_AF(e->af);
            if (e->is_output) {
                cfg |= PCR_OBE;
            } else {
                cfg |= PCR_IBE;
            }
        }
        pcr_configure(e->pcr_index, cfg);
    }
    /* All three DSPI_0 pins are now real and resolved (were TODOs):
     * SPI_SCK (PA[15]) = AF2 SCK_0, SPI_SOUT (PA[13]) = AF1 SOUT_0, both
     * visually confirmed against Table 4-1 (Reference Manual page 57)
     * and now switched on above. SPI_SIN (PA[12]) turned out not to need
     * an AF switch at all - see its table comment above. The CAN0/1_EN
     * and CAN0/1_STB_N pins are correctly GPIO as-is - they drive an
     * external CAN transceiver's control pins, not the MCU's own
     * FlexCAN TX/RX signals, per ecu_pins.h's own real net names. */
}

uint8_t siul2_pcr_for_pin(uint16_t package_pin) {
    for (unsigned i = 0; i < PINMUX_COUNT; i++) {
        if (PINMUX_TABLE[i].pin_number == package_pin) {
            return PINMUX_TABLE[i].pcr_index;
        }
    }
    return 0xFFu;
}
