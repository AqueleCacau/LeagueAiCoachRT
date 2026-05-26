from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class GameSnapshot:
    timestamp: float
    live_data: Optional[Dict[str, Any]]
    lcu_data: Optional[Dict[str, Any]]


@dataclass
class AdviceIntent:
    intent_id: str
    summary: str
    priority: int
    created_at: float
    ttl_seconds: float
    cooldown_key: str
    dedupe_key: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProactiveAudio:
    text: str
    audio: bytes
    created_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)
