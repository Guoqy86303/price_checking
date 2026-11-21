"""服务商抽象。

约定：
* ``get_balance()`` 失败时**抛异常**（带人能看懂的原因），由轮询器收集后
  显示在界面上；旧版本一律返回 None，界面上就只剩一个 ``----``。
* ``calculate_cost()`` 必须能处理「价目表里没有这个模型」的情况，
  返回 0 并让 Monitor 记一条 warning。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional


class ProviderError(RuntimeError):
    """查余额失败的可展示错误。"""


@dataclass
class ModelPrice:
    cache_hit_input: float
    cache_miss_input: float
    output: float
    cache_storage: float = 0.0


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self):
        if not self.total_tokens:
            self.total_tokens = self.prompt_tokens + self.completion_tokens

    @classmethod
    def from_openai(cls, payload: dict) -> "Usage":
        """直接吃 OpenAI 风格的 usage 字典。"""
        payload = payload or {}
        prompt = payload.get("prompt_tokens") or payload.get("input_tokens") or 0
        completion = payload.get("completion_tokens") or payload.get("output_tokens") or 0
        details = payload.get("prompt_tokens_details") or {}
        cache_hit = payload.get("prompt_cache_hit_tokens") or details.get("cached_tokens") or 0
        return cls(
            prompt_tokens=int(prompt),
            completion_tokens=int(completion),
            cache_hit_tokens=int(cache_hit),
            total_tokens=int(payload.get("total_tokens") or 0),
        )


@dataclass
class BalanceInfo:
    total_balance: float
    granted_balance: float = 0.0
    topped_up_balance: float = 0.0
    currency: str = "CNY"
    is_available: bool = True
    #: 余额是本地估算（服务商没有开放余额接口）时为 True，界面上会标注
    estimated: bool = False


class Provider(ABC):
    name: str = ""
    display_name: str = ""
    currency: str = "CNY"
    base_url: str = ""
    #: True 表示余额靠本地估算，必须在界面上说明
    balance_is_estimate: bool = False
    #: True 表示必须在配置里填 initial_balance 才能估算余额
    requires_initial_balance: bool = False

    def __init__(self, api_key: str, config: dict):
        self.api_key = api_key
        self.config = config or {}
        self.currency = self.config.get("currency") or "CNY"
        self._spend_source: Optional[Callable[[], float]] = None

    # -- 子类实现 --------------------------------------------------------
    @abstractmethod
    def get_balance(self) -> BalanceInfo:
        ...

    @abstractmethod
    def calculate_cost(self, model: str, usage: Usage, now: datetime) -> float:
        ...

    @abstractmethod
    def get_model_price(self, model: str) -> Optional[ModelPrice]:
        ...

    # -- 通用 ------------------------------------------------------------
    def attach_spend_source(self, source: Callable[[], float]) -> None:
        """让 provider 能读到 Monitor 里记录的累计花费（智谱估余额要用）。"""
        self._spend_source = source

    def spend_so_far(self) -> float:
        if self._spend_source is None:
            return 0.0
        try:
            return float(self._spend_source())
        except Exception:
            return 0.0

    def has_price(self, model: str) -> bool:
        return self.get_model_price(model) is not None

    def is_peak_hour(self, now: datetime) -> bool:
        return False

    def get_display_name(self) -> str:
        return self.display_name or self.name
