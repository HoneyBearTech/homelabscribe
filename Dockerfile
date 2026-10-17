FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

LABEL org.opencontainers.image.title="HomelabScribe" \
      org.opencontainers.image.description="Documentation for your homelab that writes itself." \
      org.opencontainers.image.source="https://github.com/HoneyBearTech/homelabscribe" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOMELABSCRIBE_CONFIG=/config/homelabscribe.yaml

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 1000 --create-home homelabscribe

USER homelabscribe
WORKDIR /data
VOLUME ["/config", "/docs", "/data"]

ENTRYPOINT ["homelabscribe"]
CMD ["run"]
