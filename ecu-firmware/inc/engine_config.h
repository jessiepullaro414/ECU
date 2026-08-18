/*
 * engine_config.h - the engine-specific parameters, in one place.
 *
 * WHY THIS FILE EXISTS. This board was always meant to run 4-, 6- and
 * 8-cylinder engines from one design (see ../ecu-pcb/README.md), but the
 * firmware had engine facts scattered as "TODO, needs a real number"
 * comments - crank wheel geometry, cylinder count, firing order. Those
 * are not things to discover from a datasheet; they are things the
 * person fitting the ECU knows about their engine. Collecting them here
 * turns a standing blocker into a setting.
 *
 * WHAT IS AND IS NOT VERIFIED, because this file is different in kind
 * from the rest of the codebase and it would be easy to misread:
 *   * The DERIVED values and the arithmetic that uses them are real -
 *     tick rates come from this board's own confirmed 60 MHz peripheral
 *     clock, and the angle maths is exact.
 *   * The ENGINE values below are DEFAULTS, not measurements. They
 *     describe a common configuration (36-1 crank wheel, 8 cylinders,
 *     the usual small-block firing order) and are marked as such. They
 *     have NOT been confirmed against any particular engine, because
 *     there isn't one yet. Anyone fitting this to a real engine must
 *     check every value in the ENGINE section against that engine.
 *   * Nothing here has run on hardware. The compile-time checks at the
 *     bottom catch self-inconsistent combinations, not wrong ones.
 *
 * Getting the crank wheel or firing order wrong does not produce a
 * subtle error - it fires cylinders at the wrong time, which is how
 * engines get damaged. Treat the ENGINE section as something to fill
 * in deliberately, not to accept.
 *
 * HOW TO OVERRIDE. Every setting below is wrapped in #ifndef, so a
 * build can supply its own without editing this file - which keeps one
 * source tree able to produce builds for different engines:
 *
 *     -DENGINE_CYLINDERS=4u -DCRANK_WHEEL_TEETH=60u  *     -DCRANK_WHEEL_MISSING=2u -DCRANK_GAP_TO_TDC_DEG=78u
 *
 * The consistency checks at the bottom apply to overrides exactly as
 * they do to the defaults.
 */
#ifndef ENGINE_CONFIG_H
#define ENGINE_CONFIG_H

#include <stdint.h>

/* =====================================================================
 * TIMEBASE - derived, real
 * ===================================================================*/

/* eMIOS sits on Peripheral Set 3, confirmed undivided against this
 * board's real 60 MHz core clock (clocks.h: CGM_SC_DC0's own reset
 * values, cross-checked with Table 6-1). */
#define ECU_EMIOS_CLOCK_HZ    60000000u

/* Global prescaler divide ratio. 60 gives a 1 MHz time base: 1 us per
 * tick, which is a natural unit for injection, and 65.535 ms of range
 * on the eMIOS channels' real 16-bit counters. That range matters -
 * it has to comfortably exceed the longest event being timed (an
 * injector pulse is single-digit milliseconds even at full load), and
 * it does, by an order of magnitude. */
#ifndef ECU_EMIOS_PRESCALER
#define ECU_EMIOS_PRESCALER   60u
#endif
#define ECU_EMIOS_TICK_HZ     (ECU_EMIOS_CLOCK_HZ / ECU_EMIOS_PRESCALER)

/* =====================================================================
 * ENGINE - DEFAULTS. Confirm every one against the actual engine.
 * ===================================================================*/

/* Cylinder count. The board provides 8 injector and 8 ignition
 * channels; a 4- or 6-cylinder engine simply uses the first N and
 * leaves the rest unwired at the harness. */
#ifndef ENGINE_CYLINDERS
#define ENGINE_CYLINDERS      8u
#endif

/* Four-stroke: one full engine cycle is two crank revolutions. Set to
 * 360 for a two-stroke, which also makes cam sync unnecessary. */
#ifndef ENGINE_CYCLE_DEGREES
#define ENGINE_CYCLE_DEGREES  720u
#endif

