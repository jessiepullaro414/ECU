"""
One-off helper (2026-08-15): renders the current ECU.kicad_pcb, transparent
background composited onto solid white, matching the pattern already used
by thermo-pcb/render_white.py and the site's existing ECU renders.
"""
import subprocess
from PIL import Image

KICAD_CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"
PCB = "ECU.kicad_pcb"


def render(output, extra_args, webp=None):
    args = [KICAD_CLI, "pcb", "render", "--quality", "high", "--floor",
            "--width", "1920", "--height", "1440", "--background", "transparent",
            "-o", output, *extra_args, PCB]
    subprocess.run(args, check=True, capture_output=True)
    im = Image.open(output).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    bg.alpha_composite(im)
    flat = bg.convert("RGB")
    flat.save(output)
    print("wrote", output)
    # The webp companions are what the site actually serves. Generated
    # here rather than converted by hand, so they cannot drift out of
    # step with the PNGs the way they had - the pair committed before
    # this change still showed the pre-L9779-regulator board.
    if webp:
        flat.save(webp, "WEBP", quality=90, method=6)
        print("wrote", webp)


shots = {
    "ecu_render_angled.png": (["--perspective", "--rotate", "-35,0,-135", "--zoom", "0.85"],
                              "render-angled.webp"),
    "ecu_render_top.png": (["--side", "top", "--zoom", "0.85"],
                           "render-top.webp"),
}

for name, (args, webp) in shots.items():
    render(name, args, webp)
