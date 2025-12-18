"""
REST API сервер
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, WebSocket, Query, Path, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from pydantic import BaseModel, Field

from .config_loader import AppConfig
from .models import (
    DeviceData, PortStatistics, CommandRequest,
    DeviceStatus, PortStatus
)
from .port_manager import PortManager
from .database import DatabaseManager
from .websocket_server import WebSocketManager
from .config_loader import ConfigLoader

logger = logging.getLogger(__name__)

class APIResponse(BaseModel):
    """Базовая модель ответа API"""
    success: bool
    message: str = ""
    data: Dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=datetime.now)

class DeviceInfo(BaseModel):
    """Информация об устройстве"""
    name: str
    address: int
    port_name: str
    poll_interval: float
    enabled: bool
    register_count: int
    last_seen: Optional[datetime] = None
    status: str = "unknown"

class PortInfo(BaseModel):
    """Информация о порте"""
    name: str
    type: str
    host: Optional[str] = None
    port: Optional[int] = None
    port_name: Optional[str] = None
    enabled: bool
    device_count: int
    status: str = "unknown"
    statistics: Optional[Dict[str, Any]] = None

class APIServer:
    """Сервер REST API"""

    def __init__(self, port_manager: PortManager,
                 database: DatabaseManager,
                 websocket_manager: WebSocketManager,
                 config: AppConfig):
        self.port_manager = port_manager
        self.database = database
        self.websocket_manager = websocket_manager
        self.config = config

        server_config = config.server
        self.host = server_config.host
        self.port = server_config.api_port

        self.app = FastAPI(
            title="Modbus Server API",
            description="API для управления и мониторинга Modbus сервера",
            version="1.0.0",
            docs_url="/api/docs",
            redoc_url="/api/redoc"
        )

        self.setup_middleware()
        self.setup_routes()

        logger.info(f"API сервер инициализирован на {self.host}:{self.port}")

    def setup_middleware(self):
        """Настройка middleware"""
        # CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # В production заменить на конкретные домены
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Логирование запросов
        @self.app.middleware("http")
        async def log_requests(request, call_next):
            start_time = datetime.now()

            response = await call_next(request)

            process_time = (datetime.now() - start_time).total_seconds() * 1000

            logger.info(
                f"{request.method} {request.url.path} "
                f"Status: {response.status_code} "
                f"Time: {process_time:.2f}ms"
            )

            return response

    def setup_routes(self):
        """Настройка маршрутов API"""

        # Health check
        @self.app.get("/api/health", tags=["System"])
        async def health_check():
            return APIResponse(
                success=True,
                message="Server is running",
                data={"status": "healthy", "timestamp": datetime.now().isoformat()}
            )

        # Получение информации о сервере
        @self.app.get("/api/server/info", tags=["System"])
        async def get_server_info():
            server_config = self.config.get("server", {})
            return APIResponse(
                success=True,
                data={
                    "version": "1.0.0",
                    "host": server_config.get("host"),
                    "api_port": server_config.get("api_port"),
                    "websocket_port": server_config.get("websocket_port"),
                    "start_time": datetime.now().isoformat(),
                    "config_file": "config/devices.yaml"
                }
            )

        # Получение списка портов
        @self.app.get("/api/ports", response_model=APIResponse, tags=["Ports"])
        async def get_ports():
            ports_info = []
            port_configs = self.port_manager.port_configs

            for port_name, port_config in port_configs.items():
                port_status = self.port_manager.get_port_status(port_name)

                port_info = PortInfo(
                    name=port_name,
                    type=port_config.type.value,
                    host=port_config.host,
                    port=port_config.port,
                    port_name=port_config.port_name,
                    enabled=port_config.enabled,
                    device_count=len(port_config.devices),
                    status=port_status.get("status", "unknown"),
                    statistics=port_status
                )
                ports_info.append(port_info.dict())

            return APIResponse(
                success=True,
                data={"ports": ports_info}
            )

        # Получение информации о порте
        @self.app.get("/api/ports/{port_name}", response_model=APIResponse, tags=["Ports"])
        async def get_port_info(
            port_name: str = Path(..., description="Имя порта")
        ):
            if port_name not in self.port_manager.port_configs:
                raise HTTPException(status_code=404, detail=f"Порт {port_name} не найден")

            port_config = self.port_manager.port_configs[port_name]
            port_status = self.port_manager.get_port_status(port_name)

            devices_info = []
            for device in port_config.devices:
                device_info = DeviceInfo(
                    name=device.name,
                    address=device.address,
                    port_name=port_name,
                    poll_interval=device.poll_interval,
                    enabled=device.enabled,
                    register_count=len(device.registers)
                )
                devices_info.append(device_info.dict())

            port_info = {
                "name": port_name,
                "type": port_config.type.value,
                "host": port_config.host,
                "port": port_config.port,
                "port_name": port_config.port_name,
                "enabled": port_config.enabled,
                "devices": devices_info,
                "status": port_status
            }

            return APIResponse(
                success=True,
                data=port_info
            )

        # Получение статистики порта
        @self.app.get("/api/ports/{port_name}/statistics", tags=["Ports"])
        async def get_port_statistics(
            port_name: str = Path(..., description="Имя порта"),
            hours: int = Query(24, description="Количество часов для статистики")
        ):
            if port_name not in self.port_manager.port_configs:
                raise HTTPException(status_code=404, detail=f"Порт {port_name} не найден")

            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)

            # Получаем статистику из БД
            async with self.database.get_session() as session:
                result = await session.execute(
                    text("""
                    SELECT
                        timestamp,
                        total_polls,
                        successful_polls,
                        failed_polls,
                        avg_response_time_ms,
                        active_devices
                    FROM port_statistics
                    WHERE port_name = :port_name
                    AND timestamp BETWEEN :start_time AND :end_time
                    ORDER BY timestamp
                    """),
                    {
                        "port_name": port_name,
                        "start_time": start_time,
                        "end_time": end_time
                    }
                )

                statistics = result.fetchall()

            # Формируем данные для ответа
            stats_data = []
            for stat in statistics:
                stats_data.append({
                    "timestamp": stat[0].isoformat(),
                    "total_polls": stat[1],
                    "successful_polls": stat[2],
                    "failed_polls": stat[3],
                    "avg_response_time_ms": stat[4],
                    "active_devices": stat[5]
                })

            return APIResponse(
                success=True,
                data={
                    "port_name": port_name,
                    "period": {
                        "start": start_time.isoformat(),
                        "end": end_time.isoformat()
                    },
                    "statistics": stats_data
                }
            )

        # Получение списка устройств
        @self.app.get("/api/devices", response_model=APIResponse, tags=["Devices"])
        async def get_devices():
            devices_info = []

            for port_name, port_config in self.port_manager.port_configs.items():
                for device in port_config.devices:
                    # Получаем последние данные устройства
                    latest_data = await self.database.get_latest_device_data(device.name)

                    device_info = DeviceInfo(
                        name=device.name,
                        address=device.address,
                        port_name=port_name,
                        poll_interval=device.poll_interval,
                        enabled=device.enabled,
                        register_count=len(device.registers),
                        last_seen=latest_data.get("timestamp") if latest_data else None,
                        status=latest_data.get("status", "unknown") if latest_data else "unknown"
                    )
                    devices_info.append(device_info.dict())

            return APIResponse(
                success=True,
                data={"devices": devices_info}
            )

        # Получение информации об устройстве
        @self.app.get("/api/devices/{device_name}", response_model=APIResponse, tags=["Devices"])
        async def get_device_info(
            device_name: str = Path(..., description="Имя устройства")
        ):
            # Поиск устройства в конфигурации
            device_config = None
            port_name = None

            for port_name_iter, port_config in self.port_manager.port_configs.items():
                for device in port_config.devices:
                    if device.name == device_name:
                        device_config = device
                        port_name = port_name_iter
                        break
                if device_config:
                    break

            if not device_config:
                raise HTTPException(status_code=404, detail=f"Устройство {device_name} не найдено")

            # Получаем последние данные
            latest_data = await self.database.get_latest_device_data(device_name)

            # Получаем статистику
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=24)
            statistics = await self.database.get_device_statistics(device_name, start_time, end_time)

            device_info = {
                "name": device_config.name,
                "address": device_config.address,
                "port_name": port_name,
                "poll_interval": device_config.poll_interval,
                "enabled": device_config.enabled,
                "registers": [
                    {
                        "type": reg.type.value,
                        "address": reg.address,
                        "name": reg.name,
                        "description": reg.description,
                        "unit": reg.unit,
                        "data_type": reg.data_type.value,
                        "read_only": reg.read_only,
                        "byteorder": reg.byteorder,
                        "wordorder": reg.wordorder
                    }
                    for reg in device_config.registers
                ],
                "latest_data": latest_data,
                "statistics": statistics
            }

            return APIResponse(
                success=True,
                data=device_info
            )

        # Получение истории устройства
        @self.app.get("/api/devices/{device_name}/history", tags=["Devices"])
        async def get_device_history(
            device_name: str = Path(..., description="Имя устройства"),
            start_time: datetime = Query(None, description="Начальное время"),
            end_time: datetime = Query(None, description="Конечное время"),
            limit: int = Query(1000, description="Лимит записей")
        ):
            if not start_time:
                start_time = datetime.now() - timedelta(hours=24)
            if not end_time:
                end_time = datetime.now()

            history = await self.database.get_device_history(
                device_name, start_time, end_time, limit
            )

            return APIResponse(
                success=True,
                data={
                    "device_name": device_name,
                    "period": {
                        "start": start_time.isoformat(),
                        "end": end_time.isoformat()
                    },
                    "history": history
                }
            )

        # Экспорт данных устройства в CSV
        @self.app.get("/api/devices/{device_name}/export", tags=["Devices"])
        async def export_device_data(
            device_name: str = Path(..., description="Имя устройства"),
            start_time: datetime = Query(None, description="Начальное время"),
            end_time: datetime = Query(None, description="Конечное время")
        ):
            if not start_time:
                start_time = datetime.now() - timedelta(days=1)
            if not end_time:
                end_time = datetime.now()

            # Генерируем имя файла
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{device_name}_{timestamp}.csv"
            filepath = f"exports/{filename}"

            # Экспортируем данные
            success = await self.database.export_to_csv(
                device_name, start_time, end_time, filepath
            )

            if success:
                return FileResponse(
                    filepath,
                    filename=filename,
                    media_type='text/csv'
                )
            else:
                raise HTTPException(status_code=500, detail="Ошибка экспорта данных")

        # Отправка команды устройству
        @self.app.post("/api/command", response_model=APIResponse, tags=["Control"])
        async def send_command(
            command: CommandRequest = Body(..., description="Команда")
        ):
            try:
                if command.command == "write_register":
                    if command.target != "device":
                        raise HTTPException(status_code=400, detail="Целью должен быть device")

                    params = command.params
                    required_params = ["port_name", "register_type", "address", "value"]

                    for param in required_params:
                        if param not in params:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Отсутствует параметр: {param}"
                            )

                    success = await self.port_manager.write_register(
                        port_name=params["port_name"],
                        device_name=command.name,
                        register_type=params["register_type"],
                        address=params["address"],
                        value=params["value"]
                    )

                    if success:
                        return APIResponse(
                            success=True,
                            message=f"Значение записано в устройство {command.name}"
                        )
                    else:
                        return APIResponse(
                            success=False,
                            message="Ошибка записи значения"
                        )

                elif command.command == "restart_port":
                    # Здесь будет логика перезапуска порта
                    return APIResponse(
                        success=False,
                        message="Команда в разработке"
                    )

                else:
                    raise HTTPException(status_code=400, detail=f"Неизвестная команда: {command.command}")

            except Exception as e:
                logger.error(f"Ошибка выполнения команды: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # Получение статистики WebSocket
        @self.app.get("/api/websocket/statistics", tags=["WebSocket"])
        async def get_websocket_statistics():
            stats = self.websocket_manager.get_statistics()
            return APIResponse(success=True, data=stats)

        # WebSocket endpoint для прямого подключения
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()

            try:
                # Здесь можно добавить прямую обработку WebSocket
                # или перенаправить к WebSocketManager
                while True:
                    data = await websocket.receive_text()
                    await websocket.send_text(f"Echo: {data}")
            except Exception as e:
                logger.error(f"Ошибка WebSocket: {e}")

    async def start(self):
        """Запуск API сервера"""
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=True
        )

        server = uvicorn.Server(config)

        logger.info(f"API сервер запущен на http://{self.host}:{self.port}")

        await server.serve()

    async def stop(self):
        """Остановка API сервера"""
        logger.info("API сервер остановлен")