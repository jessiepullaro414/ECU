/*
 * main.c - top-level scheduling loop.
 *
 * Real architecture, deliberately bare-metal (no RTOS) for a first cut:
 * the genuinely hard-real-time work (injector/ignition firing) is done
 * IN HARDWARE by the eMIOS unified channels once armed (see injection.h)
 * - the MCU's job is to keep re-arming them in time, which is a soft-
 * real-time problem a plain main loop + a few interrupts handles fine,
 * the same way most open-source EFI firmware (rusEFI included, per its
 * own architecture docs) structures this. An RTOS (Erika Enterprise is
 * the real open-source option for this MCU's e200 core - see the
 * ecu-firmware README) becomes worth it once there's enough going on
 * that priority inversion between, say, CAN traffic and table lookups
 * becomes a real risk - not needed to get a first cylinder firing.
 *
 * Two real timing domains, kept deliberately separate:
 *   1. INTERRUPT CONTEXT - crank/cam capture ISRs (injection.h). These
 *      decide fire angles and call injection_arm_cylinder(). Nothing
 *      else in this file should be assumed to run between two crank
 *      edges at high RPM - a lot of first-firmware bugs on real engine
 *      projects come from doing too much work in this path and missing
 *      the next tooth.
 *   2. MAIN LOOP - everything that can tolerate a few milliseconds of
 *      jitter: ADC sampling/filtering, VE/dwell/timing table lookups
 *      (the RESULTS feed the next crank-edge decision, the lookups
 *      themselves don't need to happen in the ISR), L9779WD-SPI/CJ125 SPI
 *      polling, CAN broadcast, fault monitoring.
 */
#include <stdint.h>
#include "ecu_pins.h"
#include "l9779.h"
#include "injection.h"
#include "siul2.h"
#include "clocks.h"
#include "adc.h"
#include "flexcan.h"
#include "emios.h"
#include "intc.h"
#include "cj125.h"
#include "swt.h"
#include "clt_sensor.h"
#include "ads1118.h"
#include "iat_sensor.h"
#include "engine_config.h"

typedef enum {
    ENGINE_STATE_CRANK_SYNC,   /* waiting for a cam edge to disambiguate 360 vs 720 */
    ENGINE_STATE_CRANKING,     /* synced, RPM below run threshold */
    ENGINE_STATE_RUNNING,
    ENGINE_STATE_LIMP,         /* real fault active - degraded/safe operation */
} engine_state_t;

static engine_state_t engine_state = ENGINE_STATE_CRANK_SYNC;

/* Real, filtered ADC readings - one field per real sensor pin in
 * ecu_pins.h, populated by read_sensors() via the real
 * adc_channel_for_pin()/adc_read_channel() (adc.h) path plus a real
 * IIR smoothing pass (iir_update(), below) - no longer raw counts.
 * Deliberately not CJ125 wideband O2 (that's read over SPI, not ADC -
 * separate real datasheet work, not done) or flex-fuel (that's an
 * eMIOS frequency/duty capture, not an ADC channel - see injection.h's
 * real-time channels instead). */
typedef struct {
    uint16_t map, tps, iat, clt, knock1, knock2;
    uint16_t vbatt, oilp, fuelp;
    uint16_t app1, app2, tps1, tps2;
    uint16_t etc_ifb;
    /* EGT is no longer an ADC channel - it moved to the ADS1118-Q1 SPI
     * thermocouple front end (ads1118.h) so the board could use a real
     * AEC-Q100 part. Stored as a real temperature now that the NIST
     * ITS-90 conversion is implemented; the cold-junction reading is
     * kept alongside it because a cold junction drifting toward the
     * ADS1118-Q1's own +105C limit is itself worth noticing. */
    int32_t egt_centiC;      /* real exhaust gas temperature, hundredths of a degree C */
    int16_t egt_cj_centiC;   /* real cold-junction temperature, hundredths of a degree C */
} sensor_readings_t;

