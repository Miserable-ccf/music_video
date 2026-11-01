"""从上传的音频里解析歌名和歌手：优先 ID3 标签，其次文件名。"""
import re


def _synchsafe(data):
    return (data[0] << 21) | (data[1] << 14) | (data[2] << 7) | data[3]


def _decode_text(raw, encoding):
    if encoding == 1 or encoding == 2:  # UTF-16（带或不带 BOM）
        return raw.decode("utf-16", errors="replace").strip("\x00 ")
    if encoding == 3:  # UTF-8
        return raw.decode("utf-8", errors="replace").strip("\x00 ")
    # ISO-8859-1：不少国内播放器写入的是 GBK 字节，先尝试还原
    try:
        text = raw.decode("latin-1")
        fixed = text.encode("latin-1").decode("gbk")
        return fixed.strip("\x00 ")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw.decode("latin-1", errors="replace").strip("\x00 ")


def read_id3_text_frames(data):
    """返回 {frame_id: 文本}，只读取 TIT2（歌名）和 TPE1（歌手）。"""
    frames = {}
    if len(data) < 10 or data[:3] != b"ID3":
        return frames
    version = data[3]
    size = _synchsafe(data[6:10])
    body = data[10:10 + size]
    pos = 0
    while pos + 10 <= len(body):
        frame_id = body[pos:pos + 4]
        if frame_id[0] == 0:
            break
        if version == 4:
            frame_size = _synchsafe(body[pos + 4:pos + 8])
        else:
            frame_size = int.from_bytes(body[pos + 4:pos + 8], "big")
        payload = body[pos + 10:pos + 10 + frame_size]
        pos += 10 + frame_size
        if frame_id in (b"TIT2", b"TPE1") and payload:
            frames[frame_id.decode("ascii")] = _decode_text(payload[1:], payload[0])
    # 个别文件的前面帧（如内嵌封面）长度字段有误，顺序解析会错位；按帧 ID 直接搜索兜底
    for frame_id in (b"TIT2", b"TPE1"):
        if frame_id.decode("ascii") in frames:
            continue
        at = body.find(frame_id)
        if at < 0 or at + 10 > len(body):
            continue
        if version == 4:
            frame_size = _synchsafe(body[at + 4:at + 8])
        else:
            frame_size = int.from_bytes(body[at + 4:at + 8], "big")
        payload = body[at + 10:at + 10 + frame_size]
        if payload and 0 < frame_size < 1024:
            frames[frame_id.decode("ascii")] = _decode_text(payload[1:], payload[0])
    return frames


def title_artist_from_filename(filename):
    """从文件名推断，例如 "08 - 歌名.mp3" 或 "歌手 - 歌名.mp3"。"""
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename.rsplit("/", 1)[-1]).strip()
    stem = re.sub(r"^\d{1,3}\s*[-.、_]\s*", "", stem).strip()
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip()
    return stem, ""


def detect_title_artist(data, filename):
    """数据为音频文件的前若干字节即可；标签缺失时回退到文件名。"""
    frames = read_id3_text_frames(data)
    title = frames.get("TIT2", "").strip()
    artist = frames.get("TPE1", "").strip()
    if not title or not artist:
        file_title, file_artist = title_artist_from_filename(filename)
        title = title or file_title
        artist = artist or file_artist
    return {"title": title, "artist": artist}
