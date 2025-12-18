#!/bin/bash

# setup.sh - Скрипт установки Modbus сервера для Linux

set -e  # Завершить при любой ошибке

echo "========================================="
echo " Установка Modbus сервера "
echo "========================================="

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функции для вывода
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Обработка аргументов
MINIMAL_MODE=false
for arg in "$@"
do
    case $arg in
        --minimal)
        MINIMAL_MODE=true
        shift
        ;;
    esac
done

# Проверка прав администратора
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_warn "Скрипт запущен без прав администратора"
        print_warn "Некоторые операции могут потребовать sudo"
    fi
}

# Проверка ОС
check_os() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
        print_info "Обнаружена ОС: $NAME $VERSION"
    else
        print_error "Не удалось определить ОС"
        exit 1
    fi
}

# Проверка Python
check_python() {
    print_info "Проверка установки Python..."

    if command -v python3.11 &> /dev/null; then
        PYTHON_VERSION=$(python3.11 --version | cut -d' ' -f2)
        print_info "Python $PYTHON_VERSION найден"
        PYTHON_CMD="python3.11"
    elif command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
        MAJOR_VERSION=$(echo $PYTHON_VERSION | cut -d'.' -f1)
        MINOR_VERSION=$(echo $PYTHON_VERSION | cut -d'.' -f2)

        if [ "$MAJOR_VERSION" -eq 3 ] && [ "$MINOR_VERSION" -ge 9 ]; then
            print_info "Python $PYTHON_VERSION найден (минимальная версия 3.9)"
            PYTHON_CMD="python3"
        else
            print_error "Требуется Python 3.9 или выше"
            exit 1
        fi
    else
        print_error "Python не найден"
        exit 1
    fi
}

# Установка системных зависимостей
install_system_deps() {
    print_info "Установка системных зависимостей..."

    case $OS in
        ubuntu|debian|linuxmint)
            sudo apt-get update
            sudo apt-get install -y \
                python3-venv \
                python3-dev \
                libpq-dev \
                build-essential \
                sqlite3 \
                libsqlite3-dev \
                pkg-config \
                libssl-dev \
                libffi-dev \
                gcc \
                g++ \
                make \
                curl \
                wget \
                git
            ;;
        fedora|centos|rhel)
            sudo dnf install -y \
                python3-devel \
                postgresql-devel \
                gcc \
                gcc-c++ \
                make \
                openssl-devel \
                libffi-devel \
                sqlite \
                sqlite-devel \
                curl \
                wget \
                git
            ;;
        arch|manjaro)
            sudo pacman -Sy --noconfirm \
                python \
                python-pip \
                sqlite \
                base-devel \
                openssl \
                libffi \
                curl \
                wget \
                git
            ;;
        *)
            print_warn "Неизвестный дистрибутив Linux, пропускаем установку системных зависимостей"
            print_warn "Убедитесь, что установлены: Python 3.9+, pip, venv, gcc"
            ;;
    esac
}

# Настройка PostgreSQL
setup_postgresql() {
    print_info "Настройка PostgreSQL..."

    # Проверяем, установлен ли PostgreSQL
    if ! command -v psql &> /dev/null; then
        print_error "PostgreSQL не установлен"
        print_error "Установите PostgreSQL вручную:"
        print_error "  Ubuntu/Debian: sudo apt-get install postgresql postgresql-contrib"
        print_error "  Fedora/RHEL: sudo dnf install postgresql postgresql-server"
        print_error "  Arch: sudo pacman -S postgresql"
        return 1
    fi

    case $OS in
        ubuntu|debian|linuxmint)
            # Проверяем, запущен ли сервис
            if ! systemctl is-active --quiet postgresql; then
                sudo systemctl start postgresql
            fi
            # Пытаемся включить, но игнорируем ошибку linked unit
            sudo systemctl enable postgresql 2>/dev/null || true
            ;;
        fedora|centos|rhel)
            if ! systemctl is-active --quiet postgresql; then
                sudo systemctl start postgresql
            fi
            # Инициализация БД если нужно
            if [ ! -d /var/lib/pgsql/data ]; then
                sudo postgresql-setup --initdb || true
            fi
            sudo systemctl enable postgresql 2>/dev/null || true
            ;;
        arch|manjaro)
            if ! systemctl is-active --quiet postgresql; then
                sudo systemctl start postgresql
            fi
            sudo systemctl enable postgresql 2>/dev/null || true
            ;;
    esac

    # Создание пользователя и базы данных (если они не существуют)
    sudo -u postgres psql -c "CREATE USER modbus_user WITH PASSWORD 'modbus_password';" 2>/dev/null || \
        print_warn "Пользователь modbus_user уже существует или ошибка создания"

    sudo -u postgres psql -c "CREATE DATABASE modbus_data OWNER modbus_user;" 2>/dev/null || \
        print_warn "База данных modbus_data уже существует или ошибка создания"

    sudo -u postgres psql -c "ALTER USER modbus_user CREATEDB;" 2>/dev/null || \
        print_warn "Не удалось изменить права пользователя"

    print_info "PostgreSQL настроен"
    print_info "  Хост: localhost:5432"
    print_info "  База данных: modbus_data"
    print_info "  Пользователь: modbus_user"
    print_info "  Пароль: modbus_password"

    return 0
}