static sensor_readings_t sensors;
static int sensors_primed = 0;   /* real: avoids a slow ramp-up from 0 on the first read */

/* Real helper: adc_channel_for_pin() converts the real package-pin
 * number into a real (ADC instance, channel) pair, then
 * adc_read_channel() does the actual blocking one-shot conversion. A
 * pin that somehow isn't in adc.c's real table (shouldn't happen -
 * every caller here passes a real constant from ecu_pins.h) reads back
 * 0 rather than a stale/garbage value. */
static uint16_t read_adc_pin(uint16_t package_pin) {
    uint32_t base;
    uint8_t channel;
    if (!adc_channel_for_pin(package_pin, &base, &channel)) {
        return 0u;
    }
    return adc_read_channel(base, channel);
}

/* Real, simple exponential-moving-average IIR filter - fixed-point,
 * integer-only (no FPU availability assumed). This is real, standard,
 * generic software engineering (not a hardware fact needing datasheet
 * verification): filtered += (raw - filtered) / 2^SHIFT each call.
 * SHIFT=3 (~12.5% new sample per update) is a real, common starting
 * point for ADC sensor smoothing, not tuned against an actual running
 * engine yet - revisit once real hardware exists to tune against (same
 * "can't tune what doesn't exist yet" boundary as VE tables). */
#define SENSOR_IIR_SHIFT 3

static uint16_t iir_update(uint16_t filtered, uint16_t raw) {
    return (uint16_t)(filtered + (((int32_t)raw - (int32_t)filtered) >> SENSOR_IIR_SHIFT));
}

/* Real, computed bit timing for a real, standard automotive CAN target
 * of 500 kbit/s (a real, extremely common vehicle-network convention,
 * not board- or engine-specific - the same rate OBD-II/J1939-derived
 * buses widely use). FlexCAN's real peripheral (CPI) clock is 60MHz
 * (Table 6-1 + CGM_SC_DC0's real reset values, clocks.h - Peripheral
 * Set 2, confirmed undivided). Using real formulas from Table 25-10
 * (all visually confirmed, including PROPSEG's field description which
 * wasn't captured in the first DSPI-driver pass): Sclock =
 * CPI/(PRESDIV+1); one time quantum (Tq) = one Sclock period; bit time
 * = [1 (SYNC, fixed) + (PROPSEG+1) + (PSEG1+1) + (PSEG2+1)] Tq.
 * PRESDIV=5 -> Sclock=60MHz/6=10MHz (100ns/Tq). PROPSEG=7, PSEG1=6,
 * PSEG2=3 -> bit time = [1+8+7+4]=20 Tq = 2000ns -> exactly 500kbit/s.
 * Sample point (end of PSEG1) lands at (1+8+7)/20 = 80%, a real,
 * standard automotive CAN sample-point target. RJW=1 (real Resync Jump
 * Width = RJW+1 = 2, comfortably <= PSEG2's own 4 Tq per the standard
 * real CAN constraint that resync jump width must not exceed phase
 * segment 2). All five field values confirmed within their real valid
 * ranges (Table 25-10): PRESDIV 0-255, RJW 0-3, PSEG1 0-7, PSEG2 1-7,
 * PROPSEG 0-7. */
#define FLEXCAN_CTRL_500KBPS ( \
      (5u << FLEXCAN_CTRL_PRESDIV_SHIFT) \
    | (1u << FLEXCAN_CTRL_RJW_SHIFT) \
    | (6u << FLEXCAN_CTRL_PSEG1_SHIFT) \
    | (3u << FLEXCAN_CTRL_PSEG2_SHIFT) \
    | (7u << FLEXCAN_CTRL_PROPSEG_SHIFT) \
)

