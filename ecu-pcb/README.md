# ECU — standalone automotive engine-management ECU

Companion KiCad project for a from-scratch standalone ECU: drives fuel
injectors and ignition coils directly, closed-loop on engine sensors,
usable across 4/6/8-cylinder engines from a single board, with both BLE
and USB-C as full (not partial) wireless/wired firmware-flashing paths.

Sibling project to [`../manifold-pcb`](../manifold-pcb) (a smaller
Arduino-Nano-pinout automotive sensor board) — this project reuses
Manifold's script-driven, kiutils + kicad-cli-verified generation workflow
directly rather than reinventing it, but is a separate, much larger board:
new directory, new schematic, not built on top of Manifold's layout.

**Status: schematic complete, board placed + fully routed + DRC-clean.**
Steps 2-9 (schematic) and step 10's layout/routing are done. The board
is **172.5 × 114.4 mm, 8 layers, 224 footprints, 236 nets**, fully
routed (0 unrouted) with GND planes poured on In1/In3/In6.Cu and +3V3 on
In4.Cu. Real `kicad-cli pcb drc` reports **0 errors and 0 unconnected
items** — the only remaining findings are the same 19 benign
`lib_footprint_mismatch` warnings this project (and Manifold before it)
has always carried.

Routing on a board this dense (236 nets, a 0.5mm-pitch H-bridge QFN, 8
layers) sits right at FreeRouting's practical convergence limit:
repeated full regeneration+route cycles land at score ~992-993 with
0-3 residual nets that vary run to run — ordinary autorouter variance,
not a stuck design, and this specific committed board is one of the
runs that landed at a genuine, DRC-verified zero.

A **distributor-verified BOM is the last remaining piece.**

The board grew from 144 parts / 160 nets in a
**sensor and actuator expansion** that closed real functional gaps found
in a full pre-BOM design review — battery-voltage sensing, a second cam
input, VVT/idle/fuel-pump/tach outputs, oil and fuel pressure inputs, a
second knock channel and a second wideband O2 bank. Every one of those
reuses a part already registered and datasheet-verified elsewhere in
this project, so the expansion introduced **no new unverified
component**. See "Sensors and actuators" below.

It grew again, from 200 parts / 209 nets to the current 224 / 236, in a
**boost/EGT/flex-fuel/ETC expansion** that added four real subsystems:
boost-control solenoid drive, exhaust-gas temperature, flex-fuel sensor
input, and full electronic throttle control (redundant pedal + throttle
sensing, H-bridge motor drive). This is the expansion that used up the
harness's last spare pins (J5 now has zero) and introduced this
project's first two parts without a found AEC-Q100 statement (AD8495,
MC33926 — the MC33926 has since been **confirmed AEC-Q100 Grade 1**;
the original absence turned out to be a stale datasheet revision, see
"Known open items") — see "Sensors and actuators" below.