# Настройка Redis
setup_redis() {
    print_info "Настройка Redis (опционально)..."

    # Проверяем, установлен ли Redis
    if ! command -v redis-server &> /dev/null; then
        print_warn "Redis не установлен. Устанавливаем..."
        case $OS in
            ubuntu|debian|linuxmint)
                sudo apt-get install -y redis-server
                ;;
            fedora|centos|rhel)
                sudo dnf install -y redis
                ;;
            arch|manjaro)
                sudo pacman -Sy --noconfirm redis
                ;;
        esac
    fi

    # Пытаемся запустить Redis
    if command -v redis-server &> /dev/null; then
        # Запускаем Redis
        if ! systemctl is-active --quiet redis; then
            sudo systemctl start redis 2>/dev/null || \
                print_warn "Не удалось запустить Redis через systemctl"
        fi

        # Пытаемся включить, но игнорируем ошибки linked unit
        sudo systemctl enable redis 2>/dev/null || \
            print_warn "Не удалось включить Redis (возможно linked unit)"

        # Проверяем, запустился ли Redis
        if systemctl is-active --quiet redis || pgrep redis-server > /dev/null; then
            print_info "Redis настроен и запущен"
            print_info "  Хост: localhost:6379"
        else
            print_warn "Redis не запущен. Запустите вручную:"
            print_warn "  sudo systemctl start redis"
            print_warn "  или redis-server --daemonize yes"
        fi
    else
        print_warn "Redis не установлен. Используем альтернативное решение."
        print_warn "Для работы кэширования рекомендуется установить Redis."
    fi
}

# Создание виртуального окружения
create_venv() {
    print_info "Создание виртуального окружения..."

    if [ ! -d "venv" ]; then
        $PYTHON_CMD -m venv venv
        print_info "Виртуальное окружение создано"
    else
        print_warn "Виртуальное окружение уже существует"
    fi

    # Активация venv
    source venv/bin/activate

    # Обновление pip
    pip install --upgrade pip setuptools wheel
}

# Установка Python зависимостей
install_python_deps() {
    print_info "Установка Python зависимостей..."

    if [ ! -f "requirements.txt" ]; then
        cat > requirements.txt << 'EOF'
# Основные зависимости
pymodbus==3.11.4
pyserial==3.5
pyserial-asyncio==0.6

# Web и API
fastapi==0.124.0
uvicorn[standard]==0.38.0
websockets==15.0
aiohttp==3.13.2

# База данных
asyncpg==0.31.0
sqlalchemy==2.0.44
aiosqlite==0.21.0
alembic==1.17.2

# Конфигурация
pyyaml==6.0.3
python-dotenv==1.2.1
pydantic==2.12.5
pydantic-settings==2.12.0
jsonschema==4.25.1

# Утилиты
structlog==25.5.0
psutil==7.1.3
cachetools==6.2.3
redis==7.1.0

# Для графиков и анализа
pandas==2.3.3
numpy==2.3.5
plotly==6.5.0

# Тестирование
pytest==9.0.2
pytest-asyncio==1.3.0
EOF
        print_info "Создан файл requirements.txt"
    fi

    pip install -r requirements.txt
    print_info "Python зависимости установлены"
}

