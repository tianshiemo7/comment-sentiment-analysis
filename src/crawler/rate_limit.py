"""限速与退避策略（可注入时钟/休眠，方便单元测试与离线基准）。

与原实现「每次 ``time.sleep(delay)``」相比，本模块解决三个问题：

1. **间隔不叠加**：以「上一次请求开始时间」为基准计算还需要等多久，重试 / 解析
   耗时不会被重复计入，实际吞吐更稳定；
2. **抖动（jitter）**：每次间隔在 ``[interval, interval * (1 + jitter)]`` 内随机，
   避免固定周期形成可识别的机器节奏，也避免多个任务同时打点；
3. **自适应降速**：触发风控时 ``penalize()`` 把间隔按倍数拉长，成功若干次后再
   ``relax()`` 缓慢回落，既能在被限流时及时退让，也不会永久变慢。

所有时间源都可注入（``clock`` / ``sleep`` / ``rng``），测试无需真的等待。
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class RateLimiter:
    """基于「最小间隔 + 抖动 + 自适应惩罚」的保守限速器。

    Args:
        min_interval: 两次请求的最小间隔（秒），必须 ≥ 0。
        jitter: 抖动比例；``0.3`` 表示间隔在 ``[min_interval, 1.3 * min_interval]``。
        max_interval: 自适应降速的上限（秒），防止被风控后无限拉长。
        clock: 单调时钟，默认 ``time.monotonic``。
        sleep: 休眠函数，默认 ``time.sleep``。
        rng: 随机数发生器，默认新建 ``random.Random()``（可传固定种子复现）。
    """

    def __init__(
        self,
        min_interval: float = 2.5,
        jitter: float = 0.3,
        max_interval: float = 60.0,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = time.sleep,
        rng: Optional[random.Random] = None,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval 不能为负数（限速是硬性要求）")
        if not 0 <= jitter < 1:
            raise ValueError("jitter 必须位于 [0, 1)")
        if max_interval < min_interval:
            raise ValueError("max_interval 不能小于 min_interval")
        self._base_interval = float(min_interval)
        self._interval = float(min_interval)
        self._jitter = float(jitter)
        self._max_interval = float(max_interval)
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._last_request: float | None = None
        self.total_wait = 0.0
        self.waits = 0

    @property
    def interval(self) -> float:
        """当前生效的最小间隔（含自适应惩罚后的值）。"""

        return self._interval

    @property
    def base_interval(self) -> float:
        return self._base_interval

    def next_delay(self) -> float:
        """返回下一次请求前应该等待的目标间隔（含抖动）。"""

        return self._interval * (1.0 + self._rng.random() * self._jitter)

    def wait(self) -> float:
        """阻塞到下一次允许请求的时刻，返回实际休眠秒数。"""

        now = self._clock()
        target = self.next_delay()
        if self._last_request is None:
            sleep_for = 0.0
        else:
            elapsed = now - self._last_request
            sleep_for = max(0.0, target - elapsed)
        if sleep_for > 0:
            self._sleep(sleep_for)
        self._last_request = self._clock()
        self.total_wait += sleep_for
        self.waits += 1
        return sleep_for

    def penalize(self, factor: float = 1.5) -> float:
        """触发风控时调用：按倍数拉长间隔，返回新的间隔。"""

        if factor <= 1:
            raise ValueError("penalty factor 必须大于 1")
        self._interval = min(self._max_interval, max(self._interval * factor, self._base_interval))
        return self._interval

    def relax(self, factor: float = 0.9) -> float:
        """连续成功时缓慢回落间隔，但不低于基础间隔。"""

        if not 0 < factor <= 1:
            raise ValueError("relax factor 必须位于 (0, 1]")
        self._interval = max(self._base_interval, self._interval * factor)
        return self._interval

    def reset(self) -> None:
        """恢复基础间隔并清空上次请求时间（用于换任务 / 测试）。"""

        self._interval = self._base_interval
        self._last_request = None


def backoff_delay(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.25,
    rng: Optional[random.Random] = None,
) -> float:
    """指数退避：``attempt`` 从 1 开始，返回第 ``attempt`` 次重试前的等待秒数。

    结果带比例抖动，避免多个客户端同时重试形成尖峰；上界为 ``cap * (1 + jitter)``。
    """

    if attempt < 1:
        raise ValueError("attempt 必须从 1 开始")
    if base <= 0 or cap <= 0:
        raise ValueError("base / cap 必须为正数")
    rng = rng or random.Random()
    raw = min(cap, base * (2 ** (attempt - 1)))
    return raw * (1.0 + rng.random() * max(0.0, jitter))


def parse_retry_after(value: Optional[str]) -> float | None:
    """解析 ``Retry-After`` 响应头（秒数或 HTTP 日期），无法解析返回 ``None``。"""

    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        pass
    else:
        return max(0.0, seconds)
    try:
        target = parsedate_to_datetime(value)
        if target is None:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        delta = (target - datetime.now(target.tzinfo)).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError, OverflowError):
        return None
