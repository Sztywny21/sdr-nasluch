# Nasłuch

Jeden projekt Dockera: skaner i strona [SDR Hub](https://github.com/shajen/sdr-hub) oraz transkrypcja, telefon i głos. Klucze API wpisujesz lokalnie, Git ich nie zapisuje.

## Drugi komputer

```bash
git clone https://github.com/Sztywny21/sdr-nasluch.git
cd sdr-nasluch
cp .env.example .env
./start.sh
```

Strona jest pod `http://127.0.0.1:8000/`. Na pustej bazie login to `admin`, hasło `password`. Wepnij RTL-SDR przed startem. Nagrania i logi zostają w katalogach `data` i `log`.

W `.env` wpisz:

- `GROQ_API_KEY` — transkrypcja i odpowiedzi. Bez klucza strona i skaner wstają, a transkrypcja czeka i sama rusza, gdy klucz się pojawi.
- `TELEGRAM_TOKEN` i `TELEGRAM_CHAT_ID` — powiadomienie na telefon. Bez nich tekst zostaje tylko na stronie.

Po zmianie kluczy: `docker compose up -d`.

## Głos na gnieździe

```bash
pactl list short sinks
```

Nazwę gniazda wpisz w `.env` jako `AUDIO_SINK`, potem:

```bash
./start.sh glos
```

## Co wstaje

- `hub` buduje się z obrazu `shajen/sdr-hub` i ma w środku nasze strony, limit spektrogramu i dekoder AM. Nie trzeba nic dogrywać po starcie.
- `transcribe` czyta te same nagrania z katalogu `data`, wysyła mowę do Groq i na Telegram tylko wtedy, gdy jest rozpoznany tekst.

Zmiany w stronach są w `overlay/`. Po edycji przebuduj: `./start.sh`.

Pliki stron i dekodera są zmianami w SDR Hub i zostają na [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html).
