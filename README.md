# Modbus Server

Сервер для опроса Modbus устройств с поддержкой TCP, RTU over TCP и последовательного RTU.

## Возможности

- Поддержка трех типов подключений:
  - Modbus TCP
  - Modbus RTU over TCP (через gateway)
  - Modbus RTU Serial (последовательный порт)
- Параллельный опрос разных портов
- Последовательный опрос устройств на одном порту
- Сохранение данных в базу данных (PostgreSQL/TimescaleDB)
- WebSocket для данных в реальном времени
- REST API для управления и мониторинга
- Конфигурация через YAML файл
- Мониторинг и статистика
- Экспорт данных в CSV

## Требования

- Python 3.11+
- PostgreSQL 15+ (рекомендуется TimescaleDB)
- Docker и Docker Compose (для контейнерного развертывания)

## Установка

### 1. Клонирование репозитория
```bash
git clone <repository-url>
cd modbus_server