"""Gateway bridge connecting channel bus traffic to ohqa runtimes."""

from __future__ import annotations

import asyncio
import logging

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus

from ohqa.gateway.router import session_key_for_message
from ohqa.gateway.runtime import OhqaSessionRuntimePool

logger = logging.getLogger(__name__)


def _format_gateway_error(exc: Exception) -> str:
    """Return a short, user-facing gateway error message."""
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if "claude oauth refresh failed" in lowered:
        return (
            "[ohqa gateway error] Claude subscription auth refresh failed. "
            "Run `oh auth claude-login` again or switch the gateway profile."
        )
    if "claude oauth refresh token is invalid or expired" in lowered:
        return (
            "[ohqa gateway error] Claude subscription token is expired. "
            "Run `claude auth login`, then `oh auth claude-login`, or switch the gateway profile."
        )
    if "auth source not found" in lowered or "access token" in lowered:
        return (
            "[ohqa gateway error] Authentication is not configured for the current "
            "gateway profile. Run `oh setup` or `ohqa config`."
        )
    if "api key" in lowered or "auth" in lowered or "credential" in lowered:
        return (
            "[ohqa gateway error] Authentication failed for the current gateway "
            "profile. Check `oh auth status` and `ohqa config`."
        )
    return f"[ohqa gateway error] {message}"


class OhqaGatewayBridge:
    """Consume inbound messages and publish assistant replies."""

    def __init__(self, *, bus: MessageBus, runtime_pool: OhqaSessionRuntimePool) -> None:
        self._bus = bus
        self._runtime_pool = runtime_pool
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                message = await asyncio.wait_for(self._bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            session_key = session_key_for_message(message)
            try:
                reply = ""
                async for update in self._runtime_pool.stream_message(message, session_key):
                    if update.kind == "final":
                        reply = update.text
                        continue
                    if not update.text:
                        continue
                    await self._bus.publish_outbound(
                        OutboundMessage(
                            channel=message.channel,
                            chat_id=message.chat_id,
                            content=update.text,
                            metadata=update.metadata,
                        )
                    )
            except Exception as exc:  # pragma: no cover - gateway failure path
                logger.exception("ohqa gateway failed to process inbound message")
                reply = _format_gateway_error(exc)
            if not reply:
                continue
            await self._bus.publish_outbound(
                OutboundMessage(
                    channel=message.channel,
                    chat_id=message.chat_id,
                    content=reply,
                    metadata={"_session_key": session_key},
                )
            )

    def stop(self) -> None:
        self._running = False
