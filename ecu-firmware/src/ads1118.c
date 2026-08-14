/*
 * ads1118.c - see ads1118.h for the full real provenance, the register
 * facts' datasheet citations, and the one deliberately-open gap (NIST
 * ITS-90 linearisation).
 */
#include "ads1118.h"
#include "dspi.h"
#include "siul2.h"
#include "ecu_pins.h"

/* Real 32-bit transaction (datasheet Section 9.5.7.1): four bytes -
 * two of conversion result followed by two of Config readback, MSB
 * first. This DSPI driver's frames are 16 bits wide, so one real
 * transaction is two back-to-back 16-bit frames inside a single CS
 * assertion. The config word is written in the first frame; the second
 * frame's transmitted value is the same config again, which the
 * datasheet explicitly permits ("Write the same Config register setting
 * twice during one transmission cycle") and which keeps the device's
 * configuration unambiguous no matter which half it latches.
 *
 * CS is a plain GPIO here, not a DSPI hardware PCS - this board has no
 * hardware chip selects (see dspi.h's own note), which is also why the
 * device's real tCSSC/tSCCS/tCSH minimums (100/100/200ns) are satisfied
 * for free: software GPIO toggling around a transfer is far slower than
 * any of them. */
static uint16_t ads1118_transaction(uint16_t config, uint16_t *config_readback) {
    uint8_t pcr = siul2_pcr_for_pin(PIN_SPI_CS_EGT);
    if (pcr == 0xFFu) {
        return 0u;
    }

    gpio_write(pcr, 0u);                                  /* assert CS (active low) */
    uint16_t result = dspi_transfer_ctas(DSPI_0_BASE, 3u, config);
    uint16_t rb     = dspi_transfer_ctas(DSPI_0_BASE, 3u, config);
    gpio_write(pcr, 1u);                                  /* deassert CS */

    if (config_readback != 0) {
        *config_readback = rb;
    }
    return result;
}

void ads1118_init(void) {
    dspi_configure_ctar(DSPI_0_BASE, 3u, ADS1118_CTAR3);

    /* Real: park CS inactive-high before anything else touches the bus.
     * The board also fits a 10k pull-up on this net (ecu-pcb R77) for
     * the window before firmware gets here at all - the MCU's GPIOs are
     * high-impedance inputs during reset, and a floating CS can read as
     * asserted and let bus noise clock in a garbage config write. */
    uint8_t pcr = siul2_pcr_for_pin(PIN_SPI_CS_EGT);
    if (pcr != 0xFFu) {
        gpio_write(pcr, 1u);
    }
}

int16_t ads1118_read_thermocouple_raw(void) {
    /* First transaction starts the single-shot conversion. Its returned
     * data is the PREVIOUS conversion, so a second transaction is issued
     * to collect this one - the same defensive two-transfer pattern
     * mc33810_read_status()/l9779_read_dia() already use, and it is
     * correct here regardless of pipelining depth. */
    (void)ads1118_transaction(ADS1118_CFG_THERMOCOUPLE, 0);
    return (int16_t)ads1118_transaction(ADS1118_CFG_THERMOCOUPLE, 0);
}

int16_t ads1118_read_coldjunction_centiC(void) {
    (void)ads1118_transaction(ADS1118_CFG_COLDJUNCTION, 0);
    uint16_t raw = ads1118_transaction(ADS1118_CFG_COLDJUNCTION, 0);

    /* Real format (Table 4): 14 bits, LEFT-justified in the 16-bit
     * result, two's complement, 0.03125 C/LSB. Right-align by 2 with an
     * ARITHMETIC shift so the sign is preserved, then scale. Working in
     * hundredths of a degree keeps this integer-only: one count is
     * 0.03125 C = 3.125 centi-C, so multiply by 100 and divide by 32. */
    int16_t counts = (int16_t)raw >> ADS1118_CJ_SHIFT;
    return (int16_t)(((int32_t)counts * 100) / 32);
}

int32_t ads1118_raw_to_nanovolts(int16_t raw) {
    /* Real: +/-256mV FSR over a signed 16-bit result = 7.8125 uV/LSB.
     * At full scale this is 32767 * 7813 = ~256 mV, comfortably inside
     * int32_t, so no overflow concern. */
    return (int32_t)raw * (int32_t)ADS1118_TC_NV_PER_LSB;
}
