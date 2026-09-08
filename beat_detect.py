#!/usr/bin/env python3
"""轻量节拍/重拍检测（仅依赖 numpy + ffmpeg）。

流程：
  1. ffmpeg 把音频解码成单声道 16bit PCM
  2. 分帧算 RMS（能量包络）
  3. 一阶差分 -> onset（能量突增处 = 拍点）
  4. 自适应阈值找局部峰 => beat times
  5. 可选：按时间聚类判定强拍（downbeat）

用法：
  python3 beat_detect.py 音乐.mp3
  python3 beat_detect.py 音乐.mp3 --out beats.txt
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np


def load_mono(path, sr=22050):
    """用 ffmpeg 解码为 float mono。返回 (samples, sr)。"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path),
             "-ac", "1", "-ar", str(sr), "-f", "f32le", str(tmp)],
            check=True, capture_output=True)
        data = np.fromfile(tmp, dtype=np.float32)
    finally:
        tmp.unlink(missing_ok=True)
    return data, sr


def rms_frames(audio, sr, hop=512, win=1024):
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32), hop / sr
    n_frames = 1 + max(0, (audio.size - win) // hop)
    if n_frames == 1:
        return np.asarray([np.sqrt(np.mean(audio ** 2))], dtype=np.float32), hop / sr
    idx = np.arange(n_frames)[:, None] * hop + np.arange(win)[None, :]
    idx = np.clip(idx, 0, audio.size - 1)
    frames = audio[idx]
    return np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12), hop / sr


def detect_beats(music, sr=22050, min_bpm=60, max_bpm=200):
    audio, sr = load_mono(music, sr=sr)
    rms, t_frame = rms_frames(audio, sr)
    if rms.size == 0:
        return [], np.zeros(0)

    # onset：能量突增，只看正向差分（攻击点）
    onset = np.diff(rms)
    onset = np.concatenate([[0.0], onset])
    onset = np.clip(onset, 0, None)

    # 自适应阈值（移动平均 + 全局噪声底）
    noise_floor = np.mean(rms) * 0.35
    threshold = onset * 0.0 + noise_floor * 0.25
    # 平滑阈值用卷积（避免直接 scipy）
    kernel = np.ones(8) / 8.0
    smooth = np.convolve(onset, kernel, mode="same")
    threshold = noise_floor * 0.35 + smooth * 1.6

    # 局部峰 + 最小间隔
    min_gap = max(2, int(60.0 / max_bpm / t_frame))
    candidates = []
    for i in range(1, onset.size - 1):
        if onset[i] > threshold[i] and onset[i] >= onset[i - 1] and onset[i] >= onset[i + 1]:
            t = (i * 512 + 512) / sr
            if candidates and t - candidates[-1] < 60.0 / max_bpm:
                if onset[i] > onset[int((t - (t - candidates[-1])) * sr // 512)]:
                    candidates[-1] = t
                continue
            candidates.append(t)

    # 基于 BPM 重采样，让节拍更规整
    if len(candidates) >= 2:
        ibi = np.diff(candidates)
        med = float(np.median(ibi[(ibi > 0.25) & (ibi < 2.0)])) if np.any((ibi > 0.25) & (ibi < 2.0)) else 0.5
        if med:
            bpm = 60.0 / med
        else:
            bpm = 120.0
    else:
        bpm = 120.0

    return list(candidates), bpm


def detect_downbeats(beats, bpm, bar_beats=4):
    """把拍点分级：强拍(down) / 次强拍(medium) / 弱拍(weak)。

    默认 4/4 拍：每小节第 1 拍 = 强拍，第 3 拍 = 次强拍，其余 = 弱拍。
    相位约定：把第一个检测到的拍点当作小节的第 1 拍（强拍）。
    """
    if not beats:
        return []
    out = []
    levels = {}
    for i, t in enumerate(beats):
        pos = i % bar_beats
        if pos == 0:
            level, strength = "down", 1.0
        elif pos == bar_beats // 2:
            level, strength = "medium", 0.62
        else:
            level, strength = "weak", 0.38
        out.append({"time": float(t), "level": level, "strength": float(strength)})
    return out


def main():
    ap = argparse.ArgumentParser(description="检测音频节拍/重拍")
    ap.add_argument("music", help="音频文件")
    ap.add_argument("--out", help="输出 json（默认打印 stdout）")
    ap.add_argument("--min-bpm", type=float, default=60)
    ap.add_argument("--max-bpm", type=float, default=200)
    args = ap.parse_args()

    beats, bpm = detect_beats(args.music, min_bpm=args.min_bpm, max_bpm=args.max_bpm)
    downs = detect_downbeats(beats, bpm)

    result = {
        "bpm": round(bpm, 2),
        "beats": [round(t, 3) for t in beats],
        "downbeats": downs,
    }

    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"已写入 {args.out}：共 {len(beats)} 个拍点，BPM≈{bpm:.1f}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
