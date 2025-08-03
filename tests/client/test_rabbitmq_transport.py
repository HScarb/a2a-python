"""Tests for RabbitMQ client transport."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from a2a.client.transports.rabbitmq import RabbitMQClientTransport
from a2a.types import AgentCard, AgentCapabilities, RabbitMQConfig, TransportProtocol


class TestRabbitMQClientTransport:
    """Test cases for RabbitMQ client transport."""

    def create_agent_card(self) -> AgentCard:
        """Create a test agent card with RabbitMQ configuration."""
        return AgentCard(
            name="Test Agent",
            description="Test agent for RabbitMQ transport",
            version="1.0.0",
            url="amqp://localhost:5672",
            preferred_transport=TransportProtocol.rabbitmq,
            protocol_version="0.3.0",
            capabilities=AgentCapabilities(),
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
        """Test initialization with valid agent card."""
        card = self.create_agent_card()
        transport = RabbitMQClientTransport(card)
        
        assert transport._agent_card == card
        assert transport._rabbitmq_config == card.rabbitmq
        assert transport._connection is None
        assert transport._channel is None

    def test_init_without_rabbitmq_config(self):
        """Test initialization fails without RabbitMQ configuration."""
        card = self.create_agent_card()
        card.rabbitmq = None
        
        with pytest.raises(ValueError, match="Agent card must contain RabbitMQ configuration"):
            RabbitMQClientTransport(card)

    @pytest.mark.asyncio
    async def test_connect_invalid_url(self):
        """Test connection fails with invalid URL."""
        card = self.create_agent_card()
        card.url = "http://invalid-url"
        transport = RabbitMQClientTransport(card)
        
        with pytest.raises(ValueError, match="Invalid RabbitMQ URL"):
            await transport._connect()

    @pytest.mark.asyncio
    async def test_close_transport(self):
        """Test closing the transport."""
        card = self.create_agent_card()
        transport = RabbitMQClientTransport(card)
        
        # Mock connection and channel
        mock_connection = AsyncMock()
        mock_channel = AsyncMock()
        transport._connection = mock_connection
        transport._channel = mock_channel
        
        # Mock consumer task
        mock_task = AsyncMock()
        transport._consumer_task = mock_task
        
        await transport.close()
        
        # Verify cleanup
        mock_task.cancel.assert_called_once()
        mock_channel.close.assert_called_once()
        mock_connection.close.assert_called_once()
