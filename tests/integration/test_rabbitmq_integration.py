"""Integration test example for RabbitMQ transport.

This example demonstrates a basic integration test that:
1. Starts a mock RabbitMQ server
2. Creates a client and server
3. Tests basic communication

Note: This requires a running RabbitMQ instance on localhost:5672
"""

import asyncio
import pytest
from unittest.mock import AsyncMock

from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.server.apps.rabbitmq.app import RabbitMQServerApp
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    MessageSendParams,
    Part,
    RabbitMQConfig,
    Task,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TextPart,
    TransportProtocol,
)


class TestRequestHandler(RequestHandler):
    """Test request handler for integration tests."""

    async def on_get_task(
        self, params: TaskQueryParams, context: ServerCallContext | None = None
    ) -> Task | None:
        return Task(
            id=params.id,
            context_id="test-context",
            status=TaskStatus(state=TaskState.completed),
            history=[],
        )

    async def on_cancel_task(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> Task | None:
        return Task(
            id=params.id,
            context_id="test-context",
            status=TaskStatus(state=TaskState.canceled),
            history=[],
        )

    async def on_message_send(
        self, params: MessageSendParams, context: ServerCallContext | None = None
    ) -> Task | Message:
        return Message(
            message_id="test-msg-1",
            role="assistant",
            parts=[TextPart(text="Hello from RabbitMQ test!")],
        )

    async def on_message_send_stream(
        self, params: MessageSendParams, context: ServerCallContext | None = None
    ):
        # Return empty generator for now
        return
        yield  # pragma: no cover

    async def on_set_task_push_notification_config(
        self, params: TaskPushNotificationConfig, context: ServerCallContext | None = None
    ) -> TaskPushNotificationConfig:
        return params

    async def on_get_task_push_notification_config(
        self, params, context: ServerCallContext | None = None
    ) -> TaskPushNotificationConfig:
        return TaskPushNotificationConfig(
            task_id="test-task",
            push_notification_config={},
        )

    async def on_list_task_push_notification_config(
        self, params, context: ServerCallContext | None = None
    ) -> list[TaskPushNotificationConfig]:
        return []

    async def on_delete_task_push_notification_config(
        self, params, context: ServerCallContext | None = None
    ) -> None:
        pass

    async def on_resubscribe_to_task(self, params, context=None):
        return
        yield  # pragma: no cover


def create_test_agent_card() -> AgentCard:
    """Create a test agent card for RabbitMQ."""
    return AgentCard(
        name="RabbitMQ Test Agent",
        description="Test agent for RabbitMQ transport integration",
        version="1.0.0",
        url="amqp://localhost:5672",
        preferred_transport=TransportProtocol.rabbitmq,
        protocol_version="0.3.0",
        capabilities=AgentCapabilities(
            streaming=False,  # Keep simple for basic test
            push_notifications=False,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="test-skill",
                name="Test Skill",
                description="A test skill",
                tags=["test"],
            )
        ],
        rabbitmq=RabbitMQConfig(
            vhost="/",
            request_queue="a2a.test.requests",
            streaming_exchange="a2a.test.streams",
            push_notification_exchange="a2a.test.notifications",
        ),
    )


@pytest.mark.skip(reason="Requires running RabbitMQ instance")
@pytest.mark.asyncio
async def test_rabbitmq_basic_communication():
    """Test basic RabbitMQ communication between client and server."""
    agent_card = create_test_agent_card()
    request_handler = TestRequestHandler()
    
    # Start server
    server = RabbitMQServerApp(agent_card, request_handler)
    
    try:
        await server.run()
        
        # Give server time to start
        await asyncio.sleep(1)
        
        # Create client
        config = ClientConfig(
            supported_transports=[TransportProtocol.rabbitmq],
            streaming=False,
        )
        factory = ClientFactory(config)
        client = factory.create(agent_card)
        
        try:
            # Test message send
            message = Message(
                message_id="test-msg",
                role="user",
                parts=[TextPart(text="Hello, test agent!")],
            )
            
            responses = []
            async for response in client.send_message(message):
                responses.append(response)
            
            # Verify response
            assert len(responses) == 1
            assert isinstance(responses[0], Message)
            assert responses[0].role == "assistant"
            
        finally:
            await client.close()
            
    finally:
        await server.stop()


if __name__ == "__main__":
    # Run the test manually if RabbitMQ is available
    asyncio.run(test_rabbitmq_basic_communication())
