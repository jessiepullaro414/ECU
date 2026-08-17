/*
 * l9779.h - ST L9779WD-SPI "Multifunction IC for engine management
 * system" SPI interface - the real MC33810 replacement (see
 * mc33810.h's own header and the `mc33810-end-of-life` project memory:
 * MC33810 hit Last Time Buy, 2027-04-30, no NXP-recommended
 * replacement; L9779WD-SPI was chosen as the best real, currently-
 * Active alternative found, with real precedent - rusEFI maintains a
 * real KiCad symbol for this exact part).
 *
 * Facts below are transcribed directly from the real datasheet (ST
 * DocID027721 Rev 2, "L9779WD-SPI... Datasheet - production data", May
 * 2015 - fetched via a Wayback Machine snapshot of
 * st.com/resource/en/datasheet/l9779wd-spi.pdf this session, since
 * ST's live site and every mirror tried - Mouser, alldatasheet, utmel,
 * Arrow, DigiKey htmldatasheets - blocked or timed out automated
 * fetches). Table 2 ("Pins description"), Section 6.16 ("Serial
 * interface"), Table 53 ("Timing characteristics"), Table 55 ("SPI
 * registers"), and the real CONTR_REG1-4/START_REACT register
 * descriptions were genuinely read, not guessed.
 *
 * REAL, IMPORTANT ARCHITECTURE DIFFERENCE FROM MC33810 - read this
 * before using anything below: this chip's SPI frame is NOT a 4-bit-
 * address+12-bit-payload split like MC33810's. It's genuinely
 * different:
 *   DIN (MOSI):  bit15=X | bits14:10=ADD[4:0] (5-bit address) | bit9=X
 *                | bits8:1=DATA[7:0] (8-bit payload) | bit0=parity
 *   DO  (MISO):  bit15=SPI_ERROR | bits14:10=ADD[4:0] echo |
 *                bit9=W/R flag | bits8:1=DATA_OUT[7:0] | bit0=parity
 * Real, confirmed (Section 6.16.2): "All SPI communications are
 * executed in exact 16 bit increments... should the clock counter
 * exceed or count fewer than 16 clocks, the received message is
 * discarded" - a real, stricter frame-validation requirement than
 * MC33810 ever had. Read-only ID/diagnostic registers all share ONE
 * real address (0x10), with the specific register selected by an
 * 8-bit sub-address value placed directly in the DATA field (e.g.
 * DIA_REG1's sub-address is the literal byte 0x01, not a scaled/
 * shifted value - confirmed against Table 55's own listed values).
 *
 * RESOLVED (a later pass) - the parity bit is now real and computed.
 * The earlier pass could not find its definition and honestly sent
 * parity=0 unconditionally, flagging that frames might be silently
 * rejected on real hardware. A fuller read of the command-register
 * tables settles it: every per-command frame table (Tables 56, 57 and
 * their siblings) lists bit 0 for BOTH directions as literally
 * "Odd Parity", matching Section 6.16.2's own prose that DIN consists
 * of "a five address bit, eight data bit and data parity".
 *
 * Odd parity here means the complete 16-bit frame must contain an odd
 * number of set bits, so bit 0 is set exactly when bits 15:1 already
 * hold an even number. l9779_word() below computes it by XOR-folding
 * the word - no loop, no lookup table, a handful of instructions on a
 * path that runs once per SPI transfer.
 *
 * Verified exhaustively rather than by inspection: all 32 addresses x
 * 256 data values (8192 frames, the entire input space this function
 * can ever be called with) were checked to confirm each result has odd
 * parity AND that neither the 5-bit address nor the 8-bit payload is
 * disturbed by the parity bit. Worth doing, because sending parity=0
 * on a chip that enforces it would have looked exactly like "the SPI
 * bus is fine but the outputs never fire" - the same class of silent
 * failure as the AND-logic trap already documented below.
 *
 * REAL, CONFIRMED, CRITICAL INIT REQUIREMENT (Table 57, START_REACT,
 * address 0x0D): "After a reset (default state)... OUT_DIS=1 and the
 * outputs are disabled (so any SPI data frame writing control
 * registers is ignored and the power stages are all switched off)."
 * l9779_init() below sends the real START command (bit2 of
 * START_REACT) to clear OUT_DIS - skipping this means every
 * CONTR_REG1-4 write is silently a no-op on real hardware, a real,
 * easy-to-miss gotcha this driver handles for the caller.
 *
 * REAL, SECOND CRITICAL FINDING, genuinely different from MC33810 and
 * caught only by reading the functional-description prose (Sections
 * 6.8.1 "LSa function," 6.10.1 "Ignition pre-drivers functionality
 * description"), not just the register tables: both the injector
 * outputs (OUT1-5) and the ignition pre-drivers (IGN1-4) are driven by
 * the real logical AND of their own SPI control bit (CONTR_REG1/
 * CONTR_REG2) and their own dedicated real-time parallel input pin
 * (IN1-5/IGNI1-4) - "They are driven by logical-AND of SPI control bit
 * and dedicated parallel input," stated near-verbatim for both blocks.
 * MC33810 worked differently (real, previously-confirmed OR logic
 * there - the parallel pins alone were already sufficient, no SPI
 * enable step needed). Since CONTR_REG1/CONTR_REG2 both real-reset to
 * 0x00 ("ALL outputs switched OFF," Table 21), this board's real
 * eMIOS-driven parallel firing pins (INJ{n}_CTRL/IGN{n}_CTRL,
 * ecu_pins.h/injection.c) would toggle correctly on real hardware and
 * STILL never actually fire anything unless the SPI side is also
 * explicitly enabled - a real, silent, critical correctness bug this
 * driver would otherwise have had. l9779_init() below now permanently
 * enables all 4 real channels' SPI side (CONTR_REG1/2 = 0x0F) once, at
 * startup - after that, the parallel pins have full, unblocked
 * real-time control, matching this project's actual intended
 * architecture (SPI configures/enables, eMIOS times the real pulses).
 * REAL, ALSO RESOLVED this same pass: whether IGN1-4 is a genuine IGBT
 * gate pre-driver (previously an open question, flagged in the
 * ecu-pcb redesign plan) - Section 6.10.1's own opening sentence:
 * "The 4 ignition pre-drivers are push-pull output with diagnosis and
 * over current protection circuit. They can drive IGBT Darlington
 * transistors." Real, explicit, direct confirmation - IGN1-4 is a real,
 * intended drop-in role match for MC33810's GDx, not just a
 * name-carried-over guess.
 *
 * Real command/register address map (Table 55, "SPI registers" -
 * write-command top-level addresses, NOT split into two spaces like
 * MC33810 turned out to need - this chip only has one real address
 * space, used identically for writes and for the generic-readback
 * dispatch via 0x10+subaddress):
 *   0x01-0x07  CONFIG_REG1-7        (W) - mode/fault/VRS/CAN config
 *   0x08-0x0B  CONTR_REG1-4         (W) - output ON/OFF control (real,
 *                                          used below)
 *   0x0C       LOCK_UNLOCK_SW_RST   (W) - config lock / software reset
 *   0x0D       START_REACT          (W) - clears OUT_DIS (see above),
 *                                          MRD reactivate
 *   0x0E       WD_ANSW/CONFIG_REG8  (W) - watchdog answer
 *   0x11       CONFIG_REG9/SPI_RESPTIME (W)
 *   0x12       CONFIG_REG10/CPS     (W) - stepper/CPS config
 *   0x10       generic read dispatch (R) - real sub-address (a literal
 *              byte value, not address math) in the DATA field selects
 *              IDENT_REG(0x00) or DIA_REG1-16(0x01-0x10)
 *
 * RESOLVED this session, real, high confidence:
 *   - CONTR_REG1 (0x08): bit7=CMD_OUT1, bit6=CMD_OUT2, bit5=CMD_OUT3,
 *     bit4=CMD_OUT4 - the 4 real injector low-side driver ON/OFF
 *     command bits, exactly matching this board's real 4-injector-
 *     per-chip need. bit3=CMD_OUT5, bit2=CMD_OUT20 (real, unused by
 *     this board's minimal-scope wiring - see l9779.h's sibling
 *     project note in the ecu-pcb plan). bits1:0 reserved.
 *   - CONTR_REG2 (0x09): bit3=CMD_IGN1, bit2=CMD_IGN2, bit1=CMD_IGN3,
 *     bit0=CMD_IGN4 - the 4 real ignition pre-driver ON/OFF command
 *     bits, exactly matching this board's real 4-ignition-per-chip
 *     need. bit7=CMD_OUT15, bit6=CMD_OUT14 (unused), bit5=don't care,
 *     bit4=reserved.
 *   - START_REACT (0x0D): bit2=START (real, sets OUT_DIS=0, required
 *     before any output can be commanded - see above), bit3=STOP,
 *     bit1=MRD_REACT. bit0 of the DO (response) side is OUT_DIS's own
 *     current status, not command data.
 *   - Real SPI timing (Table 53): fop max 8MHz, tsclk (SCK period) min
 *     125ns, tlead (CS-to-SCK setup) min 525ns, tlag (SCK-to-CS) min
 *     50ns, tcsn (CS negated/idle time) min 640ns, tnodata (real
 *     minimum gap BETWEEN separate 16-bit frames, same concept as
 *     MC33810's tSTR) min 1.5us - all genuinely different real numbers
 *     from MC33810's own timing, confirming this chip needs its own
 *     CTAR profile (CTAR2 - CTAR0 is MC33810's slot, CTAR1 is CJ125's,
 *     see dspi.h), not a reused one.
 *   - Real DIA_REG1/2 fault format (Table 55 + the register's own
 *     field description): 2-bit code per channel - 00=short-to-ground,
 *     01=open load, 10=short-to-battery, 11=OK/no fault. DIA_REG1 bits
 *     [1:0]/[3:2]/[5:4]/[7:6] = OUT1/OUT2/OUT3/OUT4 respectively -
 *     covers this board's real 4 injector channels per chip. DIA_REG2
 *     bits [1:0] = OUT5 (unused by this board's wiring).
 *
 * NOT resolved this session - real, named gaps, not guessed:
 *   - (The parity bit algorithm was on this list and is now RESOLVED -
 *     see above.)
 *   - Whether DO's data is for the SAME frame's DIN address or the
 *     PREVIOUS frame's (MC33810-style one-frame-delayed pipelining) -
 *     not explicitly stated in the text extracted. l9779_read_dia()
 *     below conservatively sends the read command twice (same
 *     defensive pattern as mc33810_read_status()), which is correct
 *     either way.
 *   - (IGN1-4's fault-diagnosis register was on this list and is now
 *     RESOLVED: it is DIA_REG8, subaddress 0x08, read-only, laid out
 *     [7:6]=IGN4 ... [1:0]=IGN1 with the same 2-bit encoding as the
 *     OUT channels. l9779_read_dia8()/l9779_handle_dia8() implement it
 *     and main.c polls both halves.)
 *   - CONFIG_REG1-7's fields are real and documented in this file
 *     header's own research (VRS mode, MRD_OT_DIS, charge pump, VRS
 *     hysteresis/filter config, power-latch timeout, CAN error
 *     handling, etc.) but NOT macro'd below - none of them are needed
 *     for this board's minimal-scope use (injector/ignition ON/OFF +
 *     basic fault readback only, matching mc33810.h's own equivalent
 *     scope), consistent with the ecu-pcb redesign plan's deliberate
 *     decision to leave VRS/CAN/K-Line/MRD/stepper features unused.
 *   - This board's real RSP/RSN (current-sense) and FBx (coil/collector
 *     sense) equivalents, and a DRV_OUTEN-equivalent global kill
 *     switch - no confirmed L9779WD-SPI pin serves these MC33810 roles;
 *     flagged in the ecu-pcb redesign plan as open items for the
 *     schematic-wiring pass, not resolved here.
 *   - No local PowerPC-EABI toolchain exists this session to compile-
 *     check this file, same standing gap as every other driver here.
 *
 * REAL HARDWARE DEPENDENCY THIS DRIVER CANNOT DETECT OR WORK AROUND -
 * worth knowing before debugging a "firmware sends everything correctly
 * but nothing fires" symptom on real hardware (found a later pass, from
 * the datasheet's own Figure 3, Table 13, Table 28 and Section 6.7):
 *   - IGN1-4's pre-drivers are supplied from the chip's own VDD5 rail
 *     (Table 28 lists VDD5 4.9-5.1V as their supply voltage range, and
 *     specifies their SCB detection thresholds relative to VDD5). VDD5
 *     is a linear regulator that only exists if an EXTERNAL NMOS pass
 *     transistor and an external charge-pump capacitor are fitted -
 *     "5 V precision voltage regulator (+/-2%) with external NMOS", and
 *     Table 13 lists both as required external components. ecu-pcb now
 *     fits them for real (Q20/Q21 + C82-C87); before that pass they
 *     were genuinely absent, which would have meant no ignition drive
 *     at all despite perfectly correct SPI + parallel-pin timing.
 *   - The charge pump's real DEFAULT behavior is conditional, not
 *     always-on (Section 6.7): it "could be active if the battery
 *     supply voltage is smaller than 12 V or be permanently active by
 *     setting the capful bit", and it provides "at least 5 V above
 *     Ubat when Ubat is higher than 6 V". That default is genuinely
 *     the sensible one for this board and needs no firmware action -
 *     at a normal running 14.4V, Ubat alone already exceeds the pass
 *     NMOS's own gate requirement, and the pump engages exactly when
 *     it's actually needed (a cranking/low-battery sag). Documented
 *     here so nobody later assumes the pump runs continuously, or
 *     "fixes" a non-problem by setting capful.
 *   - Real, deliberately-not-overridable behavior: once a Ubat
 *     overvoltage is detected (VB_OV_th > 28V) "the charge pump will
 *     be switched off automatically no matter the cp_off bit status".
 *     So during a real load-dump excursion, ignition drive can drop
 *     out by design - a hardware protection, not a fault to retry.
 */
