import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Настройки — те же, что в bot.py
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:9b"
TIMEOUT = 300

# Системный промпт из того же файла, что и у bot.py
PROMPT_FILE = Path(__file__).parent / "prompts" / "system.md"
SYSTEM_PROMPT = PROMPT_FILE.read_text(encoding="utf-8")

# Все разговоры: session_id -> список сообщений (без системного промпта).
# Живёт в оперативной памяти, поэтому пропадает при перезапуске сервера
sessions: dict[str, list[dict]] = {}

# Само приложение. title виден на странице документации /docs
app = FastAPI(title="Helpdesk bot")


# Модели данных: какой JSON приходит в запросе и какой уходит в ответе
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # необязательное поле: нет — начнём новый разговор


class ChatResponse(BaseModel):
    answer: str
    session_id: str  # всегда возвращаем, чтобы клиент знал, куда писать дальше


# GET /health — проверка, что приложение живо
@app.get("/health")
def health():
    return {"status": "ok"}


# POST /chat — вопрос пользователя -> ответ модели с учётом истории разговора
@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # 1. Находим разговор или начинаем новый
    session_id = request.session_id or str(uuid.uuid4())
    history = sessions.get(session_id, [])

    # 2. Новый вопрос пока НЕ кладём в историю: сначала дождёмся ответа
    user_message = {"role": "user", "content": request.message}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [user_message]

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
    }

    # 3. Те же ошибки, что в bot.py v5, но вместо print — HTTP-ответ с кодом ошибки
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

    # 4. Ответ получен — сохраняем вопрос и ответ в историю разговора
    sessions[session_id] = history + [
        user_message,
        {"role": "assistant", "content": answer},
    ]

    return ChatResponse(answer=answer, session_id=session_id)


# GET /sessions/{session_id} — посмотреть историю разговора
@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Разговор не найден")
    return {"session_id": session_id, "messages": sessions[session_id]}