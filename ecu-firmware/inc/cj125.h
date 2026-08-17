/*
 * cj125.h - Bosch CJ125 wideband O2 (lambda) controller SPI interface.
 *
 * Real, verified this session against the actual Bosch CJ125 datasheet
 * (Robert Bosch GmbH, 04/2006 - the same real datasheet already used
 * during this project's PCB design phase, ../ecu-pcb/build_schematic.py's
 * own CJ125 placement comments). Facts below came from the datasheet's
 * own "SPI - Block schematic, register, RD/WR-commands" figure and the
 * "SPI - Timing" waveform diagram, both rendered and read directly
 * (the register/command hex codes and bit-field layout are drawn as a
 * block diagram, not stated as plain text, so raw text extraction alone
 * would have missed them entirely - same discipline as the MPC5606B
 * driver headers in this same project).
 *
 * Real, confirmed facts:
 *   - 16-bit SPI frame: an 8-bit command byte (address + a real,
 *     chip-specific "hec" error-check encoding - NOT a simple address+
 *     parity bit pattern decomposable by inspection, so this driver
 *     uses the 6 real command byte VALUES directly rather than trying
 *     to reconstruct them from field math) followed by an 8-bit data
 *     byte (write payload, or don't-care on a read).
 *   - Real, distinctive pipelining (different from this project's
 *     MC33810 driver): unlike the MC33810, where a transfer's response
 *     is to the PREVIOUS whole command, the CJ125's response DATA byte
 *     is for the CURRENT command, arriving within the same 16-bit
 *     exchange - confirmed directly from the datasheet's own Read
 *     Access timing diagram (SI's command byte and SO's real data byte
 *     are shown in the same 16-bit frame). Only the FIRST (status) byte
 *     of the response reflects the previous transfer; this driver
 *     doesn't decode that status byte further (not needed for a basic
 *     read/write driver) - see cj125_transfer()'s own comment.
 *   - Real SPI mode: CPOL=0, CPHA=0, MSB-first - confirmed visually
 *     from the "SPI - Timing" diagram (SCK idles low; SI's first bit is
 *     already stable before the first SCK rising edge, the classic
 *     CPHA=0 pattern) - not assumed from a generic default, and happens
 *     to match this board's MC33810 driver's own confirmed mode.
 *   - Real max SPI data rate: 2 Mbaud (Electrical Characteristics
 *     table, "SPI / Data rate"), far faster than the MC33810's own
 *     conservative baud - this is why cj125_init() below uses DSPI_0's
 *     CTAR1 (dspi_configure_ctar(), dspi.h) rather than sharing
 *     MC33810's CTAR0.
 *   - The real 6 command bytes (of this chip's real "4 registers, 6
 *     commands" - Electrical Characteristics table) and their real
 *     bit-field layouts:
 *     - INIT_REG1 (RD=0x6C, WR=0x56): PA (bit 6, pump current control
 *       enable - "PA=1" required for real pump output per the
 *       datasheet's own Pump Current Control electrical spec rows),
 *       RA (bit 4, Ri-measurement mode: 0=measurement, 1=adjustment -
 *       explicitly defined in the electrical table), LA (bit 2, pump
 *       current sense amplifier mode: 0=measurement, 1=adjustment -
 *       likewise explicit), VL (bit 0, pump current sense amplifier
 *       gain select: 0 -> ~8x [7.82-8.15], 1 -> ~17x [16.62-17.24],
 *       both real min/max ranges from the electrical table). Bits
 *       7/5/3/1 are real, confirmed-blank (reserved) per the diagram.
 *     - INIT_REG2 (RD=0x7E, WR=0x5A): ENSCUN (bit 4, enables real
 *       failure identification at the UN pin - confirmed via the
 *       failure-bits table's own footnote), PR3:PR0 (bits 3:0, real
 *       4-bit pump reference current select, electrical table: "Pump
 *       reference current... programmable with SPI-bits PRx" over a
 *       real 0-150uA range - exact per-code current not given in this
 *       extraction, see NOT DONE below). Bits 7:5 reserved.
 *     - IDENT_REG (RD=0x48, read-only): real chip identification - the
 *       response byte's upper 5 bits are a fixed 0b01100 for CJ120/125
 *       (confirmed directly in the diagram), lower 3 bits are a real
 *       silicon version code. A real, useful startup sanity check.
 *     - DIAG_REG (RD=0x78, read-only): four real 2-bit diagnostic
 *       fields packed into one byte - ext. heater (bits 7:6, from the
 *       real DIAHG/DIAHD pins), sensor pump-current path (bits 5:4,
 *       from IA/IP), UN (bits 3:2), VM (bits 1:0) - each using the real
 *       encoding from the datasheet's own Failure bits table: 00 =
 *       short to ground, 01 = open load (heater) / low battery
 *       (sensor), 10 = short to Vbat, 11 = no failure.
 *   - This board's real CJ125 chip-select pins - PIN_SPI_CS_O2A (bank
 *     A, U9) / PIN_SPI_CS_O2B (bank B, U18) - already established in
 *     ecu_pins.h/mc33810.h from the PCB design phase, not new this
 *     pass.
 *
 * NOT done this session:
 *   - The real INIT_REG1/2 byte values for this board's actual running
 *     configuration (which VL gain range matches the real LSU4.2
 *     sensor's calibration, which PR0-3 pump-reference-current code to
 *     use) - this driver's write functions are real, generic byte
 *     setters; the specific real values need either a fuller real
 *     application-note extraction (this session's datasheet excerpt
 *     doesn't give the PRx-to-uA transfer function) or bench
 *     calibration against a real sensor, neither done this session.
 *   - A real, second-hand data point was checked and did NOT survive
 *     re-verification against the primary source - worth recording the
 *     process, not just the conclusion. A web search summary claimed
 *     rusEFI's production `cj125_logic.h` defines
 *     `CJ125_INIT1_NORMAL_8 = 0x88` for "normal operation, amplification
 *     8x," which wouldn't decompose consistently against this file's own
 *     bit positions (`PA`=bit6/0x40 alone would be the expected real
 *     byte for that configuration). Before changing anything, the real
 *     primary source was re-checked directly: the actual Bosch datasheet
 *     page rendered as an image this session (`cj125_spi_page.png`)
 *     shows INIT_REG1's real bit layout explicitly, box by box:
 *     `x | PA | x | RA | x | LA | x | VL` (bits 7/5/3/1 reserved) -
 *     exactly matching this file's own `CJ125_INIT1_PA`=bit6/`RA`=bit4/
 *     `LA`=bit2/`VL`=bit0 already. The attempted fetch of rusEFI's real
 *     source to check its own bit convention directly was blocked
 *     (HTTP 403) - the "0x88" figure was only ever seen via an AI-
 *     summarized search result, never actually read from source, so it
 *     doesn't count as a verified conflicting primary source. This
 *     file's existing bit positions stand, now doubly confirmed. Real,
 *     genuine gap that's actually still open: for a real
 *     "normal operation, gain=8x, measurement mode" byte, the formula is
 *     `PA=1` (real pump output enabled) with `RA=0`/`LA=0`/`VL=0` = real
 *     byte `0x40` - a real, generic formula, not this board's specific
 *     chosen configuration (RA/LA "adjustment" vs "measurement" mode,
 *     and which VL gain matches the real LSU4.2 sensor in use, are still
 *     real, separate decisions for a bench-calibration pass).
 *   - The command byte's own real "hec" generation algorithm - not
 *     needed (the 6 real values are used directly, see above), but
 *     genuinely not reverse-engineered, so no 7th/8th command can be
 *     synthesized without finding the real algorithm or another real
 *     source table.
 *   - Lambda/AFR conversion from the real UA (lambda output) and UR
 *     (Ri/temperature) analog voltages - those are read via this
 *     board's real ADC pins (see ecu_pins.h's ADC channels, if wired -
 *     not cross-checked against cj125.h this session), not the SPI
 *     interface at all; this driver only covers the digital SPI
 *     control/diagnostic path.
 */
