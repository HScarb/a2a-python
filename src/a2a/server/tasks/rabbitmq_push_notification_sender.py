"""RabbitMQ-based push notification sender."""

import asyncio
import json
import logging
from typing import Any, Dict, Optional

try:
    import aio_pika
    from aio_pika import Connection, Channel, Message
except ImportError:
    raise ImportError(
        "RabbitMQ push notifications require 'aio-pika'. Install with: pip install 'a2a-sdk[rabbitmq]'"
    )

from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.types import TaskPushNotificationConfig

logger = logging.getLogger(__name__)


class RabbitMQPushNotificationSender(BasePushNotificationSender):
    """RabbitMQ-based push notification sender.
    
    This implementation sends push notifications by publishing messages
    to RabbitMQ queues specified in the push notification configuration.
    """

    def __init__(
        self,
        connection_url: str = "amqp://localhost",
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        """Initialize RabbitMQ push notification sender.
        
        Args:
            connection_url: RabbitMQ connection URL
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retry attempts in seconds
        """
        self.connection_url = connection_url
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._connection: Optional[Connection] = None
        self._channel: Optional[Channel] = None

    async def _ensure_connection(self) -> None:
        """Ensure we have a valid connection and channel."""
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(
                self.connection_url,
                client_properties={"connection_name": "a2a-push-notifications"}
            )
            self._channel = await self._connection.channel()

    async def send_notification(
        self,
        config: TaskPushNotificationConfig,
        notification_data: Dict[str, Any],
    ) -> bool:
        """Send a push notification via RabbitMQ.

        Args:
            config: Push notification configuration containing the queue name
            notification_data: The notification payload to send

        Returns:
            True if the notification was sent successfully, False otherwise
        """
        if not config.callback_url:
            logger.error("No callback_url specified in push notification config")
            return False

        # Extract queue name from callback_url
        # For RabbitMQ, callback_url should be the queue name
        queue_name = self._extract_queue_name(config.callback_url)
        if not queue_name:
            logger.error(f"Invalid callback_url format: {config.callback_url}")
            return False

        retry_count = 0
        while retry_count <= self.max_retries:
            try:
                await self._ensure_connection()
                
                # Declare the notification queue (make it durable)
                queue = await self._channel.declare_queue(
                    queue_name,
                    durable=True,
                    arguments={"x-message-ttl": 300000}  # 5 minutes TTL
                )

                # Prepare notification message
                message_body = {
                    "task_id": config.task_id,
                    "notification": notification_data,
                    "timestamp": notification_data.get("timestamp"),
                }

                # Add auth token to headers if present
                headers = {}
                if config.auth_token:
                    headers["authorization"] = f"Bearer {config.auth_token}"

                # Create and publish message
                message = Message(
                    json.dumps(message_body).encode(),
                    content_type="application/json",
                    headers=headers,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT  # Make message persistent
                )

                await self._channel.default_exchange.publish(
                    message,
                    routing_key=queue_name
                )

                logger.info(f"Push notification sent to queue {queue_name} for task {config.task_id}")
                return True

            except aio_pika.exceptions.AMQPException as e:
                retry_count += 1
                logger.warning(
                    f"AMQP error sending push notification (attempt {retry_count}/{self.max_retries + 1}): {e}"
                )
                
                if retry_count > self.max_retries:
                    logger.error(f"Failed to send push notification after {self.max_retries + 1} attempts")
                    return False
                
                # Wait before retrying
                await asyncio.sleep(self.retry_delay * retry_count)
                
                # Reset connection on error
                if self._connection and not self._connection.is_closed:
                    await self._connection.close()
                self._connection = None
                self._channel = None

            except Exception as e:
                logger.error(f"Unexpected error sending push notification: {e}")
                return False

        return False

    def _extract_queue_name(self, callback_url: str) -> Optional[str]:
        """Extract queue name from callback URL.
        
        Args:
            callback_url: The callback URL which should contain the queue name
            
        Returns:
            The queue name, or None if invalid format
        """
        # Handle different URL formats
        if callback_url.startswith("amqp://") or callback_url.startswith("rabbitmq://"):
            # Extract queue name from URL path
            try:
                from urllib.parse import urlparse
                parsed = urlparse(callback_url)
                # Queue name should be in the path, e.g., /queue/notifications.client.xyz
                path_parts = parsed.path.strip('/').split('/')
                if len(path_parts) >= 2 and path_parts[0] == "queue":
                    return path_parts[1]
            except Exception as e:
                logger.error(f"Error parsing callback URL {callback_url}: {e}")
                return None
        else:
            # Assume callback_url is the queue name directly
            return callback_url

        return None

    async def close(self) -> None:
        """Close the RabbitMQ connection."""
        if self._channel and not self._channel.is_closed:
            await self._channel.close()
            self._channel = None

        if self._connection and not self._connection.is_closed:
            await self._connection.close()
            self._connection = None

    async def health_check(self) -> bool:
        """Check if the push notification sender is healthy.
        
        Returns:
            True if the sender can connect to RabbitMQ, False otherwise
        """
        try:
            await self._ensure_connection()
            return (
                self._connection is not None and 
                not self._connection.is_closed and
                self._channel is not None and
                not self._channel.is_closed
            )
        except Exception as e:
            logger.debug(f"RabbitMQ push notification sender health check failed: {e}")
            return False

    def get_transport_info(self) -> Dict[str, Any]:
        """Get transport information for this sender.
        
        Returns:
            Dictionary containing transport information
        """
        return {
            "transport": "RabbitMQ",
            "connection_url": self.connection_url,
            "type": "push_notification_sender",
        }