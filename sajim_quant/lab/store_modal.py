"""
SAJIM Strategy Lab — ModalStore: the LedgerStore used inside Modal (INTERFACES.md §7.1).

  * Files live on the ``sajim-results`` volume, mounted at ``/results`` in the API and
    lab_job containers. File semantics are LocalDirStore's (this class extends it), so the
    path checks and the protected-path rule are the SAME code: ``write`` refuses
    ``lab/*/ledger/**`` and ``lab/_slots/**`` case-insensitively and ignoring trailing dots
    and spaces (``ledger._is_protected``).
  * Every successful write is followed by ``volume.commit()`` so other containers see it.
    ``listdir`` and a ``read`` miss call ``volume.reload()`` first (throttled by
    ``reload_min_interval_s``) to see other containers' commits.
  * Budget slots are NOT files: ``claim_slot`` is
    ``modal.Dict("sajim-lab").put(key, value, skip_if_exists=True)``, which in modal 1.6.0
    returns ``DictUpdateResponse.created`` — True only for the first writer (or when the entry
    already holds exactly our bytes: a retried put whose first attempt landed). Dict entries
    expire after 7 idle days; ledger.py counts budget_claim records as well, so an expired
    entry cannot reset a budget (§13.3).

Cross-container note: ``write_once`` is exclusive within one container (O_CREAT|O_EXCL on
the mount, after a reload). Two containers racing on the SAME path are not serialised by a
volume; ledger file names carry a microsecond stamp + request_id, and every budget decision
goes through the Dict, which is atomic.

The Dict and Volume objects are injected (fakes in tests); ``ModalStore.from_modal()`` looks
up the real ones. Only that factory imports ``modal``.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Union

from sajim_quant.lab import ledger as L
from sajim_quant.lab.interfaces import slot_filename

logger = logging.getLogger(__name__)

SLOTS_DICT_NAME = "sajim-lab"
RESULTS_VOLUME_NAME = "sajim-results"
RESULTS_ROOT = "/results"
DEFAULT_RELOAD_MIN_INTERVAL_S = 1.0


class ModalStore(L.LocalDirStore):
    """LedgerStore on a mounted Modal Volume (files) + a Modal Dict (first-writer-wins slots)."""

    def __init__(self, volume: Any, slots: Any, *, root: Union[str, Path] = RESULTS_ROOT,
                 reload_min_interval_s: float = DEFAULT_RELOAD_MIN_INTERVAL_S,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(Path(root))
        self.volume = volume
        self.slots = slots
        self.reload_min_interval_s = float(reload_min_interval_s)
        self._clock = clock
        self._last_reload: Optional[float] = None

    @classmethod
    def from_modal(cls, *, volume: Any = None, root: Union[str, Path] = RESULTS_ROOT,
                   reload_min_interval_s: float = DEFAULT_RELOAD_MIN_INTERVAL_S) -> "ModalStore":
        """The production store: pass modal_runner.RESULTS_VOLUME (mounted at `root`) as `volume`."""
        import modal

        if volume is None:
            volume = modal.Volume.from_name(RESULTS_VOLUME_NAME, create_if_missing=True)
        slots = modal.Dict.from_name(SLOTS_DICT_NAME, create_if_missing=True)
        return cls(volume, slots, root=root, reload_min_interval_s=reload_min_interval_s)

    # ── volume visibility ───────────────────────────────────────────────────
    def refresh(self, *, force: bool = False) -> bool:
        """volume.reload() unless one ran within reload_min_interval_s. False if skipped or failed."""
        now = self._clock()
        if (not force and self._last_reload is not None
                and now - self._last_reload < self.reload_min_interval_s):
            return False
        self._last_reload = now
        try:
            self.volume.reload()
        except Exception as exc:  # noqa: BLE001 — e.g. "open files" in a busy API container: stay stale
            logger.warning("results volume reload failed (%s); using the local view", type(exc).__name__)
            return False
        return True

    def _commit(self) -> None:
        self.volume.commit()
        self._last_reload = self._clock()        # modal commits reload as well

    # ── LedgerStore ─────────────────────────────────────────────────────────
    def write_once(self, relpath: str, data: bytes) -> bool:
        self._path(relpath)                       # validate before any volume call
        if super().read(relpath) is None:
            self.refresh()                        # another container may have committed it
        if not super().write_once(relpath, data):
            return False
        self._commit()
        return True

    def write(self, relpath: str, data: bytes) -> None:
        if L._is_protected(relpath):
            raise ValueError(f"{relpath!r} is write-once (ledger records / slot claims)")
        super().write(relpath, data)
        self._commit()

    def read(self, relpath: str) -> Optional[bytes]:
        data = super().read(relpath)
        if data is None and self.refresh():
            data = super().read(relpath)
        return data

    def listdir(self, reldir: str) -> List[str]:
        self.refresh()
        return super().listdir(reldir)

    def claim_slot(self, key: str, value: bytes) -> bool:
        slot_filename(key)                        # ValueError on a malformed key
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("slot value must be bytes")
        value = bytes(value)
        if self.slots.put(key, value, skip_if_exists=True) is True:
            return True
        # modal's stub retries DictUpdate on transient errors. When the first attempt landed but its
        # response was lost, the retry answers created=False for OUR entry; the exact bytes (key,
        # request_id and a microsecond claimed_at) identify it, so this is still our claim.
        return self.read_slot(key) == value

    def read_slot(self, key: str) -> Optional[bytes]:
        slot_filename(key)
        value = self.slots.get(key)
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        logger.warning("slot %s holds a non-bytes value; treated as taken", key)
        return b""
