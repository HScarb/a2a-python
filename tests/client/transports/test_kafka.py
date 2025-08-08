"""Tests for Kafka client transport."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.client.transports.kafka import KafkaClientTransport
from a2a.client.transports.kafka_correlation import CorrelationManager
from a2a.client.errors import A2AClientError
from a2a.types import AgentCard, Message, MessageSendParams


@pytest.fixture
def agent_card():
    """Create test agent card."""
    return AgentCard(
        id="test-agent",
        name="Test Agent",
        description="Test agent for Kafka transport"
    )


@pytest.fixture
def kafka_transport(agent_card):
    """Create Kafka transport instance."""
    return KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="localhost:9092",
        request_topic="test-requests",
        reply_topic_prefix="test-reply"
    )


class TestCorrelationManager:
    """Test correlation manager functionality."""

    @pytest.mark.asyncio
    async def test_generate_correlation_id(self):
        """Test correlation ID generation."""
        manager = CorrelationManager()
        
        # Generate multiple IDs
        id1 = manager.generate_correlation_id()
        id2 = manager.generate_correlation_id()
        
        # Should be different
        assert id1 != id2
        assert len(id1) > 0
        assert len(id2) > 0

    @pytest.mark.asyncio
    async def test_register_and_complete(self):
        """Test request registration and completion."""
        manager = CorrelationManager()
        correlation_id = manager.generate_correlation_id()
        
        # Register request
        future = await manager.register(correlation_id)
        assert not future.done()
        assert manager.get_pending_count() == 1
        
        # Complete request
        result = Message(content="test response", role="assistant")
        completed = await manager.complete(correlation_id, result)
        
        assert completed is True
        assert future.done()
        assert await future == result
        assert manager.get_pending_count() == 0

    @pytest.mark.asyncio
    async def test_complete_with_exception(self):
        """Test completing request with exception."""
        manager = CorrelationManager()
        correlation_id = manager.generate_correlation_id()
        
        # Register request
        future = await manager.register(correlation_id)
        
        # Complete with exception
        exception = Exception("test error")
        completed = await manager.complete_with_exception(correlation_id, exception)
        
        assert completed is True
        assert future.done()
        
        with pytest.raises(Exception) as exc_info:
            await future
        assert str(exc_info.value) == "test error"

    @pytest.mark.asyncio
    async def test_cancel_all(self):
        """Test cancelling all pending requests."""
        manager = CorrelationManager()
        
        # Register multiple requests
        futures = []
        for i in range(3):
            correlation_id = manager.generate_correlation_id()
            future = await manager.register(correlation_id)
            futures.append(future)
        
        assert manager.get_pending_count() == 3
        
        # Cancel all
        await manager.cancel_all()
        
        assert manager.get_pending_count() == 0
        for future in futures:
            assert future.cancelled()


class TestKafkaClientTransport:
    """Test Kafka client transport functionality."""

    def test_initialization(self, kafka_transport, agent_card):
        """Test transport initialization."""
        assert kafka_transport.agent_card == agent_card
        assert kafka_transport.bootstrap_servers == "localhost:9092"
        assert kafka_transport.request_topic == "test-requests"
        assert kafka_transport.reply_topic == f"test-reply-{agent_card.id}"
        assert not kafka_transport._running

    @pytest.mark.asyncio
    @patch('a2a.client.transports.kafka.AIOKafkaProducer')
    @patch('a2a.client.transports.kafka.AIOKafkaConsumer')
    async def test_start_stop(self, mock_consumer_class, mock_producer_class, kafka_transport):
        """Test starting and stopping the transport."""
        # Mock producer and consumer
        mock_producer = AsyncMock()
        mock_consumer = AsyncMock()
        mock_producer_class.return_value = mock_producer
        mock_consumer_class.return_value = mock_consumer
        
        # Start transport
        await kafka_transport.start()
        
        assert kafka_transport._running is True
        assert kafka_transport.producer == mock_producer
        assert kafka_transport.consumer == mock_consumer
        mock_producer.start.assert_called_once()
        mock_consumer.start.assert_called_once()
        
        # Stop transport
        await kafka_transport.stop()
        
        assert kafka_transport._running is False
        mock_producer.stop.assert_called_once()
        mock_consumer.stop.assert_called_once()

    @pytest.mark.asyncio
    @patch('a2a.client.transports.kafka.AIOKafkaProducer')
    @patch('a2a.client.transports.kafka.AIOKafkaConsumer')
    async def test_send_message(self, mock_consumer_class, mock_producer_class, kafka_transport):
        """Test sending a message."""
        # Mock producer and consumer
        mock_producer = AsyncMock()
        mock_consumer = AsyncMock()
        mock_producer_class.return_value = mock_producer
        mock_consumer_class.return_value = mock_consumer
        
        # Start transport
        await kafka_transport.start()
        
        # Mock correlation manager
        with patch.object(kafka_transport.correlation_manager, 'generate_correlation_id') as mock_gen_id, \
             patch.object(kafka_transport.correlation_manager, 'register') as mock_register:
            
            mock_gen_id.return_value = "test-correlation-id"
            
            # Create a future that resolves to a response
            response = Message(content="test response", role="assistant")
            future = asyncio.Future()
            future.set_result(response)
            mock_register.return_value = future
            
            # Send message
            request = MessageSendParams(content="test message", role="user")
            result = await kafka_transport.send_message(request)
            
            # Verify result
            assert result == response
            
            # Verify producer was called
            mock_producer.send_and_wait.assert_called_once()
            call_args = mock_producer.send_and_wait.call_args
            
            assert call_args[0][0] == "test-requests"  # topic
            assert call_args[1]['value']['method'] == 'message_send'
            assert call_args[1]['value']['params']['content'] == 'test message'
            
            # Check headers
            headers = call_args[1]['headers']
            header_dict = {k: v.decode('utf-8') for k, v in headers}
            assert header_dict['correlation_id'] == 'test-correlation-id'
            assert header_dict['reply_topic'] == kafka_transport.reply_topic

    def test_parse_response(self, kafka_transport):
        """Test response parsing."""
        # Test message response
        message_data = {
            'type': 'message',
            'data': {
                'content': 'test response',
                'role': 'assistant'
            }
        }
        result = kafka_transport._parse_response(message_data)
        assert isinstance(result, Message)
        assert result.content == 'test response'
        assert result.role == 'assistant'
        
        # Test default case (should default to message)
        default_data = {
            'data': {
                'content': 'default response',
                'role': 'assistant'
            }
        }
        result = kafka_transport._parse_response(default_data)
        assert isinstance(result, Message)
        assert result.content == 'default response'

    @pytest.mark.asyncio
    async def test_context_manager(self, kafka_transport):
        """Test async context manager."""
        with patch.object(kafka_transport, 'start') as mock_start, \
             patch.object(kafka_transport, 'stop') as mock_stop:
            
            async with kafka_transport:
                mock_start.assert_called_once()
            
            mock_stop.assert_called_once()


@pytest.mark.integration
class TestKafkaIntegration:
    """Integration tests for Kafka transport (requires running Kafka)."""
    
    @pytest.mark.skip(reason="Requires running Kafka instance")
    @pytest.mark.asyncio
    async def test_real_kafka_connection(self, agent_card):
        """Test connection to real Kafka instance."""
        transport = KafkaClientTransport(
            agent_card=agent_card,
            bootstrap_servers="localhost:9092"
        )
        
        try:
            await transport.start()
            assert transport._running is True
        finally:
            await transport.stop()
            assert transport._running is False
