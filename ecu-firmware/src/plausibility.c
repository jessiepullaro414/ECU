/*
 * plausibility.c - see plausibility.h for why the redundant channels
 * exist and why the check was worth nothing before the pairs were made
 * dissimilar.
 */
#include "plausibility.h"
#include "sensor_defs.h"

static plaus_result_t pair_state[REDUNDANT_PAIR_COUNT];
static uint32_t       latched;

/* THE RULE A THROTTLE CONTROLLER MUST FOLLOW WHEN ONE OF THESE FAULTS.
 *
 * Written here rather than left to be rediscovered, because the
 * controller does not exist yet and this is the part that is easy to
 * get wrong later:
 *
 *   - A pedal pair in fault means the driver's request is unknown. The
 *     throttle must go to its limp position (closed, or the small
 *     mechanical default the plate springs to) and STAY there. It must
 *     not fall back to whichever channel still looks healthy: the whole
 *     point of the pair is that a single reading cannot be trusted, and
 *     picking one is trusting a single reading.
 *   - A throttle-position pair in fault means the plate's actual angle
 *     is unknown, so the control loop has no feedback. Cut drive to the
 *     H-bridge and let the plate spring closed. Holding the last duty
 *     cycle is the dangerous option - if the plate is stuck open, that
 *     keeps it open.
 *   - Recovery must not be automatic. A fault that clears on its own is
 *     an intermittent connection, which is the failure most likely to
 *     recur at the worst moment.
 */

/* Absolute difference between two engineering values. */
static int32_t diff_abs(int32_t a, int32_t b) {
    return (a >= b) ? (a - b) : (b - a);
}

uint8_t plausibility_update(const uint16_t *raw_by_id) {
    uint8_t faulted = 0u;

    for (uint8_t i = 0u; i < REDUNDANT_PAIR_COUNT; i++) {
        const redundant_pair_t *p = &REDUNDANT_PAIRS[i];
        int32_t va = sensor_convert(p->a, raw_by_id[p->a]);
        int32_t vb = sensor_convert(p->b, raw_by_id[p->b]);

        plaus_result_t r;
        if (va == SENSOR_INVALID || vb == SENSOR_INVALID) {
            /* Out of band on either channel: a broken wire or a short.
             * Caught here rather than by the comparison, because two
             * broken channels could sit at the same rail and "agree". */
            r = PLAUS_FAULT_RANGE;
        } else if (diff_abs(va, vb) > p->tolerance) {
            /* Both readable and both inside their bands, but they
             * disagree about the same physical travel. Because the
             * pair's transfer functions are deliberately dissimilar,
             * this catches a stuck or cross-connected track, not just
             * an open circuit. */
            r = PLAUS_FAULT_DISAGREE;
        } else {
            r = PLAUS_OK;
        }

        pair_state[i] = r;
        if (r != PLAUS_OK) {
            latched |= (uint32_t)1u << i;
            faulted++;
        }
    }
    return faulted;
}

plaus_result_t plausibility_pair_state(uint8_t pair) {
    if (pair >= REDUNDANT_PAIR_COUNT) {
        return PLAUS_OK;
    }
    return pair_state[pair];
}

uint32_t plausibility_latched(void) {
    return latched;
}

void plausibility_clear_latched(void) {
    latched = 0u;
}
