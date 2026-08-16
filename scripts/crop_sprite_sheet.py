"""Batch-crop a sprite sheet into individual PNG files.

Handles two layouts:
  1. Rule-based grid  — ``--cols``/``--rows`` or ``--cell-w``/``--cell-h``
  2. Auto-detect      — ``--auto`` finds content gaps (transparent or
     uniform background) and crops each cell to its content box.

Output files: ``{prefix}_{r}_{c}.png`` (row/col, 0-based).  With ``--map``,
a JSON dict ``{"0_0": "pm-D-1", ...}`` renames them to meaningful names.
``--preview`` writes ``preview.png`` with cell numbers overlaid.

Usage:
  python scripts/crop_sprite_sheet.py --image sheet.png --cols 8 --rows 4 --preview
  python scripts/crop_sprite_sheet.py --image sheet.png --auto --map names.json
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

MIN_ALPHA = 10      # alpha below this counts as background
SAMPLE_STEP = 2     # pixel sampling step for background detection


def load_rgba(path: str) -> Image.Image:
    img = Image.open(path)
    return img.convert("RGBA")


def detect_background(img: Image.Image):
    """Return a predicate fn(v: RGBA tuple) -> is_background.

    Transparent corners → alpha-based.  Otherwise use the corner color.
    """
    w, h = img.size
    corners = [img.getpixel(p) for p in [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)]]
    if all(c[3] < MIN_ALPHA for c in corners):
        return lambda v: v[3] < MIN_ALPHA
    bg = Counter(corners).most_common(1)[0][0]
    return lambda v: v == bg


def content_boxes(img: Image.Image):
    """Split image into content cells by row/col projection.

    Returns list of (x, y, w, h) boxes in reading order.
    """
    w, h = img.size
    is_bg = detect_background(img)
    px = img.load()
    col_hits = [0] * w
    row_hits = [0] * h
    for y in range(0, h, SAMPLE_STEP):
        for x in range(0, w, SAMPLE_STEP):
            if not is_bg(px[x, y]):
                col_hits[x] += 1
                row_hits[y] += 1

    def segments(hits, size):
        segs, start = [], None
        for i in range(size):
            if hits[i] > 0 and start is None:
                start = i
            elif hits[i] == 0 and start is not None:
                segs.append((start, i - 1))
                start = None
        if start is not None:
            segs.append((start, size - 1))
        return segs

    xsegs = segments(col_hits, w)
    ysegs = segments(row_hits, h)
    if not xsegs or not ysegs:
        return []

    # Merge near-identical segment sizes into a grid (handles 1px gaps).
    widths = sorted({b - a + 1 for a, b in xsegs})
    heights = sorted({b - a + 1 for a, b in ysegs})
    cell_w = widths[len(widths) // 2] if widths else 0
    cell_h = heights[len(heights) // 2] if heights else 0

    boxes = []
    for y0, y1 in ysegs:
        for x0, x1 in xsegs:
            boxes.append((x0, y0, x1 - x0 + 1, y1 - y0 + 1))
    return boxes, cell_w, cell_h


def crop_grid(img: Image.Image, cols: int, rows: int):
    """Crop by regular grid. Returns (boxes, cell_w, cell_h)."""
    w, h = img.size
    cell_w, cell_h = w // cols, h // rows
    boxes = []
    for r in range(rows):
        for c in range(cols):
            x, y = c * cell_w, r * cell_h
            boxes.append((x, y, cell_w, cell_h))
    return boxes, cell_w, cell_h


def trim_box(img: Image.Image, box) -> tuple:
    """Shrink *box* to the content bounding box inside it."""
    x, y, bw, bh = box
    is_bg = detect_background(img)
    region = img.crop((x, y, x + bw, y + bh))
    px = region.load()
    min_x, min_y, max_x, max_y = bw, bh, -1, -1
    for yy in range(bh):
        for xx in range(bw):
            if not is_bg(px[xx, yy]):
                min_x, min_y = min(min_x, xx), min(min_y, yy)
                max_x, max_y = max(max_x, xx), max(max_y, yy)
    if max_x < 0:
        return None  # empty cell
    return (x + min_x, y + min_y, max_x - min_x + 1, max_y - min_y + 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="sprite sheet PNG path")
    ap.add_argument("--output", default="sprites_out", help="output dir")
    ap.add_argument("--cols", type=int, default=0)
    ap.add_argument("--rows", type=int, default=0)
    ap.add_argument("--cell-w", type=int, default=0)
    ap.add_argument("--cell-h", type=int, default=0)
    ap.add_argument("--auto", action="store_true", help="auto-detect grid")
    ap.add_argument("--trim", action="store_true", help="crop to content box")
    ap.add_argument("--prefix", default="sprite", help="output filename prefix")
    ap.add_argument("--map", default="", help="JSON mapping {r_c: name}")
    ap.add_argument("--preview", action="store_true", help="write preview.png")
    args = ap.parse_args()

    img = load_rgba(args.image)
    w, h = img.size
    print(f"image: {w}x{h}")

    if args.auto:
        boxes, cw, ch = content_boxes(img)
        print(f"auto-detect: {len(boxes)} cells (~{cw}x{ch} each)")
    elif args.cols and args.rows:
        boxes, cw, ch = crop_grid(img, args.cols, args.rows)
    elif args.cell_w and args.cell_h:
        cols, rows = w // args.cell_w, h // args.cell_h
        print(f"grid: {cols} cols x {rows} rows")
        boxes, cw, ch = crop_grid(img, cols, rows)
    else:
        ap.error("provide --auto, --cols/--rows, or --cell-w/--cell-h")

    if not boxes:
        print("ERROR: no content detected")
        sys.exit(1)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    mapping = {}
    if args.map:
        with open(args.map, encoding="utf-8") as f:
            mapping = json.load(f)

    preview = img.copy() if args.preview else None
    pd = ImageDraw.Draw(preview) if preview else None

    saved = []
    ncols = max(1, args.cols or (args.cell_w and w // args.cell_w) or 0)
    for idx, box in enumerate(boxes):
        cell = box
        if args.trim:
            trimmed = trim_box(img, box)
            if trimmed is None:
                continue  # empty cell
            cell = trimmed
        r, c = divmod(idx, ncols) if ncols else (idx, 0)
        key = f"{r}_{c}"
        name = mapping.get(key, f"{args.prefix}_{key}")
        x, y, bw, bh = cell
        crop = img.crop((x, y, x + bw, y + bh))
        path = out / f"{name}.png"
        crop.save(path)
        saved.append((key, str(path)))
        if preview:
            pd.rectangle([x, y, x + bw - 1, y + bh - 1], outline=(255, 0, 0, 255))
            pd.text((x + 2, y + 2), key, fill=(255, 255, 255, 255))

    print(f"saved {len(saved)} files to {out}")
    if preview:
        preview.save(out / "preview.png")
        print(f"preview: {out / 'preview.png'}")
    if not mapping:
        print("\nCell keys (use with --map JSON, e.g. \"0_0\": \"pm-D-1\"):")
        print("  " + ", ".join(k for k, _ in saved[:20]))


if __name__ == "__main__":
    main()
