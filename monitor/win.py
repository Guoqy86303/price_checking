"""Windows 外观相关的小工具：DPI 感知、深色标题栏、圆角窗口。

全部用 try/except 包起来，失败就退回 Tk 默认外观，不影响功能。
"""

from __future__ import annotations


def enable_dpi_awareness() -> None:
    """必须在创建 Tk() 之前调用，否则高分屏上整个界面会被系统位图拉伸、发糊。"""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _hwnd(window) -> int:
    import ctypes

    return ctypes.windll.user32.GetParent(window.winfo_id())


def dark_titlebar(window) -> None:
    try:
        import ctypes

        handle = _hwnd(window)
        if not handle:
            return
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE（19 是旧版编号）
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                handle, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
        rounded = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            handle, 33, ctypes.byref(rounded), ctypes.sizeof(rounded)
        )
    except Exception:
        pass


def apply_theme(window) -> None:
    window.update_idletasks()
    dark_titlebar(window)
