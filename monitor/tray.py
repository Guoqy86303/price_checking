"""Windows 托盘图标。

旧版这段是死代码：``_WM_COMMAND`` 从未定义、图标位图返回 None、建了窗口却
没有消息循环，而且 ``SystemTray`` 全项目没人实例化 —— 结果「关闭」只是
``withdraw()``，进程变成杀不掉的幽灵。

现在：Pillow 画图标 → pywin32 建隐藏窗口 → Shell_NotifyIcon → 借 Tk 的
``after`` 泵消息。左键单击/双击显示面板，右键出菜单。任何一步失败都返回
False，界面据此决定「关闭」是真退出，绝不留幽灵进程。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from . import paths

log = logging.getLogger(__name__)

try:
    import win32api
    import win32con
    import win32gui

    _HAS_WIN32 = True
except Exception:  # pragma: no cover - 非 Windows 或没装 pywin32
    _HAS_WIN32 = False


def _draw_icon():
    """品牌小图标：紫底玻璃方块 + 三根数据条。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 61, 61), radius=18, fill=(88, 72, 204, 255),
                           outline=(255, 255, 255, 225), width=3)
    draw.rounded_rectangle((14, 34, 50, 42), radius=4, fill=(255, 205, 106, 255))
    draw.rounded_rectangle((14, 22, 36, 30), radius=4, fill=(93, 219, 210, 255))
    draw.rounded_rectangle((14, 46, 44, 54), radius=4, fill=(255, 255, 255, 200))
    return image


def ensure_icon():
    """生成（或复用）托盘/窗口图标，返回 .ico 路径。"""
    path = paths.data_dir() / "tray.ico"
    if not path.exists():
        try:
            _draw_icon().save(path, format="ICO", sizes=[(64, 64), (32, 32), (16, 16)])
        except Exception:
            log.exception("生成图标失败")
    return path


class TrayIcon:
    MENU_SHOW = 1001
    MENU_REFRESH = 1002
    MENU_QUIT = 1003

    def __init__(
        self,
        root,
        on_show: Callable[[], None],
        on_refresh: Callable[[], None],
        on_quit: Callable[[], None],
        tooltip: str = "AI 费用监控器",
    ):
        self._root = root
        self._on_show = on_show
        self._on_refresh = on_refresh
        self._on_quit = on_quit
        self._tooltip = tooltip[:120]
        self._hwnd: Optional[int] = None
        self._hicon: Optional[int] = None
        self._message: int = 0
        self._class_name = "AIMonitorTrayWnd"
        self._installed = False
        self._pumping = False

    # -- 安装 ------------------------------------------------------------
    def install(self) -> bool:
        if not _HAS_WIN32:
            log.info("没有 pywin32，托盘不可用，关闭按钮将直接退出")
            return False
        try:
            self._message = win32gui.RegisterWindowMessage("AIMonitorTrayNotify")
            self._create_window()
            self._create_icon()
            self._add_icon()
            self._installed = True
            self._pumping = True
            self._pump()
            log.info("托盘图标已就绪")
            return True
        except Exception:
            log.exception("托盘初始化失败")
            self._installed = False
            return False

    def _create_window(self) -> None:
        instance = win32gui.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name
        window_class.lpfnWndProc = self._wnd_proc
        window_class.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        try:
            window_class.hbrBackground = win32con.COLOR_WINDOW
            atom = win32gui.RegisterClass(window_class)
        except Exception:
            atom = self._class_name  # 已经注册过
        self._hwnd = win32gui.CreateWindow(
            atom, self._class_name, win32con.WS_OVERLAPPED, 0, 0, 0, 0, 0, 0, instance, None
        )
        if not self._hwnd:
            raise RuntimeError("CreateWindow 失败")

    def _create_icon(self) -> None:
        path = ensure_icon()
        self._hicon = win32gui.LoadImage(
            0, str(path), win32con.IMAGE_ICON, 0, 0,
            win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
        )
        if not self._hicon:
            self._hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

    def _add_icon(self) -> None:
        data = (
            self._hwnd,
            1,
            win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
            self._message,
            self._hicon,
            self._tooltip,
        )
        # pywin32 成功时返回 None（失败一般抛 pywintypes.error），不能拿返回值当布尔用。
        # 清一次 last error 再查，非 0 就当作没加上，界面会退回「关闭即退出」。
        try:
            win32api.SetLastError(0)
        except Exception:
            pass
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, data)
        try:
            error = win32api.GetLastError()
        except Exception:
            error = 0
        if error:
            raise RuntimeError(f"Shell_NotifyIcon 失败，错误码 {error}")

    # -- 交互 ------------------------------------------------------------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == self._message:
                if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
                    self._on_show()
                elif lparam == win32con.WM_RBUTTONUP:
                    self._popup_menu()
                return 0
            if msg == win32con.WM_COMMAND:
                self._dispatch(win32api.LOWORD(wparam))
                return 0
        except Exception:
            log.exception("托盘消息处理异常")
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _popup_menu(self) -> None:
        menu = win32gui.CreatePopupMenu()
        try:
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.MENU_SHOW, "显示面板")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.MENU_REFRESH, "立即刷新")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, None)
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.MENU_QUIT, "退出")
            try:
                win32gui.SetForegroundWindow(self._hwnd)
            except Exception:
                pass
            x, y = win32gui.GetCursorPos()
            command = win32gui.TrackPopupMenu(
                menu,
                win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON | win32con.TPM_RETURNCMD,
                x, y, 0, self._hwnd, None,
            )
            win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)
        finally:
            win32gui.DestroyMenu(menu)
        if command:
            self._dispatch(command)

    def _dispatch(self, command: int) -> None:
        if command == self.MENU_SHOW:
            self._on_show()
        elif command == self.MENU_REFRESH:
            self._on_refresh()
        elif command == self.MENU_QUIT:
            self._on_quit()

    def _pump(self) -> None:
        if not self._pumping:
            return
        try:
            win32gui.PumpWaitingMessages()
        except Exception:
            log.exception("托盘消息泵异常")
        try:
            self._root.after(80, self._pump)
        except Exception:
            self._pumping = False

    # -- 对外 ------------------------------------------------------------
    def destroy(self) -> None:
        self._pumping = False
        if not self._installed:
            return
        try:
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_DELETE, (self._hwnd, 1, 0, self._message, 0, "")
            )
        except Exception:
            pass
        try:
            if self._hwnd:
                win32gui.DestroyWindow(self._hwnd)
        except Exception:
            pass
        try:
            win32gui.UnregisterClass(self._class_name, win32gui.GetModuleHandle(None))
        except Exception:
            pass
        self._installed = False
        self._hwnd = None