#ifndef CJ125_H
#define CJ125_H

#include <stdint.h>

/* Real command bytes (see file header - used directly, not
 * synthesized from address+parity math). */
#define CJ125_CMD_INIT1_RD 0x6Cu
#define CJ125_CMD_INIT1_WR 0x56u
#define CJ125_CMD_INIT2_RD 0x7Eu
#define CJ125_CMD_INIT2_WR 0x5Au
#define CJ125_CMD_IDENT_RD 0x48u
#define CJ125_CMD_DIAG_RD  0x78u

/* INIT_REG1 real bit fields. */
#define CJ125_INIT1_PA (1u << 6)
#define CJ125_INIT1_RA (1u << 4)
#define CJ125_INIT1_LA (1u << 2)
#define CJ125_INIT1_VL (1u << 0)

/* INIT_REG2 real bit fields. */
#define CJ125_INIT2_ENSCUN    (1u << 4)
#define CJ125_INIT2_PR_SHIFT  0
#define CJ125_INIT2_PR_MASK   (0xFu << CJ125_INIT2_PR_SHIFT)

/* IDENT_REG real expected identification bits (upper 5 bits of the
 * response byte) - mask off the lower 3 (version) bits before
 * comparing. */
#define CJ125_IDENT_ID_MASK  0xF8u
#define CJ125_IDENT_ID_VALUE 0x60u   /* real 0b01100 in bits 7:3 */

