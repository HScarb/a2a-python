"""Example RabbitMQ client usage."""

import asyncio
import logging
from uuid import uuid4

from a2a.client.transport.rabbitmq_transport import RabbitMQTransport, RabbitMQTransportConfig
from a2a.client.transport_client import TransportA2AClient
from a2a.types import SendMessageRequest, MessageContent, TextContent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    """Demonstrate RabbitMQ client usage."""
    
    # Configure RabbitMQ transport
    config = RabbitMQTransportConfig(
        connection_url="amqp://guest:guest@localhost:5672/",
        agent_id="example-agent",
        request_timeout=30.0,
    )
    
    # Create transport and client
    transport = RabbitMQTransport(config)
    client = TransportA2AClient(transport)
    
    try:
        # Health check
        logger.info("Performing health check...")
        if await client.health_check():
            logger.info("✅ Client is healthy")
        else:
            logger.warning("⚠️ Client health check failed")
        
        # Example 1: Send a simple message
        logger.info("\n--- Example 1: Simple Message ---")
        
        message_request = SendMessageRequest(
            id=str(uuid4()),
            message=MessageContent(
                content=[TextContent(text="Hello from RabbitMQ client!")]
            )
        )
        
        response = await client.send_message(message_request)
        logger.info(f"Response: {response.model_dump_json(indent=2)}")
        
        # Example 2: Streaming message
        logger.info("\n--- Example 2: Streaming Message ---")
        
        streaming_request = SendMessageRequest(
            id=str(uuid4()),
            message=MessageContent(
                content=[TextContent(text="Tell me a story")]
            )
        )
        
        logger.info("Starting stream...")
        async for event in client.send_message_streaming(streaming_request):
            logger.info(f"Stream event: {event.model_dump_json()}")
            
            # Check if this is the final event
            if hasattr(event, 'final') and event.final:
                break
        
        logger.info("Stream completed")
        
        # Example 3: Setup push notifications
        logger.info("\n--- Example 3: Push Notifications Setup ---")
        
        notification_queue = f"notifications.client.{uuid4()}"
        success = await client.setup_push_notifications(
            callback_url=notification_queue,
            auth_token="optional-auth-token"
        )
        
        if success:
            logger.info(f"✅ Push notifications configured for queue: {notification_queue}")
            
            # Consume notifications (in a real app, this would run in the background)
            logger.info("Consuming push notifications for 10 seconds...")
            try:
                timeout_task = asyncio.create_task(asyncio.sleep(10))
                consume_task = asyncio.create_task(consume_notifications(transport))
                
                done, pending = await asyncio.wait(
                    [timeout_task, consume_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel any pending tasks
                for task in pending:
                    task.cancel()
                    
            except Exception as e:
                logger.error(f"Error consuming notifications: {e}")
        else:
            logger.error("❌ Failed to setup push notifications")
        
    except Exception as e:
        logger.error(f"Client error: {e}")
        
    finally:
        # Clean up
        logger.info("\nCleaning up...")
        await client.close()
        logger.info("Client closed")


async def consume_notifications(transport: RabbitMQTransport):
    """Consume push notifications from RabbitMQ."""
    try:
        async for notification in transport.consume_push_notifications():
            logger.info(f"📨 Received push notification: {notification}")
            
    except Exception as e:
        logger.error(f"Error consuming push notifications: {e}")


if __name__ == "__main__":
    asyncio.run(main())