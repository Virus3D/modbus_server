#!/bin/bash
cd "$(dirname "$0")"

echo "Остановка Modbus сервера..."

# Ищем процессы Python, запущенные из этой директории
PIDS=$(ps aux | grep "python.*src.main" | grep -v grep | awk '{print $2}')

if [ -n "$PIDS" ]; then
    echo "Найдены процессы: $PIDS"
    kill -TERM $PIDS 2>/dev/null
    sleep 2

    # Проверяем, остались ли процессы
    REMAINING=$(ps aux | grep "python.*src.main" | grep -v grep | awk '{print $2}')
    if [ -n "$REMAINING" ]; then
        echo "Принудительное завершение процессов..."
        kill -9 $REMAINING 2>/dev/null
    fi

    echo "Сервер остановлен"
else
    echo "Активных процессов сервера не найдено"
fi

# Также ищем процессы uvicorn если они есть
UVICORN_PIDS=$(ps aux | grep "uvicorn.*main:app" | grep -v grep | awk '{print $2}')
if [ -n "$UVICORN_PIDS" ]; then
    kill -TERM $UVICORN_PIDS 2>/dev/null
fi
