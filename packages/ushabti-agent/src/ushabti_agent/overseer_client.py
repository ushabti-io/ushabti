"""Reporting to an overseer: registration and regular "Here am I" messages.

Used only when ``USHABTI_OVERSEER_URL`` is set. The first message registers the
ushabti, and each later one keeps it registered.
"""

import asyncio

import httpx
import structlog

from ushabti import HereAmI
from ushabti_agent.runtime import Ushabti

_log = structlog.get_logger(__name__)


class OverseerClient:
    """Sends "Here am I" messages to one overseer."""

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def here_am_i(self, message: HereAmI) -> bool:
        """Send one message. Returns whether the overseer accepted it.

        An unreachable overseer is logged and never raised: an ushabti keeps
        its vigil whether or not anyone is listening.
        """
        try:
            response = await self._client.post(
                f"{self.base_url}/here-am-i",
                content=message.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            _log.warning("overseer_unreachable", overseer=self.base_url, error=str(error))
            return False
        return True

    async def run(self, ushabti: Ushabti, interval: float, stop: asyncio.Event) -> None:
        """Send a message now and then every ``interval`` seconds until ``stop`` is set."""
        while not stop.is_set():
            await self.here_am_i(ushabti.here_am_i())
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue

    async def close(self) -> None:
        await self._client.aclose()
