ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PELANDO_CACHE_PATH=/data/cache.sqlite
RUN useradd --create-home --uid 1000 pelando && mkdir -p /data && chown pelando:pelando /data
COPY --from=builder /opt/venv /opt/venv
USER pelando
WORKDIR /home/pelando
VOLUME ["/data"]
ENTRYPOINT ["pelando-mcp"]
