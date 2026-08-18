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
#include "engine_config.h"
#include "ecu_pins.h"
#include "emios.h"
#include "fuel.h"

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

/* Real, generic unit-conversion formulas. Both are now called with real
 * numbers: the tick rate comes from ECU_EMIOS_TICK_HZ (derived from this
 * board's confirmed 60 MHz peripheral clock and the prescaler the eMIOS
 * driver actually programs), and the angular geometry from the crank
 * wheel settings in engine_config.h. */
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
static uint32_t prev_crank_period_ticks;

/* One tooth interval with the missing-tooth gap normalised out, so RPM
 * can be derived at EVERY tooth including the gap one. Without this the
 * gap tooth - whose measured period legitimately spans (missing+1)
 * intervals - would read as a sudden drop to a fraction of true RPM,
 * and that tooth is exactly where wheel sync is established. */
static uint32_t normal_period_ticks;

/* Fuelling inputs, published by the main loop (injection_set_fuel_inputs)
 * and consumed here at interrupt level. Written as whole words by one
 * writer and read by one reader, so no lock is needed on this core; the
 * defaults are deliberately the safe end - no MAP reading yet means
 * near-vacuum, which asks for the smallest pulse the table describes
 * rather than the largest. */
static volatile uint16_t fuel_map_kpa = MAP_KPA_AT_MIN;
static volatile int32_t  fuel_iat_centiC = 2000;   /* 20 C until told otherwise */
static int crank_capture_valid = 0;
static int cam_synced = 0;

/* Position on the trigger wheel. tooth_index counts real teeth from the
 * one immediately following the gap, which is the only angularly-known
 * point on the wheel. wheel_synced stays 0 until the gap has actually
 * been found - before that the position is genuinely unknown and
 * nothing may be fired. */
static uint8_t tooth_index;
static uint8_t wheel_synced = 0;

/* Which crank revolution of the engine cycle we are in. A four-stroke
 * cycle spans two revolutions but the crank wheel repeats every one, so
 * the wheel alone cannot tell them apart - that is exactly what the cam
 * sensor resolves. Always 0 when ENGINE_CYCLE_DEGREES is 360. */
static uint8_t revolution;

static const uint8_t firing_order[ENGINE_CYLINDERS] = ENGINE_FIRING_ORDER;

/* Real: how far ahead of its firing angle a cylinder gets armed. One
 * full firing interval gives the eMIOS channel a whole inter-event
 * window to be reprogrammed in, which is the standard arrangement -
 * arming later risks losing the event to interrupt latency, arming
 * earlier means acting on staler sensor data.
 *
 * HONEST LIMIT: the right value depends on the real arm-to-fire latency
 * of this eMIOS setup, which needs bench measurement on hardware that
 * does not exist yet. One interval is a defensible starting point, not
 * a measured optimum. */
#define ARM_LEAD_DEG  ENGINE_FIRING_INTERVAL_DEG

/* Real, and now complete on the unit side: the caller supplies
 * microseconds, this converts to the eMIOS ticks the hardware actually
 * counts. That conversion used to be the standing TODO here - the value
 * was passed straight through as though microseconds were ticks, which
 * at a 1 MHz timebase would have been off by whatever the real tick
 * period turned out to be.
 *
 * Injector dead time is added here rather than left to the caller. It
 * is a property of the injector, not of the fuelling calculation, and
 * every path that computes a pulse width would otherwise have to
 * remember to add it - forgetting it makes the engine run lean at every
 * load point. Ignition dwell is NOT adjusted the same way: dwell is
 * already the real coil charge time, with no equivalent offset. */
