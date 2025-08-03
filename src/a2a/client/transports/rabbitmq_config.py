"""Configuration validation utilities for RabbitMQ transport."""

from urllib.parse import urlparse

from a2a.types import AgentCard


class RabbitMQConfigValidator:
    """Validates RabbitMQ configuration in agent cards."""

    @staticmethod
    def validate_agent_card(agent_card: AgentCard) -> None:
        """
        Validate that an agent card has proper RabbitMQ configuration.
        
        Args:
            agent_card: The agent card to validate.
            
        Raises:
            ValueError: If the configuration is invalid.
        """
        if not agent_card.rabbitmq:
            raise ValueError("AgentCard must have rabbitmq configuration for RabbitMQ transport")
        
        # Validate URL
        RabbitMQConfigValidator.validate_url(agent_card.url)
        
        # Validate required fields
        rabbitmq_config = agent_card.rabbitmq
        if not rabbitmq_config.request_queue:
            raise ValueError("request_queue is required in RabbitMQ configuration")
        
        # Validate streaming configuration if streaming is enabled
        if (agent_card.capabilities and 
            agent_card.capabilities.streaming and 
            not rabbitmq_config.streaming_exchange):
            raise ValueError("streaming_exchange is required when streaming capability is enabled")
        
        # Validate push notification configuration if push notifications are enabled
        if (agent_card.capabilities and 
            agent_card.capabilities.push_notifications and 
            not rabbitmq_config.push_notification_exchange):
            raise ValueError("push_notification_exchange is required when push_notifications capability is enabled")

    @staticmethod
    def validate_url(url: str) -> None:
        """
        Validate that a URL is a valid AMQP URL.
        
        Args:
            url: The URL to validate.
            
        Raises:
            ValueError: If the URL is invalid.
        """
        if not url:
            raise ValueError("URL cannot be empty")
        
        try:
            parsed = urlparse(url)
        except Exception as e:
            raise ValueError(f"Invalid URL format: {e}")
        
        if parsed.scheme not in ('amqp', 'amqps'):
            raise ValueError(f"URL scheme must be 'amqp' or 'amqps', got '{parsed.scheme}'")
        
        if not parsed.hostname:
            raise ValueError("URL must specify a hostname")
        
        # Default port validation
        if parsed.port is not None:
            if not (1 <= parsed.port <= 65535):
                raise ValueError(f"Port must be between 1 and 65535, got {parsed.port}")

    @staticmethod
    def get_recommended_config() -> dict:
        """
        Get recommended RabbitMQ configuration settings.
        
        Returns:
            Dictionary with recommended configuration.
        """
        return {
            "connection": {
                "heartbeat": 600,  # 10 minutes
                "blocked_connection_timeout": 300,  # 5 minutes
                "socket_timeout": 30,
            },
            "channel": {
                "prefetch_count": 10,
            },
            "queues": {
                "durable": True,
                "auto_delete": False,
                "exclusive": False,
            },
            "exchanges": {
                "durable": True,
                "auto_delete": False,
                "type": "direct",
            },
            "messages": {
                "delivery_mode": 2,  # Persistent
            }
        }
