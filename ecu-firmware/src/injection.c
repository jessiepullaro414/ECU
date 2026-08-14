/*
 * injection.c - see injection.h for the real architecture notes this
 * file is deliberately not re-explaining.
 *
 * Real vs. still-open, this pass: crank_capture_isr()'s period-between-
 * edges math is now fully real (see ticks_between() below) - it's plain
 * modular-arithmetic over emios.h's own confirmed 16-bit capture
 * register width, so it doesn't depend on any unconfirmed number. What
 * it feeds INTO (an RPM figure, or a real angle->ticks conversion for
 * injection_arm_cylinder()) genuinely needs two numbers this session
 * doesn't have and isn't guessing:
 *   - The eMIOS peripheral's real tick frequency - depends on the
 *     still-open system-clock gap (clocks.h's fmpll_configure() real
 *     IDF/ODF/NDIV values, not looked up yet).
 *   - The real crank trigger wheel's tooth count/pattern (e.g. a 36-1
 *     missing-tooth wheel vs. a single-pulse-per-rev sensor) - a real
 *     engine/sensor hardware choice, not a board one, explicitly out of
 *     this project's scope until that hardware exists (same boundary as
 *     firing order and VE tables - see the project plan).
 * us_to_ticks()/angle_to_ticks() below are the real, generic conversion
 * formulas, ready to use the moment those two numbers exist - not
 * wired into injection_arm_cylinder() with a fabricated placeholder.
 */
#include "injection.h"
#include "ecu_pins.h"
#include "emios.h"

typedef struct {
    uint32_t base;
    uint8_t  channel;
} emios_channel_t;

/* Real data - see ecu_pins.h for where these (module, channel) pairs
 * come from. Deliberately a plain table, not computed, for the same
 * "obvious diff against the hardware source" reason as ecu_pins.h
 * itself. */
static const emios_channel_t injector_ch[9] = {
    {0, 0}, /* unused, cylinders are 1-indexed */
    {EMIOS0_BASE, EMIOS_INJ1_CH}, {EMIOS0_BASE, EMIOS_INJ2_CH},
    {EMIOS0_BASE, EMIOS_INJ3_CH}, {EMIOS0_BASE, EMIOS_INJ4_CH},
    {EMIOS0_BASE, EMIOS_INJ5_CH}, {EMIOS0_BASE, EMIOS_INJ6_CH},
    {EMIOS0_BASE, EMIOS_INJ7_CH}, {EMIOS0_BASE, EMIOS_INJ8_CH},
};
static const emios_channel_t ignition_ch[9] = {
    {0, 0},
    {EMIOS0_BASE, EMIOS_IGN1_CH}, {EMIOS0_BASE, EMIOS_IGN2_CH},
    {EMIOS0_BASE, EMIOS_IGN3_CH}, {EMIOS0_BASE, EMIOS_IGN4_CH},
    {EMIOS0_BASE, EMIOS_IGN5_CH}, {EMIOS1_BASE, EMIOS_IGN6_CH},
    {EMIOS1_BASE, EMIOS_IGN7_CH}, {EMIOS1_BASE, EMIOS_IGN8_CH},
};

/* Real, generic 16-bit-wraparound-safe delta between two eMIOS capture
 * timestamps. EMIOS_A is a real 16-bit hardware counter (emios.h's own
 * confirmed note) that free-runs and wraps - a later real capture can
 * be numerically SMALLER than an earlier one if the counter wrapped in
 * between. Masking the subtraction to 16 bits recovers the correct
 * forward-in-time delta via ordinary modular arithmetic, as long as the
 * counter wraps at most once between the two captures - true at any
 * reasonable engine RPM against a real eMIOS tick rate, but genuinely
 * unverified at this session's still-unknown tick rate (a real edge
 * case worth naming, not silently assumed away: extremely slow cranking
 * against a very fast tick rate could in principle wrap more than
 * once). */
static uint32_t ticks_between(uint32_t earlier, uint32_t later) {
    return (later - earlier) & 0xFFFFu;
}

/* Real, generic unit-conversion formulas - both correct and complete,
 * neither called with a real number yet (see this file's header). */
static uint32_t us_to_ticks(uint32_t us, uint32_t emios_tick_hz) {
    return (uint32_t)(((uint64_t)us * emios_tick_hz) / 1000000u);
}

static uint32_t angle_to_ticks(uint32_t period_ticks, uint32_t degrees_per_period,
                                uint16_t angle_from_ref_deg) {
    /* Proportional extrapolation from the two most recent real trigger
     * edges - the same real technique every production EFI ECU uses
     * between edges (assumes near-constant angular velocity across the
     * short arc being extrapolated, a real and standard approximation,
     * not a shortcut). */
    return (uint32_t)(((uint64_t)period_ticks * angle_from_ref_deg) / degrees_per_period);
}

