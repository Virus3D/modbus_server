"""
Главный модуль Modbus сервера
"""
import asyncio
import signal
import logging
import sys
from pathlib import Path
from datetime import datetime
import traceback

# Добавляем текущую директорию в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config_loader import ConfigLoader, ConfigValidationError, AppConfig
from src.database import DatabaseManager
from src.port_manager import PortManager
from src.websocket_server import WebSocketManager
from src.api_server import APIServer
from src.utils import timing_decorator

class TracingFilter(logging.Filter):
    """Фильтр для добавления трассировки в логи"""
    def filter(self, record):
        if hasattr(record, 'trace') and record.trace:
            # Добавляем трассировку стека в сообщение
            stack = traceback.extract_stack()
            # Берем последние 5 вызовов (исключая сам фильтр)
            relevant_stack = stack[:-8] if len(stack) > 8 else stack
            trace_info = []
            for frame in relevant_stack[-5:]:  # Последние 5 вызовов
                if frame.filename and 'site-packages' not in frame.filename:
                    trace_info.append(f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}")

            if trace_info:
                record.msg = f"{record.msg} [Trace: {' <- '.join(trace_info)}]"

        # Добавляем информацию о задаче для асинхронного кода
        try:
            current_task = asyncio.current_task()
            if current_task:
                record.task_name = current_task.get_name()
        except:
            record.task_name = "main"

        return True

# Настройка логирования
def setup_logging(config: AppConfig, enable_trace: bool = True):
    """Настройка логирования"""
    log_config = config.server
    log_level = getattr(logging, log_config.log_level)
    log_file = log_config.log_file

    # Создаем директорию для логов
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

        # Расширенный формат логов для разработки
    if enable_trace:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - [%(task_name)s] - %(message)s"
    else:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    date_format = "%Y-%m-%d %H:%M:%S"

    # Сбрасываем существующие обработчики
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # Настройка корневого логгера
    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ],
        force=True  # Перезаписываем существующую конфигурацию
    )

    # Добавляем фильтр трассировки
    if enable_trace:
        trace_filter = TracingFilter()
        for handler in logging.root.handlers:
            handler.addFilter(trace_filter)

    # Устанавливаем уровень для библиотек
    logging.getLogger("pymodbus").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    # Включаем DEBUG для наших модулей при разработке
    if log_level == logging.DEBUG or enable_trace:
        logging.getLogger("src").setLevel(logging.DEBUG)
        logging.getLogger("__main__").setLevel(logging.DEBUG)
        # Логируем импорты и инициализацию
        logging.getLogger("src.config_loader").setLevel(logging.DEBUG)
        logging.getLogger("src.port_manager").setLevel(logging.DEBUG)

    logger = logging.getLogger(__name__)

    if enable_trace:
        logger.info("Логирование настроено с трассировкой для разработки")
    else:
        logger.info("Логирование настроено")

    return logger

