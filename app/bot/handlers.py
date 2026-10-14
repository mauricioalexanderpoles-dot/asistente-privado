import asyncio
import logging
from html import escape

import aiosqlite
from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.db import repository

logger = logging.getLogger(__name__)
router = Router(name="main")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        f"Hola, {escape(message.from_user.first_name)} 👋\n"
        "Escríbeme un evento o una tarea en lenguaje natural."
    )


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer("No conozco ese comando todavía.")


@router.message(F.text)
async def on_text(message: Message, db: aiosqlite.Connection, wakeup: asyncio.Event) -> None:
    # "db" y "wakeup" llegan inyectados desde dp.start_polling(..., db=..., wakeup=...)
    row_id, created = await repository.save_incoming_message(
        db,
        chat_id=message.chat.id,
        telegram_message_id=message.message_id,
        user_id=message.from_user.id,
        text=message.text,
        received_at=message.date,
    )

    if not created:
        logger.info("Mensaje duplicado ignorado: chat=%s msg=%s", message.chat.id, message.message_id)
        return

    logger.info("Mensaje #%s guardado como PENDIENTE", row_id)
    wakeup.set()  # despierta al worker
    await message.answer(f"📥 Recibido (#{row_id}), procesando…")


@router.message()
async def unsupported(message: Message) -> None:
    await message.answer("Por ahora solo entiendo mensajes de texto.")
