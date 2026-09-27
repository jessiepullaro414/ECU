#!/usr/bin/env python3
"""Builds and runs the host tests in test/.

WHY THESE EXIST. Nothing in this firmware can be run on the target -
there is no board yet - so the only way to execute any of it is to
compile the portable modules natively and drive them with a stub for
the hardware. That loop has caught, in order: a uint64_t air-mass
overflow that would have produced plausible-looking wrong pulse widths;
a cylinder off-by-one that wrote an eMIOS register offset into the
FLASH BOOT SECTOR; a crash inside a safety check's own error path; a
scheduling window one tooth too narrow that silently dropped two of
eight cylinders; and a comparison written backwards that only worked
because one arming angle happened to land on a real tooth.

Every one of those is invisible to review and invisible to a compiler.

WHY THEY LIVE IN THE REPO. They used to be written to a scratch
directory, which is wiped between sessions - so each of those bugs was
found by a test that no longer existed the next time anyone looked.
A regression that has to be re-derived is not a regression test.

Requires a native compiler. On Windows this is normally WSL's gcc,
which is detected automatically; set CC to override.

Run:  python tools/run_host_tests.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST = os.path.join(HERE, "test")
INC = os.path.join(HERE, "inc")
SRC = os.path.join(HERE, "src")

# Each test names the firmware sources it links against. Kept explicit
# rather than globbed: a test that quietly starts depending on another
# module is a test whose failure no longer localises anything.
TESTS = {
    "test_sensors.c":      ["sensor.c"],
    "test_tables.c":       ["fuel.c", "ignition.c", "table.c", "sensor.c"],
    "test_plausibility.c": ["plausibility.c", "sensor.c"],
    "test_scheduling.c":   ["injection.c", "fuel.c", "ignition.c", "table.c"],
}

CFLAGS = ["-std=gnu99", "-O1", "-Wall", "-Wextra"]


def wsl_path(p):
    return subprocess.run(["wsl", "wslpath", p.replace("\\", "/")],
                          capture_output=True, text=True,
                          check=True).stdout.strip()


def find_runner():
    """Returns (kind, cc). 'native' runs directly; 'wsl' shells through."""
    cc = os.environ.get("CC")
    if cc and shutil.which(cc):
        return "native", cc
    for c in ("gcc", "cc", "clang"):
        if shutil.which(c):
            return "native", c
    if shutil.which("wsl"):
        r = subprocess.run(["wsl", "-e", "bash", "-lc", "command -v gcc"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return "wsl", "gcc"
    return None, None


def main():
    kind, cc = find_runner()
    if kind is None:
        print("No native C compiler found (tried CC, gcc, cc, clang, and "
              "WSL's gcc).\nThe host tests need one - the cross-compiler "
              "cannot produce something this machine can run.", file=sys.stderr)
        return 2
    print(f"host tests via {kind} {cc}\n")

    out = os.path.join(HERE, "build", "host")
    os.makedirs(out, exist_ok=True)

    failed = []
    for test, deps in sorted(TESTS.items()):
        name = test[:-2]
        srcs = [os.path.join(TEST, test)] + [os.path.join(SRC, d) for d in deps]
        exe = os.path.join(out, name)

        if kind == "wsl":
            args = (CFLAGS + ["-I", wsl_path(INC)]
                    + [wsl_path(s) for s in srcs] + ["-o", wsl_path(exe)])
            build = subprocess.run(["wsl", "-e", "bash", "-lc",
                                    "gcc " + " ".join(args)],
                                   capture_output=True, text=True)
        else:
            build = subprocess.run([cc] + CFLAGS + ["-I", INC] + srcs
                                   + ["-o", exe],
                                   capture_output=True, text=True)
        if build.returncode != 0:
            print(f"  {name}: BUILD FAILED")
            print("    " + build.stderr.strip().replace("\n", "\n    "))
            failed.append(name)
            continue

        if kind == "wsl":
            run = subprocess.run(["wsl", "-e", wsl_path(exe)],
                                 capture_output=True, text=True)
        else:
            run = subprocess.run([exe], capture_output=True, text=True)
        for line in run.stdout.strip().splitlines():
            print("  " + line)
        if run.returncode != 0:
            failed.append(name)

    print()
    if failed:
        print(f"HOST TESTS FAILED: {', '.join(failed)}")
        return 1
    print(f"All {len(TESTS)} host test binaries passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
