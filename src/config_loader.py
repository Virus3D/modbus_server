"""
Загрузка и валидация конфигурации из нескольких файлов
"""
import yaml
import os
import logging
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
from dataclasses import dataclass, field, asdict
import jsonschema
from enum import Enum

from .models import (
    ConnectionType, RegisterType, DataType,
    PortConfig, DeviceConfig, RegisterConfig
)

logger = logging.getLogger(__name__)

class ConfigValidationError(Exception):
    """Ошибка валидации конфигурации"""
    pass

@dataclass
class ServerConfig:
    """Конфигурация сервера"""
    name: str = "Modbus Server"
    host: str = "0.0.0.0"
    api_port: int = 8000
    websocket_port: int = 8765
    log_level: str = "INFO"
    log_file: str = "logs/modbus_server.log"
    max_workers: int = 10
    debug: bool = False
    reload: bool = False

@dataclass
class DatabaseConfig:
    """Конфигурация базы данных"""
    url: str = "sqlite+aiosqlite:///data/modbus.db"
    pool_size: int = 20
    max_overflow: int = 30
    echo: bool = False
    echo_pool: bool = False
    pool_recycle: int = 3600
    pool_pre_ping: bool = True

@dataclass
class PollingConfig:
    """Конфигурация опроса"""
    max_concurrent_ports: int = 10
    device_poll_delay: float = 0.01
    buffer_size: int = 1000
    statistics_interval: int = 60
    max_retry_attempts: int = 3
    retry_delay: float = 5.0
    connection_timeout: float = 10.0
    read_timeout: float = 5.0
    write_timeout: float = 5.0

@dataclass
class WebSocketConfig:
    """Конфигурация WebSocket"""
    enabled: bool = True
    max_connections: int = 1000
    ping_interval: int = 20
    ping_timeout: int = 10
    message_queue_size: int = 100
    compression: bool = True
    max_message_size: int = 10485760

@dataclass
class ApiConfig:
    """Конфигурация REST API"""
    enabled: bool = True
    title: str = "Modbus Server API"
    description: str = "API для управления Modbus устройствами"
    version: str = "1.0.0"
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"
    openapi_url: str = "/openapi.json"
    cors_origins: List[str] = field(default_factory=lambda: [
        "http://localhost:3000",
        "http://localhost:8080"
    ])
    rate_limit: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": True,
        "requests_per_minute": 60
    })

@dataclass
class CacheConfig:
    """Конфигурация кэширования"""
    enabled: bool = True
    backend: str = "memory"  # memory, redis, disk
    ttl: int = 300
    max_size: int = 10000
    redis_url: str = "redis://localhost:6379/0"
    disk_cache_path: str = "cache"

@dataclass
class SecurityConfig:
    """Конфигурация безопасности"""
    secret_key: str = "change-this-in-production"
    token_expire_minutes: int = 1440
    bcrypt_rounds: int = 12
    rate_limit_enabled: bool = True
    allowed_hosts: List[str] = field(default_factory=lambda: [
        "localhost", "127.0.0.1", "0.0.0.0"
    ])

@dataclass
class LoggingConfig:
    """Конфигурация логирования"""
    level: str = "INFO"
    format: str = "json"
    rotate: bool = True
    max_size_mb: int = 100
    backup_count: int = 5
    compress: bool = True

@dataclass
class MonitoringConfig:
    """Конфигурация мониторинга"""
    enabled: bool = True
    metrics_port: int = 9091
    health_check_interval: int = 30
    system_stats_interval: int = 60
    alerting: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "email_notifications": False,
        "webhook_url": ""
    })

@dataclass
class NotificationConfig:
    """Конфигурация уведомлений"""
    enabled: bool = False
    email: Dict[str, Any] = field(default_factory=lambda: {
        "smtp_server": "",
        "smtp_port": 587,
        "username": "",
        "password": "",
        "from_address": ""
    })
    telegram: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "bot_token": "",
        "chat_id": ""
    })

@dataclass
class ExportConfig:
    """Конфигурация экспорта данных"""
    enabled: bool = True
    formats: List[str] = field(default_factory=lambda: ["csv", "json", "xlsx"])
    default_format: str = "csv"
    max_records_per_export: int = 100000
    retention_days: int = 90

