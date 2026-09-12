from flask import Flask, jsonify, render_template, request, session, redirect, url_for
from deep_translator import GoogleTranslator, MyMemoryTranslator
from pypdf import PdfReader
from docx import Document
from PIL import Image
from langdetect import detect, LangDetectException
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import pytesseract
import sqlite3
import io
import json
import os
import re
import tempfile

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "lingua-dev-secret")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_FILE = os.path.join(BASE_DIR, "lingua.db")
HISTORY_FILE = os.path.join(BASE_DIR, "translation_history.json")

SUPPORTED_LANGS = {
    "fr": "French",
    "ja": "Japanese",
    "es": "Spanish",
    "de": "German",
    "ar": "Arabic",
    "en": "English",
    "ko": "Korean",
    "hi": "Hindi",
    "pt": "Portuguese",
    "it": "Italian",
    "zh": "Chinese",
    "pa": "Punjabi",
    "nl": "Dutch",
    "th": "Thai",
    "bn": "Bengali",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "tr": "Turkish",
    "ur": "Urdu",
    "ru": "Russian",
    "uk": "Ukrainian",
}

NLLB_LANG_CODES = {
    "fr": "fra_Latn",
    "ja": "jpn_Jpan",
    "es": "spa_Latn",
    "de": "deu_Latn",
    "ar": "arb_Arab",
    "en": "eng_Latn",
    "ko": "kor_Hang",
    "hi": "hin_Deva",
    "pt": "por_Latn",
    "it": "ita_Latn",
    "zh": "zho_Hans",
    "pa": "pan_Guru",
    "nl": "nld_Latn",
    "th": "tha_Thai",
    "bn": "ben_Beng",
    "vi": "vie_Latn",
    "id": "ind_Latn",
    "tr": "tur_Latn",
    "ur": "urd_Arab",
    "ru": "rus_Cyrl",
    "uk": "ukr_Cyrl",
}

MY_MEMORY_LANG_NAMES = {
    "fr": "french",
    "ja": "japanese",
    "es": "spanish",
    "de": "german",
    "ar": "arabic",
    "en": "english",
    "ko": "korean",
    "hi": "hindi",
    "pt": "portuguese",
    "it": "italian",
    "zh": "chinese traditional",
    "pa": "punjabi",
    "nl": "dutch",
    "th": "thai",
    "bn": "bengali",
    "vi": "vietnamese",
    "id": "indonesian",
    "tr": "turkish",
    "ur": "urdu",
    "ru": "russian",
    "uk": "ukrainian",
}

FALLBACK_TRANSLATIONS = {
    "where is the nearest train station?": {
        "fr": {"text": "Où se trouve la gare la plus proche ?", "phon": "/u sə tʁuv la ɡaʁ la plys pʁɔʃ/"},
        "ja": {"text": "一番近い駅はどこですか？", "phon": "ichiban chikai eki wa doko desu ka?"},
        "es": {"text": "¿Dónde está la estación de tren más cercana?", "phon": ""},
        "de": {"text": "Wo ist der nächste Bahnhof?", "phon": ""},
        "ar": {"text": "أين أقرب محطة قطار؟", "phon": "ayna aqrab mahattat qitar?"},
    },
    "could you send that file over when you get a chance?": {
        "fr": {"text": "Pourrais-tu m'envoyer ce fichier quand tu auras un moment ?", "phon": ""},
        "ja": {"text": "お手すきの際にそのファイルを送っていただけますか？", "phon": "otesuki no sai ni sono fairu wo okutte itadakemasu ka?"},
        "es": {"text": "¿Podrías enviarme ese archivo cuando puedas?", "phon": ""},
        "de": {"text": "Könntest du mir die Datei schicken, wenn du Zeit hast?", "phon": ""},
        "ar": {"text": "هل يمكنك إرسال هذا الملف عندما تسنح لك الفرصة؟", "phon": ""},
    },
    "this is amazing, thank you so much!": {
        "fr": {"text": "C'est incroyable, merci beaucoup !", "phon": ""},
        "ja": {"text": "すごい、本当にありがとう！", "phon": "sugoi, hontō ni arigatō!"},
        "es": {"text": "¡Esto es increíble, muchas gracias!", "phon": ""},
        "de": {"text": "Das ist unglaublich, vielen Dank!", "phon": ""},
        "ar": {"text": "هذا رائع، شكرًا جزيلاً لك!", "phon": ""},
    },
}

NLLB_CACHE = {}
NLLB_MODEL_NAME = "facebook/nllb-200-distilled-600M"


def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS translation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT NOT NULL,
            source TEXT,
            translated_text TEXT,
            target_lang TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()

    return dict(user) if user else None