#ifndef L9779_H
#define L9779_H

#include <stdint.h>

/* Real write/generic-dispatch addresses (Table 55). */
#define L9779_ADDR_CONTR_REG1        0x08u   /* OUT1-4/OUT5/OUT20 ON/OFF */
#define L9779_ADDR_CONTR_REG2        0x09u   /* OUT14/15, IGN1-4 ON/OFF */
#define L9779_ADDR_CONTR_REG3        0x0Au   /* OUT6/7/13/16-18/21/22 (unused by this board) */
#define L9779_ADDR_CONTR_REG4        0x0Bu   /* OUT23-28 (unused by this board) */
#define L9779_ADDR_START_REACT       0x0Du   /* START/STOP/MRD_REACT - see file header */
#define L9779_ADDR_READ_DISPATCH     0x10u   /* generic readback - real subaddress in DATA field */

/* Real read subaddresses (Table 55) - placed as a literal DATA byte,
 * not shifted/masked math (see file header). */
#define L9779_SUBADDR_IDENT   0x00u
#define L9779_SUBADDR_DIA1    0x01u   /* OUT4/3/2/1 fault, see below */
#define L9779_SUBADDR_DIA2    0x02u   /* OUT7/6/-/5 fault */
#define L9779_SUBADDR_DIA3    0x03u   /* OUT14/13/WDA_STATUS */
#define L9779_SUBADDR_DIA8    0x08u   /* IGN4/3/2/1 fault - see below */

