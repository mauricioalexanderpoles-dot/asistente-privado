import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

logger = logging.getLogger(__name__)


class AllowlistMiddleware(BaseMiddleware):
    """Descarta cualquier update de usuarios no autorizados antes de llegar a los handlers."""

    def __init__(self, allowed_ids: frozenset[int]) -> None:
        self.allowed_ids = allowed_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # aiogram rellena "event_from_user" en un middleware interno que se ejecuta antes que este
        user: User | None = data.get("event_from_user")
        if user is None or user.id not in self.allowed_ids:
            logger.warning(
                "Update rechazado: user_id=%s username=%s",
                user.id if user else None,
                user.username if user else None,
            )
            return None  # silencio total: no confirmamos que el bot existe
        return await handler(event, data)
