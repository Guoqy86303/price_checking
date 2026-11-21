"""模型价目表与匹配。

价格单位：**元 / 百万 token（CNY）**。表里的数字必须能追溯到官方价目页，
改了就在这里改，并更新 ``PRICES_UPDATED``。

匹配策略：精确命中优先，其次取最长的前缀命中，因此
``deepseek-v4-flash-20260101`` 这种带日期后缀的模型也能算对价。
真的没命中时由调用方记一条 warning，而不是静默按 0 元记账。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .providers.base import ModelPrice

PRICES_UPDATED = "2026-11"
PRICES_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"

# DeepSeek（元 / 百万 token，标准时段价；错峰时段再乘 config 里的倍率）
DEEPSEEK = {
    "deepseek-flash": ModelPrice(0.04, 2.0, 8.0),
    "deepseek-v4-flash": ModelPrice(0.04, 2.0, 8.0),
    "deepseek-v4-pro": ModelPrice(0.30, 9.0, 27.0),
}


@dataclass
class TieredPrice:
    """按输入长度分档的价目（智谱部分模型）。"""

    threshold_tokens: int
    low_cache_hit_input: float
    low_cache_miss_input: float
    low_output: float
    high_cache_hit_input: float
    high_cache_miss_input: float
    high_output: float

    def pick(self, prompt_tokens: int) -> ModelPrice:
        if prompt_tokens < self.threshold_tokens:
            return ModelPrice(self.low_cache_hit_input, self.low_cache_miss_input, self.low_output)
        return ModelPrice(self.high_cache_hit_input, self.high_cache_miss_input, self.high_output)


# 智谱（元 / 百万 token）
ZHIPU_STANDARD = {
    "glm-5.3": ModelPrice(2.0, 8.0, 28.0),
    "glm-5.2": ModelPrice(2.0, 8.0, 28.0),
    "glm-5.3-flash": ModelPrice(0.23, 0.8, 2.8),
    "glm-5.3-flashx": ModelPrice(0.57, 2.0, 7.0),
    "glm-5.3-flashx-legal": ModelPrice(0.57, 2.0, 7.0),
    "glm-5": ModelPrice(1.0, 4.0, 18.0),
    "glm-4.5": ModelPrice(1.0, 4.0, 18.0),
    "glm-4.7": ModelPrice(0.6, 3.0, 14.0),
    "glm-4.7-flashx": ModelPrice(0.1, 0.5, 3.0),
    "glm-4.7-flash": ModelPrice(0.0, 0.0, 0.0),
    "glm-4.5-air": ModelPrice(0.16, 0.8, 2.0),
    "glm-4.5-airx": ModelPrice(0.16, 0.8, 2.0),
    "glm-4-4": ModelPrice(0.5, 1.0, 1.0),
    "glm-4-flash": ModelPrice(0.0, 0.0, 0.0),
    "glm-4-flashx": ModelPrice(0.05, 0.1, 0.1),
    "glm-4-long": ModelPrice(0.5, 1.0, 1.0),
    "glm-4-air-250414": ModelPrice(0.25, 0.5, 0.5),
    "glm-4.5v": ModelPrice(0.4, 2.0, 6.0),
    "glm-4.6v": ModelPrice(0.2, 1.0, 3.0),
    "glm-4.6v-flash": ModelPrice(0.0, 0.0, 0.0),
    "glm-4.6v-flashx": ModelPrice(0.03, 0.15, 1.5),
    "glm-4.1v-thinking-flash": ModelPrice(0.0, 0.0, 0.0),
    "glm-4.1v-thinking-flashx": ModelPrice(0.0, 2.0, 2.0),
    "glm-5v-turbo": ModelPrice(1.2, 5.0, 22.0),
    "glm-ocr": ModelPrice(0.0, 0.2, 0.2),
    "embedding-3": ModelPrice(0.0, 0.5, 0.0),
    "embedding-2": ModelPrice(0.0, 0.5, 0.0),
    "codegeex-4": ModelPrice(0.0, 0.1, 0.0),
    "rerank": ModelPrice(0.0, 0.8, 0.0),
    "glm-z1-air": ModelPrice(0.25, 0.5, 0.5),
    "glm-z1-airx": ModelPrice(0.0, 5.0, 5.0),
    "glm-z1-flash": ModelPrice(0.0, 0.0, 0.0),
    "glm-z1-flashx": ModelPrice(0.0, 0.1, 0.1),
}

ZHIPU_TIERED = {
    "glm-5.1": TieredPrice(32768, 1.3, 6.0, 24.0, 2.0, 8.0, 28.0),
    "glm-5-turbo": TieredPrice(32768, 1.2, 5.0, 22.0, 1.8, 7.0, 26.0),
    "glm-5": TieredPrice(32768, 1.0, 4.0, 18.0, 1.5, 6.0, 22.0),
    "glm-4.7": TieredPrice(32768, 0.6, 3.0, 14.0, 0.8, 4.0, 16.0),
    "glm-4.5-air": TieredPrice(32768, 0.16, 0.8, 2.0, 0.24, 1.2, 8.0),
    "glm-4.6v": TieredPrice(32768, 0.2, 1.0, 3.0, 0.4, 2.0, 6.0),
    "glm-5v-turbo": TieredPrice(32768, 1.2, 5.0, 22.0, 1.8, 7.0, 26.0),
}


def _lookup(table: dict, model: str):
    if not model:
        return None
    key = str(model).strip().lower()
    if key in table:
        return table[key]
    candidates = [name for name in table if key.startswith(name)]
    if candidates:
        return table[max(candidates, key=len)]
    return None


def deepseek_price(model: str) -> Optional[ModelPrice]:
    return _lookup(DEEPSEEK, model)


def zhipu_price(model: str, prompt_tokens: int = 0) -> Optional[ModelPrice]:
    tier = _lookup(ZHIPU_TIERED, model)
    if tier is not None:
        return tier.pick(prompt_tokens)
    return _lookup(ZHIPU_STANDARD, model)


def price_for(provider: str, model: str, prompt_tokens: int = 0) -> Optional[ModelPrice]:
    if provider == "deepseek":
        return deepseek_price(model)
    if provider == "zhipu":
        return zhipu_price(model, prompt_tokens)
    return None
