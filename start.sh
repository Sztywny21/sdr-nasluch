#!/bin/bash
# Podnosi stronę, skaner i transkrypcję. Klucze API są w .env.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Utworzyłem .env. Wpisz GROQ_API_KEY, TELEGRAM_TOKEN i TELEGRAM_CHAT_ID, potem uruchom jeszcze raz."
    exit 0
fi
if [ "${1:-}" = "glos" ]; then
    shift
    docker compose -f docker-compose.yml -f docker-compose.glos.yml up -d --build "$@"
else
    docker compose up -d --build "$@"
fi
echo "Strona: http://127.0.0.1:8000/"
