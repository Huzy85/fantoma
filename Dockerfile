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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY pyproject.toml /app/
RUN pip3 install --no-cache-dir \
    "camoufox>=0.4" \
    "playwright>=1.40" \
    "httpx>=0.25" \
    "capsolver" \
    "flask>=3.0" \
    && pip3 install --no-cache-dir -e /app 2>/dev/null || true

# Download Camoufox browser binary
RUN python3 -c "from camoufox.sync_api import Camoufox; print('Camoufox binary ready')" 2>/dev/null \
    || python3 -m camoufox fetch 2>/dev/null \
    || echo "Camoufox binary will be fetched on first run"

# Copy Fantoma source
COPY . /app

# Install Fantoma properly
RUN pip3 install --no-cache-dir -e ".[captcha]"

# Xvfb display setup
ENV DISPLAY=:99

# Create dirs Fantoma expects
RUN mkdir -p /root/.local/share/fantoma/traces \
    /root/.local/share/fantoma/sessions \
    /root/.local/share/fantoma/form_memory

EXPOSE 7860

# Start Xvfb + server
CMD ["sh", "-c", "Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX &>/dev/null & sleep 1 && python3 /app/server.py"]
