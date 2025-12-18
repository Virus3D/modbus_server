"""
Утилиты для Modbus сервера
"""
import asyncio
import json
import struct
import logging
import time
from typing import Dict, List, Any, Optional, Tuple, Union
from datetime import datetime, timedelta
from functools import wraps
import hashlib
from cachetools import TTLCache

logger = logging.getLogger(__name__)

class DataConverter:
    """Конвертер данных Modbus"""

    @staticmethod
    def int16_to_signed(value: int, byteorder: str = 'big') -> int:
        """Преобразование uint16 в int16 с поддержкой порядка байт"""
        if byteorder == 'little':
            # Для Little Endian меняем байты местами
            value = ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)

        if value >= 0x8000:
            return value - 0x10000
        return value

    @staticmethod
    def int32_from_registers(high: int, low: int, byteorder: str = 'big', wordorder: str = 'big') -> int:
        """Преобразование двух регистров в int32 с поддержкой порядка байт и слов"""
        # Применяем порядок слов (word order)
        if wordorder == 'little':
            high, low = low, high

        # Применяем порядок байт в каждом слове
        if byteorder == 'little':
            high = ((high & 0xFF) << 8) | ((high >> 8) & 0xFF)
            low = ((low & 0xFF) << 8) | ((low >> 8) & 0xFF)

        value = (high << 16) | low
        if value >= 0x80000000:
            return value - 0x100000000
        return value

    @staticmethod
    def uint32_from_registers(high: int, low: int, byteorder: str = 'big', wordorder: str = 'big') -> int:
        """Преобразование двух регистров в uint32 с поддержкой порядка байт и слов"""
        # Применяем порядок слов (word order)
        if wordorder == 'little':
            high, low = low, high

        # Применяем порядок байт в каждом слове
        if byteorder == 'little':
            high = ((high & 0xFF) << 8) | ((high >> 8) & 0xFF)
            low = ((low & 0xFF) << 8) | ((low >> 8) & 0xFF)

        return (high << 16) | low

    @staticmethod
    def float_from_registers(high: int, low: int, byteorder: str = 'big', wordorder: str = 'big') -> float:
        """Преобразование двух регистров в float (IEEE 754) с поддержкой порядка байт и слов"""
        # Применяем порядок слов (word order)
        if wordorder == 'little':
            high, low = low, high

        # Применяем порядок байт в каждом слове
        if byteorder == 'little':
            high = ((high & 0xFF) << 8) | ((high >> 8) & 0xFF)
            low = ((low & 0xFF) << 8) | ((low >> 8) & 0xFF)

        # Упаковка в 4 байта
        packed = struct.pack('>HH', high, low)
        # Распаковка как float
        return struct.unpack('>f', packed)[0]

    @staticmethod
    def convert_value(raw_value: int, data_type: str, scale: float = 1.0,
                      offset: float = 0.0, precision: int = 2,
                      byteorder: str = 'big', wordorder: str = 'big') -> Union[int, float, bool]:
        """Конвертация значения в соответствии с типом данных и порядком байт"""
        if data_type == "int16":
            value = DataConverter.int16_to_signed(raw_value, byteorder)
        elif data_type == "uint16":
            value = raw_value
            if byteorder == 'little':
                value = ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)
        elif data_type == "bool":
            value = bool(raw_value)
        else:
            # Для 32-битных и float значений требуются два регистра
            # Этот случай обрабатывается в port_manager.py
            value = raw_value

        # Применение масштабирования и смещения
        if isinstance(value, (int, float)):
            value = value * scale + offset
            if precision >= 0 and isinstance(value, float):
                value = round(value, precision)

        return value

    @staticmethod
    def convert_from_registers(registers: List[int], data_type: str,
                              byteorder: str = 'big', wordorder: str = 'big',
                              scale: float = 1.0, offset: float = 0.0,
                              precision: int = 2) -> Union[int, float]:
        """Конвертация массива регистров в значение"""
        if not registers:
            raise ValueError("Пустой массив регистров")

        if data_type in ["int16", "uint16", "bool"]:
            # Для 16-битных значений используем первый регистр
            raw_value = registers[0]
            return DataConverter.convert_value(raw_value, data_type, scale,
                                              offset, precision, byteorder, wordorder)

        elif data_type in ["int32", "uint32", "float"]:
            # Для 32-битных значений нужны два регистра
            if len(registers) < 2:
                raise ValueError(f"Для типа {data_type} требуется минимум 2 регистра")

            if data_type == "int32":
                value = DataConverter.int32_from_registers(
                    registers[0], registers[1], byteorder, wordorder
                )
            elif data_type == "uint32":
                value = DataConverter.uint32_from_registers(
                    registers[0], registers[1], byteorder, wordorder
                )
            elif data_type == "float":
                value = DataConverter.float_from_registers(
                    registers[0], registers[1], byteorder, wordorder
                )

            # Применяем масштабирование и смещение
            value = value * scale + offset
            if precision >= 0 and isinstance(value, float):
                value = round(value, precision)

            return value

        else:
            raise ValueError(f"Неизвестный тип данных: {data_type}")

    @staticmethod
    def swap_bytes_16(value: int) -> int:
        """Обмен байт в 16-битном значении"""
        return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)

    @staticmethod
    def swap_bytes_32(value: int) -> int:
        """Обмен байт в 32-битном значении"""
        return ((value & 0xFF000000) >> 24) | \
               ((value & 0x00FF0000) >> 8) | \
               ((value & 0x0000FF00) << 8) | \
               ((value & 0x000000FF) << 24)

    @staticmethod
    def register_to_bits(register_value: int, byteorder: str = 'big') -> str:
        """
        Преобразование значения регистра в битовую последовательность

        Args:
            register_value: значение регистра (0-65535)
            byteorder: порядок байт - 'big' (ABCD) или 'little' (BADC)

        Returns:
            Строка битов вида '0000000000000000' (16 бит)
        """
        if not 0 <= register_value <= 0xFFFF:
            raise ValueError(f"Значение регистра должно быть в диапазоне 0-65535, получено {register_value}")

        # Получаем байты регистра
        if byteorder == 'big':
            # Стандартный Modbus: старший байт первый
            byte1 = (register_value >> 8) & 0xFF  # Старший байт
            byte2 = register_value & 0xFF         # Младший байт
        elif byteorder == 'little':
            # Little Endian: младший байт первый
            byte1 = register_value & 0xFF         # Младший байт
            byte2 = (register_value >> 8) & 0xFF  # Старший байт
        else:
            raise ValueError(f"Неизвестный порядок байт: {byteorder}")

        # Преобразуем каждый байт в 8 бит
        bits1 = format(byte1, '08b')
        bits2 = format(byte2, '08b')

        # Собираем все 16 бит
        bits = bits1 + bits2

        return bits

    @staticmethod
    def register_bits_to_bytes(bits_str: str, byteorder: str = 'big') -> int:
        """
        Преобразование битовой последовательности в значение регистра

        Args:
            bits_str: строка битов вида '0000000000000000' (16 бит)
            byteorder: порядок байт - 'big' (ABCD) или 'little' (BADC)

        Returns:
            Значение регистра (0-65535)
        """
        if len(bits_str) != 16:
            raise ValueError(f"Строка битов должна содержать ровно 16 символов, получено {len(bits_str)}")

        # Разделяем на байты
        bits1 = bits_str[:8]
        bits2 = bits_str[8:]

        # Преобразуем биты в байты
        byte1 = int(bits1, 2)
        byte2 = int(bits2, 2)

        # Собираем в регистр в зависимости от порядка байт
        if byteorder == 'big':
            # Старший байт первый
            register_value = (byte1 << 8) | byte2
        elif byteorder == 'little':
            # Младший байт первый
            register_value = (byte2 << 8) | byte1
        else:
            raise ValueError(f"Неизвестный порядок байт: {byteorder}")

        return register_value

    @staticmethod
    def get_bit_from_register(register_value: int, bit_position: int,
                            byteorder: str = 'big') -> int:
        """
        Получение конкретного бита из регистра

        Args:
            register_value: значение регистра
            bit_position: позиция бита (0-15), где 0 - младший бит
            byteorder: порядок байт - 'big' (ABCD) или 'little' (BADC)

        Returns:
            Значение бита (0 или 1)
        """
        if not 0 <= bit_position <= 15:
            raise ValueError(f"Позиция бита должна быть в диапазоне 0-15, получено {bit_position}")

        # В зависимости от порядка байт определяем реальную позицию бита
        if byteorder == 'little':
            # Для Little Endian меняем порядок байт, но биты внутри байта остаются в том же порядке
            # Поэтому просто меняем байты местами
            register_value = ((register_value & 0xFF) << 8) | ((register_value >> 8) & 0xFF)

        # Получаем бит
        bit_value = (register_value >> bit_position) & 1

        return bit_value

    @staticmethod
    def set_bit_in_register(register_value: int, bit_position: int, bit_value: int,
                        byteorder: str = 'big') -> int:
        """
        Установка конкретного бита в регистре

        Args:
            register_value: исходное значение регистра
            bit_position: позиция бита (0-15)
            bit_value: значение бита (0 или 1)
            byteorder: порядок байт - 'big' (ABCD) или 'little' (BADC)

        Returns:
            Новое значение регистра
        """
        if not 0 <= bit_position <= 15:
            raise ValueError(f"Позиция бита должна быть в диапазоне 0-15, получено {bit_position}")

        if bit_value not in (0, 1):
            raise ValueError(f"Значение бита должно быть 0 или 1, получено {bit_value}")

        # Для Little Endian сначала меняем порядок байт
        if byteorder == 'little':
            register_value = ((register_value & 0xFF) << 8) | ((register_value >> 8) & 0xFF)

        # Устанавливаем бит
        if bit_value:
            register_value |= (1 << bit_position)
        else:
            register_value &= ~(1 << bit_position)

        # Возвращаем к исходному порядку байт если нужно
        if byteorder == 'little':
            register_value = ((register_value & 0xFF) << 8) | ((register_value >> 8) & 0xFF)

        return register_value

    @staticmethod
    def split_register_to_bytes(register_value: int, byteorder: str = 'big') -> tuple:
        """
        Разделение регистра на байты

        Args:
            register_value: значение регистра
            byteorder: порядок байт - 'big' (ABCD) или 'little' (BADC)

        Returns:
            Кортеж (старший_байт, младший_байт)
        """
        if byteorder == 'big':
            high_byte = (register_value >> 8) & 0xFF
            low_byte = register_value & 0xFF
        elif byteorder == 'little':
            low_byte = (register_value >> 8) & 0xFF
            high_byte = register_value & 0xFF
        else:
            raise ValueError(f"Неизвестный порядок байт: {byteorder}")

        return high_byte, low_byte

    @staticmethod
    def bytes_to_register(high_byte: int, low_byte: int, byteorder: str = 'big') -> int:
        """
        Объединение байтов в регистр

        Args:
            high_byte: старший байт (0-255)
            low_byte: младший байт (0-255)
            byteorder: порядок байт - 'big' (ABCD) или 'little' (BADC)

        Returns:
            Значение регистра
        """
        if not 0 <= high_byte <= 255:
            raise ValueError(f"Старший байт должен быть в диапазоне 0-255, получено {high_byte}")
        if not 0 <= low_byte <= 255:
            raise ValueError(f"Младший байт должен быть в диапазоне 0-255, получено {low_byte}")

        if byteorder == 'big':
            register_value = (high_byte << 8) | low_byte
        elif byteorder == 'little':
            register_value = (low_byte << 8) | high_byte
        else:
            raise ValueError(f"Неизвестный порядок байт: {byteorder}")

        return register_value

    @staticmethod
    def analyze_register_bits(register_value: int, byteorder: str = 'big') -> dict:
        """
        Анализ битов регистра

        Args:
            register_value: значение регистра
            byteorder: порядок байт

        Returns:
            Словарь с анализом битов
        """
        bits_str = DataConverter.register_to_bits(register_value, byteorder)

        analysis = {
            'hex': f"0x{register_value:04X}",
            'decimal': register_value,
            'bits': bits_str,
            'byte_order': byteorder,
            'bytes': {
                'high': (register_value >> 8) & 0xFF,
                'low': register_value & 0xFF
            },
            'bits_by_position': {}
        }

        # Анализ каждого бита
        for pos in range(16):
            bit_value = DataConverter.get_bit_from_register(register_value, pos, byteorder)
            analysis['bits_by_position'][pos] = {
                'value': bit_value,
                'weight': 2**pos,
                'description': f"Bit {pos}"
            }

        # Анализ флагов (если известны)
        flags = []

        # Пример флагов (можно расширить)
        if register_value == 0:
            flags.append("Все биты сброшены")
        if register_value == 0xFFFF:
            flags.append("Все биты установлены")
        if register_value & 0x8000:
            flags.append("Старший бит установлен")
        if register_value & 0x0001:
            flags.append("Младший бит установлен")

        analysis['flags'] = flags

        return analysis

