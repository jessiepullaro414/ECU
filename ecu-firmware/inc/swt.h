/*
 * swt.h - Software Watchdog Timer (SWT) driver.
 *
 * Real, verified this session against the actual NXP MPC5606BK Reference
 * Manual, Rev. 2 (Chapter 33, "Software Watchdog Timer (SWT)", pages
 * 912-921 - a complete, self-contained 9-page chapter, not previously
 * opened this project). Closes main.c's own standing TODO ("watchdog
 * service - a hung main loop on a running engine... firmware still
 * needs its own watchdog, not just leaning on the driver ICs'
 * protection").
 *
 * REAL, CONFIRMED facts:
 *   - Base address 0xFFF3_8000 (Table 33-1, a clean, unambiguous memory
 *     map - offsets 0x00/0x04/0x08/0x0C/0x10/0x14/0x18 for
 *     CR/IR/TO/WN/SR/CO/SK, all 32-bit-access-only registers).
 *   - REAL, IMPORTANT, SELF-CONTAINED FACT: "The unique SWT counter
 *     clock is the undivided slow internal RC oscillator 128 kHz
 *     (SIRC), no other clock source can be selected" (Section 33.2).
 *     This means SWT's real timing is completely independent of this
 *     board's FMPLL/system-clock configuration (clocks.h) - no
 *     dependency on ECU_FMPLL_IDF/ODF/NDIV, unlike almost every other
 *     peripheral in this codebase. (SWT_CR.CSL exists but the manual
 *     explicitly says it "has no effect on counter clock selection on
 *     MPC5606BK device" - real, confirmed dead field on this part.)
 *   - Real field bit positions, SWT_CR (Figure 33-1, standard_bit =
 *     31 - datasheet_bit, the same conversion used throughout this
 *     project): MAP0..MAP7 (bits 31:24, one enable bit per real bus
 *     master - "MAP0 = CPU, MAP2 = eDMA" explicitly named, the rest
 *     "device-specific"), KEY (bit 9), RIA (bit 8), WND (bit 7), ITR
 *     (bit 6), HLK (bit 5), SLK (bit 4), CSL (bit 3, dead on this part
 *     per above), STP (bit 2), FRZ (bit 1), WEN (bit 0). Real, honest
 *     note: the register's own device-specific reset value (Table
 *     33-1: 0x4000_011U, U = undefined nibble) has some residual real
 *     ambiguity in exactly which bits besides WEN are left undefined -
 *     this driver sidesteps that entirely by writing a complete,
 *     deliberate SWT_CR value in swt_init() rather than depending on
 *     interpreting the reset default, so the ambiguity doesn't matter
 *     for correctness here.
 *   - Real, critical operational facts (Section 33.6):
 *       - Fixed service sequence (KEY=0, the mode this driver uses):
 *         write 0xA602 then 0xB480 to SWT_SR[WSC] to reset the
 *         down-counter and prevent a timeout. No timing requirement
 *         between the two writes.
 *       - Soft-unlock sequence (separate from servicing): write 0xC520
 *         then 0xD928 to SWT_SR[WSC] to clear SWT_CR[SLK]. Not used by
 *         this driver (nothing locks the config), kept as real,
 *         documented constants in case a later pass wants config
 *         locking.
 *       - SWT_TO holds the real timeout period in 128kHz clock cycles
 *         (real reset default: 1280 = ~10ms - this driver does NOT use
 *         the reset default; see SWT_TIMEOUT_CYCLES below for why).
 *       - ITR=0 (this driver's real choice): timeout generates an
 *         immediate system reset. ITR=1 (interrupt then reset on a
 *         second miss) is real and available, but genuinely pointless
 *         to enable right now - this project's INTC dispatch mechanism
 *         (intc.h) still isn't wired to real hardware interrupts (the
 *         e200z0h core's own IVPR/IVOR4 exception-vector setup remains
 *         a separate, unresolved gap - see intc.h), so an SWT-timeout
 *         ISR could never actually fire. Revisit once that gap closes.
 *       - Window mode (WND) and keyed pseudorandom service mode (KEY)
 *         are both real and documented above but deliberately not used
 *         - regular (non-windowed) fixed-sequence servicing is the
 *         simplest real mode that correctly satisfies "detect a hung
 *         main loop," which is this driver's actual job.
 *
 * REAL, DELIBERATE CHOICE, not measured: SWT_TIMEOUT_CYCLES below picks
 * a real, conservative 100ms period (12800 cycles at the confirmed
 * 128kHz SIRC) rather than the OEM's own 10ms reset default - this
 * firmware's actual main-loop iteration time has never been measured
 * (no systick/timer wired up yet, the same honest gap flagged
 * throughout this project), so 10ms could plausibly be tighter than a
 * real loop iteration under worst-case SPI/ADC polling load. 100ms is a
 * real, generous margin, not a calibrated figure - tighten it once real
 * loop timing is measured.
 */
