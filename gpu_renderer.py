"""ModernGL 离屏合成器：用 OpenGL 合成已由 Pillow 预生成的纹理。"""
import math
import struct
import subprocess


VERTEX_SHADER = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 uv;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    uv = in_uv;
}
"""


FRAGMENT_SHADER = """
#version 330
uniform sampler2D image_texture;
uniform vec2 uv_scale;
uniform vec2 uv_offset;
uniform float opacity;
in vec2 uv;
out vec4 frag_color;
void main() {
    vec4 color = texture(image_texture, uv * uv_scale + uv_offset);
    frag_color = vec4(color.rgb, color.a * opacity);
}
"""


def _ease(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _ease_out(x):
    x = max(0.0, min(1.0, x))
    return 1.0 - (1.0 - x) ** 3


class GPURenderer:
    """把背景、滚动山景和动态歌词全部作为 GPU 纹理进行透明合成。"""

    def __init__(self, width, height, fps, background, scene_layers, mist,
                 title_layer, lyric_layers, emphasis_layers, segments,
                 beats, fade_sec, beat_win, emphasis_strength,
                 beat_level_gain, interlude_motion="subtle"):
        try:
            import moderngl
        except ImportError as exc:
            raise RuntimeError(
                "GPU 渲染需要 moderngl：python3 -m pip install --user moderngl glcontext") from exc

        self.moderngl = moderngl
        self.width = width
        self.height = height
        self.fps = fps
        self.segments = segments
        self.beats = beats
        self.fade_sec = fade_sec
        self.beat_win = beat_win
        self.emphasis_strength = emphasis_strength
        self.beat_level_gain = beat_level_gain
        self.interlude_motion = interlude_motion

        try:
            self.ctx = moderngl.create_standalone_context(backend="egl")
        except Exception as exc:
            diagnostic = ""
            try:
                check = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)
                diagnostic = check.stdout + check.stderr
            except (OSError, subprocess.TimeoutExpired):
                pass
            if "Driver/library version mismatch" in diagnostic:
                raise RuntimeError(
                    "NVIDIA 驱动版本不一致（Driver/library version mismatch）："
                    "驱动更新后旧内核模块仍在运行。请保存工作并重启电脑，"
                    "确认 nvidia-smi 正常后重试渲染。") from exc
            raise RuntimeError(
                f"无法创建 NVIDIA EGL 上下文：{exc}；"
                "请先确认 nvidia-smi 正常，并在主机 GPU 环境运行") from exc

        self.output_texture = self.ctx.texture((width, height), 4)
        self.framebuffer = self.ctx.framebuffer(color_attachments=[self.output_texture])
        self.program = self.ctx.program(
            vertex_shader=VERTEX_SHADER, fragment_shader=FRAGMENT_SHADER)
        vertices = struct.pack(
            "16f",
            -1, -1, 0, 0,
             1, -1, 1, 0,
            -1,  1, 0, 1,
             1,  1, 1, 1,
        )
        self.vbo = self.ctx.buffer(vertices)
        self.vao = self.ctx.vertex_array(
            self.program, [(self.vbo, "2f 2f", "in_pos", "in_uv")])
        self.program["image_texture"].value = 0

        self.background = self._texture(background)
        self.scene_layers = [(self._texture(layer), speed) for layer, speed in scene_layers]
        self.mist = self._texture(mist)
        self.title = self._texture(title_layer)
        self.lyrics = [self._texture(layer) for layer in lyric_layers]
        self.emphasis = [self._texture(layer) for layer in emphasis_layers]

    @property
    def info(self):
        return {
            "vendor": self.ctx.info.get("GL_VENDOR", "unknown"),
            "renderer": self.ctx.info.get("GL_RENDERER", "unknown"),
            "version": self.ctx.info.get("GL_VERSION", "unknown"),
        }

    def _texture(self, image):
        rgba = image.convert("RGBA")
        texture = self.ctx.texture(rgba.size, 4, rgba.tobytes())
        texture.filter = (self.moderngl.LINEAR, self.moderngl.LINEAR)
        texture.repeat_x = False
        texture.repeat_y = False
        return texture

    def _draw(self, texture, opacity=1.0, scale=(1.0, 1.0), offset=(0.0, 0.0)):
        texture.use(0)
        self.program["opacity"].value = float(max(0.0, min(1.0, opacity)))
        self.program["uv_scale"].value = scale
        self.program["uv_offset"].value = offset
        self.vao.render(self.moderngl.TRIANGLE_STRIP)

    def _active(self, t):
        active = []
        count = len(self.segments)
        for index, segment in enumerate(self.segments):
            start, end = segment["start"], segment["end"]
            duration = end - start if end is not None else math.inf
            attack = min(self.fade_sec, max(duration * 0.25, 1.0 / self.fps))
            release = min(0.07, attack / 2.0, max(duration * 0.10, 1.0 / self.fps))
            if t < start:
                continue
            if t < start + attack:
                active.append((index, 0.55 + 0.45 * _ease_out((t - start) / attack)))
            elif end is None or t <= end - release or index == count - 1:
                active.append((index, 1.0))
            elif t < end:
                active.append((index, 1.0 - _ease_out((t - (end - release)) / release)))
        return active

    def _emphasis(self, t, active):
        value = 0.0
        for beat_time, level in self.beats:
            distance = abs(t - beat_time)
            if distance <= self.beat_win:
                gain = self.beat_level_gain.get(level, 1.0)
                value = max(value, _ease(gain * (1.0 - distance / self.beat_win)))
        return min(1.0, value)

    def _interlude(self, t):
        """返回长间奏的柔和包络和进度；头尾均平滑归零。"""
        if self.interlude_motion == "off":
            return 0.0, 0.0
        for segment in self.segments:
            start, end = segment["start"], segment["end"]
            if segment["text"].strip() or end is None or end - start < 3.0:
                continue
            if start <= t < end:
                progress = (t - start) / (end - start)
                envelope = math.sin(math.pi * progress) ** 2
                return envelope, progress
        return 0.0, 0.0

    def render(self, frame_number):
        t = frame_number / self.fps
        interlude, interlude_progress = self._interlude(t)
        self.framebuffer.use()
        self.ctx.viewport = (0, 0, self.width, self.height)
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)
        self.ctx.enable(self.moderngl.BLEND)
        self.ctx.blend_func = self.moderngl.SRC_ALPHA, self.moderngl.ONE_MINUS_SRC_ALPHA

        self._draw(self.background)
        for texture, speed in self.scene_layers:
            zoom = 1.0 - 0.02 * interlude
            shift = (t * speed * (1.0 + 0.12 * interlude)) % self.width
            scale = (0.5 * zoom, zoom)
            offset = (shift / (2.0 * self.width) + (0.5 - scale[0]) / 2.0,
                      (1.0 - zoom) / 2.0)
            self._draw(texture, scale=scale, offset=offset)
        mist_breath = 1.0 - 0.10 * interlude * (
            0.5 + 0.5 * math.sin(2.0 * math.pi * interlude_progress))
        self._draw(self.mist, mist_breath)
        self._draw(self.title)

        active = self._active(t)
        for index, alpha in active:
            self._draw(self.lyrics[index], alpha)
        emphasis = self._emphasis(t, active)
        if emphasis > 0.0:
            for index, alpha in active:
                self._draw(self.emphasis[index], alpha * emphasis * self.emphasis_strength)

        # 写入纹理与读取帧缓冲的 OpenGL 方向相反，两次翻转后与 Pillow 坐标一致。
        return self.framebuffer.read(components=3, alignment=1)

    def release(self):
        for texture in (self.background, self.mist, self.title, self.output_texture):
            texture.release()
        for texture, _ in self.scene_layers:
            texture.release()
        for texture in self.lyrics + self.emphasis:
            texture.release()
        self.vao.release()
        self.vbo.release()
        self.program.release()
        self.framebuffer.release()
        self.ctx.release()
