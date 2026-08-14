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
