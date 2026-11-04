FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg pulseaudio-utils alsa-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir numpy==2.2.6 edge-tts==7.3.1

COPY transcribe/transcribe.py /opt/stt/transcribe.py

ENV SDR_DATA=/data \
    SDR_STATE=/state \
    EDGE_TTS=edge-tts \
    PYTHONUNBUFFERED=1

CMD ["python", "-u", "/opt/stt/transcribe.py"]
