import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Настройки — те же, что в bot.py
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:9b"
TIMEOUT = 300

BASE_DIR = Path(__file__).parent

# Системный промпт из того же файла, что и у bot.py
PROMPT_FILE = BASE_DIR / "prompts" / "system.md"
SYSTEM_PROMPT = PROMPT_FILE.read_text(encoding="utf-8")

# Страница чата
INDEX_FILE = BASE_DIR / "static" / "index.html"

# Файл базы данных. Создастся сам при первом запуске
DB_FILE = BASE_DIR / "helpdesk.db"


# ---------- База данных ----------

@contextmanager
def db():
    """Открыть базу, отдать соединение, в конце сохранить изменения и закрыть."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row             # строки как словари: row["role"]
    conn.execute("PRAGMA foreign_keys = ON")   # в SQLite проверку связей надо включать явно
    try:
        yield conn
        conn.commit()      # всё прошло хорошо — сохраняем
    except Exception:
        conn.rollback()    # что-то упало — отменяем незаконченные изменения
        raise
    finally:
        conn.close()       # закрываем в любом случае


def init_db():
    """Создать таблицы, если их ещё нет."""
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def session_exists(conn, session_id):
    row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row is not None


def load_history(conn, session_id):
    """Все сообщения разговора по порядку, в формате для Ollama."""
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in rows]


def save_exchange(conn, session_id, question, answer):
    """Сохранить вопрос и ответ одной парой."""
    conn.executemany(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        [
            (session_id, "user", question),
            (session_id, "assistant", answer),
        ],
    )


init_db()

# Само приложение. title виден на странице документации /docs
app = FastAPI(title="Helpdesk bot")


# ---------- Модели данных ----------

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # необязательное поле: нет — начнём новый разговор


class ChatResponse(BaseModel):
    answer: str
    session_id: str  # всегда возвращаем, чтобы клиент знал, куда писать дальше


# ---------- Эндпоинты ----------

# GET / — отдаём страницу чата
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX_FILE)


# GET /health — проверка, что приложение живо
@app.get("/health")
def health():
    return {"status": "ok"}


# POST /chat — вопрос пользователя -> ответ модели с учётом истории разговора
@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # 1. Ищем разговор в базе. Неизвестный session_id не принимаем — начнём новый
    session_id = None
    history = []
    if request.session_id:
        with db() as conn:
            if session_exists(conn, request.session_id):
                session_id = request.session_id
                history = load_history(conn, session_id)

    # 2. Собираем запрос: системный промпт + история + новый вопрос
    user_message = {"role": "user", "content": request.message}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [user_message]

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
    }

    # 3. Идём в Ollama. База в это время закрыта: ответ может идти минуту
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Ollama недоступна. Проверь: systemctl status ollama")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail=f"Модель не ответила за {TIMEOUT} секунд")
    except requests.exceptions.HTTPError:
        raise HTTPException(status_code=502, detail=f"Ошибка от Ollama: {response.text}")

    answer = response.json()["message"]["content"]

    # 4. Ответ получен — только теперь пишем в базу
    with db() as conn:
        if session_id is None:
            session_id = str(uuid.uuid4())
            conn.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
        save_exchange(conn, session_id, request.message, answer)

    return ChatResponse(answer=answer, session_id=session_id)


# GET /sessions/{session_id} — посмотреть историю разговора
@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    with db() as conn:
        if not session_exists(conn, session_id):
            raise HTTPException(status_code=404, detail="Разговор не найден")
        messages = load_history(conn, session_id)
    return {"session_id": session_id, "messages": messages}