"""Punto de entrada: BD + worker en segundo plano + bot en long polling, en un solo event loop."""

import asyncio
import logging

import aiosqlite
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot import handlers
from app.bot.middlewares import AllowlistMiddleware
from app.config import get_settings
from app.db import repository
from app.db.init_db import configure_connection, init_db
from app.db.models import MessageStatus
from app.integrations.google_auth import CredencialesNoDisponibles, cargar_credenciales
from app.integrations.google_workspace import GoogleWorkspace
from app.nlp import OpenAIInterprete
from app.worker import Worker
from app.worker.ejecutor import EjecutorGoogle

logger = logging.getLogger(__name__)


async def on_startup(bot: Bot, worker: Worker) -> None:
    worker.start(bot)


async def on_shutdown(worker: Worker) -> None:
    await worker.stop()


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

    if not settings.allowed_ids:
        logger.warning("ALLOWED_USER_IDS está vacío: se rechazará todo. Mira los logs para ver tu ID.")

    # Fail fast: sin credenciales de Google no tiene sentido arrancar.
    # Los mensajes no se pierden: Telegram los guarda 24 h hasta que los recojamos.
    try:
        creds = cargar_credenciales(settings.google_token_path)
    except CredencialesNoDisponibles as e:
        logger.critical("%s", e)
        raise SystemExit(1) from e

    await init_db(settings.db_path)

    google = GoogleWorkspace(
        creds,
        tz=settings.tz,
        calendar_id=settings.google_calendar_id,
        tasklist_id=settings.google_tasklist_id,
        duracion_evento=settings.duracion_evento,
        timeout=settings.google_timeout,
    )
    worker = Worker(
        db_path=settings.db_path,
        interprete=OpenAIInterprete(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            timeout=settings.openai_timeout,
            tz=settings.tz,
        ),
        ejecutor=EjecutorGoogle(google, settings.tz),
        poll_interval=settings.worker_poll_interval,
        max_attempts=settings.worker_max_attempts,
    )

    async with aiosqlite.connect(settings.db_path) as db:
        await configure_connection(db)

        for estado in (MessageStatus.PENDIENTE, MessageStatus.INTERPRETADO):
            logger.info("En cola %s: %s", estado, await repository.count_by_status(db, estado))

        bot = Bot(
            token=settings.telegram_bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = Dispatcher()
        dp.update.outer_middleware(AllowlistMiddleware(settings.allowed_ids))
        dp.include_router(handlers.router)
        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)

        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, db=db, worker=worker, wakeup=worker.wakeup)


if __name__ == "__main__":
    asyncio.run(main())
