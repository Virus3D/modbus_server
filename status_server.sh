#!/bin/bash
cd "$(dirname "$0")"

echo "Статус Modbus сервера:"
echo "======================"

# Проверяем процессы
PIDS=$(ps aux | grep "python.*src.main" | grep -v grep | awk '{print $2}')

if [ -n "$PIDS" ]; then
    echo "✅ Сервер запущен"
    echo "   Процессы: $PIDS"

    # Проверяем порты
    if ss -tlnp | grep -q ":8000"; then
        echo "✅ API порт 8000 открыт"
    else
        echo "❌ API порт 8000 не слушается"
    fi

    if ss -tlnp | grep -q ":8765"; then
        echo "✅ WebSocket порт 8765 открыт"
    else
        echo "❌ WebSocket порт 8765 не слушается"
    fi

    # Показываем последние логи
    echo ""
    echo "Последние 10 строк лога:"
    tail -10 logs/modbus_server.log 2>/dev/null || echo "Лог файл не найден"
else
    echo "❌ Сервер не запущен"

    # Проверяем systemd сервис
    if systemctl is-active --quiet modbus-server 2>/dev/null; then
        echo "⚠️  Сервис systemd modbus-server активен"
        sudo systemctl status modbus-server --no-pager
    fi
fi
