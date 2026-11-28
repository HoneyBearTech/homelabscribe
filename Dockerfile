FROM python:3.14-slim@sha256:22eafb370920ad8b429e0aec3fff433e0669968519f5d60469d83729c138b28b

LABEL org.opencontainers.image.title="HomelabScribe" \
      org.opencontainers.image.description="Documentation for your homelab that writes itself." \
      org.opencontainers.image.source="https://github.com/HoneyBearTech/homelabscribe" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HOMELABSCRIBE_CONFIG=/config/homelabscribe.yaml

WORKDIR /app
# Dependencies first, from a hash-pinned lockfile (regenerate it as described in docs/dependencies.md).
COPY requirements/runtime.txt ./requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
# The project is pure Python, so it runs from its source directory: nothing is pip-installed from outside the
# hash-pinned lockfile above, and there is no unpinned install step.
COPY LICENSE ./LICENSE
COPY src ./src
ENV PYTHONPATH=/app/src
RUN printf '#!/bin/sh\nexec python -m homelabscribe.cli "$@"\n' > /usr/local/bin/homelabscribe \
    && chmod 0755 /usr/local/bin/homelabscribe \
    && useradd --uid 1000 --create-home homelabscribe

USER homelabscribe
WORKDIR /data
VOLUME ["/config", "/docs", "/data"]

ENTRYPOINT ["homelabscribe"]
CMD ["run"]
