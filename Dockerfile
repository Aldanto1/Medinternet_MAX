FROM python:3.12-slim

WORKDIR /app

# Сначала зависимости — чтобы кэш слоёв не сбрасывался при правках кода
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Затем весь код (env/ и venv/ исключены через .dockerignore)
COPY . .

# Порт задаёт платформа через переменную окружения PORT
CMD ["python", "max_bot.py"]