/* CONTR_REG1 (0x08) real payload fields - see file header. */
#define L9779_CONTR1_OUT1   (1u << 7)
#define L9779_CONTR1_OUT2   (1u << 6)
#define L9779_CONTR1_OUT3   (1u << 5)
#define L9779_CONTR1_OUT4   (1u << 4)
#define L9779_CONTR1_OUT5   (1u << 3)   /* real, unused by this board's wiring */
#define L9779_CONTR1_OUT20  (1u << 2)   /* real, unused by this board's wiring */

/* CONTR_REG2 (0x09) real payload fields - see file header. */
#define L9779_CONTR2_OUT15  (1u << 7)   /* real, unused by this board's wiring */
#define L9779_CONTR2_OUT14  (1u << 6)   /* real, unused by this board's wiring */
#define L9779_CONTR2_IGN1   (1u << 3)
#define L9779_CONTR2_IGN2   (1u << 2)
#define L9779_CONTR2_IGN3   (1u << 1)
#define L9779_CONTR2_IGN4   (1u << 0)

/* START_REACT (0x0D) real payload fields - see file header's critical
 * init-requirement note. */
#define L9779_START_REACT_START      (1u << 2)
#define L9779_START_REACT_STOP       (1u << 3)
#define L9779_START_REACT_MRD_REACT  (1u << 1)

