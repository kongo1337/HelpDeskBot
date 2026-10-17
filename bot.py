import sys
from pathlib import Path

import requests

# Адрес локальной Ollama. Именно http, не https — грабли от 16.10
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:9b"
TIMEOUT = 300  # сколько секунд ждать ответа модели

# Путь к промпту считаем от папки, где лежит сам bot.py,
# чтобы скрипт работал, из какой бы папки его ни запустили
PROMPT_FILE = Path(__file__).parent / "prompts" / "system.md"

# Системный промпт читаем из файла при каждом старте
try:
    SYSTEM_PROMPT = PROMPT_FILE.read_text(encoding="utf-8")
except FileNotFoundError:
    print(f"Ошибка: не найден файл с системным промптом: {PROMPT_FILE}")
    sys.exit(1)

# История начинается с системного сообщения. Оно отправляется в каждом запросе
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
]

print(f"Бот на модели {MODEL}. Для выхода напиши exit или нажми Ctrl+C.")

try:
    while True:
        # 1. Спрашиваем пользователя и убираем пробелы по краям
        question = input("\nВопрос: ").strip()

        # 2. Команда выхода — прерываем цикл
        if question.lower() == "exit":
            break

        # 3. Пустую строку не отправляем, просто спрашиваем заново
        if not question:
            continue

        # 4. Добавляем вопрос в историю
        messages.append({"role": "user", "content": question})

        # 5. Отправляем всю историю вместе с системным промптом
        payload = {
            "model": MODEL,
            "messages": messages,
            "stream": False,
        }

        # 6. Запрос может упасть — ловим известные ошибки
        try:
            response = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            print(f"Ошибка: не могу подключиться к Ollama ({OLLAMA_URL}).")
            print("Проверь, что она запущена: systemctl status ollama")
            messages.pop()  # убираем вопрос без ответа из истории
            continue
        except requests.exceptions.Timeout:
            print(f"Ошибка: модель не ответила за {TIMEOUT} секунд.")
            messages.pop()
            continue
        except requests.exceptions.HTTPError:
            print(f"Ошибка от Ollama, код {response.status_code}: {response.text}")
            messages.pop()
            continue

        # 7. Достаём ответ и тоже кладём его в историю
        data = response.json()
        answer = data["message"]["content"]
        messages.append({"role": "assistant", "content": answer})

        print("Ответ:", answer)

        # 8. Статистика. Ollama отдаёт время в наносекундах (1 с = 1_000_000_000 нс)
        eval_count = data.get("eval_count", 0)           # сколько токенов сгенерировано
        eval_duration = data.get("eval_duration", 0)     # сколько на это ушло
        prompt_count = data.get("prompt_eval_count", 0)  # сколько токенов прочитано на входе
        load_duration = data.get("load_duration", 0)     # загрузка модели в память
        total_duration = data.get("total_duration", 0)   # весь запрос целиком

        if eval_duration > 0:
            speed = eval_count / (eval_duration / 1_000_000_000)
            print(
                f"\n[{eval_count} ток. за {eval_duration / 1e9:.1f} с = {speed:.1f} ток/с | "
                f"на входе {prompt_count} ток. | загрузка {load_duration / 1e9:.1f} с | "
                f"всего {total_duration / 1e9:.1f} с]"
            )

except (KeyboardInterrupt, EOFError):
    # Ctrl+C или Ctrl+D — выходим без трейсбэка
    print()

print("Пока!")