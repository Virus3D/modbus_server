"""
Модели данных для Modbus сервера
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Union
from datetime import datetime
from pydantic import BaseModel, Field, validator
import json

class ConnectionType(str, Enum):
    """Типы Modbus подключений"""
    TCP = "tcp"
    RTU_TCP = "rtu_tcp"
    RTU_SERIAL = "rtu_serial"

class RegisterType(str, Enum):
    """Типы Modbus регистров"""
    HOLDING = "holding"
    INPUT = "input"
    COIL = "coil"
    DISCRETE = "discrete"

class DataType(str, Enum):
    """Типы данных регистров"""
    INT16 = "int16"
    UINT16 = "uint16"
    INT32 = "int32"
    UINT32 = "uint32"
    FLOAT = "float"
    BOOL = "bool"

class PortStatus(str, Enum):
    """Статусы портов"""
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"

class DeviceStatus(str, Enum):
    """Статусы устройств"""
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"
    TIMEOUT = "timeout"

@dataclass
class RegisterConfig:
    """Конфигурация регистра"""
    type: RegisterType
    address: int
    name: str = ""
    description: str = ""
    unit: str = ""
    scale: float = 1.0
    offset: float = 0.0
    data_type: DataType = DataType.UINT16
    read_only: bool = True
    precision: int = 2  # Точность округления
    byteorder: str = "big"  # "big" или "little"
    wordorder: str = "big"  # "big" или "little"

@dataclass
class DeviceConfig:
    """Конфигурация устройства"""
    name: str
    description: str
    address: int
    port_name: str
    poll_interval: float = 1.0
    timeout: float = 2.0
    enabled: bool = True
    registers: List[RegisterConfig] = field(default_factory=list)

@dataclass
class PortConfig:
    """Конфигурация порта"""
    name: str
    description: str
    type: ConnectionType
    host: Optional[str] = None
    port: Optional[int] = None
    port_name: Optional[str] = None
    baudrate: int = 9600
    parity: str = 'N'
    stopbits: int = 1
    bytesize: int = 8
    timeout: float = 2.0
    max_retries: int = 3
    retry_delay: float = 5.0
    enabled: bool = True
    devices: List[DeviceConfig] = field(default_factory=list)
    byteorder: str = "big"  # "big" или "little"
    wordorder: str = "big"  # "big" или "little"

# Pydantic модели для API
class RegisterData(BaseModel):
    """Данные регистра"""
    value: Union[int, float, bool]
    raw_value: int
    unit: str = ""
    description: str = ""
    timestamp: datetime
    quality: str = "good"  # good, bad, uncertain

class DeviceData(BaseModel):
    """Данные устройства"""
    device_name: str
    port_name: str
    timestamp: datetime
    registers: Dict[str, RegisterData]
    status: DeviceStatus = DeviceStatus.ONLINE
    poll_time_ms: float = 0.0

class PortStatistics(BaseModel):
    """Статистика порта"""
    port_name: str
    status: PortStatus
    total_polls: int = 0
    successful_polls: int = 0
    failed_polls: int = 0
    success_rate: float = 0.0
    avg_response_time_ms: float = 0.0
    last_success: Optional[datetime] = None
    last_error: Optional[datetime] = None
    error_message: Optional[str] = None
    connected_devices: int = 0
    total_devices: int = 0

class WebSocketMessage(BaseModel):
    """Сообщение WebSocket"""
    type: str  # data, status, error, command
    data: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.now)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class CommandRequest(BaseModel):
    """Запрос на выполнение команды"""
    command: str
    target: str  # port или device
    name: str
    params: Dict[str, Any] = {}