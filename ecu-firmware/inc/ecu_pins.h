/*
 * ecu_pins.h - Real MCU pin / eMIOS channel assignments for the standalone
 * ECU (NXP MPC5606B, 144-LQFP).
 *
 * This is NOT derived independently - it is a direct transcription of the
 * MCU_USED / MCU_EMIOS dicts in ../ecu-pcb/build_schematic.py, the same
 * source of truth the PCB itself was generated from. If a net's pin
 * changes on the board, it changes here too - keep these two files in
 * sync by hand until/unless this gets generated automatically from the
 * schematic like the board is.
 *
 * Every pin number below was visually confirmed against the real NXP
 * MPC5606BK Data Sheet Rev. 5 (Table 2, "Functional port pins") during
 * hardware design - not assumed, not carried over from a rough text
 * extraction pass (that discipline is why some of these look oddly
 * specific instead of "the next free pin").
 */
#ifndef ECU_PINS_H
#define ECU_PINS_H

/* eMIOS (module, channel) pairs for the real-time channels below - the
 * other half of the same MCU_EMIOS dict the pin numbers came from
 * (build_schematic.py: 104:"INJ1" etc., cross-referenced with
 * MCU_EMIOS_CHANNEL for the real channel name, e.g. 104:"E0UC7"). Module
 * 0 = EMIOS0_BASE, module 1 = EMIOS1_BASE (see emios.h). */
#define EMIOS_MOD_0  0
#define EMIOS_MOD_1  1

/* ---- Injector low-side drive: real-time parallel firing inputs --------
 * eMIOS channel per injector. These are the MCU pins that drive the
 * injector driver chips' own parallel real-time inputs directly
 * (NOT SPI) - this is the actual firing signal, timed by eMIOS unified
 * channel PWM/OPWFM mode, not software-bit-banged. Real MC33810 ->
 * L9779WD-SPI replacement (see l9779.h - MC33810 hit Last Time Buy,
 * 2027-04-30): these MCU-side pin/role assignments are UNCHANGED by
 * that swap - only the far-end chip's own pin number differs (MC33810's
 * DIN0-3 vs L9779WD-SPI's IN1-4), which lives in ecu-pcb's schematic,
 * not here.
 * chip 0 (U5) = cylinders 1-4, chip 1 (U6) = cylinders 5-8. */
#define PIN_INJ1_CTRL   104u   /* E0UC7  */
#define PIN_INJ2_CTRL   107u   /* E0UC10 */
#define PIN_INJ3_CTRL   108u   /* E0UC11 */
#define PIN_INJ4_CTRL    31u   /* E0UC30 */
#define PIN_INJ5_CTRL    32u   /* E0UC31 */
#define PIN_INJ6_CTRL    83u   /* E0UC4  */
#define PIN_INJ7_CTRL    85u   /* E0UC5  */
#define PIN_INJ8_CTRL    87u   /* E0UC6  */

/* Same 8 channels as (module, channel-number) pairs for emios.h calls -
 * all module 0, matching the E0UC prefix above. */
#define EMIOS_INJ1_MOD  EMIOS_MOD_0
#define EMIOS_INJ1_CH   7u
#define EMIOS_INJ2_MOD  EMIOS_MOD_0
#define EMIOS_INJ2_CH   10u
#define EMIOS_INJ3_MOD  EMIOS_MOD_0
#define EMIOS_INJ3_CH   11u
#define EMIOS_INJ4_MOD  EMIOS_MOD_0
#define EMIOS_INJ4_CH   30u
#define EMIOS_INJ5_MOD  EMIOS_MOD_0
#define EMIOS_INJ5_CH   31u
#define EMIOS_INJ6_MOD  EMIOS_MOD_0
#define EMIOS_INJ6_CH   4u
#define EMIOS_INJ7_MOD  EMIOS_MOD_0
#define EMIOS_INJ7_CH   5u
#define EMIOS_INJ8_MOD  EMIOS_MOD_0
#define EMIOS_INJ8_CH   6u

/* ---- Ignition drive: real-time parallel firing inputs ------------------
 * Same real-time/parallel story as injectors, and the same "MCU-side
 * pin/role unaffected by the MC33810 -> L9779WD-SPI swap" note applies
 * (see above) - the driver chip gate-predrives the external IGBT
 * (Q3-Q10 on the board) from here. */
