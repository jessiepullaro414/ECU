/*
 * flexcan.h - FlexCAN controller driver.
 *
 * Real, verified this session against the actual NXP MPC5606BK Reference
 * Manual, Rev. 2 (Chapter 25, "FlexCAN", pages 548-...). Base addresses
 * confirmed via positioned-text extraction (Table 25-2, and cross-
 * checked - both MCR and CTRL bit layouts were visually confirmed by
 * rendering the real register diagrams (Figures 25-5, 25-6), and the
 * Message Buffer C/S+ID word layout was visually confirmed (Figure
 * 25-2) then corrected against its own field-description text (Table
 * 25-4) after a first pixel-boundary reading of the figure turned out
 * to be off by one bit for the PRIO/ID split - the text ("11 most
 * significant bits (3 to 13)") settled it, a real example of this
 * project's own "don't trust a single read, cross-check" discipline
 * catching itself.
 *
 * This board has two real, independent FlexCAN buses, both confirmed
 * against ../ecu-pcb/build_schematic.py's own real routing (not
 * re-derived here): CAN0 = FlexCAN_1 (TX=PC[10] AF1, RX=PC[11] AF0 -
 * see ecu_pins.h/siul2.c), CAN1 = FlexCAN_4 (TX=PC[2] AF2, RX=PC[3]
 * AF0). Both TX pins are real, single-AF-slot pin-mux entries; both RX
 * pins turned out to be the same "dedicated input, no AF slot" pattern
 * already seen for DSPI's SIN_0 (Table 4-1 lists CAN1RX/CAN4RX as extra
 * rows beyond AF0-3 on their respective pins, not an AF value) -
 * confirmed by the same visual read, not assumed to generalize from
 * DSPI's case.
 *
 * Real, confirmed facts:
 *   - Base addresses (Table 25-2, cross-checked against Chapter 3's
 *     memory map): FlexCAN_0 0xFFFC0000 ... FlexCAN_5 0xFFFD4000, 0x4000
 *     apart, same spacing pattern as DSPI's instances.
 *   - MCR's MDIS/FRZ/FEN/HALT/NOT_RDY/FRZ_ACK/MAXMB and CTRL's
 *     PRESDIV/RJW/PSEG1/PSEG2/PROPSEG/CLK_SRC/LOM/LPB - all visually
 *     confirmed, Figures 25-5/25-6. Both registers reset with the
 *     module already in Freeze mode (FRZ=1, HALT=1) and disabled
 *     (MDIS=1) - real, convenient: registers can be configured safely
 *     before the module starts running, no separate "enter freeze"
 *     step needed on a fresh reset (still done explicitly below for
 *     correctness on a non-fresh call).
 *   - The Message Buffer C/S word (CODE/SRR/IDE/RTR/LENGTH/TIME STAMP)
 *     and ID word (PRIO/ID) layout - Figure 25-2, cross-checked against
 *     Table 25-4's text as noted above. MAXMB's real reset default (15)
 *     is left alone - MB0-15 (16 buffers) are usable without touching
 *     it, same "don't configure past what's needed" restraint as CTR0-2
 *     in adc.h.
 *   - Message Buffer CODE encodings for Rx (INACTIVE/EMPTY/FULL/
 *     OVERRUN) and Tx (INACTIVE/transmit-once) - Tables 25-5/25-6.
 *   - The real Rx message buffer lock mechanism (Section 25.5.7.3):
 *     reading an active Rx MB's C/S word locks it; the lock releases on
 *     reading TIMER (global unlock) or another MB's C/S word.
 *     flexcan_receive_poll() below does the real read-C/S, read-ID,
 *     read-data, read-TIMER sequence for exactly this reason - skipping
 *     the TIMER read would leave the MB locked and never receive again.
 *   - This driver deliberately uses plain Message Buffers, not the Rx
 *     FIFO engine (MCR.FEN) - simpler, and this board's telemetry use
 *     case (a handful of known message IDs) doesn't need FIFO's 8-entry
 *     ID filter table.
 *
 * NOT done this session:
 *   - Real CTRL bit-timing values (PRESDIV/PSEG1/PSEG2/PROPSEG/RJW) for
 *     any specific real baud rate (e.g. 500 kbit/s) - the register
 *     mechanism and formulas are real (Sclock = CPI_clock/(PRESDIV+1);
 *     bit time in time quanta = 1 (sync, fixed) + (PROPSEG+1) +
 *     (PSEG1+1) + (PSEG2+1)), but computing real divider values needs
 *     the FlexCAN peripheral's real CPI clock frequency, which depends
 *     on the still-open system-clock gap in clocks.h. flexcan_init()
 *     takes a pre-built CTRL value from the caller, same pattern as
 *     dspi_init()'s CTAR0 argument.
 *   - Real CAN message IDs/payload formats for this ECU's actual
 *     telemetry/fault-broadcast traffic - engine/application-specific,
 *     not board-specific, same scope boundary as firing order and VE
 *     tables (see the project plan).
 *   - Bus-off recovery, error counters (ECR/ESR), and any interrupt-
 *     driven path (IMASK/IFLAG) - this driver is polled-only, matching
 *     every other peripheral driver in this firmware.
 *   - flexcan_transmit()'s blocking wait has no timeout - a bus with no
 *     other node acknowledging (or a bus-off condition) hangs it
 *     forever, the same class of gap already flagged for
 *     fmpll_wait_lock()/me_transition_to() in clocks.c.
 */
