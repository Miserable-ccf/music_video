#!/usr/bin/env python3
"""本地音乐视频制作台。启动：python3 web_app.py"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from lyric_workflow import audio_duration, replace_status, validate_lrc
import lyric_fetcher
from pipeline import workflow
from pipeline.design import FONT_FILES, extract_lyric_samples, render_keyframes, render_preview
from audio_meta import detect_title_artist
from font_catalog import available_fonts, glyph_report, load_font, FONT_ROOT, CATALOG
from video_template import DEFAULT_SETTINGS, TEMPLATE_ID, normalize_settings, settings_from_design, unified_design

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
UPLOADS = ROOT / "jobs" / "_uploads"
app = FastAPI(title="墨声音乐视频制作台")
app.mount("/static", StaticFiles(directory=WEB), name="static")


def safe_job(job_id):
    if not job_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in job_id):
        raise HTTPException(400, "任务 ID 无效")
    try:
        return workflow.load(job_id)
    except SystemExit as exc:
        raise HTTPException(404, str(exc)) from exc


def error_response(exc):
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


def _prepare_lyrics(job_id):
    data = workflow.load(job_id)
    state = data.setdefault("lyric_preparation", {})
    state.update({"status": "searching", "message": "正在网络上搜索同步歌词"})
    current = workflow.load(job_id)
    current["lyric_preparation"] = state
    workflow.save(current)
    directory = workflow.job_path(job_id)
    draft = directory / "lyrics.draft.lrc"
    try:
        text, meta = lyric_fetcher.fetch_lyrics(
            data["title"], data["artist"], audio_duration(data["audio"]), "auto")
        draft.write_text(replace_status(text, "draft_fetched"), encoding="utf-8")
        state.update({"status": "ready", "source": meta.get("source", "network"),
                      "message": "已找到同步歌词，请试听并校正", "draft": str(draft)})
    except Exception as search_error:
        state.update({"status": "failed", "message": "未找到同步歌词或网络不可用，请直接上传 LRC 或粘贴带时间戳的歌词。",
                      "search_error": str(search_error)})
    current = workflow.load(job_id)
    current["lyric_preparation"] = state
    workflow.save(current)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


BUILD = {
    "commit": _git("rev-parse", "--short", "HEAD") or "unknown",
    "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
    "dirty": bool(_git("status", "--porcelain", "--untracked-files=no")),
    "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
}


@app.get("/api/version")
def version():
    return {"ok": True, **BUILD, "pid": os.getpid()}


@app.get("/api/config")
def config():
    return {"template_id": TEMPLATE_ID, "template_defaults": DEFAULT_SETTINGS,
            "offline_template": True}


@app.get("/api/fonts")
def fonts():
    return {"fonts": available_fonts(), "fallback_font_id": "lxgw_wenkai_tc"}


@app.get("/api/fonts/{font_id}/file")
def font_file(font_id: str):
    if font_id not in FONT_FILES or not FONT_FILES[font_id].is_file():
        raise HTTPException(404, "字体不可用")
    return FileResponse(FONT_FILES[font_id], media_type="font/ttf")


@app.get("/api/fonts/{font_id}/sample")
def font_sample(font_id: str, text: str = Query("於是我們繼續", max_length=80)):
    if font_id not in FONT_FILES:
        raise HTTPException(404, "字体不可用")
    from PIL import Image, ImageDraw
    font = load_font(FONT_FILES[font_id], 42)
    image = Image.new("RGB", (840, 80), (16, 16, 16))
    draw = ImageDraw.Draw(image)
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((8, (80 - box[3] + box[1]) / 2 - box[1]), text, font=font, fill=(210, 206, 195))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(buffer.getvalue(), media_type="image/png")


@app.get("/api/fonts/{font_id}/license")
def font_license(font_id: str):
    entry = next((font for font in CATALOG if font["id"] == font_id), None)
    if not entry:
        raise HTTPException(404)
    return FileResponse(FONT_ROOT / entry["license_file"], media_type="text/plain; charset=utf-8")


@app.get("/api/jobs/{job_id}/template")
def get_template(job_id: str):
    data = safe_job(job_id)
    design = data.get("selected_design") or {}
    return {"ok": True, "template_id": TEMPLATE_ID,
            "settings": normalize_settings(data.get("template_settings"), settings_from_design(design)),
            "ready": design.get("template_id") == TEMPLATE_ID,
            "previews": [f"/api/jobs/{job_id}/file/option-a-{i}.png" for i in range(1, 6)]
                        if design.get("template_id") == TEMPLATE_ID else []}


@app.put("/api/jobs/{job_id}/template")
async def save_template(job_id: str, request: Request):
    try:
        # Shared with render start so a settings save cannot overwrite RENDERING.
        with _RENDER_START_LOCK:
            data = safe_job(job_id)
            if data.get("status") == "RENDERING":
                raise ValueError("渲染中无法修改字幕设置，请等待完成")
            if not data.get("lyrics_approved") or not data.get("lyrics"):
                raise ValueError("请先试听并批准歌词")
        body = await request.json()
        settings = normalize_settings(body, data.get("template_settings") or settings_from_design(data.get("selected_design")))
        option = unified_design(data["title"], data["artist"], settings, data.get("selected_design"))
        lyric_text = "\n".join(extract_lyric_samples(data["lyrics"], count=1000000))
        reports = {"title": glyph_report(settings["title_font_id"], data["title"]),
                   "lyric": glyph_report(settings["lyric_font_id"], lyric_text)}
        if any(report["missing"] for report in reports.values()):
            raise ValueError("字体与备用字体均缺少以下字形：" + "".join(r["missing"] for r in reports.values()))
        with _RENDER_START_LOCK:
            data = safe_job(job_id)
            if data.get("status") == "RENDERING":
                raise ValueError("渲染中无法修改字幕设置，请等待完成")
            directory = workflow.job_path(job_id)
            frames = render_keyframes(option, directory / "design-previews", data["title"], data["lyrics"],
                                      float(data["audio_probe"]["format"]["duration"]))
            source = directory / "design-options.json"
            source.write_text(json.dumps({"provider": "local-template", "options": [option]}, ensure_ascii=False, indent=2), encoding="utf-8")
            data["design_options"] = str(source)
            data["template_settings"] = settings
            # A changed template invalidates old output approvals and cover registration.
            data["outputs"] = {}
            data["human_approved"] = False
            workflow.save(data)
            workflow.command_select_design(argparse.Namespace(job_id=job_id, option="a", font_id=None))
        return {"ok": True, "settings": settings, "job": workflow.load(job_id), "glyphs": reports,
                "frames": frames, "previews": [f"/api/jobs/{job_id}/file/{f['file']}" for f in frames]}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.post("/api/jobs")
async def create_job(request: Request, title: str, artist: str, filename: str):
    try:
        if not title.strip() or not artist.strip():
            raise ValueError("歌名和歌手不能为空")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg"}:
            raise ValueError("请选择常见音频文件")
        UPLOADS.mkdir(parents=True, exist_ok=True)
        destination = UPLOADS / f"{uuid.uuid4().hex}{suffix}"
        size = 0
        with destination.open("wb") as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise ValueError("音频文件超过 1 GiB 限制")
                stream.write(chunk)
        if not destination.stat().st_size:
            raise ValueError("音频文件为空")
        job_id = f"web-{uuid.uuid4().hex[:10]}"
        args = argparse.Namespace(audio=str(destination), title=title.strip(), artist=artist.strip(),
                                  lyrics=None, job_id=job_id)
        workflow.command_create(args)
        return {"ok": True, "job": workflow.load(job_id)}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.get("/api/design-jobs")
def design_jobs():
    result = []
    for path in workflow.JOBS.glob("*/manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("audio"):
                result.append({"id": data["job_id"], "title": data["title"],
                               "artist": data["artist"], "updated": data.get("updated_at", "")})
        except (OSError, ValueError, KeyError):
            continue
    return {"jobs": sorted(result, key=lambda item: item["updated"], reverse=True)}


@app.post("/api/jobs/{job_id}/lyrics")
async def upload_lyrics(job_id: str, request: Request, confirmed: bool = False):
    try:
        data = safe_job(job_id)
        text = (await request.body()).decode("utf-8-sig")
        errors, rows = validate_lrc(text, audio_duration(data["audio"]))
        if errors:
            raise ValueError("；".join(errors))
        if not confirmed:
            raise ValueError("必须勾选“已逐句试听确认”")
        path = workflow.job_path(job_id) / "lyrics.approved.lrc"
        path.write_text(replace_status(text, "approved"), encoding="utf-8")
        workflow.command_approve_lyrics(argparse.Namespace(job_id=job_id, lyrics=str(path)))
        return {"ok": True, "lines": len(rows), "job": workflow.load(job_id)}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.post("/api/jobs/{job_id}/lyrics/prepare")
def prepare_lyrics(job_id: str):
    try:
        data = safe_job(job_id)
        current = data.get("lyric_preparation", {}).get("status")
        if current == "searching":
            return {"ok": True, "status": current}
        threading.Thread(target=_prepare_lyrics, args=(job_id,), daemon=True).start()
        return {"ok": True, "status": "searching"}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.get("/api/jobs/{job_id}/lyrics/status")
def lyrics_status(job_id: str):
    data = safe_job(job_id)
    state = data.get("lyric_preparation", {"status": "idle", "message": "尚未开始"})
    result = {"ok": True, **state}
    if data.get("lyrics_approved") and data.get("lyrics"):
        result["text"] = Path(data["lyrics"]).read_text(encoding="utf-8")
    elif state.get("status") == "ready" and state.get("draft"):
        result["text"] = Path(state["draft"]).read_text(encoding="utf-8")
    return result


@app.post("/api/jobs/{job_id}/lyrics/approve")
async def approve_edited_lyrics(job_id: str, request: Request):
    try:
        data = safe_job(job_id)
        body = await request.json()
        if not body.get("confirmed"):
            raise ValueError("必须勾选“已逐句试听确认”")
        text = str(body.get("text", ""))
        errors, rows = validate_lrc(text, audio_duration(data["audio"]))
        if errors:
            raise ValueError("；".join(errors))
        path = workflow.job_path(job_id) / "lyrics.approved.lrc"
        path.write_text(replace_status(text, "approved"), encoding="utf-8")
        workflow.command_approve_lyrics(argparse.Namespace(job_id=job_id, lyrics=str(path)))
        return {"ok": True, "lines": len(rows), "job": workflow.load(job_id)}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.post("/api/jobs/{job_id}/cover")
def generate_cover(job_id: str):
    try:
        data = safe_job(job_id)
        design = data.get("selected_design")
        if not design:
            raise ValueError("请先储存字幕模板")
        cover = workflow.job_path(job_id) / "cover.png"
        render_preview(design, cover, data["title"], data["artist"], 2560, 1440)
        data.setdefault("outputs", {})["cover"] = str(cover)
        workflow.save(data)
        return {"ok": True, "cover_url": f"/api/jobs/{job_id}/file/cover.png"}
    except (Exception, SystemExit) as exc:
        return error_response(exc)

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return safe_job(job_id)


@app.get("/api/jobs/{job_id}/audio")
def get_audio(job_id: str):
    data = safe_job(job_id)
    return FileResponse(data["audio"])


@app.get("/api/jobs/{job_id}/file/{name}")
def get_file(job_id: str, name: str):
    safe_job(job_id)
    is_preview = bool(re.fullmatch(r"option-[abc](?:-[1-5])?\.png", name))
    if name != "cover.png" and not is_preview:
        raise HTTPException(404)
    path = (workflow.job_path(job_id) / name if name == "cover.png" else
            workflow.job_path(job_id) / "design-previews" / name)
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-store"})


def _render(job_id):
    try:
        workflow.command_run(argparse.Namespace(job_id=job_id, dry_run=False))
    except BaseException as exc:
        data = workflow.load(job_id)
        workflow.set_state(data, "FAILED", str(exc))


_RENDER_START_LOCK = threading.Lock()


@app.get("/api/jobs/{job_id}/render/progress")
def render_progress(job_id: str):
    data = safe_job(job_id)
    path = workflow.job_path(job_id) / "render-progress.json"
    progress = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "percent": 0, "stage": "等待渲染", "status": "idle"}
    if data["status"] == "FAILED":
        progress["status"] = "failed"
    return {"ok": True, **progress}


@app.post("/api/jobs/{job_id}/render")
def render(job_id: str):
    try:
        with _RENDER_START_LOCK:
            data = safe_job(job_id)
            if data.get("status") == "RENDERING":
                raise ValueError("任务正在渲染")
            if not data.get("lyrics_approved") or not data.get("selected_design"):
                raise ValueError("请先审核歌词并储存字幕模板")
            workflow.set_state(data, "RENDERING", "渲染任务已排入后台")
            path = workflow.job_path(job_id) / "render-progress.json"
            path.write_text(json.dumps({"percent": 0, "stage": "准备渲染",
                                      "status": "running", "elapsed_seconds": 0}),
                            encoding="utf-8")
            threading.Thread(target=_render, args=(job_id,), daemon=True).start()
        return {"ok": True, "message": "已开始后台渲染"}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.get("/api/jobs/{job_id}/export-name")
def get_export_name(job_id: str):
    data = safe_job(job_id)
    try:
        auto = workflow.auto_export_climax(data)
        return {"ok": True, "auto_climax": auto, "climax": data.get("export_climax") or auto,
                "name": workflow.export_name_for(data)}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.put("/api/jobs/{job_id}/export-name")
async def set_export_name(job_id: str, request: Request):
    try:
        body = await request.json()
        workflow.command_set_export_climax(argparse.Namespace(job_id=job_id, text=body.get("climax", "")))
        data = workflow.load(job_id)
        return {"ok": True, "climax": workflow.export_climax(data), "name": workflow.export_name_for(data)}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.post("/api/audio-meta")
async def audio_meta(request: Request, filename: str = Query("", max_length=300)):
    try:
        data = await request.body()
        if not data:
            raise ValueError("音频为空")
        return {"ok": True, **detect_title_artist(data, filename)}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


@app.post("/api/jobs/{job_id}/package")
async def package(job_id: str, request: Request):
    try:
        body = await request.json()
        if not body.get("copyright_confirmed"):
            raise ValueError("必须确认拥有发布所需权利")
        workflow.command_approve(argparse.Namespace(job_id=job_id, copyright_confirmed=True))
        workflow.command_package(argparse.Namespace(job_id=job_id))
        return {"ok": True, "path": str(workflow.job_path(job_id) / "publish-package")}
    except (Exception, SystemExit) as exc:
        return error_response(exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="本地固定模板音乐视频制作台")
    parser.add_argument("--port", type=int, default=8765, help="网页端口，默认 8765")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1 到 65535 之间")
    uvicorn.run(app, host="127.0.0.1", port=args.port)
