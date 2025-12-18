"""
Менеджер портов Modbus
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from collections import defaultdict, deque
import contextlib

from pymodbus.client import AsyncModbusTcpClient, AsyncModbusSerialClient
from pymodbus.exceptions import ModbusException, ConnectionException
from pymodbus.pdu import ExceptionResponse
import serial

from .config_loader import PollingConfig

from .models import (
    PortConfig, DeviceConfig, RegisterConfig,
    DeviceData, RegisterData, DeviceStatus,
    PortStatus, ConnectionType, RegisterType
)
from .utils import DataConverter, async_retry, timing_decorator, create_device_key
from .database import DatabaseManager

logger = logging.getLogger(__name__)

class PortStatistics:
    """Статистика порта"""

    def __init__(self, port_name: str):
        self.port_name = port_name
        self.total_polls = 0
        self.successful_polls = 0
        self.failed_polls = 0
        self.response_times = deque(maxlen=100)  # Окно для расчета среднего
        self.last_success = None
        self.last_error = None
        self.error_count = 0
        self.connected_devices = set()
        self.device_stats = defaultdict(lambda: {
            "total_polls": 0,
            "successful_polls": 0,
            "failed_polls": 0,
            "last_response_time": 0.0
        })

    def record_poll(self, device_name: str, success: bool, response_time: float = 0.0):
        """Запись результата опроса"""
        self.total_polls += 1

        if device_name:
            device_stat = self.device_stats[device_name]
            device_stat["total_polls"] += 1

            if success:
                self.successful_polls += 1
                device_stat["successful_polls"] += 1
                device_stat["last_response_time"] = response_time
                self.last_success = datetime.now()
                self.connected_devices.add(device_name)
            else:
                self.failed_polls += 1
                device_stat["failed_polls"] += 1
                self.last_error = datetime.now()
                self.error_count += 1

        if response_time > 0:
            self.response_times.append(response_time)

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики в виде словаря"""
        avg_response_time = 0.0
        if self.response_times:
            avg_response_time = sum(self.response_times) / len(self.response_times)

        success_rate = 0.0
        if self.total_polls > 0:
            success_rate = (self.successful_polls / self.total_polls) * 100

        return {
            "port_name": self.port_name,
            "total_polls": self.total_polls,
            "successful_polls": self.successful_polls,
            "failed_polls": self.failed_polls,
            "success_rate": success_rate,
            "avg_response_time_ms": avg_response_time * 1000,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "error_count": self.error_count,
            "connected_devices": len(self.connected_devices),
            "device_stats": dict(self.device_stats)
        }

