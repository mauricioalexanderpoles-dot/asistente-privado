"""Cliente asíncrono de Google Calendar y Google Tasks.

google-api-python-client es síncrono (httplib2) y no es thread-safe, así que:
- cada llamada se ejecuta con asyncio.to_thread para no bloquear el event loop
  (y con él, la recepción de mensajes de Telegram);
- cada llamada usa su propio objeto Http (recomendación oficial de Google);
- un lock serializa las llamadas, porque las credenciales (que se refrescan
  solas cuando el access token caduca) son compartidas.

Todas las excepciones salen traducidas a ErrorTransitorio / ErrorPermanente,
que es lo único que entiende el worker.
"""

import asyncio
import logging
import threading
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.errors import ErrorPermanente, ErrorTransitorio

logger = logging.getLogger(__name__)

_HTTP_TRANSITORIOS = {408, 429, 500, 502, 503, 504}
_MSG_REINTENTO = "Google no responde ahora mismo; lo reintentaré en unos minutos."
_MSG_REAUTH = "La autorización de Google ha caducado o se ha revocado. Hay que repetir el flujo OAuth."


class GoogleErrorTransitorio(ErrorTransitorio):
    pass


class GoogleErrorPermanente(ErrorPermanente):
    pass


def _traducir_http_error(e: HttpError) -> ErrorTransitorio | ErrorPermanente:
    status = int(e.resp.status)
    contenido = (e.content or b"").lower()
    detalle = f"Google HTTP {status}: {getattr(e, 'reason', '')}"

    # 403 con rateLimitExceeded/userRateLimitExceeded es una cuota temporal, no un permiso
    if status in _HTTP_TRANSITORIOS or (status == 403 and b"ratelimitexceeded" in contenido):
        return GoogleErrorTransitorio(detalle, codigo_http=status, mensaje_usuario=_MSG_REINTENTO)
    if status == 401:
        return GoogleErrorPermanente(detalle, codigo_http=status, mensaje_usuario=_MSG_REAUTH)
    if status == 403:
        return GoogleErrorPermanente(
            detalle,
            codigo_http=status,
            mensaje_usuario="Google ha denegado el permiso (¿API desactivada o falta un scope?).",
        )
    return GoogleErrorPermanente(detalle, codigo_http=status)


def _rfc3339_fecha(d: date, hora: str = "00:00:00.000") -> str:
    """Google Tasks solo usa la parte de fecha de `due`; la hora se ignora."""
    return f"{d.isoformat()}T{hora}Z"