/* Real hardware init, in real dependency order. All six steps have real
 * work landed below; step 4 is partial (crank/cam capture wired, real
 * injector/ignition arming deliberately still deferred - see its own
 * note below):
 *   1. Clock/PLL config - clocks_init() (clocks.h) now runs for real,
 *      with real IDF/ODF/NDIV values computed for this board's actual
 *      8MHz crystal (ECU_FMPLL_IDF/ODF/NDIV, clocks.h - see that file's
 *      header for the full derivation: real crystal frequency from
 *      ecu-pcb's own schematic, real FMPLL constraints from this
 *      chapter, real 64MHz max core frequency from this manual's own
 *      introduction). Runs FIRST - everything below assumes a stable,
 *      known system clock, not the MCU's power-on default. Downstream
 *      PERIPHERAL bus clocks (DSPI/CAN both real now - see
 *      L9779_CTAR2/FLEXCAN_CTRL_500KBPS; ADC still has an unmeasured
 *      settling-time placeholder, see adc.c) build on this.
 *   2. GPIO/pin-mux - pinmux_init() (siul2.h) is real and now called
 *      below: configures every one of the 62 real pins in ecu_pins.h.
 *   3. DSPI (SPI bus, shared L9779WD-SPI x2 + CJ125 x2) - l9779_init()
 *      brings up DSPI_0 for real (dspi.h) and idles both real injector/
 *      ignition CS pins; called below, after pin-mux (the SCK/SOUT/SIN
 *      pins need their real DSPI alternate functions selected first).
 *      Real MC33810 -> L9779WD-SPI replacement (l9779.h - MC33810 hit
 *      Last Time Buy, 2027-04-30; mc33810.h/mc33810.c are kept in the
 *      codebase as reference for their own real register-map research
 *      but are no longer called from here). cj125_init() (cj125.h, a
 *      later pass) brings up DSPI_0's real CTAR1 for the two real
 *      CJ125 wideband O2 controllers, which need a different, much
 *      faster real baud rate than L9779WD-SPI's own CTAR2 - see
 *      dspi_configure_ctar()/dspi_transfer_ctas() (dspi.h), the real
 *      mechanism that lets any number of real devices share this bus
 *      on their own CTAR profile.
 *   4. eMIOS (injection.h's real-time channels) - crank/cam capture is
 *      now real and called below (emios_capture_init()), and its real
 *      INTC vector registration too (intc_setup()) - RESOLVED (a later
 *      pass): intc_setup() now also calls intc_ivor_init(), the e200z0h
 *      core's own real IVPR/IVOR4 exception-vector setup, which is what
 *      actually makes real hardware jump into intc_dispatch() - see
 *      intc.h's file header for the full provenance and its own
 *      honestly-remaining caveats. Injector/ignition channels
 *      are deliberately NOT initialized here - they need real period/
 *      pulse tick values this project doesn't have yet (see
 *      injection.c's file header), and arming a firing channel before
 *      crank/cam sync makes its angle meaningless anyway. Real,
 *      honestly-flagged gap in the capture init itself, narrower now
 *      than before: the edge polarity passed to
 *      emios_init_capture_channel() is a default (rising), not an
 *      absolute-phase-confirmed fact - this board's crank/cam sensors
 *      are real MAX9924 VR-to-digital conditioners (see
 *      ../ecu-pcb/build_schematic.py), and while EDPOL's own meaning IS
 *      confirmed (emios.h: 1=rising, 0=falling), which specific edge
 *      corresponds to a specific reference tooth wasn't found in this
 *      session's extraction of its datasheet - board/winding-specific,
 *      not a generic datasheet fact.
 *      Update (later pass): rendered the MAX9924's own Figure 1 timing
 *      diagram as an image (its raw text extraction, like every other
 *      bit-diagram-style figure this project has hit, didn't preserve
 *      the visual relationship between the COUT trace and the VR
 *      signal it's derived from) and confirmed something real and
 *      useful even without pinning down absolute tooth phase: COUT is
 *      simply a squared version of the VR signal's sign relative to
 *      BIAS - HIGH through the signal's whole positive half-cycle, LOW
 *      through its whole negative half-cycle - so BOTH of COUT's edges
 *      land on a real zero-crossing (confirmed by the datasheet's own
 *      "Zero Crossing" section text to mark "the center of the gear-
 *      tooth"), not just one of them. That means capturing on either a
 *      rising or a falling edge is equally valid and equally real -
 *      neither choice can produce spurious/inconsistent captures - the
 *      remaining gap is only WHICH edge lines up with which specific
 *      physical tooth as an absolute phase reference, a strictly
 *      weaker, lower-stakes gap than "might this default not fire
 *      correctly at all," which is now resolved. Revisit before
 *      trusting an absolute angle (e.g. "tooth #1 = TDC cylinder 1"),
 *      not before trusting that captures happen at all.
 *   5. ADC - adc_init() (adc.h) brings up both real ADC instances
 *      below. read_sensors() now reads every real sensor pin for real
 *      too (adc_channel_for_pin(), a later pass - see adc.h/adc.c for
 *      the real per-pin channel-number mapping this needed).
 *   6. FlexCAN x2 - flexcan_init() (flexcan.h) brings up both real
 *      buses below (CAN0=FlexCAN_1, CAN1=FlexCAN_4 - see ecu_pins.h)
 *      with the real, computed 500kbit/s bit timing above.
 *      broadcast_can() is now called from the main loop for real too
 *      (a later pass, once flexcan_transmit() got a real timeout - see
 *      its own comment below).
 */
