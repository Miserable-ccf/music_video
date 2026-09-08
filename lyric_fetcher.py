#!/usr/bin/env python3
"""自动从网上拉 LRC 歌词（纯标准库，无第三方依赖）。

数据源优先级：
  1. lrclib.net —— 开放歌词库，无需 key，支持同步(synced)与纯文本(plain)歌词
  2. 网易云音乐 search / song lyric 接口
都失败时给出清晰报错，并提示可手动提供 LRC 文件。

用法：
  python3 lyric_fetcher.py "孤勇者" --artist "陈奕迅" --out lyrics.lrc
  python3 lyric_fetcher.py "孤勇者" --music 孤勇者.mp3 --out lyrics.lrc   # 用音频时长辅助匹配
  python3 lyric_fetcher.py "孤勇者" --search                              # 只列候选不落盘
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TIMEOUT = 15


class LyricNotFound(Exception):
    pass


def _get_json(url, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize(s):
    return re.sub(
        r"[\s\(\)\[\]（）【】·\-—_.,，。!！?？\"'‘’“”:：/\\|&+~#*=<>@$%^]+",
        "", s or "").lower()


def fmt_time(seconds):
    seconds = max(0.0, float(seconds))
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"[{m:02d}:{s:05.2f}]"


# ---------------- lrclib.net ----------------

def lrclib_search(query, artist=None):
    params = {"q": query.strip()}
    data = _get_json("https://lrclib.net/api/search", params)
    return data if isinstance(data, list) else []


def lrclib_get(track_id):
    return _get_json(f"https://lrclib.net/api/get/{int(track_id)}")


# ---------------- 网易云 ----------------

def netease_search(query):
    data = _get_json("https://music.163.com/api/search/get/web", {
        "s": query.strip(),
        "type": 1,
        "limit": 6,
        "offset": 0,
    })
    songs = data.get("result", {}).get("songs") or []
    return [{
        "id": s.get("id"),
        "trackName": s.get("name"),
        "artistName": (s.get("artists") or [{}])[0].get("name"),
        "albumName": (s.get("album") or {}).get("name"),
        "duration": (s.get("duration") or 0) / 1000 if s.get("duration") else None,
        "source": "netease",
        "syncedLyrics": None,
        "plainLyrics": None,
    } for s in songs]


def netease_lyric(song_id):
    data = _get_json("https://music.163.com/api/song/lyric", {
        "id": int(song_id),
        "lv": 1,
        "kv": 1,
        "tv": -1,
    })
    lrc = ((data.get("lrc") or {}).get("lyric")) or ""
    tly = ((data.get("tlyric") or {}).get("lyric")) or ""
    return lrc, tly


# ---------------- 候选打分与选取 ----------------

def _score(candidate, query, artist, duration):
    score = 0
    track = candidate.get("trackName") or ""
    cand_artist = candidate.get("artistName") or ""
    nq, nt = normalize(query), normalize(track)

    if nt and nq == nt:
        score += 100
    elif nt and (nq in nt or nt in nq):
        score += 55

    na, nc = normalize(artist), normalize(cand_artist)
    if artist and nc:
        if na == nc:
            score += 50
        elif na in nc or nc in na:
            score += 25

    cand_dur = candidate.get("duration") or 0
    if duration and cand_dur:
        diff = abs(float(cand_dur) - float(duration))
        if diff <= 2:
            score += 20
        elif diff <= 6:
            score += 8

    if candidate.get("syncedLyrics"):
        score += 10
    elif candidate.get("plainLyrics"):
        score += 3
    return score


def _pick(hits, query, artist=None, duration=None, min_score=55):
    best, best_score = None, -1
    for c in hits:
        s = _score(c, query, artist, duration)
        if s > best_score:
            best, best_score = c, s
    if best is None or best_score < min_score:
        return None
    return best


def plain_to_lrc(plain, duration=None):
    """把没有时间戳的纯文本歌词，按音频时长均摊生成时间戳。"""
    lines = [ln.strip() for ln in (plain or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    duration = float(duration) if duration else float(len(lines) * 4)
    step = duration / len(lines)
    return "\n".join(fmt_time(i * step) + ln for i, ln in enumerate(lines)) + "\n"


def _search_all(query, artist, source):
    hits = []
    errors = []
    if source in ("auto", "lrclib"):
        try:
            raw = lrclib_search(query, artist)
            hits += [{
                "id": c.get("id"),
                "trackName": c.get("trackName"),
                "artistName": c.get("artistName"),
                "albumName": c.get("albumName"),
                "duration": c.get("duration"),
                "syncedLyrics": c.get("syncedLyrics"),
                "plainLyrics": c.get("plainLyrics"),
                "source": "lrclib",
            } for c in raw]
        except Exception as e:
            if source == "lrclib":
                raise
            errors.append(f"lrclib: {type(e).__name__}")
    if source in ("auto", "netease"):
        try:
            hits += netease_search(query)
        except Exception as e:
            if source == "netease":
                raise
            errors.append(f"netease: {type(e).__name__}")
    if not hits and source == "auto" and errors:
        raise LyricNotFound(
            "所有歌词源都请求失败（可能是无网络/DNS 被限制/接口不可达；"
            "出错类型：" + ", ".join(errors) + "）。可换 --source 或手动提供 LRC。")
    return hits


def fetch_lyrics(query, artist=None, duration=None, source="auto"):
    """返回 (lyrics_text, meta)。歌词文本为标准 LRC 格式，可能由纯文本合成。"""
    hits = _search_all(query, artist, source)
    best = _pick(hits, query, artist, duration)

    if best is None:
        raise LyricNotFound(
            f"没有匹配到歌词：query={query!r} artist={artist!r}。"
            f"可换更精确的歌名/歌手名，或手动提供 LRC 文件。"
        )

    meta = {
        "title": best.get("trackName") or query,
        "artist": best.get("artistName") or artist or "",
        "album": best.get("albumName") or "",
        "source": best.get("source"),
    }

    if best.get("source") == "netease":
        lyrics, _tlyric = netease_lyric(best["id"])
        return lyrics, meta

    if best.get("syncedLyrics"):
        return best["syncedLyrics"], meta
    if best.get("plainLyrics"):
        return plain_to_lrc(best["plainLyrics"], duration), meta

    raise LyricNotFound("接口返回里没有歌词（可能是纯音乐被标记 instrumental，或版权限制）")


def _print_candidates(hits, query, artist, duration):
    print(f"候选（query={query!r} artist={artist!r}）:")
    for i, c in enumerate(hits, 1):
        synced = "LRC" if c.get("syncedLyrics") else ("文本" if c.get("plainLyrics") else "无")
        dur = f"{c.get('duration'):.0f}s" if c.get("duration") else "-"
        print(f"{i}. [{c.get('source')}] {c.get('trackName')} - {c.get('artistName')}"
              f"  ({c.get('albumName') or '-'}, {dur}, {synced})")


def main():
    ap = argparse.ArgumentParser(description="自动从网络拉取 LRC 歌词")
    ap.add_argument("query", help="歌名")
    ap.add_argument("--artist", help="歌手名（可提高准确率）")
    ap.add_argument("--music", help="可选：音频文件，用 ffprobe 读取时长辅助匹配")
    ap.add_argument("--duration", type=float, help="可选：音频时长（秒）")
    ap.add_argument("--source", choices=["auto", "lrclib", "netease"], default="auto")
    ap.add_argument("--out", default="lyrics/lyrics.lrc",
                    help="输出 LRC 路径，- 表示打印到 stdout")
    ap.add_argument("--search", action="store_true", help="只列出候选、不保存")
    args = ap.parse_args()

    duration = args.duration
    if args.music:
        try:
            import subprocess
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", args.music],
                capture_output=True, text=True, check=True)
            duration = float(out.stdout.strip())
        except Exception:
            pass

    if args.search:
        try:
            hits = _search_all(args.query, args.artist, args.source)
            _print_candidates(hits, args.query, args.artist, duration)
        except LyricNotFound:
            pass
        return

    try:
        lyrics, meta = fetch_lyrics(args.query, args.artist, duration, args.source)
    except LyricNotFound as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(2)

    title = meta["title"]
    print(f"已获取：{title} - {meta['artist']}（来源 {meta['source']}）")
    if args.out == "-":
        print(lyrics)
    else:
        out_path = args.out
        parent = out_path.rsplit("/", 1)[0] if "/" in out_path else "."
        if parent != ".":
            import os
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(lyrics)
        print(f"已写入：{args.out}")


if __name__ == "__main__":
    main()