class CacheManager:
    """Менеджер кэширования"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.caches = {}
        return cls._instance

    def get_cache(self, name: str, maxsize: int = 1000, ttl: int = 300) -> TTLCache:
        """Получение или создание кэша"""
        if name not in self.caches:
            self.caches[name] = TTLCache(maxsize=maxsize, ttl=ttl)
        return self.caches[name]

    async def get_or_set(self, cache_name: str, key: str, coroutine_func,
                        maxsize: int = 1000, ttl: int = 300) -> Any:
        """Получить из кэша или установить значение асинхронно"""
        cache = self.get_cache(cache_name, maxsize, ttl)

        if key in cache:
            return cache[key]

        value = await coroutine_func()
        cache[key] = value
        return value

def async_retry(max_retries: int = 3, delay: float = 1.0,
                exceptions: tuple = (Exception,)):
    """Декоратор для повторных попыток асинхронных функций"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        logger.warning(f"Попытка {attempt + 1} не удалась: {e}. "
                                     f"Повтор через {delay} секунд...")
                        await asyncio.sleep(delay)

            logger.error(f"Все {max_retries} попыток не удались")
            raise last_exception
        return wrapper
    return decorator

def timing_decorator(func):
    """Декоратор для измерения времени выполнения"""
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        try:
            result = await func(*args, **kwargs)
            return result
        finally:
            elapsed = time.perf_counter() - start_time
            logger.debug(f"{func.__name__} выполнено за {elapsed:.3f} секунд")

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - start_time
            logger.debug(f"{func.__name__} выполнено за {elapsed:.3f} секунд")

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