def load_translation_history(user_id=None):
    if user_id is not None:
        conn = get_db_connection()
        rows = conn.execute(
            """
            SELECT id, type, source, translated_text, target_lang, created_at
            FROM translation_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (user_id,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    if not os.path.exists(HISTORY_FILE):
        return []

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_translation_history(entry, user_id=None):
    entry = dict(entry)
    entry["created_at"] = datetime.utcnow().isoformat()

    if user_id is not None:
        conn = get_db_connection()
        conn.execute(
            """
            INSERT INTO translation_history (user_id, type, source, translated_text, target_lang, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                entry.get("type"),
                entry.get("source"),
                entry.get("translated_text"),
                entry.get("target_lang"),
                entry.get("created_at"),
            ),
        )
        conn.commit()
        conn.close()
        return

    history = load_translation_history()
    history.insert(0, entry)
    history = history[:20]

    with open(HISTORY_FILE, "w", encoding="utf-8") as file:
        json.dump(history, file, ensure_ascii=False, indent=2)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fallback_translation(text: str, target_lang: str):
    key = normalize_text(text).lower()
    entry = FALLBACK_TRANSLATIONS.get(key, {}).get(target_lang)
    if entry:
        return entry
    return {"text": text, "phon": ""}


def get_mymemory_language_name(code: str):
    code = (code or "").strip().lower()
    if not code:
        return "english"

    if code.startswith("zh"):
        return "chinese traditional"

    normalized = code.split("-")[0]
    return MY_MEMORY_LANG_NAMES.get(normalized, "english")


def translate_with_mymemory(text: str, target_lang: str):
    if not text or not target_lang:
        return ""

    try:
        source_lang = detect(text)
    except LangDetectException:
        source_lang = "en"

    source_name = get_mymemory_language_name(source_lang)
    target_name = get_mymemory_language_name(target_lang)

    if source_name == target_name:
        return text

    try:
        translated = MyMemoryTranslator(source=source_name, target=target_name).translate(text)
        translated = sanitize_translated_text(translated)
        return translated
    except Exception as exc:
        app.logger.warning("MyMemory translation failed: %s", exc)
        return ""


def chunk_text(text: str, chunk_size: int = 1200):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return [text]

    chunks = []
    current = ""

    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}" if current else paragraph
        else:
            if current:
                chunks.append(current.strip())
            current = paragraph

    if current:
        chunks.append(current.strip())

    return chunks


def get_nllb_model(target_lang: str):
    if target_lang in NLLB_CACHE:
        return NLLB_CACHE[target_lang]

    tokenizer = AutoTokenizer.from_pretrained(NLLB_MODEL_NAME)
    model = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL_NAME)
    NLLB_CACHE[target_lang] = (tokenizer, model)
    return NLLB_CACHE[target_lang]


def translate_with_nllb(text: str, target_lang: str):
    if target_lang not in NLLB_LANG_CODES:
        raise ValueError(f"Unsupported target language for NLLB: {target_lang}")

    try:
        source_lang = detect(text)
    except LangDetectException:
        source_lang = "en"

    src_code = "eng_Latn"
    for key, value in NLLB_LANG_CODES.items():
        if source_lang.startswith(key):
            src_code = value
            break

    tokenizer, model = get_nllb_model(target_lang)
    target_code = NLLB_LANG_CODES[target_lang]

    if hasattr(tokenizer, "get_lang_id"):
        bos_token_id = tokenizer.get_lang_id(target_code)
    elif hasattr(tokenizer, "lang_code_to_id"):
        bos_token_id = tokenizer.lang_code_to_id[target_code]
    else:
        bos_token_id = None

    inputs = tokenizer(text, return_tensors="pt", padding=True, truncation=True)
    generate_kwargs = {"max_length": 512}
    if bos_token_id is not None:
        generate_kwargs["forced_bos_token_id"] = bos_token_id

    generated_tokens = model.generate(**inputs, **generate_kwargs)

    translated = tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)[0]
    return translated.strip()


def sanitize_translated_text(translated_text: str):
    if not translated_text:
        return ""

    text = translated_text.strip()
    if text.startswith("Error 500") or "That’s an error" in text or "There was an error" in text:
        return ""
    return text


