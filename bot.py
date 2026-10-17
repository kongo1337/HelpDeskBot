import requests

# Адрес локальной Ollama. Именно http, не https — грабли от 16.10
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:9b"

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

    # 4. Собираем тело запроса — тот же JSON, что ты отправлял через curl
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": question},
        ],
        "stream": False,
    }

    # 3. Отправляем POST-запрос и ждём ответ
    response = requests.post(OLLAMA_URL, json=payload, timeout=300)
    response.raise_for_status()

    # 4. Разбираем JSON и достаём текст ответа
    data = response.json()
    answer = data["message"]["content"]

    print("Ответ:", answer)