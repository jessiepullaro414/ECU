/*
 * emios.h - Enhanced Modular IO Subsystem (eMIOS) unified channel driver.
 *
 * Real, verified this session against the actual NXP MPC5606BK
 * Microcontroller Reference Manual, Rev. 2 (Chapter 27, section 27.4,
 * "Enhanced Modular IO Subsystem (eMIOS)") - base addresses, per-channel
 * register offsets, and the full EMIOSC[n] control-register bit layout
 * were all confirmed by rendering the real register diagrams (Figures
 * 27-8, 27-14, 27-15) to images and reading them directly, the same
 * "don't trust the raw text extraction" discipline used throughout this
 * whole project. The MODE field's OPWFMB encoding is real too (Table
 * 27-21), transcribed directly, with the one open flag noted below.
 */
#ifndef EMIOS_H
#define EMIOS_H

#include <stdint.h>

#define EMIOS0_BASE   0xC3FA0000u
#define EMIOS1_BASE   0xC3FA4000u

/* Global (per-module, not per-channel) registers - Table 27-9. */
#define EMIOS_MCR(base)    (*(volatile uint32_t *)((base) + 0x000u))
#define EMIOS_GFLAG(base)  (*(volatile uint32_t *)((base) + 0x004u))
#define EMIOS_OUDIS(base)  (*(volatile uint32_t *)((base) + 0x008u))
#define EMIOS_UCDIS(base)  (*(volatile uint32_t *)((base) + 0x00Cu))

/* EMIOSMCR fields. Same datasheet-bit -> standard-bit convention as the
 * channel registers below (standard = 31 - datasheet bit).
 *
 * GPREN IS NOT OPTIONAL. Its own field description is blunt about what
 * happens without it: "0 = Prescaler disabled (no clock) and prescaler
 * counter is cleared". Leave it clear and the eMIOS time base never
 * advances at all - every capture would read the same value and every
 * output channel would sit still, with no error anywhere to explain it.
 * This driver used to never touch MCR, so the time base was running on
 * nothing. */
#define EMIOS_MCR_GPREN       (1u << 26)  /* datasheet bit 5 */
#define EMIOS_MCR_GPRE_SHIFT  8           /* datasheet bits 16:23 */
#define EMIOS_MCR_GPRE_MASK   (0xFFu << EMIOS_MCR_GPRE_SHIFT)

/* Table 27-12: the divide ratio is GPRE + 1 (00000000 -> 1 ... 11111111
 * -> 256), so this macro takes the ratio the caller actually wants. */
#define EMIOS_MCR_GPRE(ratio) ((uint32_t)(((ratio) - 1u) & 0xFFu) << EMIOS_MCR_GPRE_SHIFT)

/* Per-channel base: UC[n] = module_base + 0x020 + n*0x020 - confirmed
 * from Table 27-10 (Channel[0] occupies 0x020-0x03F, Channel[1] occupies
 * 0x040-0x05F, etc). Valid n = 0..31 per eMIOS module. */
#define EMIOS_UC_BASE(base, n) ((base) + 0x020u + 0x020u * (uint32_t)(n))

/* Per-channel registers - offsets confirmed directly under each
 * channel's own base (Figures 27-12 through 27-16). Data/counter
 * registers are 16 bits wide but memory-mapped into the low half of a
 * 32-bit-aligned word (upper 16 bits read as 0) - real, not a typo;
 * confirmed in Figure 27-12/27-14's own bit diagrams. */
#define EMIOS_A(base, n)    (*(volatile uint32_t *)(EMIOS_UC_BASE(base, n) + 0x00u))
#define EMIOS_B(base, n)    (*(volatile uint32_t *)(EMIOS_UC_BASE(base, n) + 0x04u))
#define EMIOS_CNT(base, n)  (*(volatile uint32_t *)(EMIOS_UC_BASE(base, n) + 0x08u))
#define EMIOS_C(base, n)    (*(volatile uint32_t *)(EMIOS_UC_BASE(base, n) + 0x0Cu))
#define EMIOS_S(base, n)    (*(volatile uint32_t *)(EMIOS_UC_BASE(base, n) + 0x10u))

/* EMIOSC[n] (Channel Control Register) bit layout - Figure 27-15,
 * visually confirmed. Same reversed-bit-numbering caveat as siul2.h:
 * these macros are already converted to normal "1u << n" form. */
