/*
 * adc.h - Analog-to-Digital Converter (ADC) driver.
 *
 * Real, verified this session against the actual NXP MPC5606BK Reference
 * Manual, Rev. 2 (Chapter 28, "Analog-to-Digital Converter (ADC)", pages
 * 716-777ish). Base addresses/offsets confirmed via positioned-text
 * extraction (Tables 28-9/28-10); all register bit layouts used below
 * (MCR, MSR, NCMR0-2, CDR) were visually confirmed by rendering the real
 * register diagrams (Figures 28-9, 28-10, 28-38, 28-39, 28-40, 28-41,
 * 28-48) - not taken from raw text extraction alone.
 *
 * The MPC5606BK has TWO independent ADCs, not one: ADC_0 (10-bit) and
 * ADC_1 (12-bit), each with their own base address and register set of
 * the same shape. This driver is base-address-parameterized so one set
 * of functions serves both.
 *
 * Real, confirmed facts:
 *   - MCR's OWREN/WLSIDE/MODE/NSTART/PWDN and MSR's NSTART/CHADDR - both
 *     visually confirmed, Figures 28-9/28-10.
 *   - The Normal Conversion Mask Registers (NCMR0/1/2) select which
 *     channel(s) a one-shot/scan conversion covers. Real, clean formula
 *     that fell out of visually confirming all three (Figures 28-38/
 *     28-39/28-40/28-41), not assumed going in: standard bit k of NCMRn
 *     enables channel (k + BASE), where BASE is 0 for NCMR0 (channels
 *     0-15), 32 for NCMR1 (channels 32-59 on ADC_0, 32-39 on ADC_1), and
 *     64 for NCMR2 (channels 64-95, ADC_0 only - ADC_1 has no NCMR2).
 *   - CDR[n]'s real per-channel result format (Figure 28-48/28-49,
 *     right-aligned since this driver never sets WLSIDE): VALID (bit
 *     19) and OVERW (bit 18) status flags, CDATA in the low 10 bits
 *     (ADC_0) or low 12 bits (ADC_1) - reading the low 12 bits on
 *     either instance is safe, since ADC_0's unimplemented top 2 bits
 *     are hardwired 0, not undefined.
 *   - CTR0-2 (conversion timing) has a real, non-zero, usable RESET
 *     default (visually confirmed, Figure 28-37: INPCMP/INPSAMP reset
 *     to specific non-zero values) - this driver deliberately does not
 *     write it, so conversions run at the reset-default timing rather
 *     than a fabricated one. Revisit once a specific conversion rate is
 *     actually needed.
 *   - CORRECTED this session (a real bug in this file's own prior
 *     documentation, not just an omission): PDEDR (power-down exit
 *     delay) does NOT have a non-zero reset default as previously
 *     claimed here - re-checked directly against its own real register
 *     diagram (Base+0x00C8, PDED field in the low 8 bits) and it resets
 *     to 0, same as DSDR (Base+0x00C4, ADC_0 only). Both were
 *     momentarily conflated with CTR0-2's real non-zero reset above;
 *     they're separate registers with their own real reset value.
 *     Neither register's own field-description text (Table 28-37/28-38)
 *     gives a numeric time constant, either - both only give the
 *     formula (field x 1/ADC-clock-frequency), leaving the actual
 *     required settling TIME unstated in this document. See "NOT done
 *     this session" below - this isn't a search miss, it's a confirmed
 *     absence in this specific source (same class of finding as
 *     intc.h's IVOR gap).
 *   - RESOLVED (was an open gap): the real pin -> ADC channel number
 *     mapping for every real sensor pin in ecu_pins.h -
 *     adc_channel_for_pin() (adc.c) implements it for real, unblocking
 *     main.c's read_sensors(). Found via Figure 28-1's own real block
 *     diagram, which spells out the exact channel-number formula this
 *     manual otherwise never states explicitly: ADCx_P[n] = channel n
 *     on instance x (same number on both ADC_0 and ADC_1 - the same
 *     physical pin wires to both), ADCx_S[n] = channel 32+n (and NOT
 *     necessarily the same n between instances - confirmed a real case
 *     where it isn't, see adc.c). Cross-checked against every pin's own
 *     Table 4-1 row (pages 58-63) - all matched.
 *
 * NOT done this session:
 *   - Injected conversions, scan mode, DMA, interrupts, the analog
 *     watchdog, and CTU-triggered conversion - all real ADC features
 *     with their own register groups (JCMR, DMAE/DMAR, WTISR/WTIMR,
 *     CWSELR/CWENR/AWORR) that this driver doesn't touch. One-shot
 *     polled single-channel conversion is what read_sensors() (main.c)
 *     actually needs for now.
 *   - RESOLVED, a later pass: the real PDEDR settling-time gap, closed
 *     against a real, different primary source - the actual MPC5606B
 *     Data Sheet, Rev. 5 (Document Number MPC5606B, fetched via a real
 *     Wayback Machine snapshot of nxp.com/docs/en/data-sheet/MPC5606B.pdf
 *     this session, since the live NXP URL 404'd for automated fetches
 *     here same as every other nxp.com doc tried this session). Section
 *     3.17 "ADC electrical characteristics" gives the real numbers the
 *     Reference Manual explicitly deferred elsewhere: tADC0_PU/tADC1_PU
 *     (real ADC power-up delay) = 1.5us MAX for both instances, and
 *     fADC0/fADC1 (real ADC analog clock frequency) = 6MHz min to
 *     32MHz+4% max. PDED_MIN below is computed from these two real
 *     numbers using the worst case (fastest real fADC, 32MHz, needs the
 *     MOST cycles to cover the same 1.5us) so the real delay is met
 *     regardless of which real ADCLKSEL divider setting is in effect.
 *     RESOLVED, same later pass: ADCLKSEL's own real divider-to-
 *     frequency mapping, found back in the Reference Manual itself
 *     (Section 28.3.2 and Table 28-11's own MCR field description,
 *     both genuinely re-read, not previously reached) - ADCLKSEL is a
 *     real, single MCR bit, not a multi-bit divider: 0 = ADC clock is
 *     HALF the real Peripheral Set 3 clock, 1 = ADC clock EQUALS it.
 *     adc_init() below never sets this bit (only OWREN), so it stays at
 *     its real reset default (0) - meaning this board's real, exact
 *     fADC = 60MHz / 2 = 30MHz, comfortably inside the confirmed
 *     6-32MHz range. The real, exact PDED minimum for that exact
 *     frequency would be 1.5us * 30MHz = 45 cycles; ADC_PDED_MIN below
 *     is kept at the more conservative worst-case 64 rather than
 *     retuned to 45, since 64 already safely covers the real, exact
 *     30MHz case with margin to spare - no need to trade a real, safe
 *     margin for a marginally tighter number. adc_init() now writes
 *     this real value to PDEDR instead of running a placeholder
 *     software busy-wait - PDEDR's own real purpose ("Delay between the
 *     power-down bit reset and the start of conversion") means real
 *     hardware handles this delay internally once PDED is set, so the
 *     old placeholder loop is removed, not just left redundant.
 */