# Создание структуры проекта
create_project_structure() {
    print_step "Создание структуры проекта..."

    # Основные директории
    mkdir -p config logs exports data backup src cache config/templates

    # Создание конфигурационного файла
    if [ ! -f "config/devices.yaml" ]; then
        print_info "Создание основного конфигурационного файла..."

        cat > config/devices.yaml << 'EOF'
# Основные настройки Modbus сервера
# Этот файл содержит общие настройки сервера, базы данных, API и т.д.
# Конфигурация портов и устройств находится в отдельном файле config/ports.yaml

server:
  name: "Modbus Server"
  host: "0.0.0.0"
  api_port: 8000
  websocket_port: 8765
  log_level: "INFO"
  log_file: "logs/modbus_server.log"
  max_workers: 10
  debug: false
  reload: false

database:
  # Используем SQLite по умолчанию (не требует настройки)
  url: "sqlite+aiosqlite:///data/modbus.db"
  # Для PostgreSQL раскомментируйте строку ниже:
  # url: "postgresql://modbus_user:modbus_password@localhost:5432/modbus_data"
  pool_size: 20
  max_overflow: 30
  echo: false
  echo_pool: false
  pool_recycle: 3600
  pool_pre_ping: true

polling:
  max_concurrent_ports: 10
  device_poll_delay: 0.01
  buffer_size: 1000
  statistics_interval: 60

websocket:
  max_connections: 1000
  ping_interval: 20
  ping_timeout: 10
  message_queue_size: 100
  max_retry_attempts: 3
  retry_delay: 5.0
  connection_timeout: 10.0
  read_timeout: 5.0
  write_timeout: 5.0

websocket:
  enabled: true
  max_connections: 1000
  ping_interval: 20
  ping_timeout: 10
  message_queue_size: 100
  compression: true
  max_message_size: 10485760

api:
  enabled: true
  title: "Modbus Server API"
  description: "API для управления Modbus устройствами"
  version: "1.0.0"
  docs_url: "/docs"
  redoc_url: "/redoc"
  openapi_url: "/openapi.json"
  cors_origins:
    - "http://localhost:3000"
    - "http://localhost:8080"
  rate_limit:
    enabled: true
    requests_per_minute: 60

cache:
  enabled: true
  backend: "memory"
  ttl: 300
  max_size: 10000
  redis_url: "redis://localhost:6379/0"
  disk_cache_path: "cache"

security:
  secret_key: "change-this-in-production"
  token_expire_minutes: 1440
  bcrypt_rounds: 12
  rate_limit_enabled: true
  allowed_hosts:
    - "localhost"
    - "127.0.0.1"
    - "0.0.0.0"

logging:
  level: "INFO"
  format: "json"
  rotate: true
  max_size_mb: 100
  backup_count: 5
  compress: true

monitoring:
  enabled: true
  metrics_port: 9091
  health_check_interval: 30
  system_stats_interval: 60
  alerting:
    enabled: false
    email_notifications: false
    webhook_url: ""

notifications:
  enabled: false
  email:
    smtp_server: ""
    smtp_port: 587
    username: ""
    password: ""
    from_address: ""
  telegram:
    enabled: false
    bot_token: ""
    chat_id: ""

export:
  enabled: true
  formats:
    - "csv"
    - "json"
    - "xlsx"
  default_format: "csv"
  max_records_per_export: 100000
  retention_days: 90

# Путь к файлу конфигурации портов
# Конфигурация портов и устройств находится в отдельном файле
ports_config_file: "config/ports.yaml"

# Или можно указать порты прямо здесь (раскомментируйте для использования):
# ports:
#   пример_порта:
#     type: "tcp"
#     host: "192.168.1.100"
#     port: 502
EOF
        print_info "Создан конфигурационный файл config/devices.yaml"
    else
        print_warn "Конфигурационный файл уже существует"
    fi

    # Создаем файл конфигурации портов
    if [ ! -f "config/ports.yaml" ]; then
        print_info "Создание файла конфигурации портов..."

        cat > config/ports.yaml << 'EOF'
# Конфигурация портов и устройств Modbus
# Этот файл содержит настройки всех портов и подключенных устройств
# Порты опрашиваются параллельно, устройства на одном порту - последовательно

# Демо-порт для тестирования (включите для тестирования без реальных устройств)
demo_port:
  enabled: false  # Включите для тестирования
  type: "tcp"
  host: "127.0.0.1"
  port: 5020
  timeout: 1.0
  max_retries: 1
  retry_delay: 1.0
  description: "Демо-порт для тестирования"
  tags:
    - "демо"
    - "тест"

  devices:
    - name: "demo_temperature"
      address: 1
      enabled: true
      poll_interval: 2.0
      timeout: 0.5
      description: "Демо-датчик температуры"
      tags:
        - "демо"
        - "температура"

      registers:
        - type: "holding"
          address: 100
          name: "temperature"
          description: "Демо температура"
          unit: "°C"
          scale: 0.1
          offset: 0.0
          data_type: "uint16"
          read_only: true
          precision: 1

        - type: "holding"
          address: 101
          name: "humidity"
          description: "Демо влажность"
          unit: "%"
          scale: 0.1
          offset: 0.0
          data_type: "uint16"
          read_only: true
          precision: 1

    - name: "demo_pressure"
      address: 2
      enabled: true
      poll_interval: 3.0
      timeout: 0.5
      description: "Демо-датчик давления"
      tags:
        - "демо"
        - "давление"

      registers:
        - type: "holding"
          address: 200
          name: "pressure"
          description: "Демо давление"
          unit: "бар"
          scale: 0.01
          offset: 0.0
          data_type: "uint16"
          read_only: true
          precision: 2

# Примеры конфигурации различных типов портов (закомментированы)

# Пример TCP порта
# plc_controller:
#   enabled: true
#   type: "tcp"
#   host: "192.168.1.100"
#   port: 502
#   timeout: 2.0
#   max_retries: 3
#   retry_delay: 5.0
#   description: "Основной ПЛК"
#   tags:
#     - "производство"
#
#   devices:
#     - name: "motor_drive"
#       address: 1
#       enabled: true
#       poll_interval: 0.5
#       timeout: 1.5
#       description: "Привод двигателя"
#       tags:
#         - "двигатель"
#
#       registers:
#         - type: "holding"
#           address: 40001
#           name: "motor_speed"
#           description: "Скорость двигателя"
#           unit: "об/мин"
#           scale: 1.0
#           offset: 0.0
#           data_type: "uint16"
#           read_only: false
#           precision: 0

# Пример RTU over TCP
# moxa_gateway:
#   enabled: true
#   type: "rtu_tcp"
#   host: "192.168.1.50"
#   port: 4001
#   timeout: 3.0
#   max_retries: 5
#   retry_delay: 2.0
#   description: "MOXA NPort для RS-485"
#   tags:
#     - "gateway"
#
#   devices:
#     - name: "energy_meter"
#       address: 1
#       enabled: true
#       poll_interval: 5.0
#       timeout: 2.0
#       description: "Счетчик электроэнергии"
#       tags:
#         - "энергия"
#
#       registers:
#         - type: "holding"
#           address: 0
#           name: "total_energy"
#           description: "Суммарная энергия"
#           unit: "кВт·ч"
#           scale: 0.01
#           offset: 0.0
#           data_type: "int32"
#           read_only: true
#           precision: 2

# Пример последовательного порта
# serial_port:
#   enabled: true
#   type: "rtu_serial"
#   port_name: "/dev/ttyUSB0"
#   baudrate: 9600
#   parity: "N"
#   stopbits: 1
#   bytesize: 8
#   timeout: 1.0
#   max_retries: 3
#   retry_delay: 1.0
#   description: "Последовательный порт"
#   tags:
#     - "serial"
#
#   devices:
#     - name: "pressure_sensor"
#       address: 3
#       enabled: true
#       poll_interval: 1.0
#       timeout: 0.5
#       description: "Датчик давления"
#       tags:
#         - "давление"
#
#       registers:
#         - type: "holding"
#           address: 0
#           name: "pressure"
#           description: "Давление"
#           unit: "бар"
#           scale: 0.01
#           offset: 0.0
#           data_type: "uint16"
#           read_only: true
#           precision: 2

# Группы устройств (для организации)
groups:
  demo_devices:
    name: "Демо устройства"
    description: "Все демонстрационные устройства"
    ports:
      - "demo_port"
    tags:
      - "демо"
      - "тест"

# Настройки по умолчанию для новых устройств
defaults:
  port:
    timeout: 2.0
    max_retries: 3
    retry_delay: 5.0
  device:
    poll_interval: 1.0
    timeout: 1.0
    enabled: true
  register:
    data_type: "uint16"
    read_only: true
    scale: 1.0
    offset: 0.0
    precision: 2
EOF

        print_info "Создан файл конфигурации портов config/ports.yaml"
    else
        print_warn "Файл конфигурации портов уже существует"
    fi

    # Создаем примеры конфигурации
    if [ ! -f "config/templates/devices.example.yaml" ]; then
        cat > config/templates/devices.example.yaml << 'EOF'
# Пример основного конфигурационного файла
# Копируйте в config/devices.yaml и настройте под свои нужды

server:
  name: "Мой Modbus Сервер"
  host: "0.0.0.0"
  api_port: 8000
  websocket_port: 8765
  log_level: "INFO"
  log_file: "logs/modbus_server.log"
  max_workers: 10

database:
  url: "postgresql://user:password@localhost:5432/modbus_data"
  pool_size: 20
  max_overflow: 30

# Указываем путь к файлу с конфигурацией портов
ports_config_file: "config/ports.yaml"

# Остальные настройки...
EOF
        print_info "Создан пример основного конфигурационного файла"
    fi

    if [ ! -f "config/templates/ports.example.yaml" ]; then
        cat > config/templates/ports.example.yaml << 'EOF'
# Пример конфигурации портов
# Копируйте нужные секции в config/ports.yaml

# TCP порт с ПЛК Siemens
siemens_plc:
  enabled: true
  type: "tcp"
  host: "192.168.1.100"
  port: 502
  timeout: 2.0
  description: "ПЛК Siemens S7-1200"

  devices:
    - name: "conveyor_motor"
      address: 1
      poll_interval: 0.5
      description: "Двигатель конвейера"

      registers:
        - type: "holding"
          address: 40001
          name: "speed_setpoint"
          description: "Задание скорости"
          unit: "об/мин"
          read_only: false

        - type: "holding"
          address: 40002
          name: "actual_speed"
          description: "Фактическая скорость"
          unit: "об/мин"
          read_only: true

# RTU over TCP через MOXA
energy_meters:
  enabled: true
  type: "rtu_tcp"
  host: "192.168.1.50"
  port: 4001
  timeout: 3.0

  devices:
    - name: "main_meter"
      address: 1
      poll_interval: 5.0
      description: "Основной счетчик"

      registers:
        - type: "holding"
          address: 0
          name: "total_energy"
          description: "Суммарная энергия"
          unit: "кВт·ч"
          scale: 0.01
          data_type: "int32"
          read_only: true
EOF
        print_info "Создан пример конфигурации портов"
    fi

    # Создание .env файла
    if [ ! -f ".env" ]; then
        print_info "Создание файла окружения..."

        # Генерируем случайный секретный ключ
        SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "your-secret-key-change-me")

        cat > .env << EOF
