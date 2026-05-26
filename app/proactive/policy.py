from __future__ import annotations

import time
from typing import Dict

from app.proactive.models import AdviceIntent


class AntiSpamPolicy:
    def __init__(self, global_cooldown: float, event_cooldown: float) -> None:
        self._global_cooldown = global_cooldown
        self._event_cooldown = event_cooldown
        self._last_global_emit: float = 0.0
        self._last_by_key: Dict[str, float] = {}
        self._dedupe: Dict[str, float] = {}

    def should_emit(self, intent: AdviceIntent) -> bool:
        now = time.time()
        if now - self._last_global_emit < self._global_cooldown:
            return False
        last_event = self._last_by_key.get(intent.cooldown_key, 0.0)
        if now - last_event < self._event_cooldown:
            return False
        last_seen = self._dedupe.get(intent.dedupe_key, 0.0)
        if now - last_seen < intent.ttl_seconds:
            return False
        return True

    def mark_emitted(self, intent: AdviceIntent) -> None:
        now = time.time()
        self._last_global_emit = now
        self._last_by_key[intent.cooldown_key] = now
        self._dedupe[intent.dedupe_key] = now