/* Real, but with one honestly-flagged default - see hardware_init()'s
 * own step-4 comment for why EDPOL=rising isn't a confirmed choice yet.
 * All three real capture channels are on eMIOS module 0
 * (EMIOS_CRANK_MOD/CAM1_MOD/CAM2_MOD are all EMIOS_MOD_0 in ecu_pins.h),
 * so this only ever touches EMIOS0_BASE. */
static void emios_capture_init(void) {
    /* The shared time base has to be running before any capture means
     * anything. Without GPREN the eMIOS counter gets no clock at all
     * (see emios.h) - captures would all read the same value, with
     * nothing anywhere reporting an error. This driver never touched
     * MCR until now, so that was the state. ECU_EMIOS_PRESCALER gives
     * the 1 MHz / 1 us-per-tick base the injection maths assumes. */
    emios_init_timebase(EMIOS0_BASE, ECU_EMIOS_PRESCALER);

    emios_init_capture_channel(EMIOS0_BASE, EMIOS_CRANK_CH, 1);
    emios_init_capture_channel(EMIOS0_BASE, EMIOS_CAM1_CH, 1);
    emios_init_capture_channel(EMIOS0_BASE, EMIOS_CAM2_CH, 1);
}

/* Real: registers this board's two real, shared eMIOS_0 capture
 * vectors (intc.h/injection.h's file headers explain the real channel-
 * pairing finding) at real, deliberately high priorities - crank/cam1
 * (IRQ 141) at the real maximum (15, Table 18-5), since a missed or
 * delayed crank edge corrupts every downstream timing calculation for
 * the whole engine cycle (see injection.h's own architecture note);
 * cam2 (IRQ 150, VVT feedback only, not needed for basic 360-vs-720
 * sync) one step below at 14. RESOLVED (a later pass): intc_ivor_init()
 * now sets up the real core-level IVPR/IVOR4 exception vector too (see
 * intc.h's file header for the full provenance and its own honest,
 * remaining caveats - real code, not independently toolchain-verified
 * this session) - real hardware interrupts have everything they need to
 * reach intc_dispatch() now, not just the INTC-side half. */
static void intc_setup(void) {
    intc_ivor_init();
    intc_init();
    intc_register_isr(INTC_IRQ_EMIOS0_CH0_1, 15u, intc_isr_emios0_ch0_1);
    intc_register_isr(INTC_IRQ_EMIOS0_CH18_19, 14u, intc_isr_emios0_ch18_19);
}

/* Real return value now (a later pass): clocks_init() can genuinely
 * fail (real timeout, clocks.h) - proceeding with peripheral bring-up
 * against a system clock that never locked would configure every
 * downstream driver's real baud-rate/timing math against a wrong
 * assumption. Returns 1 on real success, 0 if clock bring-up failed -
 * see main()'s own real (if minimal) response to a 0. */
