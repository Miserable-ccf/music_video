# 墨聲 · 本地音乐视频制作台

固定使用黑色渐变背景与两层居中叠放字幕：底层为半透明灰色歌名，上层为暗红色歌词。保留原有 LRC 时序、自动均衡断句、快速淡入淡出及画幅自适应布局。CPU 与 GPU 使用同一模板。

网页支持分别选择歌名与歌词的字体、字号，搜索字库，查看字体样张、五张实际关键帧并放大预览。设置随任务保存，刷新后恢复。已移除创意对话、多方案生成、云端封面生成和模型 API 配置。

## 启动

需要 Python 3.10+、FFmpeg / ffprobe：

```bash
python3 -m pip install -r requirements.txt
python3 web_app.py
```

打开 **http://127.0.0.1:8765/**。已有服务可运行 `bash restart_server.sh` 重启。

1. 上传音频，填写歌名与歌手。
2. 上传 LRC 或粘贴带时间戳的歌词，逐句试听后批准。同步歌词网络搜索为可选步骤。
3. 在「字幕模板」分别选择两层字体和字号，点击「储存设定并更新预览」。
4. 检查五张关键帧，生成本地 2K 封面，再渲染视频。
5. 检查视频并确认素材发布权利后，可生成 Bilibili 投稿包。

模板、排版、字体样张、封面和视频导出均在本地执行。准备好依赖与本地音频、LRC 后，断网也能完成整个制作流程；不需要模型、API Key 或在线字体服务。投稿到平台及可选歌词搜索需要网络。

## 字体和字号

内置 **58 个字体选项（包括不同字重及等宽变体）**，覆盖毛笔、行草、楷书、手写、篆书及少量艺术字：

- 正风毛笔、莫大毛笔：各 5 个字重。
- 霞鹜文楷繁体：3 个字重，含对应等宽版本。
- 清松手写体 1–9：含作者提供的比例变体。
- 芫荽、文鼎繁体楷书、cwTeX 仿古楷体。
- 辰宇落雁等宽及 2.0、内海及何某手写体各 5 个字重。
- 小赖手写及等宽版、悠哉手写 3 个字重、霞鹜篆书。
- 马善政、志莽行书、刘建毛草、龙藏手书、站酷庆科黄油及快乐体。

默认保留马善政字体与自动字号。两个字号字段均以 **1080p 像素**为基准，`0` 表示沿用原来的自动适配；手动值为字号上限，输出 1440p 时按 4/3 缩放，超出安全排版区域时自动缩小，确保字幕不被裁切。字色、居中位置、原有转场与断句规则不受字体设置影响。

每个字库的繁体覆盖范围不同。所选字体缺少的字由内置霞鹜文楷繁体补齐，并在预览状态中列出；两者都没有的字会提示更换文字或字体。篆书原库字数较少，普通繁体歌词会有较多字使用备用字体。

所有字库文件、授权、来源和 SHA-256 校验值见 [fonts/README.md](fonts/README.md) 与 [fonts/catalog.json](fonts/catalog.json)。网页提供本地授权文本链接。完整字体目录约 800 MB，移动项目时请保留整个 `fonts/`。

```bash
python3 tools/check_fonts.py
```

此命令离线核验字库文件、许可文件及 SHA-256，不会下载任何资源。收录的是本次从作者仓库核实并可随项目保存的字体，不代表穷尽互联网上所有免费字体。

## 命令行直接制作

```bash
python3 make_video.py --music audio/歌曲.flac --lyrics lyrics/歌曲.lrc \
  --title 歌名 --title-font fonts/MaShanZheng-Regular.ttf \
  --lyric-font fonts/traditional/Iansui/Iansui-Regular.ttf \
  --title-size 500 --lyric-size 180 --out videos/歌曲.mp4
```

省略字号或设为 `0` 可保留原有自动适配。默认 1920×1080，使用 `--width` / `--height` 调整画幅。

```bash
# 只生成一帧检查布局
python3 make_video.py --music audio/歌曲.flac --lyrics lyrics/歌曲.lrc \
  --title 歌名 --preview previews/歌曲.png
```

GPU 合成和 NVENC 为可选功能：

```bash
python3 -m pip install moderngl glcontext
python3 make_video.py --music audio/歌曲.flac --lyrics lyrics/歌曲.lrc \
  --renderer gpu --encoder h264_nvenc --out videos/歌曲.mp4
```

网页流水线会在 GPU 不可用时回退 CPU + libx264。需要无损母版时输出 MKV 并使用 `--audio-codec flac`；MP4 默认 AAC，也可用 ALAC。

## 可恢复的本地流水线

```bash
python3 lyric_workflow.py approve lyrics/歌曲.lrc --audio audio/歌曲.flac
python3 -m pipeline create --audio audio/歌曲.flac --title 歌名 --artist 歌手 \
  --lyrics lyrics/歌曲.lrc --job-id my-song
python3 -m pipeline template my-song --title-font-id masafont_regular \
  --lyric-font-id iansui --title-size 500 --lyric-size 180
python3 -m pipeline run my-song --dry-run
python3 -m pipeline run my-song
```

`template` 自动应用唯一模板、保存参数并生成五张关键帧，无需选择方案。`run` 生成 2560×1440 FLAC 音频 MKV 母版和 AAC 音频 MP4。

网页可生成使用同一字幕模板的 2K 封面；也可用 `set-cover` 导入自备的 2560×1440 封面。检查完成后：

```bash
python3 -m pipeline set-cover my-song --cover covers/封面.png
python3 -m pipeline approve my-song --copyright-confirmed
python3 -m pipeline package my-song
```

已有任务可以从网页歌曲列表恢复。首次保存新模板会沿用旧任务已选择的字体、断句和转场；重置按钮恢复马善政与自动字号。修改歌词或模板会取消旧输出和发布确认的登记，原输出文件仍保留，重新渲染后才可再次生成投稿包。

## LRC 格式

```text
[00:00.00]第一句歌詞
[00:05.40]第二句歌詞
[00:09.80]第三句歌詞
```

歌词在对应时间戳切入，最后一句持续至音频结束。默认长句拆成每段最多 8 个字符，并按字数分配原句显示时长，默认切入时间 0.14 秒；已有任务保留它原有的参数。`--max-lyric-chars 0` 可禁用拆句，`--fade-sec 0` 可直接切换。重拍提亮仅在显式使用 `--beat` 时启用。

## 检查

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --check web/app.js
```
