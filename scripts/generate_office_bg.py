"""Generate a Stardew-Valley-style pixel office background PNG.

Output: web/public/sprites/office-bg.png (680x1120 = 2x of 340x560 logical px)

Run: python scripts/generate_office_bg.py
"""
import os
from PIL import Image, ImageDraw

# ── Stardew Valley warm palette ──
C = {
    'darkbrown':  (0x3c, 0x28, 0x17),   # outlines / shadows
    'warmbrown':  (0x6b, 0x42, 0x26),   # furniture dark
    'lightbrown': (0xa0, 0x71, 0x4f),   # furniture
    'wood':       (0xc4, 0x8d, 0x5c),   # floor
    'woodlight':  (0xd3, 0xa3, 0x74),   # floor highlight
    'cream':      (0xf5, 0xe6, 0xd3),   # walls
    'creamdark':  (0xe2, 0xcf, 0xb5),   # wall shade
    'white':      (0xff, 0xf5, 0xe8),   # paper
    'gray':       (0x4a, 0x4a, 0x4a),   # metal dark
    'graylight':  (0x8a, 0x8a, 0x8a),   # metal
    'green':      (0x5b, 0x8c, 0x4e),   # plant green
    'greenlight': (0x7e, 0xc8, 0x5c),   # screen green
    'red':        (0x8b, 0x2e, 0x2e),   # dark red
    'orange':     (0xd4, 0x74, 0x3b),   # warm orange
    'sky':        (0x8f, 0xb8, 0xd8),   # soft sky blue
    'skywhite':   (0xf5, 0xea, 0xd8),   # clouds
    'blue':       (0x5b, 0x7b, 0x9a),   # muted blue
    'purple':     (0x5c, 0x4b, 0x6e),   # muted purple
    'black':      (0x1e, 0x1e, 0x1e),   # text
    'leafdark':   (0x4a, 0x74, 0x3f),   # dark leaf
    'gold':       (0xd4, 0xa0, 0x4a),   # clock gold
}

SCALE = 2
LW, LH = 340, 560           # logical pixels
W, H = LW * SCALE, LH * SCALE

img = Image.new('RGB', (W, H), C['cream'])
d = ImageDraw.Draw(img)

def px(rect, color):
    """Draw a logical-pixel rect, scaled 2x."""
    x, y, w, h = rect
    d.rectangle([x*SCALE, y*SCALE, (x+w)*SCALE-1, (y+h)*SCALE-1], fill=color)

def rects(shape):
    for r, c in shape:
        px(r, c)

# ── tiny 3x5 pixel font for zone labels ──
FONT = {
    'A': 0x1D7, 'B': 0x1D7, 'C': 0x1C7, 'D': 0x1D7, 'E': 0x1C7, 'F': 0x1C4,
    'G': 0x1F7, 'H': 0x1D7, 'I': 0x049, 'K': 0x1D5, 'L': 0x1C7,
    'M': 0x1FF, 'N': 0x1F7, 'O': 0x1D7, 'P': 0x1D4, 'Q': 0x1D7, 'R': 0x1D5,
    'S': 0x1C3, 'T': 0x049, 'U': 0x1D7, 'V': 0x1D7, 'W': 0x1FF,
    'X': 0x155, 'Y': 0x115,
}

def pixel_text(x, y, text, color, spacing=4):
    """Draw text using 3x5 bitmap font (bits: row-major, 3 bits/row)."""
    for ci, ch in enumerate(text):
        bits = FONT.get(ch, 0)
        for row in range(5):
            for col in range(3):
                if bits & (1 << (row * 3 + col)):
                    px([x + ci * spacing + col, y + row, 1, 1], color)

# ═══════════════════════════════════════════════
# WALLS & CEILING
# ═══════════════════════════════════════════════
# ceiling band with subtle shading
for i in range(8):
    px([0, i, LW, 1], C['warmbrown'] if i < 4 else C['lightbrown'])
# walls — soft vertical gradient (slightly darker at top)
for y in range(8, 250):
    t = (y - 8) / 242
    c = tuple(int(C['cream'][k] * (1 - t * 0.08) + C['creamdark'][k] * t * 0.08) for k in range(3))
    px([0, y, LW, 1], c)
# chair rail
px([0, 122, LW, 3], C['wood'])
px([0, 125, LW, 1], C['darkbrown'])

# ═══════════════════════════════════════════════
# WINDOW (top-left)
# ═══════════════════════════════════════════════
px([16, 16, 56, 44], C['darkbrown'])          # frame outer
px([18, 18, 52, 40], C['sky'])                # sky
# clouds
clouds = [(24, 24, 14, 6), (44, 34, 18, 5), (30, 30, 10, 4)]
for cx, cy, cw, ch in clouds:
    px([cx, cy, cw, ch], C['skywhite'])