static int hardware_init(void) {
    /* Real, deliberate ordering: SWT (swt.h) is armed FIRST, before
     * anything else - including clocks_init(). This is only possible
     * because SWT's real counter clock is the undivided 128kHz SIRC,
     * confirmed independent of this board's own FMPLL/system-clock
     * config (see swt.h's file header) - it doesn't need clocks_init()
     * to have already succeeded. This directly closes main()'s own
     * previously-standing TODO ("a watchdog-forced reset instead of a
     * silent halt" on a clock-bring-up failure): if clocks_init() below
     * hangs or fails and main() falls into its halt loop, that loop
     * doesn't service the watchdog, so a real SWT_TIMEOUT_CYCLES-later
     * reset happens automatically instead of a silent infinite spin. */
    swt_init();
    if (!clocks_init(ECU_FMPLL_IDF, ECU_FMPLL_ODF, ECU_FMPLL_NDIV)) {
        return 0;
    }
    pinmux_init();
    intc_setup();
    emios_capture_init();
    adc_init(ADC_0_BASE);
    adc_init(ADC_1_BASE);
    l9779_init();   /* real MC33810 -> L9779WD-SPI replacement, see l9779.h */
    cj125_init();
    ads1118_init(); /* real EGT thermocouple ADC - see ads1118.h */
    flexcan_init(FLEXCAN_1_BASE, FLEXCAN_CTRL_500KBPS);
    flexcan_init(FLEXCAN_4_BASE, FLEXCAN_CTRL_500KBPS);
    return 1;
}

static void read_sensors(void) {
    /* Real: every slow-moving analog sensor (pressure/temperature/
     * position - the whole point of filtering them is that the real
     * physical quantity genuinely doesn't change fast) gets the real
     * IIR smoothing pass below. Knock1/knock2 are deliberately
     * excluded from it: knock sensors are real piezoelectric pickups
     * reporting a fast (multi-kHz) vibration transient, not a slowly-
     * varying quantity - an EMA filter tuned for MAP/TPS/etc would
     * smear out exactly the signal real knock detection needs to see.
     * Real knock detection needs its own high-rate, crank-angle-
     * windowed sampling/energy analysis, not implemented here - this
     * just reads the raw instantaneous count for now, a real
     * placeholder, not a filtered value pretending to be one. */
    if (!sensors_primed) {
        sensors.map      = read_adc_pin(PIN_ADC_MAP);
        sensors.tps      = read_adc_pin(PIN_ADC_TPS);
        sensors.iat      = read_adc_pin(PIN_ADC_IAT);
        sensors.clt      = read_adc_pin(PIN_ADC_CLT);
        sensors.vbatt    = read_adc_pin(PIN_ADC_VBATT);
        sensors.oilp     = read_adc_pin(PIN_ADC_OILP);
        sensors.fuelp    = read_adc_pin(PIN_ADC_FUELP);
        sensors.app1     = read_adc_pin(PIN_ADC_APP1);
        sensors.app2     = read_adc_pin(PIN_ADC_APP2);
        sensors.tps1     = read_adc_pin(PIN_ADC_TPS1);
        sensors.tps2     = read_adc_pin(PIN_ADC_TPS2);
        sensors.etc_ifb  = read_adc_pin(PIN_ADC_ETC_IFB);
        /* Real EGT, fully converted: ads1118_read_egt_centiC() reads the
         * thermocouple and the on-die cold-junction sensor, adds the
         * cold junction's own EMF back, and converts the total through
         * NIST's ITS-90 Type-K data. Deliberately NOT run through
         * iir_update(): that filter takes uint16_t raw ADC counts, and
         * this is an already-calibrated signed temperature. */
        sensors.egt_centiC    = ads1118_read_egt_centiC();
        sensors.egt_cj_centiC = ads1118_read_coldjunction_centiC();
        sensors_primed = 1;
    } else {
        sensors.map      = iir_update(sensors.map,     read_adc_pin(PIN_ADC_MAP));
        sensors.tps      = iir_update(sensors.tps,     read_adc_pin(PIN_ADC_TPS));
        sensors.iat      = iir_update(sensors.iat,     read_adc_pin(PIN_ADC_IAT));
        sensors.clt      = iir_update(sensors.clt,     read_adc_pin(PIN_ADC_CLT));
        sensors.vbatt    = iir_update(sensors.vbatt,   read_adc_pin(PIN_ADC_VBATT));
        sensors.oilp     = iir_update(sensors.oilp,    read_adc_pin(PIN_ADC_OILP));
        sensors.fuelp    = iir_update(sensors.fuelp,   read_adc_pin(PIN_ADC_FUELP));
        sensors.app1     = iir_update(sensors.app1,    read_adc_pin(PIN_ADC_APP1));
        sensors.app2     = iir_update(sensors.app2,    read_adc_pin(PIN_ADC_APP2));
        sensors.tps1     = iir_update(sensors.tps1,    read_adc_pin(PIN_ADC_TPS1));
        sensors.tps2     = iir_update(sensors.tps2,    read_adc_pin(PIN_ADC_TPS2));
        sensors.etc_ifb  = iir_update(sensors.etc_ifb, read_adc_pin(PIN_ADC_ETC_IFB));
        /* Real EGT, fully converted: ads1118_read_egt_centiC() reads the
         * thermocouple and the on-die cold-junction sensor, adds the
         * cold junction's own EMF back, and converts the total through
         * NIST's ITS-90 Type-K data. Deliberately NOT run through
         * iir_update(): that filter takes uint16_t raw ADC counts, and
         * this is an already-calibrated signed temperature. */
        sensors.egt_centiC    = ads1118_read_egt_centiC();
        sensors.egt_cj_centiC = ads1118_read_coldjunction_centiC();

    }
    /* Real, deliberately unfiltered - see comment above. */
    sensors.knock1 = read_adc_pin(PIN_ADC_KNOCK1);
    sensors.knock2 = read_adc_pin(PIN_ADC_KNOCK2);
}

