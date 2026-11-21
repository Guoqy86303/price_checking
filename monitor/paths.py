"""路径解析与原子 JSON 读写。

配置留在项目目录（跟以前一致，方便手改），运行态数据（花费、日志）
放到 ``%APPDATA%\\AIMonitor``，这样即便是只读安装目录也能正常记录。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

APP_NAME = "AIMonitor"


def project_root() -> Path:
    """monitor/ 的上一级，也就是 main.py 所在目录。"""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get("AI_MONITOR_HOME")
    if override:
        base = Path(override)
    else:
        appdata = os.environ.get("APPDATA")
        base = (Path(appdata) if appdata else Path.home()) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    override = os.environ.get("AI_MONITOR_CONFIG")
    if override:
        return Path(override)
    return project_root() / "config.json"


def state_path() -> Path:
    return data_dir() / "state.json"


def log_path() -> Path:
    return data_dir() / "monitor.log"


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, data) -> None:
    """先写临时文件再 os.replace，避免断电/崩溃留下半个 JSON。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
