#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(os.environ.get("SDR_DATA", "/data"))
STATE = Path(os.environ.get("SDR_STATE", "/state"))
MEDIA = DATA / "public" / "media"
DB = str(DATA / "db.sqlite3")
OUT = str(DATA / "transcripts.json")
LIVE_LOG = DATA / "stt_live.log"
WAVDIR = DATA / "stt_wav"
MODEL = "large-v3-turbo"
GROQ_MODEL = "whisper-large-v3"
UNRECOGNIZED = "Nie rozpoznano mowy."
JUNK_PHRASES = (
    "dzięki za uwagę",
    "dzieki za uwage",
    "dziękuję za uwagę",
    "dzięki za oglądanie",
    "subskrybuj",
    "napisy stworzone",
)
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
ANSWER_MODEL = "openai/gpt-oss-120b"
ANSWER_URL = "https://api.groq.com/openai/v1/chat/completions"
ANSWER_FAIL = "Coś poszło nie tak."
ANSWER_EMPTY = "Nie usłyszałem treści pytania."
SPEAK_WAV = STATE / "last_answer.wav"
SPEAK_BIN = os.environ.get("EDGE_TTS", "edge-tts")
SPEAK_VOICE = "pl-PL-MarekNeural"
STT_PROMPT = (
    "Rozmowa radiowa CB po polsku. "
    "Jeśli nagranie zaczyna się od tak tak, to własny głos radia: nie zapisuj tych słów ani niczego po nich."
)
ANSWER_PROMPT = (
    "Odpowiadasz po polsku na pytanie usłyszane w radiu CB. "
    "Odpowiedź ma być wprost i adekwatna do pytania, bez wstępu i bez powtórzenia pytania. "
    "Krótkie pytanie faktograficzne zakończ konkretną odpowiedzią. "
    'Przykład: na "dwa plus dwa" napisz "Odpowiedź to 4." '
    "Na dłuższe pytanie odpowiedz tyle, ile trzeba, żeby odpowiedź była pełna, i nic ponad to."
)
POLL = 20
GROQ_POLL = 3
SETTLE_SECONDS = 3
SEEN = str(STATE / "telegram_seen")
# Europejski plan CB (CEPT), 40 kanałów. Częstotliwości w Hz.
CB_CHANNELS = (
    (1, 26_965_000), (2, 26_975_000), (3, 26_985_000), (4, 27_005_000),
    (5, 27_015_000), (6, 27_025_000), (7, 27_035_000), (8, 27_055_000),
    (9, 27_065_000), (10, 27_075_000), (11, 27_085_000), (12, 27_105_000),
    (13, 27_115_000), (14, 27_125_000), (15, 27_135_000), (16, 27_155_000),
    (17, 27_165_000), (18, 27_175_000), (19, 27_185_000), (20, 27_205_000),
    (21, 27_215_000), (22, 27_225_000), (23, 27_255_000), (24, 27_235_000),
    (25, 27_245_000), (26, 27_265_000), (27, 27_275_000), (28, 27_285_000),
    (29, 27_295_000), (30, 27_305_000), (31, 27_315_000), (32, 27_325_000),
    (33, 27_335_000), (34, 27_345_000), (35, 27_355_000), (36, 27_365_000),
    (37, 27_375_000), (38, 27_385_000), (39, 27_395_000), (40, 27_405_000),
)
GROQ_HEADER = STATE / "groq.hdr"


def load_items():
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, list):
                return data
    return []


def save_items(items):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(items, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)


