"""配置的读取、校验、合并与保存。

旧的 ``SettingsDialog`` 会用界面上的几个字段重建整个 provider 字典，
导致 ``models`` 之类的手写字段被静默删掉；这里的 ``merge`` 改成深度合并，
只覆盖界面确实编辑过的键。
"""

from __future__ import annotations

import copy
import logging

from . import paths

log = logging.getLogger(__name__)

CURRENCIES = ("CNY", "USD")

DEFAULT_CONFIG = {
    "currency": "CNY",
    "polling_interval": 60,
    "total_spend_alert": 100.0,
    "alert_cooldown": 1800,
    "pricing": {
        # DeepSeek 错峰优惠：北京时间 00:30-08:30，具体倍率与时段以官方文档为准，
        # 官方调价后直接改这两项，不用改代码。
        "off_peak_window": ["00:30", "08:30"],
        "off_peak_multiplier": 0.5,
    },
    "providers": {
        "deepseek": {
            "enabled": False,
            "api_key": "",
            "balance_alert": 10.0,
            "spend_alert": 50.0,
            "currency": "CNY",
        },
        "zhipu": {
            "enabled": False,
            "api_key": "",
            "initial_balance": 100.0,
            "balance_alert": 10.0,
            "spend_alert": 50.0,
            "currency": "CNY",
        },
    },
}


def defaults() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


def _fill(target: dict, defaults_map: dict) -> dict:
    """把缺失的键补上，但绝不覆盖用户已有的值。"""
    for key, value in defaults_map.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _fill(target[key], value)
    return target


def with_defaults(data: dict) -> dict:
    merged = copy.deepcopy(data if isinstance(data, dict) else {})
    _fill(merged, DEFAULT_CONFIG)
    return normalize(merged)


def load(path=None) -> dict:
    target = path or paths.config_path()
    raw = paths.read_json(target)
    if raw is None:
        log.info("未找到配置文件 %s，使用默认配置", target)
        return defaults()
    if not isinstance(raw, dict):
        log.error("配置文件 %s 顶层不是对象，改用默认配置", target)
        return defaults()
    return with_defaults(raw)


def save(cfg: dict, path=None) -> None:
    target = path or paths.config_path()
    paths.write_json(target, normalize(copy.deepcopy(cfg)))
    log.info("配置已写入 %s", target)


def merge(current: dict, overrides: dict) -> dict:
    """深度合并：overrides 里的 dict 只覆盖自己出现的键。"""
    result = copy.deepcopy(current)

    def _walk(dst: dict, src: dict) -> None:
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                _walk(dst[key], value)
            else:
                dst[key] = copy.deepcopy(value)

    _walk(result, overrides)
    return normalize(result)


def _as_float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_int(value, fallback: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def normalize(cfg: dict) -> dict:
    """把明显不合理的值夹回可用范围，避免 0 秒轮询把 CPU 打满。"""
    cfg["currency"] = cfg.get("currency") if cfg.get("currency") in CURRENCIES else "CNY"
    cfg["polling_interval"] = max(5, _as_int(cfg.get("polling_interval"), 60))
    cfg["total_spend_alert"] = max(0.0, _as_float(cfg.get("total_spend_alert"), 100.0))
    cfg["alert_cooldown"] = max(0, _as_int(cfg.get("alert_cooldown"), 1800))

    pricing = cfg.setdefault("pricing", {})
    if not isinstance(pricing.get("off_peak_multiplier"), (int, float)):
        pricing["off_peak_multiplier"] = 0.5
    pricing["off_peak_multiplier"] = min(1.0, max(0.0, float(pricing["off_peak_multiplier"])))
    window = pricing.get("off_peak_window")
    if not (isinstance(window, (list, tuple)) and len(window) == 2):
        pricing["off_peak_window"] = ["00:30", "08:30"]

    providers = cfg.setdefault("providers", {})
    if not isinstance(providers, dict):
        cfg["providers"] = providers = {}
    for name, spec in providers.items():
        if not isinstance(spec, dict):
            providers[name] = spec = {}
        spec["enabled"] = bool(spec.get("enabled", False))
        spec["api_key"] = str(spec.get("api_key") or "").strip()
        spec["balance_alert"] = max(0.0, _as_float(spec.get("balance_alert"), 10.0))
        spec["spend_alert"] = max(0.0, _as_float(spec.get("spend_alert"), 50.0))
        spec["currency"] = spec.get("currency") if spec.get("currency") in CURRENCIES else cfg["currency"]
        if "initial_balance" in spec or name == "zhipu":
            spec["initial_balance"] = max(0.0, _as_float(spec.get("initial_balance"), 0.0))
    return cfg
