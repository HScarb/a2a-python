"""Example RabbitMQ server usage."""

import asyncio
import logging
from typing import Dict, Any

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


class ExampleRabbitMQHandler(DefaultRequestHandler):
    """Example request handler for RabbitMQ server."""
    
    async def on_message_send(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> Message:
        """Handle message/send requests."""
        logger.info(f"Received message: {params.message.content}")
        
        # Extract text from the message
        text_content = ""
        for content in params.message.content:
            if hasattr(content, 'text'):
                text_content += content.text + " "
        
        # Create a simple response
        response_text = f"Echo from RabbitMQ server: {text_content.strip()}"
        
        return Message(
            content=[TextContent(text=response_text)],
            timestamp=params.message.timestamp,
        )


async def main():
    """Run RabbitMQ server example."""
    
    # Create agent card
    agent_card = AgentCard(
        name="RabbitMQ Example Agent",
        description="An example A2A agent using RabbitMQ transport",
        version="1.0.0",
        provider=AgentProvider(
            organization="Example Org",
            url="https://example.com"
        ),
        interfaces=[
            AgentInterface(
                transport="RabbitMQ",
                url="amqp://guest:guest@localhost:5672/"
            )
        ]
    )
    
    # Configure RabbitMQ listener
    config = RabbitMQListenerConfig(
        connection_url="amqp://guest:guest@localhost:5672/",
        agent_id="example-agent",
        max_workers=5,
        prefetch_count=1,
    )
    
    # Create components
    listener = RabbitMQListener(config)
    handler = ExampleRabbitMQHandler()
    push_sender = RabbitMQPushNotificationSender(
        connection_url="amqp://guest:guest@localhost:5672/",
        max_retries=3,
    )
    
    try:
        # Start the server
        logger.info("Starting RabbitMQ server...")
        await listener.start(handler, push_sender)
        
        # Print transport info
        transport_info = listener.get_transport_info()
        logger.info(f"Server started with transport info: {transport_info}")
        
        # Keep the server running
        logger.info("Server is running. Press Ctrl+C to stop.")
        
        # Run health checks periodically
        while True:
            await asyncio.sleep(30)  # Health check every 30 seconds
            
            if await listener.health_check():
                logger.info("✅ Server health check passed")
            else:
                logger.warning("⚠️ Server health check failed")
                
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
        
    except Exception as e:
        logger.error(f"Server error: {e}")
        
    finally:
        # Clean up
        logger.info("Shutting down server...")
        await listener.stop()
        await push_sender.close()
        logger.info("Server stopped")


if __name__ == "__main__":
    asyncio.run(main())