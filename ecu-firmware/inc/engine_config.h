/*
 * engine_config.h - GENERATED FILE, do not edit by hand.
 *
 * Regenerate with:  python tools/gen_engine_config.py
 * Source of truth:  config/engine.toml
 *
 * Engine-specific facts live in that .toml rather than here so there is
 * one plain, commented place to set them, with the generator validating
 * them properly - including checking the firing order is a genuine
 * permutation of the cylinders, which a typo would otherwise turn into
 * an engine that runs badly rather than one that obviously does not run.
 *
 * THE ENGINE VALUES ARE DEFAULTS, NOT MEASUREMENTS. They describe a
 * common setup and have been confirmed against no real engine. Getting
 * the wheel pattern or firing order wrong fires cylinders at the wrong
 * time, which is how engines get damaged. See config/engine.toml.
 */
#ifndef ENGINE_CONFIG_H
#define ENGINE_CONFIG_H

#include <stdint.h>

/* ---- Timebase: derived from this board's confirmed 60 MHz clock ---- */
#define ECU_EMIOS_CLOCK_HZ    60000000u
#define ECU_EMIOS_PRESCALER   60u
#define ECU_EMIOS_TICK_HZ     (ECU_EMIOS_CLOCK_HZ / ECU_EMIOS_PRESCALER)

/* ---- Engine ------------------------------------------------------- */
#define ENGINE_CYLINDERS      8u
#define ENGINE_CYCLE_DEGREES  720u

/* Crank degrees between consecutive firing events. */
#define ENGINE_FIRING_INTERVAL_DEG (720u / 8u)

/* Firing order as an initialiser: cylinder numbers, in the order they
 * fire. Validated as a permutation of 1..8 at generation time. */
#define ENGINE_FIRING_ORDER   { 1u, 8u, 4u, 3u, 6u, 5u, 7u, 2u }

/* ---- Crank trigger wheel ------------------------------------------ */
#define CRANK_WHEEL_TEETH        36u
#define CRANK_WHEEL_MISSING      1u
#define CRANK_DEGREES_PER_TOOTH  (360u / CRANK_WHEEL_TEETH)

/* Real tooth positions per revolution - what the sensor actually sees,
 * which is fewer than CRANK_WHEEL_TEETH because of the gap. */
#define CRANK_REAL_TEETH      (CRANK_WHEEL_TEETH - CRANK_WHEEL_MISSING)

/* Crank angle in degrees BEFORE cylinder 1 compression TDC at which the
 * first tooth after the gap passes the sensor. Depends on where the
 * sensor is physically mounted; an error here shifts ALL timing. */
#define CRANK_GAP_TO_TDC_DEG  90u

/* ---- Injection / ignition ----------------------------------------- */
#define INJECTOR_DEAD_TIME_US 1000u
#define IGNITION_DWELL_US     3000u

/* ---- Fuelling ------------------------------------------------------
 * Speed-density: air mass in the cylinder from pressure, volume and
 * temperature; fuel mass from the target AFR; pulse width from the
 * injector's flow rate. VE is the measured correction that makes the
 * ideal-gas figure match what the engine actually inhales. */
#define FUEL_DISPLACEMENT_CC   5700u
#define FUEL_CYL_VOLUME_CC     (5700u / 8u)
#define FUEL_INJECTOR_CC_MIN   440u
#define FUEL_DENSITY_MG_CC     745u
#define FUEL_TARGET_AFR_X10    147u

/* MAP sensor: linear ratiometric, so kPa is a straight line in ADC
 * counts between these two points. */
#define MAP_KPA_AT_MIN         10u
#define MAP_KPA_AT_MAX         105u
#define MAP_ADC_AT_MAX         3103u

/* ---- VE table --------------------------------------------------
 * Rows are MAP breakpoints, columns RPM. Bilinearly interpolated
 * by table2d_lookup() (table.h).
 * A STARTING SHAPE, NOT A TUNED MAP - see config/engine.toml. */
#define VE_RPM_COUNT   8u
#define VE_MAP_COUNT   5u

static const uint16_t VE_RPM_AXIS[VE_RPM_COUNT] = { 500u, 1000u, 1500u, 2000u, 3000u, 4000u, 5000u, 6000u };
static const uint16_t VE_MAP_AXIS[VE_MAP_COUNT] = { 20u, 40u, 60u, 80u, 100u };
static const int16_t  VE_TABLE[VE_MAP_COUNT][VE_RPM_COUNT] = {
    {   30,   33,   35,   36,   35,   33,   30,   27 },   /*   20 kPa */
    {   45,   50,   54,   56,   56,   54,   50,   45 },   /*   40 kPa */
    {   55,   62,   68,   72,   74,   72,   67,   60 },   /*   60 kPa */
    {   62,   70,   78,   83,   86,   85,   80,   72 },   /*   80 kPa */
    {   66,   75,   84,   90,   94,   93,   88,   80 },   /*  100 kPa */
};

/* ---- Spark advance ---------------------------------------------
 * Crank degrees BEFORE top dead centre. Same shape and the same
 * interpolation as the VE table, on its own axes.
 * A STARTING SHAPE, NOT A TUNED MAP. Over-advance destroys
 * pistons - see config/engine.toml. */
#define SPARK_RPM_COUNT   8u
#define SPARK_MAP_COUNT   5u

static const uint16_t SPARK_RPM_AXIS[SPARK_RPM_COUNT] = { 500u, 1000u, 1500u, 2000u, 3000u, 4000u, 5000u, 6000u };
static const uint16_t SPARK_MAP_AXIS[SPARK_MAP_COUNT] = { 20u, 40u, 60u, 80u, 100u };
static const int16_t  SPARK_TABLE[SPARK_MAP_COUNT][SPARK_RPM_COUNT] = {
    {   12,   20,   28,   34,   40,   44,   46,   46 },   /*   20 kPa */
    {   12,   20,   27,   32,   38,   42,   44,   44 },   /*   40 kPa */
    {   10,   18,   24,   28,   34,   38,   40,   40 },   /*   60 kPa */
    {   10,   15,   20,   24,   29,   32,   34,   34 },   /*   80 kPa */
    {    8,   12,   16,   20,   26,   30,   32,   32 },   /*  100 kPa */
};

/* The largest advance the spark table can command, in crank degrees.
 * The scheduling lead has to stay wider than this - the generator
 * enforces that, this is here so the firmware can assert it too. */
#define SPARK_ADVANCE_MAX_DEG  46

#endif /* ENGINE_CONFIG_H */