@dataclass
class AppConfig:
    """Полная конфигурация приложения"""
    server: ServerConfig
    database: DatabaseConfig
    polling: PollingConfig
    websocket: WebSocketConfig
    api: ApiConfig
    cache: CacheConfig
    security: SecurityConfig
    logging: LoggingConfig
    monitoring: MonitoringConfig
    notifications: NotificationConfig
    export: ExportConfig
    ports_config_file: str = "config/ports.yaml"
    ports: Dict[str, Any] = field(default_factory=dict)

class ConfigLoader:
    """Загрузчик конфигурации из нескольких файлов"""

    # JSON Schema для валидации основной конфигурации
    MAIN_CONFIG_SCHEMA = {
        "type": "object",
        "properties": {
            "server": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "host": {"type": "string"},
                    "api_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "websocket_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "log_level": {"type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]},
                    "log_file": {"type": "string"},
                    "max_workers": {"type": "integer", "minimum": 1},
                    "debug": {"type": "boolean"},
                    "reload": {"type": "boolean"}
                },
                "required": ["host", "api_port", "websocket_port"]
            },
            "database": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "pool_size": {"type": "integer", "minimum": 1},
                    "max_overflow": {"type": "integer", "minimum": 0},
                    "echo": {"type": "boolean"},
                    "echo_pool": {"type": "boolean"},
                    "pool_recycle": {"type": "integer", "minimum": 0},
                    "pool_pre_ping": {"type": "boolean"}
                },
                "required": ["url"]
            },
            "polling": {
                "type": "object",
                "properties": {
                    "max_concurrent_ports": {"type": "integer", "minimum": 1},
                    "device_poll_delay": {"type": "number", "minimum": 0},
                    "buffer_size": {"type": "integer", "minimum": 1},
                    "statistics_interval": {"type": "integer", "minimum": 1},
                    "max_retry_attempts": {"type": "integer", "minimum": 0},
                    "retry_delay": {"type": "number", "minimum": 0},
                    "connection_timeout": {"type": "number", "minimum": 0.1},
                    "read_timeout": {"type": "number", "minimum": 0.1},
                    "write_timeout": {"type": "number", "minimum": 0.1}
                }
            },
            "websocket": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "max_connections": {"type": "integer", "minimum": 1},
                    "ping_interval": {"type": "integer", "minimum": 1},
                    "ping_timeout": {"type": "integer", "minimum": 1},
                    "message_queue_size": {"type": "integer", "minimum": 1},
                    "compression": {"type": "boolean"},
                    "max_message_size": {"type": "integer", "minimum": 1024}
                }
            },
            "api": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "version": {"type": "string"},
                    "docs_url": {"type": "string"},
                    "redoc_url": {"type": "string"},
                    "openapi_url": {"type": "string"},
                    "cors_origins": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "rate_limit": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "requests_per_minute": {"type": "integer", "minimum": 1}
                        }
                    }
                }
            },
            "cache": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "backend": {"type": "string", "enum": ["memory", "redis", "disk"]},
                    "ttl": {"type": "integer", "minimum": 1},
                    "max_size": {"type": "integer", "minimum": 1},
                    "redis_url": {"type": "string"},
                    "disk_cache_path": {"type": "string"}
                }
            },
            "security": {
                "type": "object",
                "properties": {
                    "secret_key": {"type": "string"},
                    "token_expire_minutes": {"type": "integer", "minimum": 1},
                    "bcrypt_rounds": {"type": "integer", "minimum": 4},
                    "rate_limit_enabled": {"type": "boolean"},
                    "allowed_hosts": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            },
            "logging": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]},
                    "format": {"type": "string", "enum": ["json", "text"]},
                    "rotate": {"type": "boolean"},
                    "max_size_mb": {"type": "integer", "minimum": 1},
                    "backup_count": {"type": "integer", "minimum": 1},
                    "compress": {"type": "boolean"}
                }
            },
            "monitoring": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "metrics_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "health_check_interval": {"type": "integer", "minimum": 1},
                    "system_stats_interval": {"type": "integer", "minimum": 1},
                    "alerting": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "email_notifications": {"type": "boolean"},
                            "webhook_url": {"type": "string"}
                        }
                    }
                }
            },
            "notifications": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "email": {
                        "type": "object",
                        "properties": {
                            "smtp_server": {"type": "string"},
                            "smtp_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                            "username": {"type": "string"},
                            "password": {"type": "string"},
                            "from_address": {"type": "string"}
                        }
                    },
                    "telegram": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "bot_token": {"type": "string"},
                            "chat_id": {"type": "string"}
                        }
                    }
                }
            },
            "export": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "formats": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["csv", "json", "xlsx", "pdf"]}
                    },
                    "default_format": {"type": "string"},
                    "max_records_per_export": {"type": "integer", "minimum": 1},
                    "retention_days": {"type": "integer", "minimum": 1}
                }
            },
            "ports_config_file": {"type": "string"},
            "ports": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "type": {"type": "string", "enum": ["tcp", "rtu_tcp", "rtu_serial"]},
                        "host": {"type": "string"},
                        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "port_name": {"type": "string"},
                        "baudrate": {"type": "integer", "enum": [300, 600, 1200, 2400, 4800, 9600,
                                                                19200, 38400, 57600, 115200]},
                        "parity": {"type": "string", "enum": ["N", "E", "O"]},
                        "stopbits": {"type": "integer", "enum": [1, 2]},
                        "bytesize": {"type": "integer", "enum": [5, 6, 7, 8]},
                        "timeout": {"type": "number", "minimum": 0.1},
                        "max_retries": {"type": "integer", "minimum": 0},
                        "retry_delay": {"type": "number", "minimum": 0},
                        "description": {"type": "string"},
                        "byteorder": {"type": "string", "enum": ["big", "little"]},
                        "wordorder": {"type": "string", "enum": ["big", "little"]},
                        "devices": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "address": {"type": "integer", "minimum": 1, "maximum": 247},
                                    "enabled": {"type": "boolean"},
                                    "poll_interval": {"type": "number", "minimum": 0.01},
                                    "timeout": {"type": "number", "minimum": 0.1},
                                    "description": {"type": "string"},
                                    "registers": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "type": {"type": "string", "enum": ["holding", "input", "coil", "discrete"]},
                                                "address": {"type": "integer", "minimum": 0},
                                                "name": {"type": "string"},
                                                "description": {"type": "string"},
                                                "unit": {"type": "string"},
                                                "scale": {"type": "number"},
                                                "offset": {"type": "number"},
                                                "data_type": {"type": "string", "enum": ["int16", "uint16", "int32", "uint32", "float", "bool"]},
                                                "read_only": {"type": "boolean"},
                                                "precision": {"type": "integer", "minimum": 0},
                                                "byteorder": {"type": "string", "enum": ["big", "little"]},
                                                "wordorder": {"type": "string", "enum": ["big", "little"]}
                                            },
                                            "required": ["type", "address"]
                                        }
                                    }
                                },
                                "required": ["name", "address"]
                            }
                        }
                    },
                    "required": ["type", "devices"]
                }
            }
        },
        "required": ["server", "database"]
    }

    # JSON Schema для конфигурации портов
    PORTS_CONFIG_SCHEMA = {
        "type": "object",
        "patternProperties": {
            "^[a-zA-Z0-9_-]+$": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "type": {"type": "string", "enum": ["tcp", "rtu_tcp", "rtu_serial"]},
                    "host": {"type": "string"},
                    "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                    "port_name": {"type": "string"},
                    "baudrate": {"type": "integer", "enum": [300, 600, 1200, 2400, 4800, 9600,
                                                            19200, 38400, 57600, 115200]},
                    "parity": {"type": "string", "enum": ["N", "E", "O"]},
                    "stopbits": {"type": "integer", "enum": [1, 2]},
                    "bytesize": {"type": "integer", "enum": [5, 6, 7, 8]},
                    "timeout": {"type": "number", "minimum": 0.1},
                    "max_retries": {"type": "integer", "minimum": 0},
                    "retry_delay": {"type": "number", "minimum": 0},
                    "description": {"type": "string"},
                    "byteorder": {"type": "string", "enum": ["big", "little"]},
                    "wordorder": {"type": "string", "enum": ["big", "little"]},
                    "devices": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "address": {"type": "integer", "minimum": 1, "maximum": 247},
                                "enabled": {"type": "boolean"},
                                "poll_interval": {"type": "number", "minimum": 0.01},
                                "timeout": {"type": "number", "minimum": 0.1},
                                "description": {"type": "string"},
                                "registers": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "type": {"type": "string", "enum": ["holding", "input", "coil", "discrete"]},
                                            "address": {"type": "integer", "minimum": 0},
                                            "name": {"type": "string"},
                                            "description": {"type": "string"},
                                            "unit": {"type": "string"},
                                            "scale": {"type": "number"},
                                            "offset": {"type": "number"},
                                            "data_type": {"type": "string", "enum": ["int16", "uint16", "int32", "uint32", "float", "bool"]},
                                            "read_only": {"type": "boolean"},
                                            "precision": {"type": "integer", "minimum": 0},
                                            "byteorder": {"type": "string", "enum": ["big", "little"]},
                                            "wordorder": {"type": "string", "enum": ["big", "little"]}
                                        },
                                        "required": ["type", "address"]
                                    }
                                }
                            },
                            "required": ["name", "address"]
                        }
                    }
                },
                "required": ["type", "devices"]
            }
        },
        "additionalProperties": False
    }

    def __init__(self, main_config_path: str = "config/devices.yaml"):
        self.main_config_path = Path(main_config_path)
        self.config: AppConfig = None
        self.port_configs: Dict[str, PortConfig] = {}
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def load(self) -> AppConfig:
        """Загрузка конфигурации из файлов"""
        try:
            # Проверяем существование основного файла
            if not self.main_config_path.exists():
                raise FileNotFoundError(
                    f"Основной конфигурационный файл не найден: {self.main_config_path}"
                )

            # Загружаем основную конфигурацию
            main_config = self._load_yaml_file(self.main_config_path)

            # Валидация основной конфигурации
            self._validate_config(main_config, self.MAIN_CONFIG_SCHEMA, "основной конфигурации")

            # Загружаем конфигурацию портов
            ports_config = self._load_ports_config(main_config)

            # Валидация конфигурации портов
            self._validate_config(ports_config, self.PORTS_CONFIG_SCHEMA, "конфигурации портов")

            # Объединяем конфигурации
            full_config = {**main_config, "ports": ports_config}

            # Преобразуем в объект AppConfig
            self.config = self._create_app_config(full_config)

            # Парсим конфигурацию портов в объекты
            self._parse_ports_config(ports_config)

            # Выводим предупреждения если есть
            if self.warnings:
                for warning in self.warnings:
                    logger.warning(warning)

            # Выводим ошибки если есть
            if self.errors:
                error_msg = "\n".join(self.errors)
                raise ConfigValidationError(f"Ошибки в конфигурации:\n{error_msg}")

            logger.info(f"Конфигурация загружена из {self.main_config_path}")
            logger.info(f"Загружено {len(self.port_configs)} портов")

            return self.config

        except Exception as e:
            logger.error(f"Ошибка загрузки конфигурации: {e}")
            raise

    def _load_yaml_file(self, file_path: Path) -> Dict[str, Any]:
        """Загрузка YAML файла"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigValidationError(f"Ошибка парсинга YAML файла {file_path}: {e}")

    def _load_ports_config(self, main_config: Dict[str, Any]) -> Dict[str, Any]:
        """Загрузка конфигурации портов из отдельного файла или из основной конфигурации"""
        ports_config = {}

        # Проверяем, указан ли отдельный файл для портов
        ports_config_file = main_config.get("ports_config_file")

        if ports_config_file:
            # Загружаем из отдельного файла
            ports_file_path = Path(ports_config_file)

            # Если путь относительный, делаем его относительно основного файла
            if not ports_file_path.is_absolute():
                ports_file_path = self.main_config_path.parent / ports_file_path

            if not ports_file_path.exists():
                self.warnings.append(
                    f"Файл конфигурации портов не найден: {ports_file_path}. "
                    f"Используется конфигурация из основного файла."
                )
                ports_config = main_config.get("ports", {})
            else:
                ports_config = self._load_yaml_file(ports_file_path)
                logger.info(f"Конфигурация портов загружена из {ports_file_path}")
        else:
            # Используем порты из основной конфигурации
            ports_config = main_config.get("ports", {})
            logger.info("Конфигурация портов загружена из основного файла")

        return ports_config

    def _validate_config(self, config: Dict[str, Any], schema: Dict[str, Any], config_name: str):
        """Валидация конфигурации по JSON схеме"""
        try:
            jsonschema.validate(instance=config, schema=schema)
        except jsonschema.ValidationError as e:
            self.errors.append(f"Ошибка валидации {config_name}: {e.message}")
        except Exception as e:
            self.errors.append(f"Ошибка валидации {config_name}: {e}")

    def _create_app_config(self, config: Dict[str, Any]) -> AppConfig:
        """Создание объекта AppConfig из словаря"""
        try:
            server_data = config.get("server", {})
            database_data = config.get("database", {})
            polling_data = config.get("polling", {})
            websocket_data = config.get("websocket", {})
            api_data = config.get("api", {})
            cache_data = config.get("cache", {})
            security_data = config.get("security", {})
            logging_data = config.get("logging", {})
            monitoring_data = config.get("monitoring", {})
            notifications_data = config.get("notifications", {})
            export_data = config.get("export", {})

            return AppConfig(
                server=ServerConfig(**server_data),
                database=DatabaseConfig(**database_data),
                polling=PollingConfig(**polling_data),
                websocket=WebSocketConfig(**websocket_data),
                api=ApiConfig(**api_data),
                cache=CacheConfig(**cache_data),
                security=SecurityConfig(**security_data),
                logging=LoggingConfig(**logging_data),
                monitoring=MonitoringConfig(**monitoring_data),
                notifications=NotificationConfig(**notifications_data),
                export=ExportConfig(**export_data),
                ports_config_file=config.get("ports_config_file", "config/ports.yaml"),
                ports=config.get("ports", {})
            )
        except Exception as e:
            raise ConfigValidationError(f"Ошибка создания конфигурации: {e}")

    def _parse_ports_config(self, ports_config: Dict[str, Any]):
        """Парсинг конфигурации портов в объекты"""
        try:
            for port_name, port_data in ports_config.items():
                try:
                    port_config = self._parse_port_config(port_name, port_data)
                    if port_config:
                        self.port_configs[port_name] = port_config
                except Exception as e:
                    self.errors.append(f"Ошибка парсинга порта {port_name}: {e}")

            logger.info(f"Успешно распарсено {len(self.port_configs)} портов")

        except Exception as e:
            self.errors.append(f"Ошибка парсинга конфигурации портов: {e}")

    def _parse_port_config(self, port_name: str, port_data: Dict[str, Any]) -> Optional[PortConfig]:
        """Парсинг конфигурации порта"""
        try:
            port_type = ConnectionType(port_data["type"])

            # Проверка обязательных полей в зависимости от типа
            if port_type in [ConnectionType.TCP, ConnectionType.RTU_TCP]:
                if "host" not in port_data or "port" not in port_data:
                    self.errors.append(f"Порт {port_name}: host и port обязательны для типа {port_type}")
                    return None
            elif port_type == ConnectionType.RTU_SERIAL:
                if "port_name" not in port_data:
                    self.errors.append(f"Порт {port_name}: port_name обязателен для типа {port_type}")
                    return None

            # Парсинг устройств
            devices = []
            for device_data in port_data.get("devices", []):
                try:
                    device_config = self._parse_device_config(port_name, device_data)
                    if device_config:
                        devices.append(device_config)
                except Exception as e:
                    self.errors.append(f"Ошибка парсинга устройства {device_data.get('name', 'unknown')}: {e}")

            # Создание конфигурации порта
            return PortConfig(
                name=port_name,
                type=port_type,
                host=port_data.get("host"),
                port=port_data.get("port"),
                port_name=port_data.get("port_name"),
                baudrate=port_data.get("baudrate", 9600),
                parity=port_data.get("parity", "N"),
                stopbits=port_data.get("stopbits", 1),
                bytesize=port_data.get("bytesize", 8),
                timeout=port_data.get("timeout", 2.0),
                max_retries=port_data.get("max_retries", 3),
                retry_delay=port_data.get("retry_delay", 5.0),
                enabled=port_data.get("enabled", True),
                description=port_data.get("description", ""),
                byteorder=port_data.get("byteorder", "big"),
                wordorder=port_data.get("wordorder", "big"),
                devices=devices
            )

        except Exception as e:
            self.errors.append(f"Критическая ошибка парсинга порта {port_name}: {e}")
            return None

    def _parse_device_config(self, port_name: str, device_data: Dict[str, Any]) -> Optional[DeviceConfig]:
        """Парсинг конфигурации устройства"""
        try:
            # Парсинг регистров
            registers = []
            for reg_data in device_data.get("registers", []):
                try:
                    register_config = RegisterConfig(
                        type=RegisterType(reg_data["type"]),
                        address=reg_data["address"],
                        name=reg_data.get("name", f"register_{reg_data['address']}"),
                        description=reg_data.get("description", ""),
                        unit=reg_data.get("unit", ""),
                        scale=reg_data.get("scale", 1.0),
                        offset=reg_data.get("offset", 0.0),
                        data_type=DataType(reg_data.get("data_type", "uint16")),
                        read_only=reg_data.get("read_only", True),
                        precision=reg_data.get("precision", 2),
                        byteorder=reg_data.get("byteorder", "big"),
                        wordorder=reg_data.get("wordorder", "big")
                    )
                    registers.append(register_config)
                except Exception as e:
                    self.errors.append(f"Ошибка парсинга регистра {reg_data.get('address')}: {e}")

            # Создание конфигурации устройства
            return DeviceConfig(
                name=device_data["name"],
                address=device_data["address"],
                port_name=port_name,
                poll_interval=device_data.get("poll_interval", 1.0),
                timeout=device_data.get("timeout", 2.0),
                enabled=device_data.get("enabled", True),
                description=device_data.get("description", ""),
                registers=registers
            )

        except Exception as e:
            self.errors.append(f"Критическая ошибка парсинга устройства {device_data.get('name', 'unknown')}: {e}")
            return None

    def get_port_configs(self) -> Dict[str, PortConfig]:
        """Получение конфигураций портов"""
        return self.port_configs

    def get_app_config(self) -> AppConfig:
        """Получение полной конфигурации приложения"""
        return self.config

    def reload_ports_config(self) -> bool:
        """Перезагрузка конфигурации портов"""
        try:
            old_port_count = len(self.port_configs)

            # Загружаем основную конфигурацию
            main_config = self._load_yaml_file(self.main_config_path)

            # Загружаем конфигурацию портов
            ports_config = self._load_ports_config(main_config)

            # Валидируем
            self._validate_config(ports_config, self.PORTS_CONFIG_SCHEMA, "конфигурации портов (перезагрузка)")

            # Парсим заново
            self.port_configs.clear()
            self._parse_ports_config(ports_config)

            new_port_count = len(self.port_configs)
            logger.info(f"Конфигурация портов перезагружена. Порт(ов): {old_port_count} → {new_port_count}")

            return True

        except Exception as e:
            logger.error(f"Ошибка перезагрузки конфигурации портов: {e}")
            return False

    def export_config(self, output_path: str = "config/exported_config.yaml"):
        """Экспорт всей конфигурации в один файл"""
        try:
            # Используем dataclasses.asdict для правильного преобразования
            config_dict = {
                "server": asdict(self.config.server),
                "database": asdict(self.config.database),
                "polling": asdict(self.config.polling),
                "websocket": asdict(self.config.websocket),
                "api": asdict(self.config.api),
                "cache": asdict(self.config.cache),
                "security": asdict(self.config.security),
                "logging": asdict(self.config.logging),
                "monitoring": asdict(self.config.monitoring),
                "notifications": asdict(self.config.notifications),
                "export": asdict(self.config.export),
                "ports": {}
            }

            # Конвертируем порты в словари
            for port_name, port_config in self.port_configs.items():
                port_dict = asdict(port_config)
                # Преобразуем enum значения в строки
                port_dict["type"] = port_config.type.value

                # Обрабатываем устройства
                devices_list = []
                for device in port_config.devices:
                    device_dict = asdict(device)

                    # Обрабатываем регистры
                    registers_list = []
                    for register in device.registers:
                        register_dict = asdict(register)
                        register_dict["type"] = register.type.value
                        register_dict["data_type"] = register.data_type.value
                        registers_list.append(register_dict)

                    device_dict["registers"] = registers_list
                    devices_list.append(device_dict)

                port_dict["devices"] = devices_list
                config_dict["ports"][port_name] = port_dict

            # Сохраняем в файл
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

            logger.info(f"Конфигурация экспортирована в {output_path}")
            return True

        except Exception as e:
            logger.error(f"Ошибка экспорта конфигурации: {e}")
            return False