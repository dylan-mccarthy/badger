"""Render the actual app through a Pillow test adapter; not a firmware simulator."""
from pathlib import Path
import os
import runpy
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'badge/apps/badgecluster'


class Surface:
    width, height = 160, 120
    clip = (0, 0, 160, 120)
    pen = (255, 255, 255)
    font = None

    def __init__(self):
        self.canvas = Image.new('RGB', (self.width, self.height))
        self.draw = ImageDraw.Draw(self.canvas)
        self.typeface = ImageFont.truetype('DejaVuSansMono.ttf', 8)

    def rectangle(self, area):
        x, y, width, height = area
        self.draw.rectangle((x, y, x+width-1, y+height-1), fill=self.pen)

    def text(self, value, x, y):
        self.draw.text((x, y), value, fill=self.pen, font=self.typeface)

    def measure_text(self, value):
        return (int(self.draw.textlength(value, font=self.typeface)), 9)

    def blit(self, picture, position):
        self.canvas.paste(picture, position, picture)


def preview(output=APP / 'assets', transport=None, driver=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    screen = Surface()
    state = SimpleNamespace(now=0, button=None)
    badge = SimpleNamespace(pressed=lambda button: button == state.button)
    wrap = 1 << 30
    def run(update):
        if driver is not None:
            return driver(update, screen, state, output)
        update()
        screen.canvas.save(output / 'preview_mona.png')
        state.now = 60000
        for _ in range(10):
            update()
        worker = update.__globals__['worker']
        assert worker.samples == 250
        state.button = 'right'
        update()
        assert update.__globals__['page'] == 1
        assert worker.samples == 250
        screen.canvas.save(output / 'preview_stats.png')
        state.button = 'left'
        update()
        assert update.__globals__['page'] == 0
        state.button = 'back'
        assert update() is False
        assert worker.stopped
        state.now = 120000
        state.button = None
        update()
        assert worker.samples == 250
    namespace = dict(screen=screen, badge=badge,
                     image=SimpleNamespace(load=lambda path: Image.open(path).convert('RGBA')),
                     font=SimpleNamespace(nope=None), color=SimpleNamespace(rgb=lambda *rgb: rgb),
                     vec2=lambda x, y: (x, y), run=run,
                     BUTTON_LEFT='left', BUTTON_RIGHT='right', BUTTON_BACK='back', BUTTON_HOME='home')
    original = Path.cwd()
    original_path = list(sys.path)
    real_chdir = os.chdir
    try:
        sys.path.insert(0, str(APP))
        with patch.dict(sys.modules, {'cluster_transport': SimpleNamespace(create_transport=lambda: transport)}), \
             patch('os.chdir', lambda path: real_chdir(APP if path == '/system/apps/badgecluster' else path)), \
             patch.object(time, 'ticks_ms', lambda: state.now, create=True), \
             patch.object(time, 'ticks_add', lambda a, b: (a+b) % wrap, create=True), \
             patch.object(time, 'ticks_diff', lambda a, b: (a-b+wrap//2) % wrap-wrap//2, create=True):
            runpy.run_path(str(APP / '__init__.py'), init_globals=namespace)
    finally:
        real_chdir(original)
        sys.path[:] = original_path
    print('Actual badge app host checks passed.' if driver else 'Actual app host smoke check passed: both pages, 250 samples, navigation, exit.')


if __name__ == '__main__':
    preview()
