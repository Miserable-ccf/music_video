#!/usr/bin/env python3
"""音频到投稿包的确定性工作流；外部发布始终需要单独确认。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .design import FONT_FILES, write_gallery
from video_template import settings_from_design, unified_design

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "jobs"
STATES = {
    "CREATED", "NEEDS_LYRICS_REVIEW", "READY_FOR_DESIGN", "DESIGN_OPTIONS_READY",
    "DESIGN_SELECTED", "RENDERING",
    "READY_FOR_REVIEW", "APPROVED", "PUBLISH_PACKAGE_READY", "FAILED",
}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(path: Path):
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,sample_rate",
        "-of", "json", str(path),
    ], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def job_path(job_id):
    return JOBS / job_id


def load(job_id):
    path = job_path(job_id) / "manifest.json"
    if not path.exists():
        raise SystemExit(f"任务不存在：{job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(data):
    data["updated_at"] = now()
    directory = job_path(data["job_id"])
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "manifest.json.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(directory / "manifest.json")


def set_state(data, state, note=None):
    if state not in STATES:
        raise ValueError(state)
    data["status"] = state
    data.setdefault("history", []).append({"at": now(), "status": state, "note": note})
    save(data)


def approved_lrc(path: Path):
    text = path.read_text(encoding="utf-8")
    return "[status:approved]" in text.lower()


def command_create(args):
    audio = Path(args.audio).expanduser().resolve()
    if not audio.is_file():
        raise SystemExit(f"音频不存在：{audio}")
    digest = sha256(audio)
    job_id = args.job_id or f"{digest[:12]}-{datetime.now():%Y%m%d}"
    directory = job_path(job_id)
    if (directory / "manifest.json").exists():
        raise SystemExit(f"任务已存在：{job_id}（音频哈希可防止重复生产）")
    directory.mkdir(parents=True)
    lyrics = Path(args.lyrics).expanduser().resolve() if args.lyrics else None
    data = {
        "schema_version": 1, "job_id": job_id, "created_at": now(),
        "title": args.title, "artist": args.artist, "audio": str(audio),
        "audio_sha256": digest, "audio_probe": probe(audio),
        "lyrics": str(lyrics) if lyrics else None,
        "lyrics_approved": bool(lyrics and approved_lrc(lyrics)),
        "copyright_confirmed": False, "human_approved": False,
        "outputs": {}, "metadata": None,
    }
    state = "READY_FOR_DESIGN" if data["lyrics_approved"] else "NEEDS_LYRICS_REVIEW"
    set_state(data, state, "已导入；歌词必须人工确认后才允许渲染")
    print(job_id)


def command_approve_lyrics(args):
    data = load(args.job_id)
    if data.get("status") == "RENDERING":
        raise SystemExit("渲染中不能修改歌词")
    lyrics = Path(args.lyrics).expanduser().resolve()
    if not lyrics.is_file() or not approved_lrc(lyrics):
        raise SystemExit("歌词必须先通过 lyric_workflow.py approve 并包含 [status:approved]")
    data["lyrics"] = str(lyrics)
    data["lyrics_approved"] = True
    data["selected_design"] = None
    data["outputs"] = {}
    data["human_approved"] = False
    set_state(data, "READY_FOR_DESIGN", "已登记人工审核歌词")
    print("歌词已批准，可以设置字幕模板。")


def command_template(args):
    data = load(args.job_id)
    if not data.get("lyrics_approved"):
        raise SystemExit("停止：歌词尚未人工审核")
    previous = data.get("selected_design")
    updates = {key: getattr(args, key) for key in ("title_font_id", "lyric_font_id", "title_size", "lyric_size",
                                                   "title_color", "lyric_color")
               if getattr(args, key, None) is not None}
    option = unified_design(data["title"], data["artist"], updates, previous)
    directory = job_path(args.job_id)
    write_gallery([option], directory / "design-previews", data["title"], data["artist"],
                  lyrics_path=data["lyrics"], duration=float(data["audio_probe"]["format"]["duration"]))
    source = directory / "design-options.json"
    source.write_text(json.dumps({"provider": "local-template", "options": [option]}, ensure_ascii=False, indent=2), encoding="utf-8")
    data["design_options"] = str(source)
    data["template_settings"] = settings_from_design(option)
    data["outputs"] = {}
    data["human_approved"] = False
    save(data)
    command_select_design(argparse.Namespace(job_id=args.job_id, option="a", font_id=None))
    print(directory / "design-previews/index.html")


def command_select_design(args):
    data = load(args.job_id)
    source = data.get("design_options")
    if not source:
        raise SystemExit("请先运行 design 生成方案")
    options = json.loads(Path(source).read_text(encoding="utf-8"))["options"]
    selected = next((dict(item) for item in options if item["id"] == args.option.lower()), None)
    if not selected:
        raise SystemExit("方案只能选择 a、b 或 c")
    font_id = getattr(args, "font_id", None)
    if font_id:
        if font_id not in FONT_FILES or not FONT_FILES[font_id].is_file():
            raise SystemExit("所选字体不可用")
        selected["font_id"] = font_id
        selected["title_font_id"] = font_id
        selected["lyric_font_id"] = font_id
    data["selected_design"] = selected
    data["template_settings"] = settings_from_design(selected)
    data["metadata"] = {
        "title": selected["video_title"], "description": selected["description"],
        "tags": selected["tags"], "cover_text": selected["cover_text"],
        "risk_flags": ["需人工确认音乐、歌词、字体与图片的发布授权"],
    }
    directory = job_path(args.job_id)
    (directory / "metadata.json").write_text(
        json.dumps(data["metadata"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    set_state(data, "DESIGN_SELECTED", f"已选择方案 {selected['id']}：{selected['name']}")
    (directory / "render-progress.json").write_text(json.dumps(
        {"status": "idle", "percent": 0, "stage": "字幕设置已更新，等待渲染"}, ensure_ascii=False), encoding="utf-8")
    print(f"已选择 {selected['id'].upper()}：{selected['name']}")


def render_command(data, output, audio_codec):
    design = data["selected_design"]
    settings = settings_from_design(design)
    return [sys.executable, str(ROOT / "make_video.py"), "--music", data["audio"],
            "--lyrics", data["lyrics"], "--title", data["title"],
            "--width", "2560", "--height", "1440", "--renderer", "gpu",
            "--encoder", "h264_nvenc", "--crf", "16",
            "--title-font", str(FONT_FILES[settings["title_font_id"]]),
            "--lyric-font", str(FONT_FILES[settings["lyric_font_id"]]),
            "--title-size", str(settings["title_size"]),
            "--lyric-size", str(settings["lyric_size"]),
            "--title-color", settings["title_color"], "--lyric-color", settings["lyric_color"],
            "--ink", design["ink"], "--fade-sec",
            "0" if design["transition"] == "cut" else str(design["fade_seconds"]),
            "--max-lyric-chars", str(design["max_lyric_chars"]),
            "--interlude-motion", "off",
            "--audio-codec", audio_codec, "--out", str(output)]


def command_run(args):
    data = load(args.job_id)
    if not data.get("lyrics_approved") or not data.get("lyrics"):
        raise SystemExit("停止：歌词尚未人工审核。先执行 approve-lyrics。")
    if not data.get("selected_design"):
        raise SystemExit("停止：请先运行 template 设置字幕模板。")
    directory = job_path(args.job_id)
    master = directory / "master-2k-lossless.mkv"
    publish = directory / "publish-2k.mp4"
    commands = [render_command(data, master, "flac"), render_command(data, publish, "aac")]
    if args.dry_run:
        print("\n".join(shlex.join(command) for command in commands))
        save(data)
        return
    set_state(data, "RENDERING")
    started = time.time()
    progress = {"percent": 0, "stage": "准备渲染", "started_at": started,
                "status": "running"}
    def report(**updates):
        progress.update(updates)
        progress["elapsed_seconds"] = round(time.time() - started)
        path = directory / "render-progress.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    report()
    try:
        with (directory / "render.log").open("a", encoding="utf-8") as log:
            use_cpu = False
            for index, original_command in enumerate(commands):
                command = original_command.copy()
                stage = ["生成无损母版（1/2）", "生成投稿视频（2/2）"][index]
                for attempt in range(2):
                    if use_cpu and "--renderer" in command:
                        command[command.index("--renderer") + 1] = "cpu"
                        command[command.index("--encoder") + 1] = "libx264"
                    report(stage=stage + (" · CPU 兜底" if use_cpu else " · GPU"),
                           percent=index * 49, renderer="cpu" if use_cpu else "gpu")
                    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, bufsize=1,
                                          env={**os.environ, "PYTHONUNBUFFERED": "1"}) as process:
                        recent_lines = []
                        for line in process.stdout:
                            recent_lines = (recent_lines + [line.strip()])[-8:]
                            log.write(line)
                            log.flush()
                            match = re.fullmatch(r"\s*(\d+)%\s*", line)
                            if match:
                                report(percent=round(index * 49 + min(100, int(match[1])) * .49, 1))
                        returncode = process.wait()
                    if returncode == 0:
                        break
                    detail = next((line for line in reversed(recent_lines)
                                   if line and not re.fullmatch(r"\d+%", line)), "子进程异常退出")
                    gpu_command = "--renderer" in command and command[command.index("--renderer") + 1] == "gpu"
                    if gpu_command and not use_cpu:
                        use_cpu = True
                        log.write(f"GPU 渲染失败，自动切换 CPU + libx264，重新生成当前输出：{detail}\n")
                        log.flush()
                        report(fallback_reason=detail[:800])
                        continue
                    raise RuntimeError(f"渲染失败：{detail[:800]}（详情见 render.log）")
        report(percent=99, stage="检查视频完整性")
        for output in (master, publish):
            details = probe(output)
            duration = float(details["format"]["duration"])
            if abs(duration - float(data["audio_probe"]["format"]["duration"])) > 0.5:
                raise RuntimeError(f"音视频时长偏差过大：{output}")
        data["outputs"].update({"master": str(master), "publish": str(publish)})
        set_state(data, "READY_FOR_REVIEW", "渲染及基础 ffprobe 质检通过")
        report(percent=100, stage="渲染完成", status="complete")
    except Exception as exc:
        report(stage=str(exc), status="failed")
        set_state(data, "FAILED", str(exc))
        raise


def command_approve(args):
    data = load(args.job_id)
    if data["status"] != "READY_FOR_REVIEW":
        raise SystemExit("只有完成渲染和质检的任务才能批准")
    if not data.get("outputs", {}).get("cover"):
        raise SystemExit("必须先执行 set-cover 登记并检查 2560×1440 封面")
    if not args.copyright_confirmed:
        raise SystemExit("必须显式传入 --copyright-confirmed，确认你拥有发布音频等素材的权利")
    data["copyright_confirmed"] = True
    data["human_approved"] = True
    set_state(data, "APPROVED", "人工确认视频、封面文案及版权")
    print("已批准，可生成投稿包。")


def command_set_cover(args):
    data = load(args.job_id)
    source = Path(args.cover).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"封面不存在：{source}")
    details = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", str(source),
    ], capture_output=True, text=True, check=True)
    stream = json.loads(details.stdout).get("streams", [{}])[0]
    if (stream.get("width"), stream.get("height")) != (2560, 1440):
        raise SystemExit("封面必须是 2560×1440；请先生成或缩放为精确的 16:9 2K 尺寸")
    target = job_path(args.job_id) / "cover.png"
    shutil.copy2(source, target)
    data.setdefault("outputs", {})["cover"] = str(target)
    save(data)
    print(target)

def auto_export_climax(data):
    """默认取音量最高的一句歌词，作为导出文件名的第一段。"""
    if not data.get("lyrics") or not data.get("audio"):
        return ""  # 歌词尚未审核时没有可选句，文件名只用歌名和歌手
    import make_video as renderer
    _, segments = renderer.parse_lyrics(Path(data["lyrics"]).read_text(encoding="utf-8-sig"))
    return renderer.climax_segment(segments, data["audio"])


def export_climax(data):
    """人工修改过的句子优先；否则使用自动选出的最高潮句。"""
    return data.get("export_climax") or auto_export_climax(data)


def export_name_for(data):
    import make_video as renderer
    return renderer.export_name(export_climax(data), data["title"], data["artist"])


def command_set_export_climax(args):
    data = load(args.job_id)
    text = (args.text or "").strip()
    if len(text) > 80 or "\n" in text or "\r" in text:
        raise SystemExit("导出用句需为单行，且不超过 80 字")
    data["export_climax"] = text
    save(data)
    print(export_name_for(data))


def command_package(args):
    data = load(args.job_id)
    if data["status"] != "APPROVED":
        raise SystemExit("停止：必须先人工 approve，流水线不会绕过发布确认")
    directory = job_path(args.job_id)
    package = directory / "publish-package"
    package.mkdir(exist_ok=True)
    name = export_name_for(data)
    (package / "video.mp4").unlink(missing_ok=True)
    shutil.copy2(data["outputs"]["publish"], package / f"{name}.mp4")
    data["export_name"] = name
    shutil.copy2(directory / "metadata.json", package / "metadata.json")
    cover = Path(data["outputs"]["cover"])
    shutil.copy2(cover, package / "cover.png")
    checklist = """# Bilibili 投稿检查\n\n- [ ] 视频与封面预览无误\n- [ ] 标题、简介、标签无杜撰\n- [ ] 音乐、歌词、字体和图片拥有发布授权\n- [ ] 分区及自制/转载声明选择正确\n- [ ] 在创作中心完成最终提交\n"""
    (package / "CHECKLIST.md").write_text(checklist, encoding="utf-8")
    set_state(data, "PUBLISH_PACKAGE_READY", "投稿包已生成；尚未向外部平台提交")
    print(package)