/* Crank trigger wheel. "36-1" means 36 evenly spaced tooth positions
 * with one physically removed, leaving 35 teeth and a gap that marks a
 * known angle. 60-2 is the other common pattern. Both numbers matter:
 * TEETH sets the angular resolution (360/36 = 10 degrees here), and
 * MISSING is what makes the wheel's absolute position findable. */
#ifndef CRANK_WHEEL_TEETH
#define CRANK_WHEEL_TEETH     36u
#endif
#ifndef CRANK_WHEEL_MISSING
#define CRANK_WHEEL_MISSING   1u
#endif
#define CRANK_DEGREES_PER_TOOTH  (360u / CRANK_WHEEL_TEETH)

/* Crank angle, in degrees BEFORE cylinder 1's compression TDC, at which
 * the tooth immediately following the wheel's gap passes the sensor.
 * This is the one value that cannot be guessed from the wheel pattern -
 * it depends on where the sensor is physically mounted, and it is what
 * converts "which tooth" into "what angle". Measure it; do not assume
 * this default fits. */
#ifndef CRANK_GAP_TO_TDC_DEG
#define CRANK_GAP_TO_TDC_DEG  90u
#endif

/* Firing order, as cylinder numbers in the order they fire. The default
 * is the common small-block V8 order (1-8-4-3-6-5-7-2). A 4-cylinder is
 * typically {1,3,4,2} and a straight-six {1,5,3,6,2,4} - but check the
 * engine, because this varies by manufacturer. */
#ifndef ENGINE_FIRING_ORDER
#define ENGINE_FIRING_ORDER   { 1u, 8u, 4u, 3u, 6u, 5u, 7u, 2u }
#endif

/* Injector dead time: how long the injector takes to physically open
 * after the driver turns on. Added to every computed pulse width, or
 * the engine runs lean by roughly this much at every load point. It
 * varies strongly with battery voltage, which is exactly why the board
 * has a dedicated VBATT sense channel; this single figure is a
 * placeholder for the voltage-compensated table that belongs here once
 * there is an engine to measure against. */
#ifndef INJECTOR_DEAD_TIME_US
#define INJECTOR_DEAD_TIME_US 1000u
#endif

/* Ignition coil dwell: how long the coil is energised before the spark.
 * Too short gives a weak spark, too long overheats the coil. Like dead
 * time this really wants a voltage-compensated table. */
#ifndef IGNITION_DWELL_US
#define IGNITION_DWELL_US     3000u
#endif

/* =====================================================================
 * Consistency checks. These catch a config that contradicts itself -
 * they cannot catch one that is simply wrong for your engine.
 * ===================================================================*/
#if (360u % CRANK_WHEEL_TEETH) != 0u
#error "CRANK_WHEEL_TEETH must divide 360 evenly - teeth are evenly spaced"
#endif
#if CRANK_WHEEL_MISSING >= CRANK_WHEEL_TEETH
#error "CRANK_WHEEL_MISSING must be fewer than CRANK_WHEEL_TEETH"
#endif
#if ENGINE_CYLINDERS == 0u || ENGINE_CYLINDERS > 8u
#error "ENGINE_CYLINDERS must be 1..8 - the board has 8 injector/ignition channels"
#endif
#if (ENGINE_CYCLE_DEGREES != 360u) && (ENGINE_CYCLE_DEGREES != 720u)
#error "ENGINE_CYCLE_DEGREES must be 360 (two-stroke) or 720 (four-stroke)"
#endif
#if (ECU_EMIOS_PRESCALER < 1u) || (ECU_EMIOS_PRESCALER > 256u)
#error "ECU_EMIOS_PRESCALER must be 1..256 - eMIOS GPRE encodes ratio-1 in 8 bits"
#endif
/* A cycle must divide evenly into firing events, or the events are not
 * evenly spaced and the angle maths below is meaningless. */
#if (ENGINE_CYCLE_DEGREES % ENGINE_CYLINDERS) != 0u
#error "ENGINE_CYCLE_DEGREES must divide evenly by ENGINE_CYLINDERS"
#endif

/* Crank degrees between consecutive firing events. */
#define ENGINE_FIRING_INTERVAL_DEG (ENGINE_CYCLE_DEGREES / ENGINE_CYLINDERS)

#endif /* ENGINE_CONFIG_H */
