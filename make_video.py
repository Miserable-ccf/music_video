#!/usr/bin/env python3
"""音乐 -> 黑底书法字宽屏视频（最小实现）。

两层文字在屏幕正中央重叠，形成层次感：
  底层：大号灰色 = 歌曲名（半透明，铺底）
  上层：中号暗红色 = 歌词（按 LRC 时间戳淡入淡出，压在歌名上）
默认 1920x1080（16:9 宽屏）。字号自适应：歌名撑满约 72% 高、92% 宽。
用法见 README.md，或直接：python3 make_video.py --help
"""
import argparse
import math
import random
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from font_catalog import load_font

try:
    from beat_detect import detect_beats, detect_downbeats
except Exception:  # 无 numpy 或模块缺失时不阻断主流程
    detect_beats = None
    detect_downbeats = None

WIDTH, HEIGHT = 1920, 1080
FPS = 30
BG = (0, 0, 0)
BG_TOP = (40, 40, 46)     # 渐变背景顶部（略亮）
BG_BOTTOM = (2, 2, 4)  # 渐变背景底部（近黑）
TITLE_COLOR = (68, 68, 68, 122)     # 灰色歌名，更淡更透
LYRICS_COLOR = (142, 18, 18, 255)   # 暗红歌词，更饱和醒目
FADE_SEC = 0.14
_BASE_DIR = Path(__file__).resolve().parent
DEFAULT_FONTS = [
    str(_BASE_DIR / "fonts" / "MaShanZheng-Regular.ttf"),  # 马善政毛楷（方正毛笔字）
    str(_BASE_DIR / "fonts" / "MaShanZheng-Regular.ttf"),  # 马善政毛楷（红色歌词）
]

# 布局常量（比例）
TITLE_WIDTH_RATIO = 0.92   # 歌名最大宽度占屏宽
TITLE_HEIGHT_RATIO = 0.78  # 歌名最大高度占屏高
LYRIC_WIDTH_RATIO = 0.74   # 歌词最大宽度占屏宽
LYRIC_HEIGHT_RATIO = 0.60  # 歌词（含换行）最大高度占屏高
LINE_GAP_RATIO = 0.14      # 多行之间的间距 = 字号 * 该比例

# 重拍强调参数（分级：强拍 > 次强拍 > 弱拍）
BEAT_INK = {"blur": 0.018, "erase": 0.02, "speckles": 90, "splatter": 0.018}
BEAT_COLOR = (226, 38, 28, 255)   # 重拍时更亮更饱和的红
BEAT_WIN_SEC = 0.22               # 强调脉冲的半窗（秒）
EMPHASIS_STRENGTH = 0.85          # 全局强调强度 0~1
BEAT_LEVEL_GAIN = {"down": 1.0, "medium": 0.62, "weak": 0.38}

# 泼墨效果参数（模糊/飞白均为字号的相对比例）
INK_PRESETS = {
    "dry":    {"blur": 0.010, "erase": 0.18, "speckles": 220, "splatter": 0.030},
    "normal": {"blur": 0.014, "erase": 0.10, "speckles": 160, "splatter": 0.020},
    "heavy":  {"blur": 0.022, "erase": 0.05, "speckles": 110, "splatter": 0.012},
}
TITLE_INK = INK_PRESETS["normal"]
LYRIC_INK = INK_PRESETS["normal"]

# 传给子进程的全局状态
_G_TITLE = None
_G_SEGMENTS = None
_G_TITLE_FONT = None
_G_LYRIC_FONT = None
_G_TITLE_MAX_W = None
_G_LYRIC_MAX_W = None
_G_BG = None
_G_TITLE_LAYER = None
_G_LYRIC_LAYERS = None
_G_TITLE_INK = None
_G_LYRIC_INK = None
_G_BEAT_LAYERS = None
_G_BEATS = None
_G_BEAT_WIN = BEAT_WIN_SEC
_G_SCENE = None
_G_SCENE_LAYERS = None
_G_MIST = None


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _ensure_parent(path):
    """确保输出文件所在目录存在，方便直接写到 videos/、previews/ 等子目录。"""
    p = Path(path)
    if p.parent != Path("."):
        p.parent.mkdir(parents=True, exist_ok=True)


