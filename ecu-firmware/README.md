# ECU firmware — starting skeleton

Companion firmware project for [`../ecu-pcb`](../ecu-pcb) (the standalone
automotive ECU board). This is **not a working ECU** — it's a real,
compilable-shaped skeleton laying out the actual architecture, with the
hardware-verified facts (pin numbers, SPI protocol, register maps) kept
separate from the genuinely unimplemented parts, so the boundary between
"you can trust this" and "this still needs the reference manual open" is
explicit rather than blurred.

## What's real vs. what's a TODO

**Real, verified this session, safe to build on:**
- Every pin/channel number in [`inc/ecu_pins.h`](inc/ecu_pins.h) — a
  direct transcription of `../ecu-pcb/build_schematic.py`'s own
  `MCU_USED`/`MCU_EMIOS` dicts, the same source of truth the PCB was
  generated from.
- The MC33810 SPI frame format and fault-register map in
  [`inc/mc33810.h`](inc/mc33810.h) — read directly from the real
  datasheet (MC33810 Rev. 11.0, 8/2014) this session, not remembered.
- The confirmation that injector/ignition **firing** goes through the
  dedicated DIN0-3/GIN0-3 parallel pins, not SPI — SPI is
  configuration and fault-readback only. This matches how the board
  itself is wired and shapes the whole real-time architecture below.
- **The eMIOS unified-channel driver** ([`inc/emios.h`](inc/emios.h),
  [`src/emios.c`](src/emios.c)) — a real, working register layer, not a
  stub. Downloaded the actual NXP MPC5606BK Reference Manual (964
  pages) this session and confirmed, by rendering the real register
  diagrams to images and reading them directly (not the raw PDF text
  extraction, which shuffles this document's tables badly enough to be
  actively misleading): both eMIOS base addresses, the per-channel
  register offset formula, the complete EMIOSC[n] control-register bit
  layout, and OPWFMB mode's real encoding — the double-buffered PWM
  mode that's the actual reason `emios_set_pulse_width()` can safely
  re-arm a channel's pulse width mid-cycle without glitching whatever
  pulse is currently firing. `injection.c` now calls this for real.
- **Pin muxing is fully real now** ([`inc/siul2.h`](inc/siul2.h),
  [`src/siul2.c`](src/siul2.c)) — `pinmux_init()` configures every one
  of the 62 real pins in `ecu_pins.h`, not a mechanism waiting on data.
  Built by matching each pin's real 144-LQFP package number against
  Reference Manual Table 4-1 ("Functional port pins", pages 55-74),
  extracted via positioned-text coordinates (the flat text extraction
  shuffles this table's columns) and cross-checked against each pin's
  already-known real signal name from `ecu_pins.h` — all 62 matched
  exactly once, none guessed. Fell out a real, previously-unconfirmed
  formula: PCR index = port_offset×16 + pin-within-port (A=0…G=6),
  confirmed across every port this board's pins actually land on.
- **Clock/mode bring-up is fully real now** ([`inc/clocks.h`](inc/clocks.h),
  [`src/clocks.c`](src/clocks.c)) — `clocks_init()` is a complete,
  callable RESET→SAFE→DRUN→RUN0 sequence, not a stub. Real pieces:
  FMPLL's base address, IDF/ODF/NDIV divider fields, and S_LOCK;
  `ME_MCTL`'s two-write mode-transition mechanism (key `0x5AF0` then
  inverted key `0xA50F`); `ME_RESET_MC`/`ME_SAFE_MC`'s real SYSCLK
  field; and `ME_GS`'s status bits for polling a transition to
  completion. Two real findings worth recording:
  - Caught a real mistake before it shipped: FMPLL CR's upper 16 bits
    were first transcribed from text extraction alone and put S_LOCK
    at the wrong bit. Re-rendering that half as an image and reading
    it directly caught it.
  - `ME_DRUN_MC` and `ME_RUN0_MC`…`ME_RUN3_MC` genuinely have no
    SYSCLK field of their own (confirmed visually, not a page-break
    artifact) — resolved by reading section 8.4.3.12 "System clock
    switching": a mode with no SYSCLK field has nothing to switch to,
    so the source configured in SAFE mode simply carries forward.
    `clocks_init()` sets `ME_SAFE_MC.SYSCLK=PLL` once and relies on
    that.
  - `ME_GS`'s field-name row for bits 0:15 is genuinely missing from
    the source PDF (confirmed via raw positioned-text extraction, not
    a rendering issue) — recovered by cross-referencing the real field
    order (Table 8-4), each field's real bit-width, and the real reset
    value, which only fit together one way.
  - **`clocks_init()` is now actually called for real** from `main.c`'s
    `hardware_init()` (it wasn't, for the rest of this session — this
    was the single most-referenced open gap in the whole project, since
    it silently blocked a real DSPI baud rate, ADC power-up delay, and
    FlexCAN bit timing too). What unblocked it: `../ecu-pcb`'s own
    schematic already documents a real, deliberate 8 MHz crystal
    decision from the PCB design phase (not invented for this pass);
    combined with the FMPLL chapter's own real reference/VCO range
    constraints (4-16 MHz / 256-512 MHz, Section 6.7.2) and this
    manual's own confirmed 64 MHz max core frequency (its introduction,
    not assumed), real divider values fell out:
    `ECU_FMPLL_IDF/ODF/NDIV = 0/2/60` (`clocks.h`), giving a 60 MHz core
    clock — deliberately short of the 64 MHz max, not pushed to the
    edge, given the FMPLL chapter's own note that there's no hardware
    check against an out-of-range frequency.
  - **Follow-up in the same pass: MC_CGM's peripheral clock dividers,
    also real now** (`clocks.h`'s `CGM_SC_DC0` — Chapter 7, Figure 7-5,
    visually confirmed). The real 60 MHz core clock feeds three
    independently-divided "Peripheral Set" clocks; Table 6-1 confirms
    DSPI/FlexCAN are both Peripheral Set 2 and ADC/eMIOS are both
    Peripheral Set 3, and `CGM_SC_DC0`'s own real reset values (all
    three divider-enable bits = 1, all three divide fields = 0) mean
    both sets run **undivided at the real 60 MHz core clock** by
    default — this driver never writes that register, so the reset
    default is what's live. **Still open, honestly, not assumed:**
    each individual peripheral also has its own clock-gating selection
    (`ME_PCTL[n]`/`ME_RUN_PCn`, Chapter 8) that could in principle
    leave it un-clocked in RUN0 regardless of its Peripheral Set's own
    divider — this session located the real register addresses (cross-
    checked between two independent tables) but did not find/confirm
    `ME_RUN_PC`'s own reset bit values, so whether DSPI/FlexCAN/ADC/
    eMIOS are gated ON by default is plausible (matches every other
    "just works out of reset" MC_ME default seen this session) but not
    proven. This only affects whether a peripheral is clocked *at all*,
    not *at what rate* — the real 60 MHz figure above is unconditional
    once a peripheral is clocked.
    **Update (later pass): confirmed this really can't be proven from
    this source.** Chapter 8 (pages 144–177) was checked end to end —
    its own register-description subsections stop at
    `ME_HALT_MC`/`ME_STOP_MC`/`ME_STANDBY_MC`; there is no bit-diagram
    section for `ME_RUN_PCn`/`ME_PCTL[n]` anywhere in it, even though
    Table 8-3 lists their addresses and Chapter 6 explicitly promises
    "See the ME_PCTLn section in this reference manual for details" —
    a real, internal forward-reference in this manual to a section that
    was never actually written into this document. Genuinely absent,
    not a search miss; see `clocks.h`'s file header for the full note.