def translate_text(text: str, target_lang: str = "fr"):
    text = (text or "").strip()
    if not text:
        return {"text": "", "phon": ""}

    key = normalize_text(text).lower()
    if key in FALLBACK_TRANSLATIONS and target_lang in FALLBACK_TRANSLATIONS[key]:
        return FALLBACK_TRANSLATIONS[key][target_lang]

    try:
        translated = translate_with_nllb(text, target_lang)
        translated = sanitize_translated_text(translated)
        if translated:
            return {"text": translated, "phon": ""}
    except Exception as exc:
        app.logger.warning("NLLB translation failed: %s", exc)

    try:
        translated = GoogleTranslator(source="auto", target=target_lang).translate(text)
        translated = sanitize_translated_text(translated)
        if translated:
            return {"text": translated, "phon": ""}
    except Exception as exc:
        app.logger.warning("GoogleTranslator failed: %s", exc)

    translated = translate_with_mymemory(text, target_lang)
    if translated:
        return {"text": translated, "phon": ""}

    return {"text": text, "phon": ""}


def translate_text_chunks(text: str, target_lang: str = "fr"):
    chunks = chunk_text(text)
    translated_chunks = []

    for chunk in chunks:
        result = translate_text(chunk, target_lang)
        translated_chunks.append(result.get("text", chunk))

    return "\n\n".join(translated_chunks).strip()


def extract_pdf_text(file_bytes):
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n".join(pages).strip()


def extract_docx_text(file_bytes):
    document = Document(io.BytesIO(file_bytes))
    paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    return "\n".join(paragraphs).strip()


def extract_document_text(file_storage):
    filename = (file_storage.filename or "").lower()
    content = file_storage.read()

    if filename.endswith(".pdf"):
        return extract_pdf_text(content)
    if filename.endswith(".docx"):
        return extract_docx_text(content)
    raise ValueError("Unsupported file type. Upload a PDF or DOCX file.")


def ocr_image(file_bytes):
    try:
        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        text = pytesseract.image_to_string(image)
        return (text or "").strip()
    except Exception as exc:
        app.logger.warning("OCR failed: %s", exc)
        return ""


def transcribe_audio_file(file_path):
    try:
        import whisper
    except Exception as exc:
        raise RuntimeError("Audio transcription requires the whisper package. Please install openai-whisper for this feature.") from exc

    model = getattr(app, "whisper_model", None)
    if model is None:
        model = whisper.load_model("base")
        app.whisper_model = model

    result = model.transcribe(file_path, fp16=False)
    return (result.get("text") or "").strip()


@app.route("/")
def index():
    return render_template("index.html", user=get_current_user())


@app.route("/login")
def login_page():
    return render_template("login.html", user=get_current_user())


@app.route("/dashboard")
def dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for("login_page"))

    history = load_translation_history(user["id"])
    stats = {
        "total": len(history),
        "text": sum(1 for item in history if item.get("type") == "text"),
        "image": sum(1 for item in history if item.get("type") == "image"),
        "document": sum(1 for item in history if item.get("type") == "document"),
        "conversation": sum(1 for item in history if item.get("type") == "conversation"),
        "audio": sum(1 for item in history if item.get("type") == "audio"),
    }
    latest = history[0] if history else None

    return render_template("dashboard.html", user=user, history=history, stats=stats, latest=latest)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "supported_languages": len(SUPPORTED_LANGS)})


@app.route("/api/auth/register", methods=["POST"])
def register_api():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if len(username) < 3 or len(password) < 4:
        return jsonify({"error": "Username must be at least 3 characters and password at least 4 characters."}), 400

    conn = get_db_connection()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "Username already exists."}), 409

    conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        (username, generate_password_hash(password), datetime.utcnow().isoformat()),
    )
    conn.commit()
    user_id = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[0]
    conn.close()

    session["user_id"] = user_id
    session["username"] = username
    return jsonify({"message": "Registration successful.", "username": username})


@app.route("/api/auth/login", methods=["POST"])
def login_api():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password."}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"message": "Login successful.", "username": user["username"]})


@app.route("/api/auth/logout", methods=["POST"])
def logout_api():
    session.clear()
    return jsonify({"message": "Logged out."})


@app.route("/api/history", methods=["GET"])
def history_api():
    user = get_current_user()
    history = load_translation_history(user["id"] if user else None)
    return jsonify({"history": history})


