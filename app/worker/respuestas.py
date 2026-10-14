"""Textos que el worker envía por Telegram (HTML). Separados de la lógica."""

from datetime import date, datetime
from html import escape
from zoneinfo import ZoneInfo

from app.nlp.schemas import ResultadoNLP

_DIAS_CORTOS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _fecha(d: date) -> str:
    return f"{_DIAS_CORTOS[d.weekday()]} {d:%d/%m/%Y}"


def _hora_de(punto: dict, tz: ZoneInfo) -> str | None:
    """`punto` es el start/end de un evento de Calendar."""
    if dt := punto.get("dateTime"):
        return datetime.fromisoformat(dt).astimezone(tz).strftime("%H:%M")
    return None


def _enlace(url: str | None, texto: str) -> str:
    return f'\n<a href="{escape(url, quote=True)}">{texto}</a>' if url else ""


def evento_creado(r: ResultadoNLP, evento: dict, tz: ZoneInfo) -> str:
    inicio, fin = _hora_de(evento["start"], tz), _hora_de(evento["end"], tz)
    cuando = f"{_fecha(r.fecha)}, {inicio}–{fin}" if inicio else f"{_fecha(r.fecha)} (todo el día)"
    return (
        f"📅 <b>Evento creado</b>: «{escape(r.titulo)}»\n🕒 {cuando}"
        + _enlace(evento.get("htmlLink"), "Abrir en Google Calendar")
    )


def tarea_creada(r: ResultadoNLP, tarea: dict) -> str:
    limite = f"para el {_fecha(r.fecha)}" if r.fecha else "sin fecha límite"
    return (
        f"✅ <b>Tarea creada</b>: «{escape(r.titulo)}»\n📌 {limite}"
        + _enlace(tarea.get("webViewLink"), "Abrir en Google Tasks")
    )


def agenda(fecha: date, eventos: list[dict], tareas: list[dict], tz: ZoneInfo) -> str:
    lineas = [f"🗓 <b>{_fecha(fecha)}</b>"]

    lineas.append("\n<b>Eventos</b>")
    if not eventos:
        lineas.append("— nada")
    for ev in eventos:
        hora = _hora_de(ev["start"], tz) or "todo el día"
        lineas.append(f"• {hora} · {escape(ev.get('summary', '(sin título)'))}")

    lineas.append("\n<b>Tareas</b>")
    if not tareas:
        lineas.append("— nada")
    for t in tareas:
        lineas.append(f"• {escape(t.get('title') or '(sin título)')}")

    return "\n".join(lineas)


def no_entendido() -> str:
    return (
        "🤔 No he entendido qué quieres hacer.\n"
        "Prueba con algo como «mañana a las 10 revisión del proyecto»."
    )


def error(mensaje_usuario: str) -> str:
    return f"❌ {escape(mensaje_usuario)}"
