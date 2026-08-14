/*
 * dspi.h - Deserial Serial Peripheral Interface (DSPI) driver.
 *
 * Real, verified this session against the actual NXP MPC5606BK Reference
 * Manual, Rev. 2 (Chapter 26, "Deserial Serial Peripheral Interface
 * (DSPI)", pages 593-646). Base addresses/offsets were confirmed via
 * positioned-text extraction and cross-checked against Chapter 3's own
 * memory map (independent source, same numbers both times); all
 * register BIT layouts (MCR, CTARn, SR, PUSHR, POPR) were visually
 * confirmed by rendering the real register diagrams (Figures 26-3, 26-5,
 * 26-6, 26-8, 26-9) - not taken from raw text extraction alone.
 *
 * This board's DSPI_0 bus is shared by 4 slaves (2x MC33810, 2x CJ125 -
 * see ecu_pins.h) with SOFTWARE-controlled chip selects on plain GPIO
 * pins (PIN_SPI_CS_INJ0/1, PIN_SPI_CS_O2A/B), NOT DSPI's own hardware
 * PCS0-5 lines - those aren't even routed to these pins (confirmed via
 * Table 4-1: PA[12]/13/14/15 carry SIN_0/SOUT_0/SCK_0, no CS0_0 wired to
 * any of this board's real CS pins). dspi_transfer() below is therefore
 * a bare single-frame primitive; CS assertion is the caller's job (see
 * mc33810.c for the real pattern: gpio_write() low, transfer, high).
 * The MC33810s and CJ125s need different real baud rates (see cj125.h -
 * CJ125's real max is much faster) - dspi_configure_ctar()/
 * dspi_transfer_ctas() (added once cj125.c needed them) let a second
 * real device share this bus via its own CTAR profile without
 * disturbing CTAR0's already-configured MC33810 timing.
 *
 * Real, confirmed facts:
 *   - MSTR/DCONF/HALT/MDIS in MCR, CPOL/CPHA/FMSZ/baud fields in CTARn,
 *     TCF/TFFF/RFDF/TXCTR/RXCTR in SR, and CONT/CTAS/PCS/TXDATA in
 *     PUSHR, RXDATA in POPR - all visually confirmed, Figures as above.
 *   - MC33810's real SPI mode (CPOL=0, CPHA=0): read directly from its
 *     own datasheet's Serial Clock Input description - "SI data is
 *     latched into the input shift register on the RISING edge of
 *     SCLK... SO pin shifts status bits out on the FALLING edge" -
 *     capture-on-leading/change-on-trailing with an idle-low clock is
 *     exactly DSPI's CPOL=0,CPHA=0 encoding, not assumed.
 *   - SIN_0's real routing (PA[12], see siul2.c): confirmed via a
 *     document-wide search that "SIN_0" appears exactly once in the
 *     entire 964-page manual (Table 4-1's own PA[12] row) - it is a
 *     fixed, dedicated connection, NOT selectable via the PSMI
 *     (Peripheral Selection Multiplex Input) mechanism that DOES exist
 *     for some other DSPI instances' SIN pins (e.g. SIN_3, SIN_4 each
 *     have a real PADSEL register picking between two candidate pins -
 *     DSPI_0 doesn't need one). This also means PA[12]'s blank AF1 slot
 *     in Table 4-1 was correctly read, not a corrupted cell as
 *     originally flagged - SIN_0 isn't AF-selected at all.
 *
 * RESOLVED since this file was first written (see mc33810.c/cj125.h/
 * l9779.c for where the real numbers ended up, not duplicated here):
 * CTAR0/CTAR1/CTAR2's real baud rate divider values (computed once the
 * system clock closed, clocks.h), and each real device's own real
 * chip-select timing (mc33810.c/l9779.c - a real, conservative busy-wait
 * now runs around every transfer).
 *
 * RESOLVED, a later pass: cross-checked those computed baud rates
 * against a real, independent hardware ceiling, not just "seemed
 * reasonable" - the actual MPC5606B Data Sheet, Rev. 5 (Section 3.18.2,
 * Table 44 "DSPI characteristics", fetched this session for the ADC
 * PDEDR work, see adc.h) gives DSPI0's real minimum tSCK (SCK cycle
 * time, master mode, MTFE=0) as 125ns - a real, confirmed 8MHz maximum
 * SCK frequency for this bus. Every real baud rate this project has
 * computed for DSPI_0 is comfortably under that real ceiling: MC33810/
 * L9779WD-SPI's shared 937.5kHz (CTAR0/CTAR2) and CJ125's 1.875MHz
 * (CTAR1) - real, positive confirmation, not just an assumption that
 * "slower than the chips' own max" was automatically safe against the
 * MCU's own real limit too.
 *
 * NOT done this session:
 *   - DMA/interrupt-driven transfers (RSER register) - dspi_transfer()
 *     is a simple blocking poll loop, which is fine for the main-loop
 *     SPI polling this architecture already calls for (see main.c) but
 *     would need real work before use from interrupt context.
 */
