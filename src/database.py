"""
Модуль работы с базой данных
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, JSON, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import text
import pandas as pd

from .models import DeviceData, RegisterData

logger = logging.getLogger(__name__)

Base = declarative_base()

class DeviceReading(Base):
    """Модель для хранения показаний устройств"""
    __tablename__ = 'device_readings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_name = Column(String(100), nullable=False, index=True)
    port_name = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    data = Column(JSON, nullable=False)  # JSON с показаниями регистров
    status = Column(String(20), nullable=False)
    poll_time_ms = Column(Float, nullable=False)

    # Составной индекс для быстрого поиска по устройству и времени
    __table_args__ = (
        Index('idx_device_timestamp', 'device_name', 'timestamp'),
        Index('idx_port_timestamp', 'port_name', 'timestamp'),
    )

class DeviceStatusHistory(Base):
    """История статусов устройств"""
    __tablename__ = 'device_status_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_name = Column(String(100), nullable=False, index=True)
    port_name = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False)
    error_message = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)

class PortStatistics(Base):
    """Статистика портов"""
    __tablename__ = 'port_statistics'

    id = Column(Integer, primary_key=True, autoincrement=True)
    port_name = Column(String(50), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    total_polls = Column(Integer, nullable=False, default=0)
    successful_polls = Column(Integer, nullable=False, default=0)
    failed_polls = Column(Integer, nullable=False, default=0)
    avg_response_time_ms = Column(Float, nullable=False, default=0.0)
    active_devices = Column(Integer, nullable=False, default=0)
    total_devices = Column(Integer, nullable=False, default=0)

class DatabaseManager:
    """Менеджер базы данных"""

    def __init__(self, database_url: str, pool_size: int = 20,
                 max_overflow: int = 30, echo: bool = False):
        self.database_url = database_url
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.echo = echo

        # Создание асинхронного движка
        self.engine = create_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            echo=echo,
            pool_pre_ping=True  # Проверка соединений перед использованием
        )

        # Создание фабрики сессий
        self.async_session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

        self.connection_pool = None

    async def init_db(self):
        """Инициализация базы данных (создание таблиц)"""
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Таблицы базы данных созданы")

            # Создание таблицы временных рядов если используем TimescaleDB
            await self.create_hypertable()

        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}")
            raise

    async def create_hypertable(self):
        """Создание hypertable для TimescaleDB"""
        try:
            async with self.engine.begin() as conn:
                # Проверяем, является ли БД TimescaleDB
                result = await conn.execute(
                    text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')")
                )
                has_timescaledb = result.scalar()

                if has_timescaledb:
                    # Создаем hypertable для device_readings
                    await conn.execute(
                        text("""
                        SELECT create_hypertable(
                            'device_readings',
                            'timestamp',
                            if_not_exists => TRUE,
                            chunk_time_interval => interval '1 day'
                        )
                        """)
                    )

                    # Создаем hypertable для других таблиц
                    for table in ['device_status_history', 'port_statistics']:
                        await conn.execute(
                            text(f"""
                            SELECT create_hypertable(
                                '{table}',
                                'timestamp',
                                if_not_exists => TRUE,
                                chunk_time_interval => interval '7 days'
                            )
                            """)
                        )

                    # Создаем индексы для оптимизации запросов
                    await conn.execute(
                        text("""
                        CREATE INDEX IF NOT EXISTS idx_device_readings_device_time
                        ON device_readings (device_name, timestamp DESC)
                        """)
                    )

                    logger.info("Hypertables созданы для TimescaleDB")

        except Exception as e:
            logger.warning(f"Не удалось создать hypertable (возможно, не TimescaleDB): {e}")

    @asynccontextmanager
    async def get_session(self):
        """Контекстный менеджер для получения сессии"""
        async with self.async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Ошибка сессии БД: {e}")
                raise

    async def save_device_data(self, device_data: DeviceData):
        """Сохранение данных устройства"""
        try:
            async with self.get_session() as session:
                # Преобразуем регистры в JSON
                registers_json = {
                    reg_name: {
                        "value": reg_data.value,
                        "raw_value": reg_data.raw_value,
                        "unit": reg_data.unit,
                        "description": reg_data.description,
                        "quality": reg_data.quality
                    }
                    for reg_name, reg_data in device_data.registers.items()
                }

                reading = DeviceReading(
                    device_name=device_data.device_name,
                    port_name=device_data.port_name,
                    timestamp=device_data.timestamp,
                    data=registers_json,
                    status=device_data.status.value,
                    poll_time_ms=device_data.poll_time_ms
                )

                session.add(reading)

                # Также обновляем историю статусов если статус изменился
                await self.update_device_status_history(
                    session,
                    device_data.device_name,
                    device_data.port_name,
                    device_data.timestamp,
                    device_data.status
                )

                logger.debug(f"Сохранены данные устройства {device_data.device_name}")

        except Exception as e:
            logger.error(f"Ошибка сохранения данных устройства {device_data.device_name}: {e}")

    async def update_device_status_history(self, session: AsyncSession,
                                         device_name: str, port_name: str,
                                         timestamp: datetime, status: str):
        """Обновление истории статусов устройства"""
        try:
            # Получаем последний статус
            result = await session.execute(
                text("""
                SELECT status, timestamp FROM device_status_history
                WHERE device_name = :device_name
                ORDER BY timestamp DESC
                LIMIT 1
                """),
                {"device_name": device_name}
            )

            last_status = result.fetchone()

            if last_status:
                last_status_val, last_timestamp = last_status

                # Если статус изменился, закрываем предыдущую запись
                if last_status_val != status:
                    # Обновляем продолжительность предыдущего статуса
                    await session.execute(
                        text("""
                        UPDATE device_status_history
                        SET duration_seconds = EXTRACT(EPOCH FROM (:timestamp - timestamp))
                        WHERE device_name = :device_name
                        AND timestamp = :last_timestamp
                        """),
                        {
                            "device_name": device_name,
                            "timestamp": timestamp,
                            "last_timestamp": last_timestamp
                        }
                    )

            # Добавляем новую запись статуса
            status_history = DeviceStatusHistory(
                device_name=device_name,
                port_name=port_name,
                timestamp=timestamp,
                status=status
            )
            session.add(status_history)

        except Exception as e:
            logger.error(f"Ошибка обновления истории статусов {device_name}: {e}")

    async def save_port_statistics(self, port_name: str, timestamp: datetime,
                                 statistics: Dict[str, Any]):
        """Сохранение статистики порта"""
        try:
            async with self.get_session() as session:
                stats = PortStatistics(
                    port_name=port_name,
                    timestamp=timestamp,
                    total_polls=statistics.get("total_polls", 0),
                    successful_polls=statistics.get("successful_polls", 0),
                    failed_polls=statistics.get("failed_polls", 0),
                    avg_response_time_ms=statistics.get("avg_response_time_ms", 0.0),
                    active_devices=statistics.get("active_devices", 0),
                    total_devices=statistics.get("total_devices", 0)
                )
                session.add(stats)

        except Exception as e:
            logger.error(f"Ошибка сохранения статистики порта {port_name}: {e}")

    async def get_device_history(self, device_name: str,
                               start_time: datetime,
                               end_time: datetime,
                               limit: int = 1000) -> List[Dict[str, Any]]:
        """Получение истории показаний устройства"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text("""
                    SELECT timestamp, data, status, poll_time_ms
                    FROM device_readings
                    WHERE device_name = :device_name
                    AND timestamp BETWEEN :start_time AND :end_time
                    ORDER BY timestamp DESC
                    LIMIT :limit
                    """),
                    {
                        "device_name": device_name,
                        "start_time": start_time,
                        "end_time": end_time,
                        "limit": limit
                    }
                )

                rows = result.fetchall()

                # Преобразуем в список словарей
                history = []
                for row in rows:
                    history.append({
                        "timestamp": row[0],
                        "data": row[1],
                        "status": row[2],
                        "poll_time_ms": row[3]
                    })

                return history

        except Exception as e:
            logger.error(f"Ошибка получения истории устройства {device_name}: {e}")
            return []

    async def get_device_statistics(self, device_name: str,
                                  start_time: datetime,
                                  end_time: datetime) -> Dict[str, Any]:
        """Получение статистики устройства за период"""
        try:
            async with self.get_session() as session:
                # Основная статистика
                result = await session.execute(
                    text("""
                    SELECT
                        COUNT(*) as total_readings,
                        AVG(poll_time_ms) as avg_poll_time,
                        MIN(poll_time_ms) as min_poll_time,
                        MAX(poll_time_ms) as max_poll_time,
                        SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END) as online_count,
                        SUM(CASE WHEN status = 'offline' THEN 1 ELSE 0 END) as offline_count,
                        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count
                    FROM device_readings
                    WHERE device_name = :device_name
                    AND timestamp BETWEEN :start_time AND :end_time
                    """),
                    {
                        "device_name": device_name,
                        "start_time": start_time,
                        "end_time": end_time
                    }
                )

                stats = result.fetchone()

                if stats:
                    return {
                        "total_readings": stats[0] or 0,
                        "avg_poll_time_ms": float(stats[1] or 0),
                        "min_poll_time_ms": float(stats[2] or 0),
                        "max_poll_time_ms": float(stats[3] or 0),
                        "availability_percent": (stats[4] or 0) / max(stats[0] or 1, 1) * 100
                    }
                else:
                    return {}

        except Exception as e:
            logger.error(f"Ошибка получения статистики устройства {device_name}: {e}")
            return {}

    async def get_latest_device_data(self, device_name: str) -> Optional[Dict[str, Any]]:
        """Получение последних данных устройства"""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    text("""
                    SELECT timestamp, data, status, poll_time_ms
                    FROM device_readings
                    WHERE device_name = :device_name
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """),
                    {"device_name": device_name}
                )

                row = result.fetchone()
                if row:
                    return {
                        "timestamp": row[0],
                        "data": row[1],
                        "status": row[2],
                        "poll_time_ms": row[3]
                    }
                return None

        except Exception as e:
            logger.error(f"Ошибка получения последних данных устройства {device_name}: {e}")
            return None

    async def cleanup_old_data(self, days_to_keep: int = 30):
        """Очистка старых данных"""
        try:
            cutoff_date = datetime.now() - timedelta(days=days_to_keep)

            async with self.get_session() as session:
                # Удаляем старые данные из всех таблиц
                for table in ['device_readings', 'device_status_history', 'port_statistics']:
                    await session.execute(
                        text(f"DELETE FROM {table} WHERE timestamp < :cutoff_date"),
                        {"cutoff_date": cutoff_date}
                    )

                await session.commit()

                logger.info(f"Очищены данные старше {days_to_keep} дней")

        except Exception as e:
            logger.error(f"Ошибка очистки старых данных: {e}")

    async def export_to_csv(self, device_name: str,
                          start_time: datetime,
                          end_time: datetime,
                          output_path: str):
        """Экспорт данных устройства в CSV"""
        try:
            history = await self.get_device_history(device_name, start_time, end_time, limit=100000)

            if not history:
                logger.warning(f"Нет данных для экспорта устройства {device_name}")
                return False

            # Преобразуем в DataFrame
            rows = []
            for entry in history:
                base_row = {
                    "timestamp": entry["timestamp"],
                    "status": entry["status"],
                    "poll_time_ms": entry["poll_time_ms"]
                }

                # Добавляем данные регистров
                for reg_name, reg_data in entry["data"].items():
                    base_row[f"{reg_name}_value"] = reg_data["value"]
                    base_row[f"{reg_name}_raw"] = reg_data["raw_value"]
                    base_row[f"{reg_name}_unit"] = reg_data["unit"]

                rows.append(base_row)

            df = pd.DataFrame(rows)
            df.to_csv(output_path, index=False, encoding='utf-8')

            logger.info(f"Данные экспортированы в {output_path}")
            return True

        except Exception as e:
            logger.error(f"Ошибка экспорта данных устройства {device_name}: {e}")
            return False

    async def close(self):
        """Закрытие соединений с БД"""
        try:
            await self.engine.dispose()
            logger.info("Соединения с БД закрыты")
        except Exception as e:
            logger.error(f"Ошибка закрытия соединений БД: {e}")