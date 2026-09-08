import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
_PREVIEWS = _ROOT / "previews"
_PREVIEWS.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(_ROOT))

import make_video as mv

def _font(path):
    return str(_ROOT / path)

FONTS = [
    ("LiuJianMaoCao",  "fonts/LiuJianMaoCao-Regular.ttf",  "柳建矛草"),
    ("MaShanZheng",    "fonts/MaShanZheng-Regular.ttf",    "马善政毛楷"),
    ("ZhiMangXing",    "fonts/ZhiMangXing-Regular.ttf",    "之芒星手书"),
    ("LongCang",       "fonts/LongCang-Regular.ttf",       "龙沧行书"),
    ("ZCOOLQingKeHuangYou", "fonts/ZCOOLQingKeHuangYou-Regular.ttf", "站酷庆科黄油体"),
    ("ZCOOLKuaiLe",    "fonts/ZCOOLKuaiLe-Regular.ttf",    "站酷快乐体"),
]

TITLE = "人间铁骨"
segments = [
    {"start": 0.0, "end": 4.0, "text": "力拔山兮气盖世"},
    {"start": 4.0, "end": 8.0, "text": "长风万里送秋雁"},
]

def render_one(font_path, title, out_path):
    fp = _font(font_path)
    tf, lf, ts, ls, tw, lw = mv.compute_layout(title, segments, fp, fp)
    mv.worker_init(title, segments, fp, fp, ts, ls, tw, lw,
                   mv.INK_PRESETS["normal"], mv.INK_PRESETS["normal"])
    # 取第二句中间，歌词完整显示
    mid = 6.0
    raw = mv.render_frame(int(mv.FPS * mid))
    img = Image.frombytes("RGB", (mv.WIDTH, mv.HEIGHT), raw)
    img.save(out_path)
    return img

# 单图预览 + 缩小
thumbs = []
for key, path, name in FONTS:
    out = str(_PREVIEWS / f"preview_font_{key}.png")
    img = render_one(path, TITLE, out)
    img.resize((640, 360)).save(str(_PREVIEWS / f"preview_font_{key}_small.png"))
    thumbs.append((name, img.resize((640, 360))))
    print("done", key, name)

# 拼 3x2 对比大图
cols, rows = 3, 2
cw, ch = 650, 400
grid = Image.new("RGB", (cols * cw, rows * ch), (12, 12, 16))
gd = ImageDraw.Draw(grid)
label_font = ImageFont.truetype(_font("fonts/MaShanZheng-Regular.ttf"), 34)
for i, (name, img) in enumerate(thumbs):
    r, c = divmod(i, cols)
    grid.paste(img, (c * cw, r * ch))
    # 标签
    gd.rectangle([c * cw, r * ch, c * cw + cw - 1, r * ch + 46], fill=(20, 20, 26))
    gd.text((c * cw + 10, r * ch + 6), f"{i+1}. {name}", font=label_font, fill=(210, 210, 210))
grid.save(str(_PREVIEWS / "preview_fonts_grid.png"))
print("grid saved")