#define EMIOSC_FREN        (1u << 31)  /* rm bit 0 */
#define EMIOSC_ODIS        (1u << 30)  /* rm bit 1 */
#define EMIOSC_ODISSL_SHIFT 28         /* rm bits 2:3, 2 bits */
#define EMIOSC_ODISSL_MASK (3u << EMIOSC_ODISSL_SHIFT)
#define EMIOSC_UCPRE_SHIFT 26          /* rm bits 4:5, 2 bits - internal prescaler */
#define EMIOSC_UCPRE_MASK  (3u << EMIOSC_UCPRE_SHIFT)
#define EMIOSC_UCPREN      (1u << 25)  /* rm bit 6 */
#define EMIOSC_DMA         (1u << 24)  /* rm bit 7 */
#define EMIOSC_IF_SHIFT    19          /* rm bits 9:12, 4 bits - input filter */
#define EMIOSC_IF_MASK     (0xFu << EMIOSC_IF_SHIFT)
#define EMIOSC_FCK         (1u << 18)  /* rm bit 13 */
#define EMIOSC_FEN         (1u << 17)  /* rm bit 14 - FLAG enables an interrupt/DMA request */
#define EMIOSC_FORCMA      (1u << 13)  /* rm bit 18, write-only */
#define EMIOSC_FORCMB      (1u << 12)  /* rm bit 19, write-only */
#define EMIOSC_BSL_SHIFT   9           /* rm bits 21:22, 2 bits - counter bus select */
#define EMIOSC_BSL_MASK    (3u << EMIOSC_BSL_SHIFT)
#define EMIOSC_EDSEL       (1u << 8)   /* rm bit 23 */
#define EMIOSC_EDPOL       (1u << 7)   /* rm bit 24 */
#define EMIOSC_MODE_SHIFT  0           /* rm bits 25:31, 7 bits */
#define EMIOSC_MODE_MASK   (0x7Fu << EMIOSC_MODE_SHIFT)

/* EMIOSS[n] (Status Register) - Figure 27-16, confirmed. */
#define EMIOSS_OVR   (1u << 31)  /* w1c */
#define EMIOSS_OVFL  (1u << 16)  /* w1c */
#define EMIOSS_UCIN  (1u << 2)
#define EMIOSS_UCOUT (1u << 1)
#define EMIOSS_FLAG  (1u << 0)   /* w1c */

/* Channel mode selection (Table 27-21). OPWFMB confirmed real: encoding
 * "10110b0" where the datasheet's own footnote says the lowercase 'b'
 * bit "adjust[s] parameters for the mode of operation, refer to Section
 * 27.4.4.1.1" - that adjustable bit is written here as 0 (giving
 * 0b1011000 = 0x58), matching the base OPWFMB encoding, but the exact
 * effect of setting it to 1 instead was NOT chased down this session -
 * confirm against 27.4.4.1.1 before relying on it being right for this
 * board's specific PWM requirements. */
#define EMIOS_MODE_GPIO_IN     0x00u
#define EMIOS_MODE_GPIO_OUT    0x01u
#define EMIOS_MODE_SAIC        0x02u  /* Single Action Input Capture - real crank/cam capture mode */
#define EMIOS_MODE_SAOC        0x03u
#define EMIOS_MODE_IPWM        0x04u  /* Input Pulse Width Measurement */
#define EMIOS_MODE_IPM         0x05u
/* Counter bus modulus. MCB up mode counts 1..A1 inclusive, so with A1
 * set to 0xFFFF the counter takes 65535 distinct values, not 65536 -
 * one short of a clean 16-bit mask. Anything doing modular arithmetic
 * on bus timestamps has to use THIS, not 0xFFFF+1, or it gains a tick
 * every wrap. */
#define EMIOS_COUNTER_MODULUS  65535u

#define EMIOS_MODE_DAOC_FLAG_B 0x06u  /* Double Action Output Compare, FLAG on B match */
#define EMIOS_MODE_DAOC_BOTH   0x07u  /* ... FLAG on both matches */
#define EMIOS_MODE_MCB_UP      0x50u  /* Modulus Counter Buffered, up, internal clock */
#define EMIOS_MODE_OPWFMB      0x58u  /* Output Pulse Width and Frequency Modulation Buffered - see note above */

/*
 * Configure one eMIOS unified channel in OPWFMB mode - the real
 * injector/ignition firing mode: A2 holds the period, B2 holds the
 * pulse width, both double-buffered so a write mid-cycle doesn't
 * corrupt the pulse currently in progress (confirmed in the eMIOS
 * chapter's own OPWFMB description - the buffering is exactly why this
 * mode, not the simpler SAOC, is the real choice for injector/ignition
 * timing).
 *
 * base: EMIOS0_BASE or EMIOS1_BASE. channel: 0-31.
 * period_ticks / pulse_ticks: eMIOS timebase ticks, not microseconds -
 * converting from a real time value needs the channel's actual
 * prescaler + eMIOS clock configuration, which depends on the system
 * clock setup (not done this session - see main.c's hardware_init()
 * TODO). Do not treat these as microseconds without that conversion.
 *
 * IMPORTANT real gap: this does NOT configure the pin's SIUL2 PCR to
 * route it to this eMIOS channel - see siul2.h's own TODO. Without
 * that, this function safely configures the eMIOS peripheral's internal
 * state but the physical pin will not reflect it.
 */
