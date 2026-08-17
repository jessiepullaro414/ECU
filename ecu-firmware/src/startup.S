/*
 * startup.S - real reset entry (crt0) for the MPC5606B (e200z0h, VLE).
 *
 * Runs before main() and before anything in hardware_init(). Ordering
 * here is not stylistic - each step below is blocked on the one above.
 *
 * 1. RCHW + reset vector. Chapter 5 / Figure 5-2: the SSCM searches
 *    each boot sector for BOOT_ID = 0x5A in bits 8:15 of the word at
 *    the sector base, then jumps to the 32-bit vector at offset 0x4.
 *    PowerPC numbers bits MSB-first, so that word is 0x005A0000. If it
 *    is wrong the SSCM finds no valid sector, hands over to the BAM and
 *    parks the core in static mode - the chip never runs your code and
 *    gives no other clue why.
 *
 * 2. SRAM ECC initialisation, BEFORE the stack exists. This is the step
 *    that is easy to get wrong and expensive to debug. Section 31.5 is
 *    explicit: "Because the ECC bits can contain random data after the
 *    device is powered on, the SRAM must be initialized by executing
 *    32-bit write operations prior to any read accesses. This is also
 *    true for implicit read accesses caused by any write accesses of
 *    less than 32 bits." A stack push is a write, but a stack frame is
 *    read back on the way out - so touching the stack before SRAM is
 *    initialised risks an ECC error on uninitialised syndrome bits.
 *    Everything in this step therefore runs out of registers only: no
 *    stack, no memory reads, no calls.
 *
 *    Section 31.6 adds the constraint that writes "must specify an even
 *    number of registers performed on 32-bit word-aligned boundaries",
 *    which is exactly what a store-multiple of the full register file
 *    does: e_stmw r0,0(rN) writes r0..r31 = 128 bytes per iteration,
 *    32-bit aligned, even count. 80 KB / 128 = 640 iterations.
 *
 *    The register contents are deliberately irrelevant - the point is
 *    to make the ECC syndrome match the data, not to store a value.
 *
 * 3. Stack pointer. Only legal once step 2 has run. The EABI wants the
 *    stack 8-byte aligned with a NULL back-chain word at the bottom of
 *    the frame so a debugger's backtrace terminates instead of walking
 *    into garbage.
 *
 * 4. Small-data base. GCC addresses .sdata/.sbss as offsets from r13
 *    (__SDA_BASE__) under the PowerPC EABI. If r13 is left at its reset
 *    value, every small-data access silently targets the wrong address.
 *
 * 5. .data copy (flash LMA -> SRAM VMA) and .bss zeroing, using the
 *    symbols the linker script exports. Skipped cleanly when a section
 *    is empty, which .data legitimately is on some builds.
 *
 * 6. Call main(). If main ever returns - it should not, it ends in an
 *    infinite loop - stop rather than execute whatever follows in
 *    flash.
 *
 * VLE MNEMONICS. This core is VLE-only, so classic Book E mnemonics do
 * not assemble; that is a real bug this project already hit once in
 * intc.S and fixed by cross-checking Appendix B of the VLE PEM. Every
 * instruction used here has now been checked the better way - by
 * assembling it with the real powerpc-eabivle toolchain.
 */

    .section .bootsector, "a"
    .align 2
    .long   0x005A0000          /* RCHW: BOOT_ID = 0x5A in bits 8:15 */
    .long   _start              /* 32-bit reset vector */

    .section .text.startup, "ax"
    .globl  _start
    .type   _start, @function

_start:
    /* ---- 2. SRAM ECC init: 32-bit writes across all 80 KB --------- */
    e_lis   3, __SRAM_START@h
    e_or2i  3, __SRAM_START@l
    e_lis   4, __SRAM_SIZE@h
    e_or2i  4, __SRAM_SIZE@l
    e_srwi  4, 4, 7           /* /128 bytes written per e_stmw */
    mtctr   4

sram_init_loop:
    e_stmw  0, 0(3)           /* 0..31 -> 128 bytes, 32-bit aligned */
    e_add16i 3, 3, 128
    e_bdnz  sram_init_loop

    /* ---- 3. Stack pointer (SRAM is now safe to read) -------------- */
    e_lis   1, (__STACK_TOP - 16)@h
    e_or2i  1, (__STACK_TOP - 16)@l
    e_li    0, 0
    e_stw   0, 0(1)           /* NULL back chain terminates backtraces */

    /* ---- 4. EABI small-data bases --------------------------------- */
    e_lis   13, __SDA_BASE__@h
    e_or2i  13, __SDA_BASE__@l
    e_lis   2, __SDA2_BASE__@h
    e_or2i  2, __SDA2_BASE__@l

    /* ---- 5a. Copy .data from its flash load address into SRAM ----- */
    e_lis   3, __DATA_START@h
    e_or2i  3, __DATA_START@l
    e_lis   4, __DATA_LOAD@h
    e_or2i  4, __DATA_LOAD@l
    e_lis   5, __DATA_END@h
    e_or2i  5, __DATA_END@l
    se_cmp  3, 5
    se_bge  data_copy_done

data_copy_loop:
    e_lwz   6, 0(4)
    e_stw   6, 0(3)
    e_add16i 3, 3, 4
    e_add16i 4, 4, 4
    se_cmp  3, 5
    se_blt  data_copy_loop
data_copy_done:

    /* ---- 5b. Zero .bss -------------------------------------------- */
    e_lis   3, __BSS_START@h
    e_or2i  3, __BSS_START@l
    e_lis   4, __BSS_END@h
    e_or2i  4, __BSS_END@l
    e_li    5, 0
    se_cmp  3, 4
    se_bge  bss_zero_done

bss_zero_loop:
    e_stw   5, 0(3)
    e_add16i 3, 3, 4
    se_cmp  3, 4
    se_blt  bss_zero_loop
bss_zero_done:

    /* ---- 6. Into C ------------------------------------------------ */
    e_bl    main

    /* main() is not expected to return. If it does, stop here rather
     * than run off into whatever follows in flash. */
_startup_halt:
    se_b    _startup_halt

    .size   _start, . - _start

/* GCC emits a call to __eabi at the top of main() on PowerPC EABI
 * targets. Its normal job is to set up the small-data base registers and
 * run static constructors - both of which are already handled here:
 * steps 3-5 above set r13/r2 and initialise .data/.bss, and this
 * firmware is C with no static constructors to run. Supplying a no-op
 * rather than pulling in libgcc's version keeps the whole startup path
 * in this file, where it can be read in one place.
 *
 * This has to exist as a real symbol either way - without it the link
 * fails on an undefined reference to __eabi, which is exactly how it
 * was found. */
    .globl  __eabi
    .type   __eabi, @function
__eabi:
    se_blr
    .size   __eabi, . - __eabi
