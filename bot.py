import requests

# Адрес локальной Ollama. Именно http, не https — грабли от 16.10
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:9b"

# Системный промпт — инструкция для модели: кто она и как отвечать.
# Тройные кавычки позволяют писать строку в несколько строк
SYSTEM_PROMPT = """Ты — помощник службы технической поддержки (helpdesk).
Помогаешь сотрудникам решать типовые проблемы с компьютером, принтером, сетью, почтой и учётными записями.
 
Правила:
- Отвечай по-русски, коротко и по делу, без смайликов.
- Давай пошаговые инструкции нумерованным списком.
- Если для ответа не хватает данных, задай один уточняющий вопрос.
- Если проблему не решить самому (нужны права администратора, сломано железо), посоветуй создать заявку в поддержку.
- На вопросы не про IT вежливо отвечай, что помогаешь только с техническими проблемами."""
 
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
 
except (KeyboardInterrupt, EOFError):
    # Ctrl+C или Ctrl+D — выходим без трейсбэка
    print()
 
print("Пока!")