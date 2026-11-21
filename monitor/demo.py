"""``--demo``：用假余额跑界面，方便调样式 / 截图，不读真实配置、不写状态。

真实 API 要花钱，改界面时总不能每次先充钱再看效果。
"""

from __future__ import annotations

import itertools

from . import config as config_mod, paths
from .pricing import Monitor
from .providers.base import BalanceInfo, Usage
from .providers.deepseek import DeepSeekProvider


class DemoDeepSeek(DeepSeekProvider):
    _levels = itertools.cycle([128.46, 128.12, 127.90, 128.05])

    def get_balance(self) -> BalanceInfo:
        return BalanceInfo(total_balance=next(self._levels), granted_balance=20.0,
                           topped_up_balance=108.46, currency=self.currency, is_available=True)


def build_demo_monitor() -> Monitor:
    cfg = config_mod.defaults()
    cfg["providers"]["deepseek"].update({"enabled": True, "api_key": "demo", "balance_alert": 200.0})
    cfg["providers"]["zhipu"].update({"enabled": True, "api_key": "demo", "initial_balance": 42.5})
    monitor = Monitor(
        config=cfg,
        # 演示模式下如果点了「保存」，写到独立的 demo-config.json，
        # 绝不能把你真实的 config.json（含 API Key）覆盖掉
        config_path=str(paths.data_dir() / "demo-config.json"),
        state_path=False,
        provider_classes={"deepseek": DemoDeepSeek},
    )
    # 造几笔按真实价目算出来的花费，数字看着合理
    monitor.record_usage("deepseek", "deepseek-v4-flash", Usage(1_240_000, 86_000, 780_000))
    monitor.record_usage("deepseek", "deepseek-v4-pro", Usage(320_000, 41_000, 120_000))
    monitor.record_usage("zhipu", "glm-5.3-flash", Usage(410_000, 22_000, 180_000))
    return monitor
