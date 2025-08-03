"""Tests for RabbitMQ server components."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from a2a.server.apps.rabbitmq.app import RabbitMQServerApp
from a2a.server.request_handlers.rabbitmq_handler import RabbitMQHandler
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import AgentCard, AgentCapabilities, RabbitMQConfig, TransportProtocol


class MockRequestHandler(RequestHandler):
    """Mock request handler for testing."""
    
    def __init__(self):
        self.on_message_send = AsyncMock()
        self.on_message_send_stream = AsyncMock()
        self.on_get_task = AsyncMock()
        self.on_cancel_task = AsyncMock()
        self.on_set_task_push_notification_config = AsyncMock()
        self.on_get_task_push_notification_config = AsyncMock()
        self.on_list_task_push_notification_config = AsyncMock()
        self.on_delete_task_push_notification_config = AsyncMock()
        self.on_resubscribe_to_task = AsyncMock()


class TestRabbitMQHandler:
    """Test cases for RabbitMQ request handler."""

    def test_init(self):
        """Test handler initialization."""
        request_handler = MockRequestHandler()
        handler = RabbitMQHandler(request_handler)
        
        assert handler._request_handler == request_handler
        assert handler._streaming_routes == {}


class TestRabbitMQServerApp:
    """Test cases for RabbitMQ server application."""

    def create_agent_card(self) -> AgentCard:
        """Create a test agent card with RabbitMQ configuration."""
        return AgentCard(
            name="Test Server Agent",
            description="Test server agent for RabbitMQ transport",
            version="1.0.0",
            url="amqp://localhost:5672",
            preferred_transport=TransportProtocol.rabbitmq,
            protocol_version="0.3.0",
            capabilities=AgentCapabilities(streaming=True, push_notifications=True),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[],
            rabbitmq=RabbitMQConfig(
                request_queue="test.requests",
                streaming_exchange="test.streams",
                push_notification_exchange="test.notifications",
            ),
        )

    def test_init_with_valid_card(self):
        """Test server initialization with valid agent card."""
        card = self.create_agent_card()
        request_handler = MockRequestHandler()
        server = RabbitMQServerApp(card, request_handler)
        
        assert server._agent_card == card
        assert server._rabbitmq_config == card.rabbitmq
        assert server._request_handler == request_handler
        assert not server.is_running

    def test_init_without_rabbitmq_config(self):
        """Test server initialization fails without RabbitMQ configuration."""
        card = self.create_agent_card()
        card.rabbitmq = None
        request_handler = MockRequestHandler()
        
        with pytest.raises(ValueError, match="Agent card must contain RabbitMQ configuration"):
            RabbitMQServerApp(card, request_handler)

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self):
        """Test stopping server when not running."""
        card = self.create_agent_card()
        request_handler = MockRequestHandler()
        server = RabbitMQServerApp(card, request_handler)
        
        # Should not raise an error
        await server.stop()
        assert not server.is_running
