/*
 * swt.c - see swt.h for what's verified vs. still open.
 */
#include "swt.h"

void swt_init(void) {
    SWT_TO = SWT_TIMEOUT_CYCLES;
    /* Real: WEN=1 (enable), MAP0=1 (allow CPU to service it), KEY=0
     * (fixed sequence), WND=0 (non-windowed), ITR=0 (immediate reset -
     * see file header), RIA=0 (invalid access -> bus error, not a
     * reset - this driver never does an invalid access on purpose, so
     * either choice is fine; bus error is the more conservative real
     * default), HLK=0/SLK=0 (config stays writable). */
    SWT_CR = SWT_CR_WEN | SWT_CR_MAP0;
}

void swt_service(void) {
    SWT_SR = SWT_SERVICE_KEY1;
    SWT_SR = SWT_SERVICE_KEY2;
}
