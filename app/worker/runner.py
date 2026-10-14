"""Worker asíncrono con dos fases, cada una con su cola y su presupuesto de reintentos.

    PENDIENTE --claim--> PROCESANDO --LLM ok--> INTERPRETADO --claim--> EJECUTANDO --Google ok--> COMPLETADO
        ^                    |                       ^                      |
        +--- transitorio ----+                       +---- transitorio -----+
             (backoff)       |                            (backoff)         |
                             +--> permanente / sin intentos --> ERROR <-----+

¿Por qué una fase intermedia en vez de llamar a Google justo tras el LLM?
- Si Google falla, el reintento NO vuelve a llamar al LLM: no se paga dos veces
  y, sobre todo, no se arriesga una interpretación distinta (el LLM no es determinista).
- Cada fase tiene sus propios intentos (attempts se reinicia al pasar a INTERPRETADO).
- Queda trazabilidad: en la BD se ve en qué fase se quedó cada mensaje.
"""

import asyncio
import logging
from pathlib import Path

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import LinkPreviewOptions, ReplyParameters
from pydantic import ValidationError

from app.db import repository
from app.db.init_db import configure_connection
from app.db.models import ClaimedMessage, MessageStatus
from app.errors import ErrorPermanente, ErrorTransitorio
from app.nlp import Intencion, Interprete, ResultadoNLP
from app.worker import respuestas
from app.worker.ejecutor import Ejecutor

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        *,
        db_path: Path,
        interprete: Interprete,
        ejecutor: Ejecutor,
        poll_interval: float = 5.0,
        max_attempts: int = 5,
        max_backoff: int = 300,
    ) -> None:
        self._db_path = db_path
        self._interprete = interprete
        self._ejecutor = ejecutor
        self._poll_interval = poll_interval
        self._max_attempts = max_attempts
        self._max_backoff = max_backoff

        self.wakeup = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ ciclo de vida

    def start(self, bot: Bot) -> None:
        if self._task is not None:
            raise RuntimeError("El worker ya está arrancado")
        self._task = asyncio.create_task(self._run(bot), name="worker")
        self._task.add_done_callback(self._on_task_done)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("Worker detenido")

    @staticmethod
    def _on_task_done(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (exc := task.exception()):
            logger.critical("El worker ha muerto inesperadamente", exc_info=exc)

    # ------------------------------------------------------------ bucle

    async def _run(self, bot: Bot) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await configure_connection(db)

            if recovered := await repository.recover_orphaned(db):
                logger.warning("Recuperados %s mensajes que quedaron a medias", recovered)

            logger.info("Worker arrancado (sondeo cada %.1f s)", self._poll_interval)
            while True:
                try:
                    while (msg := await repository.claim_next(db)) is not None:
                        await self._process(db, bot, msg)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Error en el bucle del worker; reintento en %.1f s", self._poll_interval)

                await self._wait_for_work()

    async def _wait_for_work(self) -> None:
        try:
            await asyncio.wait_for(self.wakeup.wait(), timeout=self._poll_interval)
        except TimeoutError:
            pass
        self.wakeup.clear()

    # ------------------------------------------------------------ un mensaje (común a las dos fases)

    async def _process(self, db: aiosqlite.Connection, bot: Bot, msg: ClaimedMessage) -> None:
        fase = "NLP" if msg.status is MessageStatus.PROCESANDO else "Google"
        logger.info("#%s fase %s (intento %s/%s)", msg.id, fase, msg.attempts, self._max_attempts)

        try:
            if msg.status is MessageStatus.PROCESANDO:
                await self._fase_interpretar(db, bot, msg)
            else:
                await self._fase_ejecutar(db, bot, msg)

        except asyncio.CancelledError:
            await asyncio.shield(repository.release(db, msg.id))
            raise

        except ErrorTransitorio as e:
            if msg.attempts >= self._max_attempts:
                logger.error("#%s agotó los intentos en fase %s: %s", msg.id, fase, e)
                await repository.mark_error(db, msg.id, error=f"[{fase}] Agotados intentos: {e}")
                await self._notify(bot, msg, respuestas.error(e.mensaje_usuario))
            else:
                delay = min(5 * 2 ** msg.attempts, self._max_backoff)
                logger.warning("#%s transitorio en %s, reintento en %s s: %s", msg.id, fase, delay, e)
                await repository.schedule_retry(
                    db, msg.id, back_to=msg.cola, error=f"[{fase}] {e}", delay_seconds=delay
                )

        except ErrorPermanente as e:
            logger.error("#%s permanente en fase %s: %s", msg.id, fase, e)
            await repository.mark_error(db, msg.id, error=f"[{fase}] {e}")
            await self._notify(bot, msg, respuestas.error(e.mensaje_usuario))

        except Exception as e:
            logger.exception("#%s error inesperado en fase %s", msg.id, fase)
            await repository.mark_error(db, msg.id, error=f"[{fase}] Inesperado: {type(e).__name__}: {e}")
            await self._notify(bot, msg, respuestas.error("Error interno al procesar el mensaje."))

    # ------------------------------------------------------------ fase 1: LLM

    async def _fase_interpretar(self, db: aiosqlite.Connection, bot: Bot, msg: ClaimedMessage) -> None:
        r = await self._interprete.interpretar(msg.text, msg.received_at)

        if r.intencion is Intencion.NO_ENTENDIDO:
            # Nada que ejecutar: se cierra aquí
            await repository.mark_completed(
                db, msg.id, intent=r.intencion.value, result_json=r.model_dump_json()
            )
            await self._notify(bot, msg, respuestas.no_entendido())
            return

        await repository.mark_interpreted(
            db, msg.id, intent=r.intencion.value, result_json=r.model_dump_json()
        )
        logger.info("#%s INTERPRETADO (%s)", msg.id, r.intencion.value)
        # No hace falta despertar a nadie: el bucle de drenaje lo reclama en la siguiente vuelta

    # ------------------------------------------------------------ fase 2: Google

    async def _fase_ejecutar(self, db: aiosqlite.Connection, bot: Bot, msg: ClaimedMessage) -> None:
        if msg.result_json is None:
            raise ErrorPermanente("INTERPRETADO sin result_json")
        try:
            r = ResultadoNLP.model_validate_json(msg.result_json)
        except ValidationError as e:
            # Por ejemplo, si cambias el esquema y quedan mensajes antiguos en cola
            raise ErrorPermanente(f"result_json ya no valida: {e}") from e

        resultado = await self._ejecutor.ejecutar(msg, r)
        await repository.mark_completed(
            db, msg.id, external_id=resultado.external_id, external_link=resultado.external_link
        )
        logger.info("#%s COMPLETADO en Google (%s)", msg.id, resultado.external_id or r.intencion.value)
        await self._notify(bot, msg, resultado.texto)

    # ------------------------------------------------------------ Telegram

    async def _notify(self, bot: Bot, msg: ClaimedMessage, text: str) -> None:
        try:
            await bot.send_message(
                msg.chat_id,
                text,
                reply_parameters=ReplyParameters(
                    message_id=msg.telegram_message_id, allow_sending_without_reply=True
                ),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except TelegramAPIError:
            logger.exception("No se pudo notificar el resultado de #%s", msg.id)
