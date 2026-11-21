"""AI 费用监控器入口。

用法::

    python main.py            # 打开玻璃拟态面板（关闭 = 收进托盘）
    python main.py --demo     # 用假数据预览界面，不读真实配置
    python main.py --once     # 查一次余额并打印快照，适合脚本/排障
"""

from __future__ import annotations

import argparse
import logging
import sys

from monitor import logs, paths, win

log = logging.getLogger(__name__)


def _print_snapshot(monitor) -> None:
    snap = monitor.snapshot()
    symbol = snap["symbol"]
    print(f"AI 费用监控器 · 状态快照（{snap['updated'] or '尚未同步'}）")
    if not snap["rows"]:
        print("  （没有启用的服务商，去 config.json 填 API Key 并设 enabled=true）")
    for row in snap["rows"]:
        if row["balance"] is None:
            balance = "----"
        else:
            balance = f"{symbol}{row['balance']:,.2f}"
            if row["estimated"]:
                balance = "≈" + balance
        line = f"  {row['label']:<14}余额 {balance:>12}   累计 {symbol}{row['spend']:.4f}"
        if row["error"]:
            line += f"   [{row['error']}]"
        print(line)
    print(f"  合计 {symbol}{snap['total_spend']:.4f} / 阈值 {symbol}{snap['total_alert']:g}")
    if snap["unknown_models"]:
        print("  缺价目模型: " + ", ".join(snap["unknown_models"]))
    print(f"  日志: {paths.log_path()}")


def _fatal(message: str) -> None:
    log.error("无法启动: %s", message)
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("AI 费用监控器", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ai-monitor", description="AI 费用监控器")
    parser.add_argument("--config", help="配置文件路径（默认项目根目录 config.json）")
    parser.add_argument("--once", action="store_true", help="查询一次并打印状态，不打开界面")
    parser.add_argument("--demo", action="store_true", help="用演示数据打开界面")
    args = parser.parse_args(argv)

    logs.setup()

    if args.once:
        from monitor.pricing import Monitor

        try:
            monitor = Monitor(config_path=args.config)
        except Exception as exc:
            print(f"读取配置失败: {exc}", file=sys.stderr)
            return 1
        try:
            monitor.refresh_now()
            _print_snapshot(monitor)
        finally:
            monitor.stop()
        return 0

    win.enable_dpi_awareness()
    try:
        if args.demo:
            from monitor.demo import build_demo_monitor

            monitor = build_demo_monitor()
        else:
            from monitor.pricing import Monitor

            monitor = Monitor(config_path=args.config)
        monitor.start()

        from monitor.app import MonitorApp

        app = MonitorApp(monitor)
        try:
            app.run()
        finally:
            monitor.stop()
        return 0
    except Exception as exc:  # noqa: BLE001 - 启动阶段任何错误都要给人看到
        log.exception("启动失败")
        _fatal(f"启动失败：{exc}\n\n详见日志：{paths.log_path()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