#ifndef FLEXCAN_H
#define FLEXCAN_H

#include <stdint.h>

/* Real base addresses, Table 25-2. */
#define FLEXCAN_0_BASE 0xFFFC0000u
#define FLEXCAN_1_BASE 0xFFFC4000u   /* this board's CAN0 bus */
#define FLEXCAN_2_BASE 0xFFFC8000u
#define FLEXCAN_3_BASE 0xFFFCC000u
#define FLEXCAN_4_BASE 0xFFFD0000u   /* this board's CAN1 bus */
#define FLEXCAN_5_BASE 0xFFFD4000u

/* Real register offsets, Table 25-2. */
#define FLEXCAN_MCR(base)      (*(volatile uint32_t *)((base) + 0x00u))
#define FLEXCAN_CTRL(base)     (*(volatile uint32_t *)((base) + 0x04u))
#define FLEXCAN_TIMER(base)    (*(volatile uint32_t *)((base) + 0x08u))
#define FLEXCAN_RXGMASK(base)  (*(volatile uint32_t *)((base) + 0x10u))
#define FLEXCAN_IFLAG1(base)   (*(volatile uint32_t *)((base) + 0x30u))

/* Message buffer access - Table 25-3/Figure 25-2. MB0..MB15 are real
 * (MCR's reset-default MAXMB=15, not changed by this driver - see file
 * header). Each MB is 16 bytes: C/S word, ID word, then 8 data bytes. */
#define FLEXCAN_MB_BASE(base, mb) ((base) + 0x80u + 0x10u * (uint32_t)(mb))
#define FLEXCAN_MB_CS(base, mb)   (*(volatile uint32_t *)(FLEXCAN_MB_BASE((base), (mb)) + 0x0u))
#define FLEXCAN_MB_ID(base, mb)   (*(volatile uint32_t *)(FLEXCAN_MB_BASE((base), (mb)) + 0x4u))
#define FLEXCAN_MB_DATA(base, mb) ((volatile uint8_t *)(FLEXCAN_MB_BASE((base), (mb)) + 0x8u))

/* MCR fields - Figure 25-5, visually confirmed. Freescale/PowerPC bit
 * numbering converted to standard "1u << n" (standard_bit = 31 - rm_bit),
 * same convention as every other driver in this firmware. */
#define FLEXCAN_MCR_MDIS     (1u << 31) /* datasheet bit 0:  Module disable (RESET DEFAULT = 1) */
#define FLEXCAN_MCR_FRZ      (1u << 30) /* datasheet bit 1:  Freeze enable (RESET DEFAULT = 1) */
#define FLEXCAN_MCR_FEN      (1u << 29) /* datasheet bit 2:  Rx FIFO enable - left 0, see file header */
#define FLEXCAN_MCR_HALT     (1u << 28) /* datasheet bit 3:  Halt (RESET DEFAULT = 1) */
#define FLEXCAN_MCR_NOT_RDY  (1u << 27) /* datasheet bit 4:  read-only status */
#define FLEXCAN_MCR_FRZ_ACK  (1u << 24) /* datasheet bit 7:  read-only status */
#define FLEXCAN_MCR_SUPV     (1u << 23) /* datasheet bit 8:  Supervisor mode (RESET DEFAULT = 1) */
#define FLEXCAN_MCR_SRX_DIS  (1u << 17) /* datasheet bit 14: Self-reception disable */
#define FLEXCAN_MCR_MAXMB_SHIFT 0        /* datasheet bits 26:31 - RESET DEFAULT = 15 (MB0-15) */
#define FLEXCAN_MCR_MAXMB_MASK  (0x3Fu << FLEXCAN_MCR_MAXMB_SHIFT)

/* CTRL fields - Figure 25-6, visually confirmed. */
#define FLEXCAN_CTRL_PRESDIV_SHIFT 24
#define FLEXCAN_CTRL_PRESDIV_MASK  (0xFFu << FLEXCAN_CTRL_PRESDIV_SHIFT)
#define FLEXCAN_CTRL_RJW_SHIFT     22
#define FLEXCAN_CTRL_RJW_MASK      (3u << FLEXCAN_CTRL_RJW_SHIFT)
#define FLEXCAN_CTRL_PSEG1_SHIFT   19
#define FLEXCAN_CTRL_PSEG1_MASK    (7u << FLEXCAN_CTRL_PSEG1_SHIFT)
#define FLEXCAN_CTRL_PSEG2_SHIFT   16
#define FLEXCAN_CTRL_PSEG2_MASK    (7u << FLEXCAN_CTRL_PSEG2_SHIFT)
#define FLEXCAN_CTRL_LPB           (1u << 12)  /* datasheet bit 19: Loopback test mode */
#define FLEXCAN_CTRL_SMP           (1u << 7)   /* datasheet bit 24: Sampling mode (3-sample vs 1-sample) */
#define FLEXCAN_CTRL_LOM           (1u << 3)   /* datasheet bit 28: Listen-only mode */
#define FLEXCAN_CTRL_PROPSEG_SHIFT 0
#define FLEXCAN_CTRL_PROPSEG_MASK  (7u << FLEXCAN_CTRL_PROPSEG_SHIFT)

