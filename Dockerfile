FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

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
