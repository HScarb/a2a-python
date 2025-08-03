import asyncio
import logging
from typing import Optional

import aio_pika
from aio_pika import connect_robust
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractQueue

from a2a.server.events.event_queue import Event
from a2a.server.request_handlers.rabbitmq_handler import RabbitMQHandler
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import AgentCard

logger = logging.getLogger(__name__)


class RabbitMQServerApp:
    """RabbitMQ server application that listens for incoming RPC requests."""

    def __init__(
        self, 
        agent_card: AgentCard, 
        request_handler: RequestHandler,
        rabbitmq_url: str | None = None
    ):
        """
        Initialize the RabbitMQ server application.
        
        Args:
            agent_card: The agent card containing RabbitMQ configuration.
            request_handler: The request handler to process incoming requests.
            rabbitmq_url: Optional RabbitMQ connection URL. If not provided,
                         will try to use the URL from the agent card.
        """
        if not agent_card.rabbitmq:
            raise ValueError("Agent card must contain RabbitMQ configuration")
        
        self._agent_card = agent_card
        self._rabbitmq_config = agent_card.rabbitmq
        self._request_handler = request_handler
        self._rabbitmq_handler = RabbitMQHandler(request_handler)
        
        # Use provided URL or fall back to agent card URL
        self._rabbitmq_url = rabbitmq_url or agent_card.url
        
        self._connection: Optional[AbstractConnection] = None
        self._channel: Optional[AbstractChannel] = None
        self._request_queue: Optional[AbstractQueue] = None
        self._streaming_exchange: Optional[aio_pika.Exchange] = None
        self._push_notification_exchange: Optional[aio_pika.Exchange] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._running = False

    async def run(self) -> None:
        """Start the RabbitMQ server and begin consuming requests."""
        if self._running:
            return
        
        # Use the provided RabbitMQ URL or agent card URL as fallback
        if not self._rabbitmq_url:
            raise ValueError("RabbitMQ URL must be provided either as parameter or in agent card")
        
        if not self._rabbitmq_url.startswith(('amqp://', 'amqps://')):
            raise ValueError(f"Invalid RabbitMQ URL: {self._rabbitmq_url}")

        logger.info(f"Connecting to RabbitMQ at {self._rabbitmq_url}")
        
        # Connect to RabbitMQ
        connection = await connect_robust(self._rabbitmq_url)
        self._connection = connection
        channel = await connection.channel()
        self._channel = channel
        
        # Set prefetch count for flow control
        await channel.set_qos(prefetch_count=10)
        
        # Declare core infrastructure
        await self._declare_infrastructure()
        
        # Set up the enhanced handler with channel access for streaming
        self._rabbitmq_handler._channel = channel
        self._rabbitmq_handler._streaming_exchange = self._streaming_exchange
        self._rabbitmq_handler._push_notification_exchange = self._push_notification_exchange
        
        # Start consuming requests
        if self._request_queue:
            logger.info(f"Starting to consume from queue: {self._rabbitmq_config.request_queue}")
            await self._request_queue.consume(self._rabbitmq_handler.handle_rpc_request)
        
        self._running = True
        logger.info("RabbitMQ server is running")

    async def _declare_infrastructure(self) -> None:
        """Declare queues and exchanges required for operation."""
        if not self._channel:
            raise RuntimeError("Not connected to RabbitMQ")
        
        # Declare RPC request queue
        self._request_queue = await self._channel.declare_queue(
            self._rabbitmq_config.request_queue,
            durable=True
        )
        
        # Declare streaming exchange if streaming is supported
        if (self._agent_card.capabilities and 
            self._agent_card.capabilities.streaming and 
            self._rabbitmq_config.streaming_exchange):
            
            self._streaming_exchange = await self._channel.declare_exchange(
                self._rabbitmq_config.streaming_exchange,
                aio_pika.ExchangeType.DIRECT,
                durable=True
            )
            logger.info(f"Declared streaming exchange: {self._rabbitmq_config.streaming_exchange}")
        
        # Declare push notification exchange if push notifications are supported
        if (self._agent_card.capabilities and 
            self._agent_card.capabilities.push_notifications and 
            self._rabbitmq_config.push_notification_exchange):
            
            self._push_notification_exchange = await self._channel.declare_exchange(
                self._rabbitmq_config.push_notification_exchange,
                aio_pika.ExchangeType.DIRECT,
                durable=True
            )
            logger.info(f"Declared push notification exchange: {self._rabbitmq_config.push_notification_exchange}")

    async def stop(self) -> None:
        """Gracefully stop the RabbitMQ server."""
        if not self._running:
            return
        
        logger.info("Stopping RabbitMQ server")
        self._running = False
        
        # Cancel consumer task if running
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        
        # Close channel and connection
        if self._channel and not self._channel.is_closed:
            await self._channel.close()
        
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
        
        logger.info("RabbitMQ server stopped")

    async def publish_stream_event(self, task_id: str, event: Event) -> None:
        """
        Publish a streaming event to the appropriate routing key.
        
        Args:
            task_id: The task ID to publish the event for.
            event: The event to publish.
        """
        if not self._streaming_exchange:
            return
        
        # Get routing key for this task
        routing_key = self._rabbitmq_handler._streaming_routes.get(task_id)
        if not routing_key:
            return
        
        # Serialize event
        import json
        event_data = event.model_dump() if hasattr(event, 'model_dump') else str(event)
        message_body = json.dumps(event_data).encode()
        
        # Create and publish message
        message = aio_pika.Message(message_body)
        await self._streaming_exchange.publish(message, routing_key=routing_key)

    async def publish_push_notification(self, routing_key: str, notification: dict) -> None:
        """
        Publish a push notification to the specified routing key.
        
        Args:
            routing_key: The routing key to publish to.
            notification: The notification data to publish.
        """
        if not self._push_notification_exchange:
            return
        
        # Serialize notification
        import json
        message_body = json.dumps(notification).encode()
        
        # Create and publish message
        message = aio_pika.Message(message_body)
        await self._push_notification_exchange.publish(message, routing_key=routing_key)

    @property
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return self._running
