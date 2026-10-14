"""Acceso a datos. Ni los handlers ni el worker escriben SQL: llaman a estas funciones."""

from datetime import datetime

import aiosqlite

from app.db.models import ClaimedMessage, MessageStatus

# Mismo formato que los DEFAULT del esquema, para poder comparar como texto
_NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


# ---------------------------------------------------------------- bot (entrada)

async def save_incoming_message(
    db: aiosqlite.Connection,
    *,
    chat_id: int,
    telegram_message_id: int,
    user_id: int,
    text: str,
    received_at: datetime,
) -> tuple[int | None, bool]:
    """Guarda un mensaje como PENDIENTE. Devuelve (id, creado); (None, False) si era duplicado."""
    async with db.execute(
        """
        INSERT INTO messages (chat_id, telegram_message_id, user_id, text, received_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (chat_id, telegram_message_id) DO NOTHING
        RETURNING id;
        """,
        (chat_id, telegram_message_id, user_id, text, received_at.isoformat()),
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    return (row[0], True) if row else (None, False)


async def count_by_status(db: aiosqlite.Connection, status: MessageStatus) -> int:
    async with db.execute(
        "SELECT COUNT(*) FROM messages WHERE status = ?;", (status.value,)
    ) as cur:
        (count,) = await cur.fetchone()
    return count


# ---------------------------------------------------------------- worker

async def recover_orphaned(db: aiosqlite.Connection) -> int:
    """Devuelve a su cola lo que quedó a medias por una caída.

    Solo es seguro porque hay UN worker: al arrancar, nadie más puede estar
    trabajando. Con varios workers haría falta un lease con caducidad.
    No se toca `attempts`: así la fase de ejecución sabe que es un reintento
    y comprueba duplicados en Google.
    """
    cur = await db.execute(
        """
        UPDATE messages
           SET status = CASE status WHEN 'PROCESANDO' THEN 'PENDIENTE' ELSE 'INTERPRETADO' END
         WHERE status IN ('PROCESANDO', 'EJECUTANDO');
        """
    )
    await db.commit()
    return cur.rowcount


async def claim_next(db: aiosqlite.Connection) -> ClaimedMessage | None:
    """Reclama atómicamente el trabajo más antiguo de cualquiera de las dos colas."""
    async with db.execute(
        f"""
        UPDATE messages
           SET status = CASE status WHEN 'PENDIENTE' THEN 'PROCESANDO' ELSE 'EJECUTANDO' END,
               attempts = attempts + 1
         WHERE id = (
                SELECT id FROM messages
                 WHERE status IN ('PENDIENTE', 'INTERPRETADO')
                   AND (next_attempt_at IS NULL OR next_attempt_at <= {_NOW_SQL})
                 ORDER BY created_at, id
                 LIMIT 1
         )
        RETURNING id, chat_id, telegram_message_id, text, received_at, attempts,
                  status, result_json;
        """
    ) as cur:
        row = await cur.fetchone()
    await db.commit()
    if row is None:
        return None
    return ClaimedMessage(
        id=row[0],
        chat_id=row[1],
        telegram_message_id=row[2],
        text=row[3],
        received_at=datetime.fromisoformat(row[4]),
        attempts=row[5],
        status=MessageStatus(row[6]),
        result_json=row[7],
    )


async def mark_interpreted(
    db: aiosqlite.Connection, message_id: int, *, intent: str, result_json: str
) -> None:
    """Fin de la fase NLP. `attempts` vuelve a 0: la fase Google tiene su propio presupuesto."""
    await db.execute(
        """
        UPDATE messages
           SET status = 'INTERPRETADO', intent = ?, result_json = ?,
               attempts = 0, error = NULL, next_attempt_at = NULL
         WHERE id = ?;
        """,
        (intent, result_json, message_id),
    )
    await db.commit()


async def mark_completed(
    db: aiosqlite.Connection,
    message_id: int,
    *,
    intent: str | None = None,
    result_json: str | None = None,
    external_id: str | None = None,
    external_link: str | None = None,
) -> None:
    await db.execute(
        """
        UPDATE messages
           SET status = 'COMPLETADO',
               intent = COALESCE(?, intent),
               result_json = COALESCE(?, result_json),
               external_id = ?, external_link = ?,
               error = NULL, next_attempt_at = NULL
         WHERE id = ?;
        """,
        (intent, result_json, external_id, external_link, message_id),
    )
    await db.commit()


async def schedule_retry(
    db: aiosqlite.Connection,
    message_id: int,
    *,
    back_to: MessageStatus,
    error: str,
    delay_seconds: int,
) -> None:
    await db.execute(
        """
        UPDATE messages
           SET status = ?, error = ?,
               next_attempt_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '+' || ? || ' seconds')
         WHERE id = ?;
        """,
        (back_to.value, error, delay_seconds, message_id),
    )
    await db.commit()


async def mark_error(db: aiosqlite.Connection, message_id: int, *, error: str) -> None:
    await db.execute(
        "UPDATE messages SET status = 'ERROR', error = ?, next_attempt_at = NULL WHERE id = ?;",
        (error, message_id),
    )
    await db.commit()


async def release(db: aiosqlite.Connection, message_id: int) -> None:
    """Devuelve un mensaje a su cola en un apagado ordenado.

    En la fase NLP se devuelve el intento (no ha pasado nada externo). En la
    fase Google NO: la petición puede haber llegado a Google aunque cancelemos
    aquí, y attempts > 1 es lo que activa la comprobación de duplicados.
    """
    await db.execute(
        """
        UPDATE messages
           SET status = CASE status WHEN 'PROCESANDO' THEN 'PENDIENTE' ELSE 'INTERPRETADO' END,
               attempts = CASE status WHEN 'PROCESANDO' THEN MAX(attempts - 1, 0) ELSE attempts END
         WHERE id = ? AND status IN ('PROCESANDO', 'EJECUTANDO');
        """,
        (message_id,),
    )
    await db.commit()
