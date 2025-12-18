FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Копирование зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY config/ ./config/
COPY src/ ./src/

# Создание директорий
RUN mkdir -p logs exports

# Порт для API и WebSocket
EXPOSE 8000 8765

# Команда запуска
CMD ["python", "-m", "src.main"]