"""OAuth2 con Google: flujo de autorización (una sola vez) y carga de credenciales.

El flujo se ejecuta en un contenedor aparte con el puerto publicado solo en
127.0.0.1 del servidor; desde el portátil se llega con un túnel SSH:

    portátil$ ssh -L 8080:localhost:8080 usuario@servidor
    servidor$ docker compose --profile tools run --rm --service-ports google-auth

El bot monta secrets/ en SOLO LECTURA: nunca escribe el token, solo lo usa.
"""

import logging
import os
import stat
from pathlib import Path

from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

# Mínimo privilegio: eventos (no gestión de calendarios) y tareas
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]

_COMANDO_AUTH = "docker compose --profile tools run --rm --service-ports google-auth"


class CredencialesNoDisponibles(RuntimeError):
    pass


# ---------------------------------------------------------------- uso normal (bot)

def cargar_credenciales(token_path: Path) -> Credentials:
    if not token_path.is_file():
        raise CredencialesNoDisponibles(
            f"No existe {token_path}. Autoriza la app con: {_COMANDO_AUTH}"
        )

    modo = stat.S_IMODE(token_path.stat().st_mode)
    if modo & 0o077:
        logger.warning("%s tiene permisos %o; deberían ser 600", token_path, modo)

    # Sin pasar scopes: así se leen los que realmente se guardaron
    creds = Credentials.from_authorized_user_file(str(token_path))

    if not creds.refresh_token:
        raise CredencialesNoDisponibles(f"{token_path} no contiene refresh_token. Repite: {_COMANDO_AUTH}")
    faltan = set(SCOPES) - set(creds.scopes or [])
    if faltan:
        raise CredencialesNoDisponibles(f"Faltan scopes {sorted(faltan)}. Repite: {_COMANDO_AUTH}")
    return creds


# ---------------------------------------------------------------- flujo de autorización

def _guardar_token(creds: Credentials, token_path: Path) -> None:
    """Escritura atómica y con permisos 600 desde el primer byte."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = token_path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    os.replace(tmp, token_path)


def ejecutar_flujo(client_secret_path: Path, token_path: Path, port: int) -> None:
    # Google permite desmarcar scopes en la pantalla de consentimiento; sin esto
    # oauthlib lanza un Warning críptico. Lo comprobamos nosotros más abajo.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    from google_auth_oauthlib.flow import InstalledAppFlow  # solo se necesita aquí

    if not client_secret_path.is_file():
        raise SystemExit(f"No encuentro {client_secret_path}. Descárgalo de Google Cloud (cliente 'Desktop app').")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(
        host="localhost",       # lo que ve Google en el redirect_uri (tu portátil, vía túnel)
        bind_addr="0.0.0.0",    # dentro del contenedor hay que escuchar en todas las interfaces
        port=port,
        open_browser=False,     # servidor headless
        authorization_prompt_message=(
            "\n1) Asegúrate de tener el túnel: ssh -L {port}:localhost:{port} usuario@servidor\n"
            "2) Abre esta URL en el navegador del portátil:\n\n{{url}}\n"
        ).format(port=port),
        success_message="Autorización completada. Ya puedes cerrar esta pestaña.",
        access_type="offline",  # queremos refresh_token
        prompt="consent",       # fuerza a Google a emitir un refresh_token nuevo
    )

    concedidos = set(getattr(creds, "granted_scopes", None) or creds.scopes or [])
    if faltan := set(SCOPES) - concedidos:
        raise SystemExit(f"No se concedieron todos los permisos: {sorted(faltan)}. Repite y marca todas las casillas.")
    if not creds.refresh_token:
        raise SystemExit("Google no devolvió refresh_token. Revoca el acceso en myaccount.google.com y repite.")

    _guardar_token(creds, token_path)
    print(f"\n✅ Token guardado en {token_path} (permisos 600).")


if __name__ == "__main__":
    from app.config import get_settings

    s = get_settings()
    ejecutar_flujo(s.google_client_secret_path, s.google_token_path, s.google_oauth_port)
