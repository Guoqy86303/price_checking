"""把一张 Pillow 图像当画布绘制的轻量工具。

玻璃拟态的关键是「模糊背景 + 低透明度白色叠加 + 1px 高光描边」。
这里的做法是：

* 背景（对角渐变 + 高斯模糊光斑 + 顶部/底部暗角）只生成一次；
* ``panel()`` 裁下自己背面的那块区域做高斯模糊，再叠白色 tint，
  所以面板是真正的磨砂玻璃，而不是假的半透明纯色；
* 文字/按钮/进度条画在独立的 RGBA 图层上再 ``alpha_composite``，
  半透明描边和文字才不会在转 RGB 时变成死白。

所有公开方法的坐标都是「逻辑像素」（96dpi 下的设计值），内部按 ``scale``
换算到设备像素，因此高分屏下不会糊。
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from . import theme

_PROBE = ImageDraw.Draw(Image.new("RGBA", (4, 4)))


def _rounded_mask(width: int, height: int, radius: float, supersample: int = 2) -> Image.Image:
    ss = max(1, int(supersample))
    mask = Image.new("L", (width * ss, height * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * ss - 1, height * ss - 1), radius=max(1, int(round(radius * ss))), fill=255
    )
    if ss > 1:
        mask = mask.resize((width, height), Image.LANCZOS)
    return mask


def _gradient(width: int, height: int) -> Image.Image:
    """先画一张 128x128 的小图再放大，几十毫秒搞定，边缘也够滑。"""
    size = 128
    small = Image.new("RGB", (size, size))
    pixels = small.load()
    stops = theme.BG_STOPS
    for y in range(size):
        ty = y / (size - 1)
        for x in range(size):
            t = (x / (size - 1)) * 0.55 + ty * 0.45
            for i in range(len(stops) - 1):
                t0, c0 = stops[i]
                t1, c1 = stops[i + 1]
                if t <= t1 or i == len(stops) - 2:
                    k = 0.0 if t1 == t0 else min(1.0, max(0.0, (t - t0) / (t1 - t0)))
                    pixels[x, y] = theme.lerp(c0, c1, k)
                    break
    return small.resize((width, height), Image.BICUBIC)


def _blobs(image: Image.Image) -> Image.Image:
    width, height = image.size
    # 在 1/4 分辨率上画+模糊，再放大，视觉几乎一致但快很多
    q = 4
    qw, qh = max(1, width // q), max(1, height // q)
    layer = Image.new("RGBA", (qw, qh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for fx, fy, fr, color, alpha in theme.BLOBS:
        radius = fr * qw
        cx, cy = fx * qw, fy * qh
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=theme.with_alpha(color, alpha * 255))
    layer = layer.filter(ImageFilter.GaussianBlur(max(2, int(qw * 0.07))))
    layer = layer.resize((width, height), Image.BILINEAR)
    return Image.alpha_composite(image, layer)


def _scrim(image: Image.Image) -> Image.Image:
    """上下压暗一点，保证白字在任何光斑上都够对比度。"""
    width, height = image.size
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    top = int(height * 0.55)
    for y in range(top):
        k = 1.0 - y / max(1, top)
        draw.line(((0, y), (width, y)), fill=(24, 14, 46, int(58 * k * k)))
    for y in range(int(height * 0.75), height):
        k = (y - height * 0.75) / max(1, height * 0.25)
        draw.line(((0, y), (width, y)), fill=(24, 14, 46, int(46 * k * k)))
    return Image.alpha_composite(image, layer)


class Surface:
    def __init__(self, width: float, height: float, scale: float = 1.0, dim: float = 0.0):
        self.scale = float(scale)
        self.width = float(width)
        self.height = float(height)
        self.w = max(1, int(round(width * self.scale)))
        self.h = max(1, int(round(height * self.scale)))
        base = _scrim(_blobs(_gradient(self.w, self.h).convert("RGBA")))
        if dim > 0:
            # 弹窗用：整体压暗，提高文字对比度（图库 README 提醒过复杂背景会降对比）
            overlay = Image.new("RGBA", base.size, (18, 12, 34, int(255 * min(0.85, float(dim)))))
            base = Image.alpha_composite(base, overlay)
        self.img = base

    # -- 基础 ------------------------------------------------------------
    def px(self, value: float) -> float:
        return float(value) * self.scale

    def clone(self) -> "Surface":
        other = object.__new__(Surface)
        other.scale = self.scale
        other.width = self.width
        other.height = self.height
        other.w = self.w
        other.h = self.h
        other.img = self.img.copy()
        return other

    def to_image(self) -> Image.Image:
        return self.img.convert("RGB")

    def _composite(self, layer: Image.Image, x0: int, y0: int) -> None:
        if x0 < 0:
            layer = layer.crop((-x0, 0, layer.width, layer.height))
            x0 = 0
        if y0 < 0:
            layer = layer.crop((0, -y0, layer.width, layer.height))
            y0 = 0
        if x0 + layer.width > self.w:
            layer = layer.crop((0, 0, self.w - x0, layer.height))
        if y0 + layer.height > self.h:
            layer = layer.crop((0, 0, layer.width, self.h - y0))
        if layer.width <= 0 or layer.height <= 0:
            return
        self.img.alpha_composite(layer, dest=(int(x0), int(y0)))

    # -- 玻璃面板 --------------------------------------------------------
    def panel(self, x, y, w, h, radius=16, blur=9, tint=theme.GLASS_TINT, stroke=theme.GLASS_STROKE, width=1.2):
        x0, y0 = int(round(self.px(x))), int(round(self.px(y)))
        w0, h0 = int(round(self.px(w))), int(round(self.px(h)))
        if w0 <= 2 or h0 <= 2:
            return
        x0 = max(0, min(x0, self.w - w0))
        y0 = max(0, min(y0, self.h - h0))
        region = self.img.crop((x0, y0, x0 + w0, y0 + h0))
        region = region.filter(ImageFilter.GaussianBlur(max(1.0, self.px(blur))))
        glass = Image.alpha_composite(region, Image.new("RGBA", (w0, h0), tint))
        glass.putalpha(_rounded_mask(w0, h0, self.px(radius), 2))
        self.img.alpha_composite(glass, dest=(x0, y0))
        if stroke is None:
            return
        # 描边单独 2x 超采样画，边缘才不毛刺
        ss = 2
        edge = Image.new("RGBA", (w0 * ss, h0 * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(edge)
        inset = max(1, int(round(self.px(width) * ss / 2)))
        draw.rounded_rectangle(
            (inset, inset, w0 * ss - 1 - inset, h0 * ss - 1 - inset),
            radius=max(1, int(round(self.px(radius) * ss))),
            outline=stroke,
            width=max(1, int(round(self.px(width) * ss))),
        )
        edge = edge.resize((w0, h0), Image.LANCZOS)
        self.img.alpha_composite(edge, dest=(x0, y0))

    # -- 形状 ------------------------------------------------------------
    def rect(self, x, y, w, h, radius=10, fill=None, stroke=None, width=1.2, ss=3):
        w0, h0 = int(round(self.px(w))), int(round(self.px(h)))
        if w0 <= 0 or h0 <= 0:
            return
        layer = Image.new("RGBA", (w0 * ss, h0 * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        inset = max(1, int(round(self.px(width) * ss / 2))) if stroke else 0
        draw.rounded_rectangle(
            (inset, inset, w0 * ss - 1 - inset, h0 * ss - 1 - inset),
            radius=max(1, int(round(self.px(radius) * ss))),
            fill=fill,
            outline=stroke,
            width=max(1, int(round(self.px(width) * ss))) if stroke else 1,
        )
        layer = layer.resize((w0, h0), Image.LANCZOS)
        self._composite(layer, int(round(self.px(x))), int(round(self.px(y))))

    def bar(self, x, y, w, h, ratio, c0, c1, track=(255, 255, 255, 40)):
        ratio = max(0.0, min(1.0, float(ratio)))
        self.rect(x, y, w, h, radius=h / 2, fill=track)
        if ratio <= 0.001:
            return
        fw = max(h, w * ratio)
        gw, gh = int(round(self.px(fw))), int(round(self.px(h)))
        if gw <= 0 or gh <= 0:
            return
        ramp = Image.new("RGB", (64, 1))
        pixels = ramp.load()
        for i in range(64):
            pixels[i, 0] = theme.lerp(c0, c1, i / 63)
        ramp = ramp.resize((gw, gh), Image.BILINEAR).convert("RGBA")
        ramp.putalpha(_rounded_mask(gw, gh, self.px(h / 2), 2))
        self._composite(ramp, int(round(self.px(x))), int(round(self.px(y))))

    # -- 文字 ------------------------------------------------------------
    def text(self, x, y, value, size, color=theme.TEXT, bold=False, mono=False, anchor="la"):
        value = str(value)
        font = theme.font(self.px(size), bold, mono)
        px, py = self.px(x), self.px(y)
        box = _PROBE.textbbox((px, py), value, font=font, anchor=anchor)
        pad = 3
        x0, y0 = int(box[0]) - pad, int(box[1]) - pad
        x1, y1 = int(box[2]) + pad, int(box[3]) + pad
        if x1 <= 0 or y1 <= 0 or x0 >= self.w or y0 >= self.h:
            return
        layer = Image.new("RGBA", (max(1, x1 - x0), max(1, y1 - y0)), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((px - x0, py - y0), value, font=font, fill=color, anchor=anchor)
        self._composite(layer, x0, y0)

    def text_width(self, value, size, bold=False, mono=False) -> float:
        font = theme.font(self.px(size), bold, mono)
        return float(_PROBE.textlength(str(value), font=font)) / self.scale
