#!/bin/bash
set -e

cd "$(dirname "$0")"

# Проверяем виртуальное окружение
if [ ! -d "venv" ]; then
    echo "Ошибка: виртуальное окружение не найдено"
    echo "Сначала запустите: python3 -m venv venv"
    exit 1
fi

# Активируем виртуальное окружение
source venv/bin/activate

# Проверяем зависимости
if ! python -c "import pymodbus" 2>/dev/null; then
    echo "Установка зависимостей..."
    pip install -r requirements.txt 2>/dev/null || {
        echo "Не удалось установить зависимости"
        exit 1
    }
fi

# Запускаем сервер
echo "Запуск Modbus сервера..."
echo "Нажмите Ctrl+C для остановки"
echo ""
echo "Логи будут записываться в logs/modbus_server.log"
echo ""

# Создаем директорию для логов если ее нет
mkdir -p logs

# Запускаем сервер
exec python -m src.main