class PortManager:
    """Менеджер портов Modbus"""

    def __init__(self, database: DatabaseManager, config: PollingConfig):
        self.database = database
        self.config = config
        self.port_configs: Dict[str, PortConfig] = {}
        self.port_tasks: Dict[str, asyncio.Task] = {}
        self.port_clients: Dict[str, Any] = {}
        self.port_stats: Dict[str, PortStatistics] = {}
        self.running = False
        self.lock = asyncio.Lock()

        # Буфер для батчевой записи в БД
        self.data_buffer: List[DeviceData] = []
        self.buffer_size = config.buffer_size
        self.buffer_flush_interval = 5.0  # секунды
        self.last_flush = time.time()

        # Подписки WebSocket
        self.websocket_subscriptions: Dict[str, Set[str]] = defaultdict(set)

        logger.info("Менеджер портов инициализирован")

    def set_port_configs(self, port_configs: Dict[str, PortConfig]):
        """Установка конфигураций портов"""
        self.port_configs = port_configs

        # Инициализация статистики для каждого порта
        for port_name in port_configs.keys():
            self.port_stats[port_name] = PortStatistics(port_name)

        logger.info(f"Загружено {len(port_configs)} конфигураций портов")

    async def start(self):
        """Запуск менеджера портов"""
        self.running = True

        # Запуск задачи для сброса буфера
        asyncio.create_task(self.buffer_flush_task())

        # Запуск опроса для каждого порта
        for port_name, port_config in self.port_configs.items():
            if not port_config.enabled:
                logger.info(f"Порт {port_name} отключен в конфигурации, пропускаем")
                continue

            task = asyncio.create_task(
                self.poll_port(port_config),
                name=f"port_{port_name}"
            )
            self.port_tasks[port_name] = task

            logger.info(f"Запущен опрос порта {port_name} ({port_config.type.value})")

        logger.info(f"Запущено {len(self.port_tasks)} задач опроса портов")

    async def stop(self):
        """Остановка менеджера портов"""
        self.running = False

        # Остановка всех задач опроса
        tasks_to_cancel = []
        for port_name, task in self.port_tasks.items():
            if not task.done():
                task.cancel()
                tasks_to_cancel.append(task)
                logger.info(f"Остановка порта {port_name}")

        # Ожидание завершения задач
        if tasks_to_cancel:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        # Закрытие всех клиентов Modbus
        for port_name, client in self.port_clients.items():
            try:
                await client.close()
                logger.info(f"Закрыто соединение с портом {port_name}")
            except Exception as e:
                logger.error(f"Ошибка закрытия соединения с портом {port_name}: {e}")

        # Сброс буфера перед завершением
        await self.flush_buffer()

        logger.info("Менеджер портов остановлен")

    async def create_modbus_client(self, port_config: PortConfig) -> Optional[Any]:
        """Создание клиента Modbus"""
        try:
            if port_config.type in [ConnectionType.TCP, ConnectionType.RTU_TCP]:
                 # Определяем framer в зависимости от типа подключения
                if port_config.type == ConnectionType.RTU_TCP:
                    framer = "rtu"
                    framer_name = "RTU over TCP"
                else:
                    framer = "socket"
                    framer_name = "TCP"

                logger.info(f"Создание {framer_name} клиента для {port_config.name} "
                      f"({port_config.host}:{port_config.port})")
                # Параметры для AsyncModbusTcpClient (актуальные для pymodbus 3.x)
                client_params = {
                    "host": port_config.host,
                    "port": port_config.port,
                    "framer": framer,
                    "timeout": port_config.timeout,
                    "retries": 0,  # Повторы обрабатываем сами
                    "close_comm_on_error": False,
                    "strict": True,
                }

                # Пробуем создать клиент
                try:
                    client = AsyncModbusTcpClient(**client_params)
                except TypeError as e:
                    # Логируем ошибку и пробуем без проблемных параметров
                    logger.debug(f"Параметры вызывают ошибку: {e}")

                    # Попробуем с минимальным набором параметров
                    basic_params = {
                        "host": port_config.host,
                        "port": port_config.port,
                        "framer": framer,
                        "timeout": port_config.timeout,
                    }

                    try:
                        client = AsyncModbusTcpClient(**basic_params)
                        logger.info(f"Клиент создан с базовыми параметрами")
                    except Exception as e2:
                        logger.error(f"Ошибка создания клиента с базовыми параметрами: {e2}")
                        return None

            elif port_config.type == ConnectionType.RTU_SERIAL:
                # Параметры для последовательного порта

                serial_params = {
                    "port": port_config.port_name,
                    "baudrate": port_config.baudrate,
                    "parity": port_config.parity,
                    "stopbits": port_config.stopbits,
                    "bytesize": port_config.bytesize,
                    "timeout": port_config.timeout,
                    "retries": 0,
                    "close_comm_on_error": False,
                    "strict": True,
                }

                # Пробуем создать клиент с разными вариантами параметров
                try:
                    client = AsyncModbusSerialClient(**serial_params)
                    logger.info(f"Создан Serial клиент для {port_config.name} "
                          f"({port_config.port_name}, {port_config.baudrate} baud)")
                except TypeError as e:
                    logger.debug(f"Ошибка с полными параметрами: {e}")

                    # Вариант 2: только обязательные параметры
                    try:
                        client = AsyncModbusSerialClient(
                            port=port_config.port_name,
                            baudrate=port_config.baudrate,
                        )
                        # Устанавливаем остальные параметры после создания если возможно
                        if hasattr(client, 'bytesize'):
                            client.bytesize = port_config.bytesize
                        if hasattr(client, 'parity'):
                            client.parity = port_config.parity
                        if hasattr(client, 'stopbits'):
                            client.stopbits = port_config.stopbits
                        if hasattr(client, 'timeout'):
                            client.timeout = port_config.timeout

                    except Exception as e2:
                        logger.error(f"Не удалось создать serial клиент: {e2}")
                        return None
            else:
                logger.error(f"Неизвестный тип подключения: {port_config.type}")
                return None

            # Подключение
            logger.debug(f"Пытаемся подключиться к {port_config.name}...")

            try:
                connected = await client.connect()

                # В pymodbus 3.x client.connect() может не возвращать значение
                # или может быть корутиной без возврата. Проверяем состояние по-разному.
                if connected is not None:
                    # Если connect возвращает значение
                    if connected:
                        logger.info(f"✅ Успешно подключились к {port_config.name}")
                        return client
                    else:
                        logger.error(f"❌ Не удалось подключиться к {port_config.name}")
                        return None
                else:
                    # Если connect не возвращает значение, проверяем состояние клиента
                    await asyncio.sleep(0.1)  # Даем время на подключение

                    # Проверяем состояние подключения
                    if hasattr(client, 'connected'):
                        if client.connected:
                            logger.info(f"✅ Успешно подключились к {port_config.name} (через атрибут connected)")
                            return client
                        else:
                            logger.error(f"❌ Клиент {port_config.name} не подключен (атрибут connected=False)")
                            return None
                    else:
                        # Если нет атрибута connected, считаем что подключение успешно
                        logger.info(f"✅ Подключение к {port_config.name} (не удалось проверить состояние)")
                        return client

            except Exception as e:
                logger.error(f"Ошибка при подключении к {port_config.name}: {e}")
                return None

        except serial.SerialException as e:
            logger.error(f"Ошибка последовательного порта {port_config.name}: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка создания клиента {port_config.name}: {e}")
            logger.debug(f"Тип ошибки: {type(e).__name__}, Подробности: {str(e)}")
            return None

    @async_retry(max_retries=3, delay=2.0, exceptions=(ConnectionException,))
    async def poll_port(self, port_config: PortConfig):
        """Опрос порта (устройства опрашиваются последовательно)"""
        logger.info(f"Начало опроса порта {port_config.name}")

        retry_count = 0
        poll_cycle_count = 0

        while self.running and retry_count < port_config.max_retries:
            try:
                # Создание или получение клиента
                if port_config.name not in self.port_clients:
                    client = await self.create_modbus_client(port_config)
                    if not client:
                        raise ConnectionException(f"Не удалось создать клиент для {port_config.name}")
                    self.port_clients[port_config.name] = client
                    client_instance = client
                else:
                    client_instance = self.port_clients[port_config.name]
                    # Проверка соединения
                    if not client_instance.connected:
                        connected = await client_instance.connect()
                        if not connected:
                            raise ConnectionException(f"Соединение с {port_config.name} потеряно")

                # Сброс счетчика повторов при успешном соединении
                retry_count = 0

                # Последовательный опрос устройств на порту
                for device_config in port_config.devices:
                    if not self.running:
                        break

                    if not device_config.enabled:
                        continue

                    try:
                        # Опрос устройства
                        device_data = await self.poll_device(client, device_config, port_config)

                        if device_data:
                            # Добавление в буфер для сохранения
                            await self.add_to_buffer(device_data)

                            # Обновление статистики
                            self.port_stats[port_config.name].record_poll(
                                device_config.name,
                                True,
                                device_data.poll_time_ms / 1000.0
                            )

                            # Отправка через WebSocket если есть подписчики
                            await self.notify_websocket_subscribers(device_data)

                        # Задержка между устройствами
                        await asyncio.sleep(0.01)

                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"Ошибка опроса устройства {device_config.name}: {e}")
                        self.port_stats[port_config.name].record_poll(
                            device_config.name,
                            False
                        )
                        continue

                poll_cycle_count += 1

                # Логирование каждые 100 циклов
                if poll_cycle_count % 100 == 0:
                    stats = self.port_stats[port_config.name].get_stats()
                    logger.info(f"Порт {port_config.name}: {stats['success_rate']:.1f}% успешных опросов")

                # Динамическая задержка для поддержания интервала опроса
                await self.adjust_polling_interval(port_config)

            except asyncio.CancelledError:
                logger.info(f"Опрос порта {port_config.name} отменен")
                break
            except ConnectionException as e:
                logger.error(f"Ошибка соединения с портом {port_config.name}: {e}")
                retry_count += 1
                if retry_count < port_config.max_retries:
                    logger.info(f"Повторная попытка {retry_count} через {port_config.retry_delay}с")
                    await asyncio.sleep(port_config.retry_delay)
            except Exception as e:
                logger.error(f"Неожиданная ошибка порта {port_config.name}: {e}")
                retry_count += 1
                if retry_count < port_config.max_retries:
                    await asyncio.sleep(port_config.retry_delay)

        # Закрытие соединения при завершении
        if port_config.name in self.port_clients:
            client = self.port_clients.pop(port_config.name)
            client.close()

        if retry_count >= port_config.max_retries:
            logger.error(f"Превышено максимальное число попыток для порта {port_config.name}")

        logger.info(f"Опрос порта {port_config.name} завершен")

    @timing_decorator
    async def poll_device(self, client, device_config: DeviceConfig,
                         port_config: PortConfig) -> Optional[DeviceData]:
        """Опрос устройства"""
        start_time = time.perf_counter()

        try:
            # Группировка регистров по типам
            grouped_registers = self.group_registers_by_type(device_config.registers)

            registers_data = {}

            # Опрос регистров по типам
            for reg_type, reg_list in grouped_registers.items():
                # Группировка в непрерывные блоки
                blocks = self.group_registers_into_blocks(reg_list)

                for start_addr, count in blocks:
                    try:
                        # Чтение блока регистров
                        response = await self.read_register_block(
                            client, reg_type, start_addr, count, device_config.address
                        )

                        if response and not response.isError():
                            # Обработка прочитанных значений
                            self.process_register_block(
                                registers_data, reg_type, start_addr,
                                response, reg_list, port_config
                            )
                        else:
                            logger.warning(f"Ошибка чтения регистров {reg_type} "
                                         f"{start_addr}-{start_addr+count-1}")

                    except asyncio.TimeoutError:
                        logger.error(f"Таймаут чтения регистров устройства {device_config.name}")
                        break
                    except Exception as e:
                        logger.error(f"Ошибка чтения регистров: {e}")
                        continue

            # Если не удалось прочитать ни одного регистра
            if not registers_data:
                return None

            poll_time = (time.perf_counter() - start_time) * 1000  # в мс

            # Создание объекта данных устройства
            device_data = DeviceData(
                device_name=device_config.name,
                port_name=port_config.name,
                timestamp=datetime.now(),
                registers=registers_data,
                status=DeviceStatus.ONLINE,
                poll_time_ms=poll_time
            )

            return device_data

        except Exception as e:
            logger.error(f"Критическая ошибка опроса устройства {device_config.name}: {e}")
            return None

    def group_registers_by_type(self, registers: List[RegisterConfig]) -> Dict[str, List[RegisterConfig]]:
        """Группировка регистров по типам"""
        grouped = {}
        for reg in registers:
            if reg.type.value not in grouped:
                grouped[reg.type.value] = []
            grouped[reg.type.value].append(reg)
        return grouped

    def group_registers_into_blocks(self, registers: List[RegisterConfig]) -> List[tuple]:
        """Группировка регистров в непрерывные блоки"""
        if not registers:
            return []

        # Сортировка по адресу
        sorted_regs = sorted(registers, key=lambda x: x.address)

        blocks = []
        start_addr = sorted_regs[0].address
        count = 1

        for i in range(1, len(sorted_regs)):
            if sorted_regs[i].address == sorted_regs[i-1].address + 1:
                count += 1
            else:
                blocks.append((start_addr, count))
                start_addr = sorted_regs[i].address
                count = 1

        blocks.append((start_addr, count))
        return blocks

    async def read_register_block(self, client, reg_type: str, start_addr: int,
                                 count: int, slave: int):
        """Чтение блока регистров"""
        if reg_type == RegisterType.HOLDING.value:
            return await client.read_holding_registers(start_addr, count=count, device_id=slave)
        elif reg_type == RegisterType.INPUT.value:
            return await client.read_input_registers(start_addr, count, device_id=slave)
        elif reg_type == RegisterType.COIL.value:
            return await client.read_coils(start_addr, count, device_id=slave)
        elif reg_type == RegisterType.DISCRETE.value:
            return await client.read_discrete_inputs(start_addr, count, device_id=slave)
        else:
            raise ValueError(f"Неизвестный тип регистра: {reg_type}")

    def process_register_block(self, registers_data: Dict, reg_type: str, start_addr: int,
                              response, registers: List[RegisterConfig], port_config: PortConfig):
        """Обработка блока регистров с поддержкой порядка байт"""
        # Получение значений в зависимости от типа регистра
        if hasattr(response, 'registers'):  # Holding/Input регистры
            values = response.registers
        elif hasattr(response, 'bits'):  # Coil/Discrete регистры
            values = response.bits
        else:
            return

        # Создание словаря для быстрого поиска регистров по адресу
        reg_dict = {reg.address: reg for reg in registers}

        # Обработка каждого значения в блоке
        i = 0
        while i < len(values):
            reg_addr = start_addr + i
            if reg_addr in reg_dict:
                reg_info = reg_dict[reg_addr]

                # Определяем количество регистров для типа данных
                register_count = 1
                if reg_info.data_type.value in ["int32", "uint32", "float"]:
                    register_count = 2

                if i + register_count > len(values):
                    logger.warning(f"Недостаточно данных для регистра {reg_addr} типа {reg_info.data_type.value}")
                    break

                # Берем необходимое количество регистров
                register_values = values[i:i + register_count]

                # Конвертируем значение с учетом порядка байт
                if register_count == 1:
                    # 16-битные значения
                    raw_value = register_values[0]
                    value = DataConverter.convert_value(
                        raw_value,
                        reg_info.data_type.value,
                        reg_info.scale,
                        reg_info.offset,
                        reg_info.precision,
                        reg_info.byteorder,
                        reg_info.wordorder,
                    )
                else:
                    # 32-битные значения (два регистра)
                    value = DataConverter.convert_from_registers(
                        register_values,
                        reg_info.data_type.value,
                        reg_info.byteorder,
                        reg_info.wordorder,
                        scale=reg_info.scale,
                        offset=reg_info.offset,
                        precision=reg_info.precision
                    )

                # Создание ключа регистра
                reg_key = f"{reg_type}_{reg_addr:05d}"

                # Создание объекта данных регистра
                register_data = RegisterData(
                    value=value,
                    raw_value=raw_value if register_count == 1 else register_values,
                    unit=reg_info.unit,
                    description=reg_info.description,
                    timestamp=datetime.now(),
                    quality="good"
                )

                registers_data[reg_key] = register_data

                # Пропускаем обработанные регистры
                i += register_count
            else:
                i += 1

    async def adjust_polling_interval(self, port_config: PortConfig):
        """Корректировка интервала опроса"""
        if not port_config.devices:
            return

        # Находим минимальный интервал опроса среди устройств
        min_interval = min(device.poll_interval for device in port_config.devices
                          if device.enabled)

        # Примерное время опроса одного устройства
        estimated_device_time = 0.05  # секунды

        # Время цикла опроса всех устройств
        cycle_time = len(port_config.devices) * estimated_device_time

        # Вычисляем задержку между циклами
        if cycle_time < min_interval:
            delay = min_interval - cycle_time
            await asyncio.sleep(delay)
        else:
            logger.warning(f"Порт {port_config.name}: время цикла опроса ({cycle_time:.2f}с) "
                          f"превышает минимальный интервал ({min_interval}с)")

    async def add_to_buffer(self, device_data: DeviceData):
        """Добавление данных в буфер"""
        async with self.lock:
            self.data_buffer.append(device_data)

            # Сброс буфера при достижении размера
            if len(self.data_buffer) >= self.buffer_size:
                await self.flush_buffer()

    async def flush_buffer(self):
        """Сброс буфера в БД"""
        if not self.data_buffer:
            return

        async with self.lock:
            buffer_to_save = self.data_buffer.copy()
            self.data_buffer.clear()
            self.last_flush = time.time()

        # Сохранение данных в БД
        try:
            tasks = [self.database.save_device_data(data) for data in buffer_to_save]
            await asyncio.gather(*tasks, return_exceptions=True)

            logger.info(f"Сброшено {len(buffer_to_save)} записей в БД")

        except Exception as e:
            logger.error(f"Ошибка сброса буфера в БД: {e}")

    async def buffer_flush_task(self):
        """Задача для периодического сброса буфера"""
        while self.running:
            try:
                await asyncio.sleep(self.buffer_flush_interval)

                # Проверяем, не пора ли сбросить буфер
                if time.time() - self.last_flush >= self.buffer_flush_interval:
                    await self.flush_buffer()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в задаче сброса буфера: {e}")

    async def notify_websocket_subscribers(self, device_data: DeviceData):
        """Уведомление подписчиков WebSocket"""
        # Здесь будет логика отправки через WebSocket
        # В реальной реализации это будет обращение к WebSocketManager
        pass

    def get_port_status(self, port_name: str) -> Dict[str, Any]:
        """Получение статуса порта"""
        if port_name not in self.port_stats:
            return {"status": "unknown", "error": "Порт не найден"}

        stats = self.port_stats[port_name].get_stats()

        # Определение статуса порта
        if stats["error_count"] > 10 and stats["successful_polls"] == 0:
            status = PortStatus.ERROR.value
        elif stats["error_count"] > stats["successful_polls"] / 2:
            status = PortStatus.ERROR.value
        elif stats["connected_devices"] == 0:
            status = PortStatus.DISCONNECTED.value
        else:
            status = PortStatus.RUNNING.value

        return {
            "status": status,
            **stats
        }

    def get_all_ports_status(self) -> Dict[str, Dict[str, Any]]:
        """Получение статуса всех портов"""
        return {
            port_name: self.get_port_status(port_name)
            for port_name in self.port_configs.keys()
        }

    async def write_register(self, port_name: str, device_name: str,
                           register_type: str, address: int, value: Any) -> bool:
        """Запись значения в регистр"""
        try:
            if port_name not in self.port_clients:
                logger.error(f"Клиент порта {port_name} не найден")
                return False

            client = self.port_clients[port_name]

            # Поиск устройства
            port_config = self.port_configs.get(port_name)
            if not port_config:
                logger.error(f"Конфигурация порта {port_name} не найдена")
                return False

            device_config = next(
                (d for d in port_config.devices if d.name == device_name),
                None
            )

            if not device_config:
                logger.error(f"Устройство {device_name} не найдена")
                return False

            # Выполнение записи в зависимости от типа регистра
            if register_type == RegisterType.HOLDING.value:
                await client.write_register(address, int(value), unit=device_config.address)
            elif register_type == RegisterType.COIL.value:
                await client.write_coil(address, bool(value), unit=device_config.address)
            else:
                logger.error(f"Запись в регистр типа {register_type} не поддерживается")
                return False

            logger.info(f"Записано значение {value} в регистр {address} устройства {device_name}")
            return True

        except Exception as e:
            logger.error(f"Ошибка записи в регистр: {e}")
            return False