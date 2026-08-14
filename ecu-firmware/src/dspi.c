/*
 * dspi.c - see dspi.h for what's verified vs. still open.
 */
#include "dspi.h"

void dspi_init(uint32_t base, uint32_t ctar0_value) {
    /* Real bring-up sequence per Chapter 26: DCONF=00 (SPI - the only
     * valid value, Table 26-3), MSTR=1 for master mode, MDIS cleared
     * (module is disabled at reset - Figure 26-3's own reset value),
     * HALT cleared last so the module doesn't start running mid-
     * configuration. CLR_TXF/CLR_RXF flush whatever was left in the
     * FIFOs from a previous configuration (both self-clearing writes,
     * Table 26-3). */
    DSPI_MCR(base) = DSPI_MCR_MSTR | DSPI_MCR_CLR_TXF | DSPI_MCR_CLR_RXF
                    | DSPI_MCR_HALT;   /* still halted while CTAR loads */

    DSPI_CTAR(base, 0) = ctar0_value;

    DSPI_MCR(base) &= ~(DSPI_MCR_HALT | DSPI_MCR_MDIS);  /* now run */
}

void dspi_configure_ctar(uint32_t base, uint8_t ctar_index, uint32_t ctar_value) {
    DSPI_CTAR(base, ctar_index) = ctar_value;
}

uint16_t dspi_transfer_ctas(uint32_t base, uint8_t ctas, uint16_t tx_data) {
    while ((DSPI_SR(base) & DSPI_SR_TFFF) == 0u) {
        /* TX FIFO full - real, simple poll (see dspi.h header: no DMA/
         * interrupt path yet, fine for main-loop-only SPI polling). */
    }
    /* PCS field left at 0: this board doesn't wire DSPI's own hardware
     * PCS lines to any real chip select (see dspi.h file header) -
     * chip select is the caller's own GPIO. */
    DSPI_PUSHR(base) = ((uint32_t)tx_data & DSPI_PUSHR_TXDATA_MASK)
                      | (((uint32_t)ctas << DSPI_PUSHR_CTAS_SHIFT) & DSPI_PUSHR_CTAS_MASK);
    DSPI_SR(base) = DSPI_SR_TFFF;   /* w1c */

    while ((DSPI_SR(base) & DSPI_SR_RFDF) == 0u) {
        /* wait for the response frame to land in the RX FIFO */
    }
    uint16_t rx = (uint16_t)(DSPI_POPR(base) & DSPI_POPR_RXDATA_MASK);
    DSPI_SR(base) = DSPI_SR_RFDF;   /* w1c */

    return rx;
}

uint16_t dspi_transfer(uint32_t base, uint16_t tx_data) {
    return dspi_transfer_ctas(base, 0u, tx_data);
}
