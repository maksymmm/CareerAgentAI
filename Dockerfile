FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

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
    && mkdir -p /app \
    && install -d -m 0700 -o 10001 -g 10001 /var/lib/careeragent \
    && chown -R 10001:10001 /app /var/lib/careeragent

WORKDIR /app
COPY --chown=10001:10001 career_agent_ai/ /app/career_agent_ai/
COPY --chmod=0555 --chown=10001:10001 container-entrypoint.sh /usr/local/bin/careeragent-entrypoint

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; bind=os.environ.get('CAREER_AGENT_CANDIDATE_HOST', '0.0.0.0').strip(); probe='127.0.0.1' if bind == '0.0.0.0' else '::1' if bind == '::' else bind; host=f'[{probe}]' if ':' in probe else probe; port=os.environ.get('CAREER_AGENT_CANDIDATE_PORT', '8080').strip(); urllib.request.urlopen(f'http://{host}:{port}/healthz', timeout=2).read()"]

ENTRYPOINT ["/usr/local/bin/careeragent-entrypoint"]
