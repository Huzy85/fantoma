FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System deps: Xvfb (virtual display), fonts, browser libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    xvfb xauth \
    # Fonts — realistic fingerprint, cover Windows + Mac + Linux families
    fonts-liberation fonts-noto fonts-dejavu-core fonts-freefont-ttf \
    fonts-noto-cjk fonts-noto-color-emoji \
    # Browser runtime deps (Firefox/Camoufox needs these)
    libgtk-3-0 libdbus-glib-1-2 libxt6 libx11-xcb1 \
    libasound2 libpci3 libxcomposite1 libxdamage1 libxrandr2 \
    libxkbcommon0 libgbm1 libpango-1.0-0 libcairo2 libatk1.0-0 \
    # Networking tools (debug)
    curl ca-certificates \
    # noVNC — manual intervention hatch
    supervisor x11vnc novnc python3-websockify \
    && rm -rf /var/lib/apt/lists/*

# Everything at runtime belongs to this user. Root is only used at build
# time and for the first few lines of docker-entrypoint.sh.
RUN useradd -m -u 1000 -s /bin/bash fantoma

WORKDIR /app

# Upgrade pip + packaging first (hatchling needs packaging>=23)
RUN pip3 install --no-cache-dir --upgrade pip packaging

# Install Python deps first (layer cache)
COPY pyproject.toml /app/
# camoufox must match the pin in pyproject.toml. A floating ">=0.4" here
# fetched the browser for the newest release, and the later editable install
# then downgraded the package underneath it.
RUN pip3 install --no-cache-dir \
    "camoufox==0.5.4" \
    "playwright>=1.40" \
    "httpx>=0.25" \
    "capsolver" \
    "flask>=3.0" \
    "werkzeug>=3.1.9"
# The editable install of fantoma itself happens below, after the source is
# copied. It used to run here too, with its errors hidden,
# when only pyproject.toml was present, so it could never succeed and a real
# failure was invisible.

# Download the Camoufox browser binary. `camoufox fetch` exits 0 even when
# GitHub's API refuses it (unauthenticated calls are limited to 60 an hour per
# IP), which once shipped images with no browser. Retry, then fail the build
# unless a version is really installed. Pass a token to lift the limit:
#   docker build --secret id=github_token,env=GITHUB_TOKEN .
USER fantoma
ENV HOME=/home/fantoma
RUN --mount=type=secret,id=github_token,required=false,uid=1000 \
    if [ -f /run/secrets/github_token ]; then export GITHUB_TOKEN="$(cat /run/secrets/github_token)"; fi; \
    for i in 1 2 3; do \
        python3 -m camoufox fetch; \
        python3 -m camoufox active | grep -qv "not fetched" && break; \
        echo "camoufox fetch failed, retrying in 60 s"; sleep 60; \
    done; \
    python3 -m camoufox active | grep -v "not fetched" \
        || { echo "Camoufox browser not installed (GitHub rate limit?)"; exit 1; }
USER root

# Copy Fantoma source (.dockerignore keeps .env, .git and run data out)
COPY --chown=fantoma:fantoma . /app

# Install Fantoma properly
RUN pip3 install --no-cache-dir -e ".[captcha]"

# Xvfb display setup
ENV DISPLAY=:99
# Inside a container loopback is unreachable from the published port, so the
# server listens on all interfaces. It refuses to start that way without
# FANTOMA_API_KEY, unless FANTOMA_INSECURE_NO_AUTH=1 is set. The compose file
# publishes the ports on the host's loopback only.
ENV FANTOMA_HOST=0.0.0.0
# Every browser in the container is ours; also reap orphans re-parented to PID 1.
ENV FANTOMA_REAP_ALL=1

# Create dirs Fantoma expects
RUN mkdir -p /home/fantoma/.local/share/fantoma/traces \
    /home/fantoma/.local/share/fantoma/sessions \
    /home/fantoma/.local/share/fantoma/form_memory \
    /var/log/supervisor \
    && chown -R fantoma:fantoma /home/fantoma /var/log/supervisor /app

# supervisord config (replaces the distro default; socket and pidfile in /tmp
# so a non-root supervisord can create them)
COPY supervisord.conf /etc/supervisor/supervisord.conf
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 7860 6080

# The entrypoint starts as root only to re-own the profiles volume and the
# log dir, then drops to uid 1000 for supervisord and everything under it.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
