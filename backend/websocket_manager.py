from typing import List
from fastapi import WebSocket

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

    def broadcast_sync(self, message: str, loop=None):
        import asyncio
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), loop)

manager = ConnectionManager()
