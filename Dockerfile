FROM python:3.12.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAREER_AGENT_ENV=production \
    CAREER_AGENT_DB_PATH=/var/lib/careeragent/candidate.sqlite \
    CAREER_AGENT_CANDIDATE_SANDBOX=true \
    CAREER_AGENT_CANDIDATE_ID_CUTOVER=drain-and-replace-scoped-v2 \
    CAREER_AGENT_CANDIDATE_HOST=0.0.0.0 \
    CAREER_AGENT_CANDIDATE_PORT=8080 \
    CAREER_AGENT_ALLOW_NETWORK_PROVIDERS=false \
    CAREER_AGENT_ALLOW_CONSEQUENTIAL_ACTIONS=false

RUN groupadd --gid 10001 careeragent \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin careeragent \
    && mkdir -p /app /var/lib/careeragent \
    && chown -R 10001:10001 /app /var/lib/careeragent

WORKDIR /app
COPY --chown=10001:10001 career_agent_ai/ /app/career_agent_ai/

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]

ENTRYPOINT ["python", "-m", "career_agent_ai.application.runtime.candidate_service"]
