"""Shared in-memory application state, published to the web UI."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .config import Action, Config


@dataclass
class InputNode:
    """One /dev/input/eventN node."""

    path: str
    name: str
    uniq: str
    phys: str
    grabbed: bool = False
    error: str | None = None


@dataclass
class KeyboardStatus:
    name: str
    nodes: dict[str, InputNode] = field(default_factory=dict)
    connected_since: float | None = None
    last_key: str | None = None
    last_key_at: float | None = None
    presses: int = 0

    @property
    def connected(self) -> bool:
        return bool(self.nodes)


@dataclass
class BridgeStatus:
    host: str
    connected: bool = False
    bridge_id: str | None = None
    error: str | None = None
    since: float | None = None


@dataclass
class LogEntry:
    ts: float
    keyboard: str
    key: str
    action: str | None
    ok: bool | None = None  # None = pending / not an action
    message: str | None = None


class AppState:
    """Holds everything the status page shows, and notifies subscribers on change.

    Two kinds of messages are published:
      * ``("key", {...})`` immediately on every key press, so the UI can flash the row;
      * ``("status", snapshot)`` after any change, coalesced so bursts send one snapshot.
    """

    COALESCE_SECONDS = 0.3

    def __init__(self, config: Config) -> None:
        self.config = config
        self.started_at = time.time()
        self.bridge = BridgeStatus(host=config.bridge.host)
        self.keyboards: dict[str, KeyboardStatus] = {
            kb.name: KeyboardStatus(kb.name) for kb in config.keyboards
        }
        self.unassigned: dict[str, InputNode] = {}
        self.log: deque[LogEntry] = deque(maxlen=100)
        # Set by HueController: returns light/room state and resolves actions.
        self.hue_snapshot: Callable[[], dict[str, Any]] = lambda: {"rooms": [], "lights": []}
        self.check_action: Callable[[Action], str | None] = lambda _a: None
        self._subscribers: set[asyncio.Queue] = set()
        self._dirty = asyncio.Event()

    # -- change notification -------------------------------------------------

    def changed(self) -> None:
        self._dirty.set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _publish(self, kind: str, payload: Any) -> None:
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):  # slow client: drop message
                q.put_nowait((kind, payload))

    async def run_publisher(self) -> None:
        while True:
            await self._dirty.wait()
            await asyncio.sleep(self.COALESCE_SECONDS)
            self._dirty.clear()
            if self._subscribers:
                self._publish("status", self.snapshot())

    # -- mutations -------------------------------------------------------------

    def record_key(self, keyboard: str, key: str, action: Action | None) -> LogEntry:
        now = time.time()
        kb = self.keyboards[keyboard]
        kb.last_key, kb.last_key_at = key, now
        kb.presses += 1
        entry = LogEntry(now, keyboard, key, action.describe() if action else None)
        self.log.append(entry)
        self._publish("key", {"keyboard": keyboard, "key": key, "mapped": action is not None})
        self.changed()
        return entry

    def finish(self, entry: LogEntry, ok: bool, message: str | None = None) -> None:
        entry.ok, entry.message = ok, message
        self.changed()

    # -- snapshot ------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        keyboards = []
        for kb_cfg in self.config.keyboards:
            st = self.keyboards[kb_cfg.name]
            keyboards.append(
                {
                    "name": kb_cfg.name,
                    "match": kb_cfg.match.model_dump(exclude_none=True),
                    "grab": kb_cfg.grab,
                    "connected": st.connected,
                    "connected_since": st.connected_since,
                    "nodes": [asdict(n) for n in st.nodes.values()],
                    "last_key": st.last_key,
                    "last_key_at": st.last_key_at,
                    "presses": st.presses,
                    "keys": [
                        {
                            "key": key,
                            "action": action.action.value,
                            "description": action.describe(),
                            "problem": self.check_action(action),
                        }
                        for key, action in kb_cfg.keys.items()
                    ],
                }
            )
        return {
            "now": time.time(),
            "started_at": self.started_at,
            "bridge": asdict(self.bridge),
            "keyboards": keyboards,
            "unassigned": [asdict(n) for n in self.unassigned.values()],
            "hue": self.hue_snapshot(),
            "log": [asdict(e) for e in reversed(self.log)],
        }
