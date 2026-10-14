"""Traduce una intención ya interpretada (ResultadoNLP) a acciones en Google."""

import hashlib
from dataclasses import dataclass
from typing import Protocol
from zoneinfo import ZoneInfo

from app.db.models import ClaimedMessage
from app.errors import ErrorPermanente
from app.integrations.google_workspace import GoogleWorkspace
from app.nlp.schemas import Intencion, ResultadoNLP
from app.worker import respuestas


@dataclass(frozen=True, slots=True)
class ResultadoEjecucion:
    texto: str                       # respuesta para Telegram (HTML)
    external_id: str | None = None
    external_link: str | None = None


class Ejecutor(Protocol):
    async def ejecutar(self, msg: ClaimedMessage, r: ResultadoNLP) -> ResultadoEjecucion: ...


def referencia_externa(msg: ClaimedMessage) -> str:
    """Identidad estable del mensaje: no depende del id de nuestra BD."""
    return f"tg-{msg.chat_id}-{msg.telegram_message_id}"


def event_id_para(msg: ClaimedMessage) -> str:
    """Calendar exige ids con caracteres base32hex (0-9, a-v); el hex encaja."""
    return hashlib.sha256(referencia_externa(msg).encode()).hexdigest()[:32]


class EjecutorGoogle:
    def __init__(self, google: GoogleWorkspace, tz: ZoneInfo) -> None:
        self._g = google
        self._tz = tz

    async def ejecutar(self, msg: ClaimedMessage, r: ResultadoNLP) -> ResultadoEjecucion:
        ref = referencia_externa(msg)

        match r.intencion:
            case Intencion.CREAR_EVENTO:
                evento = await self._g.crear_evento(
                    event_id=event_id_para(msg),
                    titulo=r.titulo,
                    fecha=r.fecha,
                    hora=r.hora,
                    descripcion=f"Creado desde Telegram por el asistente.\nMensaje: «{msg.text}»",
                    referencia=ref,
                )
                return ResultadoEjecucion(
                    texto=respuestas.evento_creado(r, evento, self._tz),
                    external_id=evento["id"],
                    external_link=evento.get("htmlLink"),
                )

            case Intencion.CREAR_TAREA:
                tarea = await self._g.crear_tarea(
                    titulo=r.titulo,
                    fecha=r.fecha,
                    marcador=f"[asistente:{ref}]",
                    # attempts > 1: hubo un intento anterior que pudo llegar a Google
                    comprobar_duplicado=msg.attempts > 1,
                )
                return ResultadoEjecucion(
                    texto=respuestas.tarea_creada(r, tarea),
                    external_id=tarea["id"],
                    external_link=tarea.get("webViewLink"),
                )

            case Intencion.LISTAR:
                fecha = r.fecha or msg.received_at.astimezone(self._tz).date()
                eventos, tareas = await self._g.listar_dia(fecha)
                return ResultadoEjecucion(texto=respuestas.agenda(fecha, eventos, tareas, self._tz))

            case _:
                raise ErrorPermanente(f"Intención sin ejecutor: {r.intencion}")
