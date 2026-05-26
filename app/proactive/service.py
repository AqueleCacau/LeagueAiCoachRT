from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Set

from app.assistant.agent import get_coach_advice
from app.assistant.session import session_manager
from app.assistant.tts import text_to_speech_stream
from app.config import settings
from app.proactive.collector import A2Collector
from app.proactive.detector import EventDetector
from app.proactive.models import AdviceIntent, ProactiveAudio
from app.proactive.policy import AntiSpamPolicy

logger = logging.getLogger(__name__)


class ProactiveQueue:
    def __init__(self, max_items: int) -> None:
        self._queues: Dict[str, Deque[ProactiveAudio]] = defaultdict(
            lambda: deque(maxlen=max_items)
        )

    def push(self, user_id: str, item: ProactiveAudio) -> None:
        self._queues[user_id].append(item)

    def pop(self, user_id: str) -> Optional[ProactiveAudio]:
        queue = self._queues.get(user_id)
        if not queue:
            return None
        return queue.popleft() if queue else None

    def clear(self, user_id: str) -> None:
        if user_id in self._queues:
            self._queues[user_id].clear()


class ProactiveService:
    def __init__(self) -> None:
        self._collector = A2Collector()
        self._detector = EventDetector(settings.proactive_event_lookback_seconds)
        self._policy = AntiSpamPolicy(
            global_cooldown=settings.proactive_global_cooldown_seconds,
            event_cooldown=settings.proactive_event_cooldown_seconds,
        )
        self._queue = ProactiveQueue(settings.proactive_queue_max_items)
        self._active_users: Set[str] = set()
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Proactive scheduler started")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._stop_event.set()
            self._task.cancel()
            logger.info("Proactive scheduler stop requested")

    def register_user(self, user_id: str) -> None:
        self._active_users.add(user_id)
        logger.info("User %s registered for proactive mode", user_id)

    def unregister_user(self, user_id: str) -> None:
        self._active_users.discard(user_id)
        self._queue.clear(user_id)
        logger.info("User %s unregistered from proactive mode", user_id)

    def next_audio(self, user_id: str) -> Optional[ProactiveAudio]:
        return self._queue.pop(user_id)

    async def _run_loop(self) -> None:
        backoff = settings.proactive_poll_interval_idle_seconds
        while not self._stop_event.is_set():
            snapshot = await self._collector.collect()
            live_data = snapshot.live_data
            if not live_data:
                backoff = min(backoff * 2, settings.proactive_poll_interval_max_seconds)
                await asyncio.sleep(backoff)
                continue

            backoff = settings.proactive_poll_interval_idle_seconds
            now_seconds = live_data.get("gameData", {}).get("gameTime", 0.0)
            intents = self._detector.detect(live_data, now_seconds)
            if intents and self._active_users:
                await self._handle_intents(intents, live_data)

            interval = settings.proactive_poll_interval_idle_seconds
            if intents:
                interval = settings.proactive_poll_interval_active_seconds
            await asyncio.sleep(interval)

    async def _handle_intents(self, intents: list[AdviceIntent], live_data: dict) -> None:
        game_stats_json = json.dumps(live_data, ensure_ascii=False)
        session = session_manager.get_or_create_session(game_stats_dict=live_data)
        for intent in sorted(intents, key=lambda i: i.priority, reverse=True):
            if not self._policy.should_emit(intent):
                continue
            advice_text = await self._generate_advice(intent, session, game_stats_json)
            if not advice_text:
                continue
            audio_bytes = await self._text_to_audio(advice_text)
            if not audio_bytes:
                continue
            created_at = time.time()
            for user_id in self._active_users:
                self._queue.push(
                    user_id,
                    ProactiveAudio(
                        text=advice_text,
                        audio=audio_bytes,
                        created_at=created_at,
                        metadata=intent.metadata,
                    ),
                )
            self._policy.mark_emitted(intent)

    async def _generate_advice(
        self, intent: AdviceIntent, session, game_stats_json: str
    ) -> Optional[str]:
        prompt = (
            "Dê uma dica proativa curta em português brasileiro com base no evento "
            f"seguinte: {intent.summary}. Seja direto, útil e sem perguntas."
        )
        try:
            return get_coach_advice(
                session=session,
                user_question=prompt,
                game_stats_json=game_stats_json,
                language=settings.proactive_language,
                use_history=False,
                update_history=False,
            )
        except Exception as exc:
            logger.error("Failed to generate proactive advice: %s", exc, exc_info=True)
            return None

    async def _text_to_audio(self, text: str) -> Optional[bytes]:
        try:
            chunks = []
            async for chunk in text_to_speech_stream(text):
                chunks.append(chunk)
            return b"".join(chunks)
        except Exception as exc:
            logger.error("Failed to generate proactive audio: %s", exc, exc_info=True)
            return None


proactive_service = ProactiveService()