class ModbusServer:
    """Главный класс Modbus сервера"""

    def __init__(self, config_path: str = "config/devices.yaml", enable_trace: bool = True):
        self.config_path = Path(config_path)
        self.config = None
        self.config_loader = None
        self.database = None
        self.port_manager = None
        self.websocket_manager = None
        self.api_server = None
        self.logger = None
        self.enable_trace = enable_trace

        # Флаг для graceful shutdown
        self.shutdown_event = asyncio.Event()

        # Регистрация обработчиков сигналов
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        """Обработчик сигналов завершения"""
        self.logger.info(f"Получен сигнал {signum}, инициируем graceful shutdown...")
        self.shutdown_event.set()

    async def initialize(self):
        """Инициализация всех компонентов"""
        try:
            # Загрузка конфигурации
            self.config_loader = ConfigLoader(self.config_path)
            self.config = self.config_loader.load()

            # Настройка логирования
            self.logger = setup_logging(self.config)

            self.logger.info("=" * 60)
            self.logger.info("Запуск Modbus сервера")
            self.logger.info("=" * 60)

            # Инициализация базы данных
            db_config = self.config.database
            self.database = DatabaseManager(
                database_url=db_config.url,
                pool_size=db_config.pool_size,
                max_overflow=db_config.max_overflow,
                echo=db_config.echo
            )

            await self.database.init_db()
            self.logger.info("База данных инициализирована")

            # Инициализация менеджера портов
            polling_config = self.config.polling
            self.port_manager = PortManager(self.database, polling_config)

            # Установка конфигураций портов
            port_configs = self.config_loader.get_port_configs()
            self.port_manager.set_port_configs(port_configs)

            # Инициализация WebSocket менеджера
            ws_config = self.config.websocket
            server_config = self.config.server
            self.websocket_manager = WebSocketManager(
                host=server_config.host,
                port=server_config.websocket_port,
                max_connections=ws_config.max_connections,
                ping_interval=ws_config.ping_interval,
                ping_timeout=ws_config.ping_timeout
            )

            # Инициализация API сервера
            self.api_server = APIServer(
                port_manager=self.port_manager,
                database=self.database,
                websocket_manager=self.websocket_manager,
                config=self.config
            )

            self.logger.info("Все компоненты инициализированы")

        except ConfigValidationError as e:
            self.logger.error(f"Ошибка конфигурации: {e}", extra={'trace': self.enable_trace})
            raise
        except Exception as e:
            self.logger.error(f"Ошибка инициализации: {e}", extra={'trace': self.enable_trace})
            raise

    @timing_decorator
    async def run(self):
        """Запуск сервера"""
        try:
            # Создаем задачи для всех компонентов
            tasks = [
                asyncio.create_task(self.port_manager.start(), name="port_manager"),
                asyncio.create_task(self.websocket_manager.start(), name="websocket"),
                asyncio.create_task(self.api_server.start(), name="api_server"),
                asyncio.create_task(self.monitoring_task(), name="monitoring"),
                asyncio.create_task(self.cleanup_task(), name="cleanup")
            ]

            self.logger.info("Сервер запущен")
            self.logger.info(f"REST API: http://{self.config.server.host}:{self.config.server.api_port}")
            self.logger.info(f"WebSocket: ws://{self.config.server.host}:{self.config.server.websocket_port}")

            # Ожидаем сигнала завершения
            await self.shutdown_event.wait()

            self.logger.info("Инициирована остановка сервера...")

            # Отменяем все задачи
            for task in tasks:
                if not task.done():
                    task.cancel()

            # Ожидаем завершения задач
            await asyncio.gather(*tasks, return_exceptions=True)

            # Останавливаем компоненты
            await self.port_manager.stop()
            await self.websocket_manager.stop()
            await self.database.close()

            self.logger.info("Сервер остановлен")

        except asyncio.CancelledError:
            self.logger.info("Задачи отменены")
        except Exception as e:
            self.logger.error(f"Ошибка во время работы сервера: {e}", extra={'trace': self.enable_trace})
            raise

    async def monitoring_task(self):
        """Задача мониторинга"""
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(60)  # Каждую минуту

                # Логирование статистики
                port_stats = self.port_manager.get_all_ports_status()

                for port_name, stats in port_stats.items():
                    if stats["status"] == "running":
                        self.logger.info(
                            f"Порт {port_name}: {stats.get('success_rate', 0):.1f}% успешных опросов, "
                            f"{stats.get('connected_devices', 0)} активных устройств"
                        )
                    elif stats["status"] == "error":
                        self.logger.warning(
                            f"Порт {port_name} в состоянии ошибки: {stats.get('last_error')}"
                        )

                # Сохранение статистики портов в БД
                for port_name in self.port_manager.port_configs.keys():
                    stats = self.port_manager.get_port_status(port_name)
                    await self.database.save_port_statistics(
                        port_name,
                        datetime.now(),
                        stats
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Ошибка в задаче мониторинга: {e}", extra={'trace': self.enable_trace})

    async def cleanup_task(self):
        """Задача очистки старых данных"""
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(3600)  # Каждый час

                # Очистка данных старше 30 дней
                await self.database.cleanup_old_data(days_to_keep=30)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Ошибка в задаче очистки: {e}", extra={'trace': self.enable_trace})

async def main():
    """Точка входа в приложение"""
    server = ModbusServer("config/devices.yaml")

    try:
        await server.initialize()
        await server.run()
    except KeyboardInterrupt:
        server.logger.info("Получен сигнал KeyboardInterrupt")
    except Exception as e:
        server.logger.error(f"Критическая ошибка: {e}", extra={'trace': self.enable_trace})
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())