"""花费的持久化。

以前 ``_spends`` 只活在内存里，界面却写「累计花费」，重启就归零。
现在写到 ``%APPDATA%\\AIMonitor\\state.json``，所以累计是真的累计。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import paths

log = logging.getLogger(__name__)


class StateStore:
    def __init__(self, path=None):
        # path=False 表示纯内存模式（--demo / 单元测试），不落盘。
        if path is False:
            self.path: Optional[Path] = None
        else:
            self.path = Path(path) if path else paths.state_path()
        self._data: dict = self._read()
        self._last_saved: dict = dict(self._data.get("spends") or {})

    def _read(self) -> dict:
        if self.path is None:
            return {}
        try:
            data = paths.read_json(self.path, {})
            return data if isinstance(data, dict) else {}
        except Exception:
            log.exception("读取状态文件失败，忽略历史累计")
            return {}

    @property
    def first_seen(self) -> Optional[str]:
        value = self._data.get("first_seen")
        return value if isinstance(value, str) else None

    def load_spends(self) -> dict:
        spends = self._data.get("spends") or {}
        result = {}
        for name, value in spends.items():
            try:
                result[str(name)] = float(value)
            except (TypeError, ValueError):
                continue
        return result

    def save_spends(self, spends: dict) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        payload = {
            "spends": {str(k): round(float(v), 6) for k, v in spends.items()},
            "first_seen": self.first_seen or now,
            "updated": now,
        }
        if payload["spends"] == {str(k): round(float(v), 6) for k, v in self._last_saved.items()}:
            return
        self._data = payload
        self._last_saved = dict(payload["spends"])
        if self.path is None:
            return
        try:
            paths.write_json(self.path, payload)
        except Exception:
            log.exception("写入状态文件失败，累计花费可能丢失")
