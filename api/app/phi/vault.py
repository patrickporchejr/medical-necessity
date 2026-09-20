"""Request-scoped re-identification map.

Holds original <-> placeholder pairs and one random date offset per patient. It lives in
memory for one request, is never written to disk, and cannot be pickled or copied out
by accident (`__repr__` and pickling expose nothing).
"""

import random
import re
import time
from collections import Counter
from datetime import timedelta
from typing import Callable

PLACEHOLDER = re.compile(r"<([A-Z]+)_(\d+)>")
OFFSET_DAYS = (30, 730)


class VaultExpired(RuntimeError):
    pass


class Vault:
    def __init__(
        self,
        ttl_seconds: float = 900,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ):
        self._clock = clock
        self._expires_at = clock() + ttl_seconds
        self._rng = rng or random.SystemRandom()
        self._forward: dict[tuple[str, str], str] = {}
        self._reverse: dict[str, tuple[str, str]] = {}
        self._counts: Counter[str] = Counter()
        self._offsets: dict[str, timedelta] = {}

    def _check(self) -> None:
        if self._clock() >= self._expires_at:
            self.clear()
            raise VaultExpired("Vault expired; the request has outlived its re-ID map")

    def placeholder(self, kind: str, original: str) -> str:
        """Stable placeholder for `original`: the same input always yields the same one."""
        self._check()
        key = (kind, original)
        if key not in self._forward:
            self._counts[kind] += 1
            token = f"<{kind}_{self._counts[kind]}>"
            self._forward[key] = token
            self._reverse[token] = key
        return self._forward[key]

    def original(self, placeholder: str) -> str | None:
        self._check()
        entry = self._reverse.get(placeholder)
        return entry[1] if entry else None

    def kind(self, placeholder: str) -> str | None:
        self._check()
        entry = self._reverse.get(placeholder)
        return entry[0] if entry else None

    def originals(self, kind: str) -> dict[str, str]:
        """original -> placeholder for everything of one kind seen so far."""
        self._check()
        return {orig: token for (k, orig), token in self._forward.items() if k == kind}

    def known(self) -> list[tuple[str, str]]:
        """(kind, original) for everything the vault holds; used by the outbound leak guard."""
        self._check()
        return list(self._forward)

    def date_offset(self, patient_id: str) -> timedelta:
        """One random, non-zero shift per patient, so intervals within a chart survive."""
        self._check()
        if patient_id not in self._offsets:
            days = self._rng.randint(*OFFSET_DAYS) * self._rng.choice((-1, 1))
            self._offsets[patient_id] = timedelta(days=days)
        return self._offsets[patient_id]

    def patients_with_offsets(self) -> list[str]:
        self._check()
        return list(self._offsets)

    def clear(self) -> None:
        self._forward.clear()
        self._reverse.clear()
        self._counts.clear()
        self._offsets.clear()

    def __repr__(self) -> str:
        return f"Vault(<{len(self._reverse)} entries>)"

    def __reduce__(self):
        raise TypeError("Vault must never be serialized")