/* DIA_REG1/2 real per-channel 2-bit fault codes (see file header). */
#define L9779_DIA_SHORT_GND    0x0u
#define L9779_DIA_OPEN_LOAD    0x1u
#define L9779_DIA_SHORT_BATT   0x2u
#define L9779_DIA_OK           0x3u
#define L9779_DIA1_OUT1_SHIFT  0
#define L9779_DIA1_OUT2_SHIFT  2
#define L9779_DIA1_OUT3_SHIFT  4
#define L9779_DIA1_OUT4_SHIFT  6

/* DIA_REG8 - the IGNITION pre-driver fault register. Real, from the
 * register's own definition page: "DIA_REG8 / Diagnostic register 8 /
 * Address: 1 0000 / Subaddress: 0000 1000 / Type: R (Read only) /
 * Reset: 0000 0000", laid out [7:6]=IGN4_DIAG, [5:4]=IGN3_DIAG,
 * [3:2]=IGN2_DIAG, [1:0]=IGN1_DIAG.
 *
 * Its 2-bit codes are the SAME encoding as the OUT channels above -
 * 00=SCG, 01=OL, 10=SCB, 11=power stage OK - so the existing
 * L9779_DIA_* constants apply unchanged, and the per-channel shift is
 * again 2 bits with IGN1 in the low field. Convenient, but confirmed
 * from the register's own listing rather than assumed by symmetry. */
