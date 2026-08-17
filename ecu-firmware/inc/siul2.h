/*
 * siul2.h - System Integration Unit Lite (SIUL) pin control.
 *
 * Real, verified this session against the actual NXP MPC5606BK
 * Microcontroller Reference Manual, Rev. 2, 05/2014 (downloaded directly
 * from NXP - 16234_MPC5606BRM_Rev2.pdf, 964 pages). Base address and the
 * PCRx bit layout were both confirmed by rendering the real register
 * diagrams to images and reading them visually (Chapter 20, "System
 * Integration Unit Lite (SIUL)", Figure 20-9 specifically) - not taken
 * from the PDF's raw text extraction, which shuffles table columns
 * badly enough on this document to be actively misleading on its own.
 *
 * IMPORTANT, easy to get wrong: the PCR register array (and gpio_write()/
 * gpio_read() below) are indexed by INTERNAL PAD NUMBER (PA[0]=PCR[0],
 * PA[1]=PCR[1], ... PB[0]=PCR[16], ...), NOT by the 144-LQFP package pin
 * number used everywhere else in this firmware (ecu_pins.h) and in the
 * PCB project. Confirmed directly from Table 4-1 "Functional port pins":
 * PA[1] = PCR[1], but PA[1]'s real 144-LQFP package pin is 11. A package
 * pin number can NEVER be passed to pcr_configure()/gpio_write() as-is -
 * use siul2_pcr_for_pin() (bottom of this file) to convert one.
 */
#ifndef SIUL2_H
#define SIUL2_H

#include <stdint.h>

#define SIUL_BASE           0xC3F90000u
#define SIUL_PCR(pcr_index) (SIUL_BASE + 0x0040u + 2u * (uint32_t)(pcr_index))
#define SIUL_GPDO_BASE      (SIUL_BASE + 0x0600u)
#define SIUL_GPDI_BASE      (SIUL_BASE + 0x0800u)

/* PCRx bit layout - Figure 20-9, visually confirmed. This is a
 * Freescale/PowerPC-style register: bit 0 is the MSB (leftmost in the
 * datasheet's own diagram), bit 15 is the LSB. The macros below are
 * already converted to normal C "1u << n" shifts against a 16-bit
 * value, so callers never need to think about the reversed numbering -
 * just don't reintroduce it by copying a "bit N" number straight out of
 * the reference manual without converting (standard_bit = 15 - rm_bit).
 */
#define PCR_WPS      (1u << 0)   /* datasheet bit 15: Weak Pull Select (1=pull-up, 0=pull-down) */
#define PCR_WPE      (1u << 1)   /* datasheet bit 14: Weak Pull Enable */
#define PCR_SRC      (1u << 2)   /* datasheet bit 13: Slew Rate Control */
#define PCR_ODE      (1u << 5)   /* datasheet bit 10: Open Drain Enable */
#define PCR_IBE      (1u << 8)   /* datasheet bit 7:  Input Buffer Enable */
#define PCR_OBE      (1u << 9)   /* datasheet bit 6:  Output Buffer Enable */
#define PCR_PA_SHIFT 10          /* datasheet bits 4:5 -> standard bits 11:10 */
#define PCR_PA_MASK  (3u << PCR_PA_SHIFT)
#define PCR_APC      (1u << 13)  /* datasheet bit 2:  Analog Pad Control */
#define PCR_SMC      (1u << 14)  /* datasheet bit 1:  Safe Mode Control */

/* PA[1:0] alternate-function select (Table 20-11):
 *   0 = GPIO, 1/2/3 = alternate function 1/2/3 - WHICH real peripheral
 *   signal each AF number maps to is per-pin, from Table 4-1
 *   ("Functional port pins") - not encoded here, see the TODO below. */
/* Cast to uint16_t is deliberate and provably lossless: the mask
 * bounds n to 3, so the widest result is 3 << 10 = 0x0C00, well
 * inside 16 bits. Stating it explicitly keeps -Wconversion usable as
 * a standing check, so a genuine truncation bug would stand out. */
