"""HTTP listener implementation for A2A server."""

import asyncio
import logging
from typing import Optional, Dict, Any

import uvicorn
from fastapi import FastAPI

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.tasks.push_notification_sender import BasePushNotificationSender
from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.types import AgentCard
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
    EXTENDED_AGENT_CARD_PATH,
)

from .base import ServerListener, ListenerError, ListenerStartError, ListenerStopError

logger = logging.getLogger(__name__)


class HTTPListener(ServerListener):
    """HTTP listener implementation using FastAPI.
    
    This listener wraps the existing FastAPI A2A application and provides
    a consistent interface with other transport listeners.
    """

    def __init__(
        self,
        agent_card: AgentCard,
        host: str = "0.0.0.0",
        port: int = 8000,
        extended_agent_card: Optional[AgentCard] = None,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
        extended_agent_card_url: str = EXTENDED_AGENT_CARD_PATH,
        uvicorn_config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize HTTP listener.
        
        Args:
            agent_card: The AgentCard describing the agent's capabilities
            host: Host to bind the server to
            port: Port to bind the server to
            extended_agent_card: Optional extended AgentCard for authenticated endpoints
            agent_card_url: URL path for the agent card endpoint
            rpc_url: URL path for the JSON-RPC endpoint
            extended_agent_card_url: URL path for the extended agent card endpoint
            uvicorn_config: Optional additional uvicorn configuration
        """
        self.agent_card = agent_card
        self.host = host
        self.port = port
        self.extended_agent_card = extended_agent_card
        self.agent_card_url = agent_card_url
        self.rpc_url = rpc_url
        self.extended_agent_card_url = extended_agent_card_url
        self.uvicorn_config = uvicorn_config or {}
        
        self._app: Optional[FastAPI] = None
        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[asyncio.Task] = None
        self._is_running = False

    async def start(
        self, 
        handler: RequestHandler,
        push_notification_sender: Optional[BasePushNotificationSender] = None
    ) -> None:
        """Start the HTTP listener."""
        if self._is_running:
            raise ListenerStartError("HTTP listener is already running", transport="HTTP")

        try:
            # Create FastAPI application
            a2a_app = A2AFastAPIApplication(
                agent_card=self.agent_card,
                http_handler=handler,
                extended_agent_card=self.extended_agent_card,
            )
            
            self._app = a2a_app.build(
                agent_card_url=self.agent_card_url,
                rpc_url=self.rpc_url,
                extended_agent_card_url=self.extended_agent_card_url,
            )

            # Configure uvicorn server
            config = uvicorn.Config(
                app=self._app,
                host=self.host,
                port=self.port,
                log_level="info",
                **self.uvicorn_config
            )

            self._server = uvicorn.Server(config)
            
            # Start server in background task
            self._server_task = asyncio.create_task(self._server.serve())
            self._is_running = True
            
            logger.info(f"HTTP listener started on {self.host}:{self.port}")

        except Exception as e:
            self._is_running = False
            raise ListenerStartError(f"Failed to start HTTP listener: {e}", transport="HTTP") from e

    async def stop(self) -> None:
        """Stop the HTTP listener."""
        if not self._is_running:
            return

        try:
            self._is_running = False
            
            if self._server:
                self._server.should_exit = True
                
            if self._server_task:
                # Wait for server to shutdown gracefully
                try:
                    await asyncio.wait_for(self._server_task, timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("HTTP server shutdown timeout, cancelling task")
                    self._server_task.cancel()
                    try:
                        await self._server_task
                    except asyncio.CancelledError:
                        pass

            self._server = None
            self._server_task = None
            self._app = None
            
            logger.info("HTTP listener stopped")

        except Exception as e:
            raise ListenerStopError(f"Failed to stop HTTP listener: {e}", transport="HTTP") from e

    def get_transport_info(self) -> Dict[str, Any]:
        """Get HTTP transport information."""
        return {
            "transport": "HTTP",
            "url": f"http://{self.host}:{self.port}{self.rpc_url}",
            "agent_card_url": f"http://{self.host}:{self.port}{self.agent_card_url}",
            "host": self.host,
            "port": self.port,
        }

    async def health_check(self) -> bool:
        """Check if HTTP listener is healthy."""
        return self._is_running and self._server is not None

    def is_running(self) -> bool:
        """Check if HTTP listener is running."""
        return self._is_running

    def get_app(self) -> Optional[FastAPI]:
        """Get the FastAPI application instance.
        
        Returns:
            The FastAPI app instance if the listener is running, None otherwise
        """
        return self._app