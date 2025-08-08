"""Kafka request handler for A2A server."""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard,
    DeleteTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import ServerError

logger = logging.getLogger(__name__)


class KafkaMessage:
    """Represents a Kafka message with headers and value."""
    
    def __init__(self, headers: List[tuple[str, bytes]], value: Dict[str, Any]):
        self.headers = headers
        self.value = value
        
    def get_header(self, key: str) -> Optional[str]:
        """Get header value by key."""
        for header_key, header_value in self.headers:
            if header_key == key:
                return header_value.decode('utf-8')
        return None


class KafkaHandler:
    """Kafka protocol adapter that connects Kafka messages to business logic."""

    def __init__(
        self,
        request_handler: RequestHandler,
        bootstrap_servers: str | List[str] = "localhost:9092",
        **kafka_config: Any,
    ) -> None:
        """Initialize Kafka handler.
        
        Args:
            request_handler: Business logic handler.
            bootstrap_servers: Kafka bootstrap servers.
            **kafka_config: Additional Kafka configuration.
        """
        self.request_handler = request_handler
        self.bootstrap_servers = bootstrap_servers
        self.kafka_config = kafka_config
        self.producer: Optional[AIOKafkaProducer] = None
        self._running = False

    async def start(self) -> None:
        """Start the Kafka handler."""
        if self._running:
            return

        try:
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                **self.kafka_config
            )
            await self.producer.start()
            self._running = True
            logger.info("Kafka handler started")

        except Exception as e:
            await self.stop()
            raise ServerError(f"Failed to start Kafka handler: {e}") from e

    async def stop(self) -> None:
        """Stop the Kafka handler."""
        if not self._running:
            return

        self._running = False
        if self.producer:
            await self.producer.stop()
        logger.info("Kafka handler stopped")

    async def handle_request(self, message: KafkaMessage) -> None:
        """Handle incoming Kafka request message.
        
        This is the core callback function called by the consumer loop.
        It extracts metadata, processes the request, and sends the response.
        """
        try:
            # Extract metadata from headers
            reply_topic = message.get_header('reply_topic')
            correlation_id = message.get_header('correlation_id')
            agent_id = message.get_header('agent_id')
            trace_id = message.get_header('trace_id')

            if not reply_topic or not correlation_id:
                logger.error("Missing required headers: reply_topic or correlation_id")
                return

            # Parse request data
            request_data = message.value
            method = request_data.get('method')
            params = request_data.get('params', {})
            streaming = request_data.get('streaming', False)
            agent_card_data = request_data.get('agent_card')

            if not method:
                logger.error("Missing method in request")
                await self._send_error_response(
                    reply_topic, correlation_id, "Missing method in request"
                )
                return

            # Create server call context
            context = ServerCallContext(
                agent_id=agent_id,
                trace_id=trace_id,
            )

            # Parse agent card if provided
            agent_card = None
            if agent_card_data:
                try:
                    agent_card = AgentCard.model_validate(agent_card_data)
                except Exception as e:
                    logger.error(f"Invalid agent card: {e}")

            # Route request to appropriate handler method
            try:
                if streaming:
                    await self._handle_streaming_request(
                        method, params, reply_topic, correlation_id, context
                    )
                else:
                    await self._handle_single_request(
                        method, params, reply_topic, correlation_id, context
                    )
            except Exception as e:
                logger.error(f"Error handling request {method}: {e}")
                await self._send_error_response(
                    reply_topic, correlation_id, f"Request processing error: {e}"
                )

        except Exception as e:
            logger.error(f"Error in handle_request: {e}")

    async def _handle_single_request(
        self,
        method: str,
        params: Dict[str, Any],
        reply_topic: str,
        correlation_id: str,
        context: ServerCallContext,
    ) -> None:
        """Handle a single (non-streaming) request."""
        result = None
        response_type = "message"

        try:
            if method == "message_send":
                request = MessageSendParams.model_validate(params)
                result = await self.request_handler.on_message_send(request, context)
                response_type = "task" if isinstance(result, Task) else "message"

            elif method == "task_get":
                request = TaskQueryParams.model_validate(params)
                result = await self.request_handler.on_get_task(request, context)
                response_type = "task"

            elif method == "task_cancel":
                request = TaskIdParams.model_validate(params)
                result = await self.request_handler.on_cancel_task(request, context)
                response_type = "task"

            elif method == "task_push_notification_config_get":
                request = GetTaskPushNotificationConfigParams.model_validate(params)
                result = await self.request_handler.on_get_task_push_notification_config(request, context)
                response_type = "task_push_notification_config"

            elif method == "task_push_notification_config_list":
                request = ListTaskPushNotificationConfigParams.model_validate(params)
                result = await self.request_handler.on_list_task_push_notification_configs(request, context)
                response_type = "task_push_notification_config_list"

            elif method == "task_push_notification_config_delete":
                request = DeleteTaskPushNotificationConfigParams.model_validate(params)
                await self.request_handler.on_delete_task_push_notification_config(request, context)
                result = {"success": True}
                response_type = "success"

            else:
                raise ServerError(f"Unknown method: {method}")

            # Send response
            await self._send_response(reply_topic, correlation_id, result, response_type)

        except Exception as e:
            logger.error(f"Error in _handle_single_request for {method}: {e}")
            await self._send_error_response(reply_topic, correlation_id, str(e))

    async def _handle_streaming_request(
        self,
        method: str,
        params: Dict[str, Any],
        reply_topic: str,
        correlation_id: str,
        context: ServerCallContext,
    ) -> None:
        """Handle a streaming request."""
        try:
            if method == "message_send":
                request = MessageSendParams.model_validate(params)
                
                # Handle streaming response
                async for event in self.request_handler.on_message_send_stream(request, context):
                    if isinstance(event, TaskStatusUpdateEvent):
                        response_type = "task_status_update"
                    elif isinstance(event, TaskArtifactUpdateEvent):
                        response_type = "task_artifact_update"
                    elif isinstance(event, Task):
                        response_type = "task"
                    else:
                        response_type = "message"
                    
                    await self._send_response(reply_topic, correlation_id, event, response_type)
                
                # Send stream completion signal
                await self._send_stream_complete(reply_topic, correlation_id)
                
            else:
                raise ServerError(f"Streaming not supported for method: {method}")

        except Exception as e:
            logger.error(f"Error in _handle_streaming_request for {method}: {e}")
            await self._send_error_response(reply_topic, correlation_id, str(e))

    async def _send_response(
        self,
        reply_topic: str,
        correlation_id: str,
        result: Any,
        response_type: str,
    ) -> None:
        """Send response back to client."""
        if not self.producer:
            logger.error("Producer not available")
            return

        try:
            # Prepare response data
            response_data = {
                "type": response_type,
                "data": result.model_dump() if hasattr(result, 'model_dump') else result,
            }

            # Prepare headers
            headers = [
                ('correlation_id', correlation_id.encode('utf-8')),
            ]

            await self.producer.send_and_wait(
                reply_topic,
                value=response_data,
                headers=headers
            )

        except KafkaError as e:
            logger.error(f"Failed to send response: {e}")
        except Exception as e:
            logger.error(f"Error sending response: {e}")

    async def _send_stream_complete(
        self,
        reply_topic: str,
        correlation_id: str,
    ) -> None:
        """Send stream completion signal."""
        if not self.producer:
            logger.error("Producer not available")
            return

        try:
            # Prepare response data
            response_data = {
                "type": "stream_complete",
                "data": {},
            }

            # Prepare headers
            headers = [
                ('correlation_id', correlation_id.encode('utf-8')),
            ]

            await self.producer.send_and_wait(
                reply_topic,
                value=response_data,
                headers=headers
            )

        except KafkaError as e:
            logger.error(f"Failed to send stream completion signal: {e}")
        except Exception as e:
            logger.error(f"Error sending stream completion signal: {e}")

    async def _send_error_response(
        self,
        reply_topic: str,
        correlation_id: str,
        error_message: str,
    ) -> None:
        """Send error response back to client."""
        if not self.producer:
            logger.error("Producer not available")
            return

        try:
            response_data = {
                "type": "error",
                "data": {
                    "error": error_message,
                },
            }

            headers = [
                ('correlation_id', correlation_id.encode('utf-8')),
            ]

            await self.producer.send_and_wait(
                reply_topic,
                value=response_data,
                headers=headers
            )

        except Exception as e:
            logger.error(f"Failed to send error response: {e}")

    async def send_push_notification(
        self,
        reply_topic: str,
        notification: Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> None:
        """Send push notification to a specific client topic."""
        if not self.producer:
            logger.error("Producer not available for push notification")
            return

        try:
            # Determine notification type
            if isinstance(notification, Task):
                response_type = "task"
            elif isinstance(notification, TaskStatusUpdateEvent):
                response_type = "task_status_update"
            elif isinstance(notification, TaskArtifactUpdateEvent):
                response_type = "task_artifact_update"
            else:
                response_type = "message"

            response_data = {
                "type": f"push_{response_type}",
                "data": notification.model_dump() if hasattr(notification, 'model_dump') else notification,
            }

            # Push notifications don't have correlation IDs
            headers = [
                ('notification_type', 'push'.encode('utf-8')),
            ]

            await self.producer.send_and_wait(
                reply_topic,
                value=response_data,
                headers=headers
            )

            logger.debug(f"Sent push notification to {reply_topic}")

        except Exception as e:
            logger.error(f"Failed to send push notification: {e}")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
