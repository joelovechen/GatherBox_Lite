"""接口限流器与风控退避调度中心 (令牌桶算法 + 请求队列 + 失败重试 + 实时监控).

功能特性：
1. 经典令牌桶算法 (Token Bucket)，支持平滑限速与突发控制。
2. 线程安全队列，高并发下所有对 B站 的请求统一调度。
3. 随机抖动 (Jitter)，避免固定时间指纹被 B站 反爬识别。
4. 智能风控自适应感知 (Banned Cooldown)：当遇到 -412 / 412 / request was banned 时，
   自动进入冷却退避状态，暂停全队列，避免账号和 IP 被持续加黑。
5. 实时监控指标（当前可用令牌数、排队长度、请求统计、最近请求日志流）。
6. 支持动态调速与持久化配置（预设档位与自由微调）。
"""

import collections
import random
import threading
import time
from typing import Any, Dict, List, Optional


class TokenBucketLimiter:
    """线程安全的令牌桶限流与排队器."""

    def __init__(
        self,
        rate: float = 0.8,         # 每秒产生令牌数 (QPS)
        capacity: float = 3.0,     # 令牌桶最大容量
        min_interval: float = 1.0, # 请求间最小间隔 (秒)
        jitter: float = 0.4,       # 随机抖动范围 (±秒)
        ban_cooldown: float = 15.0,# 遇到风控 (-412 / 403 / 429) 时的惩罚冷却时间 (秒)
        max_retries: int = 3,      # 失败重试次数
        platform_name: str = "Bilibili"
    ):
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.platform_name = platform_name

        # 核心限流参数
        self.rate = float(rate)
        self.capacity = float(capacity)
        self.min_interval = float(min_interval)
        self.jitter = float(jitter)
        self.ban_cooldown = float(ban_cooldown)
        self.max_retries = int(max_retries)

        # 运行状态
        self.tokens = float(capacity)
        self.last_update = time.time()
        self.last_request_time = 0.0
        self.is_paused = False

        # 风控冷却状态
        self.banned_until = 0.0
        self.last_ban_reason = ""

        # 统计指标
        self.queue_size = 0
        self.total_requests = 0
        self.success_requests = 0
        self.banned_requests = 0
        self.retried_requests = 0

        # 最近请求日志 (保留最近 60 条)
        self.logs: collections.deque = collections.deque(maxlen=60)

    def update_config(
        self,
        rate: Optional[float] = None,
        capacity: Optional[float] = None,
        min_interval: Optional[float] = None,
        jitter: Optional[float] = None,
        ban_cooldown: Optional[float] = None,
        max_retries: Optional[int] = None,
        is_paused: Optional[bool] = None
    ) -> Dict[str, Any]:
        """动态更新配置."""
        with self._lock:
            if rate is not None and rate > 0:
                self.rate = float(rate)
            if capacity is not None and capacity > 0:
                self.capacity = float(capacity)
                if self.tokens > self.capacity:
                    self.tokens = self.capacity
            if min_interval is not None and min_interval >= 0:
                self.min_interval = float(min_interval)
            if jitter is not None and jitter >= 0:
                self.jitter = float(jitter)
            if ban_cooldown is not None and ban_cooldown >= 0:
                self.ban_cooldown = float(ban_cooldown)
            if max_retries is not None and max_retries >= 0:
                self.max_retries = int(max_retries)
            if is_paused is not None:
                self.is_paused = bool(is_paused)
                if not self.is_paused:
                    self._cond.notify_all()
            return self.get_config()

    def get_config(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "rate": round(self.rate, 2),
                "capacity": round(self.capacity, 2),
                "min_interval": round(self.min_interval, 2),
                "jitter": round(self.jitter, 2),
                "ban_cooldown": round(self.ban_cooldown, 1),
                "max_retries": self.max_retries,
                "is_paused": self.is_paused,
            }

    def _replenish(self, now: float):
        """按时间差补充令牌."""
        delta = now - self.last_update
        if delta > 0:
            self.tokens = min(self.capacity, self.tokens + delta * self.rate)
            self.last_update = now

    def acquire(self, endpoint_name: str = "") -> float:
        """从令牌桶获取通行许可，必要时在队列中等待.

        :return: 实际等待耗时 (秒)
        """
        start_wait = time.time()
        with self._lock:
            self.queue_size += 1
            try:
                while True:
                    now = time.time()

                    # 1. 检查手动暂停
                    if self.is_paused:
                        self._cond.wait(timeout=0.5)
                        continue

                    # 2. 检查风控冷却倒计时
                    if now < self.banned_until:
                        sleep_left = self.banned_until - now
                        self._cond.wait(timeout=min(sleep_left, 1.0))
                        continue

                    # 3. 补充令牌
                    self._replenish(now)

                    # 4. 检查距离上一次请求的最小间隔 (加随机抖动)
                    target_interval = self.min_interval
                    if self.jitter > 0:
                        target_interval += random.uniform(-self.jitter * 0.5, self.jitter)
                    time_since_last = now - self.last_request_time
                    interval_wait = max(0.0, target_interval - time_since_last)

                    # 5. 检查令牌是否足够
                    if self.tokens >= 1.0 and interval_wait <= 0:
                        self.tokens -= 1.0
                        self.last_request_time = time.time()
                        break

                    # 计算需要等待的时长
                    token_wait = 0.0
                    if self.tokens < 1.0:
                        token_wait = (1.0 - self.tokens) / max(self.rate, 0.01)

                    wait_needed = max(interval_wait, token_wait, 0.05)
                    self._cond.wait(timeout=min(wait_needed, 0.5))
            finally:
                self.queue_size = max(0, self.queue_size - 1)

        waited = time.time() - start_wait
        return waited

    def on_banned(self, reason: str = "request was banned"):
        """当捕获到平台风控拦截异常时触发."""
        with self._lock:
            self.banned_requests += 1
            now = time.time()
            self.banned_until = now + self.ban_cooldown
            self.last_ban_reason = reason
            # 清空当前桶中令牌，迫使所有等待请求冷却后重新补充
            self.tokens = 0.0
            self.last_update = self.banned_until
            print(f"[!] 触发 [{self.platform_name}] 防爬风控拦截: {reason}，限流器已自动进入保护性冷却 (暂停 {self.ban_cooldown} 秒) ...", flush=True)
            self._cond.notify_all()

    def record_request(
        self,
        method: str,
        url: str,
        status_code: int,
        code: Optional[int],
        duration_ms: float,
        is_banned: bool = False,
        retries: int = 0
    ):
        """记录请求审计日志与计数."""
        with self._lock:
            self.total_requests += 1
            if not is_banned and (status_code == 200 and (code == 0 or code is None)):
                self.success_requests += 1
            if retries > 0:
                self.retried_requests += retries

            # 简写 URL 方便展示
            clean_url = url.split("?")[0]
            if "bilibili.com" in clean_url:
                clean_url = clean_url.split("bilibili.com")[-1]
            elif "zhihu.com" in clean_url:
                clean_url = clean_url.split("zhihu.com")[-1]

            log_entry = {
                "id": self.total_requests,
                "time": time.strftime("%H:%M:%S", time.localtime()),
                "method": method.upper(),
                "url": clean_url,
                "status_code": status_code,
                "code": code,
                "duration_ms": round(duration_ms, 1),
                "is_banned": is_banned,
                "retries": retries
            }
            self.logs.appendleft(log_entry)

    def reset_stats(self):
        """重置统计计数器."""
        with self._lock:
            self.total_requests = 0
            self.success_requests = 0
            self.banned_requests = 0
            self.retried_requests = 0
            self.logs.clear()

    def get_status(self) -> Dict[str, Any]:
        """获取实时监控指标."""
        with self._lock:
            now = time.time()
            self._replenish(now)
            cooldown_left = max(0.0, self.banned_until - now)
            return {
                "platform": self.platform_name,
                "config": self.get_config(),
                "available_tokens": round(max(0.0, self.tokens), 2),
                "capacity": self.capacity,
                "queue_size": self.queue_size,
                "is_paused": self.is_paused,
                "is_cooling": cooldown_left > 0,
                "cooldown_left": round(cooldown_left, 1),
                "last_ban_reason": self.last_ban_reason if cooldown_left > 0 else "",
                "total_requests": self.total_requests,
                "success_requests": self.success_requests,
                "banned_requests": self.banned_requests,
                "retried_requests": self.retried_requests,
                "recent_logs": list(self.logs)[:35]
            }


# 全局平台限流器
bili_limiter = TokenBucketLimiter(
    rate=0.8,
    capacity=3.0,
    min_interval=1.0,
    jitter=0.4,
    ban_cooldown=15.0,
    max_retries=3,
    platform_name="Bilibili"
)

zhihu_limiter = TokenBucketLimiter(
    rate=0.6,
    capacity=2.0,
    min_interval=1.5,
    jitter=0.5,
    ban_cooldown=30.0,
    max_retries=3,
    platform_name="Zhihu"
)

# 兼容旧代码引用
limiter = bili_limiter