# Настройки окружения Modbus сервера
PYTHONPATH=./src

# База данных (SQLite по умолчанию)
DATABASE_URL=sqlite+aiosqlite:///data/modbus.db
# Для PostgreSQL раскомментируйте:
# DATABASE_URL=postgresql://modbus_user:modbus_password@localhost:5432/modbus_data

# Настройки сервера
HOST=0.0.0.0
API_PORT=8000
WEBSOCKET_PORT=8765
LOG_LEVEL=INFO

# Безопасность
SECRET_KEY=$SECRET_KEY
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Дополнительные настройки
DEBUG=false
RELOAD=false
WORKERS=4
EOF
        print_info "Создан файл .env"
    else
        print_warn "Файл .env уже существует"
    fi

    # Создание структуры исходного кода
    mkdir -p src

    if [ ! -f "src/__init__.py" ]; then
        touch src/__init__.py
    fi

    # Создание основных файлов, если их нет
    if [ ! -f "src/main.py" ]; then
        print_warn "Основные файлы исходного кода отсутствуют"
        print_warn "Скопируйте файлы из репозитория или создайте их вручную"
    fi

    # Создание директории для логирования
    sudo chmod 755 logs

    print_info "Структура проекта создана"
}

# Настройка systemd сервиса
setup_systemd_service() {
    print_info "Настройка systemd сервиса..."

    SERVICE_FILE="/etc/systemd/system/modbus-server.service"

    if [ ! -f "$SERVICE_FILE" ]; then
        CURRENT_USER=$(whoami)
        CURRENT_DIR=$(pwd)

        sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Modbus Server
After=network.target
Wants=network.target

[Service]
Type=simple
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$CURRENT_DIR
Environment="PATH=$CURRENT_DIR/venv/bin"
EnvironmentFile=$CURRENT_DIR/.env
ExecStart=$CURRENT_DIR/venv/bin/python -m src.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Жесткие ограничения
LimitNOFILE=65536
LimitNPROC=65536

# Мягкие ограничения
Nice=0

# Переменные окружения
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$CURRENT_DIR/src

[Install]
WantedBy=multi-user.target
EOF

        sudo systemctl daemon-reload
        print_info "Systemd сервис создан"

        # Спрашиваем, включить ли автозагрузку
        read -p "Включить автозапуск сервиса при загрузке системы? (y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo systemctl enable modbus-server
            print_info "Сервис добавлен в автозагрузку"
        else
            print_info "Сервис не добавлен в автозагрузку"
            print_info "Для ручного запуска: sudo systemctl start modbus-server"
        fi
    else
        print_warn "Systemd сервис уже существует"
    fi

    print_info ""
    print_info "Команды управления сервисом:"
    print_info "  sudo systemctl start modbus-server    # Запуск"
    print_info "  sudo systemctl stop modbus-server     # Остановка"
    print_info "  sudo systemctl restart modbus-server  # Перезапуск"
    print_info "  sudo systemctl status modbus-server   # Статус"
    print_info "  sudo journalctl -u modbus-server -f   # Логи"
}

