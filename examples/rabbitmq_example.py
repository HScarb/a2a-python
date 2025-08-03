"""
Example usage of RabbitMQ transport for A2A communication.

This example demonstrates how to:
1. Create an agent card with RabbitMQ configuration
2. Set up a RabbitMQ server application
3. Create a client that connects to the RabbitMQ server
"""

import asyncio
import logging
from typing import AsyncGenerator

from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.server.apps.rabbitmq.app import RabbitMQServerApp
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    MessageSendParams,
    RabbitMQConfig,
    Task,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatus,
    TaskState,
    TransportProtocol,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ExampleRequestHandler(RequestHandler):
    """Example request handler that implements the required A2A methods."""

    async def on_get_task(
        self, params: TaskQueryParams, context: ServerCallContext | None = None
    ) -> Task | None:
        """Return a dummy task for demonstration."""
        return Task(
            id=params.id,
            context_id="example-context",
            status=TaskStatus(state=TaskState.completed, message="Task completed"),
            history=[],
        )

    async def on_cancel_task(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> Task | None:
        """Cancel a task."""
        return Task(
            id=params.id,
            context_id="example-context",
            status=TaskStatus(state=TaskState.cancelled, message="Task cancelled"),
            history=[],
        )

    async def on_message_send(
        self, params: MessageSendParams, context: ServerCallContext | None = None
    ) -> Task | Message:
        """Handle a message send request."""
        return Message(
            content="Hello from RabbitMQ agent!",
            role="assistant",
        )

    async def on_message_send_stream(
        self, params: MessageSendParams, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Event]:
        """Handle a streaming message request."""
        # Create initial task
        task = Task(
            id="task-123",
            context_id="example-context",
            status=TaskStatus(state=TaskState.running, message="Task started"),
            history=[],
        )
        yield task

        # Simulate streaming responses
        for i in range(3):
            await asyncio.sleep(1)
            message = Message(
                content=f"Streaming response {i + 1}",
                role="assistant",
            )
            yield message

        # Final task completion
        final_task = Task(
            id="task-123",
            context_id="example-context",
            status=TaskStatus(state=TaskState.completed, message="Task completed"),
            history=[],
        )
        yield final_task

    async def on_set_task_push_notification_config(
        self, params: TaskPushNotificationConfig, context: ServerCallContext | None = None
    ) -> TaskPushNotificationConfig:
        """Set push notification config."""
        return params

    async def on_get_task_push_notification_config(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> TaskPushNotificationConfig:
        """Get push notification config."""
        return TaskPushNotificationConfig(
            task_id=params.id,
            webhook_url="https://example.com/webhook",
        )

    async def on_list_task_push_notification_config(
        self, params, context: ServerCallContext | None = None
    ) -> list[TaskPushNotificationConfig]:
        """List push notification configs."""
        return []

    async def on_delete_task_push_notification_config(
        self, params, context: ServerCallContext | None = None
    ) -> None:
        """Delete push notification config."""
        pass


def create_agent_card() -> AgentCard:
    """Create an example agent card with RabbitMQ configuration."""
    return AgentCard(
        name="RabbitMQ Example Agent",
        description="An example agent using RabbitMQ transport",
        version="1.0.0",
        url="https://rabbitmq-example-agent.com",  # Public endpoint
        preferred_transport=TransportProtocol.rabbitmq,
        protocol_version="0.3.0",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="example-skill",
                name="Example Skill",
                description="An example skill for demonstration",
                tags=["example", "demo"],
            )
        ],
        rabbitmq=RabbitMQConfig(
            vhost="/",
            request_queue="a2a.requests",
            streaming_exchange="a2a.streams.direct",
            push_notification_exchange="a2a.push.notifications",
        ),
    )


async def run_server():
    """Run the RabbitMQ server."""
    agent_card = create_agent_card()
    request_handler = ExampleRequestHandler()
    
    # Provide RabbitMQ URL separately from agent card
    rabbitmq_url = "amqp://localhost:5672"
    server_app = RabbitMQServerApp(agent_card, request_handler, rabbitmq_url)
    
    try:
        logger.info("Starting RabbitMQ server...")
        await server_app.run()
        
        # Keep the server running
        while server_app.is_running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
    finally:
        await server_app.stop()


async def run_client():
    """Run the RabbitMQ client."""
    agent_card = create_agent_card()
    
    # Create client configuration
    config = ClientConfig(
        supported_transports=[TransportProtocol.rabbitmq],
        streaming=True,
    )
    
    # Create client factory and client
    factory = ClientFactory(config)
    client = factory.create(agent_card)
    
    try:
        logger.info("Testing RabbitMQ client...")
        
        # Test simple message
        message = Message(content="Hello, RabbitMQ agent!", role="user")
        
        async for response in client.send_message(message):
            logger.info(f"Received response: {response}")
            
    finally:
        await client.close()


async def main():
    """Main function to demonstrate RabbitMQ transport."""
    logger.info("RabbitMQ Transport Example")
    logger.info("=" * 50)
    
    # You can run either the server or client
    # Uncomment the line you want to test:
    
    # await run_server()
    await run_client()


if __name__ == "__main__":
    asyncio.run(main())