def parse_lyrics(text):
    """返回 (title, segments)。segments = [{'start','end','text'}, ...]"""
    line_re = re.compile(r"^\s*\[(\d{1,2}):(\d{1,2}(?:\.\d+)?)\]\s*(.*)$")
    meta_re = re.compile(r"^\s*\[\s*(ar|ti|al|by|offset|status)\s*:\s*(.*?)\s*\]\s*$")
    title = None
    raw = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        mm = meta_re.match(line)
        if mm:
            key, val = mm.groups()
            if key == "ti":
                title = val
            continue
        m = line_re.match(line)
        if not m:
            if title is None:
                title = line.lstrip("《").rstrip("》")
            continue
        t = int(m.group(1)) * 60 + float(m.group(2))
        raw.append((t, m.group(3)))
    raw.sort()
    if not raw:
        raise SystemExit("歌词文件里没有找到任何 [mm:ss.xx] 时间戳行")
    segments = []
    for i, (start, lyric) in enumerate(raw):
        end = raw[i + 1][0] if i + 1 < len(raw) else None
        segments.append({"start": start, "end": end, "text": lyric})
    return title, segments


def lyric_status(text):
    """读取工作流状态；普通手写/外部 LRC 没有状态时仍可使用。"""
    match = re.search(r"(?mi)^\s*\[\s*status\s*:\s*([^\]]+)\]\s*$", text)
    return match.group(1).strip().lower() if match else None


def _balanced_chunks(text, max_chars):
    """按语义空格优先、长度均衡地拆成短歌词，不在结果中保留分隔空格。"""
    text = text.strip()
    if not text or max_chars <= 0:
        return [text]
    # 全角空格是人工指定的硬断句点；普通空格仍允许自动合并（兼容英文歌词）。
    hard_parts = [part.strip() for part in text.split("　") if part.strip()]
    if len(hard_parts) > 1:
        chunks = []
        for part in hard_parts:
            chunks.extend(_balanced_chunks(part, max_chars))
        return chunks
    tokens = re.split(r"\s+", text)
    chunks = []
    current = ""
    for token in tokens:
        if len(token) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            count = math.ceil(len(token) / max_chars)
            base, extra = divmod(len(token), count)
            pos = 0
            for index in range(count):
                size = base + (1 if index < extra else 0)
                chunks.append(token[pos:pos + size])
                pos += size
        elif not current:
            current = token
        elif len(current) + len(token) <= max_chars:
            current += token
        else:
            chunks.append(current)
            current = token
    if current:
        chunks.append(current)
    return chunks or [text]


def shorten_lyric_segments(segments, max_chars):
    """将长句拆成短语，并按字符数在原时间段内分配显示时间。"""
    if max_chars <= 0:
        return segments
    result = []
    for segment in segments:
        chunks = _balanced_chunks(segment["text"], max_chars)
        if len(chunks) == 1 or segment["end"] is None or not segment["text"].strip():
            result.append(segment.copy())
            continue
        total_chars = sum(len(chunk) for chunk in chunks)
        cursor = segment["start"]
        duration = max(0.0, segment["end"] - segment["start"])
        for index, chunk in enumerate(chunks):
            end = (segment["end"] if index == len(chunks) - 1 else
                   cursor + duration * len(chunk) / total_chars)
            result.append({"start": cursor, "end": end, "text": chunk})
            cursor = end
    return result


def make_bg():
    """生成垂直线性渐变背景，顶部略亮、底部近黑。"""
    grad = Image.new("RGB", (1, HEIGHT))
    px = grad.load()
    for y in range(HEIGHT):
        t = y / max(1, HEIGHT - 1)
        px[0, y] = tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
    return grad.resize((WIDTH, HEIGHT))


def make_scene():
    """统一模板：黑色渐变，不附加场景；CPU/GPU 使用同一底图。"""
    return make_bg().convert("RGBA"), [], Image.new("RGBA", (WIDTH, HEIGHT))


_PROBE = ImageDraw.Draw(Image.new("RGBA", (4, 4)))