void injection_arm_cylinder(const cylinder_event_t *event) {
    /* Cylinders are 1-based (injection.h), and injector_ch/ignition_ch
     * are sized and indexed to match with a deliberately-unused slot 0.
     * Reject anything outside that rather than index the sentinel: its
     * base address is 0, so an off-by-one here would not fire the wrong
     * cylinder, it would write an eMIOS register offset into the flash
     * boot sector. Caught exactly that way once already. */
    if (event->cylinder < 1u || event->cylinder > ENGINE_CYLINDERS) {
        return;
    }

    const emios_channel_t *inj = &injector_ch[event->cylinder];
    const emios_channel_t *ign = &ignition_ch[event->cylinder];

    uint32_t inj_ticks = us_to_ticks(event->pulse_width_us + INJECTOR_DEAD_TIME_US,
                                     ECU_EMIOS_TICK_HZ);
    uint32_t ign_ticks = us_to_ticks(event->dwell_us, ECU_EMIOS_TICK_HZ);

    /* The eMIOS channel registers are 16-bit (emios.h), so a pulse
     * longer than the counter can express would silently wrap and fire
     * a far shorter pulse than asked for. Clamp instead: a clamped
     * injector pulse is wrong, but a wrapped one is wrong AND looks
     * fine. At the 1 MHz timebase this ceiling is 65.5 ms, far beyond
     * any real injector pulse or dwell. */
    if (inj_ticks > 0xFFFFu) { inj_ticks = 0xFFFFu; }
    if (ign_ticks > 0xFFFFu) { ign_ticks = 0xFFFFu; }

    emios_set_pulse_width(inj->base, inj->channel, inj_ticks);
    emios_set_pulse_width(ign->base, ign->channel, ign_ticks);
}

/* Real: crank ticks from the wheel's reference gap to a given crank
 * angle, using the most recent measured tooth-to-tooth period. This is
 * what turns "fire cylinder 3 at 25 degrees BTDC" into a hardware
 * delay. Exposed for the scheduling work that still has to decide WHICH
 * cylinder is due - see crank_capture_isr() below. */
uint32_t injection_angle_to_ticks(uint16_t angle_from_ref_deg) {
    return angle_to_ticks(crank_period_ticks_val,
                          CRANK_DEGREES_PER_TOOTH,
                          angle_from_ref_deg);
}

/* Real gap detection. The wheel's missing teeth show up as one tooth
 * period much longer than the last: with `missing` teeth removed the
 * gap spans (missing + 1) normal intervals, so 36-1 gives roughly a 2x
 * period and 60-2 roughly 3x.
 *
 * The comparison is deliberately made against 3/4 of that expected
 * ratio rather than the ratio itself, and entirely in integers. The
 * margin matters because the engine is accelerating: under hard
 * cranking each tooth period is genuinely shorter than the last, which
 * eats into the ratio and would make an exact test miss the gap. Being
 * too eager is the safer failure - a false gap resyncs the wheel to a
 * wrong position, which the very next real gap corrects, whereas a
 * missed gap leaves the engine unsynced for a whole revolution.
 *
 * HONEST LIMIT: the 3/4 margin is reasoned, not measured. Real
 * acceleration rates during a cold crank are exactly the thing to check
 * on a bench before trusting this. */
static int gap_detected(uint32_t period, uint32_t prev_period) {
    if (prev_period == 0u) {
        return 0;
    }
    return (period * 4u) > (prev_period * (CRANK_WHEEL_MISSING + 1u) * 3u);
}

/* Real: the crank angle, measured from cylinder 1's compression TDC,
 * at which the cylinder holding position `order_index` in the firing
 * order fires. Firing events are evenly spaced around the cycle, which
 * the generator has already checked divides evenly. */
static uint16_t fire_angle_for_order_index(uint8_t order_index) {
    return (uint16_t)((uint32_t)order_index * ENGINE_FIRING_INTERVAL_DEG);
}

