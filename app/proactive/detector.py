from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.proactive.models import AdviceIntent

logger = logging.getLogger(__name__)


class EventDetector:
    def __init__(self, lookback_seconds: float) -> None:
        self._lookback = lookback_seconds
        self._last_event_time: Optional[float] = None

    def detect(self, live_data: Dict[str, Any], now_seconds: float) -> List[AdviceIntent]:
        events = live_data.get("events", {}).get("Events", [])
        if not events:
            return []
        max_time = max((e.get("EventTime", 0.0) for e in events), default=0.0)
        if self._last_event_time is not None and max_time < self._last_event_time:
            logger.info("Event timeline reset detected. Clearing last_event_time.")
            self._last_event_time = None

        new_events = []
        for event in events:
            event_time = event.get("EventTime", 0.0)
            if self._last_event_time is not None and event_time <= self._last_event_time:
                continue
            if now_seconds - event_time > self._lookback:
                continue
            new_events.append(event)

        if not new_events:
            return []

        self._last_event_time = max(e.get("EventTime", 0.0) for e in new_events)

        intents: List[AdviceIntent] = []
        for event in new_events:
            intent = self._event_to_intent(event, now_seconds)
            if intent:
                intents.append(intent)
        return intents

    def _event_to_intent(self, event: Dict[str, Any], now_seconds: float) -> Optional[AdviceIntent]:
        name = event.get("EventName")
        event_time = event.get("EventTime", 0.0)
        killer = event.get("KillerName") or "alguém"
        victim = event.get("VictimName") or "alvo"
        dragon_type = event.get("DragonType", "elemental")

        summaries = {
            "DragonKill": f"Dragão {dragon_type} abatido por {killer}",
            "BaronKill": f"Barão abatido por {killer}",
            "HeraldKill": f"Arauto abatido por {killer}",
            "HordeKill": f"Void Grubs abatidos por {killer}",
            "ChampionKill": f"{killer} matou {victim}",
            "TurretKilled": f"Torre destruída por {killer}",
            "InhibKilled": f"Inibidor destruído por {killer}",
        }
        if name not in summaries:
            return None

        priority = 1
        if name in {"BaronKill", "DragonKill"}:
            priority = 3
        elif name in {"HeraldKill", "HordeKill", "InhibKilled"}:
            priority = 2

        summary = summaries[name]
        return AdviceIntent(
            intent_id=f"{name}-{event_time}",
            summary=summary,
            priority=priority,
            created_at=now_seconds,
            ttl_seconds=30.0,
            cooldown_key=name,
            dedupe_key=f"{name}-{event_time}",
            metadata={"event_name": name, "event_time": event_time},
        )