#define PIN_IGN1_CTRL   143u   /* E0UC3  */
#define PIN_IGN2_CTRL   141u   /* E0UC12 */
#define PIN_IGN3_CTRL   142u   /* E0UC13 */
#define PIN_IGN4_CTRL     3u   /* E0UC14 */
#define PIN_IGN5_CTRL     4u   /* E0UC15 */
#define PIN_IGN6_CTRL    36u   /* E1UC28 */
#define PIN_IGN7_CTRL    37u   /* E1UC29 */
#define PIN_IGN8_CTRL   131u   /* E1UC31 */

#define EMIOS_IGN1_MOD  EMIOS_MOD_0
#define EMIOS_IGN1_CH   3u
#define EMIOS_IGN2_MOD  EMIOS_MOD_0
#define EMIOS_IGN2_CH   12u
#define EMIOS_IGN3_MOD  EMIOS_MOD_0
#define EMIOS_IGN3_CH   13u
#define EMIOS_IGN4_MOD  EMIOS_MOD_0
#define EMIOS_IGN4_CH   14u
#define EMIOS_IGN5_MOD  EMIOS_MOD_0
#define EMIOS_IGN5_CH   15u
#define EMIOS_IGN6_MOD  EMIOS_MOD_1
#define EMIOS_IGN6_CH   28u
#define EMIOS_IGN7_MOD  EMIOS_MOD_1
#define EMIOS_IGN7_CH   29u
#define EMIOS_IGN8_MOD  EMIOS_MOD_1
#define EMIOS_IGN8_CH   31u

/* ---- Crank / cam position (VR sensors via MAX9924, open-drain COUT) --- */
#define PIN_CRANK_CAPTURE   42u   /* E0UC0 - real independent crank input capture */
#define PIN_CAM1_CAPTURE    11u   /* E0UC1 - intake cam */
#define PIN_CAM2_CAPTURE   128u   /* E0UC18 - exhaust cam, DOHC independent phasing */

#define EMIOS_CRANK_MOD  EMIOS_MOD_0
#define EMIOS_CRANK_CH   0u
#define EMIOS_CAM1_MOD   EMIOS_MOD_0
#define EMIOS_CAM1_CH    1u
#define EMIOS_CAM2_MOD   EMIOS_MOD_0
#define EMIOS_CAM2_CH    18u

/* ---- Cam phaser / idle / tach / boost PWM outputs ---------------------- */
#define PIN_VVT1_PWM       129u   /* E0UC19 */
#define PIN_VVT2_PWM       132u   /* E0UC20 */
#define PIN_IDLE_PWM       133u   /* E0UC21 */
#define PIN_TACH_OUT        84u   /* E0UC25 */
#define PIN_BOOST_PWM      109u   /* E1UC19 */

/* ---- Electronic throttle control (H-bridge real-time PWM) -------------- */
#define PIN_ETC_IN1        139u   /* E0UC22 - MC33926 IN1 */
#define PIN_ETC_IN2        140u   /* E0UC23 - MC33926 IN2 */
#define PIN_ETC_D1          25u   /* GPIO, active-HIGH disable  - ASYMMETRIC, see note below */
#define PIN_ETC_D2          26u   /* GPIO, active-LOW disable   - ASYMMETRIC, see note below */
#define PIN_ETC_EN          29u   /* GPIO, H-bridge enable */
#define PIN_ETC_SF_N        30u   /* GPIO input, H-bridge fault flag (active-low) */
/* MC33926 D1/D2 are NOT symmetric: D1 is active-HIGH, D2 is active-LOW.
 * This is a real, easy-to-get-backwards datasheet fact, not a typo -
 * verify against the MC33926 datasheet before writing the disable logic. */

/* ---- Flex-fuel frequency/duty capture ----------------------------------- */
#define PIN_FLEXFUEL_CAPTURE   103u   /* E1UC20 */

/* ---- Analog inputs (ADC0 channels) -------------------------------------- */
#define PIN_ADC_MAP      72u
#define PIN_ADC_TPS      75u   /* legacy single-channel TPS - see README */
#define PIN_ADC_IAT      76u
#define PIN_ADC_CLT      77u
#define PIN_ADC_KNOCK1   53u
#define PIN_ADC_KNOCK2   66u
#define PIN_ADC_VBATT    63u
#define PIN_ADC_OILP     64u
#define PIN_ADC_FUELP    65u
#define PIN_ADC_APP1     68u
#define PIN_ADC_APP2     69u
#define PIN_ADC_TPS1     70u   /* redundant ETC throttle-body position 1 of 2 */
#define PIN_ADC_TPS2     71u   /* redundant ETC throttle-body position 2 of 2 */
#define PIN_ADC_ETC_IFB  79u   /* MC33926 current-feedback sense */
/* NOTE: package pin 78 (PD[9]) used to be PIN_ADC_EGT, an analog input
 * feeding an AD8495 thermocouple amplifier's divided-down output. That
 * part had no AEC-Q100 qualification and no automotive-qualified
 * dedicated thermocouple amplifier IC exists to swap it for, so the EGT
 * channel was redesigned around a real AEC-Q100 Grade 1 ADC
 * (ADS1118-Q1) reading the thermocouple millivolts directly over SPI,
 * with cold-junction compensation and NIST ITS-90 linearisation moved
 * into firmware (ads1118.h). That freed this exact pin for the new
 * device's chip select - see PIN_SPI_CS_EGT below. No new MCU pin was
 * needed, because siul2.c's own Table 4-1 check had already established
 * that the Port D analog pins are ordinary multiplexed pads usable as
 * GPIO, unlike the dedicated analog-only MAP/TPS/IAT/CLT pads. */

