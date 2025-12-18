#!/bin/bash
cd "$(dirname "$0")"

LOG_FILE="logs/modbus_server.log"

if [ ! -f "$LOG_FILE" ]; then
    echo "Лог файл не найден: $LOG_FILE"
    echo "Сначала запустите сервер"
    exit 1
fi

echo "Просмотр логов сервера (Ctrl+C для выхода)..."
echo "Лог файл: $LOG_FILE"
echo ""

tail -f "$LOG_FILE"
