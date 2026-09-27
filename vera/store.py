"""Thread-safe, versioned, idempotent context store."""

from __future__ import annotations

import threading

SCOPES = ("category", "merchant", "customer", "trigger")


class ContextStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._data: dict[tuple[str, str], dict] = {}

    def put(self, scope: str, cid: str, version: int, payload: dict) -> tuple[str, int | None]:
        """Returns ('accepted'|'stale'|'noop', current_version)."""
        with self._lock:
            key = (scope, cid)
            cur = self._data.get(key)
            if cur is not None:
                if version < cur["version"]:
                    return "stale", cur["version"]
                if version == cur["version"]:
                    # identical replay = idempotent success; same version w/ different data = conflict
                    return ("noop" if cur["payload"] == payload else "conflict"), cur["version"]
            self._data[key] = {"version": version, "payload": payload}  # atomic replace
            return "accepted", version

    def get(self, scope: str, cid: str | None) -> dict | None:
        if not cid:
            return None
        with self._lock:
            e = self._data.get((scope, cid))
            return e["payload"] if e else None

    def version(self, scope: str, cid: str) -> int | None:
        with self._lock:
            e = self._data.get((scope, cid))
            return e["version"] if e else None

    def counts(self) -> dict:
        with self._lock:
            out = {s: 0 for s in SCOPES}
            for s, _ in self._data:
                out[s] = out.get(s, 0) + 1
            return out

    def all(self, scope: str) -> dict:
        with self._lock:
            return {cid: e["payload"] for (s, cid), e in self._data.items() if s == scope}

    def clear(self):
        with self._lock:
            self._data.clear()