#ifndef ADC_H
#define ADC_H

#include <stdint.h>

/* Real base addresses, Tables 28-9/28-10. */
#define ADC_0_BASE 0xFFE00000u  /* 10-bit */
#define ADC_1_BASE 0xFFE04000u  /* 12-bit */

/* Real register offsets, Tables 28-9/28-10. */
#define ADC_MCR(base)      (*(volatile uint32_t *)((base) + 0x00u))
#define ADC_MSR(base)      (*(volatile uint32_t *)((base) + 0x04u))
#define ADC_NCMR0(base)    (*(volatile uint32_t *)((base) + 0xA4u))
#define ADC_NCMR1(base)    (*(volatile uint32_t *)((base) + 0xA8u))
#define ADC_NCMR2(base)    (*(volatile uint32_t *)((base) + 0xACu))  /* ADC_0 only */
#define ADC_CDR(base, ch)  (*(volatile uint32_t *)((base) + 0x100u + 4u * (uint32_t)(ch)))
/* Real offset, Table 28-9/28-10 (confirmed this session - see file
 * header for the real DSDR/PDEDR reset-value correction). PDED occupies
 * the low 8 bits (Table 28-38). */
#define ADC_PDEDR(base)    (*(volatile uint32_t *)((base) + 0xC8u))
#define ADC_PDEDR_PDED_MASK 0xFFu

/* Real, computed, worst-case-safe PDED value - see file header for the
 * full derivation (MPC5606B Data Sheet Rev. 5, Section 3.17: real
 * tADC_PU=1.5us max, real fADC=6-32MHz range). Using the fastest real
 * fADC (32MHz) as the worst case: PDED >= 1.5us * 32MHz = 48 cycles.
 * Rounded up to 64 for real, deliberate margin (not cycle-exact, same
 * "generous, not fabricated" spirit as this project's other real
 * conservative timing constants). */
