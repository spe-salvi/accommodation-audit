"""
Persistent cache mapping Canvas assignment IDs to LTI assignment IDs.

The LTI assignment ID is the internal identifier used by the New Quiz LTI
service (franciscan.quiz-lti-pdx-prod.instructure.com). It differs from the
Canvas assignment ID and cannot be derived from Canvas API responses alone —
it must be discovered by intercepting the LTI launch handshake.

Since this mapping never changes for a given assignment, we persist it to
a local JSON file so that Playwright only needs to run for quizzes not yet
seen in a previous audit run.

File format:
    {
        "189437": 10199,
        "189412": 10197
    }

Keys are Canvas assignment IDs (stored as strings for JSON compatibility).
Values are LTI assignment IDs (integers).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(".lti_id_cache.json")


class LtiIdCache:
    """
    Read/write cache for Canvas assignment ID → LTI assignment ID mappings.

    Thread-safety: not thread-safe. This cache is intended for use in a
    single async context and does not require locking.
    """

    def __init__(self, path: Path = _DEFAULT_PATH) -> None:
        self._path = path
        self._data: dict[int, int] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, canvas_assignment_id: int) -> int | None:
        """Return the LTI assignment ID for a Canvas assignment, or None."""
        return self._data.get(canvas_assignment_id)

    def set(self, canvas_assignment_id: int, lti_assignment_id: int) -> None:
        """Store a mapping and immediately persist to disk."""
        self._data[canvas_assignment_id] = lti_assignment_id
        self._save()
        logger.debug(
            "LTI ID cache: %d → %d (persisted)",
            canvas_assignment_id,
            lti_assignment_id,
        )

    def set_many(self, mappings: dict[int, int]) -> None:
        """Store multiple mappings in a single write."""
        self._data.update(mappings)
        self._save()
        logger.debug("LTI ID cache: stored %d mappings (persisted)", len(mappings))

    def missing(self, canvas_assignment_ids: list[int]) -> list[int]:
        """Return the subset of IDs not yet in the cache."""
        return [i for i in canvas_assignment_ids if i not in self._data]

    def __len__(self) -> int:
        return len(self._data)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            logger.debug("LTI ID cache: no file at %s, starting empty", self._path)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            # Keys are stored as strings in JSON; convert back to int.
            self._data = {int(k): int(v) for k, v in raw.items()}
            logger.debug("LTI ID cache: loaded %d entries from %s", len(self._data), self._path)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("LTI ID cache: could not load %s (%s), starting empty", self._path, exc)
            self._data = {}

    def _save(self) -> None:
        # Store keys as strings (JSON requirement); values stay as ints.
        raw = {str(k): v for k, v in self._data.items()}
        self._path.write_text(
            json.dumps(raw, indent=2, sort_keys=True),
            encoding="utf-8",
        )
