#!/usr/bin/env python3
"""
buildWholeProject.py - real, top-level build orchestrator for the whole
ECU project (ecu-pcb hardware + ecu-firmware software), run from the
repo root.

Real, honest scope - read this before trusting a "BUILD OK" from this
script:
  - PCB: runs the real, existing, already-proven pipeline in ecu-pcb/ in
    the order it actually has to happen (schematic -> PCB placement ->
    autorouting -> DRC), the same real scripts and sequence documented
    in ecu-pcb/README.md. This part is real and complete - every one of
    these scripts is the actual tool that built the real, DRC-clean
    board this project already has.
  - Firmware: this project has NEVER had a local PowerPC-EABI/VLE
    toolchain available (a standing, honestly-documented gap throughout
    ecu-firmware's own README/file headers) and still doesn't have a
    real linker script or startup/crt0 file (intc.S provides the real
    IVOR4 exception-entry stub, but not full reset-vector startup code -
    see intc.S's own header). This script does NOT claim to produce a
    linked, flashable firmware image. What it DOES do, honestly: if a
    real VLE-capable PowerPC-EABI GCC is found on PATH or via the
    ECU_FW_TOOLCHAIN_PREFIX environment variable, it compiles
    (`-c`, syntax/type-check only, no link) every real .c/.S file in
    ecu-firmware/src/ against the real, confirmed-necessary `-mvle`
    flag (e200z0h is VLE-only, see ecu-firmware/inc/intc.h's own file
    header) and reports real per-file pass/fail. If no real toolchain
    is found, it says so plainly and skips straight to a summary -
    it does not fabricate a pass.

Usage:
    python buildWholeProject.py                 # both PCB and firmware
    python buildWholeProject.py --pcb-only
    python buildWholeProject.py --firmware-only
    python buildWholeProject.py --skip-route     # schematic+PCB+DRC only,
                                                  # skip the real (slow,
                                                  # ~minutes) FreeRouting
                                                  # autorouting pass
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PCB_DIR = os.path.join(HERE, "ecu-pcb")
FW_DIR = os.path.join(HERE, "ecu-firmware")
FW_INC = os.path.join(FW_DIR, "inc")
FW_SRC = os.path.join(FW_DIR, "src")

# Real, confirmed-necessary compile flag for this exact core (e200z0h is
# a VLE-only implementation - see ecu-firmware/inc/intc.h's own file
# header, and ecu-firmware/src/intc.S's real, cross-checked VLE mnemonic
# work). -mcpu=e200z0 is GCC's real, standard target name for this exact
# core in a powerpc-eabivle toolchain (e.g. NXP S32 Design Studio's
# bundled GCC).
# -std=gnu99 is not cosmetic: this toolchain is GCC 4.9.4, whose default
# is gnu90, and gnu90 rejects declarations inside a `for` initialiser -
# which this codebase uses throughout (`for (unsigned i = 0; ...)`). The
# code is valid C99; the compiler just had to be told which language it
# was reading. gnu99 rather than c99 keeps GCC's inline-asm extensions,
# which intc.c relies on for mtspr/mfspr.
REAL_CFLAGS = ["-mvle", "-mcpu=e200z0", "-std=gnu99", "-Wall", "-c", "-I", FW_INC]


def run_step(name, cmd, cwd):
    print(f"\n=== {name} ===")
    print("  $", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        print(f"FAILED: {name} (exit code {result.returncode})")
        return False
    return True


def build_pcb(skip_route):
    if not os.path.isdir(PCB_DIR):
        print(f"ecu-pcb/ not found at {PCB_DIR} - skipping PCB build.")
        return False

    ok = True
    ok = ok and run_step("Schematic generation", [sys.executable, "build_schematic.py"], PCB_DIR)
    ok = ok and run_step("PCB placement", [sys.executable, "build_pcb.py"], PCB_DIR)

    if not skip_route:
        # Real, slow step (FreeRouting - typically several minutes for
        # this board's real net count) - the same real tool that
        # actually routed this board originally, not a placeholder.
        ok = ok and run_step("Autorouting (FreeRouting)", [sys.executable, "route_board.py"], PCB_DIR)
    else:
        print("\n=== Autorouting: SKIPPED (--skip-route) ===")

    ok = ok and run_step("DRC verification", [sys.executable, "run_drc.py"], PCB_DIR)
    return ok


def find_real_toolchain():
    """Real toolchain discovery: checks ECU_FW_TOOLCHAIN_PREFIX first
    (an explicit, real override for e.g. an S32 Design Studio install
    not on PATH), then falls back to common real PowerPC-EABI/VLE GCC
    binary names that might already be on PATH. Returns the real gcc
    path, or None if genuinely not found - never guesses one into
    existence."""
    prefix = os.environ.get("ECU_FW_TOOLCHAIN_PREFIX")
    if prefix:
        candidate = prefix if os.path.isfile(prefix) else shutil.which(prefix)
        if candidate:
            return candidate
        print(f"ECU_FW_TOOLCHAIN_PREFIX={prefix!r} was set but not found/executable.")
        return None

    for name in ("powerpc-eabivle-gcc", "powerpc-eabivle-gcc.exe",
                 "powerpc-eabi-gcc", "powerpc-eabi-gcc.exe"):
        found = shutil.which(name)
        if found:
            return found

    # Nothing on PATH - look where S32 Design Studio for POWER
    # ARCHITECTURE actually installs its cross compiler. S32DS-PA does
    # not add itself to PATH, so without this the common case (a normal
    # default-path install) still reports "no toolchain".
    #
    # REAL TRAP THIS GUARDS AGAINST, found the hard way on this machine:
    # NXP ships TWO separate products both called "S32 Design Studio",
    # split by architecture. "S32 Design Studio for S32 Platform" (e.g.
    # C:\NXP\S32DS.3.6.1) is the ARM line for S32K/S32G - its bundled
    # GCCs are arm32-eabi/arm64-eabi ONLY and cannot build this MCU at
    # all. The MPC5606B is Power Architecture (Qorivva, VLE-only) and
    # needs "S32 Design Studio for Power Architecture" (S32DS-PA), which
    # ships powerpc-eabivle GCC under Cross_Tools. Having the ARM one
    # installed looks like success until you check the target triple, so
    # the globs below deliberately match only real PA install shapes.
    for pattern in (
            r"C:\NXP\*Power*\**\powerpc-eabivle-*\bin\powerpc-eabivle-gcc.exe",
            r"C:\NXP\*\Cross_Tools\powerpc-eabivle-*\bin\powerpc-eabivle-gcc.exe",
            r"C:\Freescale\*\Cross_Tools\powerpc-eabivle-*\bin\powerpc-eabivle-gcc.exe",
            r"C:\*S32DS*Power*\**\powerpc-eabivle-gcc.exe",
            # Standalone toolchain drop (no IDE) - S32DS-PA's compiler is
            # also distributed on its own as a plain powerpc-eabivle-N_N
            # tree, which is how this project's own machine has it.
            r"C:\NXP\powerpc-eabivle-*\bin\powerpc-eabivle-gcc.exe",
            r"C:\powerpc-eabivle-*\bin\powerpc-eabivle-gcc.exe",
    ):
        hits = sorted(glob.glob(pattern, recursive=True))
        if hits:
            return hits[-1]   # newest-sorting install wins
    return None


def build_firmware():
    if not os.path.isdir(FW_SRC):
        print(f"ecu-firmware/src/ not found at {FW_SRC} - skipping firmware build.")
        return False

    gcc = find_real_toolchain()
    if gcc is None:
        print(
            "\n=== Firmware compile: SKIPPED ===\n"
            "No real PowerPC-EABI/VLE toolchain found - not on PATH, and not\n"
            "at any known S32 Design Studio for Power Architecture install\n"
            "path.\n\n"
            "WATCH OUT: NXP ships TWO products called 'S32 Design Studio',\n"
            "split by architecture, and only one can build this MCU.\n"
            "  * 'S32 Design Studio for S32 Platform' (e.g. C:\\NXP\\S32DS.3.6.1)\n"
            "    is the ARM line for S32K/S32G. Its bundled GCCs are\n"
            "    arm32-eabi / arm64-eabi ONLY - no PowerPC target at all, so\n"
            "    having it installed does NOT help here.\n"
            "  * 'S32 Design Studio for Power Architecture' (S32DS-PA) is the\n"
            "    one this board needs: it lists MPC5606B as a supported device\n"
            "    and ships powerpc-eabivle GCC under Cross_Tools.\n\n"
            "Install S32DS-PA (free), or point ECU_FW_TOOLCHAIN_PREFIX at any\n"
            "real powerpc-eabivle gcc executable, then re-run."
        )
        return False

    print(f"\nReal toolchain found: {gcc}")
    src_files = sorted(
        f for f in os.listdir(FW_SRC) if f.endswith((".c", ".S"))
    )
    all_ok = True
    for f in src_files:
        src_path = os.path.join(FW_SRC, f)
        ok = run_step(f"Compile {f}", [gcc] + REAL_CFLAGS + [src_path, "-o", os.devnull], FW_SRC)
        all_ok = all_ok and ok

    print(
        "\nNOTE: this compiles each file (-c, no link) as a real syntax/\n"
        "type check only. This project has no real linker script or full\n"
        "crt0 startup file yet (see ecu-firmware/inc/intc.h and intc.S's\n"
        "own file headers for the real, honestly-documented remaining\n"
        "gap) - a linked, flashable image is NOT produced by this script."
    )
    return all_ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcb-only", action="store_true")
    parser.add_argument("--firmware-only", action="store_true")
    parser.add_argument("--skip-route", action="store_true",
                         help="Skip the real (slow) FreeRouting autorouting pass")
    args = parser.parse_args()

    do_pcb = not args.firmware_only
    do_fw = not args.pcb_only

    results = {}
    if do_pcb:
        results["PCB"] = build_pcb(args.skip_route)
    if do_fw:
        results["Firmware"] = build_firmware()

    print("\n" + "=" * 40)
    print("BUILD SUMMARY")
    print("=" * 40)
    overall_ok = True
    for name, ok in results.items():
        status = "OK" if ok else "FAILED / SKIPPED"
        print(f"  {name}: {status}")
        overall_ok = overall_ok and ok

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
