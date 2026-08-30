"""Generate the app icons.  Run from the repository root: python3 web/make_icons.py"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "icons"
GROUND = (233, 237, 236, 255)
GLASS = (250, 247, 243, 255)
PLASMA = (253, 236, 235, 255)
BLOOD = (179, 48, 44, 255)
DEEP = (92, 18, 19, 255)
RIM = (120, 130, 128, 255)
INK = (14, 21, 24, 255)


def draw_icon(size: int, inset: float) -> Image.Image:
    """A tube standing in its rack, half settled -- the app in one mark."""
    scale = 4
    s = size * scale
    image = Image.new("RGBA", (s, s), GROUND)
    draw = ImageDraw.Draw(image)

    pad = s * inset
    tube_w = s * 0.30
    x0 = (s - tube_w) / 2
    x1 = x0 + tube_w
    y0 = pad
    y1 = s - pad
    radius = tube_w / 2

    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=GLASS)

    # the settled column: packed at the bottom, plasma above
    boundary = y0 + (y1 - y0) * 0.42
    draw.rounded_rectangle([x0, y0, x1, boundary], radius=radius, fill=PLASMA)
    draw.rounded_rectangle([x0, boundary, x1, y1], radius=radius, fill=BLOOD)
    draw.rectangle([x0, boundary, x1, boundary + (y1 - boundary) * 0.35], fill=BLOOD)
    draw.rounded_rectangle(
        [x0, y1 - (y1 - y0) * 0.22, x1, y1], radius=radius, fill=DEEP
    )

    # the reading line
    line_w = max(int(s * 0.016), 2)
    draw.line([x0 - tube_w * 0.28, boundary, x1 + tube_w * 0.28, boundary],
              fill=INK, width=line_w)

    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=RIM,
                           width=max(int(s * 0.012), 2))

    # graduations on the left, the way a pipette is marked
    for i in range(1, 9):
        y = y0 + (y1 - y0) * i / 9
        length = tube_w * (0.34 if i % 3 else 0.52)
        draw.line([x0 - tube_w * 0.20 - length, y, x0 - tube_w * 0.20, y],
                  fill=RIM, width=max(int(s * 0.010), 2))

    return image.resize((size, size), Image.LANCZOS)


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" fill="#e9edec"/>
  <g>
    <rect x="179" y="56" width="154" height="400" rx="77" fill="#faf7f3"/>
    <path d="M179 224 h154 v155 a77 77 0 0 1 -77 77 a77 77 0 0 1 -77 -77 Z" fill="#b3302c"/>
    <path d="M179 380 h154 a77 77 0 0 1 -77 76 a77 77 0 0 1 -77 -76 Z" fill="#5c1213"/>
    <path d="M179 56 h154 v168 h-154 Z" fill="#fdeceb"/>
    <rect x="136" y="216" width="240" height="9" fill="#0e1518"/>
    <rect x="179" y="56" width="154" height="400" rx="77" fill="none" stroke="#788280" stroke-width="7"/>
    <g fill="#788280">
      <rect x="112" y="110" width="52" height="7"/><rect x="126" y="154" width="38" height="7"/>
      <rect x="126" y="198" width="38" height="7"/><rect x="112" y="242" width="52" height="7"/>
      <rect x="126" y="286" width="38" height="7"/><rect x="126" y="330" width="38" height="7"/>
      <rect x="112" y="374" width="52" height="7"/>
    </g>
  </g>
</svg>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw_icon(192, 0.10).save(OUT / "icon-192.png")
    draw_icon(512, 0.10).save(OUT / "icon-512.png")
    # maskable icons are cropped to a circle by the launcher, so keep clear of the edge
    draw_icon(512, 0.22).save(OUT / "icon-maskable-512.png")
    (OUT / "icon.svg").write_text(SVG)
    for path in sorted(OUT.iterdir()):
        print(f"wrote {path.relative_to(Path.cwd())} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