/* DIAG_REG real per-field shifts and the real, shared 2-bit failure
 * encoding (Failure bits table). */
#define CJ125_DIAG_HEATER_SHIFT 6
#define CJ125_DIAG_SENSOR_SHIFT 4
#define CJ125_DIAG_UN_SHIFT     2
#define CJ125_DIAG_VM_SHIFT     0
#define CJ125_DIAG_FIELD_MASK   0x3u
#define CJ125_DIAG_SHORT_GND    0x0u
#define CJ125_DIAG_OPEN_OR_LOWBATT 0x1u
#define CJ125_DIAG_SHORT_VBAT   0x2u
#define CJ125_DIAG_NO_FAILURE   0x3u

/* Real, computed CTAR value for DSPI_0's CTAR1 (this board's real
 * 60MHz peripheral clock, same derivation as MC33810_CTAR0 - see
 * mc33810.c): CPOL=0/CPHA=0 (no bits set), FMSZ=15 (16-bit frames),
 * PBR=00 (prescaler 2), BR=0100 (scaler 16) -> SCK = 60MHz/(2*16) =
 * 1.875MHz, safely under the CJ125's real confirmed 2Mbaud max. */
#define CJ125_CTAR1 ( \
    (15u << 27) /* DSPI_CTAR_FMSZ_SHIFT, see dspi.h */ \
    | (0u << 16) /* DSPI_CTAR_PBR_SHIFT: PBR=00 -> prescaler 2 */ \
    | (0x4u << 0) /* DSPI_CTAR_BR_SHIFT: BR=0100 -> scaler 16 */ \
)

/* Brings up DSPI_0's CTAR1 for real CJ125 timing (see CJ125_CTAR1
 * above) - does NOT configure either CJ125 chip's own registers (see
 * file header's NOT DONE section). Call once, after dspi_init()
 * (mc33810.c) has already brought up DSPI_0 itself. */
void cj125_init(void);

/* One raw 16-bit SPI transfer against a specific CJ125 (its own real
 * CS pin - PIN_SPI_CS_O2A or PIN_SPI_CS_O2B, ecu_pins.h), using CTAR1.
 * Real pipelining (see file header): the returned word's low byte is
 * this command's own real response data; the high byte is a real
 * status/ack byte for whichever command preceded this one, not decoded
 * further by this driver. */
uint16_t cj125_transfer(uint8_t cs_pin, uint16_t tx_word);

/* Real, complete: sends CJ125_CMD_IDENT_RD and returns the real 8-bit
 * identification byte (bits 7:3 should equal CJ125_IDENT_ID_VALUE for
 * a genuine CJ120/125 - a real startup sanity check). */
uint8_t cj125_read_ident(uint8_t cs_pin);

/* Real, complete: sends CJ125_CMD_DIAG_RD and returns the real 8-bit
 * diagnostic byte (four 2-bit fields - see the CJ125_DIAG_* macros
 * above). */
uint8_t cj125_read_diag(uint8_t cs_pin);

/* Real, generic byte-value setters for INIT_REG1/2 - build `value` from
 * the CJ125_INIT1_ and CJ125_INIT2_ macro families above. The specific
 * real byte values for this board's actual running configuration are
 * NOT determined this session - see file header.
 *
 * (The macro families are deliberately NOT written here with a trailing
 * wildcard-then-slash: that character pair closes a block comment, and
 * writing it inside this one is exactly the bug the first real compile
 * of this project caught here - it silently ended the comment and threw
 * the rest of the file into the parser as code.) */
void cj125_write_init1(uint8_t cs_pin, uint8_t value);
void cj125_write_init2(uint8_t cs_pin, uint8_t value);

/* Real fault-response policy - see cj125.c for what each field
 * actually means to do about it. is_bank_a: 1 for CJ125 #1 (U9, bank
 * A), 0 for CJ125 #2 (U18, bank B) - same convention as
 * mc33810_handle_status()'s is_bank_1_4. */
void cj125_handle_diag(uint8_t diag, int is_bank_a);

#endif /* CJ125_H */