def command_status(args):
    print(json.dumps(load(args.job_id), ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="音频→2K 歌词视频→投稿包工作流")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--audio", required=True); create.add_argument("--title", required=True)
    create.add_argument("--artist", required=True); create.add_argument("--lyrics")
    create.add_argument("--job-id"); create.set_defaults(func=command_create)
    lyrics = sub.add_parser("approve-lyrics")
    lyrics.add_argument("job_id"); lyrics.add_argument("--lyrics", required=True)
    lyrics.set_defaults(func=command_approve_lyrics)
    design = sub.add_parser("template", aliases=["design"], help="应用固定黑色渐变双层字幕模板")
    design.add_argument("job_id")
    design.add_argument("--title-font-id", choices=sorted(FONT_FILES))
    design.add_argument("--lyric-font-id", choices=sorted(FONT_FILES))
    design.add_argument("--title-size", type=int)
    design.add_argument("--lyric-size", type=int)
    design.add_argument("--title-color")
    design.add_argument("--lyric-color")
    design.set_defaults(func=command_template)
    select = sub.add_parser("select-design")
    select.add_argument("job_id"); select.add_argument("option", choices=["a", "b", "c"])
    select.add_argument("--font-id", choices=sorted(FONT_FILES), help="覆盖方案默认字体")
    select.set_defaults(func=command_select_design)
    run = sub.add_parser("run")
    run.add_argument("job_id"); run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=command_run)
    cover = sub.add_parser("set-cover")
    cover.add_argument("job_id"); cover.add_argument("--cover", required=True)
    cover.set_defaults(func=command_set_cover)
    approve = sub.add_parser("approve")
    approve.add_argument("job_id"); approve.add_argument("--copyright-confirmed", action="store_true")
    approve.set_defaults(func=command_approve)
    package = sub.add_parser("package")
    package.add_argument("job_id"); package.set_defaults(func=command_package)
    status = sub.add_parser("status")
    status.add_argument("job_id"); status.set_defaults(func=command_status)
    args = parser.parse_args()
    args.func(args)
