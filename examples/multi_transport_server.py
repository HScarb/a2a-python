"""Example server supporting multiple transport protocols."""

import asyncio
import logging
from typing import Optional

from a2a.server.listeners.http_listener import HTTPListener
from a2a.server.listeners.rabbitmq_listener import RabbitMQListener, RabbitMQListenerConfig
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.server.tasks.rabbitmq_push_notification_sender import RabbitMQPushNotificationSender
from a2a.server.context import ServerCallContext
from a2a.types import (
    AgentCard,
    AgentProvider,
    AgentInterface,
    MessageSendParams,
    Message,
    MessageContent,
    TextContent
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MultiTransportHandler(DefaultRequestHandler):
    """Request handler that works with multiple transports."""
    
    async def on_message_send(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> Message:
        """Handle message/send requests from any transport."""
        # Get transport info from context
        transport = context.transport if context else "Unknown"
        
        logger.info(f"Received message via {transport}: {params.message.content}")
        
        # Extract text from the message
        text_content = ""
        for content in params.message.content:
            if hasattr(content, 'text'):
                text_content += content.text + " "
        
        # Create a response that includes transport info
        response_text = f"Response from {transport} server: {text_content.strip()}"
        
        return Message(
            content=[TextContent(text=response_text)],
            timestamp=params.message.timestamp,
        )


async def main():
    """Run multi-transport server example."""
    
    # Create agent card with multiple interfaces
    agent_card = AgentCard(
        name="Multi-Transport Example Agent",
        description="An example A2A agent supporting HTTP and RabbitMQ transports",
        version="1.0.0",
        provider=AgentProvider(
            organization="Example Org",
            url="https://example.com"
        ),
        interfaces=[
            AgentInterface(
                transport="HTTP",
                url="http://localhost:8000/a2a"
            ),
            AgentInterface(
                transport="RabbitMQ",
                url="amqp://guest:guest@localhost:5672/"
            )
        ]
    )
    
    # Create shared handler
    handler = MultiTransportHandler()
    
    # Create push notification sender (RabbitMQ-based)
    push_sender = RabbitMQPushNotificationSender(
        connection_url="amqp://guest:guest@localhost:5672/",
        max_retries=3,
    )
    
    # Configure HTTP listener
    http_listener = HTTPListener(
        agent_card=agent_card,
        host="0.0.0.0",
        port=8000,
    )
    
    # Configure RabbitMQ listener
    rabbitmq_config = RabbitMQListenerConfig(
        connection_url="amqp://guest:guest@localhost:5672/",
        agent_id="multi-transport-agent",
        max_workers=5,
        prefetch_count=1,
    )
    rabbitmq_listener = RabbitMQListener(rabbitmq_config)
    
    listeners = [http_listener, rabbitmq_listener]
    
    try:
        # Start all listeners
        logger.info("Starting multi-transport server...")
        
        start_tasks = []
        for listener in listeners:
            start_tasks.append(listener.start(handler, push_sender))
        
        await asyncio.gather(*start_tasks)
        
        # Print transport info for all listeners
        for listener in listeners:
            transport_info = listener.get_transport_info()
            logger.info(f"Started listener: {transport_info}")
        
        logger.info("Multi-transport server is running. Press Ctrl+C to stop.")
        logger.info("Available endpoints:")
        logger.info("  HTTP: http://localhost:8000/a2a")
        logger.info("  Agent Card: http://localhost:8000/.well-known/agent.json")
        logger.info("  RabbitMQ: amqp://guest:guest@localhost:5672/ (queue: agent.requests.multi-transport-agent)")
        
        # Keep the server running with periodic health checks
        while True:
            await asyncio.sleep(60)  # Health check every minute
            
            for listener in listeners:
                transport_info = listener.get_transport_info()
                transport_name = transport_info.get('transport', 'Unknown')
                
                if await listener.health_check():
                    logger.info(f"✅ {transport_name} listener health check passed")
                else:
                    logger.warning(f"⚠️ {transport_name} listener health check failed")
                    
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
        
    except Exception as e:
        logger.error(f"Server error: {e}")
        
    finally:
        # Clean up all listeners
        logger.info("Shutting down multi-transport server...")
        
        stop_tasks = []
        for listener in listeners:
            stop_tasks.append(listener.stop())
        
        await asyncio.gather(*stop_tasks, return_exceptions=True)
        await push_sender.close()
        
        logger.info("Multi-transport server stopped")


if __name__ == "__main__":
    asyncio.run(main())