# mullions
px([44, 18, 2, 40], C['darkbrown'])
px([18, 38, 52, 2], C['darkbrown'])
# sill
px([14, 60, 60, 3], C['wood'])
px([14, 63, 60, 1], C['darkbrown'])
# tiny sun
px([24, 20, 4, 4], C['gold'])

# ═══════════════════════════════════════════════
# CLOCK (top-center-right)
# ═══════════════════════════════════════════════
px([238, 18, 30, 30], C['wood'])              # clock body
px([242, 22, 22, 22], C['cream'])             # face
px([242, 22, 22, 1], C['darkbrown'])
px([242, 43, 22, 1], C['darkbrown'])
px([242, 22, 1, 22], C['darkbrown'])
px([263, 22, 1, 22], C['darkbrown'])
# hands (10:10)
px([252, 26, 2, 12], C['darkbrown'])          # minute
px([253, 32, 8, 2], C['darkbrown'])           # hour
px([252, 31, 4, 4], C['red'])                 # center cap

# ═══════════════════════════════════════════════
# COFFEE STATION (top-right)
# ═══════════════════════════════════════════════
# counter
px([288, 44, 48, 3], C['wood'])
px([288, 47, 48, 1], C['darkbrown'])
# coffee machine
px([292, 22, 22, 22], C['gray'])
px([294, 24, 8, 5], C['red'])                 # indicator
px([296, 30, 10, 2], C['darkbrown'])          # tray
px([292, 40, 22, 3], C['darkbrown'])          # base
px([298, 14, 10, 8], C['graylight'])          # water tank
# cup
px([318, 38, 8, 6], C['white'])
px([318, 36, 8, 2], C['graylight'])           # rim
px([320, 44, 2, 3], C['white'])               # handle hint

# ═══════════════════════════════════════════════
# DESIGN ZONE (left-middle) — round table + whiteboard
# ═══════════════════════════════════════════════
pixel_text(16, 128, 'DESIGN', C['darkbrown'])
# whiteboard (small)
px([12, 138, 60, 40], C['wood'])              # frame
px([15, 141, 54, 34], C['white'])             # board
# blueprint sketches
px([22, 148, 16, 1], C['blue'])
px([22, 152, 12, 1], C['blue'])
px([22, 156, 16, 1], C['blue'])
px([46, 148, 8, 1], C['red'])
px([46, 152, 6, 1], C['red'])
px([46, 156, 8, 1], C['red'])
# round table
px([28, 186, 56, 4], C['wood'])               # table top
px([28, 190, 56, 2], C['darkbrown'])          # table edge
px([36, 192, 4, 12], C['darkbrown'])          # leg left
px([70, 192, 4, 12], C['darkbrown'])          # leg right
# blueprint papers on table
px([36, 182, 18, 4], C['white'])
px([36, 182, 18, 1], C['blue'])
px([58, 182, 12, 4], C['cream'])

# ═══════════════════════════════════════════════
# CODING ZONE (right-middle) — long desk + 3 monitors
# ═══════════════════════════════════════════════
pixel_text(140, 128, 'CODING', C['darkbrown'])
# long desk
px([132, 150, 196, 4], C['wood'])
px([132, 154, 196, 2], C['darkbrown'])
px([140, 156, 4, 14], C['darkbrown'])
px([180, 156, 4, 14], C['darkbrown'])
px([220, 156, 4, 14], C['darkbrown'])
px([260, 156, 4, 14], C['darkbrown'])
px([300, 156, 4, 14], C['darkbrown'])
# monitors
monitors = [(148, 118), (196, 118), (244, 118)]
for mx, my in monitors:
    px([mx, my, 40, 28], C['darkbrown'])      # bezel
    px([mx+3, my+3, 34, 22], C['blue'])       # screen bg
    px([mx+4, my+4, 32, 20], C['greenlight']) # screen glow
    # code lines
    px([mx+6, my+8, 18, 2], C['darkbrown'])
    px([mx+6, my+14, 14, 2], C['darkbrown'])
    px([mx+6, my+20, 20, 2], C['darkbrown'])
    # stand
    px([mx+17, my+28, 6, 4], C['darkbrown'])
# keyboard hints
px([156, 148, 24, 2], C['darkbrown'])
px([204, 148, 24, 2], C['darkbrown'])
px([252, 148, 24, 2], C['darkbrown'])

# ═══════════════════════════════════════════════
# REVIEW ZONE (left-bottom) — big board + checklist
# ═══════════════════════════════════════════════
pixel_text(16, 254, 'REVIEW', C['darkbrown'])
# big board
px([12, 262, 140, 60], C['wood'])
px([15, 265, 134, 54], C['white'])
# board screen panel
px([20, 270, 72, 44], C['darkbrown'])
px([23, 273, 66, 38], C['greenlight'])
# code lines on screen
for i, w_ in enumerate([40, 32, 46, 28, 36]):
    px([26, 278 + i*7, w_, 2], C['darkbrown'])
