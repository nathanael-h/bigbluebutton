# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Set INSTALL_FASTER_WHISPER=true to include local faster-whisper (~1.5 GB extra).
# Default is false — use provider: openai-compatible with an external STT server instead.
ARG INSTALL_FASTER_WHISPER=false

# libgomp1 is required by CTranslate2 (the faster-whisper backend).
# Installed unconditionally because it is small (45 KB) and needed if faster-whisper
# is ever enabled at build time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root service user.
# UID/GID 999 matches the bigbluebutton system user on a standard BBB host,
# so /var/bigbluebutton/captions bind-mount permissions work without chown.
RUN groupadd --gid 999 bbb-transcriber \
    && useradd --uid 999 --gid 999 --no-create-home --shell /sbin/nologin bbb-transcriber

WORKDIR /app

# Copy requirements first for layer caching.
COPY requirements.txt requirements-docker.txt ./

RUN if [ "$INSTALL_FASTER_WHISPER" = "true" ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements-docker.txt; \
    fi

COPY agent.py bbb_redis.py config.py locale_tracker.py transcript_state.py vtt_writer.py ./
COPY samples_jfk.wav ./

# Redirect faster-whisper model downloads to /app/.cache.
# Mount a named volume at /app/.cache to persist models across container restarts.
ENV HF_HOME=/app/.cache

USER bbb-transcriber

ENTRYPOINT ["python", "agent.py"]
CMD ["start"]