def decode_narrow(rel_path, wav_name, modulation, sample_rate):
    # GNU Radio's FM filter needs at least 16 kHz. Narrow CB clips are demodulated here.
    import numpy as np
    import wave

    src = MEDIA / rel_path
    raw = np.fromfile(src, dtype=np.uint8)
    raw = raw[: (raw.size // 2) * 2]
    if raw.size < 4:
        raise RuntimeError("plik nagrania jest za krótki")
    signed = raw.astype(np.float32) - 128.0
    analytic = signed[0::2] + 1j * signed[1::2]
    if modulation == "AM":
        audio = np.abs(analytic)
        audio = audio - float(np.mean(audio))
    else:
        audio = np.angle(analytic[1:] * np.conj(analytic[:-1])).astype(np.float32)
    window = max(1, int(sample_rate / 4000))
    if window > 1:
        kernel = np.ones(window, dtype=np.float32) / window
        audio = np.convolve(audio, kernel, mode="same")
    peak = float(np.percentile(np.abs(audio), 99.5)) or 1.0
    audio = np.clip(audio / peak, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16)
    dest = WAVDIR / wav_name
    with wave.open(str(dest), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return dest


def decode(rel_path, wav_name, modulation, sample_rate):
    if int(sample_rate) < 16000:
        return decode_narrow(rel_path, wav_name, modulation, sample_rate)
    src = "/app/data/public/media/" + rel_path
    dst = "/app/data/stt_wav/" + wav_name
    code = "\n".join(
        [
            "import os",
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE','monitor.settings')",
            "import django",
            "django.setup()",
            "import sdr.signals",
            f"sdr.signals.decode_audio({src!r}, {dst!r}, {modulation!r}, {int(sample_rate)})",
            f"print(os.path.getsize({dst!r}))",
        ]
    )
    result = subprocess.run(
        ["docker", "exec", "-w", "/app", "sdr-hub", "python3", "-c", code],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "decode failed").strip().splitlines()
        raise RuntimeError(detail[-1][:300])
    return WAVDIR / wav_name


def accepted_speech(payload):
    segments = payload.get("segments") or []
    kept = []
    for segment in segments:
        piece = (segment.get("text") or "").strip()
        if not piece:
            continue
        no_speech = float(segment.get("no_speech_prob") or 0)
        logprob = float(segment.get("avg_logprob") or 0)
        if no_speech >= 0.5 or logprob <= -0.5:
            continue
        lowered = piece.lower()
        if any(phrase in lowered for phrase in JUNK_PHRASES):
            continue
        kept.append(piece)
    if segments:
        return " ".join(kept).strip()
    raw = (payload.get("text") or "").strip()
    lowered = raw.lower()
    if any(phrase in lowered for phrase in JUNK_PHRASES):
        return ""
    return raw


def live(message):
    line = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%H:%M:%S") + "  " + " ".join(str(message).split())
    print(line, flush=True)
    try:
        with LIVE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if LIVE_LOG.stat().st_size > 200_000:
            kept = LIVE_LOG.read_text(encoding="utf-8", errors="replace")[-150_000:]
            LIVE_LOG.write_text(kept, encoding="utf-8")
    except Exception:
        pass


def groq_enabled():
    return bool(os.environ.get("GROQ_API_KEY", "").strip())


def groq_transcribe(wav_path):
    token = os.environ.get("GROQ_API_KEY", "").strip()
    if not token:
        raise RuntimeError("brak GROQ_API_KEY")
    GROQ_HEADER.write_text("Authorization: Bearer " + token + "\n", encoding="utf-8")
    os.chmod(GROQ_HEADER, 0o600)
    body_path = Path("/tmp") / f"groq-{os.getpid()}.json"
    delay = 2
    last_error = "Groq nie odpowiedział"
    for _attempt in range(6):
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-o",
                str(body_path),
                "-w",
                "%{http_code}",
                "-H",
                "@" + str(GROQ_HEADER),
                "-F",
                "model=" + GROQ_MODEL,
                "-F",
                "language=pl",
                "-F",
                "temperature=0",
                "-F",
                "response_format=verbose_json",
                "-F",
                "prompt=" + STT_PROMPT,
                "-F",
                "file=@" + str(wav_path) + ";type=audio/wav",
                GROQ_URL,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        code = (result.stdout or "").strip()
        payload = body_path.read_text(encoding="utf-8", errors="replace") if body_path.exists() else ""
        if code == "200":
            text = accepted_speech(json.loads(payload))
            return text or UNRECOGNIZED
        last_error = f"Groq HTTP {code or result.returncode}"
        if code in {"429", "500", "502", "503", "504"}:
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        detail = payload.replace(token, "").strip().splitlines()
        if detail:
            last_error = detail[-1][:240]
        raise RuntimeError(last_error)
    raise RuntimeError(last_error)


def is_tak_word(word):
    if word in {"tak", "ta", "tac", "tack", "tag"}:
        return True
    if "taktak" in word:
        return True
    return word.startswith("tak") and len(word) <= 6


def own_voice(text):
    # „Tak tak” otwiera własną odpowiedź w eterze. Całe nagranie jest nasze, nie nowe pytanie.
    raw = str(text or "").strip()
    if not raw or raw == UNRECOGNIZED or raw.startswith("Błąd") or raw.startswith("("):
        return False
    folded = fold_letters(raw)
    if "wasnygosradia" in folded or "niezapisujtychsow" in folded:
        return True
    words = [fold_letters(word) for word in re.findall(r"[A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻż]+", raw)]
    words = [word for word in words if word]
    for index in range(min(3, len(words))):
        word = words[index]
        if "taktak" in word:
            return True
        if index + 1 < len(words) and is_tak_word(word) and is_tak_word(words[index + 1]):
            return True
    return False


def worth_sending(item):
    if item.get("own_voice"):
        return False
    if item.get("question"):
        return bool(str(item.get("answer") or "").strip() or str(item.get("answer_error") or "").strip())
    text = str(item.get("text") or "").strip()
    if (
        not text
        or text == UNRECOGNIZED
        or text.startswith("Nie rozpoznano")
        or text.startswith("Błąd:")
        or text.startswith("(cisza")
        or text.startswith("(pominięte")
    ):
        return False
    return True


def fold_letters(word):
    folded = unicodedata.normalize("NFKD", word)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z]", "", folded.lower())


def levenshtein(left, right):
    if abs(len(left) - len(right)) > 1:
        return 2
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def is_question_word(word):
    folded = fold_letters(word)
    if len(folded) < 5:
        return False
    if folded.startswith("pytani"):
        return True
    return levenshtein(folded, "pytanie") <= 1


def split_question(text):
    raw = str(text or "").strip()
    if not raw or raw == UNRECOGNIZED or raw.startswith("Błąd") or raw.startswith("("):
        return None
    match = re.match(r"\W*(\w+)", raw)
    if not match:
        return None
    if not is_question_word(match.group(1)):
        return None
    return raw[match.end() :].strip().lstrip(" \t,.:;!?-–—\"'«»„”")


def play_jack(path):
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")
    sink = os.environ.get("AUDIO_SINK", "alsa_output.pci-0000_00_1f.3.analog-stereo")
    hdmi = os.environ.get("AUDIO_HDMI", "alsa_output.pci-0000_00_1f.3.hdmi-stereo")
    card = os.environ.get("AUDIO_CARD", "alsa_card.pci-0000_00_1f.3")
    subprocess.run(["pactl", "set-card-profile", card, "output:analog-stereo"], env=env, capture_output=True)
    subprocess.run(["pactl", "set-sink-mute", hdmi, "1"], env=env, capture_output=True)
    subprocess.run(["amixer", "-c", "0", "sset", "PCM", "100%"], capture_output=True)
    subprocess.run(["amixer", "-c", "0", "sset", "Master", "100%", "unmute"], capture_output=True)
    subprocess.run(["pactl", "set-sink-volume", sink, "80%"], env=env, capture_output=True)
    subprocess.run(["pactl", "set-sink-mute", sink, "0"], env=env, capture_output=True)
    try:
        played = subprocess.run(
            ["paplay", "--device=" + sink, str(path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if played.returncode != 0:
            detail = (played.stderr or played.stdout or "paplay").strip().splitlines()
            raise RuntimeError(detail[-1][:200] if detail else "paplay")
    finally:
        subprocess.run(["pactl", "set-sink-mute", sink, "1"], env=env, capture_output=True)
        subprocess.run(["amixer", "-c", "0", "sset", "Master", "0%", "mute"], capture_output=True)


def ensure_vox_cue():
    cue = STATE / "vox_tak_marek.wav"
    if cue.exists() and cue.stat().st_size > 1000:
        return cue
    mp3 = Path("/tmp/vox-cue.mp3")
    made = subprocess.run(
        [SPEAK_BIN, "--voice", SPEAK_VOICE, "--rate=+10%", "--text", "tak", "--write-media", str(mp3)],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if made.returncode != 0:
        detail = (made.stderr or made.stdout or "edge-tts").strip().splitlines()
        raise RuntimeError(detail[-1][:200] if detail else "edge-tts")
    baked = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
            "-ac", "1", "-ar", "22050",
            "-filter:a", "silenceremove=start_periods=1:start_silence=0.02:start_threshold=-35dB,areverse,silenceremove=start_periods=1:start_silence=0.02:start_threshold=-35dB,areverse,highpass=f=200,lowpass=f=3200,volume=0.95",
            str(cue),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if baked.returncode != 0:
        detail = (baked.stderr or "ffmpeg").strip().splitlines()
        raise RuntimeError(detail[-1][:200] if detail else "ffmpeg")
    return cue


def speak_answer(text, rate="-8%"):
    # Wolniejsze „tak tak”, krótka przerwa, potem odpowiedź. Raz, bez pętli.
    spoken = " ".join(str(text or "").split())
    if not spoken or spoken in {ANSWER_FAIL, ANSWER_EMPTY}:
        return
    mp3 = Path("/tmp") / f"answer-{os.getpid()}.mp3"
    speech = SPEAK_WAV.with_name("last_answer.speech.wav")
    part = SPEAK_WAV.with_name("last_answer.tmp.wav")
    result = subprocess.run(
        [SPEAK_BIN, "--voice", SPEAK_VOICE, "--rate=" + rate, "--text", spoken, "--write-media", str(mp3)],
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "edge-tts").strip().splitlines()
        raise RuntimeError(detail[-1][:200] if detail else "edge-tts")
    voice = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(mp3),
            "-ac",
            "1",
            "-ar",
            "22050",
            "-filter:a",
            "highpass=f=200,lowpass=f=3200,volume=0.95",
            str(speech),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if voice.returncode != 0:
        detail = (voice.stderr or "ffmpeg").strip().splitlines()
        raise RuntimeError(detail[-1][:200] if detail else "ffmpeg")
    cue = ensure_vox_cue()
    conv = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(cue),
            "-f",
            "lavfi",
            "-t",
            "0.22",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=22050",
            "-i",
            str(cue),
            "-f",
            "lavfi",
            "-t",
            "0.35",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=22050",
            "-i",
            str(speech),
            "-filter_complex",
            "[0:a]aformat=sample_rates=22050:channel_layouts=mono[c1];"
            "[1:a]aformat=sample_rates=22050:channel_layouts=mono[g1];"
            "[2:a]aformat=sample_rates=22050:channel_layouts=mono[c2];"
            "[3:a]aformat=sample_rates=22050:channel_layouts=mono[wait];"
            "[4:a]aformat=sample_rates=22050:channel_layouts=mono[sp];"
            "[c1][g1][c2][wait][sp]concat=n=5:v=0:a=1,pan=stereo|c0=c0|c1=0*c0",
            str(part),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if conv.returncode != 0:
        detail = (conv.stderr or "ffmpeg").strip().splitlines()
        raise RuntimeError(detail[-1][:200] if detail else "ffmpeg")
    os.replace(part, SPEAK_WAV)
    play_jack(SPEAK_WAV)


def groq_answer(question):
    token = os.environ.get("GROQ_API_KEY", "").strip()
    if not token:
        raise RuntimeError("brak GROQ_API_KEY")
    GROQ_HEADER.write_text("Authorization: Bearer " + token + "\n", encoding="utf-8")
    os.chmod(GROQ_HEADER, 0o600)
    body_path = Path("/tmp") / f"groq-answer-{os.getpid()}.json"
    request_path = Path("/tmp") / f"groq-answer-req-{os.getpid()}.json"
    request_path.write_text(
        json.dumps(
            {
                "model": ANSWER_MODEL,
                "temperature": 0,
                "reasoning_effort": "low",
                "max_completion_tokens": 2048,
                "messages": [
                    {"role": "system", "content": ANSWER_PROMPT},
                    {"role": "user", "content": question},
                ],
            }
        ),
        encoding="utf-8",
    )
    delay = 2
    last_error = "Groq nie odpowiedział"
    for _attempt in range(4):
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-A",
                "curl/8.5.0",
                "-o",
                str(body_path),
                "-w",
                "%{http_code}",
                "-H",
                "@" + str(GROQ_HEADER),
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                "@" + str(request_path),
                ANSWER_URL,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        code = (result.stdout or "").strip()
        payload = body_path.read_text(encoding="utf-8", errors="replace") if body_path.exists() else ""
        if code == "200":
            message = json.loads(payload)["choices"][0]["message"]
            thought = message.get("reasoning") or message.get("reasoning_content") or ""
            if thought:
                live("Myślę: " + str(thought)[:4000])
            else:
                live("Model nie pokazał toku myśli. Została sama odpowiedź.")
            answer = str(message.get("content") or "").strip()
            if not answer:
                raise RuntimeError("pusta odpowiedź")
            return answer
        last_error = f"Groq HTTP {code or result.returncode}"
        if code in {"429", "500", "502", "503", "504"}:
            time.sleep(delay)
            delay = min(delay * 2, 20)
            continue
        detail = payload.strip().splitlines()
        if detail:
            last_error = detail[-1][:240]
        raise RuntimeError(last_error)
    raise RuntimeError(last_error)


def html_escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cb_channel(rel_path, begin_f, end_f):
    # Środek nagrania to siatka skanera co 2,5 kHz. Nośną bierzemy ze szczytu w pliku.
    try:
        import numpy as np

        rate = int(end_f - begin_f)
        center = (int(begin_f) + int(end_f)) / 2.0
        hz = center
        path = MEDIA / rel_path
        raw = np.fromfile(path, dtype=np.uint8).astype(np.float32)
        raw = raw[: (raw.size // 2) * 2]
        if raw.size >= 4096 and rate > 0:
            iq = (raw[0::2] - 128.0) + 1j * (raw[1::2] - 128.0)
            iq = iq[:65536]
            iq = iq - np.mean(iq)
            spec = np.abs(np.fft.fftshift(np.fft.fft(iq)))
            freqs = (np.arange(spec.size) - spec.size / 2.0) * rate / spec.size
            spec[np.abs(freqs) < 300] = 0
            hz = center + float(freqs[int(np.argmax(spec))])
        number, channel_hz = min(CB_CHANNELS, key=lambda item: abs(item[1] - hz))
        if abs(channel_hz - hz) > 6000:
            return None
        return number
    except Exception:
        return None


def pretty_mhz(value):
    try:
        mhz = float(str(value).replace(",", "."))
    except ValueError:
        return str(value)
    return f"{mhz:.3f}".replace(".", ",")


def pretty_when(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
    local = stamp.astimezone(ZoneInfo("Europe/Warsaw"))
    return local.strftime("%d.%m.%Y, %H:%M")


def telegram_details(item):
    parts = [
        "Częstotliwość",
        html_escape(pretty_mhz(item.get("frequency_mhz"))) + " MHz",
    ]
    channel = item.get("cb_channel")
    if channel:
        parts.extend(["", "Kanał CB", f"<blockquote><b>{int(channel)}</b></blockquote>"])
    modulation = str(item.get("modulation") or "").strip()
    if modulation:
        parts.extend(["", "Modulacja", html_escape(modulation)])
    when = pretty_when(item.get("begin"))
    if when:
        parts.extend(["", "Czas nagrania", html_escape(when)])
    group = str(item.get("group") or "").strip()
    if group and group.lower() != "default":
        parts.extend(["", "Grupa", html_escape(group)])
    link = transmission_url(item)
    if link:
        parts.extend(["", f'<a href="{html_escape(link)}">Odsłuch i spektrogram</a>'])
    return parts


def question_telegram(item):
    ask = str(item.get("ask") or "").strip()
    answer = str(item.get("answer") or "").strip()
    error = str(item.get("answer_error") or "").strip()
    parts = ["<b>Pytanie CB</b>", ""]
    if error and not answer:
        parts.extend(["<b>Coś poszło nie tak.</b>", ""])
    else:
        if ask:
            parts.extend(["Pytanie", f"<blockquote><b>{html_escape(ask)}</b></blockquote>", ""])
        parts.extend(["Odpowiedź", f"<blockquote><b>{html_escape(answer)}</b></blockquote>", ""])
    parts.extend(telegram_details(item))
    return "\n".join(parts)[:4000]


def telegram_text(item):
    if item.get("question"):
        return question_telegram(item)
    raw = str(item.get("text") or "").strip()
    parts = ["<b>Nagranie CB</b>", ""]
    if raw.startswith("Nie rozpoznano"):
        parts.extend(
            [
                "<b>Nie rozpoznano mowy.</b>",
                "To szum albo dźwięk bez zrozumiałych słów.",
                "",
            ]
        )
    else:
        parts.extend(
            [
                "Rozpoznana mowa",
                f"<blockquote><b>{html_escape(raw)}</b></blockquote>",
                "",
            ]
        )
    parts.extend(telegram_details(item))
    return "\n".join(parts)[:4000]


def transmission_url(item):
    try:
        transmission_id = int(item.get("id"))
    except (TypeError, ValueError):
        return ""
    return f"http://192.168.0.203:8000/sdr/transmission/{transmission_id}/"


def send_telegram(item):
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("telegram pominięty, brak konfiguracji", flush=True)
        return False
    body = urllib.parse.urlencode(
        {
            "chat_id": chat,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
            "text": telegram_text(item),
        }
    ).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        if not payload.get("ok"):
            print(f"telegram odrzucony {item.get('id')}", flush=True)
            return False
    except Exception as exc:
        print(f"telegram błąd {item.get('id')}: {exc}", flush=True)
        return False
    print(f"telegram {item.get('id')}", flush=True)
    live(f"Wysłałem na Telegram, nagranie {item.get('id')}.")
    return True


def flush_telegram(items):
    changed = False
    for item in items:
        if item.get("telegram"):
            continue
        if not worth_sending(item):
            item["telegram"] = True
            changed = True
            continue
        if send_telegram(item):
            item["telegram"] = True
            changed = True
    if changed:
        save_items(items)
    return items


def mark_already_on_page():
    if os.path.exists(SEEN):
        return
    items = load_items()
    for item in items:
        item["telegram"] = True
    if items:
        save_items(items)
    with open(SEEN, "w", encoding="utf-8") as handle:
        handle.write("ok\n")


def recording_closed(rel_path, end_date):
    # sdr-hub dopisuje do tego samego pliku, dopóki mowa trwa. Czekamy, aż plik i koniec w bazie staną.
    path = MEDIA / rel_path
    if not path.exists() or path.stat().st_size < 8:
        return False
    if time.time() - path.stat().st_mtime < SETTLE_SECONDS:
        return False
    raw = str(end_date or "").strip()
    if not raw:
        return True
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
    age = (datetime.now(ZoneInfo("UTC")) - stamp).total_seconds()
    return age >= SETTLE_SECONDS


def finished(item):
    if str(item.get("text", "")).startswith("Błąd:"):
        return False
    if item.get("question") and not str(item.get("answer") or "").strip() and not str(item.get("answer_error") or "").strip():
        return False
    return True


def pending_rows(done_ok):
    con = sqlite3.connect(DB)
    rows = con.execute(
        """
        select t.id, t.data_file, t.begin_frequency, t.end_frequency,
               t.begin_date, t.end_date, ifnull(g.modulation,'FM'), ifnull(g.name,'')
        from sdr_transmission t
        left join sdr_group g on g.id = t.group_id
        where t.data_type = 'uint8'
        order by t.id
        """
    ).fetchall()
    con.close()
    return [row for row in rows if row[0] not in done_ok]


def main():
    WAVDIR.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    announced = False
    while not groq_enabled():
        if not announced:
            print("brak GROQ_API_KEY, transkrypcja czeka", flush=True)
            live("Brak klucza Groq. Strona działa, transkrypcja czeka.")
            announced = True
        time.sleep(15)
    mark_already_on_page()
    use_groq = True
    model = None
    if use_groq:
        print("transkrypcja: Groq " + GROQ_MODEL + ", odpowiedzi: " + ANSWER_MODEL, flush=True)
        live("Czekam na nagranie. Głos: Marek. Głośność gniazda: 80%.")
    else:
        print("transkrypcja: lokalny Whisper, brak klucza Groq", flush=True)
        from faster_whisper import WhisperModel

        model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    while True:
        items = flush_telegram(load_items())
        done_ok = {item["id"] for item in items if finished(item)}
        for row in pending_rows(done_ok):
            tid, rel, begin_f, end_f, begin, end, modulation, group = row
            if not recording_closed(rel, end):
                continue
            rate = int(end_f - begin_f)
            freq = (begin_f + end_f) / 2 / 1e6
            item = {
                "id": tid,
                "begin": begin,
                "frequency_mhz": f"{freq:.5f}",
                "cb_channel": cb_channel(rel, begin_f, end_f),
                "modulation": modulation,
                "group": group,
                "text": "",
            }
            channel = item["cb_channel"]
            live(
                f"Słucham nagrania {tid}"
                + (f", kanał {channel}" if channel else "")
                + f", {freq:.3f} MHz."
            )
            try:
                if modulation not in ("FM", "AM"):
                    item["text"] = "(pominięte, to nie jest nagranie mowy)"
                    live(f"Pomijam {tid}: to nie jest nagranie mowy.")
                else:
                    wav = decode(rel, f"{tid}.wav", modulation, rate)
                    if use_groq:
                        text = groq_transcribe(wav)
                    else:
                        segments, _info = model.transcribe(
                            str(wav),
                            language="pl",
                            vad_filter=True,
                            beam_size=5,
                            condition_on_previous_text=False,
                            initial_prompt=STT_PROMPT,
                        )
                        text = " ".join(seg.text.strip() for seg in segments).strip()
                    item["text"] = text or "(cisza albo brak zrozumiałej mowy)"
                    live(f"Usłyszałem: {item['text'][:500]}")
                    if own_voice(item["text"]):
                        item["own_voice"] = True
                        item["text"] = "(pominięte, własny głos)"
                        print(f"własny głos {tid}", flush=True)
                        live("To moje tak tak. Nie zapisuję tego i nie odpowiadam.")
                    asked = split_question(item["text"])
                    if asked is not None:
                        item["question"] = True
                        item["ask"] = asked
                        if not asked:
                            item["answer"] = ANSWER_EMPTY
                            live("Słowo pytanie było, ale treści pytania nie usłyszałem.")
                        else:
                            live(f"Pytanie: {asked}. Pytam model.")
                            try:
                                item["answer"] = groq_answer(asked)
                                live(f"Odpowiedź: {item['answer'][:500]}")
                                try:
                                    live("Mówię na gniazdo.")
                                    speak_answer(item["answer"])
                                    live("Skończyłem mówić. Gniazdo wyciszone.")
                                except Exception as exc:
                                    print(f"głos błąd {tid}: {exc}", flush=True)
                                    live(f"Głos się nie udał: {exc}")
                            except Exception as exc:
                                item["answer_error"] = ANSWER_FAIL
                                print(f"odpowiedź błąd {tid}: {exc}", flush=True)
                                live(f"Model nie odpowiedział: {exc}")
                    elif not item.get("own_voice"):
                        if worth_sending(item):
                            live("Jest mowa. Zapisuję i wysyłam na Telegram.")
                        else:
                            live("Szum albo obca fala. Na Telegram nie wysyłam.")
            except Exception as exc:
                item["text"] = "Błąd: " + str(exc).splitlines()[-1][:300]
                live(f"Błąd nagrania {tid}: {item['text']}")
            items = [old for old in items if old.get("id") != tid]
            items.append(item)
            save_items(items)
            flush_telegram(items)
            print(f"done {tid}: {item['text'][:160]}", flush=True)
            if item.get("question"):
                print(f"odpowiedź {tid}: {(item.get('answer') or item.get('answer_error') or '')[:160]}", flush=True)
        time.sleep(GROQ_POLL if use_groq else POLL)


if __name__ == "__main__":
    main()
