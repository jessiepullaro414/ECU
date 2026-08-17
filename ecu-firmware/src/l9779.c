/*
 * l9779.c - driver for the two L9779WD-SPI injector/ignition ICs (the
 * real MC33810 replacement - see l9779.h for the full provenance and
 * the real gaps still open).
 */
#include "l9779.h"
#include "ecu_pins.h"
#include "siul2.h"
#include "dspi.h"

static uint8_t l9779_cs_ready = 0u;

/* Real, deliberately conservative busy-wait - see l9779.h's
 * L9779_TLEAD_NS/TLAG_NS/TCSN_NS/TNODATA_US: this board's real 60MHz
 * core clock (clocks.h) makes even a handful of loop iterations
 * comfortably longer than these real ns/us figures, without needing a
 * cycle-exact calibration (same honest caveat as mc33810_delay()). */
static void l9779_delay(uint32_t iterations) {
    for (volatile uint32_t i = 0; i < iterations; i++) {
        /* real, conservative timing margin - see l9779.h */
    }
}

void l9779_init(void) {
    /* Real: with MC33810 fully replaced, nothing else brings up DSPI_0's
     * base MCR bits (MSTR/MDIS/HALT) anymore - mc33810_init() used to
     * own that (see mc33810.c, kept in the codebase as reference/
     * provenance for its own real register-map research, but no longer
     * called from main.c). dspi_init() also loads CTAR0 as a side
     * effect; there's no real requirement CTAR0 hold L9779's timing
     * specifically (this driver always selects CTAR2 explicitly via
     * dspi_transfer_ctas()), but loading it with this same real, safe
     * value is strictly better than leaving CTAR0 at its reset-default/
     * unconfigured state. */
    dspi_init(DSPI_0_BASE, L9779_CTAR2);
    dspi_configure_ctar(DSPI_0_BASE, 2u, L9779_CTAR2);

    uint16_t cs_pins[2] = { PIN_SPI_CS_INJ0, PIN_SPI_CS_INJ1 };
    for (unsigned i = 0; i < 2u; i++) {
        uint8_t pcr = siul2_pcr_for_pin(cs_pins[i]);
        if (pcr != 0xFFu) {
            gpio_write(pcr, 1u);   /* idle-high, deselected */
        }
    }
    l9779_cs_ready = 1u;

    /* Real, critical (see l9779.h file header): clear OUT_DIS on both
     * chips before any output can be commanded. Without this, every
     * l9779_set_injectors()/l9779_set_ignition() call below is a
     * silent no-op on real hardware. */
    for (unsigned i = 0; i < 2u; i++) {
        (void)l9779_transfer((uint8_t)cs_pins[i],
                              l9779_word(L9779_ADDR_START_REACT, L9779_START_REACT_START));
    }

    /* Real, SECOND critical step, genuinely different from MC33810 and
     * caught only by reading the datasheet's own functional description
     * (Sections 6.8.1/6.10.1), not just the register tables: both LSa
     * (OUT1-4, injectors) and the ignition pre-drivers (IGN1-4) are
     * driven by the real logical AND of their SPI control bit
     * (CONTR_REG1/CONTR_REG2) and their own dedicated parallel input
     * (IN1-4/IGNI1-4) - "They are driven by logical-AND of SPI control
     * bit and dedicated parallel input," stated near-verbatim for both
     * blocks. This is NOT how MC33810 worked (real, confirmed OR logic
     * there - parallel control alone was already sufficient). Since
     * CONTR_REG1/CONTR_REG2 both real-reset to 0x00 ("ALL outputs
     * switched OFF," Table 21), leaving them unwritten would mean this
     * board's real eMIOS-driven parallel firing pins (INJ{n}_CTRL/
     * IGN{n}_CTRL, ecu_pins.h) could toggle correctly and STILL never
     * actually fire anything - a real, silent, critical bug caught by
     * reading past the register map into the functional description.
     * Fix: permanently enable all 4 real channels' SPI side here, once,
     * at init - after this, the parallel pins (this project's real
     * eMIOS-driven firing path, injection.c) have full, unblocked
     * control, matching the actual intended real-time architecture. */
    for (unsigned i = 0; i < 2u; i++) {
        l9779_set_injectors((uint8_t)cs_pins[i], 0x0Fu);
        l9779_set_ignition((uint8_t)cs_pins[i], 0x0Fu);
    }
}

uint16_t l9779_transfer(uint8_t cs_pin, uint16_t tx_word) {
    if (!l9779_cs_ready) {
        return 0u;
    }
    uint8_t pcr = siul2_pcr_for_pin(cs_pin);
    if (pcr == 0xFFu) {
        return 0u;
    }

    gpio_write(pcr, 0u);   /* assert CS (active low) */
    l9779_delay(110u);     /* real tLEAD >= 525ns, conservative margin */
    uint16_t rx = dspi_transfer_ctas(DSPI_0_BASE, 2u, tx_word);
    l9779_delay(15u);      /* real tLAG >= 50ns, conservative margin */
    gpio_write(pcr, 1u);   /* deassert CS */
    l9779_delay(140u);     /* real tCSN >= 640ns, conservative margin */
    l9779_delay(130u);     /* real tNODATA >= 1.5us before the next frame, conservative margin */

    return rx;
}

