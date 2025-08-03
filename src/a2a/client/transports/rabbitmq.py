import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import aio_pika
from aio_pika import connect_robust, Message as RabbitMessage
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractQueue

from a2a.client.middleware import ClientCallContext
from a2a.client.transports.base import ClientTransport
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
)


class RabbitMQClientTransport(ClientTransport):
    """RabbitMQ client transport implementation."""

    def __init__(self, agent_card: AgentCard):
        """
        Initialize the RabbitMQ client transport.
        
        Args:
            agent_card: The agent card containing RabbitMQ configuration.
        """
        if not agent_card.rabbitmq:
            raise ValueError("Agent card must contain RabbitMQ configuration")
        
        self._agent_card = agent_card
        self._rabbitmq_config = agent_card.rabbitmq
        self._connection: AbstractConnection | None = None
        self._channel: AbstractChannel | None = None
        self._callback_queue: AbstractQueue | None = None
        self._futures: dict[str, asyncio.Future] = {}
        self._consumer_task: asyncio.Task | None = None

    async def _connect(self) -> None:
        """Establish connection to RabbitMQ if not already connected."""
        if self._connection and not self._connection.is_closed:
            return

        # Parse URL from agent card
        url = self._agent_card.url
        if not url.startswith(('amqp://', 'amqps://')):
            raise ValueError(f"Invalid RabbitMQ URL: {url}")

        # Connect to RabbitMQ
        connection = await connect_robust(url)
        self._connection = connection
        self._channel = await connection.channel()
        
        # Create callback queue for RPC responses
        self._callback_queue = await self._channel.declare_queue(exclusive=True)
        
        # Start consuming responses
        self._consumer_task = asyncio.create_task(self._consume_responses())

    async def _consume_responses(self) -> None:
        """Consume RPC responses from the callback queue."""
        if not self._callback_queue:
            return

        async with self._callback_queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    correlation_id = message.correlation_id
                    if correlation_id and correlation_id in self._futures:
                        future = self._futures.pop(correlation_id)
                        try:
                            response_data = json.loads(message.body.decode())
                            future.set_result(response_data)
                        except Exception as e:
                            future.set_exception(e)

    async def _send_rpc_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send an RPC request and wait for response."""
        await self._connect()
        
        if not self._channel:
            raise RuntimeError("Not connected to RabbitMQ")
        
        # Generate correlation ID
        correlation_id = str(uuid.uuid4())
        
        # Create future for response
        future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self._futures[correlation_id] = future
        
        # Serialize request
        request_data: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": correlation_id,
        }
        if params is not None:
            request_data["params"] = params
            
        request_body = json.dumps(request_data).encode()
        
        # Create message
        message = RabbitMessage(
            request_body,
            reply_to=self._callback_queue.name if self._callback_queue else None,
            correlation_id=correlation_id,
        )
        
        # Send message to request queue
        if self._rabbitmq_config.rpc_exchange:
            exchange = await self._channel.get_exchange(self._rabbitmq_config.rpc_exchange)
            await exchange.publish(message, routing_key=self._rabbitmq_config.request_queue)
        else:
            # Use default exchange
            await self._channel.default_exchange.publish(
                message, routing_key=self._rabbitmq_config.request_queue
            )
        
        # Wait for response
        response_data = await future
        
        # Check for errors
        if "error" in response_data:
            raise Exception(f"RPC Error: {response_data['error']}")
        
        return response_data

    async def send_message(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task | Message:
        """Sends a non-streaming message request to the agent."""
        response_data = await self._send_rpc_request("message/send", request.model_dump())
        
        result = response_data.get("result")
        if result is None:
            raise Exception("No result in RPC response")
        
        # Determine if response is Task or Message
        if result.get("kind") == "task":
            return Task.model_validate(result)
        else:
            return Message.model_validate(result)

    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        await self._connect()
        
        if not self._channel:
            raise RuntimeError("Not connected to RabbitMQ")
        
        if not self._rabbitmq_config.streaming_exchange:
            raise ValueError("Streaming exchange not configured in agent card")
        
        # Generate unique routing key for this stream
        routing_key = f"stream-{uuid.uuid4()}"
        
        # Declare streaming exchange
        streaming_exchange = await self._channel.declare_exchange(
            self._rabbitmq_config.streaming_exchange,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        
        # Create temporary queue for streaming events
        stream_queue = await self._channel.declare_queue(exclusive=True)
        await stream_queue.bind(streaming_exchange, routing_key)
        
        # Modify request to include routing key in metadata
        request_dict = request.model_dump()
        request_dict.setdefault("metadata", {})["routing_key"] = routing_key
        
        # Send initial streaming request
        try:
            response_data = await self._send_rpc_request("message/stream", request_dict)
            
            # Yield initial response if available
            if "result" in response_data:
                result = response_data["result"]
                if result.get("kind") == "task":
                    yield Task.model_validate(result)
                else:
                    yield Message.model_validate(result)
        except Exception:
            # If initial request fails, we still try to consume from stream
            pass
        
        # Consume streaming events
        async with stream_queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    try:
                        event_data = json.loads(message.body.decode())
                        
                        # Determine event type and yield appropriate object
                        if event_data.get("kind") == "task":
                            yield Task.model_validate(event_data)
                        elif event_data.get("kind") == "task_status_update":
                            yield TaskStatusUpdateEvent.model_validate(event_data)
                        elif event_data.get("kind") == "task_artifact_update":
                            yield TaskArtifactUpdateEvent.model_validate(event_data)
                        else:
                            yield Message.model_validate(event_data)
                    except Exception:
                        # Skip malformed messages
                        continue

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        response_data = await self._send_rpc_request("tasks/get", request.model_dump())
        
        result = response_data.get("result")
        if result is None:
            raise Exception("No result in RPC response")
        
        return Task.model_validate(result)

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        response_data = await self._send_rpc_request("tasks/cancel", request.model_dump())
        
        result = response_data.get("result")
        if result is None:
            raise Exception("No result in RPC response")
        
        return Task.model_validate(result)

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        response_data = await self._send_rpc_request("tasks/pushNotificationConfig/set", request.model_dump())
        
        result = response_data.get("result")
        if result is None:
            raise Exception("No result in RPC response")
        
        return TaskPushNotificationConfig.model_validate(result)

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        response_data = await self._send_rpc_request("tasks/pushNotificationConfig/get", request.model_dump())
        
        result = response_data.get("result")
        if result is None:
            raise Exception("No result in RPC response")
        
        return TaskPushNotificationConfig.model_validate(result)

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent]:
        """Reconnects to get task updates."""
        await self._connect()
        
        if not self._channel:
            raise RuntimeError("Not connected to RabbitMQ")
        
        if not self._rabbitmq_config.streaming_exchange:
            raise ValueError("Streaming exchange not configured in agent card")
        
        # Generate unique routing key for this resubscription
        routing_key = f"resubscribe-{request.id}-{uuid.uuid4()}"
        
        # Declare streaming exchange
        streaming_exchange = await self._channel.declare_exchange(
            self._rabbitmq_config.streaming_exchange,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        
        # Create temporary queue for streaming events
        stream_queue = await self._channel.declare_queue(exclusive=True)
        await stream_queue.bind(streaming_exchange, routing_key)
        
        # Send resubscribe request
        request_dict = request.model_dump()
        request_dict["routing_key"] = routing_key
        
        try:
            response_data = await self._send_rpc_request("tasks/resubscribe", request_dict)
            
            # Yield initial response if available
            if "result" in response_data:
                result = response_data["result"]
                if result.get("kind") == "task":
                    yield Task.model_validate(result)
                else:
                    yield Message.model_validate(result)
        except Exception:
            pass
        
        # Consume streaming events
        async with stream_queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    try:
                        event_data = json.loads(message.body.decode())
                        
                        if event_data.get("kind") == "task":
                            yield Task.model_validate(event_data)
                        elif event_data.get("kind") == "task_status_update":
                            yield TaskStatusUpdateEvent.model_validate(event_data)
                        elif event_data.get("kind") == "task_artifact_update":
                            yield TaskArtifactUpdateEvent.model_validate(event_data)
                        else:
                            yield Message.model_validate(event_data)
                    except Exception:
                        continue

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the AgentCard."""
        response_data = await self._send_rpc_request("agent/getCard")
        
        result = response_data.get("result")
        if result is None:
            raise Exception("No result in RPC response")
        
        return AgentCard.model_validate(result)

    async def close(self) -> None:
        """Closes the transport."""
        # Cancel consumer task
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all pending futures
        for future in self._futures.values():
            if not future.done():
                future.cancel()
        self._futures.clear()
        
        # Close channel and connection
        if self._channel and not self._channel.is_closed:
            await self._channel.close()
        
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
