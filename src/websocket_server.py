"""
WebSocket сервер для реального времени
"""
import asyncio
import json
import logging
from typing import Dict, Set, List, Any, Optional
from datetime import datetime
from collections import defaultdict
import weakref

import websockets
from websockets.exceptions import ConnectionClosed
from websockets.server import WebSocketServerProtocol

from .models import WebSocketMessage, DeviceData

logger = logging.getLogger(__name__)

class WebSocketManager:
    """Менеджер WebSocket соединений"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765,
                 max_connections: int = 1000, ping_interval: int = 20,
                 ping_timeout: int = 10):
        self.host = host
        self.port = port
        self.max_connections = max_connections
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        # Подписки: device_name -> set(websocket)
        self.device_subscriptions: Dict[str, Set[WebSocketServerProtocol]] = defaultdict(set)

        # Подписки: websocket -> set(device_name)
        self.websocket_subscriptions: Dict[WebSocketServerProtocol, Set[str]] = defaultdict(set)

        # Очередь сообщений для каждого WebSocket
        self.message_queues: Dict[WebSocketServerProtocol, asyncio.Queue] = {}

        # Статистика
        self.connection_count = 0
        self.total_messages_sent = 0

        self.server = None
        self.running = False

        logger.info(f"WebSocketManager инициализирован на {host}:{port}")

    async def start(self):
        """Запуск WebSocket сервера"""
        self.running = True

        try:
            self.server = await websockets.serve(
                self.connection_handler,
                self.host,
                self.port,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
                max_size=2**20,  # 1MB max message size
                compression=None
            )

            logger.info(f"WebSocket сервер запущен на ws://{self.host}:{self.port}")

            # Запуск задачи очистки мертвых соединений
            asyncio.create_task(self.cleanup_dead_connections())

            await self.server.wait_closed()

        except Exception as e:
            logger.error(f"Ошибка запуска WebSocket сервера: {e}")
            raise

    async def stop(self):
        """Остановка WebSocket сервера"""
        self.running = False

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # Закрытие всех активных соединений
        for websocket in list(self.websocket_subscriptions.keys()):
            try:
                await websocket.close()
            except Exception:
                pass

        logger.info("WebSocket сервер остановлен")

    async def connection_handler(self, websocket: WebSocketServerProtocol, path: str):
        """Обработчик WebSocket соединений"""
        # Регистрация соединения
        self.connection_count += 1
        self.message_queues[websocket] = asyncio.Queue(maxsize=100)

        remote_addr = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        logger.info(f"Новое WebSocket соединение: {remote_addr} (всего: {self.connection_count})")

        try:
            # Запуск задачи отправки сообщений из очереди
            sender_task = asyncio.create_task(self.message_sender(websocket))

            # Обработка входящих сообщений
            async for message in websocket:
                try:
                    await self.handle_message(websocket, message)
                except json.JSONDecodeError:
                    await self.send_error(websocket, "Некорректный JSON")
                except Exception as e:
                    logger.error(f"Ошибка обработки сообщения: {e}")
                    await self.send_error(websocket, f"Внутренняя ошибка: {e}")

            await sender_task

        except ConnectionClosed as e:
            logger.info(f"WebSocket соединение закрыто: {remote_addr} - {e}")
        except Exception as e:
            logger.error(f"Ошибка WebSocket соединения {remote_addr}: {e}")
        finally:
            # Очистка при отключении
            await self.unregister_websocket(websocket)
            self.connection_count -= 1
            logger.info(f"WebSocket соединение завершено: {remote_addr}")

    async def message_sender(self, websocket: WebSocketServerProtocol):
        """Отправка сообщений из очереди"""
        queue = self.message_queues.get(websocket)
        if not queue:
            return

        try:
            while True:
                message = await queue.get()

                try:
                    await websocket.send(message)
                    self.total_messages_sent += 1
                except ConnectionClosed:
                    break
                except Exception as e:
                    logger.error(f"Ошибка отправки сообщения: {e}")
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Ошибка в message_sender: {e}")

    async def handle_message(self, websocket: WebSocketServerProtocol, message: str):
        """Обработка входящего сообщения"""
        data = json.loads(message)

        command = data.get("command")

        if command == "subscribe":
            device_names = data.get("devices", [])
            if isinstance(device_names, str):
                device_names = [device_names]

            for device_name in device_names:
                await self.subscribe(websocket, device_name)

            # Отправляем подтверждение
            response = WebSocketMessage(
                type="subscription",
                data={
                    "status": "subscribed",
                    "devices": device_names,
                    "timestamp": datetime.now().isoformat()
                }
            )
            await self.send_to_websocket(websocket, response)

        elif command == "unsubscribe":
            device_names = data.get("devices", [])
            if isinstance(device_names, str):
                device_names = [device_names]

            for device_name in device_names:
                await self.unsubscribe(websocket, device_name)

            response = WebSocketMessage(
                type="subscription",
                data={
                    "status": "unsubscribed",
                    "devices": device_names,
                    "timestamp": datetime.now().isoformat()
                }
            )
            await self.send_to_websocket(websocket, response)

        elif command == "list_subscriptions":
            devices = list(self.websocket_subscriptions.get(websocket, []))
            response = WebSocketMessage(
                type="subscription_list",
                data={
                    "devices": devices,
                    "timestamp": datetime.now().isoformat()
                }
            )
            await self.send_to_websocket(websocket, response)

        elif command == "ping":
            response = WebSocketMessage(
                type="pong",
                data={
                    "timestamp": datetime.now().isoformat(),
                    "server_time": datetime.now().timestamp()
                }
            )
            await self.send_to_websocket(websocket, response)

        else:
            await self.send_error(websocket, f"Неизвестная команда: {command}")

    async def subscribe(self, websocket: WebSocketServerProtocol, device_name: str):
        """Подписка на устройство"""
        self.device_subscriptions[device_name].add(websocket)
        self.websocket_subscriptions[websocket].add(device_name)

        logger.debug(f"WebSocket подписался на устройство {device_name}")

    async def unsubscribe(self, websocket: WebSocketServerProtocol, device_name: str):
        """Отписка от устройства"""
        if device_name in self.device_subscriptions:
            self.device_subscriptions[device_name].discard(websocket)
            # Удаляем пустые множества
            if not self.device_subscriptions[device_name]:
                del self.device_subscriptions[device_name]

        if websocket in self.websocket_subscriptions:
            self.websocket_subscriptions[websocket].discard(device_name)
            if not self.websocket_subscriptions[websocket]:
                del self.websocket_subscriptions[websocket]

        logger.debug(f"WebSocket отписался от устройства {device_name}")

    async def unregister_websocket(self, websocket: WebSocketServerProtocol):
        """Удаление WebSocket из всех подписок"""
        # Удаляем из подписок устройств
        for device_name in list(self.device_subscriptions.keys()):
            self.device_subscriptions[device_name].discard(websocket)
            if not self.device_subscriptions[device_name]:
                del self.device_subscriptions[device_name]

        # Удаляем подписки WebSocket
        if websocket in self.websocket_subscriptions:
            del self.websocket_subscriptions[websocket]

        # Удаляем очередь сообщений
        if websocket in self.message_queues:
            del self.message_queues[websocket]

    async def broadcast_device_data(self, device_data: DeviceData):
        """Трансляция данных устройства всем подписчикам"""
        if device_data.device_name not in self.device_subscriptions:
            return

        # Создание сообщения
        message = WebSocketMessage(
            type="device_data",
            data={
                "device": device_data.device_name,
                "port": device_data.port_name,
                "timestamp": device_data.timestamp.isoformat(),
                "registers": {
                    reg_name: {
                        "value": reg_data.value,
                        "unit": reg_data.unit,
                        "description": reg_data.description,
                        "quality": reg_data.quality
                    }
                    for reg_name, reg_data in device_data.registers.items()
                },
                "status": device_data.status.value,
                "poll_time_ms": device_data.poll_time_ms
            }
        )

        # Отправка всем подписчикам
        await self.broadcast_to_device(device_data.device_name, message)

    async def broadcast_to_device(self, device_name: str, message: WebSocketMessage):
        """Отправка сообщения всем подписчикам устройства"""
        if device_name not in self.device_subscriptions:
            return

        message_json = message.json()

        # Собираем задачи отправки
        send_tasks = []
        dead_websockets = []

        for websocket in list(self.device_subscriptions[device_name]):
            if websocket.open:
                queue = self.message_queues.get(websocket)
                if queue:
                    try:
                        queue.put_nowait(message_json)
                    except asyncio.QueueFull:
                        # Пропускаем сообщение если очередь полна
                        logger.warning(f"Очередь WebSocket полна, сообщение пропущено")
            else:
                dead_websockets.append(websocket)

        # Удаляем мертвые соединения
        for websocket in dead_websockets:
            await self.unregister_websocket(websocket)

    async def send_to_websocket(self, websocket: WebSocketServerProtocol, message: WebSocketMessage):
        """Отправка сообщения конкретному WebSocket"""
        if not websocket.open:
            return

        queue = self.message_queues.get(websocket)
        if queue:
            try:
                await queue.put(message.json())
            except asyncio.QueueFull:
                logger.warning(f"Очередь WebSocket полна, сообщение пропущено")

    async def send_error(self, websocket: WebSocketServerProtocol, error_message: str):
        """Отправка сообщения об ошибке"""
        message = WebSocketMessage(
            type="error",
            data={
                "message": error_message,
                "timestamp": datetime.now().isoformat()
            }
        )
        await self.send_to_websocket(websocket, message)

    async def cleanup_dead_connections(self):
        """Очистка мертвых соединений"""
        while self.running:
            try:
                await asyncio.sleep(60)  # Проверка каждую минуту

                dead_websockets = []
                for websocket in list(self.websocket_subscriptions.keys()):
                    if not websocket.open:
                        dead_websockets.append(websocket)

                for websocket in dead_websockets:
                    await self.unregister_websocket(websocket)

                if dead_websockets:
                    logger.info(f"Очищено {len(dead_websockets)} мертвых WebSocket соединений")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в cleanup_dead_connections: {e}")

    def get_statistics(self) -> Dict[str, Any]:
        """Получение статистики WebSocket сервера"""
        active_connections = len(self.websocket_subscriptions)
        total_subscriptions = sum(len(devices) for devices in self.device_subscriptions.values())

        return {
            "active_connections": active_connections,
            "total_connections": self.connection_count,
            "total_messages_sent": self.total_messages_sent,
            "device_subscriptions": total_subscriptions,
            "subscribed_devices": len(self.device_subscriptions),
            "queued_messages": sum(q.qsize() for q in self.message_queues.values())
        }