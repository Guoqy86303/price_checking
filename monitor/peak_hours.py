"""DeepSeek 的高峰/错峰时段。

官方口径是**北京时间每日 00:30-08:30 为优惠（错峰）时段**，其余时间按标准价。
时段和倍率都在 ``config.json`` 的 ``pricing`` 里可覆盖，官方调价时改配置即可。

（旧版本把工作日 9-12、14-18 点当成高峰，并硬编码了一份 2026 年节假日表，
白天有一大半时间在按 5 折算钱，跟实际账单对不上；已经去掉。）
"""

from __future__ import annotations

from datetime import datetime, time

DEFAULT_WINDOW = ("00:30", "08:30")


def _parse(value: str) -> time:
    hour, _, minute = str(value).partition(":")
    return time(int(hour) % 24, int(minute or 0) % 60)


def parse_window(window) -> tuple:
    try:
        start, end = window
        return _parse(start), _parse(end)
    except (TypeError, ValueError):
        return _parse(DEFAULT_WINDOW[0]), _parse(DEFAULT_WINDOW[1])


def is_off_peak(dt: datetime, window=DEFAULT_WINDOW) -> bool:
    start, end = parse_window(window)
    current = dt.time()
    if start <= end:
        return start <= current < end
    # 跨零点，例如 23:00-07:00
    return current >= start or current < end


def is_peak(dt: datetime, window=DEFAULT_WINDOW) -> bool:
    return not is_off_peak(dt, window)


# 兼容旧调用名
def is_deepseek_peak(dt: datetime) -> bool:
    return is_peak(dt)
