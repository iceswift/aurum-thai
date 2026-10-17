"""Persistent, revisioned snapshots for the Thai Gold API."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
import copy
import datetime
import json
import os
import tempfile

try:  # Linux production hosts use flock to coordinate multiple worker processes.
    import fcntl
except ImportError:  # pragma: no cover - Windows is only used for local development.
    fcntl = None


@dataclass(frozen=True)
class SnapshotCommit:
    changed: bool
    revision: int
    updated_at: Optional[str]
    previous_revision: int


def build_board_snapshot(cache: Dict[str, Any]) -> Dict[str, Any]:
    """Build the public board shape from one in-memory cache snapshot."""
    gold_data = copy.deepcopy(cache.get("gold_bar_data", []))
    source = cache.get("source_type", "None")
    if source == "Classic Website":
        latest = gold_data[0] if gold_data else {}
        history = gold_data[:20]
    else:
        latest = gold_data[-1] if gold_data else {}
        history = gold_data[-20:]

    return {
        "source": source,
        "market_status": cache.get("market_status", "Initializing..."),
        "latest": latest,
        "history": history,
        "jewelry": copy.deepcopy(cache.get("jewelry_percent", [])),
        "shops": copy.deepcopy(cache.get("shop_data", [])),
        "counts": {
            "history": len(gold_data),
            "jewelry": len(cache.get("jewelry_percent", [])),
            "shops": len(cache.get("shop_data", [])),
        },
    }


def canonical_json(value: Any) -> str:
    """Stable JSON representation used only for business-data comparisons."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SnapshotStore:
    def __init__(self, state_path: str):
        self.path = Path(state_path)
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def load(self) -> Optional[Dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
            if isinstance(state, dict) and isinstance(state.get("revision"), int):
                return state
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as error:
            print(f"⚠️ [SnapshotState] Load failed: {error}")
        return None

    def commit(self, cache: Dict[str, Any], checked_at: str) -> SnapshotCommit:
        """Persist a new revision only when the public business snapshot changed."""
        candidate_snapshot = build_board_snapshot(cache)

        with self._exclusive_lock():
            current_state = self.load() or {}
            previous_revision = int(current_state.get("revision", 0))
            previous_snapshot = current_state.get("snapshot")

            if previous_snapshot is not None and canonical_json(previous_snapshot) == canonical_json(candidate_snapshot):
                current_state["last_checked_at"] = checked_at
                self._atomic_write(current_state)
                return SnapshotCommit(False, previous_revision, current_state.get("updated_at"), previous_revision)

            updated_at = datetime.datetime.fromisoformat(checked_at).isoformat()
            next_state = {
                "version": 1,
                "revision": previous_revision + 1,
                "updated_at": updated_at,
                "last_checked_at": checked_at,
                "snapshot": candidate_snapshot,
                "cache": {
                    "gold_bar_data": copy.deepcopy(cache.get("gold_bar_data", [])),
                    "jewelry_percent": copy.deepcopy(cache.get("jewelry_percent", [])),
                    "shop_data": copy.deepcopy(cache.get("shop_data", [])),
                    "market_status": cache.get("market_status", "Initializing..."),
                    "source_type": cache.get("source_type", "None"),
                },
            }
            self._atomic_write(next_state)
            return SnapshotCommit(True, next_state["revision"], updated_at, previous_revision)

    def _atomic_write(self, state: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                json.dump(state, temporary_file, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