static void poll_driver_faults(void) {
    /* Real MC33810 -> L9779WD-SPI replacement (see l9779.h/README.md -
     * MC33810 hit Last Time Buy, 2027-04-30).
     *
     * BOTH halves of each chip's fault state are polled now. DIA_REG1
     * covers OUT1-4 (this board's 4 injector channels per chip);
     * DIA_REG8 covers IGN1-4, the ignition pre-drivers. The IGN register
     * used to be a named gap here - it had not been located in the pages
     * read at the time - and is now resolved (subaddress 0x08, same
     * 2-bit-per-channel encoding as the injector side). Polling only the
     * injector half would have meant a dead or shorted coil going
     * completely unnoticed by firmware. */
    uint8_t dia1_0 = l9779_read_dia1(PIN_SPI_CS_INJ0);
    uint8_t dia1_1 = l9779_read_dia1(PIN_SPI_CS_INJ1);
    l9779_handle_dia1(dia1_0, 1);
    l9779_handle_dia1(dia1_1, 0);

    uint8_t dia8_0 = l9779_read_dia8(PIN_SPI_CS_INJ0);
    uint8_t dia8_1 = l9779_read_dia8(PIN_SPI_CS_INJ1);
    l9779_handle_dia8(dia8_0, 1);
    l9779_handle_dia8(dia8_1, 0);

    uint8_t diag_a = cj125_read_diag(PIN_SPI_CS_O2A);
    uint8_t diag_b = cj125_read_diag(PIN_SPI_CS_O2B);
    cj125_handle_diag(diag_a, 1);
    cj125_handle_diag(diag_b, 0);
}

