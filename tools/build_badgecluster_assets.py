"""Rebuild original MIT-licensed BadgeCluster pixel art (requires Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / 'badge/apps/badgecluster'
ASSETS = ROOT / 'assets'
ASSETS.mkdir(exist_ok=True)

mona = Image.new('RGBA', (56, 40))
d = ImageDraw.Draw(mona)
ink, fur, face, purple = '#151a2b', '#252d47', '#ffd0b0', '#b69cff'
# Raised arms, triangular ears, face, and octocat-inspired tentacles.
d.line([(16, 27), (6, 17), (4, 7)], fill=purple, width=5)
d.line([(40, 27), (50, 17), (52, 7)], fill=purple, width=5)
d.ellipse((1, 3, 8, 11), fill=face)
d.ellipse((48, 3, 55, 11), fill=face)
d.polygon([(12, 12), (11, 0), (23, 7), (33, 7), (45, 0), (44, 15)], fill=fur)
d.ellipse((11, 6, 45, 29), fill=fur)
d.rounded_rectangle((16, 12, 40, 26), radius=6, fill=face)
d.rectangle((21, 17, 23, 20), fill=ink)
d.rectangle((33, 17, 35, 20), fill=ink)
d.line((26, 23, 30, 23), fill=ink, width=1)
d.rounded_rectangle((20, 27, 36, 36), radius=4, fill=purple)
for x, end in [(21, 12), (26, 23), (30, 33), (35, 45)]:
    d.line([(x, 32), (x, 37), (end, 38)], fill=purple, width=3)
mona.save(ASSETS / 'mona.png', optimize=True)
for i, radius in enumerate((7, 10, 13, 16, 19)):
    sphere = Image.new('RGBA', (40, 40))
    pen = ImageDraw.Draw(sphere)
    pen.ellipse((20-radius, 20-radius, 20+radius, 20+radius), fill='#336d99', outline='#7ae5d3', width=2)
    pen.arc((20-radius+3, 20-radius+2, 20+radius-3, 20+radius-2), 205, 305, fill='#e7fff6', width=2)
    pen.line((17, 18, 23, 22), fill='#7ae5d3', width=1)
    sphere.save(ASSETS / ('sphere_%d.png' % i), optimize=True)
icon = Image.new('RGB', (24, 24), '#0d111c')
p = ImageDraw.Draw(icon)
p.ellipse((7, 1, 17, 11), fill='#336d99', outline='#7ae5d3', width=1)
p.line([(5, 13), (2, 8)], fill='#b69cff', width=2)
p.line([(19, 13), (22, 8)], fill='#b69cff', width=2)
p.polygon([(6, 19), (5, 11), (10, 14), (14, 14), (19, 11), (18, 19)], fill='#b69cff')
p.rectangle((9, 17, 10, 18), fill='#0d111c')
p.rectangle((14, 17, 15, 18), fill='#0d111c')
p.rectangle((9, 20, 15, 22), fill='#b69cff')
icon.save(ROOT / 'icon.png', optimize=True)
