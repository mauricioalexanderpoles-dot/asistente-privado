from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MessageStatus(StrEnum):
    """Debe coincidir con el CHECK de la migración 0003."""

    PENDIENTE = "PENDIENTE"        # en cola para el LLM
    PROCESANDO = "PROCESANDO"      # el LLM lo está interpretando
    INTERPRETADO = "INTERPRETADO"  # en cola para Google (result_json ya guardado)
    EJECUTANDO = "EJECUTANDO"      # llamando a Google
    COMPLETADO = "COMPLETADO"
    ERROR = "ERROR"


# Estado "en curso" -> estado de cola al que vuelve si hay que reintentar
COLA_DE = {
    MessageStatus.PROCESANDO: MessageStatus.PENDIENTE,
    MessageStatus.EJECUTANDO: MessageStatus.INTERPRETADO,
}


@dataclass(frozen=True, slots=True)
class ClaimedMessage:
    """Mensaje reclamado por el worker (ya está en PROCESANDO o EJECUTANDO)."""

    id: int
    chat_id: int
    telegram_message_id: int
    text: str
    received_at: datetime
    attempts: int  # intentos de la fase actual, incluido este
    status: MessageStatus
    result_json: str | None

    @property
    def cola(self) -> MessageStatus:
        return COLA_DE[self.status]
