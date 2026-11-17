# Signage server - plain Python (only segno) + ffmpeg for auto-conversion
FROM python:3.12-slim

WORKDIR /app
# segno = QR generator (pure Python, no C dependencies) for the optional QR target
RUN pip install --no-cache-dir segno
# ffmpeg = auto-conversion of uploaded videos. It is used only for REMUXING
# (-c copy), not for re-encoding: a typical .mov has a fine H.264 track and only
# the QuickTime container and a moov index placed after the data get in the way.
# Without ffmpeg the server keeps running and rejects such a file with
# instructions instead of fixing it.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY tvserver.py multipart.py video_convert.py fix_media.py errors.py validators.py brand.py ratelimit.py mediacheck.py alerts.py mediautil.py statefmt.py ./
COPY tv ./tv

# content and state are stored in /data (mount it as a volume so it survives restarts)
ENV SIGNAGE_MEDIA=/data/tv-media \
    SIGNAGE_STATE=/data/tv-state.json
RUN mkdir -p /data/tv-media

# The container deliberately runs as root (no USER): the /data volume may already
# be owned by root from earlier root containers, and a non-root user could not
# write to it (state file, uploads, fix_media.py). Switching to USER requires a
# one-time `chown -R <uid>` of the data volume and a matching --user at run time.
EXPOSE 8090
# /api/health is public and unauthenticated; a non-zero exit means unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=3)"
CMD ["python3", "tvserver.py", "8090"]
