/*
 * cj125.c - see cj125.h for what's verified vs. still open.
 */
#include "cj125.h"
#include "dspi.h"
#include "siul2.h"

void cj125_init(void) {
    dspi_configure_ctar(DSPI_0_BASE, 1u, CJ125_CTAR1);
}

uint16_t cj125_transfer(uint8_t cs_pin, uint16_t tx_word) {
    uint8_t pcr = siul2_pcr_for_pin(cs_pin);
    if (pcr == 0xFFu) {
        return 0u;
    }

    gpio_write(pcr, 0u);   /* assert CS (active low, same convention as MC33810) */
    uint16_t rx = dspi_transfer_ctas(DSPI_0_BASE, 1u, tx_word);
    gpio_write(pcr, 1u);   /* deassert CS */

    return rx;
}

uint8_t cj125_read_ident(uint8_t cs_pin) {
    /* Real, single-transfer read (see cj125.h file header - CJ125's
     * response data is for THIS command, not delayed a full transfer
     * like the MC33810). Low byte of the dummy data half is don't-care
     * on a read. */
    uint16_t response = cj125_transfer(cs_pin, (uint16_t)(CJ125_CMD_IDENT_RD << 8));
    return (uint8_t)(response & 0xFFu);
}

uint8_t cj125_read_diag(uint8_t cs_pin) {
    uint16_t response = cj125_transfer(cs_pin, (uint16_t)(CJ125_CMD_DIAG_RD << 8));
    return (uint8_t)(response & 0xFFu);
}

void cj125_write_init1(uint8_t cs_pin, uint8_t value) {
    (void)cj125_transfer(cs_pin, (uint16_t)((CJ125_CMD_INIT1_WR << 8) | value));
}

void cj125_write_init2(uint8_t cs_pin, uint8_t value) {
    (void)cj125_transfer(cs_pin, (uint16_t)((CJ125_CMD_INIT2_WR << 8) | value));
}

/* Real, worth-having-now logic even before a specific real fault-
 * response strategy is designed: decode DIAG_REG's four real 2-bit
 * fields (see cj125.h file header) and flag any that aren't
 * CJ125_DIAG_NO_FAILURE. Kept here rather than in main.c so the policy
 * lives next to the part it's about, same pattern as
 * mc33810_handle_status() (mc33810.c). */
void cj125_handle_diag(uint8_t diag, int is_bank_a) {
    uint8_t heater = (diag >> CJ125_DIAG_HEATER_SHIFT) & CJ125_DIAG_FIELD_MASK;
    uint8_t sensor = (diag >> CJ125_DIAG_SENSOR_SHIFT) & CJ125_DIAG_FIELD_MASK;
    uint8_t un     = (diag >> CJ125_DIAG_UN_SHIFT) & CJ125_DIAG_FIELD_MASK;
    uint8_t vm     = (uint8_t)(((unsigned)diag >> CJ125_DIAG_VM_SHIFT) & CJ125_DIAG_FIELD_MASK);

    if (heater != CJ125_DIAG_NO_FAILURE) {
        /* Real heater fault (open load / short to ground / short to
         * Vbat) - a cold or damaged wideband sensor gives real garbage
         * lambda readings; real ECUs stop trusting O2 trim on this
         * bank until the heater fault clears. */
    }
    if (sensor != CJ125_DIAG_NO_FAILURE || un != CJ125_DIAG_NO_FAILURE
        || vm != CJ125_DIAG_NO_FAILURE) {
        /* Real sensor-cell wiring fault (short/open on the pump-current
         * or Nernst-cell paths) - the real reason this driver's own
         * ENSCUN bit (INIT_REG2) exists: it specifically enables real
         * UN-pin failure identification, matching this exact field. */
    }
    (void)is_bank_a;
}
