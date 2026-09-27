/*
 * plausibility.h - cross-checks redundant sensor pairs.
 *
 * WHY THE BOARD HAS REDUNDANT PEDAL AND THROTTLE CHANNELS. On a
 * drive-by-wire engine nothing mechanical connects the pedal to the
 * throttle plate, so a sensor that lies is a throttle that opens when
 * the driver did not ask. Two independent channels measuring the same
 * travel let a single failure be caught by disagreement. That is the
 * failure mode this hardware exists to prevent, and ecu-pcb's README
 * assigns the checking to firmware.
 *
 * WHY THE CHECK WAS WORTH NOTHING BEFORE THIS. app1/app2 and tps1/tps2
 * were configured IDENTICALLY - same band, same slope, same units. Two
 * channels with the same transfer function detect an open circuit and
 * nothing else: a stuck track, a track shorted to its neighbour, or a
 * cross-connected harness reads plausibly on BOTH, the two agree, and
 * the comparison passes. A redundancy check that cannot fail is worse
 * than no check, because it is believed.
 *
 * Production pedals ship a deliberately dissimilar second track for
 * exactly this reason, and the config now does too - app2 at half
 * slope, tps2 inverted. The generator refuses a pair whose transfer
 * functions are not distinguishable, so this cannot quietly regress.
 *
 * WHAT THIS MODULE DOES AND DOES NOT DO. It detects and latches. It
 * does NOT drop throttle authority, because there is no throttle
 * controller yet - the MC33926 H-bridge is on the board and unused. The
 * response belongs with that controller when it is written, and the
 * rule it must follow is written down in plausibility.c rather than
 * left for someone to rediscover.
 */
#ifndef PLAUSIBILITY_H
#define PLAUSIBILITY_H

#include <stdint.h>
#include "sensor.h"

typedef enum {
    PLAUS_OK = 0,
    PLAUS_FAULT_RANGE,      /* one or both channels outside their signal band */
    PLAUS_FAULT_DISAGREE    /* both readable, but they do not agree */
} plaus_result_t;

/* Re-checks every configured pair against the supplied raw ADC counts,
 * indexed by sensor_id_t. Returns the number of pairs currently in
 * fault. Call once per main-loop sensor sweep. */
uint8_t plausibility_update(const uint16_t *raw_by_id);

/* The most recent result for one pair, by index into REDUNDANT_PAIRS. */
plaus_result_t plausibility_pair_state(uint8_t pair);

/* Bitmask of pairs that have faulted since the last clear, one bit per
 * pair index.
 *
 * LATCHED DELIBERATELY. An intermittent connection that disagrees for
 * one sweep out of a hundred is exactly the fault worth catching, and a
 * live-only flag would show clean by the time anyone looked. Clearing
 * is an explicit act. */
uint32_t plausibility_latched(void);

/* Clears the latch. Intended for a deliberate diagnostic reset, not for
 * the main loop to call - clearing every pass would defeat latching. */
void plausibility_clear_latched(void);

#endif /* PLAUSIBILITY_H */
