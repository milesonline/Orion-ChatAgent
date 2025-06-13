import asyncio
import logging
from orion import Orion  

logger = logging.getLogger(__name__)

class OrionService:
    def __init__(self, server_script: str):
        self.orion = Orion()
        self.server_script = server_script
        self.connected = False
        #avoid parallel calls
        self.lock = asyncio.Lock()  

    async def connect(self):
        if not self.connected:
            await self.orion.connect_to_server(self.server_script)
            self.connected = True
            logger.info("Connected to MCP server")

    async def chat(self, query: str) -> str:
        async with self.lock:
            if not self.connected:
                await self.connect()
            response = await self.orion.process_query(query)
            return response
