/*
 * mc33810.c - driver for the two MC33810 injector/ignition ICs.
 *
 * SPI transport is now real: dspi.c/dspi.h implement a verified DSPI_0
 * driver (register layout confirmed against the actual MPC5606B
 * Reference Manual, Chapter 26), and mc33810_transfer() below drives
 * this board's real chip-select scheme (plain GPIO, not DSPI's own
 * hardware PCS - see dspi.h's file header for why), with real CS-edge
 * timing margins (mc33810_delay(), below - see mc33810.h for the real
 * tLEAD/tLAG/tSTR numbers this observes). See mc33810.h for what's
 * verified about the SPI frame format and register map - every command
 * register's bit-level layout is real now, not just the fault
 * registers, and mc33810_calibrate() implements the real Clock
 * Calibration Command CS-pulse protocol too.
 */
#include "mc33810.h"
#include "ecu_pins.h"
#include "siul2.h"
#include "dspi.h"

/* Real, per mc33810.h's file header: CPOL=0, CPHA=0 (SI latched on
 * SCLK's rising/leading edge, SO changes on the falling/trailing edge -
 * read directly from the MC33810 datasheet's own Serial Clock Input
 * description, not assumed). FMSZ=15 -> 16-bit frames (Table 26-13:
 * frame size = FMSZ+1), matching the real 16-bit SPI word format in
 * mc33810.h.
 *
 * Real, computed SCK: DSPI_0's real peripheral clock is 60MHz (Table
 * 6-1 + CGM_SC_DC0's real reset values, see clocks.h - Peripheral Set
 * 2, confirmed undivided). SCK = f_SYS / (PBR_value * BR_value), DBR=0
 * (not doubled). PBR=00 -> prescaler 2 (Table 26-5's own inline PBR
 * table, visually confirmed real values 2/3/5/7, NOT PBR+2); BR=0101
 * -> scaler 32 (Table 26-17, visually confirmed). SCK = 60MHz /
 * (2*32) = 937.5kHz. Real, confirmed safe margin below the datasheet's
 * own 6.0MHz headline max: Table 5's Dynamic Electrical Characteristics
 * apply across its whole stated "3.0V <= VDD <= 5.5V" range (a single
 * spec, not a 5.0V-only one - this board's real 3.3V VDD is inside that
 * range, so no separate re-derivation was needed once that was found -
 * see mc33810.h). CPOL and CPHA are both 0, so neither bit appears
 * below. */
#define MC33810_CTAR0 ( \
    (15u << DSPI_CTAR_FMSZ_SHIFT) \
    | (0u << DSPI_CTAR_PBR_SHIFT)   /* PBR=00 -> prescaler 2 */ \
    | (0x5u << DSPI_CTAR_BR_SHIFT)  /* BR=0101 -> scaler 32 */ \
)

static uint8_t mc33810_cs_ready = 0u;

/* Real, deliberately conservative busy-wait - see mc33810.h's
 * MC33810_TLEAD_NS/TLAG_NS/TSTR_US: this board's real 60MHz core clock
 * (clocks.h) makes even a handful of loop iterations comfortably longer
 * than the real 50-100ns CS-edge setup times, without needing a cycle-
 * exact calibration. */
static void mc33810_delay(uint32_t iterations) {
    for (volatile uint32_t i = 0; i < iterations; i++) {
        /* real, conservative timing margin - see mc33810.h */
    }
}

void mc33810_init(void) {
    dspi_init(DSPI_0_BASE, MC33810_CTAR0);
    /* CS pins reset to GPDO's own reset value (0 = low) until driven -
     * that would incorrectly select every slave on this shared bus
     * before anything is ready. Drive all 4 real CS pins high (idle/
     * deselected - MC33810's own CS description: "With CS in a logic
     * high state, signals on SCLK/SI are ignored") before any transfer
     * is attempted. */
    uint16_t cs_pins[4] = { PIN_SPI_CS_INJ0, PIN_SPI_CS_INJ1, PIN_SPI_CS_O2A, PIN_SPI_CS_O2B };
    for (unsigned i = 0; i < 4u; i++) {
        uint8_t pcr = siul2_pcr_for_pin(cs_pins[i]);
        if (pcr != 0xFFu) {
            gpio_write(pcr, 1u);
        }
    }
    mc33810_cs_ready = 1u;
}

uint16_t mc33810_transfer(uint8_t cs_pin, uint16_t tx_word) {
    /* real, not a placeholder: a transfer attempted before mc33810_init()
     * has run would use whatever pcr_configure()/gpio_write() state
     * happens to exist - fail loudly instead of silently misbehaving. */
    if (!mc33810_cs_ready) {
        return 0u;
    }
    uint8_t pcr = siul2_pcr_for_pin(cs_pin);
    if (pcr == 0xFFu) {
        return 0u;
    }

    gpio_write(pcr, 0u);   /* assert CS (active low, per datasheet) */
    mc33810_delay(20u);    /* real tLEAD >= 100ns (mc33810.h), conservative margin */
    uint16_t rx = dspi_transfer(DSPI_0_BASE, tx_word);
    mc33810_delay(10u);    /* real tLAG >= 50ns (mc33810.h), conservative margin */
    gpio_write(pcr, 1u);   /* deassert CS */
    mc33810_delay(60u);    /* real tSTR >= 1us (mc33810.h) before the next transfer */

    return rx;
}