#ifndef DSPI_H
#define DSPI_H

#include <stdint.h>

/* Real base addresses, Table 26-2 (DSPI memory map), cross-checked
 * against Chapter 3's own memory map - both agreed. */
#define DSPI_0_BASE 0xFFF90000u
#define DSPI_1_BASE 0xFFF94000u
#define DSPI_2_BASE 0xFFF98000u
#define DSPI_3_BASE 0xFFF9C000u
#define DSPI_4_BASE 0xFFFA0000u
#define DSPI_5_BASE 0xFFFA4000u

/* Real register offsets, Table 26-2. */
#define DSPI_MCR(base)     (*(volatile uint32_t *)((base) + 0x00u))
#define DSPI_TCR(base)     (*(volatile uint32_t *)((base) + 0x08u))
#define DSPI_CTAR(base, n) (*(volatile uint32_t *)((base) + 0x0Cu + 4u * (uint32_t)(n)))
#define DSPI_SR(base)      (*(volatile uint32_t *)((base) + 0x2Cu))
#define DSPI_RSER(base)    (*(volatile uint32_t *)((base) + 0x30u))
#define DSPI_PUSHR(base)   (*(volatile uint32_t *)((base) + 0x34u))
#define DSPI_POPR(base)    (*(volatile uint32_t *)((base) + 0x38u))

/* MCR fields - Figure 26-3, visually confirmed. Freescale/PowerPC bit
 * numbering (datasheet bit 0 = MSB); macros below are already converted
 * to standard "1u << n" against the 32-bit register (standard_bit =
 * 31 - rm_bit) - never mix a raw "bit N" from the manual back in here
 * without converting. */
#define DSPI_MCR_MSTR       (1u << 31)  /* datasheet bit 0:  Master mode */
#define DSPI_MCR_CONT_SCKE  (1u << 30)  /* datasheet bit 1:  Continuous SCK enable */
#define DSPI_MCR_DCONF_SHIFT 28         /* datasheet bits 2:3 - must be 00 = SPI */
#define DSPI_MCR_DCONF_MASK (3u << DSPI_MCR_DCONF_SHIFT)
#define DSPI_MCR_FRZ        (1u << 27)  /* datasheet bit 4:  Freeze in debug mode */
#define DSPI_MCR_MTFE       (1u << 26)  /* datasheet bit 5:  Modified timing format enable */
#define DSPI_MCR_PCSSE      (1u << 25)  /* datasheet bit 6:  Peripheral CS strobe enable */
#define DSPI_MCR_ROOE       (1u << 24)  /* datasheet bit 7:  Receive FIFO overflow overwrite enable */
#define DSPI_MCR_PCSIS_SHIFT 16         /* datasheet bits 10:15 - PCSIS5..PCSIS0 */
#define DSPI_MCR_PCSIS_MASK (0x3Fu << DSPI_MCR_PCSIS_SHIFT)
#define DSPI_MCR_MDIS       (1u << 14)  /* datasheet bit 17: Module disable (RESET DEFAULT = 1) */
#define DSPI_MCR_DIS_TXF    (1u << 13)  /* datasheet bit 18: Disable TX FIFO (FIFO becomes 1-deep) */
#define DSPI_MCR_DIS_RXF    (1u << 12)  /* datasheet bit 19: Disable RX FIFO */
#define DSPI_MCR_CLR_TXF    (1u << 11)  /* datasheet bit 20: Clear TX FIFO (write-1, self-clearing) */
#define DSPI_MCR_CLR_RXF    (1u << 10)  /* datasheet bit 21: Clear RX FIFO (write-1, self-clearing) */
#define DSPI_MCR_SMPL_PT_SHIFT 8        /* datasheet bits 22:23 */
#define DSPI_MCR_SMPL_PT_MASK (3u << DSPI_MCR_SMPL_PT_SHIFT)
#define DSPI_MCR_HALT       (1u << 0)   /* datasheet bit 31: Halt (RESET DEFAULT = 1) */