- **DSPI (SPI bus) is fully real now** ([`inc/dspi.h`](inc/dspi.h),
  [`src/dspi.c`](src/dspi.c)) — `dspi_init()`/`dspi_transfer()` are a
  complete, real, blocking master-mode driver, not stubs, and
  `mc33810_transfer()` (`mc33810.c`) calls it for real — no longer a
  stub either. Real pieces, all visually confirmed against Chapter 26's
  own register diagrams: MCR's MSTR/DCONF/MDIS/HALT/CLR_TXF/CLR_RXF,
  CTARn's CPOL/CPHA/FMSZ/baud fields, SR's TCF/TFFF/RFDF/w1c behavior,
  PUSHR/POPR's real 16-bit frame format. Three real findings worth
  recording:
  - This board's chip selects are plain GPIO, not DSPI's own hardware
    PCS0-5 lines — confirmed directly from Table 4-1 (none of this
    board's real CS pins carry a CS0_x-family alternate function).
    `dspi_transfer()` is therefore a bare frame primitive; CS toggling
    lives in `mc33810_transfer()`.
  - `SPI_SIN`'s (PA[12]) alternate function — flagged last pass as a
    corrupted, unconfirmed table cell — turned out not to be corrupted
    at all: `SIN_0` isn't AF-selected on this pin, confirmed by a
    document-wide search showing the string "SIN_0" appears exactly
    once in the entire 964-page manual (this same table row), unlike
    `SIN_3`/`SIN_4` which each have a real PSMI (Peripheral Selection
    Multiplex Input) register offering a genuine choice between two
    candidate pins. The existing GPIO-input configuration for this pin
    was already correct.
  - Found and fixed a real, previously-dormant bug: `ecu_pins.h`'s
    `PIN_SPI_CS_*` constants are 144-LQFP package pin numbers (matching
    every other constant in that file), but driving a pin as GPIO
    needs a PCR index — a different numbering, the same distinction
    `pcr_configure()` already carried a warning about. This was inert
    while `mc33810_transfer()` was a stub; wiring it up for real would
    have silently toggled the wrong pin. Fixed by adding
    `siul2_pcr_for_pin()` (`siul2.c`/`siul2.h`), a real runtime lookup
    against the same `PINMUX_TABLE` `pinmux_init()` already uses, so
    every caller keeps using the package-pin numbers `ecu_pins.h`
    already establishes as this project's convention.
  - The MC33810's real SPI clock mode (CPOL=0, CPHA=0) came directly
    from its own datasheet's Serial Clock Input description ("SI data
    is latched...on the rising edge of SCLK...SO...shifts out on the
    falling edge") — not assumed from a generic SPI default.
  - **`MC33810_CTAR0`'s baud rate is now real too** (a later pass, once
    the system clock gap closed) — 937.5 kHz, computed from the real
    60 MHz DSPI_0 peripheral clock and PBR's own real inline prescaler
    table (Table 26-5: values 2/3/5/7, not previously looked up).
  - **The "VDD=3.3V not confirmed" flag is resolved** (a later pass) —
    not by finding a separate 3.3V row, but by finding there isn't one:
    the datasheet's own Dynamic Electrical Characteristics table (Table
    5) states its values apply across "3.0V ≤ VDD ≤ 5.5V", a single
    spec covering this board's real 3.3V. The "1.0 MHz, 5.0V" figure
    previously read as a rating turned out to be the ATE test
    condition for guaranteed-by-design parameters (its own footnote
    says so), not a reduced real limit.
  - **Real `tLEAD`/`tLAG` values found, plus a previously-unknown real
    `tSTR` constraint** (same later pass) — Table 5's own "SPI DIGITAL
    INTERFACE TIMING" section: tLEAD ≥ 100ns, tLAG ≥ 50ns, and a real
    minimum 1.0µs gap required *between* separate transfers (tSTR,
    "Sequential Transfer Rate") that this project didn't know existed
    until this pass. `mc33810_transfer()` now runs a real, conservative
    busy-wait around every CS edge and between transfers, computed
    against this board's confirmed 60MHz core clock — closing what had
    been a standing TODO since the driver was first written.
  - **Most of Table 22's bit-level register layout is real now too**,
    not just the fault registers (a later pass) — found by rendering
    the register-address column header at the same crop/scale as each
    data row and tracing bit boundaries straight down against it (the
    same table had previously been dismissed as too garbled to trust).
    Mode Command and LSD Fault Command registers are fully real,
    high-confidence (`MC33810_MODE_*`/`MC33810_LSD_*`, `mc33810.h`).
    One real self-correction worth recording: a first pixel-count pass
    misplaced the LSD register's 3-bit fault-operation field at bits
    11:8 with a spare reserved bit at 7; re-deriving field widths from
    the total real bit budget (12 bits = 3+1+4+4, not 4+1+... which
    doesn't divide evenly) caught it and placed it correctly at bits
    11:9 with bit 8 reserved — the same "don't trust one read" discipline
    this project has used throughout catching itself again. Spark
    Command and DAC Command registers got real field *names* and
    *order* confirmed, but not exact bit-width splits for their
    multi-bit fields — left as real, generic 12-bit payload setters
    rather than fabricated sub-field macros.
  - **Update (later pass): a real address-space bug found and fixed,
    plus every remaining named MC33810 register gap closed.** Re-opened
    Table 21 ("SPI Command Message Set") — the datasheet's *write*
    command table, distinct from Table 22's *readback* table — and
    found it uses a completely different 4-bit top-nibble address for
    the same-named registers (e.g. Spark Command's real write address
    is `0x4`, not the `0x8` this file had previously taken from Table
    22). This wasn't a guess: Table 21's own "hex" column is internally
    self-consistent with its own 4 binary control-address bits on every
    row, and independently corroborated by repeated, non-tabular prose
    elsewhere in the datasheet ("...SPI Command Message Spark command
    (Command 0100, hex 4)..."). Table 22's addresses turned out to be a
    *separate*, real address space — the "Internal Register Address"
    used only inside the generic Read Registers Command's own payload
    to select what gets echoed back on SO, not a write address at all;
    its own header text says as much ("Next SO Response to HEX1 to HEX
    A Commands..."). The two spaces differ by a consistent, real `+4`
    offset for the 10 registers appearing in both. Every
    `MC33810_ADDR_*` constant except `ALL_STATUS` (which happens to
    coincide, `0x0`, in both spaces) was wrong for writing — renamed
    and split into `MC33810_WCMD_*` (real write address) and
    `MC33810_RADDR_*` (real internal register address) to make the two
    spaces impossible to conflate again. Along the way, also found and
    fixed a second, related bug: `mc33810_read_status()`'s All Status
    read was sending an all-zero `0x000` payload where Table 21 shows
    the real required command bits are `0xA00` — never actually
    triggered on real hardware, since no board has been fabricated yet,
    but a real bug regardless. Table 21's own bit-diagram also gave
    real, visually confirmed, full bit-width layouts for every register
    this project had previously left as generic 12-bit setters or not
    reached at all: Spark Command, DAC Command, GPGD Short Threshold
    Voltage Command, GPGD Short Duration Timer Command, GPGD Fault
    Operation Select Command, and PWM0-3 Freq & DC Command — closing
    every one of the previously-named MC33810 gaps in this ledger
    except the Clock Calibration Command's own separate 32µs CS-pulse
    protocol (real, documented, but genuinely needs a dedicated
    CS-pulse primitive this driver doesn't have yet). One real,
    deliberately unresolved discrepancy surfaced and was *not* silently
    picked: Table 10 lists DAC Command's Overlap Setting default
    (`<100>`) as 35%, but Table 21's own default annotation for the same
    field/same code says 50% — both numbers are transcribed honestly in
    `mc33810.h` rather than one being guessed as correct.
- **MC33810 replaced by ST L9779WD-SPI** ([`inc/l9779.h`](inc/l9779.h),
  [`src/l9779.c`](src/l9779.c)) — a real part-obsolescence event, not a
  scope choice: the MC33810 hit **Last Time Buy** status (confirmed via
  DigiKey: last order date 2027-04-30, ~205 units left, no
  NXP-recommended replacement — see the `mc33810-end-of-life` memory).
  No true pin/footprint-compatible drop-in exists (the whole "narrow
  scope" IC segment has moved to bigger, more integrated engine-
  management ICs); L9779WD-SPI (HiQUAD-64, confirmed **Active**, 500
  units in stock) was chosen as the best-targeted real alternative,
  with real precedent — [rusEFI](https://github.com/rusefi/kicad6-libraries/blob/main/L9779WD-SPI.kicad_sym)
  maintains a real KiCad symbol for this exact part. `mc33810.h`/
  `mc33810.c` are kept in the codebase as reference for their own real
  register-map research but are no longer called from `main.c`.
  Real, genuinely different SPI protocol (ST DocID027721 Rev 2, Section
  6.16, read via a Wayback Machine snapshot since ST's live site and
  every mirror tried blocked or timed out automated fetches this
  session): a **5-bit address + 8-bit data + odd-parity-bit** 16-bit
  frame, not MC33810's 4-bit-address/12-bit-payload split. Real,
  critical gotcha caught before it became a live bug: outputs default
  to disabled (`OUT_DIS=1`) after reset — any control-register write is
  silently ignored until the real `START_REACT` command's `START` bit
  is sent, which `l9779_init()` now does for both chips automatically.
  Real, confirmed register map for this board's actual scope:
  `CONTR_REG1` bits 7:4 = `CMD_OUT1-4` (this board's 4 real injector
  channels), `CONTR_REG2` bits 3:0 = `CMD_IGN1-4` (4 real ignition
  channels), `DIA_REG1` = 2-bit-per-channel fault readback for OUT1-4
  (00=short-to-ground, 01=open load, 10=short-to-battery, 11=OK) — same
  real fault-encoding shape as MC33810's, different bit positions. Real
  SPI timing (Table 53: fop max 8MHz, tlead 525ns, tlag 50ns, tcsn
  640ns, tnodata 1.5µs — all genuinely different numbers from MC33810's)
  lives in its own new `DSPI_0` CTAR2 profile (`dspi_configure_ctar()`),
  not reused from MC33810's CTAR0, via the same generic multi-CTAR
  mechanism CJ125 already proved. Real, deliberately unused features
  (available for a later, separate decision — see the plan file's
  redesign section): a built-in VRS sensor interface (only one channel
  per chip, can't replace all 3 of this board's MAX9924s anyway), a
  built-in CAN transceiver, K-Line, a main relay driver, and a 4-channel
  stepper driver.
  **Update (later pass): a real, critical correctness bug caught by
  reading the functional-description prose, not just the register
  tables.** Both LSa (`OUT1-5`, injectors) and the ignition pre-drivers
  (`IGN1-4`) are driven by the real logical **AND** of their own SPI
  control bit and their own dedicated parallel input pin (Sections
  6.8.1/6.10.1 — "They are driven by logical-AND of SPI control bit and
  dedicated parallel input," stated near-verbatim for both). MC33810
  worked differently (real, confirmed **OR** logic — the parallel pins
  alone were already sufficient). Since `CONTR_REG1`/`CONTR_REG2`
  real-reset to all-OFF, this board's real eMIOS-driven parallel firing
  pins would have toggled correctly on real hardware and still never
  fired anything, silently, until this was caught — `l9779_init()` now
  permanently enables all 4 real channels' SPI side at startup
  (`CONTR_REG1`/`CONTR_REG2` = `0x0F`), after which the parallel pins
  have full, unblocked real-time control. Same pass also confirmed
  `IGN1-4` is a genuine IGBT gate pre-driver — Section 6.10.1's own
  opening line: "They can drive IGBT Darlington transistors" — closing
  the real, previously-open question of whether it's a true drop-in
  match for MC33810's `GDx`.
  **Update (later pass): a real hardware dependency this driver can
  neither detect nor work around, worth knowing before debugging a
  "firmware sends everything correctly but nothing fires" symptom.**
  `IGN1-4`'s pre-drivers are supplied from the chip's own `VDD5` rail
  (Table 28 lists `VDD5` 4.9-5.1 V as their supply voltage range and
  specifies their short-to-battery detection thresholds relative to
  it), and `VDD5` is a linear regulator that only exists if an
  **external NMOS pass transistor** and an external charge-pump
  capacitor are fitted — the feature list says "5 V precision voltage
  regulator (±2%) with external NMOS", and Table 13 lists both as
  *required* external components. Those were genuinely absent from
  `ecu-pcb` until a later pass fitted them for real (`Q20`/`Q21` +
  `C82`-`C87`) — before that, this board would have had no ignition
  drive at all despite perfectly correct SPI and parallel-pin timing.
  Two more real behaviors now documented in `l9779.h` because they
  affect how firmware should interpret a dropout rather than what it
  should write: the charge pump's real default is *conditional*, not
  always-on (Section 6.7 — active below 12 V Ubat, or permanently via
  the `capful` bit; the default is genuinely correct for this board and
  needs no firmware action), and above a 28 V Ubat overvoltage it
  "will be switched off automatically no matter the `cp_off` bit
  status" — so ignition dropping out during a real load-dump excursion
  is designed protection, not a fault to retry through.
  **Real, named gaps, not guessed:** the parity bit's own calculation
  isn't confirmed from the text read this session — every frame sends
  parity=0, a real placeholder; whether `DO`'s data is same-frame or
  one-frame-delayed relative to `DIN`'s address isn't confirmed either
  (`l9779_read_dia1()` defensively reads twice, same pattern as
  `mc33810_read_status()`); IGN1-4's own real fault-diagnosis register
  wasn't located in the pages read, so ignition fault readback isn't
  implemented yet; and MC33810's real RSP/RSN (current-sense), FBx
  (coil/collector-sense), and `DRV_OUTEN` (shared kill-switch) pins have
  no confirmed L9779WD-SPI equivalent — flagged in `ecu_pins.h`'s
  `PIN_DRV_OUTEN` comment and the plan file's redesign section as open
  items for the `ecu-pcb` schematic-wiring pass, not resolved here. The
  PCB-side half of this redesign (new footprint, rewired schematic,
  re-routed board) is tracked separately in the plan file and hasn't
  started yet.
- **ADC is fully real now** ([`inc/adc.h`](inc/adc.h),
  [`src/adc.c`](src/adc.c)) — `adc_init()`/`adc_read_channel()` are a
  complete, real, blocking one-shot single-channel driver, not stubs,
  called from `hardware_init()` for both real ADC instances. Real
  pieces, visually confirmed against Chapter 28's own register
  diagrams: MCR's OWREN/MODE/NSTART/PWDN, MSR's status fields, CDR's
  VALID/OVERW/CDATA result format. One real finding worth recording:
  the MPC5606BK has **two independent ADCs** (10-bit `ADC_0`, 12-bit
  `ADC_1`), and which of three Normal Conversion Mask Registers
  (NCMR0/1/2) enables a given channel — and at which bit — follows a
  clean, real formula that fell out of visually confirming all three
  (Figures 28-38 through 28-41): bit *k* of NCMR*n* enables channel
  *(k + BASE)*, BASE = 0/32/64 for NCMR0/1/2. `adc_read_channel()` is
  channel-number-generic (takes a raw ADC channel, not a pin).
  - **The real pin → ADC-channel mapping is done too** (a later pass) —
    `adc_channel_for_pin()` (`adc.c`) real-maps every one of the 15
    real sensor pins in `ecu_pins.h`, and `read_sensors()` (`main.c`)
    now actually populates a real `sensor_readings_t` from them (raw,
    unfiltered — see that struct's own comment for the real filtering
    TODO). The channel-number formula this needed isn't stated
    explicitly anywhere in Table 4-1 — it came from Figure 28-1's own
    block diagram instead: `ADCx_P[n]` = channel *n* (same number on
    both `ADC_0`/`ADC_1` — confirmed the same physical pin wires to
    both), `ADCx_S[n]` = channel *32+n*. All 15 pins land on `ADC_1`
    (12-bit — free extra resolution, since every P[n] pin is wired to
    both instances) except `PIN_ADC_KNOCK1`, whose pad has no P[n] at
    all — only `ADC0_S[0]`/`ADC1_S[4]`, a real, confirmed case where
    the two instances' channel numbers for one physical pin genuinely
    differ (32 vs. 36), not a copy-paste.
- **Crank period tracking is fully real now** ([`src/injection.c`](src/injection.c)) —
  `crank_capture_isr()` computes the real, 16-bit-wraparound-safe tick
  delta between the two most recent crank edges (`ticks_between()`),
  and `cam1_capture_isr()` records a real "has a cam edge been seen"
  flag, both exposed to `main.c` via new real accessors
  (`injection_crank_period_ticks()`, `injection_crank_synced()`).
  `main.c`'s `update_engine_state()` now uses the sync flag for a real
  `ENGINE_STATE_CRANK_SYNC` → `ENGINE_STATE_CRANKING` transition — the
  first real state transition in the engine state machine. This math
  is genuinely frequency/tooth-count-agnostic (plain modular arithmetic
  over `emios.h`'s own confirmed 16-bit capture register width), so
  none of it depends on an unconfirmed number. Two real, complete
  conversion formulas were also added (`us_to_ticks()`,
  `angle_to_ticks()` in `injection.c`) but are deliberately **not**
  wired into `injection_arm_cylinder()` yet — see below for why.
- **FlexCAN is fully real now** ([`inc/flexcan.h`](inc/flexcan.h),
  [`src/flexcan.c`](src/flexcan.c)) — `flexcan_init()`/
  `flexcan_transmit()`/`flexcan_receive_poll()` are a complete, real,
  polled Message-Buffer driver (not the Rx FIFO engine — this board's
  telemetry use case doesn't need FIFO's ID filter table), wired into
  `hardware_init()` for both of this board's real, independent buses
  (CAN0 = FlexCAN_1, CAN1 = FlexCAN_4 — confirmed against
  `../ecu-pcb/build_schematic.py`'s own real routing, which also
  surfaced 4 pins — `PIN_CAN0_TX/RX`, `PIN_CAN1_TX/RX` — missing from
  `ecu_pins.h` since this file was first written; added for real this
  pass, along with their real pin-mux entries in `siul2.c`). Every
  register bit layout used (MCR, CTRL, the Message Buffer C/S+ID word)
  was visually confirmed against Chapter 25's own figures. Real
  findings worth recording:
  - A first pixel-boundary reading of the C/S+ID word figure put the
    PRIO/ID split one bit off; Table 25-4's own text ("only the 11 most
    significant bits (3 to 13) are used") caught and corrected it — a
    real example of this project's "cross-check, don't trust one read"
    discipline catching itself mid-session.
  - The real Rx message buffer **lock mechanism** (Section 25.5.7.3):
    reading an active Rx MB's C/S word locks it against being
    overwritten mid-read; the lock only releases on reading TIMER (a
    real "global unlock" register read) or another MB's C/S word.
    `flexcan_receive_poll()` does the real C/S-then-ID-then-data-then-
    TIMER sequence for exactly this reason — skipping the TIMER read
    would leave the MB locked and silently stop receiving forever.
  - **`FLEXCAN_CTRL_500KBPS`'s bit timing is real too** (a later pass,
    once the system/peripheral clock gaps closed) — a real, standard
    automotive 500 kbit/s target, computed from the real 60 MHz CPI
    clock and Table 25-10's real formulas: `PRESDIV=5` (Sclock =
    60MHz/6 = 10MHz), `PROPSEG=7`/`PSEG1=6`/`PSEG2=3` (20 time quanta
    per bit = exactly 500kbit/s, sample point at a real, standard 80%),
    `RJW=1`. All five field values confirmed within their real valid
    ranges (Table 25-10).
- **eMIOS crank/cam capture channel init is real now** (a later pass,
  `main.c`'s `emios_capture_init()`, called from `hardware_init()`) —
  step 4 of the roadmap, previously entirely unwired. Also resolved in
  the same pass: `EDPOL`'s real sense in SAIC (capture) mode — Table
  27-17's own explicit field description ("1 = Trigger on a rising
  edge, 0 = Trigger on a falling edge"), not just its bit position.
  The existing `emios_init_capture_channel()` logic already matched
  this real meaning before it was confirmed — now verified, not merely
  assumed. One real, honestly-flagged default remains, narrower now:
  this board's crank/cam sensors are real MAX9924 VR-to-digital
  conditioner ICs (`../ecu-pcb/build_schematic.py`), and which specific
  edge lines up with which specific reference tooth (an absolute phase
  question) wasn't found in this session's extraction of its datasheet
  — board/winding-specific, not generic. **Update (later pass):**
  rendered the MAX9924's own Figure 1 timing diagram as an image (text
  extraction didn't preserve the visual relationship between COUT and
  the VR signal) and confirmed COUT is simply a squared version of the
  VR signal's sign relative to BIAS — high through its whole positive
  half-cycle, low through its whole negative half-cycle — so *both*
  COUT edges land on a real zero-crossing, which the datasheet's own
  "Zero Crossing" section ties to "the center of the gear-tooth," not
  just one edge. Capturing on either edge is therefore equally valid
  and can't produce spurious captures; `emios_capture_init()`'s
  rising-edge default is a real, safe (if unproven-absolute-phase)
  choice, not a guess that might not even trigger correctly.
- **INTC (Interrupt Controller) is fully real now** ([`inc/intc.h`](inc/intc.h),
  [`src/intc.c`](src/intc.c)) — closing most of the gap flagged
  immediately above, in the same pass. `intc_init()`/
  `intc_register_isr()`/`intc_dispatch()` are a complete, real
  software-vector-mode driver, visually confirmed against Chapter 18's
  own register diagrams (MCR, CPR, IACKR, EOIR) and cross-checked
  address formulas. Two real findings worth recording:
  - **PSRn (priority select) is genuinely byte-addressable per source**
    despite its own figure grouping 4 sources per 32-bit word for
    display — the field-description text confirms it, the same real
    pattern already seen for `ME_PCTL[n]` (`clocks.h`).
  - **eMIOS interrupt vectors are shared two channels at a time in real
    hardware** — Table 18-10 lists one IRQ number per *pair* of
    channels (`EMIOS_GFR[F0,F1]`, `EMIOS_GFR[F2,F3]`, ...). This
    board's crank (channel 0) and cam1 (channel 1) captures are exactly
    such a pair (IRQ 141); cam2 (channel 18) shares IRQ 150 with an
    unused channel 19. `intc_isr_emios0_ch0_1()`/
    `intc_isr_emios0_ch18_19()` (`injection.c`) check each channel's
    own FLAG bit individually (`emios_flag_is_set()`, a new real,
    side-effect-free read added to `emios.h`) before calling the
    matching real `*_capture_isr()` — the INTC vector alone can't tell
    the channels apart. `main.c`'s `intc_setup()` registers both real
    vectors at deliberately high priorities (15 and 14 — Table 18-5).
  - Software vector mode's real mechanism turned out simpler than
    expected: reading `INTC_IACKR` returns the complete, ready-to-
    dereference address of the current interrupt's own vector-table
    slot (not just a raw source number needing manual math) —
    `intc_dispatch()` is a direct function-pointer call, no INTVEC
    extraction/shifting needed.
  - **What's still real and separately gapped, narrower now than
    before:** the e200z0h core's own exception-vector setup (`IVPR`,
    `IVOR4` — "External Input") and the actual assembly-level interrupt
    prologue/epilogue that makes real hardware jump into
    `intc_dispatch()` in the first place. This is genuinely outside
    portable C and typically supplied by toolchain startup code (e.g.
    S32 SDK) — no local PowerPC-EABI toolchain was available this
    session to write or check it against. Until it exists,
    `intc_dispatch()` is real, correct C that nothing yet calls from
    real hardware — but everything feeding INTO it (vector table,
    priorities, per-channel dispatch logic) now is.
  - **Update (later pass): this gap was researched further and narrowed,
    not closed.** Chapter 15 "e200z0h Core" — the manual's own core-
    architecture chapter — was read in full. Real findings: `IVPR` is
    confirmed SPR 63 (Figure 15-2, the same figure already used for
    `SRR0`/`SRR1`/`CSRR0`/`CSRR1`/`SPRG0`/`SPRG1`); the core is
    explicitly described as a **VLE-only** design (variable-length-
    encoding Power Architecture instructions, not classic/Book E
    encoding) — a real, previously-unflagged constraint meaning the
    still-missing interrupt prologue/epilogue assembly must target a
    VLE-aware toolchain mode (e.g. GCC `-mvle`), not generic PowerPC.
    The individual `IVOR0`–`IVOR15` SPR numbers, however, are confirmed
    **absent** from this specific 964-page manual — an exhaustive search
    for "IVOR" returns only 2 generic prose hits, no numbered table —
    and Section 15.5 says why: it explicitly defers the full
    architecture-defined register set to "the Power Architecture
    specification," a separate document not available this session. Per
    this project's no-guessing discipline, `IVOR4`'s number is
    deliberately still not written into `intc.h` even though it's
    well-established elsewhere in the wider e200/Book E family in
    general knowledge — this project only commits numbers verified from
    a real source actually consulted this session. See `intc.h`'s file
    header for the full writeup.
  - **Update (later pass): closed for real, via a real, different
    primary source.** The e200z759n3 Core Reference Manual (a sibling
    e200-family core, fetched via a real community.nxp.com forum
    attachment) has its own real Table 16 with an explicit `IVOR0`–`15`
    SPR list (`IVOR4`=SPR404, etc.). This isn't the MPC5606B's own
    e200z0h-specific manual, so this project's own discipline required
    real corroboration, not just "close enough" — and got it: every
    other SPR number this codebase had already confirmed from the
    actual MPC5606BK manual (`IVPR`=63, `PIR`=286, `SPRG0`=272,
    `SPRG1`=273) appears in the e200z759 manual's same table with the
    exact same numbers, four independent matches with zero mismatches.
    `intc_ivor_init()` (`intc.c`) now sets `IVPR`/`IVOR4` for real via
    GCC inline `mtspr`, and a real exception-entry assembly stub
    (`src/intc.S` — context save, call `intc_dispatch()`, context
    restore, return-from-interrupt) is written and wired in
    (`intc_setup()`, `main.c`).
    **Update (later pass): the VLE-mnemonic gap is closed too, and it
    caught a real bug.** The actual Freescale/NXP VLE Programming
    Environments Manual (fetched via Wayback Machine) has a complete,
    real VLE instruction mnemonic list (Appendix B) — checked every
    mnemonic `intc.S` uses against it directly. The first version of the
    stub used several bare Book E mnemonics (`lwz`/`stw`/`stwu`/`addi`/
    `rfi`/`mflr`/`mtlr`/`mfxer`/`mtxer`/`mfctr`/`mtctr`/`mtcr`/`bl`) that
    are genuinely absent from the real VLE instruction set and would not
    have assembled on this VLE-only core — a real, confirmed bug, not a
    hypothetical one. Replaced with the real, confirmed VLE forms
    (`e_lwz`/`e_stw`/`e_stwu`/`e_addi`/`se_rfi`/`e_bl`, plus `mtspr`/
    `mfspr`/`mfcr`/`mtcrf`, all independently confirmed real and
    unprefixed under VLE). Real, still-open, narrower risk: individual
    instructions' exact bit-encodings/operand-range limits weren't read
    this pass — a real VLE assembler (still not available this session)
    would be needed to catch a range/encoding mistake at assemble time.
    `e200z0h`'s own exact `IVOR4` field width also remains unverified
    (see `intc.c`'s own comment). This is real, substantive progress,
    not a full toolchain-verified guarantee.
- **CJ125 (wideband O2) driver is real now** ([`inc/cj125.h`](inc/cj125.h),
  [`src/cj125.c`](src/cj125.c)) — a whole new peripheral this session,
  built from the same real Bosch CJ125 datasheet already used during
  this project's PCB design phase. `cj125_init()`/`cj125_transfer()`/
  `cj125_read_ident()`/`cj125_read_diag()`/`cj125_handle_diag()` are
  real and wired into `hardware_init()`/`poll_driver_faults()` —
  closing a TODO that had been sitting since the MC33810 pass. The real
  register map, command bytes, and bit fields came from the datasheet's
  own SPI block-diagram figure (drawn, not stated as plain text — raw
  text extraction alone would have missed it entirely, same discipline
  as every MPC5606B driver header in this project). Two real findings:
  - **CJ125's response pipelining is genuinely different from the
    MC33810's** — confirmed directly from the datasheet's own Read
    Access timing diagram: the response DATA byte is for the *current*
    command, arriving in the same 16-bit exchange (only the leading
    status byte reflects the previous transfer), not delayed a whole
    transfer like the MC33810's pattern this project already knew.
    Missing this would have made every real register read return the
    wrong device's data.
  - **CJ125 needs a different, much faster real baud rate (2 Mbaud max)
    than the MC33810s share on CTAR0** — real, since both chip families
    share one DSPI_0 bus. This surfaced a real gap in the DSPI driver
    itself: `dspi_transfer()` only ever used CTAR0. Fixed generically,
    not with a CJ125-specific hack — `dspi_configure_ctar()`/
    `dspi_transfer_ctas()` (`dspi.h`/`dspi.c`) let any second real
    device share the bus on its own CTAR profile. `CJ125_CTAR1`
    computes a real, conservative 1.875 MHz SCK from the confirmed
    60 MHz DSPI_0 peripheral clock.
- **Sensor filtering is real now** (`main.c`'s `iir_update()`) — a
  simple, real, integer-only exponential-moving-average filter applied
  to every slow-moving sensor (MAP/TPS/IAT/CLT/VBATT/oil-fuel
  pressure/APP1-2/TPS1-2/EGT/ETC feedback) in `read_sensors()`.
  Deliberately excludes knock1/knock2: knock sensors are real
  piezoelectric pickups reporting a fast (multi-kHz) vibration
  transient, not a slowly-varying quantity, so the same EMA filter
  would smear out exactly the signal real knock detection needs — those
  two fields stay raw, unfiltered instantaneous reads, a real,
  intentional distinction, not an oversight. `SENSOR_IIR_SHIFT`'s
  specific time constant isn't tuned against a real running engine yet
  (can't be, until one exists) — same scope boundary as VE tables.
- **Real CLT (coolant temperature) sensor driver** ([`inc/clt_sensor.h`](inc/clt_sensor.h),
  [`src/clt_sensor.c`](src/clt_sensor.c)) — the board's CLT sensor was
  swapped to a real GM-style resistive sending unit (DIYAutoTune's
  "GM Closed Element CLT/Oil Temperature Sensor", the same real part/
  curve the sibling [thermo-pcb](https://github.com/jessiepullaro414/Thermo)
  project uses for its own engine-temperature sensor, and genuinely what
  production automotive coolant senders are). Real published curve
  (manufacturer's own page, only 3 points exist): -40°F=100.7kΩ,
  86°F=2.24kΩ, 210.2°F=177Ω. Since only 3 real calibration points exist,
  a single global NTC Beta constant across the full 250°F span would be
  a real approximation error (the two segments' own local Betas differ
  by ~8%, confirmed by computing both) — so a piecewise-Beta model was
  fit per real segment and used to derive a 14-row lookup table at
  design time (shown in `clt_sensor.c`'s own derivation comment), with
  plain integer linear interpolation at runtime, no floating point,
  matching every other driver here. Readings outside the real calibrated
  range are clamped, not extrapolated (no real data exists past -40°F
  or 210.2°F) — which doubles as free open/short sensor-fault detection,
  same behavior the identical circuit already gives thermo-pcb. This
  redesign also caught and fixed a real hardware bug on the PCB side:
  both IAT's and CLT's pull-ups were wired to +5V despite this MCU's ADC
  domain genuinely running at 3.3V (no separate VRH/VRL pins) — a real
  latent over-voltage risk at cold temperatures, fixed in
  `ecu-pcb/build_schematic.py` (R24/R25 now pull up to +3V3). Not yet
  wired into engine-control logic (warm-up enrichment) — that's real
  engine-specific tuning data, same "needs a running engine" boundary as
  the VE/dwell tables, see `injection.h`'s own note.
- **Real IAT (intake air temperature) sensor driver**
  ([`inc/iat_sensor.h`](inc/iat_sensor.h), [`src/iat_sensor.c`](src/iat_sensor.c))
  — same session, same real part swap for the board's IAT sensor
  (DIYAutoTune's "GM Open Element IAT Temperature Sensor"). Real, honest
  discrepancy found and resolved while sourcing it, not glossed over:
  the IAT product page's own published 3-point curve shares its first
  two resistance values EXACTLY with CLT's own curve (100.7kΩ@-40°F,
  2.24kΩ@86-87°F) but disagrees on the third point's temperature for the
  identical 177Ω reading (146°F here vs. CLT's 210.2°F) — two genuinely
  different real sensors can't both be right about that. Real evidence
  this is copy-paste content contamination on DIYAutoTune's own IAT
  page, not two authentic curves: a leftover sentence on the IAT page
  itself calls it a "closed-element sensor" despite the product being
  titled/featured as open-element, and taking 146°F at face value would
  imply a per-segment NTC Beta more than 2x CLT's own for the shared
  -40..87°F segment (~7900K vs. ~3800K) — physically implausible for one
  real thermistor, versus CLT's own internally-consistent ~8% spread.
  Conclusion: genuinely the same underlying GM-pattern thermistor
  element as CLT, different physical package (open element for air vs.
  closed/NPT for liquid) — `iat_sensor.c` reuses CLT's own
  already-cross-checked curve rather than the IAT page's likely-
  erroneous 146°F figure (kept, separately, as `IAT_RATED_MAX_TENTHF` —
  DIYAutoTune's real stated max operating temp for this specific
  package, an honest fact worth keeping even though it's not used as a
  second clamp). R24=4.22kΩ (E96), deliberately different sizing from
  CLT's R25=1.00kΩ: IAT genuinely swings across nearly its whole real
  range in normal use (cold-soak to hot under-hood air), unlike
  thermostatically-regulated coolant, so it's sized via geometric-mean
  (sqrt(177Ω × 100.7kΩ) ≈ 4.22kΩ) for resolution across the full span
  rather than centered on one narrow sub-range.
- **Real EGT thermocouple front end** ([`inc/ads1118.h`](inc/ads1118.h),
  [`src/ads1118.c`](src/ads1118.c)) — a whole new peripheral, driven by
  a real qualification problem rather than a feature request. The
  board's EGT channel used an AD8495 thermocouple amplifier feeding the
  MCU's own ADC; that part carries no AEC-Q100 statement, and **no
  AEC-Q100 dedicated thermocouple amplifier IC exists at all** (MAX31855,
  MAX31856, AD8495, LTC2983, MCP9600 all checked). So the fix was
  architectural: a real AEC-Q100 Grade 1 ADC (TI ADS1118-Q1) reads the
  thermocouple millivolts directly over SPI, and cold-junction
  compensation moves into firmware using the device's own on-die
  temperature sensor. Every register fact here is from TI's SBAS457F,
  read this pass: Config register layout (Figure 44/Table 7), the
  32-bit transaction format (Section 9.5.7.1), and the cold-junction
  data format — 14-bit, **left-justified** in the 16-bit result,
  0.03125 °C/LSB, two's complement (Table 4, with TI's own worked
  examples confirming it). Two real findings worth calling out: the
  `NOP[1:0]` field **must** be `01` or the device silently ignores the
  config write entirely, and this part needs **CPHA=1** (it shifts DOUT
  on the rising edge and latches DIN on the falling), which is why it
  cannot share CJ125's CTAR1 despite both being comfortably slow enough
  — it takes its own CTAR3 at 1.875 MHz, well under this part's real
  4 MHz ceiling. Moving EGT to SPI also freed the analog pin it used to
  occupy (PD[9]) to become this device's chip select, so it cost no new
  MCU pin — `siul2.c`'s own earlier Table 4-1 check had already
  established those Port D analog pads are usable as GPIO.
  **Real, named gap, deliberately not faked:** the ITS-90 type-K
  conversion (cold-junction temperature → equivalent voltage → total
  voltage → temperature) is *not* implemented. NIST's own table
  download returned an HTML page rather than data, and this project does
  not invent numeric constants from recall. The SPI layer, config,
  both raw reads and the voltage scaling are complete and real; only
  that final conversion is outstanding, and `ads1118.h` records the two
  real anchor points TI's datasheet does confirm (50.644 mV at 1250 °C
  against a 0 °C cold junction, 52.171 mV against −40 °C). `main.c`
  stores both halves honestly rather than computing a fabricated
  temperature from them.
- **Real watchdog (SWT) driver, closing a standing TODO** ([`inc/swt.h`](inc/swt.h),
  [`src/swt.c`](src/swt.c)) — a whole new, previously-untouched chapter
  (Chapter 33, "Software Watchdog Timer", 9 self-contained pages) read
  and implemented in full: real register map, real fixed service
  sequence (`0xA602`/`0xB480`), real field bit positions. One real,
  genuinely useful architectural fact this unlocked: SWT's counter
  clock is the undivided 128kHz SIRC, explicitly confirmed independent
  of this board's own FMPLL/system-clock configuration — meaning
  `swt_init()` can run (and does) as the *very first* thing in
  `hardware_init()`, before `clocks_init()`. That directly closes a
  previously-standing TODO in `main()`'s own comment ("a watchdog-forced
  reset instead of a silent halt" on a clock-bring-up failure) — the
  halt loop deliberately never services the watchdog, so a real,
  automatic reset now follows instead of an infinite silent spin.
  `swt_service()` is called once per real main-loop iteration, giving
  this firmware a genuine backstop against a hung main loop that the
  driver ICs' own hardware protections (MAXI/max-dwell, GPGD fault
  handling) can't provide, since those only catch output-stage faults,
  not a hung MCU. Real, deliberate, not-yet-measured choice: a 100ms
  timeout (12800 cycles at the confirmed 128kHz clock), more
  conservative than the chip's own 10ms reset default, since this
  firmware's real main-loop iteration time has never been measured (no
  systick/timer wired up yet). **Update (later pass): `ITR` is now a
  deliberate design choice, not a blocked gap.** SWT's real "Timeout"
  interrupt source was found in Table 18-10 (`SWT_IRQ_TIMEOUT` = IRQ 28)
  — real hardware interrupts can reach C code now (the e200z0h IVOR4
  gap is closed, see the INTC entry above), so nothing mechanical blocks
  wiring an `ITR=1` interrupt-then-reset ISR anymore. `swt_init()`
  deliberately keeps `ITR=0` (immediate reset) anyway: for a hung-
  main-loop backstop on a running engine, giving a genuinely stuck
  system a second timeout window to keep misbehaving is a worse real
  outcome than a fast, deterministic reset — a real safety trade-off,
  not an oversight. `SWT_IRQ_TIMEOUT` is documented and ready if that
  judgment ever needs revisiting. Window mode and keyed pseudorandom
  servicing remain deliberately unused too.

**Deliberately left as TODO, not guessed:**
- **A real CAN message ID/payload map.** `broadcast_can()`'s CAN ID
  (`0x100`) and single-byte `engine_state` payload are placeholders,
  not a defined protocol — this ECU's actual telemetry format is an
  application-layer decision, same scope boundary as VE tables.
- **RPM and any real angle→ticks conversion.** `us_to_ticks()`/
  `angle_to_ticks()` (`injection.c`) are real, correct, generic
  formulas, ready to use — but calling them for real needs two numbers
  this project doesn't have: the eMIOS peripheral's real tick frequency
  (same downstream MC_CGM peripheral-divider gap noted above for
  DSPI/CAN — the *core* clock is real now, this specific peripheral's
  clock still isn't derived from it) and the real crank trigger wheel's
  tooth count/pattern (an engine/sensor hardware choice, not a board
  one — same scope boundary as firing order and VE
  tables). `injection_arm_cylinder()` still passes microseconds
  straight through to `emios_set_pulse_width()` uncoverted as a result.
- **Which tooth/cylinder is at TDC.** `crank_capture_isr()` tracks a
  real period but doesn't yet decide when to call
  `injection_arm_cylinder()` — that needs the real trigger wheel tooth
  pattern and firing order, neither of which exist yet.
- Injected conversions, scan mode, DMA, interrupts, the analog
  watchdog, and CTU-triggered conversion (ADC) — real features with
  their own register groups, not touched by this pass's one-shot
  polled driver.
- ~~A real ADC power-up delay~~ — **resolved, a later pass, via a real,
  different primary source.** The Reference Manual explicitly deferred
  electrical timing specs to a separate Data Sheet document — this pass
  found and fetched that real document (MPC5606B Data Sheet, Rev. 5, via
  a Wayback Machine snapshot, since the live NXP URL 404'd the same way
  every other nxp.com doc did this session). Section 3.17 "ADC electrical
  characteristics" gives the real numbers: `tADC0_PU`/`tADC1_PU` (power-up
  delay) = 1.5µs max, `fADC0`/`fADC1` (analog clock) = 6–32MHz. Computed
  a real, worst-case-safe `PDED` value (`ADC_PDED_MIN` = 64 cycles,
  covering the fastest real `fADC` so the delay holds regardless of
  `ADCLKSEL`'s setting) and `adc_init()` now writes it to the real
  `PDEDR` register. **Update: `ADCLKSEL` itself is resolved too** —
  found back in the Reference Manual (Section 28.3.2/Table 28-11): a
  single MCR bit, 0 = half the Peripheral Set clock, 1 = equal to it.
  `adc_init()` never sets it, so it stays at its real default (0) —
  this board's real, exact `fADC` is `60MHz / 2 = 30MHz`, comfortably
  inside the confirmed range. The real exact minimum at that frequency
  would be 45 cycles; `ADC_PDED_MIN` stays at the more conservative 64
  rather than being retuned tighter. The old
  software busy-wait placeholder is gone, not just redundant, since
  `PDEDR`'s own real purpose is to make the hardware itself enforce this
  delay before the next conversion. (Also caught and fixed a real bug in
  this same ledger while re-checking: `PDEDR` was previously miscredited
  with a non-zero reset default alongside `CTR0-2` — re-verified against
  its own diagram, `PDEDR`/`DSDR` both reset to 0; only `CTR0-2`'s
  non-zero reset was ever real.)
- ~~`fmpll_wait_lock()` and `me_transition_to()` have no timeout~~ —
  **resolved** (a later pass): both now return 1/0 (success/timeout)
  after a real, bounded `CLOCKS_WAIT_ITERATIONS` count (`clocks.h`), and
  `clocks_init()`/`hardware_init()`/`main()` all propagate a real
  failure up to a genuinely minimal (halt-only) fault response — see
  `main()`'s own comment for why that's honestly named as minimal, not
  a complete fault strategy. `flexcan_transmit()` and
  `adc_read_channel()` got the same real, bounded-iteration-count
  timeout treatment in the same pass (`flexcan.h`/`adc.c`) — none of
  these are calibrated against a real time unit (this firmware has no
  systick/timer wired up yet to measure real microseconds against),
  just a real, generous iteration count at this board's confirmed
  60MHz core clock, honestly documented as such rather than implying a
  precision they don't have. This is what made it safe to finally call
  `broadcast_can()` from the real main loop (see below) — the whole
  reason a real CAN timeout was worth adding.
- **`ME_PCTL[n]`/`ME_RUN_PCn`'s real reset values** — real register
  addresses located (cross-checked between Table 32-5 and Table 6-1),
  but the actual per-peripheral clock-gating reset state wasn't
  found/confirmed this session (see `clocks.h`'s `CGM_SC_DC0` comment).
  Plausible peripherals are gated ON by default, not proven. **Update:**
  confirmed genuinely absent from this manual, not unresearched —
  Chapter 8 (pages 144–177) never contains a bit-diagram section for
  either register, despite two other places in the same document
  pointing to one that doesn't exist. Closing this for real needs a
  different real source than what's in hand.
- ~~`dspi_transfer()`'s real baud rate~~, ~~MC33810 tLEAD/tLAG timing~~,
  ~~VDD=3.3V confirmation~~ — all **resolved**, see the DSPI/MC33810
  "real" section above for the real numbers and findings.
- ~~The Spark Command and DAC Command registers' exact multi-bit field
  widths~~ — **resolved**, along with ~~GPGD short-threshold/duration
  timer commands~~ and ~~PWM0-3 freq/duty command~~ — see the MC33810
  "real" section above (Table 21's own bit-diagram gave every one of
  these for real, plus caught a real write-address-space bug in the
  process). The MC33810's Clock Calibration Command's own 32µs CS-pulse
  protocol is real and documented (`mc33810.h`) but still not
  implemented — genuinely needs a dedicated CS-pulse primitive, not
  just a command address.
- Everything engine-specific: firing order, VE/dwell/timing tables,
  fuel injector flow-rate scaling. These depend on the actual engine
  this ECU ends up on, not the board.

## Toolchain

**The firmware now compiles and links into a real, bootable image.**
`powerpc-eabivle-gcc` 4.9.4 (built for `ELe200`, the e200 core family
this MCU uses) builds all 18 source files — including the hand-written
VLE assembly in `startup.S` and `intc.S` — and links them against
[`link/mpc5606b.ld`](link/mpc5606b.ld) into `build/ecu.elf`. Run it
from the repo root:

```bash
python buildWholeProject.py --firmware-only
```

Current image: **12,932 bytes of flash** (of 1 MB) and **3,036 bytes of
SRAM** (of 80 KB) — plenty of headroom.

**Watch out for the wrong product.** NXP ships two separate IDEs both
called *S32 Design Studio*, split by architecture, and only one can
build this MCU:

- *S32 Design Studio for **S32 Platform*** is the **ARM** line
  (S32K/S32G). Its bundled GCCs are `arm32-eabi`/`arm64-eabi` only —
  no PowerPC target at all. Having it installed does not help.
- *S32 Design Studio for **Power Architecture*** (S32DS-PA) is the one
  this board needs. It lists MPC5606B as a supported device and ships
  `powerpc-eabivle` GCC under `Cross_Tools`.

`buildWholeProject.py` searches the real S32DS-PA install locations
automatically, and also finds a standalone `powerpc-eabivle-N_N`
toolchain tree; `ECU_FW_TOOLCHAIN_PREFIX` overrides both.

NXP's **TRK-MPC5606B StarterTRAK** kit ships real reference code for

NXP's **TRK-MPC5606B StarterTRAK** kit ships real reference code for
this exact part's peripherals (SCI/CAN/LIN/GPIO) — a genuine reference
worth pulling if any of the remaining named gaps above (real clock
divider values, bit-timing/baud-rate figures, CJ125 support) need a
second real source to check against.

## Boot and startup

Two files carry everything between reset and `main()`, and both are
built from the MPC5606BK Reference Manual rather than a vendor template.

**[`link/mpc5606b.ld`](link/mpc5606b.ld)** — code flash 1 MB at
`0x0000_0000`, L2SRAM 80 KB at `0x4000_0000` (Table 31-3). It also
carries two `ASSERT`s that turn silent runtime corruption into
link-time errors, the useful one being an SRAM overflow check so
`.data + .bss + stack` can never quietly exceed 80 KB.

**[`src/startup.S`](src/startup.S)** — the reset entry. Two parts of it
are genuinely easy to get wrong and expensive to debug:

1. **The boot header.** Chapter 5 / Figure 5-2: the SSCM scans each
   boot sector for `BOOT_ID = 0x5A` in bits 8:15 of the word at the
   sector base, then jumps to the 32-bit vector at offset `0x4`.
   PowerPC numbers bits MSB-first, so that word is **`0x005A0000`**.
   Get it wrong and the SSCM finds no valid sector across all five,
   hands over to the BAM, and parks the core in static mode — the chip
   never runs your code and gives no other symptom. The build verifies
   the word actually landed rather than assuming it did.
2. **SRAM ECC initialisation, before the stack exists.** Section 31.5
   is explicit that because ECC syndrome bits power up random, SRAM
   "must be initialized by executing 32-bit write operations prior to
   any read accesses" — including implicit reads caused by sub-32-bit
   writes. A stack frame is read back on the way out, so the stack
   cannot be touched until this is done. The init therefore runs out of
   registers only: no stack, no reads, no calls. It uses `e_stmw` to
   write all 32 registers (128 bytes, 32-bit aligned, even count, as
   Section 31.6 requires) per iteration — 640 iterations covering
   exactly 80 KB, which the disassembly confirms.

`startup.S` also supplies a no-op `__eabi`, which GCC calls at the top
of `main()` on PowerPC EABI targets; the small-data bases (`r13`/`r2`)
and `.data`/`.bss` init it would normally arrange are already done
explicitly in the startup path.

## Real-time architecture

Two timing domains, deliberately kept separate (see `src/main.c` and
`inc/injection.h` for the full reasoning):

1. **Interrupt context** — crank/cam capture only. Decides fire angles,
   arms the next eMIOS event. Nothing else should run here.
2. **Main loop** — ADC sampling, table lookups, MC33810/CJ125 SPI
   polling, CAN, fault monitoring. Feeds the *next* interrupt-context
   decision; never blocks waiting on the current one.

No RTOS in this skeleton — the actual hard-real-time work (pulse
timing) is done by the eMIOS hardware once armed, not by software
scheduling, so a plain main loop is a reasonable real starting point
(the same structure most bare-metal EFI firmware uses). If task
isolation becomes worth the complexity later, **Erika Enterprise** is
the real open-source option for this MCU's e200 core — OSEK/VDX
RTOS, GPL, with confirmed Power-Architecture e200 support for several
MPC56xx variants (MPC5674F, MPC5668G, MPC5643L). The MPC5606B itself
isn't confirmed on their supported list — same "verify, don't assume"
gap as everything else here.

## Open-source reference material (not reusable code)

**rusEFI** is the closest real open-source EFI project in spirit — the
same MC33810 driver-IC pattern this board uses — but it targets STM32
only; there is no Power Architecture support. Its actual firmware code
won't port. Its documented *algorithms* (VE table interpolation,
cranking/warm-up compensation, closed-loop O2 trim structure) are
genuinely worth reading as a reference while writing the table-lookup
logic this skeleton's `update_tables()` stub leaves open.

## Known open items

- **All six driver roadmap steps have real work landed** (see above):
  eMIOS, SIUL2/pin-mux, clock/mode bring-up, DSPI, ADC, crank-period
  tracking, and FlexCAN. Step 4 (eMIOS) is partial (crank/cam capture
  channel init is real, injector/ignition arming deliberately deferred
  — needs real tick values this project doesn't have). Step 5 is
  partial too (period tracking is real, RPM/angle-to-ticks conversion
  is correctly formula'd yet deliberately not wired in). The clock
  chain is now real end to end for DSPI/CAN (60MHz core →
  confirmed-undivided peripheral clock → real, computed 937.5kHz SPI
  baud rate and 500kbit/s CAN bit timing). INTC (Interrupt Controller)
  is real too now — real vector table, priorities, and per-channel
  dispatch for this board's two real, shared eMIOS capture vectors.
  Every blocking wait that used to have no timeout (`fmpll_wait_lock()`,
  `me_transition_to()`, `flexcan_transmit()`, `adc_read_channel()`) now
  has one — real, bounded, iteration-count-based (not calibrated
  against a real time unit, no systick/timer wired up yet, honestly
  documented as such). ~~What's left: ADC's own real settling-TIME
  figure~~ — resolved, see the ADC entry above (real MPC5606B Data
  Sheet, real `PDEDR` value now written). What's left: the
  still-honestly-unconfirmed `ME_PCTL`/`ME_RUN_PC` per-peripheral
  clock-gating reset values (plausible but not proven — checked this
  same new real MPC5606B Data Sheet for `ME_PCTL`/`ME_RUN_PC` too;
  genuinely absent from it as well, real confirmed, not just unfound —
  this specific gap needs a different real source than either document
  in hand). **Update: the e200z0h core's own
  `IVPR`/`IVOR4` exception-vector setup is real now too** (`intc_ivor_init()`,
  `intc.c`, plus a real assembly entry stub, `intc.S`) — see the INTC
  entry above for the full provenance and its own honestly-flagged,
  still-open risks (VLE-encoding and exact `IVOR4` field width, neither
  independently verified this session, no local PowerPC-EABI/VLE
  toolchain available to check either against). The CJ125 (wideband O2) driver is
  real now too, a whole new peripheral this session, closing the
  MC33810-pass TODO — see above.
- `injection_arm_cylinder()` still passes microseconds straight through
  to `emios_set_pulse_width()` unconverted, and `crank_capture_isr()`
  doesn't yet decide when to actually arm a cylinder - both need real
  numbers (eMIOS tick frequency, crank trigger wheel geometry, firing
  order) that don't exist yet (see above).
- `broadcast_can()` is now called from the real main loop (see above) -
  `flexcan_transmit()`'s real timeout made that safe.
- MC33810 SPI transfer (`mc33810_transfer()`) is implemented with a
  real, conservative baud rate and real tLEAD/tLAG/tSTR timing margins
  (see above) - both previously-open gaps now closed.
- No firing-order, VE, dwell, or timing table exists — engine-specific,
  not board-specific.
- **The real CJ125 INIT_REG1/2 byte values for this board's actual
  running configuration** — `cj125_write_init1()`/`cj125_write_init2()`
  are real, generic byte setters, but which gain range (`VL`) matches
  the real LSU4.2 sensor's calibration and which pump-reference-current
  code (`PR0-3`) to use needs either a fuller application-note
  extraction (the PRx-to-µA transfer function wasn't in this session's
  excerpt) or bench calibration against a real sensor — neither done.
  Nothing calls the write functions yet as a result. **Update (later
  pass):** a web search surfaced a claimed conflicting value
  (`0x88` for "normal, gain=8×" in a real, widely-used open-source EFI
  project's driver code) — rather than trust an AI-summarized secondhand
  claim, it was checked directly against the real Bosch datasheet's own
  `INIT_REG1` diagram (already rendered this project, `cj125_spi_page.png`).
  The primary source re-confirms this file's existing bit positions
  exactly (`x|PA|x|RA|x|LA|x|VL`, bits 7/5/3/1 reserved) — the claimed
  `0x88` never actually got verified from source (the real fetch was
  blocked), so it doesn't count as a real contradiction. `cj125.h` now
  documents the real, generic formula for a "normal, gain-8×,
  measurement mode" byte (`PA=1` alone = `0x40`) — still not this
  board's specific chosen configuration, that part of the gap remains.
- The CJ125's own real command-byte "hec" (header error check)
  generation algorithm — not reverse-engineered; this driver uses the 6
  real command byte values directly rather than needing it, but no 7th
  command can be synthesized without finding the real algorithm.
- Lambda/AFR conversion from the CJ125's real UA/UR analog outputs —
  those are separate real ADC channels, not covered by this SPI-only
  driver, and weren't cross-checked against `ecu_pins.h` this session.

## Reference material used this session

The real NXP MPC5606BK Microcontroller Reference Manual, Rev. 2,
05/2014 (964 pages) — the primary source for everything in `emios.h`,
`siul2.h`, `clocks.h`, `dspi.h`, `adc.h`, `flexcan.h`, and `intc.h`. Not re-hosted here; if you need it again, NXP's own direct
link 404s in some environments (bot-protected), but it's mirrored at
`dtsheet.com/doc/2288517` and was also fetched successfully via a
regular browser session pointed at NXP's own
`docs/pcn_attachments/16234_MPC5606BRM_Rev2.pdf` when curl's request
was blocked.

The real Bosch CJ125 datasheet (Robert Bosch GmbH, 04/2006) — the
source for `cj125.h`/`cj125.c`, the same real datasheet already used
during `../ecu-pcb`'s own PCB design phase (see that project's own
schematic comments). Landed via a Wayback Machine mirror after direct
fetches were blocked, same real pattern as this project's other
datasheets.
