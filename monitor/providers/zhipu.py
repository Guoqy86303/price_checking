"""智谱 GLM：官方没有开放的余额查询接口，所以余额是本地估算。

估算口径：``initial_balance - 累计花费``。以前只减「本次会话」的花费，
重启后余额会莫名涨回去；现在累计花费来自持久化状态，并标注为估算。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .. import prices
from .base import BalanceInfo, ModelPrice, Provider, ProviderError, Usage

log = logging.getLogger(__name__)


class ZhipuProvider(Provider):
    name = "zhipu"
    display_name = "GLM / 智谱"
    base_url = "https://open.bigmodel.cn/api/paas/v4"
    balance_is_estimate = True
    requires_initial_balance = True

    # -- 余额 ------------------------------------------------------------
    def get_balance(self) -> BalanceInfo:
        try:
            initial = float((self.config or {}).get("initial_balance") or 0)
        except (TypeError, ValueError):
            initial = 0.0
        if initial <= 0:
            raise ProviderError("未设置初始余额，无法估算（设置 → 初始余额）")
        spent = self.spend_so_far()
        remaining = max(0.0, initial - spent)
        return BalanceInfo(
            total_balance=remaining,
            currency=self.currency,
            is_available=remaining > 0,
            estimated=True,
        )

    # -- 价格 ------------------------------------------------------------
    def get_model_price(self, model: str) -> Optional[ModelPrice]:
        return prices.zhipu_price(model, 0)

    def calculate_cost(self, model: str, usage: Usage, now: datetime) -> float:
        price = prices.zhipu_price(model, max(0, usage.prompt_tokens))
        if price is None:
            return 0.0
        cache_hit = max(0, min(usage.cache_hit_tokens, usage.prompt_tokens))
        cache_miss = max(0, usage.prompt_tokens - cache_hit)
        return (
            cache_hit / 1_000_000 * price.cache_hit_input
            + cache_miss / 1_000_000 * price.cache_miss_input
            + max(0, usage.completion_tokens) / 1_000_000 * price.output
        )
