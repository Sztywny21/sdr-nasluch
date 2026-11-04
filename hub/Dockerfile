FROM shajen/sdr-hub:latest

COPY overlay/views.py /app/sdr/views.py
COPY overlay/urls.py /app/sdr/urls.py
COPY overlay/models.py /app/sdr/models.py
COPY overlay/am_decoder.py /app/sdr/decoders/am_decoder.py
COPY overlay/spectogram_reader.py /app/sdr/utils/spectogram_reader.py
COPY overlay/main.html /app/templates/generic/main.html
COPY overlay/home.html /app/templates/home.html
COPY overlay/transmission.html /app/sdr/templates/transmission.html
COPY overlay/transcripts.html /app/sdr/templates/transcripts.html
COPY overlay/answers.html /app/sdr/templates/answers.html
COPY overlay/live.html /app/sdr/templates/live.html
COPY overlay/groups.html /app/sdr/templates/groups.html
