import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
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


def save_exchange(session_id, is_new, question, answer):
    """Сохранить вопрос и ответ одной парой. Новый разговор создаётся здесь же."""
    with db() as conn:
        if is_new:
            conn.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
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


# ---------- Общая логика для /chat и /chat/stream ----------

def prepare(request: ChatRequest):
    """Найти разговор и собрать список сообщений для модели.

    Возвращает (session_id, is_new, messages).
    Неизвестный session_id не принимаем — начинаем новый разговор.
    """
    history = []
    is_new = True
    session_id = str(uuid.uuid4())

    if request.session_id:
        with db() as conn:
            if session_exists(conn, request.session_id):
                session_id = request.session_id
                is_new = False
                history = load_history(conn, session_id)

    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + history
        + [{"role": "user", "content": request.message}]
    )
    return session_id, is_new, messages


def call_ollama(messages, stream):
    """Отправить запрос в Ollama. Ошибки превращаем в понятные HTTP-ответы."""
    payload = {"model": MODEL, "messages": messages, "stream": stream}
    try:
        # stream=True у requests: не читать ответ целиком, а отдавать по мере прихода
        response = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT, stream=stream)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Ollama недоступна. Проверь: systemctl status ollama")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail=f"Модель не ответила за {TIMEOUT} секунд")
    except requests.exceptions.HTTPError:
        raise HTTPException(status_code=502, detail=f"Ошибка от Ollama: {response.text}")
    return response


def event(data: dict) -> str:
    """Одна строка NDJSON: JSON-объект и перевод строки."""
    return json.dumps(data, ensure_ascii=False) + "\n"


# ---------- Эндпоинты ----------

# GET / — отдаём страницу чата
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX_FILE)


# GET /health — проверка, что приложение живо
@app.get("/health")
def health():
    return {"status": "ok"}


# POST /chat — ответ целиком одним JSON. Удобно для curl
@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    session_id, is_new, messages = prepare(request)
    response = call_ollama(messages, stream=False)
    answer = response.json()["message"]["content"]
    save_exchange(session_id, is_new, request.message, answer)
    return ChatResponse(answer=answer, session_id=session_id)


# POST /chat/stream — ответ по кусочкам, по мере генерации
@app.post("/chat/stream")
def chat_stream(request: ChatRequest):
    session_id, is_new, messages = prepare(request)

    # Подключаемся к Ollama ДО начала ответа клиенту:
    # если она лежит, клиент получит нормальную ошибку 503, как в /chat
    response = call_ollama(messages, stream=True)

    def generate():
        parts = []  # сюда собираем кусочки, чтобы потом сохранить ответ целиком
        try:
            yield event({"type": "session", "session_id": session_id})

            # Ollama присылает по строке JSON на каждый кусочек текста
            for line in response.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)

                if "error" in chunk:
                    yield event({"type": "error", "detail": chunk["error"]})
                    return

                text = chunk.get("message", {}).get("content", "")
                if text:
                    parts.append(text)
                    yield event({"type": "token", "content": text})

                if chunk.get("done"):
                    break

            # Ответ пришёл полностью — только теперь пишем в базу
            save_exchange(session_id, is_new, request.message, "".join(parts))
            yield event({"type": "done"})

        except requests.exceptions.RequestException:
            # Связь с Ollama оборвалась посреди ответа. В базу ничего не пишем
            yield event({"type": "error", "detail": "Связь с Ollama оборвалась во время ответа"})
        finally:
            response.close()

    return StreamingResponse(generate(), media_type="application/x-ndjson")


# GET /sessions/{session_id} — посмотреть историю разговора
@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    with db() as conn:
        if not session_exists(conn, session_id):
            raise HTTPException(status_code=404, detail="Разговор не найден")
        messages = load_history(conn, session_id)
    return {"session_id": session_id, "messages": messages}