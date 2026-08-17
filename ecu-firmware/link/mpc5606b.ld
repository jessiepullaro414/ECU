/*
 * mpc5606b.ld - real linker script for the MPC5606B (e200z0h, VLE-only).
 *
 * Every address and size below is from the MPC5606BK Reference Manual,
 * Rev. 2, read directly - not carried over from a vendor template:
 *
 *   Code flash   0x0000_0000 .. 0x000F_FFFF   1024 KB  (Ch.4 memory map;
 *                the map then shows 0x0010_0000-0x001F_FFFF Reserved,
 *                which is what bounds the 1 MB figure)
 *   L2SRAM       0x4000_0000                    80 KB  (Table 31-3 and
 *                Section 31.1: "as much as 80 KB of general-purpose
 *                static RAM", base 0x4000_0000)
 *
 * BOOT, from Chapter 5 (Microcontroller Boot) and its Figure 5-2, which
 * was rendered as an image to read the bit positions rather than
 * trusting text extraction:
 *   - The SSCM searches each boot sector in turn for a valid BOOT_ID.
 *   - Boot sector 0 starts at 0x0000_0000. Within a boot sector:
 *       offset 0x0  RCHW      - BOOT_ID = 0x5A in bits 8:15
 *       offset 0x4  32-bit reset vector -> start address of user code
 *       offset 0x8  application code onward
 *   - PowerPC numbers bits MSB-first, so BOOT_ID in bits 8:15 of a
 *     32-bit word means the second-most-significant byte: the word is
 *     0x005A0000 with the reserved fields left zero. Get this wrong and
 *     the SSCM finds no valid BOOT_ID, gives up on all five boot
 *     sectors, and parks the core in static mode via the BAM - the chip
 *     simply never runs your code, with no other symptom.
 *
 * The .bootsector section below is therefore NOT decoration - it is the
 * only reason the part boots at all. KEEP() stops --gc-sections from
 * discarding it, since nothing in C ever references it.
 */

OUTPUT_ARCH(powerpc)
ENTRY(_start)

MEMORY
{
    flash (rx)  : ORIGIN = 0x00000000, LENGTH = 1024K
    sram  (rwx) : ORIGIN = 0x40000000, LENGTH = 80K
}

/* Real SRAM bounds, exported so startup.S can ECC-initialise exactly
 * this range. Section 31.5 requires every location be written with
 * 32-bit writes before ANY read, because the ECC syndrome bits power up
 * random - see startup.S for why this has to happen before the stack
 * exists. */
__SRAM_START = ORIGIN(sram);
__SRAM_SIZE  = LENGTH(sram);
__SRAM_END   = ORIGIN(sram) + LENGTH(sram);

/* Stack lives at the top of SRAM and grows down. 4 KB is generous for
 * this firmware (no recursion, no large stack frames, deepest path is
 * an eMIOS capture ISR through intc_dispatch into injection.c). */
__STACK_SIZE = 4K;
__STACK_TOP  = __SRAM_END;

SECTIONS
{
    /* Boot sector 0, at the very base of flash. */
    .bootsector 0x00000000 :
    {
        KEEP(*(.bootsector))
    } > flash

    .text :
    {
        . = ALIGN(4);
        *(.text)
        *(.text.*)
        . = ALIGN(4);
    } > flash

    .rodata :
    {
        . = ALIGN(4);
        *(.rodata)
        *(.rodata.*)
        /* Read-only small data. r2 must point at __SDA2_BASE__ per the
         * PowerPC EABI, the read-only counterpart to r13/__SDA_BASE__.
         * startup.S sets both. */
        __SDA2_BASE__ = . + 0x8000;
        *(.sdata2)
        *(.sdata2.*)
        *(.sbss2)
        *(.sbss2.*)
        . = ALIGN(4);
    } > flash

    /* Initialised data: stored in flash (LMA), lives in SRAM (VMA).
     * startup.S copies it across using the three symbols below. */
    .data : ALIGN(4)
    {
        __DATA_START = .;
        *(.data)
        *(.data.*)
        /* Small-data area. r13 must point at __SDA_BASE__ per the
         * PowerPC EABI, and GCC addresses .sdata/.sbss relative to it. */
        __SDA_BASE__ = . + 0x8000;
        *(.sdata)
        *(.sdata.*)
        . = ALIGN(4);
        __DATA_END = .;
    } > sram AT > flash

    __DATA_LOAD = LOADADDR(.data);

    .bss (NOLOAD) : ALIGN(4)
    {
        __BSS_START = .;
        *(.sbss)
        *(.sbss.*)
        *(.bss)
        *(.bss.*)
        *(COMMON)
        . = ALIGN(4);
        __BSS_END = .;
    } > sram

    /* intc.c's vector table asks the compiler for 2048-byte alignment
     * (INTC_IACKR's VTBA field only encodes the upper 21 bits of the
     * address, so anything less would silently truncate). It lands in
     * .bss above; this check makes a violation a link-time error rather
     * than a mystery at run time. */
    ASSERT((__BSS_START & 3) == 0, "BSS must be 4-byte aligned")

    /* Stack occupies the top of SRAM. Guard against .bss growing into
     * it - without this the overflow only shows up as corrupted
     * variables on real hardware. */
    ASSERT(__BSS_END <= (__STACK_TOP - __STACK_SIZE),
           "SRAM overflow: .data + .bss + stack exceed 80 KB")

    /DISCARD/ :
    {
        *(.comment)
        *(.note.*)
        *(.eh_frame)
    }
}
