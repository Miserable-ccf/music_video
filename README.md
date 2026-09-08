# 音乐转黑底书法字幕视频

最小实现：`ffmpeg + Pillow`，把「音频 + LRC 歌词」渲染成宽屏视频（默认 1920x1080，可用 `--width` / `--height` 改成竖屏）。

- 背景：黑色渐变
- 底层：大号灰色泼墨 = 歌曲名（固定显示，马善政毛楷）
- 上层：中号暗红色 = 歌词（按时间戳滚动、带淡入淡出；马善政毛楷、纯色无泼墨、自动均衡断句）

## 目录结构

```text
music_video/
├── videos/      # 输出的 mp4 视频
├── previews/    # 预览 PNG（--preview 产物）
├── lyrics/      # LRC 歌词（lyric_fetcher.py 默认输出目录）
├── audio/       # 输入的音频文件
├── fonts/       # 内置中文字体
├── make_video.py
├── beat_detect.py
└── lyric_fetcher.py
```

## 依赖

```bash
python3 -m pip install Pillow
# ffmpeg / ffprobe 需在 PATH
```

## 用法

### 方式一：自动拉歌词（推荐）

有网时，只给音频 + 歌名即可，脚本会先从 lrclib.net 拉同步歌词，失败再回退网易云：

```bash
python3 make_video.py \
  --music 输入.mp3 \
  --query 孤勇者 \
  --artist 陈奕迅 \
  --lyric-out lyrics.lrc \
  --out videos/输出.mp4
```

`--query` 和 `--artist` 越精确，匹配越准；音频时长会参与候选打分。

### 方式二：手动指定 LRC

```bash
python3 make_video.py \
  --music 输入.mp3 \
  --lyrics 歌词.lrc \
  --out videos/输出.mp4 \
  --title 歌曲名
```

可选参数：`--title-font`、`--lyric-font`、`--ink {dry,normal,heavy}`、`--fade-sec`、`--beat`、
`--width`、`--height`、`--crf`、`--preview`、`--lyric-source {auto,lrclib,netease}`。

### 单独拉歌词 / 查看候选

```bash
python3 lyric_fetcher.py "孤勇者" --artist "陈奕迅" --out lyrics.lrc
python3 lyric_fetcher.py "孤勇者" --artist "陈奕迅" --search
python3 lyric_fetcher.py "孤勇者" --music 输入.mp3
```

`lyric_fetcher.py --out` 未指定时默认写入 `lyrics/lyrics.lrc`。

先出一帧预览看效果：

```bash
python3 make_video.py --music 输入.mp3 --lyrics 歌词.lrc --preview previews/preview.png
```

## 歌词格式

标准 LRC，一行一个时间戳（支持多行同名同时显示被忽略，取最后匹配行）：

```text
[00:00.00]歌曲名（可省略，用 --title 指定）
[00:01.20]第一句歌词
[00:05.40]第二句歌词
[00:09.80]第三句歌词
```

每两个时间戳之间即该句的显示时长，最后一句持续到音频结束。

## 一条命令批量生产

把多首歌写成 `list.txt`（每行：音频路径<TAB>歌名<TAB>歌手）：

```bash
while IFS=$'\t' read -r music q artist; do
  [ -z "$music" ] && continue
  python3 make_video.py --music "$music" --query "$q" --artist "$artist" \
    --out "videos/$(basename "$music" | sed 's/\.[^.]*$//').mp4"
done < list.txt
```

## 数据源说明

- **lrclib.net**：开放接口，优先使用，返回带时间戳的同步歌词时直接可用
- **网易云 music.163.com**：回退源，`search` + `/api/song/lyric`，无需登录，可能受版权/风控影响
- 两个源都失败时会明确提示「无网络/DNS 被限制/接口不可达」，不会误报成「没匹配到」

## 后续可扩展成 agent 的插件点

- 歌词兜底：QQ 音乐、Spotify 或 `Whisper` 本地转写
- 生图：纯黑底可换成 AI 生成背景，`render_frame` 里换 `BG` 即可
- 音频：加 BGM 混音、歌词高亮卡拉OK效果
- 调度：把 `lyric_fetcher.fetch_lyrics` / `make_video.main` 包成工具函数，接到任意 agent 循环里
