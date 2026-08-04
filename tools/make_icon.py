"""Generate app.ico (multi-size video-player style icon) with Pillow.

Run:  python tools/make_icon.py  ->  app.ico
Redesign by editing the drawing code and re-running.
"""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024  # master canvas, downscaled for smooth edges
BG_TOP = (47, 111, 237)    # #2f6fed - app play button blue
BG_BOTTOM = (36, 88, 201)  # #2458c9 - pressed variant, subtle gradient
BORDER = (28, 70, 160)
WHITE = (255, 255, 255)

OUT = Path(__file__).resolve().parent.parent / "app.ico"
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=255)
    return mask


def _gradient(size: int) -> Image.Image:
    base = Image.new("RGB", (size, size))
    for y in range(size):
        t = y / (size - 1)
        base.putpixel_row = None  # no-op guard
    # per-row fill via ImageDraw line is simpler than putpixel loop
    draw = ImageDraw.Draw(base)
    for y in range(size):
        t = y / (size - 1)
        color = tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)
    return base


def build(size: int) -> Image.Image:
    img = _gradient(size)
    mask = _rounded_mask(size, size // 5)
    img.putalpha(255)
    img = Image.composite(img, Image.new("RGBA", img.size, (0, 0, 0, 0)), mask)

    draw = ImageDraw.Draw(img)
    # play triangle, slightly right of center for optical balance
    cx, cy = size * 0.54, size * 0.5
    w, h = size * 0.34, size * 0.40
    draw.polygon(
        [(cx - w / 2, cy - h / 2), (cx - w / 2, cy + h / 2), (cx + w / 2, cy)],
        fill=WHITE,
    )
    return img


def main() -> None:
    master = build(SIZE)
    master.save(OUT, format="ICO", sizes=ICO_SIZES)
    print(f"wrote {OUT} with sizes {ICO_SIZES}")


if __name__ == "__main__":
    main()