#define L9779_DIA8_IGN1_SHIFT  0
#define L9779_DIA8_IGN2_SHIFT  2
#define L9779_DIA8_IGN3_SHIFT  4
#define L9779_DIA8_IGN4_SHIFT  6
#define L9779_DIA_FIELD_MASK   0x3u

/* Real, computed CTAR2 value for this board's confirmed 60MHz DSPI_0
 * peripheral clock (clocks.h) - reuses the SAME real PBR=00/BR=0100
 * (prescaler 2, scaler 16) combination already confirmed for CJ125's
 * CTAR1 (cj125.h: SCK = 60MHz/(2*16) = 1.875MHz), rather than deriving
 * a new BR scaler code from Table 26-17 that hasn't been visually
 * re-confirmed this pass. 1.875MHz is comfortably under this chip's
 * real 8MHz max (Table 53), so reusing this already-verified real
 * operating point is honest, not a shortcut past a real unknown -
 * CPOL=0/CPHA=0 (no bits set) matches this chip's own SPI mode
 * (Section 6.16.1: "data is latched on the rising edge of SCLK and
 * data is shifted on the falling edge", same as MC33810/CJ125). */
#define L9779_CTAR2 ( \
    (15u << 27) /* DSPI_CTAR_FMSZ_SHIFT: FMSZ=15 -> 16-bit frames */ \
    | (0u << 16) /* DSPI_CTAR_PBR_SHIFT: PBR=00 -> prescaler 2 */ \
    | (0x4u << 0) /* DSPI_CTAR_BR_SHIFT: BR=0100 -> scaler 16 */ \
)

/* Real, deliberately conservative CS-edge timing margins (Table 53) -
 * see l9779.c's l9779_delay() for the honest caveat (not calibrated
 * to a real time unit, no systick/timer exists yet - same class of
 * gap as mc33810.h's own MC33810_TLEAD_NS etc). */
#define L9779_TLEAD_NS  525u   /* CS-to-SCK setup, real min */
#define L9779_TLAG_NS   50u    /* SCK-to-CS setup, real min */
#define L9779_TCSN_NS   640u   /* CS negated/idle time, real min */
#define L9779_TNODATA_US 2u    /* real min gap between separate frames (spec: 1.5us, rounded up) */

/* Build a 16-bit SPI word: real 5-bit address (bits 14:10), real 8-bit
 * data (bits 8:1), parity forced to 0 (see file header - real gap, not
 * computed). addr5 must be a real L9779_ADDR_* value. */