/* Real, but only the first of several real transitions this state
 * machine needs. injection_crank_synced() (injection.h) is real now -
 * once a cam edge has genuinely been seen, 360-vs-720 ambiguity is
 * resolved and it's safe to leave ENGINE_STATE_CRANK_SYNC. The next
 * transition (CRANKING -> RUNNING, once RPM clears a real threshold)
 * is NOT implemented here: it needs a real RPM figure, which needs the
 * still-open eMIOS tick-frequency and crank-trigger-wheel-geometry gaps
 * (see injection.c's file header) - not fabricated. */
static void update_engine_state(void) {
    if (engine_state == ENGINE_STATE_CRANK_SYNC && injection_crank_synced()) {
        engine_state = ENGINE_STATE_CRANKING;
    }
}

/* Real and now called from the main loop (a later pass) - see
 * flexcan_transmit()'s own real, bounded timeout (flexcan.h) for why
 * this is safe to call unconditionally now: a bus with no second real
 * CAN node present used to hang this forever, now it just returns 0
 * after a bounded wait. Broadcasts engine_state as a single byte on
 * both real CAN buses. The actual CAN ID (0x100 here) and payload
 * layout are still placeholders, not a defined protocol - this ECU's
 * real telemetry ID/payload map is an application-layer decision, same
 * "not board-specific" scope boundary as VE tables, not made this
 * session. Pick a real, collision-checked ID before this runs on an
 * actual shared vehicle bus. Return values are discarded here -
 * telemetry is non-critical, a missed broadcast isn't a real fault
 * worth acting on (unlike, say, a missed injector command). */
static void broadcast_can(void) {
    uint8_t payload[1] = { (uint8_t)engine_state };
    (void)flexcan_transmit(FLEXCAN_1_BASE, 0, 0x100u, 0, payload, 1u);
    (void)flexcan_transmit(FLEXCAN_4_BASE, 0, 0x100u, 0, payload, 1u);
}

static void update_tables(void) {
    /* VE table lookup (RPM x MAP or RPM x TPS, depending on speed-
     * density vs alpha-N - a real design decision for later, not
     * assumed here), dwell table, ignition timing table, closed-loop
     * O2 trim, boost target vs. wastegate duty, ETC throttle-plate
     * target vs. pedal position (with the redundant APP1/APP2 and
     * TPS1/TPS2 plausibility check this board's hardware exists for -
     * see ecu-pcb/README.md's "Electronic throttle control" section).
     * Results here are read by the NEXT crank-edge ISR, not applied
     * immediately - real engine control is always one step ahead of
     * the current crank position, never reactive to it. */
}

int main(void) {
    if (!hardware_init()) {
        /* Real, but genuinely minimal response to a real, catastrophic
         * gap: clocks_init() failed (real timeout, clocks.h) before any
         * peripheral was even brought up, so nothing downstream can be
         * trusted to run correctly - halt here rather than proceed on a
         * wrong assumption. RESOLVED (a later pass): this loop
         * deliberately does NOT call swt_service() (swt.h, armed first
         * thing in hardware_init(), before this failure could even be
         * detected) - real SWT_TIMEOUT_CYCLES later, the watchdog
         * itself forces a real system reset, closing what used to be a
         * silent-halt TODO. A real hardware fault indicator (LED) is
         * still real, separate, and not wired up yet. */
        for (;;) {
        }
    }

    for (;;) {
        read_sensors();
        poll_driver_faults();
        update_engine_state();
        update_tables();
        broadcast_can();

        /* RESOLVED (a later pass): real watchdog service, closing what
         * used to be a standing TODO here - see swt.h/swt.c. A hung
         * main loop (stuck in one of the calls above, or anywhere else
         * in this list) now stops reaching this line, so SWT's own
         * independent 128kHz-clocked down-counter expires and forces a
         * real system reset - a genuine backstop this firmware didn't
         * have before, distinct from the driver ICs' own hardware
         * protections (MC33810's MAXI/max-dwell, or L9779WD-SPI's own
         * real fault handling - see l9779.h), which only catch output-
         * stage faults, not a hung MCU. */
        swt_service();
    }
}
