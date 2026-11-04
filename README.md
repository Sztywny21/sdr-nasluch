# Nasłuch

Jeden projekt Dockera: skaner i strona [SDR Hub](https://github.com/shajen/sdr-hub) oraz transkrypcja, telefon i głos. Klucze API wpisujesz lokalnie. Git ich nie zapisuje.

## Pobranie

Na drugim komputerze zainstaluj [Docker](https://docs.docker.com/engine/install/), wepnij RTL-SDR i pobierz projekt:

```bash
git clone https://github.com/Sztywny21/sdr-nasluch.git
cd sdr-nasluch
cp .env.example .env
```

Potem wpisz klucze, tak jak niżej, i uruchom:

```bash
./start.sh
```

Strona jest pod `http://127.0.0.1:8000/`. Na pustej bazie login to `admin`, hasło `password`. Nagrania i logi zostają w katalogach `data` i `log`.

## Klucz do modeli AI

Oba modele idą przez Groq i korzystają z jednego klucza:

- `whisper-large-v3` zamienia nagranie na tekst
- `openai/gpt-oss-120b` odpowiada, gdy nagranie zaczyna się od słowa „pytanie”

Głos Marka na gnieździe nie potrzebuje klucza.

1. Wejdź na [console.groq.com](https://console.groq.com) i załóż konto.
2. Otwórz **API Keys** i zrób **Create API Key**.
3. Skopiuj klucz. Groq pokazuje go tylko raz.
4. Wklej go w pliku `.env` w tej linii, bez cudzysłowu i bez spacji:

```
GROQ_API_KEY=wklej_tutaj_klucz
```

5. Wczytaj klucz do kontenera:

```bash
docker compose up -d --force-recreate transcribe
```

Bez tego klucza strona i skaner wstają, a transkrypcja czeka. Jak dopiszesz klucz i zrobisz krok 5, sama rusza. Klucza nie wklejaj do gita ani do czatu.

## Telegram

To nie jest klucz modelu. Bez niego tekst zostaje na stronie, na telefon nic nie idzie.

1. W Telegramie napisz do [@BotFather](https://t.me/BotFather), wyślij `/newbot` i skopiuj token.
2. Napisz coś do swojego bota, potem otwórz w przeglądarce `https://api.telegram.org/botTOKEN/getUpdates` (wstaw swój token zamiast `TOKEN`) i odczytaj `chat.id`.
3. Wpisz oba w `.env`:

```
TELEGRAM_TOKEN=token_z_botfather
TELEGRAM_CHAT_ID=twoj_chat_id
```

4. `docker compose up -d --force-recreate transcribe`

Na telefon idzie tylko rozpoznana mowa albo pytanie, które ma odpowiedź.

## Głos na gnieździe

```bash
pactl list short sinks
```

Nazwę gniazda wpisz w `.env` jako `AUDIO_SINK`, potem:

```bash
./start.sh glos
```

## Co wstaje

- `hub` buduje się z obrazu `shajen/sdr-hub` i ma w środku nasze strony, limit spektrogramu i dekoder AM.
- `transcribe` czyta nagrania z katalogu `data` i wysyła je do Groq.

Zmiany w stronach są w `overlay/`. Po edycji przebuduj: `./start.sh`.

Pliki stron i dekodera są zmianami w SDR Hub i zostają na [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html).
