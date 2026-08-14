/*
 * flexcan.c - see flexcan.h for what's verified vs. still open.
 */
#include "flexcan.h"

void flexcan_init(uint32_t base, uint32_t ctrl_value) {
    /* Real: explicit Freeze entry (module resets this way already, but
     * do it for real if this is called after a previous run) with
     * MDIS=0 (module clocks enabled) - Freeze+Halt is what lets CTRL/MB
     * be safely configured before the module starts running. Real
     * reset-default MAXMB (15, giving MB0-15) is kept explicitly rather
     * than left implicit. */
    FLEXCAN_MCR(base) = FLEXCAN_MCR_FRZ | FLEXCAN_MCR_HALT | FLEXCAN_MCR_SUPV
                       | (15u << FLEXCAN_MCR_MAXMB_SHIFT);
    while ((FLEXCAN_MCR(base) & FLEXCAN_MCR_FRZ_ACK) == 0u) {
        /* wait for the module to actually acknowledge Freeze mode */
    }

    FLEXCAN_CTRL(base) = ctrl_value;

    /* Real, known starting state: every usable MB's C/S word cleared to
     * INACTIVE before flexcan_transmit()/flexcan_receive_poll() claims
     * one. */
    for (uint8_t mb = 0; mb <= 15u; mb++) {
        FLEXCAN_MB_CS(base, mb) = (FLEXCAN_CODE_RX_INACTIVE << FLEXCAN_CS_CODE_SHIFT);
    }

    FLEXCAN_RXGMASK(base) = 0u;   /* accept-all - see file header */

    FLEXCAN_MCR(base) &= ~(FLEXCAN_MCR_FRZ | FLEXCAN_MCR_HALT);   /* exit Freeze, start running */
    while ((FLEXCAN_MCR(base) & FLEXCAN_MCR_NOT_RDY) != 0u) {
        /* real, simple poll - no timeout, see file header */
    }
}

int flexcan_transmit(uint32_t base, uint8_t mb, uint32_t id, int extended,
                      const uint8_t *data, uint8_t len) {
    if (len > 8u) {
        len = 8u;
    }

    uint32_t id_word;
    uint32_t cs_word = (FLEXCAN_CODE_TX_INACTIVE << FLEXCAN_CS_CODE_SHIFT);
    if (extended) {
        id_word = id & FLEXCAN_ID_EXT_MASK;
        cs_word |= FLEXCAN_CS_IDE | FLEXCAN_CS_SRR;   /* SRR must be 1 for extended Tx frames */
    } else {
        id_word = (id << FLEXCAN_ID_STD_SHIFT) & FLEXCAN_ID_STD_MASK;
    }

    /* Real ordering: mark INACTIVE first, then load ID/data, then the
     * real CODE that actually starts transmission - the same
     * deactivate-before-load discipline Section 25.5.7 describes for
     * MB coherence. */
    FLEXCAN_MB_CS(base, mb) = cs_word;
    FLEXCAN_MB_ID(base, mb) = id_word;
    for (uint8_t i = 0; i < len; i++) {
        FLEXCAN_MB_DATA(base, mb)[i] = data[i];
    }

    cs_word = (cs_word & ~FLEXCAN_CS_CODE_MASK) | (FLEXCAN_CODE_TX_ONCE << FLEXCAN_CS_CODE_SHIFT)
            | ((uint32_t)len << FLEXCAN_CS_LENGTH_SHIFT);
    FLEXCAN_MB_CS(base, mb) = cs_word;

    /* Real completion signal (Table 25-6): after a successful one-shot
     * transmit, CODE automatically returns to TX_INACTIVE. Real, bounded
     * timeout - see flexcan.h's file header. */
    uint32_t i;
    for (i = 0; i < FLEXCAN_WAIT_ITERATIONS; i++) {
        if (((FLEXCAN_MB_CS(base, mb) & FLEXCAN_CS_CODE_MASK) >> FLEXCAN_CS_CODE_SHIFT)
            == FLEXCAN_CODE_TX_INACTIVE) {
            return 1;
        }
    }
    return 0;   /* real timeout - no ACKing node on the bus, or bus-off */
}

int flexcan_receive_poll(uint32_t base, uint8_t mb, uint32_t *id_out,
                          int *extended_out, uint8_t *data_out, uint8_t *len_out) {
    uint32_t cs = FLEXCAN_MB_CS(base, mb);
    uint32_t code = (cs & FLEXCAN_CS_CODE_MASK) >> FLEXCAN_CS_CODE_SHIFT;

    if (code == FLEXCAN_CODE_RX_INACTIVE) {
        /* Not armed yet (e.g. first call after flexcan_init()) - arm it
         * and report nothing received this call. */
        FLEXCAN_MB_CS(base, mb) = (FLEXCAN_CODE_RX_EMPTY << FLEXCAN_CS_CODE_SHIFT);
        return 0;
    }
    if (code != FLEXCAN_CODE_RX_FULL) {
        return 0;   /* still EMPTY, or transiently BUSY - nothing to read yet */
    }

    /* Real lock-safe read sequence (Section 25.5.7.3, see file header):
     * re-reading the C/S word here (not reusing the `cs` sampled above
     * for the code check) is exactly what locks the MB for the atomic
     * read that follows - reading a stale local copy would skip the
     * real hardware lock. */
    cs = FLEXCAN_MB_CS(base, mb);
    uint32_t id_word = FLEXCAN_MB_ID(base, mb);

    int is_ext = (cs & FLEXCAN_CS_IDE) != 0u;
    uint8_t len = (uint8_t)((cs & FLEXCAN_CS_LENGTH_MASK) >> FLEXCAN_CS_LENGTH_SHIFT);
    if (len > 8u) {
        len = 8u;
    }
    for (uint8_t i = 0; i < len; i++) {
        data_out[i] = FLEXCAN_MB_DATA(base, mb)[i];
    }

    (void)FLEXCAN_TIMER(base);   /* real global unlock - see file header */

    *id_out = is_ext ? (id_word & FLEXCAN_ID_EXT_MASK)
                      : ((id_word & FLEXCAN_ID_STD_MASK) >> FLEXCAN_ID_STD_SHIFT);
    *extended_out = is_ext;
    *len_out = len;

    FLEXCAN_MB_CS(base, mb) = (FLEXCAN_CODE_RX_EMPTY << FLEXCAN_CS_CODE_SHIFT);   /* re-arm */

    return 1;
}