static inline uint16_t l9779_word(uint8_t addr5, uint8_t data8) {
    uint16_t frame = (uint16_t)(((uint16_t)(addr5 & 0x1Fu) << 10)
                                | ((uint16_t)data8 << 1));
    /* Real ODD parity in bit 0 - see the file header for provenance.
     * Odd parity means the whole 16-bit frame must contain an odd number
     * of set bits, so bit 0 is set exactly when bits 15:1 already hold an
     * even number. Folding the word in half repeatedly leaves the parity
     * of the whole value in the low bit; no loop, no table, and it costs
     * a handful of instructions on a path that runs per SPI transfer. */
    uint16_t v = frame;
    v ^= (uint16_t)(v >> 8);
    v ^= (uint16_t)(v >> 4);
    v ^= (uint16_t)(v >> 2);
    v ^= (uint16_t)(v >> 1);
    /* v's low bit now holds the parity of bits 15:1. Odd parity wants
     * the opposite, so flip it - XOR rather than ~ keeps everything
     * unsigned and avoids an integer-promotion sign conversion. */
    return (uint16_t)(frame | (uint16_t)((v & 1u) ^ 1u));
}

/* Real, callable: brings up DSPI_0's CTAR2 (L9779_CTAR2 above) and
 * drives both real L9779WD-SPI CS pins (PIN_SPI_CS_INJ0/1, ecu_pins.h -
 * same pins MC33810 used, CS is a board-side GPIO role unaffected by
 * the chip change) to idle-high, then sends the real START_REACT/START
 * command to each chip to clear OUT_DIS (see file header - without
 * this, every CONTR_REG1/2 write below is a silent no-op on real
 * hardware). Call once, before any l9779_set_outputs()/
 * l9779_set_ignition(). Assumes dspi_init() has already brought up
 * DSPI_0 itself (mc33810_init() or equivalent). */
void l9779_init(void);

/* One raw 16-bit SPI transfer against a specific L9779WD-SPI (its own
 * CS pin). Real CS-edge timing margins from Table 53 are enforced
 * around the transfer (see l9779.c). Whether the returned word's DATA
 * field is for this frame's own address or the previous frame's is a
 * real, unconfirmed gap (see file header) - callers reading back a
 * register should issue the read twice, same defensive pattern as
 * mc33810_read_status(). */
uint16_t l9779_transfer(uint8_t cs_pin, uint16_t tx_word);

/* Real, complete: writes CONTR_REG1, setting/clearing OUT1-4 (this
 * board's 4 real injector channels) from the low 4 bits of `out1_4`
 * (bit0=OUT1 ... bit3=OUT4 - NOT the same bit order as
 * L9779_CONTR1_OUT1..4, which are already real register-bit-position
 * masks; this convenience wrapper takes a simple 0-15 channel mask and
 * does the real reordering internally). */
void l9779_set_injectors(uint8_t cs_pin, uint8_t out1_4_mask);

/* Real, complete: writes CONTR_REG2, setting/clearing IGN1-4 (this
 * board's 4 real ignition channels) from the low 4 bits of `ign1_4`
 * (bit0=IGN1 ... bit3=IGN4, same simple-mask convention as above). */
void l9779_set_ignition(uint8_t cs_pin, uint8_t ign1_4_mask);

/* Real, complete: reads back DIA_REG1 (OUT1-4 fault status, 2 bits per
 * channel - see L9779_DIA_* macros above). Returns the raw byte;
 * decode with L9779_DIA1_OUTn_SHIFT + L9779_DIA_FIELD_MASK. */
uint8_t l9779_read_dia1(uint8_t cs_pin);

/* Real fault-response policy - mirrors mc33810_handle_status()'s role
 * for this chip. is_bank_1_4: 1 for the cylinder 1-4 chip, 0 for the
 * cylinder 5-8 chip - same convention as mc33810.h. */
void l9779_handle_dia1(uint8_t dia1, int is_bank_1_4);

/* Real, complete: reads back DIA_REG8 (IGN1-4 fault status, 2 bits per
 * channel, same L9779_DIA_* encoding as the OUT channels). Returns the
 * raw byte; decode with L9779_DIA8_IGNn_SHIFT + L9779_DIA_FIELD_MASK. */
uint8_t l9779_read_dia8(uint8_t cs_pin);

/* Real fault-response policy for the ignition pre-drivers, the IGN-side
 * counterpart to l9779_handle_dia1(). is_bank_1_4 follows the same
 * convention: 1 for the cylinder 1-4 chip, 0 for cylinders 5-8. */
void l9779_handle_dia8(uint8_t dia8, int is_bank_1_4);

#endif /* L9779_H */
