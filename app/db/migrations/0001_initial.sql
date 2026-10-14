-- Esquema v1. Cada mensaje entrante es una "unidad de trabajo" con un estado.
-- STRICT obliga a que los tipos se respeten (SQLite >= 3.37).

CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id             INTEGER NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    user_id             INTEGER NOT NULL,
    text                TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'PENDIENTE'
                        CHECK (status IN ('PENDIENTE', 'PROCESANDO', 'COMPLETADO', 'ERROR')),
    intent              TEXT,               -- se rellenará en la fase del intérprete
    result_json         TEXT,               -- respuesta de Google / salida del LLM
    error               TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,
    received_at         TEXT    NOT NULL,   -- fecha del mensaje según Telegram (ISO 8601, UTC)
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- Idempotencia: el mismo mensaje de Telegram nunca se guarda dos veces
    UNIQUE (chat_id, telegram_message_id)
) STRICT;

-- El worker buscará "los PENDIENTE más antiguos": este índice cubre esa consulta.
CREATE INDEX IF NOT EXISTS idx_messages_status_created
    ON messages (status, created_at);

-- Mantiene updated_at sin depender de que el código se acuerde de hacerlo.
CREATE TRIGGER IF NOT EXISTS trg_messages_updated_at
AFTER UPDATE ON messages
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE messages
       SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
     WHERE id = NEW.id;
END;
