"""Example HTTP transport client using the new transport layer."""

import asyncio
import logging
from uuid import uuid4

import httpx
from a2a.client.transport.http_transport import HttpTransport
from a2a.client.transport_client import TransportA2AClient
from a2a.types import SendMessageRequest, MessageContent, TextContent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    """Demonstrate HTTP transport client usage."""
    
    # Create HTTP transport
    async with httpx.AsyncClient() as httpx_client:
        transport = HttpTransport(
            base_url="http://localhost:8000/a2a",
            httpx_client=httpx_client,
            default_timeout=30.0,
        )
        
        # Create transport-based client
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
                    content=[TextContent(text="Hello from HTTP transport client!")]
                )
            )
            
            response = await client.send_message(message_request)
            logger.info(f"Response: {response.model_dump_json(indent=2)}")
            
            # Example 2: Streaming message (if server supports it)
            logger.info("\n--- Example 2: Streaming Message ---")
            
            streaming_request = SendMessageRequest(
                id=str(uuid4()),
                message=MessageContent(
                    content=[TextContent(text="Tell me a story")]
                )
            )
            
            try:
                logger.info("Starting stream...")
                event_count = 0
                async for event in client.send_message_streaming(streaming_request):
                    event_count += 1
                    logger.info(f"Stream event {event_count}: {event.model_dump_json()}")
                    
                    # Limit output for demo
                    if event_count >= 5:
                        logger.info("Stopping stream after 5 events...")
                        break
                
                logger.info("Stream completed")
                
            except Exception as e:
                logger.warning(f"Streaming not supported or error: {e}")
            
            # Example 3: Setup push notifications (HTTP callback)
            logger.info("\n--- Example 3: Push Notifications Setup ---")
            
            callback_url = "http://localhost:9000/webhook"
            success = await client.setup_push_notifications(
                callback_url=callback_url,
                auth_token="optional-webhook-token"
            )
            
            if success:
                logger.info(f"✅ Push notifications configured for: {callback_url}")
            else:
                logger.error("❌ Failed to setup push notifications")
            
            # Show transport info
            transport_info = client.get_transport_info()
            logger.info(f"\nTransport info: {transport_info}")
            
        except Exception as e:
            logger.error(f"Client error: {e}")
            
        finally:
            # Clean up
            logger.info("\nCleaning up...")
            await client.close()
            logger.info("Client closed")


if __name__ == "__main__":
    asyncio.run(main())