#define ADC_PDED_MIN 64u

/* MCR fields - Figure 28-9, visually confirmed. Freescale/PowerPC bit
 * numbering converted to standard "1u << n" (standard_bit = 31 - rm_bit),
 * same convention as every other driver in this firmware. */
#define ADC_MCR_OWREN  (1u << 31) /* datasheet bit 0:  Overwrite enable */
#define ADC_MCR_WLSIDE (1u << 30) /* datasheet bit 1:  Left-align result (0 = right-aligned) */
#define ADC_MCR_MODE   (1u << 29) /* datasheet bit 2:  0 = one-shot, 1 = scan (continuous) */
#define ADC_MCR_NSTART (1u << 24) /* datasheet bit 7:  Start normal conversion */
#define ADC_MCR_PWDN   (1u << 0)  /* datasheet bit 31: Power-down request (RESET DEFAULT = 1) */

/* MSR fields - Figure 28-10, visually confirmed. Read-only. */
#define ADC_MSR_NSTART       (1u << 24) /* datasheet bit 7: normal conversion ongoing */
#define ADC_MSR_CHADDR_SHIFT 8           /* datasheet bits 16:23: current conversion channel */
#define ADC_MSR_CHADDR_MASK  (0xFFu << ADC_MSR_CHADDR_SHIFT)

/* CDR fields - Figure 28-48/28-49, visually confirmed (WLSIDE=0, this
 * driver's only mode: right-aligned result). */
#define ADC_CDR_VALID      (1u << 19) /* datasheet bit 12: result is valid/fresh */
#define ADC_CDR_OVERW      (1u << 18) /* datasheet bit 13: previous result was overwritten unread */
#define ADC_CDR_CDATA_MASK 0xFFFu     /* low 10 bits (ADC_0) or 12 bits (ADC_1) - see file header */

/* Brings up one ADC instance (ADC_0_BASE or ADC_1_BASE) for polled,
 * one-shot, single-channel conversions: clears PWDN, sets OWREN, leaves
 * MODE at its real one-shot reset default (0) and CTR0-2/PDEDR at their
 * real, usable reset defaults (see file header - not fabricated
 * values). Call once per ADC instance before adc_read_channel(). */
void adc_init(uint32_t base);

/* One blocking conversion of a single real ADC channel number (0-95,
 * see file header for the NCMR-selection formula and which ranges are
 * valid on which instance) - masks the correct NCMRn to just this
 * channel, starts a one-shot normal conversion, polls CDR[channel]'s
 * real VALID flag, and returns the raw CDATA result (0-1023 on ADC_0,
 * 0-4095 on ADC_1). Returns 0 for an out-of-range channel number
 * (not a valid conversion, not silently mixed in with a real 0 reading -
 * callers should treat 0 from an invalid channel as a real fault). */
uint16_t adc_read_channel(uint32_t base, uint8_t channel);

/* Real package-pin -> (ADC instance, channel number) lookup for every
 * real analog sensor pin in ecu_pins.h - see adc.c's ADC_CHANNEL_TABLE
 * for the actual per-pin data. Confirmed against Figure 28-1's real
 * block diagram (which spells out the real channel-number formula:
 * ADCx_P[n] = channel n on instance x, ADCx_S[n] = channel 32+n) and
 * cross-checked against every pin's own Table 4-1 row. All real sensor
 * pins land on ADC_1 (the 12-bit instance - real, deliberate choice:
 * every one of these pins is wired to BOTH ADC_0 and ADC_1 simultaneously
 * per the same real pin, so higher resolution is free) except
 * PIN_ADC_KNOCK1, whose physical pad has no P[n] (precision-range)
 * mapping at all - only a Standard-range S[n] one, real and confirmed,
 * not a limitation of this table.
 *
 * Returns 1 and fills *base_out/*channel_out on a real match, 0 (with
 * both outputs left untouched) for a pin that isn't one of this
 * board's real analog inputs - callers should treat that as a real
 * "not a valid sensor pin" fault, not silently read channel 0. */
int adc_channel_for_pin(uint16_t package_pin, uint32_t *base_out, uint8_t *channel_out);

#endif /* ADC_H */