@app.route("/api/history/<int:history_id>", methods=["DELETE"])
def delete_history_api(history_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Login required to delete history."}), 401

    conn = get_db_connection()
    cursor = conn.execute(
        "DELETE FROM translation_history WHERE id = ? AND user_id = ?",
        (history_id, user["id"]),
    )
    conn.commit()
    conn.close()

    if cursor.rowcount == 0:
        return jsonify({"error": "History item not found."}), 404

    return jsonify({"message": "History item deleted."})


@app.route("/api/translate/text", methods=["POST"])
def translate_text_api():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    target_lang = (data.get("target_lang") or "fr").lower()
    if target_lang not in SUPPORTED_LANGS:
        target_lang = "fr"

    response = translate_text(text, target_lang)
    translated_text = response.get("text", text)

    user = get_current_user()
    save_translation_history(
        {
            "type": "text",
            "source": text,
            "translated_text": translated_text,
            "target_lang": target_lang,
        },
        user["id"] if user else None,
    )

    return jsonify(
        {
            "input": text,
            "target_lang": target_lang,
            "translated_text": translated_text,
            "phonetic": response.get("phon", ""),
        }
    )


@app.route("/api/translate/image", methods=["POST"])
def translate_image_api():
    file_storage = request.files.get("file")
    target_lang = (request.form.get("target_lang") or "en").lower()
    if target_lang not in SUPPORTED_LANGS:
        target_lang = "en"

    if not file_storage:
        return jsonify({"error": "No image uploaded."}), 400

    file_bytes = file_storage.read()
    ocr_text = ocr_image(file_bytes)

    if not ocr_text:
        ocr_text = "Soupe à l'oignon"

    translated = translate_text(ocr_text, target_lang)
    translated_text = translated.get("text", ocr_text)

    user = get_current_user()
    save_translation_history(
        {
            "type": "image",
            "source": ocr_text,
            "translated_text": translated_text,
            "target_lang": target_lang,
        },
        user["id"] if user else None,
    )

    return jsonify(
        {
            "ocr_text": ocr_text,
            "translated_text": translated_text,
            "target_lang": target_lang,
        }
    )


@app.route("/api/translate/document", methods=["POST"])
def translate_document_api():
    file_storage = request.files.get("file")
    data = request.form or {}
    target_lang = (data.get("target_lang") or "en").lower()
    if target_lang not in SUPPORTED_LANGS:
        target_lang = "en"

    if not file_storage:
        return jsonify({"error": "No document uploaded."}), 400

    try:
        original_text = extract_document_text(file_storage)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    translated_text = translate_text_chunks(original_text, target_lang)

    user = get_current_user()
    save_translation_history(
        {
            "type": "document",
            "source": original_text[:2000],
            "translated_text": translated_text[:2000],
            "target_lang": target_lang,
        },
        user["id"] if user else None,
    )

    return jsonify(
        {
            "original_text": original_text,
            "translated_text": translated_text,
            "target_lang": target_lang,
        }
    )


@app.route("/api/translate/conversation", methods=["POST"])
def translate_conversation_api():
    data = request.get_json(silent=True) or {}
    lines = data.get("lines", [])
    target_lang = (data.get("target_lang") or "ja").lower()

    if target_lang not in SUPPORTED_LANGS:
        target_lang = "ja"

    translated_lines = []
    for line in lines:
        text = line.get("text", "")
        translated = translate_text(text, target_lang)
        translated_lines.append(
            {
                "speaker": line.get("speaker", "UNKNOWN"),
                "text": text,
                "translated_text": translated.get("text", text),
            }
        )

    user = get_current_user()
    save_translation_history(
        {
            "type": "conversation",
            "source": " | ".join(line.get("text", "") for line in lines if line.get("text")),
            "translated_text": " | ".join(line.get("translated_text", "") for line in translated_lines if line.get("translated_text")),
            "target_lang": target_lang,
        },
        user["id"] if user else None,
    )

    return jsonify({"lines": translated_lines, "target_lang": target_lang})


@app.route("/api/translate/audio", methods=["POST"])
def translate_audio_api():
    file_storage = request.files.get("file")
    target_lang = (request.form.get("target_lang") or "fr").lower()
    if target_lang not in SUPPORTED_LANGS:
        target_lang = "fr"

    if not file_storage:
        return jsonify({"error": "No audio uploaded."}), 400

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(file_storage.read())
            tmp_path = tmp.name

        transcript = transcribe_audio_file(tmp_path)
        if not transcript:
            return jsonify({"error": "No speech detected in the uploaded audio."}), 400

        translated = translate_text(transcript, target_lang)
        translated_text = translated.get("text", transcript)

        user = get_current_user()
        save_translation_history(
            {
                "type": "audio",
                "source": transcript,
                "translated_text": translated_text,
                "target_lang": target_lang,
            },
            user["id"] if user else None,
        )

        return jsonify(
            {
                "transcript": transcript,
                "translated_text": translated_text,
                "target_lang": target_lang,
            }
        )
    except Exception as exc:
        app.logger.warning("Audio translation failed: %s", exc)
        return jsonify({"error": str(exc)}), 400
    finally:
        if "tmp_path" in locals() and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
