/*
 * emios.c - see emios.h for what's verified vs. still open.
 */
#include "emios.h"

void emios_init_opwfmb_channel(uint32_t base, uint8_t channel,
                                uint32_t period_ticks, uint32_t pulse_ticks) {
    /* Real sequence per the reference manual's own rule (27.4.4.1.1):
     * go to GPIO mode first before changing MODE[0:6], never jump
     * directly between two non-GPIO modes. */
    EMIOS_C(base, channel) = (EMIOS_MODE_GPIO_OUT << EMIOSC_MODE_SHIFT);

    /* A2 = period, B2 = pulse width - OPWFMB's own real register
     * assignment (confirmed in the eMIOS chapter's OPWFMB description:
     * A sets the total period, B sets the active pulse width, measured
     * from the same edge). Both writes here land in the "2" (buffered)
     * side automatically per the mode's own double-buffering behavior -
     * EMIOS_A/EMIOS_B are the same register address regardless of which
     * physical buffer (1 or 2) the read/write actually reaches; that
     * routing is handled by the eMIOS hardware based on current mode,
     * not chosen by the address written to. */
    EMIOS_A(base, channel) = period_ticks;
    EMIOS_B(base, channel) = pulse_ticks;

    EMIOS_C(base, channel) = (EMIOS_MODE_OPWFMB << EMIOSC_MODE_SHIFT)
                            | EMIOSC_FEN;   /* FLAG enabled - lets main.c poll/interrupt on completion */
}

void emios_set_pulse_width(uint32_t base, uint8_t channel, uint32_t pulse_ticks) {
    /* Real, load-bearing detail: writing B (=B2, the buffered side)
     * while OPWFMB is already running does NOT glitch the pulse
     * currently in progress - it takes effect on the next period. This
     * is the entire reason OPWFMB (not the plain SAOC mode) is correct
     * for injector/ignition control: the firmware can safely arm the
     * NEXT event's width without touching the one currently firing. */
    EMIOS_B(base, channel) = pulse_ticks;
}

void emios_init_capture_channel(uint32_t base, uint8_t channel, int rising_edge) {
    EMIOS_C(base, channel) = (EMIOS_MODE_GPIO_IN << EMIOSC_MODE_SHIFT);

    uint32_t ctrl = (EMIOS_MODE_SAIC << EMIOSC_MODE_SHIFT) | EMIOSC_FEN;
    /* EDPOL selects which edge triggers capture in SAIC mode - real,
     * confirmed (Table 27-17's own explicit field description, not just
     * the bit position): "1 = Trigger on a rising edge, 0 = Trigger on
     * a falling edge". The rising_edge parameter's sense below was
     * already correct before this was confirmed - now verified, not
     * just assumed. */
    if (rising_edge) {
        ctrl |= EMIOSC_EDPOL;
    }
    EMIOS_C(base, channel) = ctrl;
}

uint32_t emios_read_capture(uint32_t base, uint8_t channel) {
    uint32_t value = EMIOS_A(base, channel);
    /* FLAG is w1c (write-1-to-clear) - confirmed in Figure 27-16. */
    EMIOS_S(base, channel) = EMIOSS_FLAG;
    return value;
}

int emios_flag_is_set(uint32_t base, uint8_t channel) {
    return (EMIOS_S(base, channel) & EMIOSS_FLAG) != 0u;
}

void emios_init_timebase(uint32_t base, uint32_t divide_ratio) {
    /* Documented order (Section 27.6.1): disable the prescaler, set the
     * ratio, then re-enable. Writing GPRE while GPREN is still set would
     * change the divider under a running counter. */
    EMIOS_MCR(base) &= ~EMIOS_MCR_GPREN;
    uint32_t mcr = EMIOS_MCR(base) & ~EMIOS_MCR_GPRE_MASK;
    EMIOS_MCR(base) = mcr | EMIOS_MCR_GPRE(divide_ratio);
    EMIOS_MCR(base) |= EMIOS_MCR_GPREN;
}

void emios_init_counter_bus(uint32_t base, uint8_t channel) {
    /* Same mode-transition rule the other init functions follow
     * (27.4.4.1.1.1): go via GPIO, never jump between two non-GPIO
     * modes. */
    EMIOS_C(base, channel) = (EMIOS_MODE_GPIO_OUT << EMIOSC_MODE_SHIFT);

    /* A1 is the modulus. The counter runs 1..A1 and restarts, so this
     * gives the widest time base the 16-bit registers can express. */
    EMIOS_A(base, channel) = EMIOS_COUNTER_MODULUS;

    /* "in order to avoid the counter wrap condition, make sure its value
     * is within the 0x1 to A1 register value range when the MCB mode is
     * entered" - the reset value is 0, which is outside that range, so
     * seed it explicitly rather than inherit a documented hazard. */
    EMIOS_CNT(base, channel) = 1u;

    /* MODE[6] = 0 selects the internal (prescaled) clock rather than the
     * channel's input pin, which is what EMIOS_MODE_MCB_UP encodes. No
     * FEN: this channel is a time base, not an event source, and an
     * interrupt every 65535 ticks would be pure overhead. */
    EMIOS_C(base, channel) = (EMIOS_MODE_MCB_UP << EMIOSC_MODE_SHIFT);
}

void emios_init_output_channel(uint32_t base, uint8_t channel) {
    EMIOS_C(base, channel) = (EMIOS_MODE_GPIO_OUT << EMIOSC_MODE_SHIFT);

    /* BSL = 00 selects counter bus A on every channel (Table 27-20),
     * which is the bus emios_init_counter_bus() drives and the same one
     * the capture channels timestamp against.
     *
     * EDPOL = 1 so an A match sets the pin and a B match clears it. Mode
     * entry drives the output to the complement of EDPOL, so the pin
     * starts LOW - injectors shut, coils cold.
     *
     * No FEN. Nothing needs an interrupt when a pulse completes: the
     * next crank tooth is what schedules the next one, and an ISR per
     * edge across sixteen channels would be real load for no benefit. */
    EMIOS_C(base, channel) = (EMIOS_MODE_DAOC_FLAG_B << EMIOSC_MODE_SHIFT)
                           | EMIOSC_EDPOL;
}

void emios_schedule_pulse(uint32_t base, uint8_t channel,
                          uint32_t on_ticks, uint32_t off_ticks) {
    /* Writing A2/B2 transfers to A1/B1 on the next system clock and
     * re-enables each comparator. Order matters only in that both should
     * be in place before either can match; at any real engine speed the
     * two writes are adjacent instructions and the earliest match is
     * many microseconds away. */
    EMIOS_A(base, channel) = on_ticks;
    EMIOS_B(base, channel) = off_ticks;
}
