#!/usr/bin/env python3
"""LRC 歌词的人工校对与审核工作流。"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TIMED_LINE = re.compile(r"^\s*\[(\d{1,3}):(\d{1,2}(?:\.\d+)?)\]\s*(.*?)\s*$")
STATUS_LINE = re.compile(r"^\s*\[status\s*:\s*([^\]]+)\]\s*$", re.I)


def fmt_time(seconds):
    seconds = max(0.0, float(seconds))
    minute = int(seconds // 60)
    return f"[{minute:02d}:{seconds - minute * 60:05.2f}]"


def audio_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def parse_timed_lines(text):
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        match = TIMED_LINE.match(line)
        if not match:
            continue
        timestamp = int(match.group(1)) * 60 + float(match.group(2))
        lyric = match.group(3).strip()
        if lyric:
            rows.append((number, timestamp, lyric))
    return rows


def validate_lrc(text, duration=None):
    rows = parse_timed_lines(text)
    errors = []
    if not rows:
        errors.append("没有找到带时间戳的非空歌词")
        return errors, rows
    for previous, current in zip(rows, rows[1:]):
        if current[1] <= previous[1]:
            errors.append(f"第 {current[0]} 行时间戳没有递增")
    if duration is not None:
        for number, timestamp, _ in rows:
            if timestamp > duration + 0.5:
                errors.append(f"第 {number} 行时间戳超过音频时长")
    return errors, rows


def replace_status(text, status):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if STATUS_LINE.match(line):
            lines[index] = f"[status:{status}]"
            break
    else:
        lines.insert(0, f"[status:{status}]")
    return "\n".join(lines) + "\n"


def command_approve(args):
    source = Path(args.lrc)
    text = source.read_text(encoding="utf-8")
    duration = audio_duration(args.audio) if args.audio else None
    errors, rows = validate_lrc(text, duration)
    if errors:
        raise SystemExit("歌词审核失败：\n- " + "\n- ".join(errors))
    output = Path(args.out) if args.out else source
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(replace_status(text, "approved"), encoding="utf-8")
    print(f"审核通过：{len(rows)} 句歌词，已写入 {output}")
    print("注意：自动审核只检查格式和时间范围；approved 表示你已完成人工试听确认。")


def main():
    parser = argparse.ArgumentParser(description="LRC 歌词人工审核工作流")
    sub = parser.add_subparsers(dest="command", required=True)

    approve = sub.add_parser("approve", help="人工试听修正后，将草稿标记为可渲染")
    approve.add_argument("lrc", help="已人工修正的草稿 LRC")
    approve.add_argument("--audio", help="可选，用于检查时间戳是否超出音频时长")
    approve.add_argument("--out", help="输出路径；默认原地更新")
    approve.set_defaults(func=command_approve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
