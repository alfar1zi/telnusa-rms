"""In-process SSE broker with explicit, observable resource bounds.

SSE-01 invariants:
- Every subscriber has a bounded buffer. On overflow the buffer is collapsed to a
  single resync marker instead of silently dropping the newest or oldest event, so a
  slow client is told its view is stale instead of rendering a plausible-looking
  partial timeline.
- Every event carries a monotonic id, so a client can resume with Last-Event-ID.
- Connection counts are capped per device, per user and in total. Exhaustion is an
  explicit, recoverable rejection, never an unbounded fan-out.
"""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass

from .config import config

# Event types the frontend must be able to distinguish. A dropped/overflowed stream
# is NOT reported as a generic "offline": the client needs to know the backend is
# healthy and only its own buffer overflowed.
EVENT_RESYNC = "resync"


@dataclass(frozen=True)
class SubscriptionLimit:
    """Why a subscribe was refused. Surfaced verbatim to the client as SSE-01 detail."""

    scope: str      # "device" | "user" | "total"
    limit: int
    current: int


class SubscriptionRejected(RuntimeError):
    """Raised when a new SSE connection would exceed an explicit cap."""

    def __init__(self, limit: SubscriptionLimit):
        self.limit = limit
        super().__init__(
            f"SSE {limit.scope} connection limit reached ({limit.limit}, current {limit.current})."
        )


class Subscriber:
    """One client connection's bounded buffer plus its last-served sequence id."""

    __slots__ = ("device_id", "user_key", "queue", "last_event_id", "overflowed")

    def __init__(self, device_id: str, user_key: str, maxsize: int):
        self.device_id = device_id
        self.user_key = user_key
        self.queue: queue.Queue[str] = queue.Queue(maxsize=maxsize)
        self.last_event_id = 0
        self.overflowed = False

    def offer(self, data: str, event_id: int) -> None:
        """Enqueue without blocking. On overflow, collapse to a resync marker.

        Deterministic: the first overflow discards everything buffered and leaves
        exactly one resync marker, so the client resyncs from a known point rather
        than replaying an arbitrarily-truncated tail.

        The broker stamps its monotonic sequence into each frame as `_seq` so the
        HTTP route can emit honest `id:` fields. Unknown envelope keys are ignored
        by the client frame validator; the wire event shape is unchanged.
        """
        try:
            self.queue.put_nowait(_with_seq(data, event_id))
            return
        except queue.Full:
            pass

        self.overflowed = True
        self.last_event_id = event_id
        self._reset_to_resync(event_id)

    def _reset_to_resync(self, event_id: int) -> None:
        marker = _with_seq(
            json.dumps(
                {"type": EVENT_RESYNC, "payload": {"reason": "buffer_overflow"}},
                separators=(",", ":"),
            ),
            event_id,
        )
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        try:
            self.queue.put_nowait(marker)
        except queue.Full:  # pragma: no cover - queue was just drained
            self.overflowed = False

    def close(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                return


def _with_seq(data: str, event_id: int) -> str:
    """Stamp the broker sequence into a queued JSON frame as `_seq`."""
    try:
        obj = json.loads(data)
    except ValueError:
        return data
    if isinstance(obj, dict):
        obj["_seq"] = event_id
        return json.dumps(obj, separators=(",", ":"))
    return data


class EventBroker:
    def __init__(
        self,
        max_queue: int = 100,
        max_total: int = 200,
        max_per_device: int = 50,
        max_per_user: int = 10,
    ):
        self._lock = threading.Lock()
        self._subs: dict[str, list[Subscriber]] = {}
        self._max_queue = max_queue
        self._max_total = max_total
        self._max_per_device = max_per_device
        self._max_per_user = max_per_user
        self._seq = 0
        self.dropped_events = 0
        self.published_events = 0
        self.rejected_subscriptions = 0

    # -- connection accounting ------------------------------------------
    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._subs.values())

    def last_event_id(self) -> int:
        """Current head sequence id, used as the resume point for a fresh client."""
        with self._lock:
            return self._seq

    def _check_limits(self, device_id: str, user_key: str) -> None:
        """Caller must hold self._lock."""
        per_device = len(self._subs.get(device_id, ()))
        if per_device >= self._max_per_device:
            raise SubscriptionRejected(
                SubscriptionLimit("device", self._max_per_device, per_device)
            )
        per_user = sum(
            1
            for subs in self._subs.values()
            for s in subs
            if s.user_key == user_key
        )
        if per_user >= self._max_per_user:
            raise SubscriptionRejected(
                SubscriptionLimit("user", self._max_per_user, per_user)
            )
        total = sum(len(v) for v in self._subs.values())
        if total >= self._max_total:
            raise SubscriptionRejected(SubscriptionLimit("total", self._max_total, total))

    # -- lifecycle -------------------------------------------------------
    def subscribe(self, device_id: str, user_key: str = "anonymous") -> Subscriber:
        with self._lock:
            try:
                self._check_limits(device_id, user_key)
            except SubscriptionRejected:
                self.rejected_subscriptions += 1
                raise
            sub = Subscriber(device_id, user_key, self._max_queue)
            self._subs.setdefault(device_id, []).append(sub)
            return sub

    def unsubscribe(self, device_id: str, sub: Subscriber) -> None:
        sub.close()
        with self._lock:
            subs = self._subs.get(device_id)
            if not subs:
                return
            try:
                subs.remove(sub)
            except ValueError:
                return
            if not subs:
                self._subs.pop(device_id, None)

    # -- publishing ------------------------------------------------------
    def publish(self, device_id: str, event: dict) -> None:
        data = json.dumps(event, separators=(",", ":"))
        with self._lock:
            self._seq += 1
            event_id = self._seq
            subs = list(self._subs.get(device_id, ()))

        for s in subs:
            s.offer(data, event_id)
            with self._lock:
                self.published_events += 1
                if s.overflowed:
                    self.dropped_events += 1

    def stats(self) -> dict:
        with self._lock:
            return {
                "subscribers": sum(len(v) for v in self._subs.values()),
                "published_events": self.published_events,
                "dropped_events": self.dropped_events,
                "rejected_subscriptions": self.rejected_subscriptions,
                "last_event_id": self._seq,
            }


# Bounds come from configuration so an operator can tune them without a code change.
broker = EventBroker(
    max_queue=config.SSE_QUEUE_MAX,
    max_total=config.SSE_MAX_TOTAL_CONNECTIONS,
    max_per_device=config.SSE_MAX_CONNECTIONS_PER_DEVICE,
    max_per_user=config.SSE_MAX_CONNECTIONS_PER_USER,
)
