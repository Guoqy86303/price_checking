"""业务核心：把服务商、轮询、花费记账、告警串起来。

这一层**不 import tkinter**，界面通过 ``on_change`` / ``on_alert`` 注册回调，
再自己 ``after`` 到主线程。这样 ``python main.py --once``、单元测试
和 GUI 用的是同一套逻辑。

线程模型：
* 轮询线程调用 ``_handle_balances``，只改受锁保护的内部状态；
* 回调在调用方线程里触发，界面负责投递到主线程（app.py 用 queue）。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from . import config as config_mod
from .alerter import Alert, Alerter
from .balance import BalancePoller
from .providers.base import BalanceInfo, Provider, Usage
from .providers.deepseek import DeepSeekProvider
from .providers.zhipu import ZhipuProvider
from .state import StateStore

log = logging.getLogger(__name__)

PROVIDER_CLASSES = {
    "deepseek": DeepSeekProvider,
    "zhipu": ZhipuProvider,
}


class Monitor:
    def __init__(
        self,
        config_path=None,
        config: Optional[dict] = None,
        state_path=None,
        provider_classes: Optional[dict] = None,
    ):
        self.config_path = config_path
        self.config = config if config is not None else config_mod.load(config_path)
        self.config = config_mod.normalize(self.config)
        self._classes = dict(provider_classes or PROVIDER_CLASSES)
        self._lock = threading.RLock()
        self._providers: list[Provider] = []
        self._balances: dict[str, BalanceInfo] = {}
        self._errors: dict[str, str] = {}
        self._spends: dict[str, float] = {}
        self._unknown_models: set[str] = set()
        self.state = StateStore(state_path)
        # 先把历史累计读进来，否则界面上的「累计花费」每次重启都从 0 开始
        self._spends.update(self.state.load_spends())
        self.alerter = Alerter(cooldown=float(self.config.get("alert_cooldown", 1800)))
        self._change_listeners: list[Callable[[], None]] = []
        self._alert_listeners: list[Callable[[Alert], None]] = []
        self.last_update: Optional[datetime] = None
        self._started = False
        self._poller = BalancePoller(interval=self.polling_interval, on_result=self._handle_balances)
        self.rebuild(notify=False)

    # -- 生命周期 --------------------------------------------------------
    def start(self) -> None:
        self._started = True
        self._poller.start()

    def stop(self) -> None:
        self._started = False
        self._poller.stop()
        with self._lock:
            self.state.save_spends(self._spends)

    def refresh_async(self) -> None:
        self._poller.query_now()

    def refresh_now(self) -> None:
        """同步查询一次（CLI / 测试用，界面请用 refresh_async）。"""
        self._poller.refresh_sync()

    # -- 属性 ------------------------------------------------------------
    @property
    def polling_interval(self) -> int:
        try:
            return max(5, int(self.config.get("polling_interval", 60)))
        except (TypeError, ValueError):
            return 60

    @property
    def providers(self) -> list[Provider]:
        with self._lock:
            return list(self._providers)

    def symbol(self) -> str:
        return "¥" if self.config.get("currency", "CNY") == "CNY" else "$"

    def provider_names(self) -> dict:
        return {p.name: p.get_display_name() for p in self.providers}

    def get_balances(self) -> dict:
        with self._lock:
            return dict(self._balances)

    def get_spends(self) -> dict:
        with self._lock:
            return dict(self._spends)

    def get_errors(self) -> dict:
        with self._lock:
            return dict(self._errors)

    def get_total_spend(self) -> float:
        with self._lock:
            return sum(self._spends.values())

    def get_unknown_models(self) -> set:
        with self._lock:
            return set(self._unknown_models)

    def spend_since(self) -> Optional[str]:
        return self.state.first_seen

    # -- 配置 ------------------------------------------------------------
    def rebuild(self, notify: bool = True) -> None:
        """按当前 self.config 重建服务商与轮询器（设置保存后调用，无需重启）。"""
        with self._lock:
            providers: list[Provider] = []
            for name, spec in (self.config.get("providers") or {}).items():
                if not spec.get("enabled"):
                    continue
                api_key = str(spec.get("api_key") or "").strip()
                if not api_key:
                    log.info("%s 已启用但没有填 API Key，跳过", name)
                    continue
                cls = self._classes.get(name) or PROVIDER_CLASSES.get(name)
                if cls is None:
                    log.warning("未知服务商 %s，跳过", name)
                    continue
                try:
                    provider = cls(api_key, {**spec, "pricing": self.config.get("pricing", {})})
                except Exception:
                    log.exception("初始化服务商 %s 失败", name)
                    continue
                providers.append(provider)

            active = {p.name for p in providers}
            self._providers = providers
            # 不裁剪历史花费：临时停用某个服务商不该把它的累计消费抹掉
            for provider in providers:
                self._spends.setdefault(provider.name, 0.0)
            self._balances = {k: v for k, v in self._balances.items() if k in active}
            self._errors = {k: v for k, v in self._errors.items() if k in active}
            for provider in providers:
                provider.attach_spend_source(
                    lambda name=provider.name: self.get_spends().get(name, 0.0)
                )

        self._poller.set_providers(providers)
        self._poller.set_interval(self.polling_interval)
        self.alerter.set_cooldown(float(self.config.get("alert_cooldown", 1800)))
        if notify:
            self._notify_change()

    def apply_config(self, cfg: dict) -> None:
        self.config = config_mod.normalize(cfg)
        self.rebuild(notify=False)
        if self._started:
            self._poller.query_now()
        self._notify_change()

    # -- 记账 ------------------------------------------------------------
    def record_usage(self, provider: str, model: str, usage: Usage, now: Optional[datetime] = None) -> float:
        """记录一次调用花费，返回本次花费。未知模型按 0 计但会记 warning。"""
        moment = now or datetime.now()
        with self._lock:
            target = next((p for p in self._providers if p.name == provider), None)
            if target is None:
                log.debug("忽略未启用服务商的花费记录: %s", provider)
                return 0.0
            if not target.has_price(model):
                if model not in self._unknown_models:
                    self._unknown_models.add(model)
                    log.warning("价目表里没有模型 %s，本次按 0 元计（请在 prices.py 补充）", model)
                cost = 0.0
            else:
                cost = float(target.calculate_cost(model, usage, moment))
            self._spends[provider] = self._spends.get(provider, 0.0) + cost
            self.state.save_spends(self._spends)
        self._notify_change()
        self._maybe_alert()
        return cost

    def record_from_usage_dict(self, provider: str, model: str, payload: dict, now=None) -> float:
        """接 OpenAI 风格 usage 字典的便捷入口。"""
        return self.record_usage(provider, model, Usage.from_openai(payload), now)

    # -- 轮询回调 --------------------------------------------------------
    def _handle_balances(self, balances: dict, errors: dict) -> None:
        with self._lock:
            for name, info in balances.items():
                self._balances[name] = info
                self._errors.pop(name, None)
            for name, message in errors.items():
                self._errors[name] = message
            self.last_update = datetime.now()
        self._notify_change()
        self._maybe_alert()

    def _maybe_alert(self) -> None:
        listeners = list(self._alert_listeners)
        if not listeners:
            return
        alerts = self.alerter.evaluate(
            balances=self.get_balances(),
            spends={p.name: self.get_spends().get(p.name, 0.0) for p in self.providers},
            providers_cfg=self.config.get("providers") or {},
            total_spend=self.get_total_spend(),
            total_threshold=self.config.get("total_spend_alert", 0),
            symbol=self.symbol(),
            labels=self.provider_names(),
        )
        for alert in alerts:
            log.info("告警: %s - %s", alert.title, alert.message)
            for listener in listeners:
                try:
                    listener(alert)
                except Exception:
                    log.exception("告警回调出错")

    # -- 订阅 ------------------------------------------------------------
    def on_change(self, callback: Callable[[], None]) -> None:
        self._change_listeners.append(callback)

    def on_alert(self, callback: Callable[[Alert], None]) -> None:
        self._alert_listeners.append(callback)

    def _notify_change(self) -> None:
        for callback in list(self._change_listeners):
            try:
                callback()
            except Exception:
                log.exception("状态回调出错")

    # -- 快照（CLI / 测试用）--------------------------------------------
    def snapshot(self) -> dict:
        balances = self.get_balances()
        spends = self.get_spends()
        errors = self.get_errors()
        rows = []
        for provider in self.providers:
            info = balances.get(provider.name)
            rows.append(
                {
                    "name": provider.name,
                    "label": provider.get_display_name(),
                    "balance": info.total_balance if info else None,
                    "estimated": bool(info.estimated) if info else False,
                    "spend": spends.get(provider.name, 0.0),
                    "error": errors.get(provider.name),
                    "balance_alert": (self.config["providers"].get(provider.name) or {}).get("balance_alert"),
                    "spend_alert": (self.config["providers"].get(provider.name) or {}).get("spend_alert"),
                }
            )
        return {
            "rows": rows,
            "total_spend": self.get_total_spend(),
            "total_alert": self.config.get("total_spend_alert", 0),
            "symbol": self.symbol(),
            "updated": self.last_update.strftime("%Y-%m-%d %H:%M:%S") if self.last_update else None,
            "unknown_models": sorted(self.get_unknown_models()),
            "interval": self.polling_interval,
        }