/* Message Buffer C/S word fields - Figure 25-2, cross-checked against
 * Table 25-4 (see file header for the real correction this caught). */
#define FLEXCAN_CS_CODE_SHIFT   24
#define FLEXCAN_CS_CODE_MASK    (0xFu << FLEXCAN_CS_CODE_SHIFT)
#define FLEXCAN_CS_SRR          (1u << 22)
#define FLEXCAN_CS_IDE          (1u << 21)
#define FLEXCAN_CS_RTR          (1u << 20)
#define FLEXCAN_CS_LENGTH_SHIFT 16
#define FLEXCAN_CS_LENGTH_MASK  (0xFu << FLEXCAN_CS_LENGTH_SHIFT)

/* Message Buffer ID word fields - real: PRIO is a 3-bit LOCAL-only
 * field (never transmitted, Table 25-4), so the real CAN ID starts at
 * standard bit 28. Standard (11-bit) frames use only the top 11 bits of
 * that - std bits 28:18 - per Table 25-4's own text ("only the 11 most
 * significant bits (3 to 13) [datasheet numbering] are used"). Extended
 * (29-bit) frames use all of std bits 28:0. */
#define FLEXCAN_ID_STD_SHIFT 18
#define FLEXCAN_ID_STD_MASK  (0x7FFu << FLEXCAN_ID_STD_SHIFT)
#define FLEXCAN_ID_EXT_MASK  0x1FFFFFFFu   /* shift 0 */

/* Message Buffer CODE encodings - Tables 25-5/25-6. */
#define FLEXCAN_CODE_RX_INACTIVE 0x0u
#define FLEXCAN_CODE_RX_EMPTY    0x4u
#define FLEXCAN_CODE_RX_FULL     0x2u
#define FLEXCAN_CODE_TX_INACTIVE 0x8u
#define FLEXCAN_CODE_TX_ONCE     0xCu   /* "transmit data frame unconditionally once" */

/* Brings up one FlexCAN module (FLEXCAN_1_BASE = this board's CAN0,
 * FLEXCAN_4_BASE = CAN1) for polled, plain-Message-Buffer operation:
 * enters Freeze mode, clears MDIS, loads ctrl_value into CTRL (real bit
 * timing - caller-supplied, see file header), clears every MB0-15's C/S
 * word to INACTIVE, sets RXGMASK to accept-all (real, simple default -
 * this board's specific telemetry ID filtering is an application-layer
 * choice, not done here), then exits Freeze mode. */
void flexcan_init(uint32_t base, uint32_t ctrl_value);

/* Real, bounded wait-count timeout (same honest caveat as
 * clocks.h's CLOCKS_WAIT_ITERATIONS - not calibrated against a real
 * time unit, no systick/timer wired up yet, just a real, generous
 * count at this board's confirmed 60MHz core clock). */
#define FLEXCAN_WAIT_ITERATIONS 1000000u

/* Blocking transmit of one frame on message buffer mb (0-15) - writes
 * ID/data/C-S, waits for the real CODE-returns-to-INACTIVE completion
 * (Table 25-6), or FLEXCAN_WAIT_ITERATIONS is exhausted. extended: 0
 * for an 11-bit standard ID, 1 for a 29-bit extended ID. len: 0-8 real
 * CAN data bytes. Returns 1 on real completion, 0 on timeout -
 * RESOLVED: a bus with no ACKing node used to hang this forever (real
 * CAN protocol-level retry behavior); now it reports the failure
 * instead, which is what makes calling this from the main loop
 * (main.c's broadcast_can()) safe. */
int flexcan_transmit(uint32_t base, uint8_t mb, uint32_t id, int extended,
                      const uint8_t *data, uint8_t len);

/* Configures message buffer mb (0-15) to receive (CODE=EMPTY) if it
 * isn't already armed, then does a single non-blocking check: if a
 * frame has arrived (CODE=FULL), performs the real lock-safe read
 * sequence (C/S, then ID, then data, then TIMER to unlock - see file
 * header), re-arms the MB for the next frame, and returns 1 with
 * id_out, data_out, len_out and extended_out filled in. Returns 0 if
 * nothing new arrived - call this every main-loop iteration, it does
 * not block. */
int flexcan_receive_poll(uint32_t base, uint8_t mb, uint32_t *id_out,
                          int *extended_out, uint8_t *data_out, uint8_t *len_out);

#endif /* FLEXCAN_H */
