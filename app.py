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

# Само приложение. title виден на странице документации /docs
app = FastAPI(title="Helpdesk bot")


# Модели данных: какой JSON приходит в запросе и какой уходит в ответе
class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str


# GET /health — проверка, что приложение живо
@app.get("/health")
def health():
    return {"status": "ok"}


# POST /chat — вопрос пользователя -> ответ модели
@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Пока без памяти: каждый запрос — новый разговор
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": request.message},
    ]

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
    }

    # Те же ошибки, что в bot.py v5, но вместо print — HTTP-ответ с кодом ошибки
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
    return ChatResponse(answer=answer)