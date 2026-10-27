# Spoolman -> Niimbot web app.
#
# NOTE ON PRINTING: the printer is a USB serial device. A container can only
# reach it when the USB port is passed through, which works on a LINUX host
# (`--device=/dev/ttyACM0`). On Docker Desktop (Windows/macOS) the engine runs
# in a VM with no host USB access, so the web UI + preview work but physical
# printing does not. See the README.

FROM python:3.12-slim

# Fonts: the label renderer falls back to DejaVu if Arial isn't present, so
# install a font package to get clean text in the container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY . .

# Run as a non-root user. On a Linux host, this user must be in the group that
# owns the serial device (often "dialout") for printing to work; the simplest
# approach at run time is `--group-add dialout` (see README).
RUN useradd --create-home --uid 1000 app \
    && chown -R app:app /app
USER app

EXPOSE 8777

# Bind to all interfaces inside the container so the published port is reachable.
ENV HOST=0.0.0.0 PORT=8777
CMD ["python", "webapp.py"]
