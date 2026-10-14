-- v2: reintentos con espera (backoff) para errores transitorios del LLM.
ALTER TABLE messages ADD COLUMN next_attempt_at TEXT;

-- Cubre la consulta del worker: PENDIENTE + ya toca reintentar + más antiguo primero.
CREATE INDEX IF NOT EXISTS idx_messages_queue
    ON messages (status, next_attempt_at, created_at);
