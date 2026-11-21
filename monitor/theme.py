"""玻璃拟态设计令牌。

配色与结构参考 frontend-style-gallery / ``gallery/06-glassmorphism``：
背景 ``#5546c7`` → ``#e07aaf`` → ``#f5b279`` 对角渐变，
模糊光斑 ``#ffcd6a`` / ``#5ddbd2``，面板为白色低透明度叠加 + 1px 高光描边。
"""

from __future__ import annotations

import os
from functools import lru_cache

from PIL import ImageFont

# --- 背景 ---------------------------------------------------------------
BG_STOPS = (
    (0.00, (85, 70, 199)),    # #5546c7
    (0.52, (224, 122, 175)),  # #e07aaf
    (1.00, (245, 178, 121)),  # #f5b279
)
# (相对 x, 相对 y, 半径比例, 颜色, 不透明度)
BLOBS = (
    (0.78, 0.08, 0.44, (255, 205, 106), 0.62),
    (0.06, 0.88, 0.36, (93, 219, 210), 0.50),
    (1.00, 0.60, 0.28, (124, 108, 240), 0.46),
)

# --- 玻璃 ---------------------------------------------------------------
GLASS_TINT = (255, 255, 255, 26)
GLASS_TINT_STRONG = (255, 255, 255, 40)
GLASS_STROKE = (255, 255, 255, 118)
GLASS_STROKE_HOVER = (255, 255, 255, 208)

WHITE = (255, 255, 255)

# --- 文字 ---------------------------------------------------------------
TEXT = (255, 255, 255, 255)
TEXT_DIM = (255, 255, 255, 176)
TEXT_FAINT = (255, 255, 255, 122)
INK = (43, 30, 76, 255)

# --- 强调色 -------------------------------------------------------------
ACCENT = (255, 205, 106, 255)
ACCENT_RGB = (255, 205, 106)
MINT = (152, 244, 210, 255)
MINT_RGB = (152, 244, 210)
CORAL = (255, 176, 166, 255)
CORAL_RGB = (255, 176, 166)

_FONTS = {
    ("sans", False): ("msyh.ttc", "msyh.ttf", "segoeui.ttf", "arial.ttf"),
    ("sans", True): ("msyhbd.ttc", "msyhbd.ttf", "segoeuib.ttf", "arialbd.ttf"),
    ("mono", False): ("consola.ttf", "CascadiaMono.ttf", "cour.ttf"),
    ("mono", True): ("consolab.ttf", "CascadiaMono.ttf", "courbd.ttf"),
}
_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


@lru_cache(maxsize=128)
def font(size: float, bold: bool = False, mono: bool = False):
    """按像素取字体，找不到就用默认位图字体兜底。"""
    for name in _FONTS[("mono" if mono else "sans", bold)]:
        path = os.path.join(_FONT_DIR, name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, max(6, int(round(size))))
            except OSError:
                continue
    return ImageFont.load_default()


def lerp(a, b, t: float):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def with_alpha(color, alpha) -> tuple:
    return (color[0], color[1], color[2], int(alpha))


def hex_str(color) -> str:
    return "#%02x%02x%02x" % (color[0], color[1], color[2])
