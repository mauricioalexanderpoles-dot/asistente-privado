-- v3: fase de ejecución en Google.
--   PENDIENTE -> PROCESANDO -> INTERPRETADO -> EJECUTANDO -> COMPLETADO | ERROR
--
-- SQLite no permite modificar un CHECK con ALTER TABLE, así que se reconstruye
-- la tabla (procedimiento oficial: crear nueva, copiar, borrar, renombrar).
-- init_db.py envuelve este fichero en BEGIN/COMMIT: o se aplica entero o nada.

CREATE TABLE messages_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id             INTEGER NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    user_id             INTEGER NOT NULL,
    text                TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'PENDIENTE'
                        CHECK (status IN ('PENDIENTE', 'PROCESANDO', 'INTERPRETADO',
                                          'EJECUTANDO', 'COMPLETADO', 'ERROR')),
    intent              TEXT,
    result_json         TEXT,               -- salida validada del LLM (ResultadoNLP)
    error               TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,   -- intentos de la FASE actual
    received_at         TEXT    NOT NULL,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    next_attempt_at     TEXT,
    external_id         TEXT,               -- id del evento o tarea en Google
    external_link       TEXT,               -- htmlLink / webViewLink
    UNIQUE (chat_id, telegram_message_id)
) STRICT;

INSERT INTO messages_new (
    id, chat_id, telegram_message_id, user_id, text, status, intent, result_json,
    error, attempts, received_at, created_at, updated_at, next_attempt_at
)
SELECT
    id, chat_id, telegram_message_id, user_id, text, status, intent, result_json,
    error, attempts, received_at, created_at, updated_at, next_attempt_at
FROM messages;

-- DROP elimina también los índices y el trigger de la tabla vieja
DROP TABLE messages;
ALTER TABLE messages_new RENAME TO messages;

CREATE INDEX idx_messages_status_created ON messages (status, created_at);
CREATE INDEX idx_messages_queue ON messages (status, next_attempt_at, created_at);

CREATE TRIGGER trg_messages_updated_at
AFTER UPDATE ON messages
FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE messages
       SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
     WHERE id = NEW.id;
END;
