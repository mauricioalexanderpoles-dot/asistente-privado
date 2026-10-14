"""Contrato de salida del LLM.

Este modelo se envía a OpenAI como JSON Schema estricto (Structured Outputs),
así que el modelo SOLO puede devolver estos campos con estos tipos. Las reglas
que JSON Schema no puede expresar (coherencia entre campos, fechas que existan)
se validan después con validadores de Pydantic.

Notas sobre el modo estricto de OpenAI:
- Todos los campos deben ser obligatorios. Lo "opcional" se expresa como
  `X | None` SIN valor por defecto: el campo siempre viene, a veces a null.
- Los `field_validator`/`model_validator` no viajan en el esquema; se ejecutan
  en local al parsear la respuesta.
"""

from datetime import date, time
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FECHA_RE = r"^\d{4}-\d{2}-\d{2}$"
HORA_RE = r"^([01]\d|2[0-3]):[0-5]\d$"

FechaISO = Annotated[str, Field(pattern=FECHA_RE)]
HoraISO = Annotated[str, Field(pattern=HORA_RE)]


class Intencion(StrEnum):
    CREAR_EVENTO = "crear_evento"
    CREAR_TAREA = "crear_tarea"
    LISTAR = "listar"
    # Válvula de escape: sin ella, el LLM está OBLIGADO a encajar "hola" en una de las anteriores
    NO_ENTENDIDO = "no_entendido"


class ResultadoNLP(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intencion: Intencion = Field(description="Acción que el usuario quiere realizar.")
    titulo: str | None = Field(
        description="Título breve y limpio del evento o tarea, sin la fecha ni la hora. "
        "null si la intención es listar o no_entendido."
    )
    fecha_iso: FechaISO | None = Field(
        description="Fecha en formato YYYY-MM-DD, ya resuelta respecto a la fecha de referencia. "
        "null si el usuario no indica ninguna."
    )
    hora_iso: HoraISO | None = Field(
        description="Hora de inicio en formato HH:MM (24 h). null si no se indica una hora concreta."
    )

    # --------------------------------------------------------- validación semántica

    @field_validator("titulo")
    @classmethod
    def _limpiar_titulo(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("fecha_iso")
    @classmethod
    def _fecha_existe(cls, v: str | None) -> str | None:
        # El patrón acepta 2026-02-30; esto no.
        if v is not None:
            date.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def _coherencia(self) -> "ResultadoNLP":
        if self.hora_iso and not self.fecha_iso:
            raise ValueError("hay hora pero no fecha")
        if self.intencion is Intencion.CREAR_EVENTO:
            if not self.titulo:
                raise ValueError("un evento necesita título")
            if not self.fecha_iso:
                raise ValueError("un evento necesita fecha")
        if self.intencion is Intencion.CREAR_TAREA and not self.titulo:
            raise ValueError("una tarea necesita título")
        return self

    # --------------------------------------------------------- acceso tipado

    @property
    def fecha(self) -> date | None:
        return date.fromisoformat(self.fecha_iso) if self.fecha_iso else None

    @property
    def hora(self) -> time | None:
        return time.fromisoformat(self.hora_iso) if self.hora_iso else None