# Настройка Nginx (опционально)
setup_nginx() {
    print_info "Проверка Nginx..."

    if ! command -v nginx &> /dev/null; then
        print_warn "Nginx не установлен"
        read -p "Установить Nginx для проксирования? (y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            case $OS in
                ubuntu|debian|linuxmint)
                    sudo apt-get install -y nginx
                    ;;
                fedora|centos|rhel)
                    sudo dnf install -y nginx
                    ;;
                arch|manjaro)
                    sudo pacman -Sy --noconfirm nginx
                    ;;
            esac
        else
            print_info "Пропускаем установку Nginx"
            return
        fi
    fi

    if command -v nginx &> /dev/null; then
        print_info "Настройка Nginx..."

        NGINX_CONFIG="/etc/nginx/sites-available/modbus-server"

        if [ ! -f "$NGINX_CONFIG" ]; then
            sudo tee "$NGINX_CONFIG" > /dev/null << EOF
server {
    listen 80;
    server_name _;

    # Статические файлы
    location /static/ {
        alias $(pwd)/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # REST API
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;

        proxy_connect_timeout 7d;
        proxy_send_timeout 7d;
        proxy_read_timeout 7d;
    }

    # Документация API
    location /docs/ {
        proxy_pass http://127.0.0.1:8000/docs;
        proxy_set_header Host \$host;
    }

    # Корень - редирект на документацию
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
    }
}
EOF

            # Создаем симлинк в sites-enabled
            sudo ln -sf "$NGINX_CONFIG" /etc/nginx/sites-enabled/

            # Проверяем конфигурацию
            sudo nginx -t

            # Перезагружаем Nginx
            sudo systemctl reload nginx

            print_info "Nginx настроен"
            print_info "Сервер будет доступен по http://ваш-ip"
        else
            print_warn "Конфигурация Nginx уже существует"
        fi
    fi
}

