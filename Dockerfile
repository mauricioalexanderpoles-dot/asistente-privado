FROM python:3.12-slim

# UID/GID del usuario del host que es dueño de ./data (compruébalo con `id -u` e `id -g`)
ARG UID=1000
ARG GID=1000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias primero: así Docker cachea esta capa y no reinstala en cada cambio de código
COPY requirements.txt .
RUN pip install -r requirements.txt

# Usuario sin privilegios
RUN groupadd --gid ${GID} app && useradd --uid ${UID} --gid ${GID} --no-create-home app

COPY app ./app

USER app

CMD ["python", "-m", "app.main"]