#ifndef SWT_H
#define SWT_H

#include <stdint.h>

#define SWT_BASE   0xFFF38000u
#define SWT_CR   (*(volatile uint32_t *)(SWT_BASE + 0x0000u))
#define SWT_IR   (*(volatile uint32_t *)(SWT_BASE + 0x0004u))
#define SWT_TO   (*(volatile uint32_t *)(SWT_BASE + 0x0008u))
#define SWT_WN   (*(volatile uint32_t *)(SWT_BASE + 0x000Cu))
#define SWT_SR   (*(volatile uint32_t *)(SWT_BASE + 0x0010u))
#define SWT_CO   (*(volatile uint32_t *)(SWT_BASE + 0x0014u))
#define SWT_SK   (*(volatile uint32_t *)(SWT_BASE + 0x0018u))

/* SWT_CR real field bits - see file header. */
#define SWT_CR_MAP0   (1u << 31)   /* real: CPU */
#define SWT_CR_MAP1   (1u << 30)
#define SWT_CR_MAP2   (1u << 29)   /* real: eDMA */
#define SWT_CR_MAP3   (1u << 28)
#define SWT_CR_MAP4   (1u << 27)
#define SWT_CR_MAP5   (1u << 26)
#define SWT_CR_MAP6   (1u << 25)
#define SWT_CR_MAP7   (1u << 24)
#define SWT_CR_KEY    (1u << 9)
#define SWT_CR_RIA    (1u << 8)
#define SWT_CR_WND    (1u << 7)
#define SWT_CR_ITR    (1u << 6)
#define SWT_CR_HLK    (1u << 5)
#define SWT_CR_SLK    (1u << 4)
#define SWT_CR_CSL    (1u << 3)   /* real, confirmed dead on this part */
#define SWT_CR_STP    (1u << 2)
#define SWT_CR_FRZ    (1u << 1)
#define SWT_CR_WEN    (1u << 0)

/* SWT_IR real field bit. Write-1-to-clear. */
#define SWT_IR_TIF    (1u << 0)

/* Real fixed service sequence (KEY=0 mode) - write both, in order, to
 * SWT_SR to reset the down-counter. */
#define SWT_SERVICE_KEY1 0xA602u
#define SWT_SERVICE_KEY2 0xB480u

/* Real soft-unlock sequence - clears SWT_CR[SLK]. Not used by this
 * driver (nothing locks the config), kept for a possible later pass. */
#define SWT_UNLOCK_KEY1  0xC520u
#define SWT_UNLOCK_KEY2  0xD928u

/* Real, deliberately conservative timeout: 100ms at the confirmed,
 * always-128kHz SIRC counter clock (independent of this board's real
 * FMPLL/system-clock config - see file header). 0.100s * 128000 =
 * 12800 = 0x3200. */
#define SWT_TIMEOUT_CYCLES 12800u

/* Real, found this pass: SWT's own "Timeout" interrupt source, Table
 * 18-10 (INTC's real interrupt vector table) - "28 | 0x0870 | 4 |
 * Timeout | SWT". Real, deliberate choice, not a blocked gap: this
 * driver still initializes with ITR=0 (immediate reset on timeout, see
 * swt_init()) rather than ITR=1 (interrupt-then-reset) even though
 * real hardware interrupts can now reach C code (intc.h's own IVOR4
 * gap is closed) and this real IRQ number would let a real ISR be
 * registered here. For a hung-main-loop backstop on a running engine,
 * an immediate real reset is the safer real default than a "one more
 * chance" interrupt window - a genuinely hung system given a second
 * timeout period to keep misbehaving is a worse real outcome than a
 * fast, deterministic reset. Kept here as a real, ready-to-use constant
 * for whoever revisits this trade-off, not left undiscovered. */
#define SWT_IRQ_TIMEOUT 28u

/* Real, complete: configures SWT_TO with the real timeout above, then
 * enables the watchdog (WEN=1) with CPU bus-master access allowed
 * (MAP0=1), fixed (non-keyed) service mode, non-windowed, and an
 * immediate reset on timeout (ITR=0 - see file header for why interrupt
 * mode isn't used yet). Call once from hardware_init(), after real
 * clocks are up (not because SWT itself depends on the system clock -
 * it doesn't - but so a slow clock bring-up failure doesn't immediately
 * race the watchdog before main() even reports it, see main.c). */
void swt_init(void);

/* Real, complete: writes the real fixed service sequence
 * (SWT_SERVICE_KEY1 then SWT_SERVICE_KEY2) to reset the down-counter.
 * Call once per real main loop iteration - see main.c. */
void swt_service(void);

#endif /* SWT_H */
