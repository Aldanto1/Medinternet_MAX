FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# API + внутрипроцессный воркер рассылки в одном контейнере.
CMD ["python", "-m", "app.main"]