#define PCR_AF(n)    ((uint16_t)(((uint32_t)(n) & 0x3u) << PCR_PA_SHIFT))

/* Direct 16-bit register write - real, safe to use once the caller has
 * the correct PCR index (see the file-level warning above). */
static inline void pcr_configure(uint8_t pcr_index, uint16_t value) {
    *(volatile uint16_t *)SIUL_PCR(pcr_index) = value;
}

static inline uint16_t pcr_read(uint8_t pcr_index) {
    return *(volatile uint16_t *)SIUL_PCR(pcr_index);
}

/* Single-pin GPIO output/input, byte-addressable - real, confirmed
 * visually (Figure 20-11/Table 20-14, GPDO0_3): each pin's PDO bit is
 * independently byte-addressable at SIUL_GPDO_BASE + pcr_index, value
 * in the byte's bit 0 (0=low, 1=high when the pad is configured as an
 * output via PCR_OBE). Same indexing as pcr_configure() - PCR index,
 * not package pin number. This is how this board's DSPI chip-select
 * pins are driven: they're plain GPIO (see siul2.c), not the DSPI
 * peripheral's own hardware PCS lines, since the bus is shared across
 * 4 slaves (2x MC33810 + 2x CJ125) needing independent software-timed
 * selection. */
static inline void gpio_write(uint8_t pcr_index, uint8_t value) {
    *(volatile uint8_t *)(SIUL_GPDO_BASE + pcr_index) = value & 1u;
}

static inline uint8_t gpio_read(uint8_t pcr_index) {
    return *(volatile uint8_t *)(SIUL_GPDI_BASE + pcr_index) & 1u;
}

/*
 * RESOLVED (was the open gap above): the real package-pin -> (PCR
 * index, AF number) table for every pin this board uses now lives in
 * siul2.c (PINMUX_TABLE) - all 62 real entries matched against Table
 * 4-1 "Functional port pins" (Reference Manual pages 55-74) by real
 * 144-LQFP pin number, using positioned-text extraction (not the flat
 * text extraction, which shuffles this table's columns) and
 * cross-checked against each pin's already-known real signal name from
 * ecu_pins.h. Every entry matched exactly once - none guessed.
 *
 * Real, confirmed port-to-PCR formula that fell out of building that
 * table (not assumed going in): PCR index = port_offset*16 +
 * pin_within_port, where A=0, B=1, C=2, D=3, E=4, G=6 (confirmed for
 * every port this board's real pins actually land on - F and H not
 * exercised, not extended to them without evidence). e.g. PC[4] =
 * PCR[2*16+4] = PCR[36].
 */

/* Configures every real pin in siul2.c's PINMUX_TABLE - see that file
 * for the actual per-pin data. All 62 entries are now fully resolved,
 * including the DSPI_0 bus (SPI_SCK/SPI_SOUT/SPI_SIN), switched onto
 * their real alternate functions once the DSPI driver existed to use
 * them (see dspi.h/dspi.c). */
void pinmux_init(void);

/* Real package-pin -> PCR-index lookup, backed by siul2.c's own
 * PINMUX_TABLE (the same real data pinmux_init() configures from) -
 * for callers that need to drive/read a pin as plain GPIO at runtime
 * (e.g. mc33810.c's chip-select toggling) using the same package pin
 * numbers ecu_pins.h already gives every other caller, rather than
 * requiring every caller to know the internal PCR-index distinction
 * pcr_configure()/gpio_write() actually need. Returns 0xFF if
 * package_pin isn't one of this board's real 62 muxed pins - callers
 * should treat that as a real "not a valid pin" fault, not silently
 * proceed with pcr_index 0. */
uint8_t siul2_pcr_for_pin(uint16_t package_pin);

#endif /* SIUL2_H */
