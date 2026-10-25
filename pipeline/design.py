"""本地固定模板的关键帧与封面，复用成片渲染器。"""

from __future__ import annotations

import json
import re
import threading
from html import escape
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
from font_catalog import FONT_FILES
from video_template import settings_from_design

def render_preview(option, destination, title, artist, width=1280, height=720):
    """封面沿用成片模板与字体，歌名作为上层文字。"""
    render_effect_preview({**option, "max_lyric_chars": 0}, destination, title, title, 0, width, height)


_FRAME_LOCK = threading.Lock()


def render_keyframes(option, directory, title, lyrics_path, duration, width=1280, height=720):
    """复用成片布局和图层，抽取五个有歌词的时刻；不截断或改写歌词。"""
    import make_video as renderer
    _, segments = renderer.parse_lyrics(Path(lyrics_path).read_text(encoding="utf-8-sig"))
    for segment in segments:
        segment["end"] = min(segment["end"] if segment["end"] is not None else duration, duration)
    segments = renderer.shorten_lyric_segments(segments, option["max_lyric_chars"])
    available = [segment for segment in segments
                 if segment["text"].strip() and segment["end"] - segment["start"] > 0.1]
    if not available:
        raise ValueError("没有可预览的有效歌词，请先审核同步歌词")
    chosen = [available[round(i * (len(available) - 1) / 4)] for i in range(5)]
    settings = settings_from_design(option)
    title_font = str(FONT_FILES[settings["title_font_id"]])
    font = str(FONT_FILES[settings["lyric_font_id"]])
    directory.mkdir(parents=True, exist_ok=True)
    # 视频渲染器使用模块状态；串行初始化和绘制，避免并发换字体互相污染。
    with _FRAME_LOCK:
        renderer.WIDTH, renderer.HEIGHT = width, height
        renderer.FADE_SEC = 0 if option["transition"] == "cut" else option["fade_seconds"]
        _, _, ts, ls, tw, lw = renderer.compute_layout(
            title, segments, title_font, font, settings["title_size"], settings["lyric_size"])
        renderer.worker_init(title, chosen, title_font, font, ts, ls, tw, lw)
        result = []
        for index, segment in enumerate(chosen):
            moment = (segment["start"] + segment["end"]) / 2
            frame_no = round(moment * renderer.FPS)
            # 精确选择当前句，短歌重复取样也不会重复叠加同一句。
            renderer._G_SEGMENTS = [segment]
            layer = renderer._G_LYRIC_LAYERS[index]
            saved_layers = renderer._G_LYRIC_LAYERS
            renderer._G_LYRIC_LAYERS = [layer]
            pixels = renderer.render_frame(frame_no)
            renderer._G_LYRIC_LAYERS = saved_layers
            filename = f"option-{option['id']}-{index + 1}.png"
            Image.frombytes("RGB", (width, height), pixels).save(directory / filename)
            result.append({"file": filename, "time": frame_no / renderer.FPS,
                           "lyric": segment["text"]})
        (directory / f"option-{option['id']}-frames.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def render_effect_preview(option, destination, title, lyric, index, width=960, height=540):
    """兼容离线示意预览；实际任务使用 render_keyframes。"""
    import make_video as renderer
    with _FRAME_LOCK:
        renderer.WIDTH, renderer.HEIGHT = width, height
        segments = renderer.shorten_lyric_segments(
            [{"start": 0, "end": 10, "text": lyric}], option["max_lyric_chars"])
        settings = settings_from_design(option)
        title_font = str(FONT_FILES[settings["title_font_id"]])
        font = str(FONT_FILES[settings["lyric_font_id"]])
        renderer.FADE_SEC = 0 if option["transition"] == "cut" else option["fade_seconds"]
        _, _, ts, ls, tw, lw = renderer.compute_layout(
            title, segments, title_font, font, settings["title_size"], settings["lyric_size"])
        renderer.worker_init(title, segments, title_font, font, ts, ls, tw, lw)
        destination.parent.mkdir(parents=True, exist_ok=True)
        pixels = renderer.render_frame(round(segments[0]["end"] / 2 * renderer.FPS))
        Image.frombytes("RGB", (width, height), pixels).save(destination)


def extract_lyric_samples(path, count=5):
    if not path or not Path(path).is_file():
        return []
    lines = []
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        text = re.sub(r"^(?:\[[^\]]+\])+", "", raw).strip()
        if text and text not in lines:
            lines.append(text)
    if len(lines) <= count:
        return lines
    return [lines[round(index * (len(lines) - 1) / (count - 1))] for index in range(count)]


def write_gallery(options, directory, title, artist, lyric_samples=None, lyrics_path=None, duration=None):
    samples = list(lyric_samples or [])[:5]
    samples += [title] * (5 - len(samples))
    rows = []
    for item in options:
        filename = f"option-{item['id']}.png"
        render_preview(item, directory / filename, title, artist)
        if lyrics_path:
            render_keyframes(item, directory, title, lyrics_path, duration)
        frames = []
        for index, lyric in enumerate(samples):
            frame = f"option-{item['id']}-{index + 1}.png"
            if not lyrics_path:
                render_effect_preview(item, directory / frame, title, lyric, index)
            frames.append(f"<img src='{frame}'>")
        rows.append(f"<article><div class='frames'>{''.join(frames)}</div><h2>{item['id'].upper()} · {escape(item['name'])}</h2>"
                    f"<p>{escape(item['concept'])}</p><p>标题：{escape(item['video_title'])}</p>"
                    f"<code>python3 -m pipeline select-design JOB_ID {item['id']}</code></article>")
    html = """<!doctype html><meta charset='utf-8'><title>字幕模板预览</title>
<style>body{margin:40px;background:#111;color:#ddd;font:16px sans-serif}main{display:grid;gap:28px;grid-template-columns:repeat(auto-fit,minmax(360px,1fr))}article{background:#191919;padding:16px}.frames{display:grid;grid-template-columns:repeat(2,1fr);gap:5px}.frames img{width:100%}.frames img:first-child{grid-column:span 2}code{color:#d78b8b}</style>
<h1>字幕模板关键帧预览</h1><p>五张带歌词画面，沿用视频渲染器的暗红歌词、灰色歌名及自适应字号。</p><main>""" + "".join(rows) + "</main>"
    (directory / "index.html").write_text(html, encoding="utf-8")