# Настройка firewall
setup_firewall() {
    print_info "Настройка firewall..."

    # Проверяем, нужно ли настраивать firewall
    read -p "Настроить firewall (открыть порты 8000 и 8765)? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Пропускаем настройку firewall"
        return
    fi

    if command -v ufw &> /dev/null && systemctl is-active --quiet ufw; then
        print_info "Настройка UFW..."
        sudo ufw allow 8000/tcp comment "Modbus Server API"
        sudo ufw allow 8765/tcp comment "Modbus Server WebSocket"
        print_info "UFW правила добавлены"
    elif command -v firewall-cmd &> /dev/null && systemctl is-active --quiet firewalld; then
        print_info "Настройка firewalld..."
        sudo firewall-cmd --permanent --add-port=8000/tcp
        sudo firewall-cmd --permanent --add-port=8765/tcp
        sudo firewall-cmd --reload
        print_info "Firewalld правила добавлены"
    elif command -v iptables &> /dev/null; then
        print_info "Настройка iptables..."
        sudo iptables -A INPUT -p tcp --dport 8000 -j ACCEPT
        sudo iptables -A INPUT -p tcp --dport 8765 -j ACCEPT
        print_info "Правила iptables добавлены (временные)"
        print_warn "Для сохранения правил iptables после перезагрузки:"
        print_warn "  Ubuntu/Debian: sudo netfilter-persistent save"
        print_warn "  Другие: сохраните правила вручную"
    else
        print_warn "Не удалось определить активный firewall"
        print_info "Если используется firewall, откройте порты вручную:"
        print_info "  Порты: 8000 (API) и 8765 (WebSocket)"
    fi
}

# Инициализация базы данных
init_database() {
    print_info "Инициализация базы данных..."

    source venv/bin/activate

    # Создаем простой скрипт инициализации
    cat > init_db.py << 'EOF'
#!/usr/bin/env python3
import asyncio
import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def init_sqlite():
    """Инициализация SQLite базы данных"""
    try:
        from database import DatabaseManager

        # Используем SQLite
        db_url = "sqlite+aiosqlite:///data/modbus.db"
        print(f"Инициализация SQLite базы данных: {db_url}")

        db = DatabaseManager(db_url)
        await db.init_db()
        print("✅ SQLite база данных успешно инициализирована")

        return True
    except ImportError as e:
        print(f"❌ Ошибка импорта: {e}")
        return False
    except Exception as e:
        print(f"❌ Ошибка инициализации SQLite: {e}")
        return False

async def init_postgresql():
    """Инициализация PostgreSQL базы данных"""
    try:
        from database import DatabaseManager

        # Пытаемся подключиться к PostgreSQL
        db_url = "postgresql://modbus_user:modbus_password@localhost:5432/modbus_data"
        print(f"Попытка подключения к PostgreSQL: {db_url}")

        db = DatabaseManager(db_url)
        await db.init_db()
        print("✅ PostgreSQL база данных успешно инициализирована")

        return True
    except Exception as e:
        print(f"⚠️ Не удалось подключиться к PostgreSQL: {e}")
        print("Будет использована SQLite база данных")
        return False

async def main():
    print("Начало инициализации базы данных...")

    # Сначала пробуем PostgreSQL
    postgres_ok = await init_postgresql()

    # Если PostgreSQL не сработал, используем SQLite
    if not postgres_ok:
        print("Используем SQLite как основную базу данных...")
        sqlite_ok = await init_sqlite()

        if sqlite_ok:
            print("✅ База данных готова к использованию")
        else:
            print("❌ Не удалось инициализировать базу данных")
            print("Проверьте наличие файлов и зависимости")
    else:
        print("✅ База данных готова к использованию")

if __name__ == "__main__":
    asyncio.run(main())
EOF

    python init_db.py
    rm -f init_db.py

    print_info "Инициализация базы данных завершена"
}

# Создание скриптов управления
create_management_scripts() {
    print_info "Создание скриптов управления..."

    # Скрипт запуска
    cat > start_server.sh << 'EOF'
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
EOF

    # Скрипт остановки
    cat > stop_server.sh << 'EOF'
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
EOF

    # Скрипт перезапуска
    cat > restart_server.sh << 'EOF'
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
EOF

    # Скрипт просмотра логов
    cat > view_logs.sh << 'EOF'
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
EOF

    # Скрипт обновления
    cat > update_server.sh << 'EOF'
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
EOF

    # status_server.sh
    cat > status_server.sh << 'EOF'
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
EOF

    chmod +x start_server.sh stop_server.sh restart_server.sh view_logs.sh update_server.sh status_server.sh

    print_info "Скрипты управления созданы:"
    print_info "  ./start_server.sh    - Запуск сервера"
    print_info "  ./stop_server.sh     - Остановка сервера"
    print_info "  ./restart_server.sh  - Перезапуск сервера"
    print_info "  ./view_logs.sh       - Просмотр логов"
    print_info "  ./status_server.sh   - Проверка статуса"
}