/* ---- SPI bus (shared: 2x injector/ignition driver, 2x CJ125) ----------
 * Real MC33810 -> L9779WD-SPI replacement (l9779.h - MC33810 hit Last
 * Time Buy, 2027-04-30): CS is a board-side GPIO role, unaffected by
 * which chip is actually listening - these constants and their real
 * pin numbers are unchanged. */
#define PIN_SPI_SCK     40u
#define PIN_SPI_SIN     45u   /* MCU receive  - ties to slaves' shared SO */
#define PIN_SPI_SOUT    44u
#define PIN_SPI_CS_INJ0  9u   /* injector/ignition driver #1 (U5, cyl 1-4) chip select */
#define PIN_SPI_CS_INJ1 10u   /* injector/ignition driver #2 (U6, cyl 5-8) chip select */
#define PIN_SPI_CS_O2A  13u   /* CJ125 #1 (U9, bank A) chip select */
#define PIN_SPI_CS_O2B  86u   /* CJ125 #2 (U18, bank B) chip select */
#define PIN_SPI_CS_EGT  78u   /* ADS1118-Q1 EGT thermocouple ADC (U19) chip select.
                               * Real, deliberate reuse of the pin that used to be
                               * PIN_ADC_EGT - see the note above its old definition. */
/* Real, open gap surfaced by the MC33810 -> L9779WD-SPI replacement
 * (see l9779.h/ecu-pcb's redesign plan): this MCU pin drove MC33810's
 * real shared OUTEN kill-switch pin (pin 9). No confirmed
 * L9779WD-SPI equivalent was found in the pins read this session -
 * this constant is kept as a real, physical MCU pin that WAS wired
 * for this role, not deleted, but nothing in this driver uses it yet.
 * Real per-chip equivalent may be START_REACT's STOP bit (see l9779.h)
 * instead of a dedicated hardware pin - not confirmed, a task for the
 * ecu-pcb schematic-wiring pass. */
#define PIN_DRV_OUTEN    6u

/* ---- Other GPIO -------------------------------------------------------- */
#define PIN_RELAY_CTRL      5u   /* main power relay (K1) */
#define PIN_HTR_CTRL       12u   /* O2 bank-A heater PWM */
#define PIN_HTR2_CTRL      88u   /* O2 bank-B heater PWM */
#define PIN_FPUMP_CTRL     67u   /* fuel pump relay */
#define PIN_CAN0_EN        14u
#define PIN_CAN0_STB_N     15u
#define PIN_CAN1_EN        16u
#define PIN_CAN1_STB_N     17u

/* ---- CAN bus (2x independent real FlexCAN pairs) ------------------------
 * Real, transcribed from ../ecu-pcb/build_schematic.py's own MCU_USED
 * dict (comment there: "Step 6: two independent real FlexCAN pairs...").
 * These were missing from this file until the firmware's own CAN driver
 * pass needed them - same source of truth as every other pin here, just
 * not carried over when this file was first written. CAN0 uses the
 * MCU's FlexCAN_1 module, CAN1 uses FlexCAN_4 (FlexCAN_0 was ruled out
 * on the PCB - it shares pins 31/32 with eMIOS INJ4/INJ5; CAN2/CAN3 were
 * checked and dropped for other conflicts - see build_schematic.py's own
 * comment for the full reasoning). */
#define PIN_CAN0_TX        28u   /* PC[10], FlexCAN_1 */
#define PIN_CAN0_RX        27u   /* PC[11], FlexCAN_1 */
#define PIN_CAN1_TX       117u   /* PC[2],  FlexCAN_4 */
#define PIN_CAN1_RX       116u   /* PC[3],  FlexCAN_4 */

#endif /* ECU_PINS_H */