class CircularBuffer:
    """Циркулярный буфер для хранения данных"""

    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.buffer = []
        self.write_index = 0
        self.size = 0

    def append(self, item: Any):
        """Добавление элемента в буфер"""
        if self.size < self.max_size:
            self.buffer.append(item)
            self.size += 1
        else:
            self.buffer[self.write_index] = item
            self.write_index = (self.write_index + 1) % self.max_size

    def get_all(self) -> List[Any]:
        """Получение всех элементов"""
        if self.size < self.max_size:
            return self.buffer.copy()
        else:
            return self.buffer[self.write_index:] + self.buffer[:self.write_index]

    def clear(self):
        """Очистка буфера"""
        self.buffer.clear()
        self.write_index = 0
        self.size = 0

    def __len__(self):
        return self.size

def create_device_key(port_name: str, device_address: int) -> str:
    """Создание ключа для устройства"""
    return f"{port_name}:{device_address}"

def create_register_key(port_name: str, device_address: int,
                       register_type: str, register_address: int) -> str:
    """Создание ключа для регистра"""
    return f"{port_name}:{device_address}:{register_type}:{register_address}"

def calculate_hash(data: Any) -> str:
    """Вычисление хеша данных"""
    data_str = json.dumps(data, sort_keys=True, default=str)
    return hashlib.md5(data_str.encode()).hexdigest()