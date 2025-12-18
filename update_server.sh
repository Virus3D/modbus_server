#!/bin/bash
cd "$(dirname "$0")"
echo "Остановка сервера..."
./stop_server.sh
echo "Обновление кода..."
git pull origin main
echo "Установка зависимостей..."
source venv/bin/activate
pip install -r requirements.txt --upgrade
echo "Запуск сервера..."
./start_server.sh