# checklist paper
px([106, 270, 36, 44], C['white'])
px([106, 270, 36, 1], C['darkbrown'])
px([106, 313, 36, 1], C['darkbrown'])
# checklist rows
for i in range(4):
    px([110, 277 + i*8, 14, 2], C['gray'])
    px([128, 277 + i*8, 8, 2], C['red'] if i == 0 else C['green'])

# ═══════════════════════════════════════════════
# INTEGRATION ZONE (right-bottom) — corner desk + dual monitors
# ═══════════════════════════════════════════════
pixel_text(258, 254, 'INTEGR', C['darkbrown'])
# corner desk (L-shape)
px([220, 296, 112, 4], C['wood'])
px([220, 300, 112, 2], C['darkbrown'])
px([222, 302, 4, 14], C['darkbrown'])
px([326, 302, 4, 14], C['darkbrown'])
# dual monitors
for mx in (230, 282):
    px([mx, 268, 42, 28], C['darkbrown'])
    px([mx+3, 271, 36, 22], C['blue'])
    px([mx+4, 272, 34, 20], C['greenlight'])
    px([mx+6, 277, 22, 2], C['darkbrown'])
    px([mx+6, 284, 16, 2], C['darkbrown'])
    px([mx+17, 296, 6, 4], C['darkbrown'])
# cable
px([340, 288, 2, 12], C['darkbrown'])

# ═══════════════════════════════════════════════
# FLOOR (warm wood planks)
# ═══════════════════════════════════════════════
for y in range(330, LH, 8):
    px([0, y, LW, 8], C['wood'])
    px([0, y+7, LW, 1], C['darkbrown'])       # plank seam
    px([0, y, LW, 1], C['woodlight'])          # plank highlight
# staggered vertical seams
for y in range(330, LH, 8):
    seam = 60 if (y // 8) % 3 else 180
    px([seam, y, 1, 7], C['darkbrown'])
    px([seam + 90, y, 1, 7], C['darkbrown']) if seam + 90 < LW else None

# ═══════════════════════════════════════════════
# SUPPORT STRIP — front counter + plants
# ═══════════════════════════════════════════════
px([0, 322, LW, 4], C['wood'])                 # counter top
px([0, 326, LW, 2], C['darkbrown'])            # counter edge
# counter legs
for cx in (20, 120, 220, 320):
    px([cx, 328, 4, 14], C['darkbrown'])

# plants (potted)
def plant(bx, by):
    # pot
    px([bx, by, 16, 12], C['warmbrown'])
    px([bx+2, by-3, 12, 3], C['warmbrown'])
    # leaves
    leaves = [(bx+2, by-8, 6, 6), (bx+8, by-10, 6, 6), (bx+4, by-14, 5, 6),
              (bx+9, by-15, 5, 6), (bx+6, by-19, 6, 5)]
    for lx, ly, lw, lh in leaves:
        px([lx, ly, lw, lh], C['green'])
        px([lx, ly, lw, 1], C['leafdark'])
    # pot highlight
    px([bx+2, by, 4, 1], C['lightbrown'])

plant(150, 306)
plant(272, 306)

# ═══════════════════════════════════════════════
# HANGING LAMPS (soft warm light)
# ═══════════════════════════════════════════════
for lx in (80, 200, 300):
    px([lx+5, 8, 2, 8], C['darkbrown'])        # cord
    px([lx, 16, 12, 5], C['wood'])             # shade
    px([lx+2, 21, 8, 3], C['gold'])            # warm bulb
    px([lx+1, 24, 10, 1], C['orange'])         # glow hint

# subtle warm light pool under lamps
for lx in (80, 200, 300):
    px([lx-10, 240, 32, 60], (0xff, 0xf0, 0xd0))

# vignette — darker at edges
for i in range(8):
    edge = i * 2
    px([0, i, LW, 1], tuple(int(v * (1 - i * 0.06)) for v in C['darkbrown']))
    px([0, LH-1-i, LW, 1], tuple(int(v * (1 - i * 0.06)) for v in C['darkbrown']))

# ═══════════════════════════════════════════════
# SAVE
# ═══════════════════════════════════════════════
out = os.path.join(os.path.dirname(__file__), '..', 'web', 'public', 'sprites', 'office-bg.png')
out = os.path.abspath(out)
os.makedirs(os.path.dirname(out), exist_ok=True)
img.save(out)
print(f'Saved: {out} ({img.size[0]}x{img.size[1]})')
