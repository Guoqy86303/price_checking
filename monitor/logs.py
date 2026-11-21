"""日志：文件 + 控制台（pythonw 下没有 stderr，忽略即可）。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import paths

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_ai_monitor_ready", False):
        return
    formatter = logging.Formatter(_FORMAT)
    try:
        handler = RotatingFileHandler(
            paths.log_path(), maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except Exception:
        pass
    try:
        if sys.stderr is not None:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(formatter)
            root.addHandler(stream)
    except Exception:
        pass
    root.setLevel(level)
    root._ai_monitor_ready = True
    logging.getLogger("urllib3").setLevel(logging.WARNING)