create_simple_server() {
    print_info "Создание простого сервера для тестирования..."

    if [ ! -f "src/main.py" ]; then
        mkdir -p src
        cat > src/main.py << 'EOF'
#!/usr/bin/env python3
"""
Простой Modbus сервер для тестирования
"""
import asyncio
import logging
import sys
import json
from datetime import datetime
import random
from pathlib import Path

# Добавляем текущую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/modbus_server.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class SimpleModbusServer:
    def __init__(self):
        self.running = False
        self.devices = [
            {"name": "temperature_sensor_1", "type": "temperature"},
            {"name": "pressure_sensor_1", "type": "pressure"},
            {"name": "flow_meter_1", "type": "flow"}
        ]

    async def simulate_device_data(self, device):
        """Симуляция данных устройства"""
        if device["type"] == "temperature":
            value = round(random.uniform(20.0, 25.0), 2)
            unit = "°C"
        elif device["type"] == "pressure":
            value = round(random.uniform(980.0, 1020.0), 1)
            unit = "hPa"
        else:  # flow
            value = round(random.uniform(0.0, 100.0), 1)
            unit = "m³/h"

        return {
            "device": device["name"],
            "type": device["type"],
            "timestamp": datetime.now().isoformat(),
            "value": value,
            "unit": unit,
            "quality": "good"
        }

    async def run_websocket_server(self):
        """Запуск простого WebSocket сервера"""
        try:
            import asyncio
            import websockets

            async def handler(websocket, path):
                logger.info(f"Новое WebSocket соединение: {websocket.remote_address}")
                try:
                    async for message in websocket:
                        # Простая обработка сообщений
                        if message == "ping":
                            await websocket.send("pong")
                        elif message.startswith("subscribe"):
                            device = message.split()[1] if len(message.split()) > 1 else "all"
                            await websocket.send(f"subscribed to {device}")
                except Exception as e:
                    logger.error(f"WebSocket ошибка: {e}")

            server = await websockets.serve(handler, "0.0.0.0", 8765)
            logger.info("WebSocket сервер запущен на порту 8765")
            await server.wait_closed()

        except ImportError:
            logger.warning("WebSocket библиотека не установлена, пропускаем WebSocket сервер")
        except Exception as e:
            logger.error(f"Ошибка WebSocket сервера: {e}")

    async def run_fastapi_server(self):
        """Запуск FastAPI сервера"""
        try:
            from fastapi import FastAPI
            import uvicorn

            app = FastAPI(title="Modbus Server API", version="1.0.0")

            @app.get("/")
            async def root():
                return {
                    "service": "Modbus Server",
                    "status": "running",
                    "timestamp": datetime.now().isoformat()
                }

            @app.get("/api/health")
            async def health():
                return {"status": "healthy"}

            @app.get("/api/devices")
            async def get_devices():
                return {"devices": self.devices}

            config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()

        except ImportError:
            logger.warning("FastAPI/uvicorn не установлены, пропускаем API сервер")
        except Exception as e:
            logger.error(f"Ошибка API сервера: {e}")

    async def run(self):
        """Основной цикл работы сервера"""
        self.running = True
        logger.info("=" * 60)
        logger.info("🚀 Запуск Modbus сервера")
        logger.info("=" * 60)

        # Запускаем сервисы в фоне
        import asyncio
        tasks = []

        # Запускаем WebSocket сервер если установлена библиотека
        try:
            import websockets
            ws_task = asyncio.create_task(self.run_websocket_server())
            tasks.append(ws_task)
        except ImportError:
            logger.info("WebSocket сервер отключен (установите websockets)")

        # Запускаем API сервер если установлена библиотека
        try:
            from fastapi import FastAPI
            api_task = asyncio.create_task(self.run_fastapi_server())
            tasks.append(api_task)
        except ImportError:
            logger.info("API сервер отключен (установите fastapi uvicorn)")

        # Основной цикл генерации данных
        try:
            cycle = 0
            while self.running:
                cycle += 1
                logger.info(f"📊 Цикл опроса #{cycle}")

                for device in self.devices:
                    data = await self.simulate_device_data(device)
                    logger.info(f"📡 {device['name']}: {json.dumps(data, ensure_ascii=False)}")

                # Ждем 5 секунд до следующего цикла
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info("Сервер остановлен")
        except KeyboardInterrupt:
            logger.info("Получен сигнал KeyboardInterrupt")
        finally:
            self.running = False
            # Отменяем все задачи
            for task in tasks:
                task.cancel()

            # Ждем завершения задач
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Сервер завершил работу")

    async def stop(self):
        """Остановка сервера"""
        self.running = False

async def main():
    server = SimpleModbusServer()
    try:
        await server.run()
    except KeyboardInterrupt:
        await server.stop()

if __name__ == "__main__":
    asyncio.run(main())
EOF

        chmod +x src/main.py
        print_info "Создан простой сервер в src/main.py"
    else
        print_warn "Файл src/main.py уже существует"
    fi
}

