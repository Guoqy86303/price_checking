"""告警规则：同一条告警在冷却时间内只弹一次。

旧逻辑是「比上次告警时的数值更差就再弹」，结果每次轮询余额降一点就弹一次
（实测 6 次轮询弹 5 次），而且 modal 弹窗会卡住轮询线程。现在：

* 每个告警键独立计时，默认 30 分钟冷却；
* 条件恢复（余额回到阈值以上）后立即重新武装，下次越线还能立刻提醒；
* 纯逻辑，不碰 tkinter，所以可以在单元测试里跑。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Alert:
    key: str
    title: str
    message: str
    level: str = "warning"


def _number(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


class Alerter:
    def __init__(self, cooldown: float = 1800.0, clock: Optional[Callable[[], float]] = None):
        self.cooldown = max(0.0, float(cooldown))
        self._clock = clock or time.monotonic
        self._last: dict[str, float] = {}

    def set_cooldown(self, cooldown: float) -> None:
        self.cooldown = max(0.0, float(cooldown))

    def reset(self) -> None:
        self._last.clear()

    def evaluate(
        self,
        *,
        balances: dict,
        spends: dict,
        providers_cfg: dict,
        total_spend: float,
        total_threshold: float,
        symbol: str = "¥",
        labels: Optional[dict] = None,
        now: Optional[float] = None,
    ) -> list[Alert]:
        names = labels or {}
        moment = self._clock() if now is None else now
        alerts: list[Alert] = []

        for name, info in balances.items():
            cfg = providers_cfg.get(name) or {}
            threshold = _number(cfg.get("balance_alert"), 0.0)
            key = f"{name}:balance"
            if threshold <= 0:
                self._last.pop(key, None)
                continue
            if info.total_balance < threshold:
                if self._ready(key, moment):
                    suffix = "（估算）" if info.estimated else ""
                    alerts.append(
                        Alert(
                            key,
                            f"{names.get(name, name)} 余额偏低",
                            f"当前 {symbol}{info.total_balance:.2f}{suffix}，"
                            f"低于阈值 {symbol}{threshold:.2f}",
                        )
                    )
            else:
                self._last.pop(key, None)

        for name, spend in spends.items():
            cfg = providers_cfg.get(name) or {}
            threshold = _number(cfg.get("spend_alert"), 0.0)
            key = f"{name}:spend"
            if threshold <= 0:
                self._last.pop(key, None)
                continue
            if spend > threshold:
                if self._ready(key, moment):
                    alerts.append(
                        Alert(
                            key,
                            f"{names.get(name, name)} 花费超阈值",
                            f"累计花费 {symbol}{spend:.2f}，超过阈值 {symbol}{threshold:.2f}",
                        )
                    )
            else:
                self._last.pop(key, None)

        total_threshold = _number(total_threshold, 0.0)
        key = "total:spend"
        if total_threshold > 0 and total_spend > total_threshold:
            if self._ready(key, moment):
                alerts.append(
                    Alert(
                        key,
                        "总花费超过阈值",
                        f"所有服务商累计 {symbol}{total_spend:.2f}，"
                        f"超过阈值 {symbol}{total_threshold:.2f}",
                    )
                )
        else:
            self._last.pop(key, None)
        return alerts

    def _ready(self, key: str, moment: float) -> bool:
        last = self._last.get(key)
        if last is None or self.cooldown <= 0 or (moment - last) >= self.cooldown:
            self._last[key] = moment
            return True
        return False

