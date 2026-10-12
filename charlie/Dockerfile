# One image, one service per container (command selects the service).
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ASSISTANTS_ROOT=/app PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home /app app
COPY pyproject.toml ./
COPY src ./src
# Optional extra CA (only for networks with TLS inspection; empty by default).
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir -e . "anthropic==1.13.0"
COPY policy ./policy
COPY prompts ./prompts
COPY supabase ./supabase
COPY config.example.yaml ./
RUN mkdir -p /state && chown app:app /state
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"
ENTRYPOINT ["python", "-m", "assistants.services.run"]
