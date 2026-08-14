/*
 * injection.h - sequential injection + per-cylinder ignition scheduling.
 *
 * Architecture (not yet register-level implementation): each of the 16
 * real-time channels in ecu_pins.h is one MPC5606B eMIOS unified channel
 * configured in OPWFM (Output Pulse Width and Frequency Modulation) or
 * OPWMB mode - the eMIOS hardware itself holds the pulse start/width once
 * armed, so firing timing does NOT depend on interrupt latency the way a
 * software-toggled GPIO would. This is the whole reason this MCU was
 * chosen over a general-purpose part (see ../ecu-pcb/README.md).
 *
 * The firmware's real job per engine cycle is computing WHAT width/angle
 * to arm each channel with next, not toggling pins directly:
 *   - Pulse width (injectors) from the VE (volumetric efficiency) table,
 *     corrected for battery voltage (injector dead time genuinely varies
 *     with supply voltage - this is real, not a nicety; it's exactly why
 *     the board has a dedicated VBATT_ADC channel, see ecu_pins.h) and
 *     for coolant temp during warm-up enrichment (the real raw-count ->
 *     degrees-F conversion for this board's GM-style resistive CLT
 *     sending unit is done, see clt_sensor.h - not yet consumed here,
 *     since warm-up enrichment's own correction curve is real engine-
 *     specific tuning data, same "needs a running engine, not planned in
 *     detail yet" boundary as the VE/dwell tables above).
 *   - Dwell time (ignition) from a dwell table vs. battery voltage and
 *     RPM, with the MC33810's own max-dwell protection as a hardware
 *     backstop, not the primary limit.
 *   - Firing ANGLE (both) from the cam-synchronized crank position -
 *     sequential injection/ignition needs to know which cylinder is on
 *     which stroke, which needs a cam edge to disambiguate crank
 *     position (360° vs 720° ambiguity on a 4-stroke engine) - this is
 *     the real reason the board has real cam inputs, not just crank.
 */
#ifndef INJECTION_H
#define INJECTION_H

#include <stdint.h>

typedef struct {
    uint8_t  cylinder;        /* 1-8 */
    uint16_t pulse_width_us;  /* commanded injector ON time */
    uint16_t dwell_us;        /* commanded ignition coil charge time */
    uint16_t fire_angle_deg;  /* crank angle (0-719 on a 4-stroke) to fire at */
} cylinder_event_t;

/* Arms one cylinder's injector + ignition eMIOS channels for its next
 * event. Called once per cylinder per engine cycle, well ahead of the
 * actual fire angle (how far ahead depends on RPM and the eMIOS's own
 * arm-to-fire latency - needs real bench measurement, not guessed).
 *
 * Real now: which eMIOS unit/channel per cylinder (injection.c's own
 * lookup tables) and the OPWFMB register sequence itself
 * (emios_set_pulse_width(), see emios.h). Still a TODO: pulse_width_us/
 * dwell_us are passed straight through as if they were already eMIOS
 * ticks - the real microseconds-to-ticks conversion (us_to_ticks() in
 * injection.c) exists but needs the real eMIOS tick frequency, which
 * isn't known yet (see injection.c's file header). fire_angle_deg isn't
 * used by this function at all yet - the angle-to-time conversion
 * (angle_to_ticks() in injection.c) also exists but needs the real
 * crank trigger wheel geometry, likewise not known yet. */
void injection_arm_cylinder(const cylinder_event_t *event);

/* Real-time crank/cam ISR handlers (E0UC0 = crank, E0UC1 = cam1,
 * E0UC18 = cam2, all input-capture mode). These are genuinely latency-
 * sensitive - every real engine-management firmware treats crank/cam
 * capture as the highest-priority interrupt in the system, since a
 * missed or delayed edge here corrupts every downstream timing
 * calculation for that entire engine cycle.
 *
 * Real, previously-unflagged gap: these three functions are real and
 * correct (main.c's hardware_init() now calls emios_capture_init() to
 * arm the real eMIOS channels, EMIOSC_FEN set on each so a real capture
 * event does set the real hardware FLAG bit), but nothing in this
 * project yet routes that hardware event to actually CALL these
 * functions. That needs the e200z0h core's real interrupt vector table
 * (IVPR/IVORx) and the INTC (interrupt controller) module - assigning
 * each eMIOS channel's real interrupt source a priority and vector
 * entry pointing at the matching *_capture_isr() here. Genuinely not
 * researched this session - a separate, substantial subsystem (its own
 * real chapter), not a small follow-up. Until it's done, these
 * functions only run if something calls them directly (e.g. future
 * bench/unit testing), never from a real hardware event. */
void crank_capture_isr(uint32_t capture_time);
void cam1_capture_isr(uint32_t capture_time);
void cam2_capture_isr(uint32_t capture_time);

/* Real, generic (frequency/tooth-count-agnostic) accessors for the state
 * crank_capture_isr()/cam1_capture_isr() now genuinely track - see
 * injection.c for the real wraparound-safe math behind them.
 *
 * injection_crank_period_ticks(): eMIOS ticks between the two most
 * recent crank edges. Real and correct as a tick COUNT; converting it
 * to an RPM or a time-till-target-angle needs two numbers this session
 * doesn't have (see injection.c's file-level comment): the eMIOS
 * peripheral's real tick frequency (depends on the still-open system-
 * clock gap in clocks.h) and the real crank trigger wheel's tooth
 * count/pattern (an engine/sensor hardware choice, not a board one -
 * out of this project's scope until that hardware is chosen). Returns
 * 0 before the second-ever crank edge (no period measured yet).
 *
 * injection_crank_synced(): 1 once at least one real cam edge has been
 * seen since boot (resolves the 360°-vs-720° ambiguity - see
 * injection.h's own file header). main.c's engine_state must not leave
 * ENGINE_STATE_CRANK_SYNC, and no injector/ignition channel should be
 * armed, while this is still 0. */
uint32_t injection_crank_period_ticks(void);
int injection_crank_synced(void);

/* Real INTC vector handlers for this board's two real, shared eMIOS_0
 * capture interrupt vectors (see intc.h's file header for the real
 * channel-pairing finding: IRQ 141 = crank/cam1 channels 0+1's shared
 * FLAG, IRQ 150 = cam2/unused channel 18+19's). Each checks every
 * channel sharing its vector via emios_flag_is_set() before calling the
 * matching *_capture_isr() above. Register with intc_register_isr() -
 * see main.c. */
void intc_isr_emios0_ch0_1(void);
void intc_isr_emios0_ch18_19(void);

#endif /* INJECTION_H */