def ink_height(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _layout_text(lines, font):
    """计算每行居中绘制坐标，返回 [(text, x, top, width, ink_h, bbox), ...]"""
    inks = [ink_height(_PROBE, ln, font) for ln in lines]
    gap = int(font.size * LINE_GAP_RATIO)
    total = sum(inks) + gap * (len(lines) - 1)
    y = (HEIGHT - total) / 2
    out = []
    for ln, ih in zip(lines, inks):
        bbox = _PROBE.textbbox((0, 0), ln, font=font)
        w = _PROBE.textlength(ln, font=font)
        x = WIDTH / 2 - w / 2
        top = y - bbox[1]
        out.append((ln, x, top, w, ih, bbox))
        y += ih + gap
    return out


def _scale_alpha(layer, ratio):
    if ratio >= 1.0:
        return layer
    scaled = layer.copy()
    a = scaled.getchannel("A").point(lambda v: int(v * ratio))
    scaled.putalpha(a)
    return scaled


def _ease(x):
    """smoothstep 缓动：开头慢、结尾慢，让歌词淡入淡出更柔和。"""
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _ease_out(x):
    """快速起步、末端收稳，适合干脆的歌词入场。"""
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


def _beat_emphasis(t):
    """返回当前时间重拍强调强度 0~1。

    _G_BEATS 元素为 (time, level)；按 BEAT_LEVEL_GAIN 对强/次强/弱拍加权。
    """
    if not _G_BEATS:
        return 0.0
    half = _G_BEAT_WIN
    val = 0.0
    for item in _G_BEATS:
        b = item[0]
        level = item[1] if len(item) > 1 else "down"
        gain = BEAT_LEVEL_GAIN.get(level, 1.0)
        d = abs(t - b)
        if d <= half:
            k = gain * (1.0 - d / half)
            val = max(val, _ease(k))
    return min(1.0, val)


def wrap_by_pixels(draw, text, font, max_width):
    lines = []
    for raw in text.split("\n"):
        if not raw:
            continue
        cur = ""
        for ch in raw:
            if draw.textlength(cur + ch, font=font) <= max_width:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines or [" "]


def wrap_balanced(draw, text, font, max_width):
    """均衡断行：优先让各行的字数尽量接近（如 5 字句断成 3+2 而非 4+1）。

    以显式换行符分段，每段独立排布；只有宽度超限时才多断一行。
    """
    lines = []
    for raw in text.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        if draw.textlength(raw, font=font) <= max_width:
            lines.append(raw)
            continue

        chars = list(raw)
        k = 1
        while k < len(chars):
            broken = False
            cur = []
            idx = 0
            for i in range(k):
                cnt = math.ceil((len(chars) - idx) / (k - i))
                seg = "".join(chars[idx:idx + cnt])
                if draw.textlength(seg, font=font) > max_width:
                    broken = True
                    break
                cur.append(seg)
                idx += cnt
            if not broken:
                lines.extend(cur)
                break
            k += 1
        else:
            lines.extend(chars)
    return lines or [" "]


def compute_layout(title, segments, title_font_path, lyric_font_path,
                   title_size=0, lyric_size=0):
    """返回 (title_font, lyric_font, title_size, lyric_size, title_max_w, lyric_max_w)。

    歌名按‘宽度 92% + 高度 72%’求最大字号；歌词独立按‘宽度 52% + 高度 36%’
    求最大字号，保证整句在同一行、且全部行都落在屏幕内。
    """
    title_max_w = int(WIDTH * TITLE_WIDTH_RATIO)
    lyric_max_w = int(WIDTH * LYRIC_WIDTH_RATIO)
    title_max_h = int(HEIGHT * TITLE_HEIGHT_RATIO)
    lyric_max_h = int(HEIGHT * LYRIC_HEIGHT_RATIO)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    def title_metrics(tsize):
        tf = load_font(title_font_path, tsize)
        lines = wrap_by_pixels(probe, title, tf, title_max_w)
        gap = int(tsize * LINE_GAP_RATIO)
        h = sum(ink_height(probe, ln, tf) for ln in lines) + gap * (len(lines) - 1)
        w = max((probe.textlength(line, font=tf) for line in lines), default=0)
        return tf, h, w

    lo, hi = 6, min(3000, max(6, round(title_size * HEIGHT / 1080))) if title_size else 3000
    best_tf, best_size = None, 6
    while lo <= hi:
        mid = (lo + hi) // 2
        tf, h, w = title_metrics(mid)
        if h <= title_max_h and w <= title_max_w:
            best_tf, best_size = tf, mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best_tf is None:
        best_tf, _, _ = title_metrics(6)
        best_size = 6

    def lyric_metrics(lsize):
        lf = load_font(lyric_font_path, lsize)
        gap = int(lsize * LINE_GAP_RATIO)
        max_h = max_w = 0
        for seg in segments:
            lines = wrap_balanced(probe, seg["text"], lf, lyric_max_w)
            h = sum(ink_height(probe, ln, lf) for ln in lines) + gap * (len(lines) - 1)
            max_h = max(max_h, h)
            max_w = max(max_w, max((probe.textlength(line, font=lf) for line in lines), default=0))
        return lf, max_h, max_w

    lo, hi = 6, min(3000, max(6, round(lyric_size * HEIGHT / 1080))) if lyric_size else 3000
    best_lf, lyric_size = None, 6
    while lo <= hi:
        mid = (lo + hi) // 2
        lf, h, w = lyric_metrics(mid)
        if h <= lyric_max_h and w <= lyric_max_w:
            best_lf, lyric_size = lf, mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best_lf is None:
        best_lf, _, _ = lyric_metrics(6)
        lyric_size = 6

    return best_tf, best_lf, best_size, lyric_size, title_max_w, lyric_max_w


def worker_init(title, segments, title_font_path, lyric_font_path,
                title_size, lyric_size, title_max_w, lyric_max_w,
                title_ink=None, lyric_ink=None, beats=None,
                beat_win=BEAT_WIN_SEC):
    global _G_TITLE, _G_SEGMENTS, _G_TITLE_FONT, _G_LYRIC_FONT
    global _G_TITLE_MAX_W, _G_LYRIC_MAX_W, _G_BG
    global _G_TITLE_LAYER, _G_LYRIC_LAYERS, _G_TITLE_INK, _G_LYRIC_INK
    global _G_BEAT_LAYERS, _G_BEATS, _G_BEAT_WIN
    global _G_SCENE, _G_SCENE_LAYERS, _G_MIST
    _G_TITLE = title
    _G_SEGMENTS = segments
    _G_TITLE_FONT = load_font(title_font_path, title_size)
    _G_LYRIC_FONT = load_font(lyric_font_path, lyric_size)
    _G_TITLE_MAX_W = title_max_w
    _G_LYRIC_MAX_W = lyric_max_w
    _G_BG, _G_SCENE_LAYERS, _G_MIST = make_scene()
    _G_SCENE = _G_BG
    _G_TITLE_INK = title_ink or TITLE_INK
    _G_LYRIC_INK = lyric_ink or LYRIC_INK
    _G_BEATS = []
    for b in (beats or []):
        if isinstance(b, (tuple, list)) and len(b) >= 2:
            _G_BEATS.append((float(b[0]), b[1]))
        else:
            _G_BEATS.append((float(b), "down"))
    _G_BEAT_WIN = beat_win

    title_lines = wrap_by_pixels(_PROBE, _G_TITLE, _G_TITLE_FONT, _G_TITLE_MAX_W)
    _G_TITLE_LAYER = _build_clean_layer(
        title_lines, _G_TITLE_FONT, TITLE_COLOR[:3], TITLE_COLOR[3] / 255.0)

    _G_LYRIC_LAYERS = []
    _G_BEAT_LAYERS = []
    for i, seg in enumerate(_G_SEGMENTS):
        lines = wrap_balanced(_PROBE, seg["text"], _G_LYRIC_FONT, _G_LYRIC_MAX_W)
        _G_LYRIC_LAYERS.append(_build_clean_layer(
            lines, _G_LYRIC_FONT, LYRICS_COLOR[:3], LYRICS_COLOR[3] / 255.0))
        # 重拍强调层：更亮更浓，叠加时更显气势
        _G_BEAT_LAYERS.append(_build_clean_layer(
            lines, _G_LYRIC_FONT, BEAT_COLOR[:3], BEAT_COLOR[3] / 255.0))


def _build_clean_layer(lines, font, rgb, opacity):
    """纯色文字层：无飞白/晕染/溅点，仅保留柔和描边便于阅读。"""
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    stroke = max(1, int(font.size * 0.02))
    for ln, x, top, w, ih, bbox in _layout_text(lines, font):
        d.text((x, top), ln, font=font, fill=rgb + (255,), stroke_width=stroke, stroke_fill=rgb + (255,))
    return _scale_alpha(layer, opacity)


def _build_ink_layer(lines, font, rgb, opacity, seed, ink):
    """把文字渲染成带泼墨质感（飞白 + 晕染 + 浓淡 + 溅点）的 RGBA 层。"""
    blur_px = int(font.size * ink["blur"])
    splatter_r = max(1, int(font.size * ink["splatter"]))
    rng = random.Random(seed)

    # 1) 主体文字
    full = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(full)
    for ln, x, top, w, ih, bbox in _layout_text(lines, font):
        d.text((x, top), ln, font=font, fill=rgb + (255,))
    base_mask = full.getchannel("A")

    # 2) 飞白（干笔随机镂空）+ 浓淡变化
    marr = np.asarray(base_mask, dtype=np.float32)
    h, w = marr.shape
    dry = np.random.default_rng(seed + 1).random((h, w))
    marr *= (dry > ink["erase"]).astype(np.float32)
    density = 0.70 + 0.30 * np.random.default_rng(seed + 2).random((h, w))
    marr *= density
    marr = np.clip(marr, 0, 255)
    crisp_mask = Image.fromarray(marr.astype(np.uint8), mode="L")
    crisp = Image.new("RGBA", (WIDTH, HEIGHT), rgb + (0,))
    crisp.putalpha(crisp_mask)

    # 3) 晕染：把完整笔画高斯模糊后垫在底层，形成渗墨
    if blur_px > 0:
        wash_mask = base_mask.filter(ImageFilter.GaussianBlur(blur_px))
        wash_mask = wash_mask.point(lambda a: int(a * 0.30))
        wash = Image.new("RGBA", (WIDTH, HEIGHT), rgb + (0,))
        wash.putalpha(wash_mask)
    else:
        wash = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    layer = Image.alpha_composite(wash, crisp)

    # 4) 溅点
    if ink["speckles"]:
        ys, xs = np.nonzero(marr)
        if len(xs):
            sp = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sp)
            x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
            pad = splatter_r * 4
            for _ in range(ink["speckles"]):
                px = int(rng.uniform(x0 - pad, x1 + pad))
                py = int(rng.uniform(y0 - pad, y1 + pad))
                pr = rng.uniform(0.6, splatter_r)
                a = int(rng.uniform(25, 110))
                sd.ellipse([px - pr, py - pr, px + pr, py + pr],
                           fill=rgb + (a,))
            layer = Image.alpha_composite(layer, sp)

    return _scale_alpha(layer, opacity)


