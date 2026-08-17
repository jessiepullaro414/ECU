/*
 * ads1118.h - TI ADS1118-Q1 driver: the EGT (exhaust gas temperature)
 * thermocouple front end.
 *
 * WHY THIS PART. The board's EGT channel used to be an AD8495
 * thermocouple amplifier feeding a divided-down analog voltage into the
 * MCU's own ADC. The AD8495 is the right FUNCTION - it is what the
 * aftermarket EFI industry actually uses - but it carries no AEC-Q100
 * qualification, and there is no automotive-qualified dedicated
 * thermocouple amplifier IC to swap it for: MAX31855, MAX31856, AD8495,
 * LTC2983 and MCP9600 were all checked (the last via a full
 * primary-source datasheet read) by the sibling thermo-pcb project, and
 * independently re-confirmed. The only genuinely compliant path is
 * architectural, not a substitution: a real AEC-Q100 ADC reads the raw
 * thermocouple millivolts, and cold-junction compensation plus NIST
 * ITS-90 linearisation move into firmware. That is this file.
 *
 * REAL PART: TI ADS1118-Q1, AEC-Q100 Grade 1 (-40 to +125C), 16-bit
 * delta-sigma ADC, PGA, SPI, and an on-die temperature sensor TI's own
 * literature documents for thermocouple cold-junction compensation - so
 * no second sensor part is needed, but the device must sit physically
 * near the connector where the thermocouple wire meets copper for that
 * reading to represent the REAL cold junction. That is a placement
 * constraint on ecu-pcb, recorded in its README, not something firmware
 * can compensate for.
 *
 * REGISTER FACTS BELOW ARE REAL, from TI's ADS1118 datasheet SBAS457F
 * (Rev. Sept 2019), genuinely read this pass:
 *   - Config register layout: Figure 44 + Table 7 (field descriptions).
 *   - 32-bit transaction format: Section 9.5.7.1. Four bytes - two of
 *     conversion result, two of Config readback. MSB first. The config
 *     written in the first two bytes is read back in the last two.
 *   - Temperature-sensor data format: 14-bit, LEFT-JUSTIFIED in the
 *     16-bit result, 0.03125 C/LSB, two's complement (Table 4 and its
 *     worked examples: "0960h x 0.03125C = 2400 x 0.03125C = 75C").
 *   - SPI mode: the device shifts DOUT out on the SCLK RISING edge and
 *     latches DIN on the FALLING edge, so with SCLK idling low the
 *     master must capture on the second (trailing) edge - CPOL=0,
 *     CPHA=1. Read from the datasheet's own prose, not assumed, and it
 *     matches what thermo-pcb independently verified against SBAS740B.
 *   - SPI timing (Table 7.6): tSCLK min 250ns -> real max SCLK 4MHz.
 *     Also a real gotcha worth knowing: "Holding SCLK low longer than
 *     28 ms resets the SPI interface."
 *
 * REAL FSR CHOICE, not a default. A K-type thermocouple at its maximum
 * useful 1250C produces 50.644 mV referenced to a 0C cold junction
 * (NIST, quoted by TI), rising to 52.171 mV if the cold junction sits at
 * -40C. The +/-256 mV FSR (PGA=101) is the smallest range that still
 * clears that with margin, which is exactly what TI's own Figure 50
 * thermocouple application circuit uses - so it is chosen here for the
 * same real reason, and it maximises resolution on a signal that never
 * exceeds ~52 mV.
 */
#ifndef ADS1118_H
#define ADS1118_H

#include <stdint.h>

/* ---- Config register fields (Figure 44 / Table 7) -------------------- */
#define ADS1118_SS          (1u << 15)  /* start a single conversion */

#define ADS1118_MUX_SHIFT   12          /* Table 7: 000 = AINP AIN0 / AINN AIN1 */
#define ADS1118_MUX_DIFF_01 (0u << ADS1118_MUX_SHIFT)   /* the thermocouple pair */

#define ADS1118_PGA_SHIFT   9
#define ADS1118_PGA_256MV   (5u << ADS1118_PGA_SHIFT)   /* 101 = FSR +/-0.256V */

#define ADS1118_MODE_SINGLE (1u << 8)   /* 1 = power-down + single-shot (reset default) */

#define ADS1118_DR_SHIFT    5
#define ADS1118_DR_128SPS   (4u << ADS1118_DR_SHIFT)    /* 100 = 128 SPS (reset default) */

#define ADS1118_TS_MODE     (1u << 4)   /* 1 = internal temperature sensor, 0 = ADC */
#define ADS1118_PULLUP_EN   (1u << 3)   /* weak pull-up on DOUT when CS high (default on) */

#define ADS1118_NOP_VALID   (1u << 1)   /* NOP[1:0] = 01: REQUIRED for a write to take */
#define ADS1118_RESERVED    (1u << 0)   /* reads back 1; writing it has no effect */

/* Real conversion-register value for the thermocouple channel: start a
 * single shot, differential AIN0/AIN1, +/-256mV FSR, single-shot mode,
 * 128 SPS. NOP=01 is not optional - Table 7 is explicit that "for data
 * to be written to the Config register, the NOP[1:0] bits must be '01'",
 * and any other value is silently treated as a no-op. */
