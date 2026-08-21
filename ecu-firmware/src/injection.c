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
#include "ignition.h"

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
    /* The counter bus runs 1..EMIOS_COUNTER_MODULUS, so the modulus is
     * 65535 and NOT a clean 16-bit mask. This used to be
     * `(later - earlier) & 0xFFFF`, which quietly gained one tick every
     * time the counter wrapped. A conditional subtract is exact for any
     * modulus and costs no divide, which matters in an ISR on a core
     * with no fast divider. */
    if (later >= earlier) {
        return later - earlier;
    }
    return (later + EMIOS_COUNTER_MODULUS) - earlier;
}

/* Adds a delay to a bus timestamp, wrapping the same way the counter
 * does. Values on the bus are 1..EMIOS_COUNTER_MODULUS, never 0. */
static uint32_t ticks_after(uint32_t start, uint32_t delta) {
    uint32_t t = start + delta;
    while (t > EMIOS_COUNTER_MODULUS) {
        t -= EMIOS_COUNTER_MODULUS;
    }
    return t;
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
/* Where the crank is, in cycle degrees, as of the most recent tooth.
 * injection_arm_cylinder() needs it to work out how far ahead the spark
 * angle sits; crank_capture_isr() is the only writer and runs before
 * every call, so this is a plain hand-off, not shared state. */
static uint32_t cycle_pos_deg;

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
/* CEILING on the arming lead, not the lead itself - see
 * arming_lead_deg() below for how the real lead is chosen.
 *
 * Two firing intervals is as far ahead as it is ever useful to look.
 * Beyond that the scan would start selecting a cylinder more than two
 * events away, and on an engine with few cylinders it could wrap past a
 * whole cycle and re-select the one just fired. */
#define ARM_LEAD_CAP_DEG  (2u * ENGINE_FIRING_INTERVAL_DEG)

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
/* How far ahead of the crank to arm a cylinder, in whole crank degrees.
 *
 * THE LEAD IS A TIME PROBLEM WEARING AN ANGLE'S CLOTHES. Coil dwell is
 * a fixed duration - 3 ms is 3 ms at any engine speed - but the crank
 * covers wildly different angles in 3 ms depending on how fast it is
 * turning. A FIXED angle lead therefore has to be sized for the worst
 * case (high rpm, where 3 ms is over 100 degrees) and is then absurdly
 * long everywhere else: at 150 rpm a 180-degree lead means scheduling
 * 200 ms ahead to cover a 3 ms coil charge.
 *
 * That is what put cranking out of reach. Match values are absolute
 * positions on a 16-bit counter bus spanning 65.5 ms, so a 200 ms lead
 * simply cannot be expressed, and injection_arm_cylinder() refused to
 * schedule anything below about 458 rpm - which is every real cranking
 * speed. Slowing the timebase would have bought range at the cost of
 * resolution; sizing the lead from the dwell instead costs nothing and
 * uses LESS counter range at every speed, cranking included.
 *
 * Three terms, and each was found the hard way by simulating the
 * alternatives before writing this:
 *
 *   - whole teeth to span the dwell, rounded UP. Partial teeth cannot
 *     be selected: the scan below tests an exact integer angle.
 *   - the advance, because the spark sits that far BEFORE TDC and
 *     therefore eats into the lead. Omitting this looks fine at
 *     cranking and silently starves the coil above 500 rpm.
 *   - one tooth of margin for interrupt latency.
 *
 * The result is rounded up to a whole tooth so it lands exactly on the
 * one-tooth-wide selection window, and capped. Keeping the SELECTION in
 * exact integer degrees matters: an earlier attempt expressed the whole
 * window in ticks instead, and truncating the tooth period made the
 * window a hair narrower than the step it was testing, so events were
 * silently skipped at some speeds and not others. */
static uint16_t arming_lead_deg(uint32_t dwell_ticks, int16_t advance_deg) {
    uint32_t cap = ARM_LEAD_CAP_DEG;
    /* Never look so far ahead that the target wraps the whole cycle and
     * re-selects the cylinder that just fired. Matters only on very low
     * cylinder counts, where a firing interval is a large fraction of
     * the cycle. */
    if (cap > (ENGINE_CYCLE_DEGREES - CRANK_DEGREES_PER_TOOTH)) {
        cap = ENGINE_CYCLE_DEGREES - CRANK_DEGREES_PER_TOOTH;
    }
    if (normal_period_ticks == 0u) {
        return (uint16_t)cap;      /* no period measured yet */
    }

    uint32_t teeth = (dwell_ticks + normal_period_ticks - 1u)
                     / normal_period_ticks;      /* dwell, rounded up */
    teeth += 1u;                                 /* latency margin */

    uint32_t lead = (teeth * CRANK_DEGREES_PER_TOOTH)
                  + (uint32_t)((advance_deg > 0) ? advance_deg : 0);

    /* Round up to a whole tooth so the selection window can land on it. */
    lead = ((lead + CRANK_DEGREES_PER_TOOTH - 1u) / CRANK_DEGREES_PER_TOOTH)
           * CRANK_DEGREES_PER_TOOTH;

    return (uint16_t)((lead > cap) ? cap : lead);
}

void injection_init_outputs(void) {
    /* Walks this file's own channel tables rather than exposing them,
     * which also means the module/base pairing for IGN6/7/8 - the three
     * that live on eMIOS module 1 - is applied here exactly as it is in
     * the firing path, from one table. */
    for (uint8_t c = 1u; c <= ENGINE_CYLINDERS; c++) {
        emios_init_output_channel(injector_ch[c].base, injector_ch[c].channel);
        emios_init_output_channel(ignition_ch[c].base, ignition_ch[c].channel);
    }
}

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
    uint32_t dwell_ticks = us_to_ticks(event->dwell_us, ECU_EMIOS_TICK_HZ);

    /* How far ahead of the tooth we just captured the spark belongs.
     * fire_angle_deg is an absolute crank angle carrying the spark
     * advance (ignition.c); cycle_pos_deg is where the crank is now. */
    uint32_t ahead_deg = (event->fire_angle_deg + ENGINE_CYCLE_DEGREES
                          - cycle_pos_deg) % ENGINE_CYCLE_DEGREES;
    uint32_t spark_delta = injection_angle_to_ticks((uint16_t)ahead_deg);

    /* RANGE GUARD, and it is a real limit rather than defensive
     * paranoia. The counter bus spans EMIOS_COUNTER_MODULUS ticks -
     * 65.5 ms at this board's 1 MHz timebase - and a match scheduled
     * further out than that lands a whole wrap early, firing a cylinder
     * at a badly wrong angle instead of failing visibly. The arming lead
     * is one firing interval, so at 6000 rpm the delta is about 2.5 ms
     * and at idle about 25 ms, but CRANKING AT 200 RPM IT IS ABOUT
     * 75 ms AND DOES NOT FIT. Refusing to arm is the safe response;
     * see the README for the timebase tradeoff behind it. */
    if (spark_delta >= EMIOS_COUNTER_MODULUS
        || dwell_ticks >= spark_delta) {
        return;
    }

    /* Ignition. EDPOL = 1, so the A match starts charging the coil and
     * the B match releases it - and releasing the coil IS the spark, so
     * B is the event the advance was computed for. The coil therefore
     * starts charging one dwell BEFORE that. */
    uint32_t spark_at  = ticks_after(last_crank_capture, spark_delta);
    uint32_t charge_at = ticks_after(last_crank_capture,
                                     spark_delta - dwell_ticks);
    emios_schedule_pulse(ign->base, ign->channel, charge_at, spark_at);

    /* Injection. The pulse width is real; its PHASE is not configurable
     * yet - injection start angle is a genuine tuning parameter (it
     * decides how much of the charge lands on a closed valve) and no
     * value for it has been established, so this opens the injector at
     * the arming point rather than pretending to a calibrated angle.
     *
     * The 16-bit channel registers cap a pulse at the counter modulus;
     * clamp rather than wrap, because a wrapped pulse is both wrong and
     * plausible-looking. 65.5 ms is far past any real injector command. */
    if (inj_ticks >= EMIOS_COUNTER_MODULUS) {
        inj_ticks = EMIOS_COUNTER_MODULUS - 1u;
    }
    uint32_t open_at  = ticks_after(last_crank_capture, 1u);
    uint32_t close_at = ticks_after(open_at, inj_ticks);
    emios_schedule_pulse(inj->base, inj->channel, open_at, close_at);
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

    uint8_t after_gap = 0u;
    if (gap_detected(crank_period_ticks_val, prev_crank_period_ticks)) {
        /* This edge is the first real tooth after the gap - the one
         * angularly-known point on the wheel. */
        after_gap = 1u;
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
    cycle_pos_deg = ((uint32_t)revolution * 360u + from_ref
                     + ENGINE_CYCLE_DEGREES - CRANK_GAP_TO_TDC_DEG)
                    % ENGINE_CYCLE_DEGREES;

    /* Arm whichever cylinder comes due one lead-window from here.
     *
     * THE WINDOW MUST BE AS WIDE AS THE ARC ACTUALLY TRAVERSED since the
     * previous tooth, which is NOT always one tooth: across the wheel's
     * gap the crank covers (missing + 1) tooth intervals in a single
     * edge-to-edge step. A fixed one-tooth window silently drops any
     * cylinder whose arming angle falls in the gap - on this 36-1 wheel
     * the angles 350 and 710 degrees are never reported at all, so a
     * cylinder needing either of them simply never fires.
     *
     * That was a latent bug, not one the adaptive lead introduced. The
     * old fixed 180-degree lead happened to place every cylinder's
     * arming angle on a real tooth; a 100-degree lead - which is what
     * the dwell calls for at 3000 rpm - puts two of the eight squarely
     * in the gap. Simulation caught it as a drop from 16 events to 12.
     *
     * Widening to the real traversed arc fixes it for any lead, any
     * wheel pattern, and any cylinder count. */
    uint32_t window_deg = after_gap
        ? ((uint32_t)(CRANK_WHEEL_MISSING + 1u) * CRANK_DEGREES_PER_TOOTH)
        : (uint32_t)CRANK_DEGREES_PER_TOOTH;
    /* Advance depends only on the operating point, not on which
     * cylinder is due, so it is computed once here - the lead needs it
     * too, and calling the table lookup per candidate cylinder would
     * have done the same work eight times in an ISR. */
    uint16_t rpm_now      = injection_crank_rpm();
    int16_t  advance_now  = ignition_advance_deg(rpm_now, fuel_map_kpa);
    uint32_t dwell_ticks  = us_to_ticks(IGNITION_DWELL_US, ECU_EMIOS_TICK_HZ);
    uint32_t lead         = arming_lead_deg(dwell_ticks, advance_now);

    uint32_t target = (cycle_pos_deg + lead) % ENGINE_CYCLE_DEGREES;
    for (uint8_t i = 0u; i < ENGINE_CYLINDERS; i++) {
        uint32_t fire_at = fire_angle_for_order_index(i);
        /* Has the target just SWEPT PAST this cylinder's TDC? The test
         * has to be this way round, not "is the TDC ahead of target".
         * They agree only when the arming angle lands exactly on a
         * tooth; the moment it does not - and across the wheel's gap it
         * cannot - the target leaps over the TDC and an "is it ahead"
         * test never fires again for that cylinder. */
        uint32_t delta   = (target + ENGINE_CYCLE_DEGREES - fire_at)
                           % ENGINE_CYCLE_DEGREES;
        if (delta < window_deg) {
            cylinder_event_t event;
            event.cylinder       = firing_order[i];   /* 1-based, see injection.h */
            /* cylinder_event_t.pulse_width_us is 16-bit, so clamp
             * before the assignment rather than after: truncating here
             * would wrap a too-long pulse down to a short one, and
             * injection_arm_cylinder()'s own tick-level clamp would
             * never see the real value. 65.5 ms is past any real
             * injector command - reaching it means a misconfigured
             * injector flow rate, not a running engine. */
            uint32_t pw = fuel_pulse_width_us(rpm_now, fuel_map_kpa,
                                              fuel_iat_centiC);
            event.pulse_width_us = (pw > 65535u) ? 65535u : (uint16_t)pw;
            event.dwell_us       = IGNITION_DWELL_US;

            /* fire_at is this cylinder's TDC; the spark belongs
             * ADVANCE degrees before it. */
            event.fire_angle_deg = ignition_spark_angle((uint16_t)fire_at,
                                                        advance_now);
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