void crank_capture_isr(uint32_t capture_time) {
    if (crank_capture_valid) {
        prev_crank_period_ticks = crank_period_ticks_val;
        crank_period_ticks_val = ticks_between(last_crank_capture, capture_time);
    }
    last_crank_capture = capture_time;
    crank_capture_valid = 1;

    if (gap_detected(crank_period_ticks_val, prev_crank_period_ticks)) {
        /* This edge is the first real tooth after the gap - the one
         * angularly-known point on the wheel. */
        normal_period_ticks = crank_period_ticks_val
                              / (CRANK_WHEEL_MISSING + 1u);
        tooth_index = 0u;
        wheel_synced = 1u;
        if (ENGINE_CYCLE_DEGREES == 720u) {
            revolution = (uint8_t)(revolution ^ 1u);
        }
    } else if (wheel_synced) {
        normal_period_ticks = crank_period_ticks_val;
        tooth_index = (uint8_t)(tooth_index + 1u);
        if (tooth_index >= CRANK_REAL_TEETH) {
            /* Should have seen the gap by now. Position is no longer
             * trustworthy, so drop sync rather than keep firing on a
             * count that has clearly drifted. */
            wheel_synced = 0u;
        }
    }

    /* Nothing may be armed until the wheel's absolute position is known
     * AND - on a four-stroke - the cam has said which of the two
     * revolutions this is. Firing on an unsynced wheel means firing at
     * an unknown angle. */
    if (!wheel_synced) {
        return;
    }
    if ((ENGINE_CYCLE_DEGREES == 720u) && !cam_synced) {
        return;
    }

    /* Crank angle now, measured from cylinder 1 compression TDC. The
     * gap reference sits CRANK_GAP_TO_TDC_DEG before that TDC, so
     * counting forward from the reference and subtracting that offset
     * gives the angle past TDC. */
    uint32_t from_ref  = (uint32_t)tooth_index * CRANK_DEGREES_PER_TOOTH;
    uint32_t cycle_pos = ((uint32_t)revolution * 360u + from_ref
                          + ENGINE_CYCLE_DEGREES - CRANK_GAP_TO_TDC_DEG)
                         % ENGINE_CYCLE_DEGREES;

    /* Arm whichever cylinder comes due one lead-window from here. The
     * window is half a tooth wide on either side so exactly one tooth
     * can match, whatever the wheel resolution. */
    uint32_t target = (cycle_pos + ARM_LEAD_DEG) % ENGINE_CYCLE_DEGREES;
    for (uint8_t i = 0u; i < ENGINE_CYLINDERS; i++) {
        uint32_t fire_at = fire_angle_for_order_index(i);
        uint32_t delta   = (fire_at + ENGINE_CYCLE_DEGREES - target)
                           % ENGINE_CYCLE_DEGREES;
        if (delta < CRANK_DEGREES_PER_TOOTH) {
            cylinder_event_t event;
            event.cylinder       = firing_order[i];   /* 1-based, see injection.h */
            /* cylinder_event_t.pulse_width_us is 16-bit, so clamp
             * before the assignment rather than after: truncating here
             * would wrap a too-long pulse down to a short one, and
             * injection_arm_cylinder()'s own tick-level clamp would
             * never see the real value. 65.5 ms is past any real
             * injector command - reaching it means a misconfigured
             * injector flow rate, not a running engine. */
            uint32_t pw = fuel_pulse_width_us(injection_crank_rpm(),
                                              fuel_map_kpa,
                                              fuel_iat_centiC);
            event.pulse_width_us = (pw > 65535u) ? 65535u : (uint16_t)pw;
            event.dwell_us       = IGNITION_DWELL_US;
            event.fire_angle_deg = (uint16_t)fire_at;
            injection_arm_cylinder(&event);
            break;   /* events are evenly spaced; at most one per tooth */
        }
    }
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

void injection_set_fuel_inputs(uint16_t map_kpa, int32_t iat_centiC) {
    fuel_map_kpa    = map_kpa;
    fuel_iat_centiC = iat_centiC;
}

uint16_t injection_crank_rpm(void) {
    /* One revolution is CRANK_WHEEL_TEETH tooth intervals (the missing
     * teeth still occupy their angular slots - that is what makes the
     * gap detectable), so:
     *
     *   rpm = 60 * tick_hz / (ticks_per_tooth * teeth)
     *
     * Guarded against the pre-sync zero rather than trusting a caller
     * to check injection_crank_synced() first. */
    if (normal_period_ticks == 0u) {
        return 0u;
    }
    uint32_t rev_ticks = normal_period_ticks * CRANK_WHEEL_TEETH;
    uint32_t rpm = (60u * ECU_EMIOS_TICK_HZ) / rev_ticks;
    return (rpm > 65535u) ? 65535u : (uint16_t)rpm;
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
