"""Jerarquía de errores compartida por todas las capas (NLP, Google...).

El worker solo necesita distinguir dos cosas: ¿merece la pena reintentar?
Cada integración traduce SUS excepciones a una de estas dos.
"""


class AppError(Exception):
    """`mensaje_usuario` se puede enseñar por Telegram; str(exc) es el detalle técnico."""

    def __init__(
        self,
        detalle: str,
        *,
        mensaje_usuario: str | None = None,
        codigo_http: int | None = None,
    ) -> None:
        super().__init__(detalle)
        self.mensaje_usuario = mensaje_usuario or "No he podido procesar tu mensaje."
        self.codigo_http = codigo_http


class ErrorTransitorio(AppError):
    """Puede funcionar si se reintenta más tarde (red, timeout, 429, 5xx)."""


class ErrorPermanente(AppError):
    """Reintentar no lo va a arreglar (credenciales, petición inválida, datos incoherentes)."""