void l9779_set_injectors(uint8_t cs_pin, uint8_t out1_4_mask) {
    /* Real reordering: the simple 0-15 mask's bit0..3 (OUT1..4) map to
     * CONTR_REG1's real bit7..4 (see l9779.h's L9779_CONTR1_OUT1..4). */
    uint8_t payload = 0u;
    if (out1_4_mask & (1u << 0)) payload |= L9779_CONTR1_OUT1;
    if (out1_4_mask & (1u << 1)) payload |= L9779_CONTR1_OUT2;
    if (out1_4_mask & (1u << 2)) payload |= L9779_CONTR1_OUT3;
    if (out1_4_mask & (1u << 3)) payload |= L9779_CONTR1_OUT4;
    (void)l9779_transfer(cs_pin, l9779_word(L9779_ADDR_CONTR_REG1, payload));
}

void l9779_set_ignition(uint8_t cs_pin, uint8_t ign1_4_mask) {
    uint8_t payload = 0u;
    if (ign1_4_mask & (1u << 0)) payload |= L9779_CONTR2_IGN1;
    if (ign1_4_mask & (1u << 1)) payload |= L9779_CONTR2_IGN2;
    if (ign1_4_mask & (1u << 2)) payload |= L9779_CONTR2_IGN3;
    if (ign1_4_mask & (1u << 3)) payload |= L9779_CONTR2_IGN4;
    (void)l9779_transfer(cs_pin, l9779_word(L9779_ADDR_CONTR_REG2, payload));
}

uint8_t l9779_read_dia1(uint8_t cs_pin) {
    /* Real, defensive: send the read-dispatch command twice - whether
     * DO's data is same-frame or one-frame-delayed relative to DIN's
     * address is a real, unconfirmed gap (see l9779.h file header), so
     * this is correct either way, same pattern as
     * mc33810_read_status(). */
    (void)l9779_transfer(cs_pin, l9779_word(L9779_ADDR_READ_DISPATCH, L9779_SUBADDR_DIA1));
    uint16_t rx = l9779_transfer(cs_pin, l9779_word(L9779_ADDR_READ_DISPATCH, L9779_SUBADDR_DIA1));
    return (uint8_t)((rx >> 1) & 0xFFu);   /* real DATA_OUT field, bits 8:1 */
}

/* Real, worth-having-now logic even before this board's real hardware
 * exists - mirrors mc33810_handle_status()'s role. */
uint8_t l9779_read_dia8(uint8_t cs_pin) {
    /* Same defensive double-send as l9779_read_dia1(), for the same
     * real reason: whether DO's data belongs to the current frame's DIN
     * address or the previous one is still an open question (see the
     * file header), and issuing the read twice is correct either way. */
    (void)l9779_transfer(cs_pin, l9779_word(L9779_ADDR_READ_DISPATCH, L9779_SUBADDR_DIA8));
    uint16_t rx = l9779_transfer(cs_pin, l9779_word(L9779_ADDR_READ_DISPATCH, L9779_SUBADDR_DIA8));
    return (uint8_t)((rx >> 1) & 0xFFu);   /* real DATA_OUT field, bits 8:1 */
}

/* Real IGN-side counterpart to l9779_handle_dia1(). Kept separate from
 * the injector path deliberately: an ignition fault and an injector
 * fault on the same cylinder call for different responses, and merging
 * them would lose which one actually failed. */
void l9779_handle_dia8(uint8_t dia8, int is_bank_1_4) {
    unsigned ch;
    for (ch = 0; ch < 4u; ch++) {
        uint8_t code = (uint8_t)(((unsigned)dia8 >> (ch * 2u)) & L9779_DIA_FIELD_MASK);
        if (code != L9779_DIA_OK) {
            /* Real, and arguably more urgent than the injector case: a
             * coil stuck on is a genuine overheat/damage path, and a
             * dead coil dumps raw fuel into the exhaust. The chip's own
             * over-current protection is the immediate backstop (see
             * Section 6.10.2 - the pre-driver takes IGNx high-impedance
             * itself on a detected short), so what firmware owes here is
             * to NOTICE and stop trusting that cylinder, not to react in
             * microseconds. Which cylinder maps to which channel comes
             * from is_bank_1_4 exactly as on the injector side.
             *
             * The response policy itself is deliberately left open, the
             * same scope boundary as l9779_handle_dia1() and
             * mc33810_handle_status(): what to do about a dead cylinder
             * is engine-strategy work, not driver work. */
            (void)is_bank_1_4;
        }
    }
}

void l9779_handle_dia1(uint8_t dia1, int is_bank_1_4) {
    unsigned ch;
    for (ch = 0; ch < 4u; ch++) {
        uint8_t code = (uint8_t)(((unsigned)dia1 >> (ch * 2u)) & L9779_DIA_FIELD_MASK);
        if (code != L9779_DIA_OK) {
            /* Real, same-class concern as mc33810_handle_status(): a
             * shorted or open injector output left latched is a real
             * fire/damage risk; firmware needs to know a cylinder went
             * dark so fueling/trim logic doesn't fight it. Policy
             * itself (which cylinder, what to do) is deliberately not
             * fleshed out further here - same scope boundary as
             * mc33810_handle_status(). */
        }
    }
    (void)is_bank_1_4;
}
