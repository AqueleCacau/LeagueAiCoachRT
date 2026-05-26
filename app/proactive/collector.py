from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.proactive.models import GameSnapshot

logger = logging.getLogger(__name__)


@dataclass
class LcuAuth:
    port: int
    password: str
    protocol: str = "https"


class LiveClientPoller:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def fetch_all_gamedata(self) -> Optional[Dict[str, Any]]:
        url = f"{self._base_url}/liveclientdata/allgamedata"
        try:
            async with httpx.AsyncClient(verify=False, timeout=self._timeout) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    logger.debug("Live client returned status %s", response.status_code)
                    return None
                return response.json()
        except (httpx.RequestError, ValueError) as exc:
            logger.debug("Live client request failed: %s", exc)
            return None


class LcuConnector:
    def __init__(self, lockfile_path: Optional[str], timeout_seconds: float) -> None:
        self._lockfile_path = lockfile_path
        self._timeout = timeout_seconds

    def _candidate_paths(self) -> list[Path]:
        if self._lockfile_path:
            return [Path(self._lockfile_path)]
        candidates = []
        if os.name == "nt":
            candidates.append(Path("C:/Riot Games/League of Legends/lockfile"))
            candidates.append(Path("C:/Program Files/Riot Games/League of Legends/lockfile"))
        else:
            candidates.append(Path("/Applications/League of Legends.app/Contents/LoL/lockfile"))
            candidates.append(Path.home() / "Riot Games/League of Legends/lockfile")
        return candidates

    def _read_lockfile(self) -> Optional[LcuAuth]:
        for path in self._candidate_paths():
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.debug("Failed to read LCU lockfile %s: %s", path, exc)
                continue
            parts = content.split(":")
            if len(parts) < 5:
                logger.debug("Invalid LCU lockfile format at %s", path)
                continue
            try:
                port = int(parts[2])
            except ValueError:
                logger.debug("Invalid LCU port in lockfile %s", path)
                continue
            lcu_pass = parts[3]
            protocol = parts[4] or "https"
            return LcuAuth(port, lcu_pass, protocol)
        return None

    async def fetch_gameflow(self) -> Optional[Dict[str, Any]]:
        auth = self._read_lockfile()
        if not auth:
            return None
        base_url = f"{auth.protocol}://127.0.0.1:{auth.port}"
        try:
            async with httpx.AsyncClient(
                verify=False,
                timeout=self._timeout,
                auth=httpx.BasicAuth("riot", auth.password),
            ) as client:
                phase = await client.get(f"{base_url}/lol-gameflow/v1/gameflow-phase")
                session = await client.get(f"{base_url}/lol-gameflow/v1/session")
                summoner = await client.get(f"{base_url}/lol-summoner/v1/current-summoner")
            return {
                "phase": phase.json() if phase.status_code == 200 else None,
                "session": session.json() if session.status_code == 200 else None,
                "summoner": summoner.json() if summoner.status_code == 200 else None,
            }
        except (httpx.RequestError, ValueError) as exc:
            logger.debug("LCU request failed: %s", exc)
            return None


class A2Collector:
    def __init__(self) -> None:
        self._live_client = LiveClientPoller(
            base_url=settings.live_client_base_url,
            timeout_seconds=settings.proactive_http_timeout_seconds,
        )
        self._lcu = LcuConnector(
            lockfile_path=settings.lcu_lockfile_path,
            timeout_seconds=settings.proactive_http_timeout_seconds,
        )

    async def collect(self) -> GameSnapshot:
        live_task = asyncio.create_task(self._live_client.fetch_all_gamedata())
        lcu_task = asyncio.create_task(self._lcu.fetch_gameflow())
        live_data, lcu_data = await asyncio.gather(live_task, lcu_task)
        return GameSnapshot(
            timestamp=asyncio.get_event_loop().time(),
            live_data=live_data,
            lcu_data=lcu_data,
        )
