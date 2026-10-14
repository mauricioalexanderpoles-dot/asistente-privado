"""Inicialización y migraciones del esquema SQLite.

Cada fichero de migrations/ se llama NNNN_descripcion.sql. Se aplican en orden
los que tengan número mayor que PRAGMA user_version, cada uno en su propia
transacción: o se aplica entero o no se aplica.

También se puede ejecutar a mano:
    python -m app.db.init_db
"""

import asyncio
import logging
import re
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_MIGRATION_RE = re.compile(r"^(\d{4})_.+\.sql$")


def _discover_migrations() -> list[tuple[int, Path]]:
    found = []
    for path in MIGRATIONS_DIR.iterdir():
        if match := _MIGRATION_RE.match(path.name):
            found.append((int(match.group(1)), path))
    return sorted(found)


async def configure_connection(db: aiosqlite.Connection) -> None:
    """PRAGMAs que hay que aplicar en CADA conexión (no se guardan en el fichero)."""
    await db.execute("PRAGMA foreign_keys = ON;")
    await db.execute("PRAGMA busy_timeout = 5000;")  # espera hasta 5 s si otra conexión escribe


async def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        # WAL es persistente: permite leer mientras otra conexión escribe.
        await db.execute("PRAGMA journal_mode = WAL;")
        await configure_connection(db)

        async with db.execute("PRAGMA user_version;") as cur:
            (current,) = await cur.fetchone()

        pending = [(v, p) for v, p in _discover_migrations() if v > current]
        if not pending:
            logger.info("Esquema al día (v%s)", current)
            return

        for version, path in pending:
            logger.info("Aplicando migración %s", path.name)
            sql = path.read_text(encoding="utf-8")
            try:
                # user_version es transaccional: se actualiza junto con la migración
                await db.executescript(
                    f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            except Exception:
                await db.rollback()
                logger.exception("Falló la migración %s; la BD queda en v%s", path.name, current)
                raise
            current = version


if __name__ == "__main__":
    from app.config import get_settings

    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_db(get_settings().db_path))