def render_frame(frame_no):
    img = _G_BG.copy() if _G_BG is not None else Image.new("RGB", (WIDTH, HEIGHT), BG)
    img = img.convert("RGBA")

    t = frame_no / FPS
    for layer, speed in _G_SCENE_LAYERS:
        shift = int((t * speed) % WIDTH)
        visible = layer.crop((shift, 0, shift + WIDTH, HEIGHT))
        img.alpha_composite(visible, (0, 0))

    if _G_MIST is not None:
        img.alpha_composite(_G_MIST)

    # 底层：灰色泼墨歌名铺底
    img.alpha_composite(_G_TITLE_LAYER)

    # 上层：在时间戳处快速切句。新句不提前出现，以较高初始不透明度入场，
    # 再用短促 ease-out 收稳；旧句只在末尾两三帧迅速退出，避免拖沓交叉淡化。
    active = []
    n = len(_G_SEGMENTS)
    for i, seg in enumerate(_G_SEGMENTS):
        start = seg["start"]
        end = seg["end"]
        is_last = i == n - 1
        dur = (end - start) if end is not None else 1e18
        attack = min(FADE_SEC, max(dur * 0.25, 1.0 / FPS))
        release = min(0.07, attack / 2.0, max(dur * 0.10, 1.0 / FPS))

        if t < start:
            continue
        if t < start + attack:
            a = 0.55 + 0.45 * _ease_out((t - start) / attack)
            active.append((i, a))
            continue

        if end is None or t <= end - release:
            active.append((i, 1.0))
            continue

        if is_last:
            active.append((i, 1.0))
            continue
        if t < end:
            a = 1.0 - _ease_out((t - (end - release)) / release)
            active.append((i, a))

    for i, a in active:
        layer = _scale_alpha(_G_LYRIC_LAYERS[i], a)
        img.alpha_composite(layer)

    # 默认不闪亮；仅显式使用 --beat 时才叠加强拍强调。
    e = _beat_emphasis(t)
    if e > 0 and _G_BEAT_LAYERS:
        for i, a in active:
            layer = _scale_alpha(_G_BEAT_LAYERS[i], a * e * EMPHASIS_STRENGTH)
            img.alpha_composite(layer)

    return img.convert("RGB").tobytes("raw", "RGB")


