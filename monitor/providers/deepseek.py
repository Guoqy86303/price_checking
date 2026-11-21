"""DeepSeek：余额走官方 /user/balance 接口，单价按百万 token 计，错峰时段打折。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests

from .. import prices
from ..peak_hours import DEFAULT_WINDOW, is_off_peak
from .base import BalanceInfo, ModelPrice, Provider, ProviderError, Usage

log = logging.getLogger(__name__)


class DeepSeekProvider(Provider):
    name = "deepseek"
    display_name = "DeepSeek"
    base_url = "https://api.deepseek.com"
    balance_is_estimate = False

    # -- 余额 ------------------------------------------------------------
    def get_balance(self) -> BalanceInfo:
        try:
            response = requests.get(
                f"{self.base_url}/user/balance",
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"网络错误: {exc.__class__.__name__}") from exc
        if response.status_code == 401:
            raise ProviderError("API Key 无效（HTTP 401）")
        if response.status_code != 200:
            raise ProviderError(f"HTTP {response.status_code}: {response.text[:120]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("余额接口返回的不是 JSON") from exc
        infos = data.get("balance_infos") or []
        if not infos:
            raise ProviderError("响应里没有 balance_infos")
        info = next((item for item in infos if item.get("currency") == self.currency), infos[0])
        try:
            return BalanceInfo(
                total_balance=float(info["total_balance"]),
                granted_balance=float(info.get("granted_balance") or 0),
                topped_up_balance=float(info.get("topped_up_balance") or 0),
                currency=info.get("currency") or self.currency,
                is_available=bool(data.get("is_available", True)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"余额字段异常: {info}") from exc

    # -- 价格 ------------------------------------------------------------
    def get_model_price(self, model: str) -> Optional[ModelPrice]:
        return prices.deepseek_price(model)

    def _window(self):
        pricing = (self.config or {}).get("pricing") or {}
        if not pricing:
            # provider 配置里没有 pricing 时回落到全局默认
            return DEFAULT_WINDOW
        return pricing.get("off_peak_window") or DEFAULT_WINDOW

    def _multiplier(self):
        pricing = (self.config or {}).get("pricing") or {}
        try:
            return min(1.0, max(0.0, float(pricing.get("off_peak_multiplier", 0.5))))
        except (TypeError, ValueError):
            return 0.5

    def is_peak_hour(self, now: datetime) -> bool:
        return not is_off_peak(now, self._window())

    def calculate_cost(self, model: str, usage: Usage, now: datetime) -> float:
        price = self.get_model_price(model)
        if price is None:
            return 0.0
        multiplier = 1.0 if self.is_peak_hour(now) else self._multiplier()
        cache_hit = max(0, min(usage.cache_hit_tokens, usage.prompt_tokens))
        cache_miss = max(0, usage.prompt_tokens - cache_hit)
        cost = (
            cache_hit / 1_000_000 * price.cache_hit_input
            + cache_miss / 1_000_000 * price.cache_miss_input
            + max(0, usage.completion_tokens) / 1_000_000 * price.output
        )
        return cost * multiplier
