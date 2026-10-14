from app.errors import ErrorPermanente, ErrorTransitorio


class NLPErrorTransitorio(ErrorTransitorio):
    """Fallo temporal del proveedor de LLM."""


class NLPErrorPermanente(ErrorPermanente):
    """Credenciales, modelo inexistente o salida incoherente del LLM."""
