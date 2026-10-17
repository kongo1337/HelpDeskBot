import requests

# Адрес локальной Ollama. Именно http, не https — грабли от 16.10
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:9b"

# История диалога. Создаётся ОДИН раз, до цикла, и растёт с каждым вопросом
messages = []

print(f"Бот на модели {MODEL}. Для выхода напиши exit.")

while True:

    # 1. Спрашиваем пользователя и убираем пробелы по краям
    question = input("\nВопрос: ").strip()

    # 2. Команда выхода - прерываем цикл
    if question.lower() == "exit":
        print("Пока!")
        break
    
    # 3. Пустую строку не отправляем, просто спрашиваем заново
    if not question:
        continue

    # 4. Добавляем вопрос в историю
    messages.append({"role": "user", "content": question})

    # 5. Собираем тело запроса — тот же JSON, что ты отправлял через curl
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
    }

    # 6. Отправляем POST-запрос и ждём ответ
    response = requests.post(OLLAMA_URL, json=payload, timeout=300)
    response.raise_for_status()

    # 7. Достаём ответ и тоже кладём его в историю
    data = response.json()
    answer = data["message"]["content"]
    messages.append({"role": "assistant", "content": answer})

    print("Ответ:", answer)