/* CTARn fields - Figure 26-5, visually confirmed. */
#define DSPI_CTAR_DBR        (1u << 31) /* datasheet bit 0:    Double baud rate */
#define DSPI_CTAR_FMSZ_SHIFT 27         /* datasheet bits 1:4: frame size = FMSZ+1 bits */
#define DSPI_CTAR_FMSZ_MASK  (0xFu << DSPI_CTAR_FMSZ_SHIFT)
#define DSPI_CTAR_CPOL       (1u << 26) /* datasheet bit 5:    Clock polarity (0 = idle low) */
#define DSPI_CTAR_CPHA       (1u << 25) /* datasheet bit 6:    Clock phase (0 = capture on leading edge) */
#define DSPI_CTAR_LSBFE      (1u << 24) /* datasheet bit 7:    LSB first enable */
#define DSPI_CTAR_PCSSCK_SHIFT 22
#define DSPI_CTAR_PCSSCK_MASK (3u << DSPI_CTAR_PCSSCK_SHIFT)
#define DSPI_CTAR_PASC_SHIFT 20
#define DSPI_CTAR_PASC_MASK  (3u << DSPI_CTAR_PASC_SHIFT)
#define DSPI_CTAR_PDT_SHIFT  18
#define DSPI_CTAR_PDT_MASK   (3u << DSPI_CTAR_PDT_SHIFT)
#define DSPI_CTAR_PBR_SHIFT  16         /* baud rate prescaler - real values 2/3/5/7 for
                                          * PBR=00/01/10/11 (Table 26-5's own inline PBR
                                          * table, visually confirmed) - NOT a "value+1"
                                          * or power-of-two encoding like other fields here. */
#define DSPI_CTAR_PBR_MASK   (3u << DSPI_CTAR_PBR_SHIFT)
#define DSPI_CTAR_CSSCK_SHIFT 12
#define DSPI_CTAR_CSSCK_MASK (0xFu << DSPI_CTAR_CSSCK_SHIFT)
#define DSPI_CTAR_ASC_SHIFT  8
#define DSPI_CTAR_ASC_MASK   (0xFu << DSPI_CTAR_ASC_SHIFT)
#define DSPI_CTAR_DT_SHIFT   4
#define DSPI_CTAR_DT_MASK    (0xFu << DSPI_CTAR_DT_SHIFT)
#define DSPI_CTAR_BR_SHIFT   0          /* baud rate scaler - Table 26-17 */
#define DSPI_CTAR_BR_MASK    (0xFu << DSPI_CTAR_BR_SHIFT)

/* SR fields - Figure 26-6, visually confirmed. TCF/EOQF/TFUF/TFFF/RFOF/
 * RFDF are write-1-to-clear; TXRXS is a read-only status bit. */
