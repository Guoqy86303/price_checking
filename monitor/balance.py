"""余额轮询器。

三条规则：
1. 轮询在后台线程里跑，界面永远不阻塞；
2. ``query_now()`` 异步触发一次查询，不会卡住 Tk 主线程；
3. 每个服务商的成功/失败分开上报，界面上能看到「为什么是 ----」。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)


class BalancePoller:
    def __init__(
        self,
        providers: Optional[list] = None,
        interval: int = 60,
        on_result: Optional[Callable[[dict, dict], None]] = None,
    ):
        self._providers = list(providers or [])
        self._interval = max(5, int(interval))
        self._on_result = on_result
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._query_lock = threading.Lock()

    # -- 配置 ------------------------------------------------------------
    def set_providers(self, providers: list) -> None:
        self._providers = list(providers or [])

    def set_interval(self, interval: int) -> None:
        self._interval = max(5, int(interval))
        self._wake.set()

    def on_result(self, callback: Callable[[dict, dict], None]) -> None:
        self._on_result = callback

    # -- 生命周期 --------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="balance-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def query_now(self) -> None:
        """异步查一次，UI 线程调用安全。"""
        threading.Thread(target=self._query_all, name="balance-query", daemon=True).start()

    def refresh_sync(self) -> None:
        """同步查一次，只给 CLI / 测试用；界面里别调，会卡。"""
        self._query_all()

    # -- 内部 ------------------------------------------------------------
    def _loop(self) -> None:
        self._query_all()
        while not self._stop.is_set():
            self._wake.wait(timeout=self._interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            self._query_all()

    def _query_all(self) -> None:
        if not self._query_lock.acquire(blocking=False):
            return  # 上一轮还没结束
        try:
            results: dict = {}
            errors: dict = {}
            for provider in list(self._providers):
                try:
                    info = provider.get_balance()
                except Exception as exc:  # noqa: BLE001 - 逐个服务商兜底
                    message = str(exc) or exc.__class__.__name__
                    errors[provider.name] = message
                    log.warning("查询 %s 余额失败: %s", provider.name, message)
                    continue
                if info is None:
                    errors[provider.name] = "服务商没有返回余额"
                    continue
                results[provider.name] = info
            if self._on_result is not None:
                try:
                    self._on_result(results, errors)
                except Exception:
                    log.exception("处理余额结果时出错")
        finally:
            self._query_lock.release()
