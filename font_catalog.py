"""Project-local fonts and glyph fallback; rendering never downloads fonts."""
from functools import lru_cache
import json
import math
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FONT_ROOT = ROOT / "fonts"
CATALOG = json.loads((FONT_ROOT / "catalog.json").read_text(encoding="utf-8"))
FONT_FILES = {item["id"]: FONT_ROOT / item["file"] for item in CATALOG}
FONT_LABELS = {item["id"]: item["label"] for item in CATALOG}
FALLBACK_PATH = FONT_FILES["lxgw_wenkai_tc"]


def available_fonts():
    return [{**item, "url": f"/api/fonts/{item['id']}/file"}
            for item in CATALOG if FONT_FILES[item["id"]].is_file()]


@lru_cache(maxsize=128)
def codepoints(path):
    with TTFont(str(path), lazy=True, fontNumber=0) as font:
        return frozenset(font.getBestCmap() or {})


def glyph_report(font_id, text):
    primary = codepoints(str(FONT_FILES[font_id]))
    fallback = codepoints(str(FALLBACK_PATH))
    chars = set(text) - set("\n\r\t ")
    return {"fallback": "".join(sorted(c for c in chars if ord(c) not in primary and ord(c) in fallback)),
            "missing": "".join(sorted(c for c in chars if ord(c) not in primary and ord(c) not in fallback))}


class FallbackFont(ImageFont.FreeTypeFont):
    """Keep supported runs in the selected face, fill gaps with bundled WenKai TC.

    Exposes Pillow's font protocol so measurement, wrapping and drawing use the
    same glyphs. Unchanged strings delegate to FreeType, preserving old layout.
    """
    def __init__(self, path, size):
        self.size = size
        self.primary = ImageFont.truetype(str(path), size)
        self.coverage = codepoints(str(path))
        self.fallback = ImageFont.truetype(str(FALLBACK_PATH), size)

    def runs(self, text):
        runs = []
        for char in text:
            font = self.primary if char.isspace() or ord(char) in self.coverage else self.fallback
            if runs and runs[-1][1] is font:
                runs[-1] = (runs[-1][0] + char, font)
            else:
                runs.append((char, font))
        return runs

    def getlength(self, text, mode="", direction=None, features=None, language=None):
        return sum(font.getlength(run, mode, direction, features, language) for run, font in self.runs(text))

    def getbbox(self, text, mode="", direction=None, features=None, language=None,
                stroke_width=0, anchor=None):
        runs = self.runs(text)
        if len(runs) <= 1:
            return (runs[0][1] if runs else self.primary).getbbox(
                text, mode, direction, features, language, stroke_width, anchor)
        if anchor not in (None, "la"):
            raise ValueError("Mixed-font text requires the default left ascender anchor")
        ascent = self.primary.getmetrics()[0]
        boxes, x = [], 0
        for run, font in runs:
            left, top, right, bottom = font.getbbox(run, stroke_width=stroke_width, anchor="ls")
            boxes.append((x + left, ascent + top, x + right, ascent + bottom))
            x += font.getlength(run)
        return (math.floor(min(b[0] for b in boxes)), min(b[1] for b in boxes),
                math.ceil(max(b[2] for b in boxes)), max(b[3] for b in boxes))

    def getmask2(self, text, mode="", **kwargs):
        runs = self.runs(text)
        if len(runs) <= 1:
            return (runs[0][1] if runs else self.primary).getmask2(text, mode, **kwargs)
        stroke = kwargs.get("stroke_width", 0)
        left, top, right, bottom = self.getbbox(text, stroke_width=stroke)
        mask = Image.new("L", (max(1, right - left), max(1, bottom - top)))
        draw = ImageDraw.Draw(mask)
        x, baseline = -left, self.primary.getmetrics()[0] - top
        for run, font in runs:
            draw.text((x, baseline), run, font=font, anchor="ls", fill=255,
                      stroke_width=stroke, stroke_fill=255)
            x += font.getlength(run)
        return mask.im, (left, top)


def load_font(path, size):
    return FallbackFont(path, size)