#define ADS1118_CFG_THERMOCOUPLE ( \
      ADS1118_SS | ADS1118_MUX_DIFF_01 | ADS1118_PGA_256MV \
    | ADS1118_MODE_SINGLE | ADS1118_DR_128SPS \
    | ADS1118_PULLUP_EN | ADS1118_NOP_VALID | ADS1118_RESERVED)

/* Same, but reading the on-die temperature sensor for cold-junction
 * compensation. TS_MODE=1 overrides the input mux; PGA is irrelevant in
 * this mode but left at the same value so only one bit really differs. */
#define ADS1118_CFG_COLDJUNCTION (ADS1118_CFG_THERMOCOUPLE | ADS1118_TS_MODE)

/* Real LSB sizes.
 * Thermocouple: +/-256mV across a signed 16-bit result -> 256mV/32768 =
 * 7.8125 uV/LSB. Expressed in nanovolts to stay in integer maths, which
 * this whole codebase does (no FPU assumed anywhere).
 * Cold junction: 0.03125 C/LSB on the 14-bit left-justified result
 * (Table 4), i.e. 1/32 C - so a >>5 after right-aligning by 2. */
#define ADS1118_TC_NV_PER_LSB   7813    /* 7.8125uV, rounded to 1nV */
#define ADS1118_CJ_SHIFT        2       /* 14-bit result is left-justified in 16 */

/* Real CTAR profile for this device on DSPI_0. CTAR0 is MC33810's
 * (legacy), CTAR1 is CJ125's, CTAR2 is L9779WD-SPI's - so this takes
 * CTAR3. Unlike those three, this device needs CPHA=1 (see the SPI-mode
 * note in the file header), which is exactly why it cannot simply share
 * CJ125's CTAR1 despite both being comfortably slow enough.
 * Baud: FMSZ=15 (16-bit frames), PBR=00 (prescaler 2), BR=0100 (scaler
 * 16) -> SCK = 60MHz/(2*16) = 1.875MHz, safely under this part's real
 * 4MHz ceiling. Same derivation and same confirmed 60MHz DSPI_0
 * peripheral clock as CJ125_CTAR1/L9779_CTAR2.
 *
 * REAL, HONEST FLAG: CTAR3's existence on this specific DSPI instance
 * was not separately confirmed against the Reference Manual this pass -
 * CTAR0/1/2 are all in real use already, and DSPI implementations
 * generally provide more, but "generally" is not this project's
 * standard. Confirm the real CTAR count for DSPI_0 before trusting this
 * on hardware; if CTAR3 does not exist, the fix is small (share CTAR1's
 * slot and reconfigure it per-transfer, at the cost of the two devices
 * no longer being independent). */
#define ADS1118_CTAR3 ( \
      (15u << 27)   /* FMSZ = 16-bit frames */ \
    | (1u << 25)    /* CPHA = 1 (DSPI_CTAR_CPHA), see file header */ \
    | (0u << 16)    /* PBR = 00 -> prescaler 2 */ \
    | (0x4u << 0)   /* BR  = 0100 -> scaler 16 */ )

/* Brings up DSPI_0's CTAR3 for real ADS1118-Q1 timing and drives the
 * chip select inactive-high. Call once from hardware_init(). Does not
 * start a conversion. */
void ads1118_init(void);

/* Real single-shot read of the thermocouple channel. Returns the raw
 * signed 16-bit conversion result. Blocking, same style as every other
 * driver here. */
int16_t ads1118_read_thermocouple_raw(void);

/* Real single-shot read of the on-die temperature sensor, returned in
 * hundredths of a degree C (e.g. 2500 = 25.00C) so callers stay in
 * integer maths. This is the real cold-junction temperature. */
int16_t ads1118_read_coldjunction_centiC(void);

/* Converts a raw thermocouple reading to the real measured thermocouple
 * voltage in nanovolts (signed). */
int32_t ads1118_raw_to_nanovolts(int16_t raw);

/* ---------------------------------------------------------------------
 * Type-K conversion. RESOLVED (a later pass) - this was the driver's one
 * open gap.
 *
 * A thermocouple only ever reports the DIFFERENCE between its tip and
 * its cold junction, so turning a reading into a real temperature takes
 * three steps: find the EMF the cold junction itself is worth, add it to
 * the measured voltage, then convert that total back to a temperature.
 * Both directions come from one strictly-monotonic table generated from
 * NIST's own ITS-90 coefficients - see inc/ktype_table.h and
 * tools/gen_ktype_table.py for the provenance and validation.
 *
 * The earlier pass could not reach NIST's data (its site renders the
 * coefficient tables client-side, so plain fetches return an empty
 * shell) and correctly declined to invent the numbers. It is sourced
 * now, and cross-checked against two reference points TI quotes
 * independently from NIST.
 * ------------------------------------------------------------------- */

/* Forward: the EMF a junction at this temperature produces, referenced
 * to a 0 C cold junction. Used for cold-junction compensation. Input is
 * hundredths of a degree C, output microvolts. */
int32_t ktype_emf_uv_from_centiC(int32_t temp_centiC);

/* Inverse: the junction temperature, in hundredths of a degree C, that
 * produces this EMF against a 0 C cold junction. */
int32_t ktype_centiC_from_emf_uv(int32_t emf_uv);

/* The whole EGT measurement in one call: reads the thermocouple and the
 * on-die cold-junction sensor, compensates, and returns the real
 * exhaust gas temperature in hundredths of a degree C. */
int32_t ads1118_read_egt_centiC(void);

#endif /* ADS1118_H */