void emios_init_opwfmb_channel(uint32_t base, uint8_t channel,
                                uint32_t period_ticks, uint32_t pulse_ticks);

/* Update just the pulse width of an already-running OPWFMB channel -
 * this is the real per-cylinder-event call (injection.c's
 * injection_arm_cylinder), not the one-time init above. */
void emios_set_pulse_width(uint32_t base, uint8_t channel, uint32_t pulse_ticks);

/* Brings up the module's shared time base: sets the global prescaler to
 * `divide_ratio` and enables it. Must be called before any capture or
 * output channel is useful, because without GPREN the counter has no
 * clock at all.
 *
 * Follows the sequence the reference manual gives for changing the
 * prescaler (Section 27.6.1): clear GPREN, write GPRE, then set GPREN -
 * rather than writing the whole register in one go, so the divider is
 * never changed underneath a running counter. */
void emios_init_timebase(uint32_t base, uint32_t divide_ratio);

/* THE COUNTER BUS. Every timing function on this part - input capture
 * and output compare alike - compares against a "counter bus", and a
 * counter bus is nothing but one channel running a modulus counter and
 * broadcasting its value. Counter bus A is the global one: Table 27-20
 * confirms BSL = 00 selects it on ALL channels, and the feature list
 * confirms it is driven by Unified Channel 23.
 *
 * Nothing drove it before this function existed, which meant the crank
 * and cam capture channels were timestamping against a bus that never
 * counted - every capture read the same value, so the measured tooth
 * period was always zero and the engine could never sync.
 *
 * Putting captures and output compares on the SAME bus is what makes
 * the whole scheme work: a timestamp taken at a crank tooth and a match
 * value scheduled for an injector are then in one shared time base, so
 * "fire N ticks after the tooth I just saw" is a plain addition.
 *
 * MCB counts 1..A1 inclusive (Section 27.4.4.1.1.8: "the MCB mode
 * counts between 0x1 and A1 register value"), so the modulus is A1
 * itself, NOT A1 + 1 - see EMIOS_COUNTER_MODULUS. The manual also warns
 * the counter must already be inside that range at mode entry, so this
 * seeds it rather than leaving it at its reset 0. */
void emios_init_counter_bus(uint32_t base, uint8_t channel);

/* Arms one output channel for angular event scheduling: DAOC against
 * counter bus A, EDPOL = 1 so an A match drives the pin HIGH and a B
 * match drives it LOW.
 *
 * Mode entry leaves the output at the COMPLEMENT of EDPOL - low - which
 * is the safe state on this board: injectors closed, coils not
 * charging. That is worth stating because the alternative would energise
 * every coil the instant the firmware initialised. */
void emios_init_output_channel(uint32_t base, uint8_t channel);

/* Schedules one pulse: output goes high at `on_ticks` and low at
 * `off_ticks`, both absolute values on counter bus A.
 *
 * DAOC is genuinely one-shot, which is exactly what an injector or a
 * coil wants and is why this is not the periodic OPWFMB the channels
 * were originally written for. Per Section 27.4.4.1.1.6 each comparator
 * "is enabled only after the transfer to A1/B1 occurs and is disabled
 * on the next match" - so writing here arms one pulse, it fires once,
 * and the hardware disarms itself until the next crank event writes
 * again. A free-running PWM would have kept pulsing regardless of where
 * the crank was. */
void emios_schedule_pulse(uint32_t base, uint8_t channel,
                          uint32_t on_ticks, uint32_t off_ticks);

/* Configure a channel for crank/cam edge capture (SAIC mode).
 * rising_edge: real, confirmed sense (Table 27-17's own field
 * description, not just its bit position) - 1 = trigger on a rising
 * edge, 0 = trigger on a falling edge. */
void emios_init_capture_channel(uint32_t base, uint8_t channel, int rising_edge);

/* Read the most recent capture timestamp and clear FLAG. */
uint32_t emios_read_capture(uint32_t base, uint8_t channel);

/* Real, side-effect-free check of a channel's own FLAG bit (does NOT
 * clear it, unlike emios_read_capture()) - needed because real eMIOS
 * interrupt vectors are shared two channels at a time (see intc.h's
 * file header: IRQ 141 fires for EITHER channel 0 or channel 1's real
 * FLAG), so a shared ISR must check each channel's own FLAG
 * individually to know which one(s) actually triggered before calling
 * emios_read_capture() on just the ones that did. */
int emios_flag_is_set(uint32_t base, uint8_t channel);

#endif /* EMIOS_H */
