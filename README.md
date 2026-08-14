# ECU

This is the repo for the ECU PCB and firmware — an NXP MPC5606B-based engine
control unit, built with the same real, register/datasheet-verified
discipline throughout (see each subproject's own README for the full,
honest ledger of what's confirmed vs. still open).

- [`ecu-firmware/`](ecu-firmware/README.md) — MPC5606B (e200z0h core)
  register-accurate C/assembly firmware: clocks, SIUL2, DSPI, ADC, eMIOS
  injection/ignition timing, FlexCAN, INTC, SWT watchdog, and drivers for
  the L9779WD-SPI injector/ignition ICs and CJ125 wideband O2 front end.
- [`ecu-pcb/`](ecu-pcb/README.md) — 8-layer, 172.5×114.4mm KiCad board
  (224 parts / 236 nets), fully routed and DRC-clean, built with a
  script-driven generate → route → verify pipeline.

Run `python buildWholeProject.py` from this directory to build both:
schematic → PCB placement → autorouting → DRC for the board, and a
real per-file compile check for the firmware if a PowerPC-EABI/VLE
toolchain is available (see the script's own header for exactly what it
does and does not claim to produce).