class GoogleWorkspace:
    def __init__(
        self,
        credentials: Credentials,
        *,
        tz: ZoneInfo,
        calendar_id: str = "primary",
        tasklist_id: str = "@default",
        duracion_evento: timedelta = timedelta(hours=1),
        timeout: float = 30.0,
    ) -> None:
        self._creds = credentials
        self._tz = tz
        self._calendar_id = calendar_id
        self._tasklist_id = tasklist_id
        self._duracion = duracion_evento
        self._timeout = timeout
        self._lock = threading.Lock()

        # static_discovery: usa el documento de la API incluido en la librería,
        # sin petición de red al arrancar.
        self._calendar = build("calendar", "v3", credentials=credentials, static_discovery=True)
        self._tasks = build("tasks", "v1", credentials=credentials, static_discovery=True)

    # ------------------------------------------------------------ ejecución común

    async def _execute(self, request: Any) -> dict:
        def _run() -> dict:
            with self._lock:
                http = AuthorizedHttp(self._creds, http=httplib2.Http(timeout=self._timeout))
                # num_retries: reintentos cortos del propio cliente (5xx/429) con backoff.
                # Los fallos largos los cubre el worker con next_attempt_at.
                return request.execute(http=http, num_retries=2)

        try:
            return await asyncio.to_thread(_run)
        except HttpError as e:
            raise _traducir_http_error(e) from e
        except RefreshError as e:
            # invalid_grant = token revocado o caducado. Algunas versiones marcan
            # los fallos del servidor de tokens como reintentables.
            if getattr(e, "retryable", False):
                raise GoogleErrorTransitorio(f"RefreshError: {e}", mensaje_usuario=_MSG_REINTENTO) from e
            raise GoogleErrorPermanente(f"RefreshError: {e}", mensaje_usuario=_MSG_REAUTH) from e
        except (TransportError, httplib2.HttpLib2Error, TimeoutError, OSError) as e:
            # DNS, conexión rechazada, TLS, timeout... (ssl.SSLError hereda de OSError)
            raise GoogleErrorTransitorio(
                f"{type(e).__name__}: {e}", mensaje_usuario=_MSG_REINTENTO
            ) from e

    # ------------------------------------------------------------ Calendar

    async def crear_evento(
        self,
        *,
        event_id: str,
        titulo: str,
        fecha: date,
        hora: time | None,
        descripcion: str,
        referencia: str,
    ) -> dict:
        """Inserta un evento de forma IDEMPOTENTE.

        `event_id` lo elegimos nosotros (determinista a partir del mensaje). Si un
        reintento intenta insertarlo otra vez, Google responde 409 y devolvemos
        el que ya existe en lugar de duplicarlo.
        """
        if hora is not None:
            inicio = datetime.combine(fecha, hora, tzinfo=self._tz)
            fin = inicio + self._duracion
            start = {"dateTime": inicio.isoformat(), "timeZone": self._tz.key}
            end = {"dateTime": fin.isoformat(), "timeZone": self._tz.key}
        else:
            # Evento de día completo: el fin es EXCLUSIVO (día siguiente)
            start = {"date": fecha.isoformat()}
            end = {"date": (fecha + timedelta(days=1)).isoformat()}

        body = {
            "id": event_id,
            "summary": titulo,
            "description": descripcion,
            "start": start,
            "end": end,
            "reminders": {"useDefault": True},
            "extendedProperties": {"private": {"asistente_ref": referencia}},
        }
        try:
            return await self._execute(
                self._calendar.events().insert(calendarId=self._calendar_id, body=body)
            )
        except GoogleErrorPermanente as e:
            if e.codigo_http != 409:
                raise
            logger.info("El evento %s ya existía: reintento idempotente", event_id)
            return await self._execute(
                self._calendar.events().get(calendarId=self._calendar_id, eventId=event_id)
            )

    # ------------------------------------------------------------ Tasks

    async def crear_tarea(
        self,
        *,
        titulo: str,
        fecha: date | None,
        marcador: str,
        comprobar_duplicado: bool,
    ) -> dict:
        """Tasks NO permite elegir el id, así que la idempotencia se hace con un
        marcador en las notas: en un reintento, primero se busca la tarea."""
        if comprobar_duplicado and (existente := await self._buscar_tarea(marcador)):
            logger.info("La tarea %s ya existía: reintento idempotente", marcador)
            return existente

        body: dict[str, Any] = {"title": titulo, "notes": marcador}
        if fecha is not None:
            body["due"] = _rfc3339_fecha(fecha)
        return await self._execute(
            self._tasks.tasks().insert(tasklist=self._tasklist_id, body=body)
        )

    async def _buscar_tarea(self, marcador: str) -> dict | None:
        # Solo las tocadas en los últimos días: suficiente para cubrir reintentos
        desde = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        resp = await self._execute(
            self._tasks.tasks().list(
                tasklist=self._tasklist_id,
                updatedMin=desde,
                showCompleted=True,
                showHidden=True,
                maxResults=100,
            )
        )
        return next(
            (t for t in resp.get("items", []) if marcador in (t.get("notes") or "")),
            None,
        )

    # ------------------------------------------------------------ consulta

    async def listar_dia(self, fecha: date) -> tuple[list[dict], list[dict]]:
        inicio = datetime.combine(fecha, time.min, tzinfo=self._tz)
        fin = inicio + timedelta(days=1)

        eventos = await self._execute(
            self._calendar.events().list(
                calendarId=self._calendar_id,
                timeMin=inicio.isoformat(),
                timeMax=fin.isoformat(),
                singleEvents=True,   # expande los eventos recurrentes
                orderBy="startTime",
                maxResults=50,
            )
        )
        tareas = await self._execute(
            self._tasks.tasks().list(
                tasklist=self._tasklist_id,
                dueMin=_rfc3339_fecha(fecha),
                dueMax=_rfc3339_fecha(fecha, "23:59:59.999"),
                showCompleted=False,
                maxResults=100,
            )
        )
        return eventos.get("items", []), tareas.get("items", [])