def render_chunk(args):
    start, count = args
    return [render_frame(i) for i in range(start, start + count)]


def main():
    global WIDTH, HEIGHT, FADE_SEC
    ap = argparse.ArgumentParser(description="把音乐 + 歌词转成黑底书法字宽屏视频")
    ap.add_argument("--music", required=True, help="音频文件 mp3/wav/m4a")
    ap.add_argument("--lyrics", help="歌词文件，LRC 格式 [mm:ss.xx]歌词；不填则用 --query 自动拉取")
    ap.add_argument("--query", help="自动拉歌词用的歌名（--lyrics 未指定时必填）")
    ap.add_argument("--artist", help="自动拉歌词用的歌手名")
    ap.add_argument("--lyric-source", choices=["auto", "lrclib", "netease"],
                    default="auto", help="歌词源；默认优先 lrclib 失败回退网易云")
    ap.add_argument("--lyric-out", help="把自动拉到的 LRC 同时存到这个文件")
    ap.add_argument("--out", default="output.mp4", help="输出 mp4 路径")
    ap.add_argument("--title", help="歌曲名，缺省取歌词文件/接口返回的歌名，其次音频文件名")
    ap.add_argument("--title-font", default=DEFAULT_FONTS[0])
    ap.add_argument("--lyric-font", default=DEFAULT_FONTS[1])
    ap.add_argument("--title-size", type=int, default=0, help="歌名字号上限（1080p 基准）；0 为原有自动适配")
    ap.add_argument("--lyric-size", type=int, default=0, help="歌词字号上限（1080p 基准）；0 为原有自动适配")
    ap.add_argument("--ink", choices=["dry", "normal", "heavy"], default="normal",
                    help="泼墨强度：dry 更枯/多点，heavy 更润/更晕")
    ap.add_argument("--fade-sec", type=float, default=FADE_SEC,
                    help="歌词快速入场时长（秒），默认 0.14")
    ap.add_argument("--max-lyric-chars", type=int, default=8,
                    help="每次显示的歌词最大字符数，默认 8；设 0 禁用自动拆句")
    ap.add_argument("--interlude-motion", choices=["off", "subtle"], default="off",
                    help="保留旧命令兼容性；统一模板为静态背景")
    ap.add_argument("--beat", action="store_true",
                    help="开启重拍强调：检测音频节拍，在重拍时让歌词更亮更浓")
    ap.add_argument("--beat-win", type=float, default=BEAT_WIN_SEC,
                    help="重拍强调脉冲半窗（秒），默认 0.22")
    ap.add_argument("--width", type=int, default=WIDTH, help="视频宽，默认 1920")
    ap.add_argument("--height", type=int, default=HEIGHT, help="视频高，默认 1080")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--renderer", choices=["cpu", "gpu"], default="cpu",
                    help="逐帧合成后端；gpu 使用 ModernGL/EGL")
    ap.add_argument("--encoder", choices=["auto", "libx264", "h264_nvenc"], default="auto",
                    help="视频编码器；auto 在 GPU 渲染时使用 NVENC")
    ap.add_argument("--audio-codec", choices=["aac", "flac", "alac"], default="aac",
                    help="音频编码；完整保真建议 FLAC 并输出 MKV")
    ap.add_argument("--workers", type=int, default=1,
                    help="渲染进程数，默认 1；水墨场景占用内存较大")
    ap.add_argument("--preview", help="只渲染一帧 PNG 预览后退出")
    ap.add_argument("--preview-time", type=float,
                    help="预览帧时间（秒）；默认取第一句中间")
    args = ap.parse_args()
    if not 0 <= args.title_size <= 1200 or not 0 <= args.lyric_size <= 1200:
        ap.error("字号必须在 0 到 1200 之间")

    WIDTH, HEIGHT = args.width, args.height

    title = args.title or Path(args.music).stem
    if args.lyrics:
        lyrics_text = Path(args.lyrics).read_text(encoding="utf-8")
    else:
        if not args.query:
            raise SystemExit("未指定 --lyrics 时，必须提供 --query 用于自动拉歌词")
        duration = ffprobe_duration(args.music)
        try:
            import lyric_fetcher
        except ImportError:
            raise SystemExit("找不到 lyric_fetcher.py，请把它和 make_video.py 放同一目录")
        lyrics_text, meta = lyric_fetcher.fetch_lyrics(
            args.query, getattr(args, "artist"), duration, args.lyric_source)
        if not args.title and meta.get("title"):
            title = meta["title"]
        print(f"已自动获取歌词：《{meta.get('title')}》- {meta.get('artist')}（{meta.get('source')}）")
        if args.lyric_out:
            _ensure_parent(args.lyric_out)
            Path(args.lyric_out).write_text(lyrics_text, encoding="utf-8")
            print(f"LRC 已保存：{args.lyric_out}")

    status = lyric_status(lyrics_text)
    if status in {"draft", "draft_asr", "text_corrected"}:
        raise SystemExit(
            f"歌词状态为 {status!r}，仍是待校对草稿。"
            "请试听并修正后运行 lyric_workflow.py approve，再生成视频。")

    auto_title, segments = parse_lyrics(lyrics_text)
    if not args.title and auto_title:
        title = auto_title

    duration = ffprobe_duration(args.music)
    for seg in segments:
        if seg["end"] is None:
            seg["end"] = duration
        seg["end"] = min(seg["end"], duration)
    segments = shorten_lyric_segments(segments, args.max_lyric_chars)

    title_font, lyric_font, title_size, lyric_size, title_max_w, lyric_max_w = \
        compute_layout(title, segments, args.title_font, args.lyric_font,
                       args.title_size, args.lyric_size)

    beats = []
    if args.beat:
        if detect_beats is None:
            raise SystemExit("重拍强调需要 beat_detect.py 与 numpy")
        try:
            beats, bpm = detect_beats(args.music)
            downs = detect_downbeats(beats, bpm) if detect_downbeats else []
            n_down = sum(1 for d in downs if d.get("level") == "down")
            print(f"节拍检测：{len(beats)} 个拍点，BPM≈{bpm:.1f}，强拍 {n_down} 个")
            beats = [(d["time"], d["level"]) for d in downs]
        except Exception as ex:
            print(f"节拍检测失败（按无节拍继续）：{type(ex).__name__}: {ex}")
            beats = []

    print(f"title={title!r} lyric_segments={len(segments)} duration={duration:.1f}s "
          f"size={WIDTH}x{HEIGHT} title_size={title_size} lyric_size={lyric_size}")

    title_ink = INK_PRESETS[args.ink]
    lyric_ink = INK_PRESETS[args.ink]
    FADE_SEC = args.fade_sec
    init = (title, segments, args.title_font, args.lyric_font,
            title_size, lyric_size, title_max_w, lyric_max_w,
            title_ink, lyric_ink, beats, args.beat_win)

    gpu = None
    if args.renderer == "gpu":
        try:
            from gpu_renderer import GPURenderer
            worker_init(*init)
            gpu = GPURenderer(
                WIDTH, HEIGHT, FPS, _G_BG, _G_SCENE_LAYERS, _G_MIST,
                _G_TITLE_LAYER, _G_LYRIC_LAYERS, _G_BEAT_LAYERS,
                segments, beats, FADE_SEC, args.beat_win,
                EMPHASIS_STRENGTH, BEAT_LEVEL_GAIN, args.interlude_motion)
            print(f"GPU 渲染器：{gpu.info['renderer']} ({gpu.info['version']})")
        except Exception as ex:
            raise SystemExit(f"GPU 渲染器初始化失败：{ex}") from ex

    if args.preview:
        _ensure_parent(args.preview)
        if gpu is None:
            worker_init(*init)
        seg = segments[0]
        mid = (args.preview_time if args.preview_time is not None else
               seg["start"] + max(0.2, (seg["end"] - seg["start"]) / 2))
        mid = max(0.0, min(mid, duration))
        frame = gpu.render(int(FPS * mid)) if gpu else render_frame(int(FPS * mid))
        img = Image.frombytes("RGB", (WIDTH, HEIGHT), frame)
        img.save(args.preview)
        if gpu:
            gpu.release()
        print(f"预览已保存: {args.preview}")
        return

    total = int(math.ceil(duration * FPS))
    print(f"渲染 {total} 帧 @ {WIDTH}x{HEIGHT} ...")

    _ensure_parent(args.out)
    encoder = args.encoder
    if encoder == "auto":
        encoder = "h264_nvenc" if args.renderer == "gpu" else "libx264"
    if encoder == "h264_nvenc":
        video_codec = ["-c:v", "h264_nvenc", "-preset", "p4",
                       "-cq", str(args.crf), "-b:v", "0"]
    else:
        video_codec = ["-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf)]
    if args.audio_codec == "flac":
        # FFmpeg 用 s32 容器承载 24-bit FLAC，避免源音频被自动降为 16-bit。
        audio_codec = ["-c:a", "flac", "-sample_fmt", "s32", "-compression_level", "8"]
    elif args.audio_codec == "alac":
        audio_codec = ["-c:a", "alac"]
    else:
        audio_codec = ["-c:a", "aac", "-b:a", "192k"]

    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}",
         "-r", str(FPS), "-i", "-",
         "-i", str(args.music),
         "-map", "0:v:0", "-map", "1:a:0", *video_codec,
         "-pix_fmt", "yuv420p",
         # 音频异常提前结束时补静音，以视频帧数为准收尾，避免 rawvideo 管道 BrokenPipe。
         "-af", "apad", *audio_codec,
         "-shortest", str(args.out)],
        stdin=subprocess.PIPE,
    )

    # 每帧约 WIDTH*HEIGHT*3 字节；小批次避免多个 worker 同时回传数 GB 数据。
    chunk = 1 if args.workers == 1 else 4
    try:
        if gpu:
            for frame_number in range(total):
                ffmpeg.stdin.write(gpu.render(frame_number))
                if (frame_number + 1) % 30 == 0 or frame_number + 1 == total:
                    print(f"  {int((frame_number + 1) / total * 100)}%", flush=True)
        else:
            with ProcessPoolExecutor(max_workers=max(1, args.workers),
                                     initializer=worker_init, initargs=init) as ex:
                for i, data_list in enumerate(ex.map(render_chunk,
                                                     ((i, min(chunk, total - i))
                                                      for i in range(0, total, chunk)))):
                    for data in data_list:
                        ffmpeg.stdin.write(data)
                    if (i + 1) * chunk % 60 == 0 or (i + 1) * chunk >= total:
                        pct = min(100, int(((i + 1) * chunk) / total * 100))
                        print(f"  {pct}%", flush=True)
        ffmpeg.stdin.close()
        ffmpeg.wait()
        if ffmpeg.returncode != 0:
            raise SystemExit("ffmpeg 合成失败")
    except BaseException:
        ffmpeg.kill()
        raise
    finally:
        if gpu:
            gpu.release()

    print(f"完成: {args.out}")


if __name__ == "__main__":
    main()