One real routing problem came out of this expansion worth recording:
the MC33926's GND pin (pin 5, AGND) sits on a 0.5mm-pitch QFN wedged
between two VBATT_SW pins that were themselves already using the only
open board around it — not a hard-to-find escape, but a **provably
nonexistent** one on F.Cu (checked by exhaustively searching every
reasonable 2-bend path against the real routed geometry of its
neighbors). The real fix is what real fine-pitch QFN escape uses for
exactly this: a **microvia via-in-pad**, blind from F.Cu straight to the
GND plane on In1.Cu, plus a small keepout on In1.Cu reserving that same
spot before routing even starts — without the keepout, FreeRouting is
free to route an ordinary signal through that exact point (since planes
aren't poured until after routing), and on separate runs it did, twice,
once as a clearance graze and once as an outright short. See the
`U20_PAD5_KEEPOUT_R` comment in `route_board.py` for the full story.

Placement went through two significant user-driven reworks worth
recording, because both were real fixes rather than cosmetics:
1. **Compactness and edge-mount connectors.** All three edge connectors
   now define real board edges rather than floating inside them: J4/J5
   at the bottom via the TE AMPSEAL's own vendor "PCB EDGE" marker
   (local Y=13.5 — its pads sit at Y 0–8 and its body runs on to 36.1,
   i.e. it is *designed* to overhang ~22.6mm past the edge into free
   air), and J3 at the top via the USB-C receptacle's mating face
   (local Y=+5.23, verified from the Amphenol footprint: the 24 signal
   pads are at the rear, the front shield legs at +2.84). All three
   land on the outline to **0.000 mm**. J3 in particular had been left
   stranded ~23mm inside the board — unreachable by any cable — because
   the top edge was derived from whatever content happened to sit
   highest, and passives had drifted above it.
2. **Cluster-based placement.** Getting this right took three attempts,
   and the failures are worth recording because they were only ever
   visible by *looking at the board*, never from the numbers:
   - *Uniform rows.* The first compact pass tiled parts into rigid rows
     — dense, but nothing like a real layout.
   - *Confetti.* The second packed all the big parts tight first and
     then hunted for somewhere to put each passive. But a tight pack of
     big parts leaves no room between them, so passives got flung to
     whatever hole existed elsewhere, ending up tens of mm from the
     chip they belong to. A decoupling cap that far from its pin isn't
     decoupling anything.
   - *What it does now.* Each IC is packed **together with the passives
     that are electrically its own**, as a single cluster, and it is
     the clusters that get arranged — into blocks, then onto the board.
     Passives physically cannot drift away from their chip, because
     they're inside the same rectangle before it's ever placed. This
     deleted the entire gap-search and compaction machinery (~190
     lines) that existed only to fight the previous approach.

   Association is by real net sharing, with **schematic proximity** as
   the fallback — which matters more than it sounds, because a plain
   +5V-to-GND decoupling cap touches nothing but common rails, so the
   netlist alone can say nothing about it, yet those are exactly the
   parts that most need to be next to their chip. The schematic already
   encodes the answer, since `build_schematic.py` draws each IC's
   decouplers beside it. Without this fallback, 34 of the SENSOR block's
   91 parts fell through into one anonymous pile.

   Chips that **talk to each other** are clustered together too (any two
   ICs sharing ≥2 real signal nets). This was not cosmetic: the first
   route of the clustered board failed to connect `FT_RXD`, because the
   arbitration switch and the USB bridge had landed 28mm apart with the
   BLE chip between them. Merging them costs ~15% board area and buys a
   board that actually routes — an unroutable board is not tighter, it
   is just smaller and wrong.

   **How this was actually caught matters.** Every one of these placement
   defects — uniform rows, a stray column off the left edge, confetti
   passives, J3 stranded off the edge — passed *every* automated check
   the project has: overlap, net pin-count, DRC and ERC all clean. Each
   was found only by rendering the board and looking at it. For layout
   work, rendering is a verification step, not a presentation step.
   `kicad-cli pcb export pdf` gives a truthful 2D view for this, free of
   any 3D-model quirks.
3. **Gap backfill.** Another one only visible by looking at the render
   (user: *"I think the stuff in the black box can be moved into the red
   boxes"*) — a handful of small SENSOR-block clusters and J1 had ended
   up stranded in their own tall column off the right edge, while real
   ~30×30mm holes sat empty next to J3 and above J5. Not a packing-order
   bug: the cluster packer is a 1D skyline (one current height per x),
   which can only ever build *upward* from a column — it is structurally
   blind to a 2D hole formed between two neighbouring columns that
   happen to settle at different heights, no matter what order items are
   packed in. Fixed with a post-pass (`backfill_gaps` in `build_pcb.py`):
   grid the packed area, repeatedly find the single largest empty
   rectangle (maximal-rectangle-in-histogram scan), and drop whichever
   still-misplaced cluster fits it best, until nothing more fits
   anywhere. 14 clusters relocated on the first real run, and the board
   shrank slightly as a side effect (175.1×114.1mm → 172.5×114.4mm) —
   never grows it, since every candidate is bounded to the
   already-packed area. Re-routed clean afterward (the whole point of
   this project's "regenerate via script" discipline: a placement fix
   like this is permanent, not a one-off hand-edit of the board file).

See "Architecture" and "Build order" below for what's built and why, and
"Known open items" at the end for what is deliberately not done yet.

## Architecture

Researched against real manufacturer datasheets (not assumed/remembered),
same rigor as Manifold's parts research:

- **MCU: NXP Qorivva MPC5606B** (Power Architecture, 144-LQFP). NXP's
  purpose-built *engine control* line (real production ECUs use this
  family), not a general-purpose automotive part: 1MB flash, 80KB RAM,
  6x CAN, up to 64 timer channels via its eMIOS engine (Enhanced Modular
  I/O Subsystem, two instances) — a hardware timing coprocessor well
  suited to injection/ignition pulse generation and crank/cam decode.
  *(Correction, found during step-4 follow-up research: earlier planning
  here assumed "eTPU2" — wrong. The real reference manual states this
  part "implements a scaled-down version of the eMIOS module"; eTPU2
  exists on other Qorivva siblings, e.g. MPC5674F, not this one. Doesn't
  change the MCU choice's rationale, just the peripheral's real name.)*
  LQFP package keeps the same QFP assembly technique already proven on
  Manifold's MCU, just more pins. Programmed via NXP S32 Design Studio
  (same IDE family as Manifold's S32K144, adds Power Architecture
  support).
  - *Alternative considered:* NXP S32K344 (Arm Cortex-M7 lockstep,
    ASIL-D-capable) — a body/gateway/BMS-class general-purpose part in a
    172-pin HDQFP. Worth revisiting only if functional-safety
    certification becomes a hard requirement.

- **Injector/ignition drivers: 2x NXP MC33810** ("Automotive Engine
  Control IC", 32-pin exposed-pad SOIC). Each chip is 4 low-side injector
  drivers + 4 ignition IGBT gate pre-drivers, SPI-configurable, with
  fault/overcurrent/overtemp feedback — the standard building block real
  open-source EFI projects (rusEFI) use for this exact job. Two chips
  populate the board for full 8-cylinder capability; 4- and 6-cylinder
  builds use a subset of channels via the harness/connector — same PCB,
  no separate board variant.

- **Programming — dual full-reflash paths:**
  - *Wired:* FTDI **FT4232HA** (AEC-Q100 Grade 2, 64-QFN) behind USB-C.
    Two of its four UART/MPSSE channels cover a bootloader UART for
    flashing plus JTAG (via MPSSE) for full on-chip debug.
  - *Wireless:* TI **CC2640R2F-Q1** (AEC-Q100 Grade 2 BLE SoC) as a
    co-processor — receives firmware over BLE and replays it into the
    MPC5606B over the same bootloader UART/JTAG lines the USB bridge
    uses, giving BLE genuine full-reflash capability, not just telemetry.
    Needs shared-bus arbitration with the USB path (only one drives the
    programming lines at a time) — to be finalized in schematic design.
  - Neither USB nor BLE exists natively on the Qorivva line, so both are
    external, automotive-qualified companion chips.

- **Sensors and actuators** (done):
  - **Timing:** 3x Maxim MAX9924 VR interfaces — crank + **two** cams,
    each on its own real hardware input-capture channel (a DOHC engine
    phasing intake and exhaust independently needs both cams measured;
    an input-capture channel avoids the ISR latency a software edge
    interrupt would fold into the timestamp).
  - **Analog in:** MAP and TPS (RC-filtered), IAT and CLT (NTC pull-up
    dividers), oil pressure, fuel pressure, and **battery voltage** —
    the last of these matters more than it looks, because real injector
    dead time varies strongly with supply voltage and can't be
    compensated without measuring it. **CLT is now a real, specific
    part** (not a generic placeholder): the DIYAutoTune GM Closed
    Element CLT/Oil Temperature Sensor — the same real resistive
    sending-unit family production engine ECUs actually use, and the
    same exact part/curve as the sibling
    [thermo-pcb](https://github.com/jessiepullaro414/Thermo) project's
    own engine-temperature sensor. Real published curve: -40°F=100.7kΩ,
    86°F=2.24kΩ, 210.2°F=177Ω. R25's 1.00kΩ pull-up is sized (like
    thermo-pcb's own R12) to center ADC resolution on the sensor's real
    86-210°F engine-operating range rather than the cold-start extreme.
    Fixing this also caught and fixed a real bug: both IAT's and CLT's
    pull-ups were wired to +5V, but this MCU has no separate ADC
    reference pins — the ADC domain genuinely runs at 3.3V, so a cold
    sensor (high resistance) could have pulled the pin close to 5V, over
    the 3.3V-domain rating. Both now pull up to +3V3. **IAT is now a
    real, specific part too**: DIYAutoTune's GM Open Element IAT
    Temperature Sensor. Real, honest discrepancy found and resolved
    while sourcing it (full reasoning inline in `build_schematic.py`
    above R24's registration, and in `ecu-firmware/inc/iat_sensor.h`):
    the IAT product page's own published third calibration point
    (146°F at 177Ω) conflicts with CLT's page (210.2°F at the identical
    177Ω) for what's evidently the same underlying GM-pattern thermistor
    element (identical resistance values at both lower anchor points,
    plus a leftover "closed-element" sentence found directly on the IAT
    page's own description text) — concluded to be copy-paste content
    contamination on DIYAutoTune's own site, not two genuinely different
    curves, so the firmware reuses CLT's own cross-checked curve for
    IAT's conversion. R24=4.22kΩ (E96) — deliberately different sizing
    from CLT's 1.00kΩ: IAT genuinely swings across nearly its whole real
    range in normal use (cold-soak to hot under-hood air), unlike
    thermostatically-regulated coolant, so it's sized via geometric-mean
    (sqrt(177Ω × 100.7kΩ) ≈ 4.22kΩ) for resolution across the full span
    rather than centered on one narrow sub-range.
  - **Knock:** 2x TI TLV2372-Q1 front ends (one per bank).
  - **Wideband O2:** 2x Bosch CJ125 + external heater MOSFET (one per
    bank), both on the shared SPI bus.
  - **Actuator outputs:** VVT cam-phaser solenoids x2, idle-air valve,
    fuel-pump relay, and a tach output — each a MOSFET low-side driver
    with a Schottky flyback clamp, the same pattern already proven on
    the main relay and O2 heater. Driving the phasers is what makes
    *measuring* two cams actually useful. Deliberate tradeoff: an
    integrated SPI multi-channel low-side driver would add real
    open-load/short diagnostics, but would have been a new unverified
    part — see "Known open items".
  - (Ignition IGBT power stage is done — see step 4 below.)
  - **Boost control:** a MOSFET-driven solenoid output (flyback-clamped,
    same low-side pattern as the other actuators) for a turbo wastegate
    or boost-control solenoid.
  - **EGT (exhaust gas temperature):** Analog Devices **AD8495**
    thermocouple amplifier (5mV/°C, real datasheet value). Its ~5V
    full-scale output would overrange the MCU's 3.3V ADC domain, so a
    10k/20k divider (0.667 ratio, undone in firmware) brings it in range
    — a real catch, not a rounding choice.
  - **Flex-fuel sensor input:** a frequency-capture input (ethanol % =
    frequency, fuel temp = duty cycle) on its own eTPU2/eMIOS channel.
    The GM-style sensor is open-collector and only ever pulls the line
    low, so the pull-up is referenced to the board's own +3V3 (not the
    sensor's 12V supply) — the same precedent already used for the
    MAX9924 COUT pull-ups.
  - **Electronic throttle control (ETC):** NXP **MC33926** H-bridge
    drives the throttle-body motor, with **redundant** sensing on both
    ends — APP1/APP2 on the pedal, TPS1/TPS2 on the throttle body — so
    firmware can cross-check for implausible readings. The H-bridge's
    two disable pins are independent of the PWM control path and,
    per its datasheet, **asymmetric**: D1 is active-HIGH, D2 is
    active-LOW — an easy real mistake, called out here because it is
    one. This is the biggest safety-critical subsystem on the board;
    see "Known open items" for what stays firmware's job.

- **Resolved cross-cutting item:** the MPC5606B's real eMIOS/DSPI pin-mux
  table is now known and wired (16 real eMIOS channels for the 8
  injector + 8 ignition real-time firing lines, real DSPI_0 for the SPI
  bus, 4 arbitrary reserved-pool GPIOs for CS/OUTEN/relay control) — see
  step 4 below. Step 5's ADC-based sensors will still need their own
  real pin research (ADC channel-to-pin mapping wasn't part of this
  pass), but the SPI/real-time-control gap that was blocking steps 3/4
  is closed.

- **Power:** 12V automotive input, reverse-polarity + load-dump
  protection, buck-derived 5V/3.3V logic rails, a separate switched
  high-current path for injector/ignition drive current. CAN
  transceiver(s) TBD.

- **Connectors:** given the much higher pin count than Manifold (8
  injectors + 8 ignition + crank + cam + analog sensors + O2 + knock +
  CAN + power/ground + relays, likely 60-100+ signals), plan is multiple
  automotive connectors split by function (engine harness vs. sensor
  harness), following real OEM ECU convention, rather than one giant
  connector. Specific part(s) TBD, verified against real vendor drawings.

## Build order

Each step gets its own real-datasheet verification pass and ERC/DRC loop,
the same way Manifold was built up subsystem by subsystem:

1. ~~Project scaffolding~~ (done)
2. ~~Power input + protection + regulation subsystem~~ (done — one shared
   reverse-battery/load-dump protection stage sized for the whole board,
   split into an always-on logic branch (5V buck + 3.3V LDO) and a
   relay-gated power-stage branch with separately-fused injector/ignition
   rails. See `build_schematic.py`'s own comments for the full real-part
   rationale — Vishay SQM40020EL_GE3, Littelfuse 5KP33A, TE/P&B
   T9AP5D52-equivalent relay. `kicad-cli sch erc` reports 5 violations, all
   documented, expected exceptions — see the schematic's own NOTES text.)
3. ~~MPC5606B core~~ (done — real, cross-verified 144-LQFP pin table: NXP's
   own datasheet/reference-manual URLs 404 live, same access problem
   Manifold hit with the S32K144, so pin data was recovered via a chipdip.ru
   mirror of the datasheet plus a Wayback Machine snapshot of the reference
   manual, cross-checked against the real rendered pinout diagram. Power
   domains (VDD_HV/VSS_HV, internally-regulated VDD_LV/VSS_LV off VDD_BV,
   dual ADC analog supplies), 8MHz crystal on EXTAL/XTAL, JTAG header
   (TDI/TDO/TCK/TMS — this part has no TRST/JCOMP pin), and the real Boot
   Assist Module config (FAB=PA9/ABS=PA8) + LINFlex serial-boot pins
   (LIN0TX=PB2/LIN0RX=PB3) are wired, with the latter two pairs broken out
   as labeled stubs for step 7 to complete. The other 113 real GPIO pins
   are marked no_connect with their real pin numbers, unclaimed until
   steps 4-6 assign them. `kicad-cli sch erc` reports 16 violations, all
   documented, expected exceptions — see the schematic's own NOTES text.)
4. ~~2x MC33810 injector/ignition driver subsystem~~ (done — real,
   cross-verified 32-pin SOICW-EP pin table, same chipdip.ru-mirror
   research technique as step 3. Real architecture confirmed from the
   datasheet: OUT0-3 are the chip's own integrated injector switches, but
   GD0-3 are only pre-drivers — 8 external ON Semi FGP3040G2 automotive
   ignition IGBTs (Q3-Q10) were added this step since real ignition
   current never flows through the MC33810 itself. RSP/RSN is one shared
   current-sense comparator per chip (not per channel, confirmed via the
   datasheet's own "MAXI Trip Point During Overlapping Dwell" spec) —
   all 4 of a chip's IGBT emitters share one sense resistor to GND, giving
   autonomous overcurrent latch-off with no MCU involvement required.
   SCLK/SI/SO are shared across both chips (separate CS per chip) —
   confirmed safe from the datasheet's own text on SO's tri-state behavior
   BEFORE wiring, the same class of check that had caught a real
   `pin_to_pin` bug on the JTAG TDO pin in step 3. DIN0-3/GIN0-3 (the real
   real-time per-cylinder firing inputs) and the SPI bus were initially
   left as labeled stubs, then **wired to real MPC5606B pins in a same-day
   follow-up pass**: 16 real eMIOS channels for the firing lines, real
   DSPI_0 for the SPI bus, 4 arbitrary reserved-pool GPIOs for CS/OUTEN/
   relay control — see `build_schematic.py`'s own notes for the full pin
   list and the eTPU2→eMIOS correction this research surfaced. `kicad-cli
   sch erc` dropped from 65 to 25 violations as a result, all documented,
   expected exceptions — see the schematic's own NOTES text.)
   **Update: MC33810 replaced by ST L9779WD-SPI (real part
   obsolescence).** MC33810 hit Last Time Buy status (DigiKey: last order
   date 2027-04-30, ~205 units left, no NXP-recommended replacement — see
   `ecu-firmware`'s `mc33810-end-of-life` project memory). No true pin/
   footprint-compatible drop-in exists; L9779WD-SPI (HiQUAD-64, confirmed
   Active, 500 units in stock) was chosen with real precedent — rusEFI
   maintains a real KiCad symbol/footprint for this exact part
   (`footprints/rusefi.pretty/L9779WD-SPI.kicad_mod`, sourced and
   cross-checked pad-for-pad against ST DocID027721 Rev 2's own Table 58
   mechanical data — real 0.65mm pitch matches exactly). U5/U6 now
   instantiate this part with the same real net names (`SPI_CS_{chip}`,
   shared `SPI_SCLK`/`SI`/`SO`, `INJ{n}_CTRL`, `IGN{n}_CTRL`,
   `INJ{n}_LO`) wherever a real equivalent exists. **Real, deliberate
   change:** `VB` is fed from `VIN_PROT` (always-on protected battery),
   not `VBATT_SW` (relay-switched) — matching how the TJA1043T CAN
   transceivers already use `VIN_PROT` for wake capability, since
   L9779WD-SPI needs to be alive to generate its own internal logic
   rails. **Real, open gaps, not silently resolved:** MC33810's RSP/RSN
   (current-sense) and FBx (coil/collector-sense) roles have no confirmed
   L9779WD-SPI equivalent, so the per-chip current-sense resistor was
   removed and each IGBT's emitter now ties straight to GND instead;
   MC33810's real `DRV_OUTEN` shared kill-switch pin also has no
   confirmed equivalent (kept as a real, physically-wired-but-unused MCU
   pin — see `ecu-firmware/inc/ecu_pins.h`'s `PIN_DRV_OUTEN` comment);
   and the chip's real charge-pump (`CP`) and external-MOS-gate-supply
   (`VDD_G`) pins were left unconnected pending the real application
   circuit. **RESOLVED (a later pass)** — Figure 3 and Table 13 were
   genuinely read, and the gap turned out to be worse than the original
   TODO wording suggested: `VDD5` is not an optional convenience output,
   it is IGN1-4's own supply (Table 28 specifies their supply voltage
   range as `VDD5` 4.9-5.1 V and their short-to-battery detection
   thresholds relative to it), and `VDD5` cannot exist at all without an
   external NMOS pass transistor — the feature list says "5 V precision
   voltage regulator (±2%) with external NMOS" and Table 13 lists that
   NMOS plus a 100 nF `CP` capacitor as *required* external components.
   So the board as previously drawn would have had **no ignition drive
   whatsoever**, not merely degraded drive. Now fitted for real, per
   chip: `Q20`/`Q21` (external pass NMOS, D→VB, G←`VDD_G`, S→`VDD5`),
   `C82`/`C83` (100 nF `CP`-to-VB charge-pump cap — a bootstrap/flying
   cap between `CP` and VB, *not* to ground), `C84`/`C85` (10 µF
   `VDD5`), `C86`/`C87` (1 µF `V3V3`). One real, honest sub-gap remains:
   ST names `STD20NF06L` as its own "testing reference" NMOS and that's
   what's used, but its AEC-Q101 status could not be confirmed — every
   route to a real datasheet (st.com, onsemi, Mouser mirror) timed out
   or returned a block page this session. Every other active part on
   this board has a confirmed qualification, so this is tracked in
   "Known open items", not quietly assumed. **Fitting these parts also
   surfaced a second real defect in the same sourced rusEFI footprint,**
   found only because the added components changed placement enough for
   the router to run a GND track under it: an `fp_poly` on `F.Mask`
   whose main body was *exactly* coincident with the `EPAD` pad's own
   mask aperture (same ±4.8514 mm coordinates — pure duplication, since
   the pad is already on `F.Mask` and generates that opening itself),
   plus two ~2 mm tabs extending past the pad into areas with no pad at
   all. Those tabs are a bare mask opening over whatever copper is
   routed underneath — a real solder-bridge hazard at reflow, which is
   exactly what `kicad-cli pcb drc` reported (`solder_mask_bridge`,
   error severity, against both the routed GND track and `EPAD`
   itself). Removed from the local footprint copy. This is the same
   structural defect class as the `F.Cu` polygon already removed from
   this footprint during the migration: KiCad's `.kicad_mod` format
   binds nets only to `pad` objects, so a graphical polygon overlapping
   netted copper can never pass DRC as authored. A real, genuine bug was found
   and fixed in the sourced footprint itself while verifying with
   `kicad-cli pcb drc`: a bare copper polygon on `F.Cu` with no way to
   carry a net (KiCad's `.kicad_mod` format only binds nets to `pad`
   objects) sat directly over the real EPAD exposed-pad pad, causing a
   real `shorting_items`/`solder_mask_bridge` violation — removed from
   the local footprint copy after confirming it was redundant with the
   pad's own real copper. **The board has been re-routed for real** with
   `route_board.py` (FreeRouting, same real 4-stage DSN/route/SES/zones
   pipeline used to route the board originally) — converged from 658
   unrouted connections to 0 in 12 passes (~6.5 minutes), GND/+3V3 zone
   pours refilled on all four inner layers. A real, clean `kicad-cli pcb
   drc` afterward: only 2 unconnected items (both on `U20`, unrelated to
   this change, a minor pre-existing GND-via/plane residue) and the same
   19 pre-existing `lib_footprint_mismatch` warnings already confirmed
   unrelated (`J3`/`Q3-Q10`/`F1-F4`/etc.) — **zero real violations caused
   by this redesign.** The board is DRC-clean again with the real MC33810
   replacement in place.
5. ~~Crank/cam + analog sensor front end~~ (done — 2x Maxim MAX9924
   AEC-Q100 VR-sensor interfaces for crank/cam (real 10-pin pinout, open-
   drain COUT level-shifted to +3V3 via pull-up), Bosch CJ125 wideband O2
   controller + external heater MOSFET (real 24-pin pinout + real
   application-circuit *topology*, though exact component values are
   flagged pending the full Bosch app note, not just the short "Product
   Information" brief this session could reach), standard-practice RC
   filtering for MAP/TPS, NTC pull-up dividers for IAT/CLT, and a real
   TI TLV2372-Q1 dual op-amp front end for the knock sensor (one channel
   gain stage, one channel buffered mid-supply reference). All ADC inputs
   and the 2nd MC33810-bus SPI chip-select land on real, freshly-verified
   MPC5606B pins. One genuine open item: a second independent eMIOS
   capture pin for cam position was offered by research but turned out to
   share an internal channel with an already-claimed injector pin - not
   wired rather than risk a silent conflict, needs one more targeted
   lookup. `kicad-cli sch erc` reports 40 violations, all documented,
   expected exceptions — see the schematic's own NOTES text.)
6. ~~CAN transceiver(s)~~ (done — 2x NXP TJA1043T (real 14-pin SOIC,
   AEC-Q100), 2 fully independent real FlexCAN pairs (FlexCAN_1:
   TX=pin28/RX=pin27, FlexCAN_4: TX=pin117/RX=pin116), cross-verified by
   both an independent research pass and a direct manual read of the
   rendered datasheet table landing on the same pins. VBAT ties to the
   always-on VIN_PROT rail (not the relay-gated VBATT_SW) so a real
   bus-wake event can revive the ECU with ignition off; VIO ties to +3V3
   so TXD/RXD interface at the MCU's own logic level with no level
   shifter. Split-termination network doubles as real bus termination,
   marked DNP-unless-bus-end-node. `kicad-cli sch erc` reports 41
   violations, all documented, expected exceptions. **Also this step: a
   real duplicate reference-designator bug** (two unrelated parts both
   named "R15", from a colliding ref-number formula) **was found and
   fixed** — this specific ERC pass hadn't flagged it, so a permanent
   duplicate-reference check was added to `build_schematic.py`'s own
   self-validation so it can't silently recur.)
7+8. ~~USB-C (FT4232HA) + BLE (CC2640R2F-Q1) programming paths, with
   real shared-bus arbitration~~ (done together, by design — they share
   the MCU's bootloader UART/boot-select lines. U13=FT4232HA (real
   18-of-64 pins verified — a deliberately simplified/partial symbol,
   unlike the MCU/MC33810's full treatment). U14=CC2640R2F-Q1 BLE SoC
   (real power/RF/oscillator/reset pins verified; UART/boot-control DIO
   pin choices are candidate/plausible, not independently verified
   against the full DIO crossbar — confirm before firmware bring-up).
   U15=TI SN3257-Q1, a real AEC-Q100 4-channel analog switch that maps
   exactly onto the 4 signals needing arbitration (UART TX, UART RX,
   BOOT_FAB, BOOT_ABS) — SEL is driven by a simple USB VBUS-presence
   divider, so the wired path automatically wins whenever USB-C is
   plugged in, no MCU firmware decision required. This wiring is what
   finally resolves step 3's original BOOT_FAB/BOOT_ABS/LIN0_TX/LIN0_RX
   stubs. Real, deliberate simplifications: CC2640R2F-Q1's optional
   32.768kHz crystal is omitted (internal RCOSC_LF substitutes); the BLE
   RF balun's real topology is wired but exact matching values are
   placeholder pending TI's official reference-design BOM; hardware-
   forced reset-into-bootloader is NOT implemented (neither bridge has a
   spare pin for it) — current mechanism is a firmware-cooperative
   software self-reset over the arbitrated UART, an honestly-flagged
   real limitation, not a hidden one. `kicad-cli sch erc` reports 39
   violations (down from 42 mid-step once 3 real footprint gaps were
   fixed), all documented, expected exceptions.)
9. ~~Connector selection + full net-to-pin mapping~~ (done — J4/J5 reuse
   the exact real TE AMPSEAL 776180-1 footprint already dimensionally
   verified in `manifold-pcb` (copied directly, our own signal
   assignment on real physical pin numbers, same pattern Manifold used
   for its own J1). J4 "engine harness" (25/35 pins): power, 8x
   injector + 8x ignition channels, crank/cam. J5 "sensor+CAN harness"
   (18/35 pins): MAP/TPS/IAT/CLT/KNOCK, 5x wideband-O2 sensor-cell pins
   + heater pair, both CAN buses. J6 = real Hirose U.FL connector for an
   external BLE antenna (a metal enclosure wouldn't let an on-board
   antenna radiate well). **This step resolved 23 of the previous
   session's violations** — every real external-interface net now
   reaches a physical connector pin. `kicad-cli sch erc` dropped from 39
   to **16 violations, zero new/unexpected findings** — the remaining 16
   are the exact same documented tool-limitation/deliberate-design
   exceptions established across every earlier step. **Steps 2-9 are now
   complete: the schematic is functionally done.**)
10. PCB layout, routing, DRC — **done except the BOM.** Current board:
    **165.8 × 104.8 mm, 6 layers, 144 footprints, 160 nets, fully routed
    (0 unrouted), `kicad-cli pcb drc` reports 0 errors** (only the 19
    benign `lib_footprint_mismatch` warnings). `build_pcb.py` buckets
    parts into 6 functional blocks by their real schematic Y-position,
    packs each block's large "anchor" parts with a skyline packer, then
    threads every small passive into the nearest real empty gap next to
    whichever anchor it actually shares a net with. 5 connectors (J1
    JTAG / J3 USB-C / J4+J5 AMPSEAL harness / J6 BLE antenna) are placed
    as deliberate edge anchors, with J3 rotated 180° so its receptacle
    faces out of the board for real from-outside access. Routing is
    FreeRouting via the same DSN/SES pipeline Manifold proved out, with
    GND poured on In1.Cu/In4.Cu and +3V3 on In2.Cu after routing (each
    zone in its own subprocess, per Manifold's own fix for a real pcbnew
    multi-zone-fill segfault). **Verification is always the real DRC
    engine, never FreeRouting's own "fully routed" self-report** — that
    distinction caught real violations more than once here. Several real
    bugs were found and fixed through this loop rather than by
    inspection; see `build_pcb.py`'s and `route_board.py`'s own comments
    for each. The historical detail below is kept for the reasoning
    trail:

    *(historical, from the first placement pass)* `build_pcb.py` places all 139
    real footprints (315 schematic symbols minus power-flag symbols) in
    6 functional blocks (power, MCU, injector/ignition, sensors, CAN,
    programming — bucketed by each part's real schematic Y-position, not
    hand-listed) arranged in a 2-column grid, with 5 connectors (J1
    JTAG/J3 USB-C/J4+J5 AMPSEAL harness/J6 BLE antenna) placed as
    deliberate edge anchors rather than folded into the generic pack.
    Board: 320x141mm, 6 layers. `kicad-cli pcb drc` on the unrouted board
    reports only the same benign `lib_footprint_mismatch` category
    Manifold's own project accepted (embedded-vs-library metadata
    differences, not a real defect) — zero real placement/clearance
    findings. **Two real bugs were found and fixed via this DRC loop**,
    not by eyeballing: (1) the board-outline offset math put the left/top
    edges flush against the leftmost/topmost real parts with zero margin
    (all of `BOARD_MARGIN` silently landed on the right/bottom only) —
    fixed by correctly subtracting the margin on the low side too; (2)
    the net pin-count check (the same real-vs-schematic verification
    Manifold's own script was built around) caught that K1's relay
    footprint (chosen back in step 2) was actually a 2-hole
    *mounting-only* footprint with zero electrical pads — not a PCB-
    solderable part at all — and that J3's USB-C footprint uses the real
    Amphenol A/B lettered pad scheme, not the simplified sequential
    numbering the schematic had used. Both fixed with real replacement
    parts/pin mappings (Schrack RT1-16A-FormC relay — real PCB-mountable
    part, though only 16A-rated against a ~22-28A realistic worst case,
    honestly flagged as a follow-up **and since genuinely closed** — see
    "Known open items", K1 is now a 40 A Panasonic CB1a-T-P-12V; real
    Amphenol 12401610E4-2A pin map). **Routing is done**: FreeRouting (same DSN/SES pipeline
    Manifold proved out, scaled to 158 real nets — 3.4x Manifold's 47)
    reached 0 unrouted on both attempts, in ~3 minutes each. Real
    `kicad-cli pcb drc` verification (not just trusting FreeRouting's own
    "fully routed" claim — Manifold's own memory explicitly warned this
    self-report isn't always accurate, and this session hit that exact
    scenario) confirms 0 unconnected items, and traced 4 small violations
    (copper-edge-clearance x3, hole-clearance x1) down to a SINGLE real
    trace (a `USB_CC1` segment on the In1.Cu ground-plane layer, routed
    too close to J3's real NPTH mechanical mounting hole) — not a
    systemic problem, genuinely one segment. Increasing J3/J6 spacing
    across two reroute attempts didn't fully clear it; needs either a
    manual trace nudge (completely normal post-autorouting cleanup in
    real PCB workflows, not unusual) or a real DSN-level keepout around
    that specific NPTH hole (not yet implemented) before this one spot is
    fab-ready. Zone pours succeeded cleanly: real GND on both In1.Cu and
    In4.Cu (this board's two ground planes) + real +3V3 on In2.Cu, each
    in its own subprocess per Manifold's own established fix for a real
    pcbnew multi-zone-fill segfault.

## Stackup

**8 layers**, up from 6 (and Manifold's 4):

| Layer | Role |
|---|---|
| F.Cu | signal |
| In1.Cu | GND plane |
| In2.Cu | signal |
| In3.Cu | GND plane |
| In4.Cu | +3V3 plane |
| In5.Cu | signal |
| In6.Cu | GND plane |
| B.Cu | signal |

Every signal layer is adjacent to a solid reference plane — this board
carries a USB 2.0 differential pair, a 2.4GHz BLE feed, two CAN buses
and several switching supplies, so that matters. A 5-signal-layer split
(two signal layers back to back) would route more easily still, but puts
adjacent signal layers with no plane between them; not worth the
crosstalk here.

**Why 8 and not 6:** this was a measured routing failure, not a
preference. On 6 layers only *three* layers were available for signals,
and that — not component area — turned out to be what set the minimum
board size. The components cover roughly 5,000 mm² of a ~21,000 mm²
board; the rest is the channel space 209 nets need. Every attempt to
shrink below ~120 mm²/part on 6 layers left the autorouter unable to
finish:

| mm²/part | 6-layer result |
|---|---|
| 76 | thrashed — 3,400+ CPU-s, never finished |
| 95 | thrashed, never finished |
| 106 | finished, 6 nets unrouted |
| 121 | clean |

8 layers adds a fourth signal layer (+33% routing capacity), with every
signal layer against a reference plane.

**An honest footnote on what actually unblocked it.** Moving to 8 layers
did *not*, by itself, fix the 106 mm²/part case — it still came back
with exactly 6 unrouted nets, the same number as on 6 layers. That
identical count was the clue: it was never a capacity problem. All six
were J3's *own* GND pads, strangled by an oversized keepout of mine
(1.4mm radius against pads sitting 1.08mm from the hole centres — pads
are "allowed" inside a keepout, so they still existed, but no track
could reach them). Sizing the keepout from the rule instead of by feel
(0.475mm drill radius + 0.25mm clearance = 0.725, so 0.75) fixed it,
and the board then routed fully in 5 minutes instead of 15. The extra
layers are still the right call for signal integrity on a board with a
USB 2.0 pair, a 2.4GHz feed and multiple switchers — but the lesson is
that a keepout larger than its rule requires protects nothing and
strangles its neighbours.

## Known open items

Honest list of what this board does **not** do yet. None of these are
hidden — each is either deliberately scoped out or flagged during the
step that surfaced it.

**Blocking fab:**
- **No distributor-verified BOM.** The last real step.
- ~~**K1 relay is undersized.**~~ **RESOLVED.** The Schrack
  RT1-16A-FormC was 16 A against a 30 A main fuse and a real ~22-28 A
  switched load. Replaced with **Panasonic CB1a-T-P-12V**: a real
  automotive PCB relay, 1 Form A (SPST-NO), **40 A @ 14 V DC** nominal
  switching capacity, 40 A continuous carry at 85 °C, 2 mΩ contacts,
  12 V / 117 mA coil, sealed, and — critically — the `-T` heat-resistant
  variant rated **−40 to +125 °C** rather than the standard type's
  +85 °C. 40 A now comfortably exceeds F1's 30 A, so the *fuse* is the
  weakest link in the switched path rather than the relay, which is the
  correct way round. Splitting into two relays was considered and
  rejected: it would not have helped, because the ignition branch alone
  (F4, 25 A fuse, ~14-20 A real draw) already exceeds a 16 A relay.
  No bundled KiCad footprint could be used — every `Relay_THT`
  footprint was checked programmatically, and the only ≥30 A one with
  real pads is an EV/solar part rated to just +85 °C. Worth recording:
  **both** Potter&Brumfield "12V30A" footprints self-describe as
  `Dummy for Space NO Pads`, so the trap that bit this project once
  (see the T9AP5D52 story below) applies to the SPST variant too, not
  only the SPDT one. The footprint is therefore hand-built by
  [`build_k1_footprint.py`](build_k1_footprint.py) from Panasonic's own
  datasheet PC-board-pattern drawing, with the full derivation in that
  script's docstring. Two knock-on effects were checked rather than
  assumed: the coil's real 117 mA (up from the Schrack's ~40 mA class)
  is comfortably within Q2's and D2's ratings, and the relay's real
  1 A minimum switching capacity is satisfied many times over.

**Remaining functional gaps:**
- **No per-channel diagnostics on the actuator outputs.** They are
  discrete MOSFET low-side drivers, chosen deliberately over an
  integrated SPI multi-channel driver (Infineon SPIDER/FLEX, ST
  L9301/L9305 class): those parts add real open-load/short detection,
  but each would have been a new unverified component, and this
  project's rule is that nothing ships unverified. Worth revisiting if
  fault reporting on these outputs becomes a requirement.
- **Qualification gaps: two of three now closed.**
  - ~~**MC33926** (ETC H-bridge)~~ — **RESOLVED, and it was never
    actually unqualified.** The revision originally read for this part
    was Rev. 10.0 (8/2014, Freescale-era), which genuinely contains no
    AEC statement anywhere, so the original flag was honest given the
    document in hand. NXP's own revision history settles it: Rev. 13
    (8/2018) records *"Added AEC-Q100 grade 1 qualified to Section 1 and
    Section 3"*, and Rev. 13+ state it twice — in the general
    description and as a features bullet. The MC33926 **is** AEC-Q100
    Grade 1 (−40 to +125 °C). Real lesson worth keeping for the rest of
    this BOM: a missing qualification statement can mean "not qualified"
    *or* "qualified in a later revision than the one you happen to be
    reading" — check the revision date and revision history before
    recording an absence.
  - ~~**`Q20`/`Q21`** (L9779WD-SPI `VDD5` pass NMOS)~~ — **RESOLVED.**
    ST makes a genuinely automotive-qualified version of the very part
    it names as its own Table 13 "testing reference":
    **STD20NF06LAG** — "automotive-grade N-channel 60 V, 32 mΩ typ.,
    24 A, STripFET II Power MOSFET in a DPAK package", AEC-Q101
    qualified. Same process, voltage, R<sub>DS(on)</sub>, current and
    package as the non-AG part, and it keeps the "L" logic-level gate
    the charge-pump drive depends on. That matters because of ST's own
    substitution warning — a replacement needs matching V<sub>th</sub>
    *and* C<sub>iss</sub>, since the FET sits inside the regulator's
    control loop — and using the automotive grade of the exact device ST
    tested with is the closest possible match rather than a
    same-ratings lookalike. **This also caught a real package bug:**
    the part was previously placed on the D2PAK (`TO-263`) footprint,
    but the whole STD20NF06L family is **DPAK** (`TO-252`). Now on a new
    `MOSFET_N_DPAK` symbol. *Honest sourcing note:* st.com timed out on
    every attempt this session, so AEC-Q101 status and package are
    corroborated across three independent sources (Newark parametrics,
    ST's own product-page title via search index, alldatasheet's marking
    database) rather than ST's own PDF — worth a direct check before fab,
    but a genuinely different evidence level from "unconfirmed".
  - **AD8495** (EGT amp) — **still open, and not closeable by a part
    swap.** No AEC-Q100-qualified dedicated thermocouple amplifier IC
    exists at all. This was established exhaustively by the sibling
    [thermo-pcb](https://github.com/jessiepullaro414/Thermo) project
    (which checked MAX31855, MAX31856, AD8495, LTC2983 and MCP9600 —
    including a full primary-source datasheet read of the MCP9600) and
    independently re-confirmed here. The only genuinely compliant path
    is architectural, not a substitution: an AEC-Q100 ADC reading the
    raw thermocouple millivolts plus a local sensor for cold-junction
    reference, with NIST ITS-90 linearisation and CJC done in firmware.
    thermo-pcb took exactly that route with the **TI ADS1118-Q1**
    (AEC-Q100 Grade 1, 16-bit SPI ADC, PGA, internal temp sensor TI's
    own literature documents for thermocouple cold-junction
    compensation), and its VSSOP-10 footprint is already hand-built and
    verified there, so it is reusable. Adopting it here is a real
    decision with real scope (new SPI device, plus K-type ITS-90
    polynomial firmware this board's EGT channel does not currently
    need) — deliberately not taken unilaterally.
- **`Q20`/`Q21` are linear pass elements and dissipate real power.**
  `VDD5` is a linear regulator, so each NMOS burns (V<sub>B</sub> − 5 V)
  × I<sub>load</sub> continuously — roughly 9.4 V × ~100 mA ≈ 1 W per
  chip at a nominal 14.4 V, ~2 W across both. D2PAK was chosen over
  this board's usual SOT-23 signal MOSFETs for exactly this reason, but
  both tabs need real copper pour at layout. The datasheet also gives a
  real placement constraint for the VB-side reservoir caps (`C19`/`C21`):
  "The Cin capacitor on VB line should be put as close as possible to
  the drain of external MOS."
- **ETC plausibility checking is a firmware responsibility.** The board
  provides two independent sensors on the pedal (APP1/APP2) and two on
  the throttle body (TPS1/TPS2), plus two independent hardware disable
  inputs on the H-bridge — but comparing each pair and deciding when to
  disable is firmware's job. This board cannot and does not implement
  ISO 26262-style throttle safety in hardware alone.
- Harness headroom after this expansion: **J4 has 2 spare pins, J5 has 0.**

**Cylinder count:** the board is 8-cylinder-capable (2x MC33810 = 8
injector + 8 ignition channels) and covers 4/6/8 from one PCB. **10 and
12 cylinders are deliberately not supported** — a considered decision,
not an oversight. The MCU is nowhere near the limit: the real datasheet
shows 64 eMIOS unified channels (32 per instance) of which only 16 are
used, and 73 MCU pins are free. The actual costs are a 3rd MC33810 plus
4 more TO-220 IGBTs — real board area on a board that was deliberately
compacted — and 8 harness pins, which is now *exactly* all of J4's
remaining spare capacity, for an engine class that is a small fraction
of the market. V10 in particular has notably thin aftermarket support;
V12 is mostly served by upper-tier ECUs (Motec M1, Emtron KV12, Haltech
Elite 2500, which covers 1-12 cylinders). Because the whole design is
script-generated, a 12-cylinder variant stays cheap to produce later if
it is ever wanted, without burdening the standard board.

**Smaller, previously-flagged items:**
- CC2640R2F-Q1's UART/boot-control DIO pin choices are plausible but
  not verified against the full DIO crossbar — confirm before firmware
  bring-up. Its BLE balun matching values are placeholder pending TI's
  reference-design BOM.
- Hardware-forced reset-into-bootloader is not implemented (neither
  bridge has a spare pin); the current mechanism is a
  firmware-cooperative software self-reset, which cannot recover a hung
  MCU.
- Q1 (D2PAK MOSFET) and the FGP3040G2 IGBTs' pin-to-terminal mappings
  have not had the same independent datasheet re-verification the
  MCU/MC33810/CJ125 pin tables received.
- Two components in the CJ125 network (the UN pin's series resistor and
  its filter cap) are genuinely **unlabeled in Bosch's own application
  circuit** — real topology, chosen values, flagged rather than
  presented as verified.

## Opening it

Open `ECU.kicad_pro` in KiCad 8, 9, or 10. Everything the schematic needs
is embedded in `ECU.kicad_sch` itself; `sym-lib-table` additionally
registers a real external `ECU.kicad_sym` (both regenerated by
`build_schematic.py` — don't hand-edit either), same pattern as Manifold,
needed for KiCad's own symbol browser and for `kicad-cli sch erc` to not
flag every part as "library not included in configuration".

## Verifying

```bash
python build_schematic.py
kicad-cli sch erc ECU.kicad_sch
python run_drc.py                    # once a .kicad_pcb exists
```

Add `python route_board.py` between the two to re-route after any
schematic change. Current expected results:

- `kicad-cli sch erc` → **15 violations**, all documented, expected
  exceptions (see the NOTES text block inside the generated schematic
  itself for the per-item justification).
- `python run_drc.py` on the routed board → 0 unconnected items and 19
  benign `lib_footprint_mismatch` warnings.

One caveat learned the hard way here: **an "expected exception" is a
claim that needs evidence, and needs re-justifying whenever the circuit
around it changes.** One entry on that ERC list was briefly a genuine
short between two driven outputs that had been written off as a tool
limitation; re-checking it against the real datasheet figure removed
the violation instead of excusing it.

Before finalizing any part choice: pull the real manufacturer datasheet
(not a distributor summary page) and confirm pin count, package, and
electrical specs directly. This caught multiple real errors on Manifold
(wrong LQFP package, wrong exposed-pad pin count, undersized charge-pump
cap, marginal MOSFET current rating) and several here too — the K1 relay
that turned out to be a mounting-only footprint with no electrical pads,
J3's real A/B lettered USB-C pad scheme, and a CJ125 network whose real
topology only became clear when Bosch's own application-circuit figure
was rendered at high magnification and read directly. Treat it as
mandatory, given this board will control a running engine.
