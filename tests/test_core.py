"""核心逻辑回归测试（stdlib unittest，不需要额外依赖）。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import config as config_mod  # noqa: E402
from monitor import prices  # noqa: E402
from monitor.alerter import Alerter  # noqa: E402
from monitor.peak_hours import is_off_peak  # noqa: E402
from monitor.pricing import Monitor  # noqa: E402
from monitor.providers.base import BalanceInfo, Provider, Usage  # noqa: E402
from monitor.providers.deepseek import DeepSeekProvider  # noqa: E402
from monitor.providers.zhipu import ZhipuProvider  # noqa: E402
from monitor.state import StateStore  # noqa: E402


class FakeProvider(Provider):
    name = "deepseek"
    display_name = "Fake"

    def __init__(self, api_key, cfg, balance=50.0):
        super().__init__(api_key, cfg)
        self._balance = balance

    def get_balance(self):
        if self._balance is None:
            raise RuntimeError("boom")
        return BalanceInfo(self._balance, estimated=True)

    def get_model_price(self, model):
        return prices.deepseek_price(model)

    def calculate_cost(self, model, usage, now):
        price = self.get_model_price(model)
        if price is None:
            return 0.0
        return (
            usage.prompt_tokens / 1_000_000 * price.cache_miss_input
            + usage.completion_tokens / 1_000_000 * price.output
        )


class TestPeakHours(unittest.TestCase):
    def test_window_boundaries(self):
        self.assertTrue(is_off_peak(datetime(2026, 11, 21, 0, 30)))
        self.assertTrue(is_off_peak(datetime(2026, 11, 21, 3, 15)))
        self.assertTrue(is_off_peak(datetime(2026, 11, 21, 8, 29)))
        self.assertFalse(is_off_peak(datetime(2026, 11, 21, 8, 30)))
        self.assertFalse(is_off_peak(datetime(2026, 11, 21, 14, 0)))

    def test_cross_midnight_window(self):
        window = ["23:00", "07:00"]
        self.assertTrue(is_off_peak(datetime(2026, 11, 21, 23, 30), window))
        self.assertTrue(is_off_peak(datetime(2026, 11, 21, 6, 59), window))
        self.assertFalse(is_off_peak(datetime(2026, 11, 21, 12, 0), window))


class TestPrices(unittest.TestCase):
    def test_unknown_model_is_none(self):
        self.assertIsNone(prices.deepseek_price("some-new-model"))
        self.assertIsNone(prices.zhipu_price("nope"))

    def test_prefix_match_with_date_suffix(self):
        price = prices.deepseek_price("deepseek-v4-flash-20260101")
        self.assertIsNotNone(price)
        self.assertEqual(price.output, 8.0)

    def test_longest_prefix_wins(self):
        # glm-4-flashx 必须命中自己，而不是更短的 glm-4-flash
        self.assertEqual(prices.zhipu_price("glm-4-flashx").cache_miss_input, 0.1)

    def test_tiered_price_switches_at_threshold(self):
        low = prices.zhipu_price("glm-4.7", 1000)
        high = prices.zhipu_price("glm-4.7", 40000)
        self.assertEqual(low.output, 14.0)
        self.assertEqual(high.output, 16.0)

    def test_tiered_model_consistent_between_paths(self):
        provider = ZhipuProvider("k", {"currency": "CNY"})
        display = provider.get_model_price("glm-5-turbo")
        self.assertEqual(display.output, 22.0)  # 低档展示价
        cost_low = provider.calculate_cost("glm-5-turbo", Usage(1000, 0), datetime.now())
        cost_high = provider.calculate_cost("glm-5-turbo", Usage(40000, 0), datetime.now())
        self.assertLess(cost_low, cost_high)


class TestAlerter(unittest.TestCase):
    def _evaluate(self, alerter, moment, balance, spend=0.0, threshold=10.0, spend_threshold=50.0):
        return alerter.evaluate(
            balances={"deepseek": BalanceInfo(balance)},
            spends={"deepseek": spend},
            providers_cfg={"deepseek": {"balance_alert": threshold, "spend_alert": spend_threshold}},
            total_spend=spend,
            total_threshold=0,
            labels={"deepseek": "DeepSeek"},
            now=moment,
        )

    def test_no_spam_on_each_poll(self):
        clock = {"t": 0.0}
        alerter = Alerter(cooldown=1800, clock=lambda: clock["t"])
        alerts = []
        for index in range(6):
            clock["t"] = index * 60.0
            alerts += self._evaluate(alerter, clock["t"], 9.0 - index * 0.1)
        self.assertEqual(len(alerts), 1, "冷却期内只应弹一次")

    def test_rearm_after_recovery(self):
        clock = {"t": 0.0}
        alerter = Alerter(cooldown=1800, clock=lambda: clock["t"])
        self.assertEqual(len(self._evaluate(alerter, 0.0, 5.0)), 1)
        self.assertEqual(len(self._evaluate(alerter, 10.0, 50.0)), 0)   # 恢复
        self.assertEqual(len(self._evaluate(alerter, 20.0, 4.0)), 1)    # 再次越线
        self.assertEqual(len(self._evaluate(alerter, 30.0, 4.0)), 0)    # 冷却中

    def test_spend_alert_once_per_cooldown(self):
        clock = {"t": 0.0}
        alerter = Alerter(cooldown=1800, clock=lambda: clock["t"])
        alerts = []
        for index, spend in enumerate([51, 52, 53, 54]):
            clock["t"] = index * 60.0
            alerts += self._evaluate(alerter, clock["t"], 100.0, spend=spend)
        self.assertEqual(len(alerts), 1)


class TestConfig(unittest.TestCase):
    def test_merge_keeps_unknown_keys(self):
        current = {
            "polling_interval": 60,
            "providers": {"deepseek": {"enabled": True, "api_key": "k", "models": {"x": 1}, "notes": "手写"}},
        }
        merged = config_mod.merge(current, {"providers": {"deepseek": {"api_key": "new"}}})
        self.assertEqual(merged["providers"]["deepseek"]["api_key"], "new")
        self.assertEqual(merged["providers"]["deepseek"]["models"], {"x": 1})
        self.assertEqual(merged["providers"]["deepseek"]["notes"], "手写")

    def test_normalize_clamps_interval(self):
        cfg = config_mod.with_defaults({"polling_interval": 0})
        self.assertEqual(cfg["polling_interval"], 5)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            cfg = config_mod.defaults()
            cfg["providers"]["deepseek"]["api_key"] = "secret"
            config_mod.save(cfg, path)
            loaded = config_mod.load(path)
            self.assertEqual(loaded["providers"]["deepseek"]["api_key"], "secret")
            self.assertIn("pricing", loaded)


class TestMonitor(unittest.TestCase):
    def _monitor(self, balance=50.0, initial=100.0):
        cfg = config_mod.defaults()
        cfg["providers"]["deepseek"].update({"enabled": True, "api_key": "k"})
        cfg["providers"]["zhipu"].update({"enabled": True, "api_key": "k", "initial_balance": initial})

        class Zhipu(FakeProvider):
            name = "zhipu"
            display_name = "GLM"

        return Monitor(
            config=cfg,
            state_path=False,
            provider_classes={"deepseek": FakeProvider, "zhipu": Zhipu},
        )

    def test_record_usage_accumulates(self):
        monitor = self._monitor()
        cost = monitor.record_usage("deepseek", "deepseek-v4-flash", Usage(1_000_000, 0))
        self.assertAlmostEqual(cost, 2.0, places=6)
        self.assertAlmostEqual(monitor.get_total_spend(), 2.0, places=6)

    def test_unknown_model_costs_zero_and_is_tracked(self):
        monitor = self._monitor()
        cost = monitor.record_usage("deepseek", "mystery-model", Usage(1000, 100))
        self.assertEqual(cost, 0.0)
        self.assertIn("mystery-model", monitor.get_unknown_models())

    def test_balance_errors_are_exposed(self):
        monitor = self._monitor()
        monitor.providers[0]._balance = None
        monitor.refresh_now()
        self.assertIn("deepseek", monitor.get_errors())

    def test_zhipu_balance_is_estimate_of_initial_minus_spend(self):
        monitor = self._monitor(initial=100.0)
        monitor.record_usage("zhipu", "glm-5.3-flash", Usage(1_000_000, 0))
        monitor.refresh_now()
        info = monitor.get_balances()["zhipu"]
        self.assertTrue(info.estimated)
        self.assertLess(info.total_balance, 100.0)

    def test_apply_config_rebuilds_without_restart(self):
        monitor = self._monitor()
        self.assertEqual(len(monitor.providers), 2)
        cfg = dict(monitor.config)
        cfg["providers"] = {
            "deepseek": {"enabled": False, "api_key": "k"},
            "zhipu": {"enabled": True, "api_key": "k", "initial_balance": 10},
        }
        monitor.apply_config(cfg)
        self.assertEqual([p.name for p in monitor.providers], ["zhipu"])

    def test_state_store_persists_spends(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            store = StateStore(path)
            store.save_spends({"deepseek": 1.5})
            again = StateStore(path)
            self.assertEqual(again.load_spends(), {"deepseek": 1.5})
            self.assertIsNotNone(again.first_seen)


class TestDeepSeekPricing(unittest.TestCase):
    def test_off_peak_is_cheaper(self):
        provider = DeepSeekProvider("k", {"currency": "CNY", "pricing": {}})
        usage = Usage(1_000_000, 1_000_000)
        peak = provider.calculate_cost("deepseek-v4-flash", usage, datetime(2026, 11, 21, 12, 0))
        off = provider.calculate_cost("deepseek-v4-flash", usage, datetime(2026, 11, 21, 3, 0))
        self.assertAlmostEqual(off, peak * 0.5, places=6)


if __name__ == "__main__":
    unittest.main()