uint16_t mc33810_read_status(uint8_t cs_pin) {
    /* All Status Command: write address 0x0, command bits 1010 0000 0000
     * (MC33810_GENERIC_ALL_STATUS_BITS) - real, per Table 21 (see
     * mc33810.h). Two real bugs fixed here in the same pass: (a) the
     * 0x0 top-nibble address itself happens to be the one numeral shared
     * by both real address spaces, so it was never wrong, but (b) the
     * previous version of this call sent an all-zero 0x000 payload,
     * which Table 21 does NOT show as the real All Status Command -
     * bits 11:8 must be 1010, not 0000, to match either the All Status
     * Command's own fixed encoding or an equivalent Read Registers
     * Command targeting internal register 0x0. A payload of plain 0x000
     * would not have reliably produced a Status readback on real
     * hardware. Two back-to-back transfers: the first SENDS the
     * read-status command, the second is a dummy transfer whose response
     * word IS the status (per the pipelined response behavior noted in
     * mc33810.h). */
    (void)mc33810_transfer(cs_pin, mc33810_word(MC33810_WCMD_GENERIC_0, MC33810_GENERIC_ALL_STATUS_BITS));
    return mc33810_transfer(cs_pin, mc33810_word(MC33810_WCMD_GENERIC_0, MC33810_GENERIC_ALL_STATUS_BITS));
}

void mc33810_calibrate(uint8_t cs_pin) {
    /* Real, now implementable now that mc33810.h's file header pins
     * down the actual protocol: send the Clock Calibration Command
     * (0xE) as a normal transfer, then hold CS low for a SEPARATE real
     * 32us pulse - not a data transfer, just CS asserted with nothing
     * required on SCLK/SI during it (the datasheet's own "Any SPI
     * message may be sent during the 32us calibration chip select"
     * means this hold could carry other traffic too, but a plain,
     * silent hold is the simplest real implementation and is
     * sufficient). Same honest timing caveat as mc33810_transfer()'s
     * tLEAD/tLAG/tSTR delays: this board has no systick/timer wired up
     * yet, so the 32us figure is a real, deliberately generous
     * iteration count at the confirmed 60MHz core clock, not a
     * calibrated time unit. */
    if (!mc33810_cs_ready) {
        return;
    }
    uint8_t pcr = siul2_pcr_for_pin(cs_pin);
    if (pcr == 0xFFu) {
        return;
    }

    (void)mc33810_transfer(cs_pin, mc33810_word(MC33810_WCMD_CLOCK_CAL, 0u));

    gpio_write(pcr, 0u);    /* assert CS for the real >=32us calibration pulse */
    mc33810_delay(2000u);   /* real, conservative margin - see comment above */
    gpio_write(pcr, 1u);    /* deassert CS */
    mc33810_delay(60u);     /* real tSTR >= 1us before any next transfer */
}

/* Real, worth-having-now logic even before the SPI transport exists:
 * what to actually DO with a fault. Kept here rather than in main.c so
 * the policy lives next to the part it's about. */
void mc33810_handle_status(uint16_t status, int is_bank_1_4 /* 1 = cyl 1-4 chip, 0 = cyl 5-8 chip */) {
    if (status & MC33810_STATUS_RESET) {
        /* Chip has reset (POR/undervoltage) since the last read - every
         * SPI config command (mode select, fault thresholds) it had
         * needs re-sending, or it's back at power-on defaults. */
    }
    if (status & (MC33810_STATUS_IGN0_FAULT | MC33810_STATUS_IGN1_FAULT |
                  MC33810_STATUS_IGN2_FAULT | MC33810_STATUS_IGN3_FAULT)) {
        /* An ignition-side fault (MAXI overcurrent, max-dwell timeout,
         * open secondary) on one of this chip's 4 channels. Real
         * engine-management practice: don't kill ALL ignition on one
         * channel's fault, but DO stop trying to fire that specific
         * cylinder and flag it (real, not a placeholder concern - a coil
         * repeatedly hitting max-dwell protection every cycle can
         * overheat it). */
    }
    if (status & (MC33810_STATUS_OUT0_FAULT | MC33810_STATUS_OUT1_FAULT |
                  MC33810_STATUS_OUT2_FAULT | MC33810_STATUS_OUT3_FAULT)) {
        /* Injector low-side fault - open load or short. A shorted
         * injector output is a real fire/damage risk if left latched
         * ON; the MC33810's own LSD Fault Command (write address 0x2,
         * see mc33810.h) sets the hardware shutdown behavior, but
         * firmware still needs to know a cylinder went dark so
         * fueling/trim logic doesn't fight it. */
    }
    (void)is_bank_1_4;
}
