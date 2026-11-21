"""主窗口：玻璃拟态面板。

实现要点：
* 背景（渐变 + 模糊光斑 + 暗角）和玻璃面板一次性烘焙成 Pillow 图，
  文字/按钮/进度条每帧画在副本上 —— 所以面板是真空的磨砂玻璃；
* 交互区是一组逻辑矩形，鼠标事件换算回逻辑坐标后命中；
* 后台线程只往 ``queue`` 里塞事件，主线程 ``after`` 消费，Tk 全程单线程。
"""

from __future__ import annotations

import logging
import queue
import tkinter as tk
import time
from typing import Optional

from PIL import ImageTk

from . import theme, win
from .alerter import Alert
from .render import Surface
from .tray import TrayIcon

log = logging.getLogger(__name__)

PAD = 22
WIDTH = 480
HERO_Y = 84
HERO_H = 138
CARD_H = 102
CARD_GAP = 10
BUTTON_H = 42


class MonitorApp:
    def __init__(self, monitor):
        self.monitor = monitor
        self.root = tk.Tk()
        self.root.title("AI 费用监控器")
        self.root.configure(bg="#2b2350")
        try:
            from .tray import ensure_icon

            self.root.iconbitmap(default=str(ensure_icon()))
        except Exception:
            pass
        self.scale = self._detect_scale()

        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._hover: Optional[str] = None
        self._pressed: Optional[str] = None
        self._regions: list = []
        self._status = "idle"
        self._sync_started = 0.0
        self._photo = None
        self._toasts: list = []
        self._active_toast = None

        self._compute_layout()
        self._static = self._build_static()

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, bg="#2b2350")
        self.canvas.place(x=0, y=0, width=self._px(WIDTH), height=self._px(self._height))
        self._apply_geometry()
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Control-r>", lambda _event: self.refresh())
        self.root.bind("<Escape>", lambda _event: self.hide_to_tray())
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.monitor.on_change(lambda: self._events.put(("change", None)))
        self.monitor.on_alert(lambda alert: self._events.put(("alert", alert)))

        self.tray = TrayIcon(
            self.root, on_show=self.show, on_refresh=self.refresh, on_quit=self.quit
        )
        self._tray_ok = self.tray.install()
        if not self._tray_ok:
            # 托盘不可用就老老实实退出，绝不 withdraw 成一个幽灵进程
            self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self._render()
        win.apply_theme(self.root)
        self._drain()

    # -- 尺寸 / 布局 -----------------------------------------------------
    def _detect_scale(self) -> float:
        try:
            scale = float(self.root.winfo_fpixels("1i")) / 96.0
        except Exception:
            scale = 1.0
        return round(min(2.0, max(1.0, scale)), 2)

    def _px(self, value: float) -> int:
        return int(round(value * self.scale))

    def _compute_layout(self) -> None:
        providers = self.monitor.providers
        y = HERO_Y + HERO_H + 16
        self._cards: list = []
        if providers:
            for provider in providers:
                self._cards.append((provider, y, CARD_H))
                y += CARD_H + CARD_GAP
            y -= CARD_GAP
        else:
            self._cards.append((None, y, 88))
            y += 88
        self._status_y = y + 16
        self._buttons_y = self._status_y + 24
        self._height = self._buttons_y + BUTTON_H + 20
        self._buttons = [
            ("refresh", "刷 新", 118, "primary"),
            ("settings", "设 置", 104, "glass"),
            ("quit", "退 出", 88, "glass"),
        ]
        self._button_x = {
            "refresh": PAD,
            "settings": PAD + 130,
            "quit": WIDTH - PAD - 88,
        }

    def _apply_geometry(self) -> None:
        self.root.geometry(f"{self._px(WIDTH)}x{self._px(self._height)}")
        self.root.resizable(False, False)
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(0, (screen_w - self._px(WIDTH)) // 2)
            y = max(0, (screen_h - self._px(self._height)) // 3)
            self.root.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _build_static(self) -> Surface:
        surf = Surface(WIDTH, self._height, self.scale)
        surf.panel(PAD, HERO_Y, WIDTH - 2 * PAD, HERO_H, radius=20,
                   tint=theme.GLASS_TINT_STRONG, blur=11)
        for _provider, y, height in self._cards:
            surf.panel(PAD, y, WIDTH - 2 * PAD, height, radius=16, blur=8)
        return surf

    # -- 绘制 ------------------------------------------------------------
    def _render(self) -> None:
        surf = self._static.clone()
        self._regions = []
        self._draw_header(surf)
        self._draw_hero(surf)
        self._draw_cards(surf)
        self._draw_footer(surf)
        self._draw_buttons(surf)
        image = surf.to_image()
        if self._photo is None:
            self._photo = ImageTk.PhotoImage(image)
            self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        else:
            self._photo.paste(image)

    def _status_view(self):
        if self._status == "syncing":
            return "查询中…", theme.ACCENT
        errors = self.monitor.get_errors()
        if errors:
            return "部分服务商查询失败", theme.CORAL
        updated = self.monitor.last_update
        if updated:
            return f"已同步 {updated.strftime('%H:%M:%S')}", theme.TEXT_DIM
        return "等待查询…", theme.TEXT_DIM

    def _draw_header(self, surf: Surface) -> None:
        surf.text(PAD, 28, "AI 费用监控", 21, bold=True)
        surf.text(PAD, 58, "Aurora Glass · 余额与花费实时监控", 9.5, color=theme.TEXT_DIM)
        label, color = self._status_view()
        surf.text(WIDTH - PAD, 34, label, 9.5, color=color, anchor="ra")

    def _money(self, value: float, digits: int = 2) -> str:
        return f"{self.monitor.symbol()}{value:,.{digits}f}"

    def _draw_hero(self, surf: Surface) -> None:
        x, y, w = PAD, HERO_Y, WIDTH - 2 * PAD
        total = self.monitor.get_total_spend()
        try:
            threshold = float(self.monitor.config.get("total_spend_alert") or 0)
        except (TypeError, ValueError):
            threshold = 0.0

        surf.text(x + 18, y + 18, "累计花费", 10, color=theme.TEXT_DIM)
        surf.text(x + 18, y + 62, self._money(total), 34, bold=True, mono=True, anchor="lm")

        if threshold > 0:
            ratio = total / threshold if threshold else 0.0
            color = theme.CORAL if ratio >= 1 else (theme.ACCENT if ratio >= 0.8 else theme.MINT)
            surf.text(x + w - 18, y + 18, f"阈值 {self._money(threshold, 0)}", 9.5,
                      color=theme.TEXT_DIM, anchor="ra")
            surf.text(x + w - 18, y + 52, f"{ratio * 100:.0f}%", 15, bold=True, mono=True,
                      color=color, anchor="rm")
            surf.bar(x + 18, y + 112, w - 36, 5, ratio, theme.MINT_RGB, theme.ACCENT_RGB)

        since = self.monitor.spend_since()
        since_text = f"自 {since[5:10].replace('-', '/')} 起累计" if since else "本次会话累计"
        count = len(self.monitor.providers)
        surf.text(x + 18, y + 86, f"{count} 个服务商 · {since_text}", 8.5,
                  color=theme.TEXT_FAINT)

    def _chip(self, surf: Surface, right: float, y: float, label: str, color) -> None:
        size = 8.5
        height = 20.0
        width = surf.text_width(label, size, bold=True) + 20
        surf.rect(right - width, y, width, height, radius=height / 2,
                  fill=theme.with_alpha(color, 48), stroke=theme.with_alpha(color, 150), width=1.0)
        surf.text(right - width / 2, y + height / 2, label, size, color=color, bold=True, anchor="mm")

    def _draw_cards(self, surf: Surface) -> None:
        balances = self.monitor.get_balances()
        spends = self.monitor.get_spends()
        errors = self.monitor.get_errors()
        symbol = self.monitor.symbol()
        providers_cfg = self.monitor.config.get("providers") or {}

        for provider, y, height in self._cards:
            x, w = PAD, WIDTH - 2 * PAD
            if provider is None:
                surf.text(x + 18, y + 30, "还没有启用任何服务商", 11.5, bold=True)
                surf.text(x + 18, y + 54, "点「设置」填入 API Key 并勾选启用", 9,
                          color=theme.TEXT_DIM)
                continue

            spec = providers_cfg.get(provider.name) or {}
            info = balances.get(provider.name)
            error = errors.get(provider.name)
            spend = spends.get(provider.name, 0.0)

            try:
                balance_alert = float(spec.get("balance_alert") or 0)
            except (TypeError, ValueError):
                balance_alert = 0.0

            if error:
                state, state_color = "查询失败", theme.CORAL
            elif info is None:
                state, state_color = "等待查询", theme.TEXT_DIM
            elif info.total_balance <= 0:
                state, state_color = "余额耗尽", theme.CORAL
            elif balance_alert > 0 and info.total_balance < balance_alert:
                state, state_color = "余额偏低", theme.CORAL
            else:
                state, state_color = "正常", theme.MINT

            surf.text(x + 16, y + 18, provider.get_display_name(), 13, bold=True, anchor="lm")
            self._chip(surf, x + w - 16, y + 8, state, state_color)

            if info is not None:
                prefix = "≈" if info.estimated else ""
                balance_text = f"{prefix}{symbol}{info.total_balance:,.2f}"
            else:
                balance_text = f"{symbol}----"
            balance_label = "余额（估算）" if (info is not None and info.estimated) else "余额"
            surf.text(x + 16, y + 44, balance_label, 9, color=theme.TEXT_FAINT)
            surf.text(x + 16, y + 66, balance_text, 19, bold=True, mono=True, anchor="lm")

            surf.text(x + w * 0.52, y + 44, "累计花费", 9, color=theme.TEXT_FAINT)
            surf.text(x + w * 0.52, y + 66, self._money(spend, 3), 15, bold=True, mono=True,
                      anchor="lm")

            if error:
                message = error if len(error) <= 34 else error[:33] + "…"
                surf.text(x + 16, y + 90, message, 8.5, color=theme.CORAL)
            else:
                try:
                    spend_alert = float(spec.get("spend_alert") or 0)
                except (TypeError, ValueError):
                    spend_alert = 0.0
                ratio = spend / spend_alert if spend_alert > 0 else 0.0
                surf.bar(x + 16, y + 88, w - 32, 4, ratio,
                         theme.MINT_RGB, theme.CORAL_RGB)

    def _draw_footer(self, surf: Surface) -> None:
        updated = self.monitor.last_update
        interval = self.monitor.polling_interval
        if updated:
            left = f"上次同步 {updated.strftime('%H:%M:%S')} · 每 {interval} 秒轮询"
        else:
            left = f"尚未同步 · 每 {interval} 秒轮询"
        unknown = self.monitor.get_unknown_models()
        if unknown:
            left += f" · {len(unknown)} 个模型缺价目"
        surf.text(PAD, self._status_y, left, 8.5, color=theme.with_alpha(theme.WHITE, 168))
        surf.text(WIDTH - PAD, self._status_y, "Glassmorphism", 8.5,
                  color=theme.with_alpha(theme.WHITE, 118), anchor="ra")

    def _pill(self, surf: Surface, x, y, w, h, label, kind, state) -> None:
        if kind == "primary":
            fills = {"normal": theme.with_alpha(theme.ACCENT_RGB, 232),
                     "hover": theme.with_alpha(theme.ACCENT_RGB, 250),
                     "press": (238, 186, 86, 255)}
            fill = fills[state]
            fg = theme.INK
            stroke = (255, 255, 255, 170) if state != "normal" else (255, 255, 255, 96)
        else:
            fills = {"normal": (255, 255, 255, 26),
                     "hover": (255, 255, 255, 58),
                     "press": (255, 255, 255, 84)}
            fill = fills[state]
            fg = theme.TEXT
            stroke = theme.GLASS_STROKE_HOVER if state != "normal" else theme.GLASS_STROKE
        surf.rect(x, y, w, h, radius=h / 2, fill=fill, stroke=stroke, width=1.1)
        surf.text(x + w / 2, y + h / 2 + 0.5, label, 11, color=fg, bold=True, anchor="mm")

    def _draw_buttons(self, surf: Surface) -> None:
        for key, label, width, kind in self._buttons:
            x = self._button_x[key]
            state = "press" if self._pressed == key else ("hover" if self._hover == key else "normal")
            self._pill(surf, x, self._buttons_y, width, BUTTON_H, label, kind, state)
            self._regions.append((x, self._buttons_y, width, BUTTON_H, key))

    # -- 交互 ------------------------------------------------------------
    def _hit(self, event) -> Optional[str]:
        x, y = event.x / self.scale, event.y / self.scale
        for rx, ry, rw, rh, key in self._regions:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return key
        return None

    def _on_motion(self, event) -> None:
        key = self._hit(event)
        if key != self._hover:
            self._hover = key
            try:
                self.canvas.configure(cursor="hand2" if key else "")
            except tk.TclError:
                pass
            self._render()

    def _on_leave(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            self.canvas.configure(cursor="")
            self._render()

    def _on_press(self, event) -> None:
        key = self._hit(event)
        if key:
            self._pressed = key
            self._render()

    def _on_release(self, event) -> None:
        key = self._hit(event)
        pressed, self._pressed = self._pressed, None
        if key and key == pressed:
            self._action(key)
        else:
            self._render()

    def _action(self, key: str) -> None:
        if key == "refresh":
            self.refresh()
        elif key == "settings":
            self.open_settings()
        elif key == "quit":
            self.quit()

    # -- 行为 ------------------------------------------------------------
    def refresh(self) -> None:
        self._status = "syncing"
        self._sync_started = time.monotonic()
        self._render()
        self.monitor.refresh_async()

    def open_settings(self) -> None:
        from .settings_ui import SettingsDialog

        SettingsDialog(self.root, self.monitor, on_saved=self._after_settings)

    def _after_settings(self) -> None:
        self._rebuild()
        self.refresh()

    def _rebuild(self) -> None:
        self._compute_layout()
        self._static = self._build_static()
        self.canvas.place_configure(width=self._px(WIDTH), height=self._px(self._height))
        self._apply_geometry()
        self.canvas.delete("all")
        self._photo = None
        self._render()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def hide_to_tray(self) -> None:
        if not self._tray_ok:
            self.quit()
            return
        self.root.withdraw()
        self._show_alert(
            Alert("hidden", "已收进托盘", "双击托盘图标恢复面板，右键菜单可退出")
        )

    def quit(self) -> None:
        try:
            self.monitor.stop()
        except Exception:
            log.exception("停止监控失败")
        try:
            self.tray.destroy()
        except Exception:
            log.exception("销毁托盘失败")
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()

    # -- 事件泵 ----------------------------------------------------------
    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "change":
                    if self._status == "syncing":
                        self._status = "idle"
                    self._render()
                elif kind == "alert":
                    self._show_alert(payload)
        except queue.Empty:
            pass
        if self._status == "syncing" and time.monotonic() - self._sync_started > 20:
            self._status = "idle"
            self._render()
        self.root.after(150, self._drain)

    # -- 告警气泡 --------------------------------------------------------
    def _show_alert(self, alert: Alert) -> None:
        self._toasts.append(alert)
        self._maybe_show_toast()

    def _maybe_show_toast(self) -> None:
        if self._active_toast is not None or not self._toasts:
            return
        alert = self._toasts.pop(0)
        self._active_toast = GlassToast(self.root, alert, self.scale, self._toast_closed)

    def _toast_closed(self) -> None:
        self._active_toast = None
        self._maybe_show_toast()


def _wrap(surf: Surface, text: str, size: float, max_width: float, max_lines: int = 2) -> list:
    lines: list = []
    current = ""
    for char in text:
        if surf.text_width(current + char, size) > max_width:
            lines.append(current)
            current = char
            if len(lines) == max_lines:
                return lines
        else:
            current += char
    if current:
        lines.append(current)
    return lines[:max_lines]


class GlassToast:
    """右下角浮出的玻璃提示条，替代会阻塞线程的 messagebox。"""

    W, H = 352, 88

    def __init__(self, root, alert: Alert, scale: float, on_close):
        self._on_close = on_close
        self._closed = False
        self._root = tk.Toplevel(root)
        self._root.title("AIMonitorToast")   # 别继承主窗口标题，免得自动化/读屏混淆
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        try:
            self._root.attributes("-transparentcolor", "#010203")
            background = "#010203"
        except tk.TclError:
            background = "#1c1430"
        self._root.configure(bg=background)

        surf = Surface(self.W, self.H, scale)
        surf.rect(0, 0, self.W, self.H, radius=18, fill=(26, 18, 48, 238),
                  stroke=(255, 255, 255, 150), width=1.2)
        surf.rect(1.4, 18, 3.2, self.H - 36, radius=1.6, fill=theme.ACCENT)
        surf.text(20, 20, alert.title, 11, bold=True)
        lines = _wrap(surf, alert.message, 9, self.W - 44, 2)
        for index, line in enumerate(lines):
            surf.text(20, 44 + index * 15, line, 9, color=theme.TEXT_DIM)

        self._photo = ImageTk.PhotoImage(surf.to_image())
        self._canvas = tk.Canvas(self._root, width=self._px(self.W), height=self._px(self.H),
                                 highlightthickness=0, bd=0, bg=background)
        self._canvas.pack()
        self._canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._canvas.bind("<Button-1>", lambda _event: self.close())

        width, height = self._px(self.W), self._px(self.H)
        try:
            screen_w = self._root.winfo_screenwidth()
            screen_h = self._root.winfo_screenheight()
            self._root.geometry(f"{width}x{height}+{screen_w - width - 26}+{screen_h - height - 76}")
        except Exception:
            pass
        self._root.after(6500, self.close)

    def _px(self, value: float) -> int:
        try:
            return int(round(value * float(self._root.winfo_fpixels("1i")) / 96.0))
        except Exception:
            return int(round(value))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._root.destroy()
        except Exception:
            pass
        try:
            self._on_close()
        except Exception:
            pass