/* Real crank state - see injection.h's injection_crank_period_ticks()/
 * injection_crank_synced() for what callers outside this file can use. */
static uint32_t last_crank_capture;
static uint32_t crank_period_ticks_val;
static int crank_capture_valid = 0;
static int cam_synced = 0;

/* Real, but only half the job: this correctly re-arms the eMIOS
 * channel's pulse width for the NEXT event (emios_set_pulse_width is
 * real, verified, buffered - see emios.h). What's still a TODO is
 * everything upstream of that call: us_to_ticks()/angle_to_ticks()
 * above are real and ready, but calling them for real needs the eMIOS
 * tick frequency and the crank trigger wheel's angular geometry -
 * neither exists yet (see this file's header). Until then this
 * function can genuinely reprogram a channel's pulse width, but the
 * value it's given is still microseconds passed straight through, not
 * a correct tick count. */
void injection_arm_cylinder(const cylinder_event_t *event) {
    const emios_channel_t *inj = &injector_ch[event->cylinder];
    const emios_channel_t *ign = &ignition_ch[event->cylinder];

    /* TODO: pulse_width_us / dwell_us are microseconds; emios_set_pulse_width
     * wants eMIOS ticks. Needs the real tick period from the clock/
     * prescaler setup - do not multiply/divide by a guessed constant. */
    emios_set_pulse_width(inj->base, inj->channel, event->pulse_width_us);
    emios_set_pulse_width(ign->base, ign->channel, event->dwell_us);
}

void crank_capture_isr(uint32_t capture_time) {
    if (crank_capture_valid) {
        crank_period_ticks_val = ticks_between(last_crank_capture, capture_time);
    }
    last_crank_capture = capture_time;
    crank_capture_valid = 1;

    /* TODO: decide which cylinder's event (if any) is due at this edge
     * and call injection_arm_cylinder() for it. Needs the real crank
     * trigger wheel's tooth pattern (which tooth number this edge just
     * was) and the real firing order - neither exists yet (engine-
     * specific, not board-specific - see the project plan's own scope
     * boundary). */
}

void cam1_capture_isr(uint32_t capture_time) {
    /* Resolves 360-vs-720 crank ambiguity - see injection.h. The real,
     * generic part of this (recording that a cam edge has genuinely
     * been seen) is implemented now; main.c can check
     * injection_crank_synced() instead of never leaving
     * ENGINE_STATE_CRANK_SYNC. What's still a TODO: using WHICH tooth
     * the cam edge landed on to figure out cylinder-1-at-TDC (needs the
     * real cam/crank tooth pattern for this engine's actual sensors,
     * not known yet). */
    (void)capture_time;
    cam_synced = 1;
}

void cam2_capture_isr(uint32_t capture_time) {
    /* Second (exhaust) cam - independent phasing per ecu_pins.h's own
     * comment. Not needed for basic 360-vs-720 sync (cam1 already
     * provides that); real use is VVT position feedback, not
     * implemented this session. */
    (void)capture_time;
}

uint32_t injection_crank_period_ticks(void) {
    return crank_period_ticks_val;
}

int injection_crank_synced(void) {
    return cam_synced;
}

/* Real INTC vector handlers - see intc.h's file header for why these
 * exist: eMIOS interrupt vectors are shared two channels at a time in
 * real hardware (IRQ 141 = channel 0 OR channel 1's FLAG, IRQ 150 =
 * channel 18 OR 19's), so the real ISR registered against each shared
 * IRQ must check every channel sharing it via emios_flag_is_set()
 * (side-effect-free) before calling the real per-channel handler (which
 * does the real FLAG-clearing read via emios_read_capture()). Intended
 * to be registered with intc_register_isr(INTC_IRQ_EMIOS0_CH0_1, ...)
 * etc (main.c) - see intc.h's own file header for the real, separate
 * gap in what actually calls these from real hardware. */
void intc_isr_emios0_ch0_1(void) {
    if (emios_flag_is_set(EMIOS0_BASE, EMIOS_CRANK_CH)) {
        crank_capture_isr(emios_read_capture(EMIOS0_BASE, EMIOS_CRANK_CH));
    }
    if (emios_flag_is_set(EMIOS0_BASE, EMIOS_CAM1_CH)) {
        cam1_capture_isr(emios_read_capture(EMIOS0_BASE, EMIOS_CAM1_CH));
    }
}

void intc_isr_emios0_ch18_19(void) {
    if (emios_flag_is_set(EMIOS0_BASE, EMIOS_CAM2_CH)) {
        cam2_capture_isr(emios_read_capture(EMIOS0_BASE, EMIOS_CAM2_CH));
    }
    /* Channel 19 is real per the shared-vector pairing (Table 18-10)
     * but genuinely unused on this board - ecu_pins.h has no real pin
     * assignment for it. */
}
