"""设置对话框：玻璃卡片背景 + 真实输入控件。

做法：
1. 背景、卡片、标题用 Pillow 渲染；
2. 每个输入位先画一个圆角「玻璃凹槽」，并记住它的平涂颜色；
3. 真正的 ``Entry`` / ``Checkbutton`` 贴着凹槽放上去，底色用凹槽颜色，
   所以输入框看起来是长在玻璃里的，而不是贴了一块灰方块。

保存用 ``config.merge`` 深度合并（手写字段不会被清掉），改完立即
``apply_config``，不用重启。
"""

from __future__ import annotations

import logging
import os
import tkinter as tk
from typing import Optional

from PIL import ImageTk

from . import config as config_mod
from . import paths, theme, win
from .render import Surface

log = logging.getLogger(__name__)

PAD = 22
WIDTH = 470
FIELD_H = 26
BUTTON_H = 42


class SettingsDialog:
    def __init__(self, parent, monitor, on_saved=None):
        self.monitor = monitor
        self.on_saved = on_saved
        self._error: Optional[str] = None
        self._hover: Optional[str] = None
        self._photo = None
        self._regions: list = []

        self.dlg = tk.Toplevel(parent)
        self.dlg.title("设置")
        self.dlg.configure(bg="#2b2350")
        self.dlg.transient(parent)
        self.dlg.resizable(False, False)
        try:
            self.scale = float(self.dlg.winfo_fpixels("1i")) / 96.0
        except Exception:
            self.scale = 1.0
        self.scale = round(min(2.0, max(1.0, self.scale)), 2)

        self._plan()
        self.dlg.geometry(f"{self._px(WIDTH)}x{self._px(self._height)}")
        self.canvas = tk.Canvas(self.dlg, highlightthickness=0, bd=0, bg="#2b2350")
        self.canvas.place(x=0, y=0, width=self._px(WIDTH), height=self._px(self._height))
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)

        self._draw()
        self._build_widgets()
        self.dlg.bind("<Escape>", lambda _event: self.close())
        self.dlg.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self.dlg.grab_set()
        except tk.TclError:
            pass
        win.apply_theme(self.dlg)
        self.dlg.after(60, lambda: self.dlg.focus_force())

    # -- 坐标 ------------------------------------------------------------
    def _px(self, value: float) -> int:
        return int(round(value * self.scale))

    def _plan(self) -> None:
        cfg = self.monitor.config
        self._v_interval = tk.StringVar(value=str(cfg.get("polling_interval", 60)))
        self._v_total = tk.StringVar(value=_fmt(cfg.get("total_spend_alert", 100)))
        self._v_cooldown = tk.StringVar(value=str(cfg.get("alert_cooldown", 1800)))

        global_y = 72
        global_h = 150
        self._blocks: list = [
            {
                "kind": "global",
                "title": "全局设置",
                "y": global_y,
                "h": global_h,
                "rows": [
                    ("轮询间隔（秒）", self._v_interval, global_y + 36),
                    ("总花费警告阈值", self._v_total, global_y + 70),
                    ("告警冷却（秒）", self._v_cooldown, global_y + 104),
                ],
            }
        ]
        self._inputs: list = []
        self._checks: list = []

        for _label, var, row_y in self._blocks[0]["rows"]:
            self._inputs.append(
                {"x": WIDTH - PAD - 16 - 170, "y": row_y, "w": 170, "var": var, "justify": "right"}
            )

        y = global_y + global_h + 12
        providers_cfg = cfg.get("providers") or {}
        from .pricing import PROVIDER_CLASSES

        for name, spec in providers_cfg.items():
            cls = PROVIDER_CLASSES.get(name)
            label = getattr(cls, "display_name", name) if cls else name
            extra = bool(getattr(cls, "requires_initial_balance", False)) or "initial_balance" in spec
            height = 116 + (34 if extra else 0)
            block = {
                "kind": "provider",
                "name": name,
                "title": label,
                "y": y,
                "h": height,
                "enabled": tk.BooleanVar(value=bool(spec.get("enabled", False))),
                "key": tk.StringVar(value=str(spec.get("api_key") or "")),
                "balance_alert": tk.StringVar(value=_fmt(spec.get("balance_alert", 10))),
                "spend_alert": tk.StringVar(value=_fmt(spec.get("spend_alert", 50))),
                "initial_balance": (
                    tk.StringVar(value=_fmt(spec.get("initial_balance", 0))) if extra else None
                ),
                "estimate": bool(getattr(cls, "balance_is_estimate", False)),
            }
            self._blocks.append(block)
            self._inputs.append(
                {"x": PAD + 16, "y": y + 46, "w": WIDTH - 2 * PAD - 32, "var": block["key"]}
            )
            self._inputs.append(
                {"x": PAD + 92, "y": y + 78, "w": 86, "var": block["balance_alert"], "justify": "right"}
            )
            self._inputs.append(
                {"x": PAD + 292, "y": y + 78, "w": 118, "var": block["spend_alert"], "justify": "right"}
            )
            if block["initial_balance"] is not None:
                self._inputs.append(
                    {"x": PAD + 92, "y": y + 112, "w": 86,
                     "var": block["initial_balance"], "justify": "right"}
                )
            self._checks.append(
                {"x": WIDTH - PAD - 96, "y": y + 6, "w": 84, "h": 24, "var": block["enabled"]}
            )
            y += height + 10
        y -= 10

        self._error_y = y + 18
        self._buttons_y = y + 34
        self._height = self._buttons_y + BUTTON_H + 18

    # -- 渲染 ------------------------------------------------------------
    def _draw(self) -> None:
        surf = Surface(WIDTH, self._height, self.scale, dim=0.34)
        self._regions = []
        surf.text(PAD, 24, "设置", 16, bold=True)
        surf.text(PAD, 50, "改完点保存即刻生效，不需要重启", 9, color=theme.TEXT_DIM)

        for block in self._blocks:
            surf.panel(PAD, block["y"], WIDTH - 2 * PAD, block["h"], radius=16,
                       blur=8, tint=(255, 255, 255, 22))
            if block["kind"] == "global":
                surf.text(PAD + 16, block["y"] + 18, block["title"], 11, bold=True, anchor="lm")
                for label, _var, row_y in block["rows"]:
                    surf.text(PAD + 16, row_y + FIELD_H / 2, label, 9,
                              color=theme.TEXT_DIM, anchor="lm")
            else:
                surf.text(PAD + 16, block["y"] + 18, block["title"], 12, bold=True, anchor="lm")
                note = "余额为本地估算" if block["estimate"] else "余额来自官方接口"
                surf.text(PAD + 16 + surf.text_width(block["title"], 12, bold=True) + 10,
                          block["y"] + 19, note, 8.5, color=theme.TEXT_FAINT, anchor="lm")
                surf.text(PAD + 16, block["y"] + 34, "API Key", 8.5, color=theme.TEXT_FAINT)
                surf.text(PAD + 16, block["y"] + 91, "余额警告", 9,
                          color=theme.TEXT_DIM, anchor="lm")
                surf.text(PAD + 216, block["y"] + 91, "花费警告", 9,
                          color=theme.TEXT_DIM, anchor="lm")
                if block["initial_balance"] is not None:
                    surf.text(PAD + 16, block["y"] + 125, "初始余额", 9,
                              color=theme.TEXT_DIM, anchor="lm")

        self._paint_fields(surf)

        if self._error:
            surf.text(PAD, self._error_y, "⚠ " + self._error, 9, color=theme.CORAL)

        for key, label, x, width, kind in self._buttons():
            state = "hover" if self._hover == key else "normal"
            self._pill(surf, x, self._buttons_y, width, BUTTON_H, label, kind, state)
            self._regions.append((x, self._buttons_y, width, BUTTON_H, key))

        image = surf.to_image()
        if self._photo is None:
            self._photo = ImageTk.PhotoImage(image)
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self._photo.paste(image)

    def _flat_color(self, pixels, x, y, w, h):
        points = [
            (x + w * fx, y + h * fy)
            for fx in (0.15, 0.5, 0.85)
            for fy in (0.25, 0.75)
        ]
        samples = []
        for px, py in points:
            ix = min(self._px(WIDTH) - 1, max(0, self._px(px)))
            iy = min(self._px(self._height) - 1, max(0, self._px(py)))
            samples.append(pixels[ix, iy])
        average = tuple(sum(s[i] for s in samples) // len(samples) for i in range(3))
        return theme.lerp(average, (255, 255, 255), 0.13)

    def _paint_fields(self, surf: Surface) -> None:
        """先画玻璃凹槽再取色，保证真控件底色和凹槽完全一致。

        颜色只在第一次计算并缓存：否则每次重绘都会在「已画过凹槽」的图上再取色，
        越悬停越白。
        """
        base_pixels = surf.to_image().load()
        for item in self._inputs:
            if "color" not in item:
                item["color"] = self._flat_color(base_pixels, item["x"], item["y"], item["w"], FIELD_H)
            surf.rect(item["x"], item["y"], item["w"], FIELD_H, radius=8,
                      fill=item["color"] + (255,), stroke=(255, 255, 255, 46), width=0.9)
        for item in self._checks:
            if "color" not in item:
                item["color"] = self._flat_color(base_pixels, item["x"], item["y"], item["w"], item["h"])
            surf.rect(item["x"], item["y"], item["w"], item["h"], radius=item["h"] / 2,
                      fill=item["color"] + (255,), stroke=(255, 255, 255, 46), width=0.9)

    def _buttons(self):
        return [
            ("save", "保 存", PAD, 118, "primary"),
            ("cancel", "取 消", PAD + 130, 96, "glass"),
            ("open", "打开配置", WIDTH - PAD - 112, 112, "glass"),
        ]

    def _pill(self, surf, x, y, w, h, label, kind, state) -> None:
        if kind == "primary":
            fill = theme.with_alpha(theme.ACCENT_RGB, 250 if state == "hover" else 232)
            fg, stroke = theme.INK, (255, 255, 255, 150)
        else:
            fill = (255, 255, 255, 58 if state == "hover" else 26)
            fg = theme.TEXT
            stroke = theme.GLASS_STROKE_HOVER if state == "hover" else theme.GLASS_STROKE
        surf.rect(x, y, w, h, radius=h / 2, fill=fill, stroke=stroke, width=1.1)
        surf.text(x + w / 2, y + h / 2 + 0.5, label, 11, color=fg, bold=True, anchor="mm")

    # -- 真实控件 --------------------------------------------------------
    def _build_widgets(self) -> None:
        for item in self._inputs:
            entry = tk.Entry(
                self.dlg, textvariable=item["var"], bg=theme.hex_str(item["color"]),
                fg="#ffffff", insertbackground=theme.hex_str(theme.MINT),
                relief="flat", highlightthickness=0, bd=0,
                justify=item.get("justify", "left"),
                font=("Microsoft YaHei UI", 10),
            )
            entry.place(
                x=self._px(item["x"] + 9), y=self._px(item["y"] + 3),
                width=self._px(item["w"] - 18), height=self._px(FIELD_H - 6),
            )
        for item in self._checks:
            check = tk.Checkbutton(
                self.dlg, text="启用", variable=item["var"], bg=theme.hex_str(item["color"]),
                fg="#ffffff", activebackground=theme.hex_str(item["color"]),
                activeforeground="#ffffff",
                selectcolor=theme.hex_str(theme.lerp(item["color"], (12, 8, 26), 0.45)),
                highlightthickness=0, bd=0, font=("Microsoft YaHei UI", 9),
            )
            check.place(
                x=self._px(item["x"] + 6), y=self._px(item["y"] + 1),
                width=self._px(item["w"] - 8), height=self._px(item["h"] - 2),
            )

    # -- 交互 ------------------------------------------------------------
    def _hit(self, x: float, y: float) -> Optional[str]:
        for rx, ry, rw, rh, key in self._regions:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return key
        return None

    def _on_motion(self, event) -> None:
        key = self._hit(event.x / self.scale, event.y / self.scale)
        if key != self._hover:
            self._hover = key
            try:
                self.canvas.configure(cursor="hand2" if key else "")
            except tk.TclError:
                pass
            self._draw()

    def _on_leave(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            self.canvas.configure(cursor="")
            self._draw()

    def _on_click(self, event) -> None:
        key = self._hit(event.x / self.scale, event.y / self.scale)
        if key == "save":
            self.save()
        elif key == "cancel":
            self.close()
        elif key == "open":
            self._open_config()

    # -- 动作 ------------------------------------------------------------
    def _config_target(self):
        """跟着 Monitor 走：用 --config 指定的文件就写那个文件，别乱写到默认路径。"""
        return self.monitor.config_path or paths.config_path()

    def _open_config(self) -> None:
        target = self._config_target()
        try:
            if not target.exists():
                config_mod.save(self.monitor.config, target)
            os.startfile(str(target))  # noqa: S606 - Windows 默认编辑器
        except Exception as exc:
            self._error = f"打开配置文件失败: {exc}"
            self._draw()

    def close(self) -> None:
        try:
            self.dlg.grab_release()
        except tk.TclError:
            pass
        self.dlg.destroy()

    def _number(self, var, label, errors, minimum=0.0, integer=False):
        raw = (var.get() or "").strip()
        if not raw:
            errors.append(f"{label}不能为空")
            return None
        try:
            value = float(raw)
        except ValueError:
            errors.append(f"{label}必须是数字")
            return None
        if value < minimum:
            errors.append(f"{label}不能小于 {minimum:g}")
            return None
        return int(value) if integer else value

    def save(self) -> None:
        errors: list = []
        interval = self._number(self._v_interval, "轮询间隔", errors, minimum=5, integer=True)
        total = self._number(self._v_total, "总花费警告阈值", errors, minimum=0)
        cooldown = self._number(self._v_cooldown, "告警冷却", errors, minimum=0, integer=True)

        overrides: dict = {
            "polling_interval": interval,
            "total_spend_alert": total,
            "alert_cooldown": cooldown,
            "providers": {},
        }
        for block in self._blocks:
            if block["kind"] != "provider":
                continue
            spec = {
                "enabled": bool(block["enabled"].get()),
                "api_key": (block["key"].get() or "").strip(),
                "balance_alert": self._number(block["balance_alert"],
                                              f"{block['title']} 余额警告", errors, 0),
                "spend_alert": self._number(block["spend_alert"],
                                            f"{block['title']} 花费警告", errors, 0),
            }
            if block["initial_balance"] is not None:
                spec["initial_balance"] = self._number(
                    block["initial_balance"], f"{block['title']} 初始余额", errors, 0
                )
            if spec["enabled"] and not spec["api_key"]:
                errors.append(f"{block['title']} 勾选了启用但 API Key 是空的")
            overrides["providers"][block["name"]] = spec

        if errors:
            self._error = "；".join(errors[:3])
            self._draw()
            return

        merged = config_mod.merge(self.monitor.config, overrides)
        try:
            config_mod.save(merged, self._config_target())
        except Exception as exc:
            log.exception("写入配置失败")
            self._error = f"写入配置文件失败: {exc}"
            self._draw()
            return
        self.monitor.apply_config(merged)
        if self.on_saved is not None:
            try:
                self.on_saved()
            except Exception:
                log.exception("设置保存回调失败")
        self.close()


def _fmt(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    return f"{number:g}"