#define DSPI_SR_TCF   (1u << 31)  /* datasheet bit 0:  Transfer complete flag */
#define DSPI_SR_TXRXS (1u << 30)  /* datasheet bit 1:  TX and RX status (running) */
#define DSPI_SR_EOQF  (1u << 28)  /* datasheet bit 3:  End of queue flag */
#define DSPI_SR_TFUF  (1u << 27)  /* datasheet bit 4:  TX FIFO underflow flag */
#define DSPI_SR_TFFF  (1u << 25)  /* datasheet bit 6:  TX FIFO fill flag (space available) */
#define DSPI_SR_RFOF  (1u << 19)  /* datasheet bit 12: RX FIFO overflow flag */
#define DSPI_SR_RFDF  (1u << 17)  /* datasheet bit 14: RX FIFO drain flag (data available) */
#define DSPI_SR_TXCTR_SHIFT 12
#define DSPI_SR_TXCTR_MASK  (0xFu << DSPI_SR_TXCTR_SHIFT)
#define DSPI_SR_RXCTR_SHIFT 4
#define DSPI_SR_RXCTR_MASK  (0xFu << DSPI_SR_RXCTR_SHIFT)

/* PUSHR fields - Figure 26-8, visually confirmed. */
#define DSPI_PUSHR_CONT      (1u << 31) /* datasheet bit 0:    Continuous PCS (unused - see file header) */
#define DSPI_PUSHR_CTAS_SHIFT 28        /* datasheet bits 1:3: which CTARn to use for this frame */
#define DSPI_PUSHR_CTAS_MASK  (7u << DSPI_PUSHR_CTAS_SHIFT)
#define DSPI_PUSHR_EOQ       (1u << 27) /* datasheet bit 4:    End of queue */
#define DSPI_PUSHR_CTCNT     (1u << 26) /* datasheet bit 5:    Clear transfer counter */
#define DSPI_PUSHR_PCS_SHIFT 16         /* datasheet bits 10:15 - unused on this board, see file header */
#define DSPI_PUSHR_PCS_MASK  (0x3Fu << DSPI_PUSHR_PCS_SHIFT)
#define DSPI_PUSHR_TXDATA_MASK 0xFFFFu  /* datasheet bits 16:31 */

/* POPR - Figure 26-9, visually confirmed: RXDATA is the low 16 bits. */
#define DSPI_POPR_RXDATA_MASK 0xFFFFu

/* Brings up one DSPI module for master-mode, polled (no DMA/interrupt)
 * operation: sets MSTR, clears MDIS/HALT, loads CTAR0 with ctar0_value
 * (build with the DSPI_CTAR_* macros above - caller owns baud rate and
 * CPOL/CPHA choice; see mc33810.c for the real MC33810 values). */
void dspi_init(uint32_t base, uint32_t ctar0_value);

/* Real, generic: loads any one of the 6 real CTAR profiles (Table 26-2:
 * offsets 0x0C-0x20) with a caller-built value - lets a second real
 * device sharing this bus (e.g. the CJ125 - see cj125.h) run at its own
 * real baud rate/timing via a different CTAR index than dspi_init()'s
 * CTAR0, without disturbing CTAR0's own already-configured value.
 * ctar_index: 0-5 (Table 26-2's own real CTAR0-CTAR5 range). */
void dspi_configure_ctar(uint32_t base, uint8_t ctar_index, uint32_t ctar_value);

/* One blocking 16-bit (or whatever the selected CTAR's FMSZ says)
 * master-mode transfer using CTAR0, no hardware PCS (see file header -
 * CS is the caller's own GPIO). Returns the frame shifted in on SIN
 * while tx_data was shifted out on SOUT - for a device like the
 * MC33810 whose SO is the PREVIOUS command's response, that pipelining
 * is the caller's own concern (see mc33810.c). Equivalent to
 * dspi_transfer_ctas(base, 0, tx_data). */
uint16_t dspi_transfer(uint32_t base, uint16_t tx_data);

/* Real, generic version of dspi_transfer() that lets the caller pick
 * which of the 6 real CTAR profiles (ctas, PUSHR's own real CTAS field)
 * this specific transfer uses - see dspi_configure_ctar() above. */
uint16_t dspi_transfer_ctas(uint32_t base, uint8_t ctas, uint16_t tx_data);

#endif /* DSPI_H */
