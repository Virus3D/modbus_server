#!/bin/bash
cd "$(dirname "$0")"

echo "Перезапуск Modbus сервера..."

if [ -f "stop_server.sh" ]; then
    ./stop_server.sh
    sleep 2
fi

if [ -f "start_server.sh" ]; then
    ./start_server.sh
else
    echo "Ошибка: start_server.sh не найден"
    exit 1
fi