show_final_instructions() {
    echo ""
    echo "========================================="
    if [ "$MINIMAL_MODE" = true ]; then
        echo "✅ МИНИМАЛЬНАЯ УСТАНОВКА ЗАВЕРШЕНА!"
    else
        echo "✅ ПОЛНАЯ УСТАНОВКА ЗАВЕРШЕНА!"
    fi
    echo "========================================="
    echo ""
    echo "📁 СТРУКТУРА КОНФИГУРАЦИИ:"
    echo ""
    echo "  📄 config/devices.yaml    - Основные настройки сервера"
    echo "  📄 config/ports.yaml      - Конфигурация портов и устройств"
    echo "  📁 config/templates/      - Примеры конфигураций"
    echo "  📄 .env                   - Переменные окружения"
    echo ""
    echo "⚙️  НАСТРОЙКА КОНФИГУРАЦИИ:"
    echo ""
    echo "  1. Основные настройки (config/devices.yaml):"
    echo "     - Порт API (по умолчанию: 8000)"
    echo "     - Порт WebSocket (по умолчанию: 8765)"
    echo "     - Настройки базы данных"
    echo "     - Настройки безопасности"
    echo ""
    echo "  2. Конфигурация портов (config/ports.yaml):"
    echo "     - Добавьте свои порты и устройства"
    echo "     - Укажите тип подключения (tcp, rtu_tcp, rtu_serial)"
    echo "     - Настройте адреса, интервалы опроса и регистры"
    echo ""
    echo "  3. Для начала работы включите демо-порт:"
    echo "     nano config/ports.yaml"
    echo "     Измените 'enabled: false' на 'enabled: true' для demo_port"
    echo ""
    echo "🚀 ЗАПУСК СЕРВЕРА:"
    echo ""
    echo "  Способ A - Ручной запуск:"
    echo "    ./start_server.sh"
    echo ""

    if [ -f "/etc/systemd/system/modbus-server.service" ]; then
        echo "  Способ B - Через systemd:"
        echo "    sudo systemctl start modbus-server"
        echo "    sudo systemctl status modbus-server"
        echo ""
    fi

    echo "🌐 ДОСТУП К СЕРВИСАМ:"
    echo ""
    echo "  REST API:"
    echo "    http://localhost:8000/docs"
    echo "    http://localhost:8000/redoc"
    echo ""
    echo "  WebSocket:"
    echo "    ws://localhost:8765"
    echo ""
    echo "  Метрики (если включено):"
    echo "    http://localhost:9091"
    echo ""
    echo "🔧 УПРАВЛЕНИЕ:"
    echo ""
    echo "  ./start_server.sh    - Запуск"
    echo "  ./stop_server.sh     - Остановка"
    echo "  ./restart_server.sh  - Перезапуск"
    echo "  ./status_server.sh   - Статус"
    echo "  ./view_logs.sh       - Логи"
    echo ""
    echo "📊 ДЛЯ РАБОТЫ С РЕАЛЬНЫМИ УСТРОЙСТВАМИ:"
    echo ""
    echo "  1. Добавьте конфигурацию порта в config/ports.yaml"
    echo "  2. Укажите правильные параметры подключения"
    echo "  3. Настройте регистры устройств"
    echo "  4. Перезапустите сервер"
    echo ""
    echo "💡 ПРИМЕРЫ КОНФИГУРАЦИИ:"
    echo ""
    echo "  В папке config/templates/ находятся примеры:"
    echo "    - devices.example.yaml"
    echo "    - ports.example.yaml"
    echo ""
    echo "========================================="
    echo "⚡ Готово к работе!"
    echo "========================================="
}

main() {
    if [ "$MINIMAL_MODE" = true ]; then
        print_info "Запуск в минимальном режиме..."
        check_python
        create_venv
        install_python_deps
        create_project_structure
        create_simple_server
        create_management_scripts
        show_final_instructions
        exit 0
    fi

    print_info "Начало установки Modbus сервера"
    echo ""

    check_root
    check_os
    check_python

    echo ""
    read -p "Установить системные зависимости? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        install_system_deps
    else
        print_info "Пропускаем установку системных зависимостей"
    fi

    echo ""
    read -p "Настроить PostgreSQL базу данных? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        setup_postgresql
    else
        print_info "Пропускаем настройку PostgreSQL"
        print_info "Будет использована SQLite база данных"
    fi

    echo ""
    read -p "Настроить Redis для кэширования? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        setup_redis
    else
        print_info "Пропускаем настройку Redis"
    fi

    create_venv
    install_python_deps
    create_project_structure
    create_simple_server

    echo ""
    read -p "Настроить systemd сервис для автозапуска? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        setup_systemd_service
    else
        print_info "Пропускаем настройку systemd"
    fi

    echo ""
    read -p "Настроить Nginx для проксирования? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        setup_nginx
    else
        print_info "Пропускаем настройку Nginx"
    fi

    echo ""
    read -p "Настроить firewall? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        setup_firewall
    else
        print_info "Пропускаем настройку firewall"
    fi

    init_database
    create_management_scripts

    show_final_instructions
}

# Запуск основной функции с обработкой ошибок
trap 'print_error "Установка прервана"; exit 1' INT TERM

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi