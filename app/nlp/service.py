"""Intérprete de lenguaje natural.

`Interprete` es un Protocol: el worker depende de él, no de OpenAI. Cuando
quieras un modelo local (Ollama) o un doble para los tests, basta con escribir
otra clase con el mismo método `interpretar`.
"""

import logging
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import openai
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.nlp.errors import NLPErrorPermanente, NLPErrorTransitorio
from app.nlp.prompts import construir_instrucciones
from app.nlp.schemas import ResultadoNLP

logger = logging.getLogger(__name__)


class Interprete(Protocol):
    async def interpretar(self, texto: str, recibido_en: datetime) -> ResultadoNLP: ...


class OpenAIInterprete:
    def __init__(self, *, api_key: str, model: str, timeout: float, tz: ZoneInfo) -> None:
        # max_retries: el SDK ya reintenta fallos breves con backoff; los fallos
        # largos (API caída minutos) los cubre el worker con next_attempt_at.
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=2)
        self._model = model
        self._tz = tz

    async def interpretar(self, texto: str, recibido_en: datetime) -> ResultadoNLP:
        referencia = recibido_en.astimezone(self._tz)

        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=construir_instrucciones(referencia),
                input=texto,
                text_format=ResultadoNLP,
                store=False,  # que OpenAI no guarde la conversación en su lado
            )
        # --- transitorios: la petición era correcta, el servicio no respondió bien
        except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as e:
            raise NLPErrorTransitorio(
                f"{type(e).__name__}: {e}",
                mensaje_usuario="El servicio de IA no responde; lo reintentaré más tarde.",
            ) from e
        # --- permanentes: 400, 401, 403, 404... (credenciales, modelo inexistente, esquema)
        except openai.APIStatusError as e:
            raise NLPErrorPermanente(f"HTTP {e.status_code}: {e.message}") from e
        # --- el JSON cumple el esquema pero no nuestras reglas (_coherencia, fecha imposible)
        except ValidationError as e:
            raise NLPErrorPermanente(
                f"Salida del LLM incoherente: {e.errors(include_url=False)}",
                mensaje_usuario="No he sabido interpretar la fecha o el título. "
                "¿Puedes reformularlo? Ej.: «el jueves a las 10 reunión con el tutor».",
            ) from e
        except openai.OpenAIError as e:
            raise NLPErrorPermanente(f"{type(e).__name__}: {e}") from e

        resultado = response.output_parsed
        if resultado is None:
            # Respuesta incompleta o rechazo del modelo: no hay objeto parseado
            raise NLPErrorPermanente(f"Sin salida estructurada (status={response.status})")

        logger.debug("NLP -> %s", resultado.model_dump())
        return resultado
