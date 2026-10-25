"""The fixed black-gradient template shared by web, previews and production."""
import math

from font_catalog import FONT_FILES

TEMPLATE_ID = "black-gradient-v1"
DEFAULT_SETTINGS = {"title_font_id": "ma_shan_zheng", "lyric_font_id": "ma_shan_zheng",
                    "title_size": 0, "lyric_size": 0}


def normalize_settings(value=None, previous=None):
    result = {**DEFAULT_SETTINGS, **(previous or {})}
    value = value or {}
    if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTINGS):
        raise ValueError("字幕设置包含未知字段")
    result.update(value)
    for key in ("title_font_id", "lyric_font_id"):
        if result[key] not in FONT_FILES or not FONT_FILES[result[key]].is_file():
            raise ValueError("所选字体不可用")
    for key in ("title_size", "lyric_size"):
        size = result[key]
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size) or int(size) != size or not 0 <= size <= 1200:
            raise ValueError("字号必须为 0 到 1200 的整数；0 表示自动适配")
        result[key] = int(size)
    return result


def settings_from_design(design=None):
    design = design or {}
    legacy = design.get("font_id", DEFAULT_SETTINGS["lyric_font_id"])
    return normalize_settings({key: design.get(key, legacy if key.endswith("font_id") else 0)
                               for key in DEFAULT_SETTINGS})


def unified_design(title, artist, settings=None, previous=None):
    previous = previous or {}
    settings = normalize_settings(settings, settings_from_design(previous))
    return {"id": "a", "template_id": TEMPLATE_ID,
            "name": "黑色漸變・雙層字幕", "concept": "灰色歌名鋪底，暗紅歌詞居中疊放",
            "palette": ["#28282E", "#020204", "#8E1212"],
            "font_id": settings["lyric_font_id"], **settings,
            "ink": previous.get("ink", "normal"), "motion": "off",
            "transition": previous.get("transition", "quick_fade"),
            "fade_seconds": previous.get("fade_seconds", 0.14),
            "max_lyric_chars": previous.get("max_lyric_chars", 8),
            "cover_text": [title, artist], "video_title": f"{title}｜2K繁體書法歌詞",
            "description": f"{artist}《{title}》・黑色漸變雙層書法歌詞。",
            "tags": [artist, title, "繁體歌詞", "書法", "2K"]}
