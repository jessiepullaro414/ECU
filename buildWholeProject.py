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
  - Firmware: compiles every .c/.S file in ecu-firmware/src/ with the
    real, confirmed-necessary `-mvle` flag (e200z0h is VLE-only) and
    then LINKS them into ecu-firmware/build/ecu.elf against the real
    MPC5606B memory map in link/mpc5606b.ld, using this project's own
    reset entry in src/startup.S. It finishes by checking the linked
    image actually carries a valid boot header, because on this part
    that is the difference between a chip that runs and one that
    silently parks itself in static mode.
    This used to be a compile-only check: for most of this project's
    life no local PowerPC-EABI/VLE toolchain existed, and there was no
    linker script or reset-vector startup code. Both gaps are closed;
    the toolchain is found automatically (see find_real_toolchain) or
    via ECU_FW_TOOLCHAIN_PREFIX. If none is found the script says so
    plainly rather than fabricating a pass.

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
FW_BUILD = os.path.join(FW_DIR, "build")
LINKER_SCRIPT = os.path.join(FW_DIR, "link", "mpc5606b.ld")

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
    os.makedirs(FW_BUILD, exist_ok=True)
    all_ok = True
    objects = []
    for f in src_files:
        src_path = os.path.join(FW_SRC, f)
        # Object names KEEP THE SOURCE EXTENSION - "intc.c" -> "intc.c.o",
        # never "intc.o". This project really does contain both intc.c and
        # intc.S, and the usual <stem>.o convention makes the second one
        # silently overwrite the first. The link then fails with every
        # symbol from intc.c undefined, which is exactly how this was found
        # on the first real link attempt.
        obj = os.path.join(FW_BUILD, f + ".o")
        ok = run_step(f"Compile {f}", [gcc] + REAL_CFLAGS + [src_path, "-o", obj], FW_SRC)
        all_ok = all_ok and ok
        if ok:
            objects.append(obj)

    if not all_ok:
        print("\nCompile failed - not attempting the link.")
        return False

    # Real link against the MPC5606B memory map.
    #
    # ld is invoked DIRECTLY rather than through the gcc driver. The
    # driver route (gcc -nostdlib -T ... -lgcc) fails on this toolchain
    # with "collect2: ld returned 123 exit status" and produces no
    # diagnostic at all, even under -Wl,--verbose, while the identical
    # link driven straight through ld succeeds and yields a correct
    # image. Not worth chasing a silent driver failure when the tool
    # underneath it works.
    #
    # libgcc is still required despite -nostdlib: injection.c's 64-bit
    # division pulls in compiler helper routines such as __udivdi3. Its
    # path is asked of the compiler rather than guessed.
    ld = gcc.replace("gcc.exe", "ld.exe")
    libgcc = subprocess.run([gcc, "-mvle", "-mcpu=e200z0",
                             "-print-libgcc-file-name"],
                            capture_output=True, text=True).stdout.strip()
    elf = os.path.join(FW_BUILD, "ecu.elf")
    link_cmd = [ld, "-T", LINKER_SCRIPT,
                "-Map=" + os.path.join(FW_BUILD, "ecu.map"),
                "-o", elf] + objects
    if libgcc and os.path.isfile(libgcc):
        link_cmd.append(libgcc)
    if not run_step("Link ecu.elf", link_cmd, FW_SRC):
        return False

    print("\n=== Image checks ===")
    size_bin = gcc.replace("gcc.exe", "size.exe")
    if os.path.isfile(size_bin):
        subprocess.run([size_bin, elf])

    # A linked ELF only means something if the part will actually boot it,
    # and on this MCU that hinges on one 32-bit word. The SSCM scans each
    # boot sector for BOOT_ID = 0x5A and, finding none, hands over to the
    # BAM and parks the core in static mode - the chip simply never runs,
    # with no other symptom. So verify the RCHW landed rather than assume.
    objdump = gcc.replace("gcc.exe", "objdump.exe")
    if os.path.isfile(objdump):
        out = subprocess.run([objdump, "-s", "-j", ".bootsector", elf],
                             capture_output=True, text=True).stdout
        packed = "".join(out.split()).lower()
        if "005a0000" in packed:
            print("Boot header OK: RCHW BOOT_ID=0x5A present at flash base")
        else:
            print("WARNING: RCHW 0x005A0000 not found in .bootsector -")
            print("         the SSCM would find no valid boot sector.")
            print(out)
            return False
    return True


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
