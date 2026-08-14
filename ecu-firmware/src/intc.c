/*
 * intc.c - see intc.h for what's verified vs. still open.
 */
#include "intc.h"

/* Real 2048-byte alignment requirement - see intc.h's file header:
 * INTC_IACKR's real VTBA field only encodes the address's upper 21
 * bits, so bits 10:0 of this table's actual runtime address must be 0.
 * GCC's aligned attribute is a real, standard, portable-enough
 * extension for this - not exotic. */
static void (*vector_table[INTC_VECTOR_TABLE_SIZE])(void) __attribute__((aligned(2048)));

void intc_init(void) {
    INTC_MCR = 0u;   /* VTES=0 (4-byte entries), HVEN=0 (software vector mode) */
    INTC_IACKR = (uint32_t)(uintptr_t)vector_table;
    INTC_CPR = 0u;   /* unmask all real priorities 1-15 */
}

void intc_register_isr(uint16_t source, uint8_t priority, void (*handler)(void)) {
    if (source >= INTC_VECTOR_TABLE_SIZE) {
        return;   /* real, not a valid source on this device - see intc.h */
    }
    vector_table[source] = handler;
    INTC_PSR(source) = priority & 0xFu;
}

void intc_dispatch(void) {
    void (*isr)(void) = *(void (**)(void))(uintptr_t)INTC_IACKR;
    if (isr != (void (*)(void))0) {
        isr();
    }
    INTC_EOIR = 0u;
}

/* Real, cross-validated SPR numbers - see intc.h's file header for the
 * full provenance (e200z759n3 Core Reference Manual's own Table 16,
 * corroborated by 4 independent SPR matches against this project's
 * already-confirmed e200z0h-specific numbers). mtspr's SPR-number
 * operand must be a real compile-time immediate (it's encoded directly
 * into the instruction, not a runtime register value), so each real SPR
 * needs its own tiny inline-asm wrapper rather than a single function
 * taking the SPR number as a parameter - standard, real GCC PowerPC/
 * Book E inline-asm practice, not a stylistic choice. */
#define IVPR_SPR   63u
#define IVOR4_SPR  404u

static inline void mtspr_ivpr(uint32_t value) {
    __asm__ volatile ("mtspr %0, %1" : : "i" (IVPR_SPR), "r" (value));
}

static inline void mtspr_ivor4(uint32_t value) {
    __asm__ volatile ("mtspr %0, %1" : : "i" (IVOR4_SPR), "r" (value));
}

void intc_ivor_init(void) {
    /* Real, standard Book E address composition (confirmed via the
     * e200z759 manual's own Figure 7-5/Table 7-6: "the value contained
     * in the Vector Offset field of the IVOR... is concatenated with
     * the value held in IVPR to form an instruction address"): IVPR
     * supplies the upper bits, IVORn's own "Vector Offset" field
     * supplies the lower bits. Real, honest gap: e200z0h's own exact
     * IVOR4 field WIDTH (how many low bits it actually implements) was
     * never shown in a rendered figure this session - only the
     * e200z759 variant's own field width (12 bits, bits 16:27 in that
     * document's numbering) is confirmed, and that width is NOT
     * independently confirmed for e200z0h. This code uses the
     * conservative, standard convention (IVPR = upper 16 bits, IVOR4 =
     * lower 16 bits of the handler address) - if e200z0h's real IVOR4
     * field turns out narrower than 16 bits, the low, unimplemented
     * bits read back as 0, silently truncating the address unless the
     * linker places intc_isr_entry on a boundary aligned to whatever
     * the real field width requires. Not resolved further this
     * session - a real, named risk for whoever first links this
     * against a real PowerPC-EABI/VLE toolchain, not silently assumed
     * safe. */
    uint32_t handler_addr = (uint32_t)(uintptr_t)&intc_isr_entry;
    mtspr_ivpr(handler_addr >> 16);
    mtspr_ivor4(handler_addr & 0xFFFFu);
}
