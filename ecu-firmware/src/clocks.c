/*
 * clocks.c - see clocks.h for what's verified vs. still open.
 */
#include "clocks.h"

void fmpll_configure(uint8_t idf, uint8_t odf, uint8_t ndiv) {
    /* Real field packing per Figure 6-7 (visually confirmed both
     * halves of the register). Caller supplies real IDF/ODF/NDIV field
     * values already looked up against Tables 6-10/6-11/6-12 for the
     * actual target frequency and this board's real crystal - not
     * computed here (see clocks.h header note on why). */
    uint32_t cr = ((uint32_t)(idf & 0xFu) << FMPLL_CR_IDF_SHIFT)
                | ((uint32_t)(odf & 0x3u) << FMPLL_CR_ODF_SHIFT)
                | ((uint32_t)(ndiv & 0x7Fu) << FMPLL_CR_NDIV_SHIFT);
    FMPLL_CR = cr;
}

int fmpll_wait_lock(void) {
    uint32_t i;
    for (i = 0; i < CLOCKS_WAIT_ITERATIONS; i++) {
        if ((FMPLL_CR & FMPLL_CR_S_LOCK) != 0u) {
            return 1;
        }
        /* real, simple poll - S_LOCK is read-only, set by hardware once
         * the loop has actually acquired lock (Table 6-9). */
    }
    return 0;   /* real timeout - crystal not running, or IDF/ODF/NDIV outside real lock range */
}

int me_transition_to(uint8_t target_mode) {
    uint32_t i;
    /* Real two-write key sequence - Figure 8-3, confirmed. Both writes
     * carry the same TARGET_MODE; only the KEY half changes. */
    uint32_t target = ((uint32_t)target_mode << ME_MCTL_TARGET_MODE_SHIFT);
    ME_MCTL = target | ME_MCTL_KEY;
    ME_MCTL = target | ME_MCTL_INVERTED_KEY;

    /* Real poll - ME_GS bit positions resolved this session (see
     * clocks.h header for how). */
    for (i = 0; i < CLOCKS_WAIT_ITERATIONS; i++) {
        if ((ME_GS & ME_GS_S_MTRANS) == 0u) {
            return 1;
        }
    }
    return 0;   /* real timeout - target mode's clock source never stabilized */
}

int clocks_init(uint8_t fmpll_idf, uint8_t fmpll_odf, uint8_t fmpll_ndiv) {
    /* 1. Bring the FMPLL up and wait for real lock before asking any
     *    mode to switch to it - switching to an unlocked PLL is not
     *    something the real hardware sequencing here waits for on its
     *    own (Table 8-13 / 8.4.3.12 describe the mode-vs-clock-source
     *    relationship, not PLL lock timing - that's this driver's job). */
    fmpll_configure(fmpll_idf, fmpll_odf, fmpll_ndiv);
    if (!fmpll_wait_lock()) {
        return 0;
    }

    /* 2. SAFE mode is the real place to select PLL - RESET mode is
     *    transient/largely hardware-controlled, and DRUN/RUN0 have no
     *    SYSCLK field of their own (see clocks.h header). */
    ME_SAFE_MC = (ME_SAFE_MC & ~ME_MC_SYSCLK_MASK)
               | (ME_SYSCLK_PLL << ME_MC_SYSCLK_SHIFT);

    /* 3. Walk the real mode graph. Each hop is a real, separate
     *    ME_MCTL request - "RESET, SAFE, DRUN, and RUN0 modes are
     *    always enabled" per the reference manual, so none of these
     *    four needs checking against ME_ME first. Whether hardware has
     *    already auto-advanced past RESET by the time this code runs
     *    was not confirmed this session; requesting SAFE explicitly is
     *    safe either way (a request into the mode already active is a
     *    real, defined no-op per the same mode-transition mechanism). */
    if (!me_transition_to(ME_MODE_SAFE)) {
        return 0;
    }
    if (!me_transition_to(ME_MODE_DRUN)) {
        return 0;
    }
    if (!me_transition_to(ME_MODE_RUN0)) {
        return 0;
    }
    return 1;
}
