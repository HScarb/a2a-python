# RabbitMQ Transport for A2A Python SDK

This implementation adds RabbitMQ transport support to the A2A Python SDK, enabling message-based communication between agents using AMQP protocol.

## Features

- **RPC Communication**: Request-response patterns using temporary queues
- **Streaming Support**: Real-time event streaming using dedicated exchanges
- **Push Notifications**: Asynchronous notifications via message queues
- **Connection Management**: Robust connection handling with automatic reconnection
- **Flow Control**: Built-in prefetch limits and queue management

## Installation

Install the A2A SDK with RabbitMQ support:

```bash
pip install "a2a-sdk[rabbitmq]"
```

## Configuration

### Agent Card Configuration

Add RabbitMQ configuration to your agent card:

```python
from a2a.types import AgentCard, RabbitMQConfig, TransportProtocol

agent_card = AgentCard(
    name="My RabbitMQ Agent",
    description="An agent using RabbitMQ transport",
    version="1.0.0",
    url="https://my-agent.example.com",  # Public endpoint URL
    preferred_transport=TransportProtocol.rabbitmq,
    # ... other fields ...
    rabbitmq=RabbitMQConfig(
        vhost="/",  # Virtual host (optional, defaults to "/")
        request_queue="a2a.requests",  # Queue for RPC requests
        streaming_exchange="a2a.streams.direct",  # Exchange for streaming events
        push_notification_exchange="a2a.push.notifications",  # Exchange for push notifications
    )
)
```

**Note**: The `agent_card.url` is the public endpoint for your agent. The RabbitMQ connection URL should be provided separately when starting the server, as it's typically an internal infrastructure detail.

## Server Usage

### Basic Server Setup

```python
import asyncio
from a2a.server.apps.rabbitmq.app import RabbitMQServerApp
from a2a.server.request_handlers.request_handler import RequestHandler

class MyRequestHandler(RequestHandler):
    # Implement the required methods
    async def on_message_send(self, params, context=None):
        # Handle message requests
        pass
    
    # ... implement other required methods

async def main():
    agent_card = create_agent_card()  # Your agent card with RabbitMQ config
    request_handler = MyRequestHandler()
    
    # Provide RabbitMQ URL separately from agent card
    rabbitmq_url = "amqp://user:password@localhost:5672"
    
    server = RabbitMQServerApp(agent_card, request_handler, rabbitmq_url)
    
    try:
        await server.run()
        # Server is now running and listening for requests
        while server.is_running:
            await asyncio.sleep(1)
    finally:
        await server.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Server Configuration Options

The RabbitMQ server can be configured in different ways:

1. **Explicit RabbitMQ URL** (recommended):

```python
server = RabbitMQServerApp(agent_card, request_handler, "amqp://localhost:5672")
```

2. **Using agent card URL** (if your agent card URL is a RabbitMQ URL):

```python
# Only if agent_card.url is "amqp://..." 
server = RabbitMQServerApp(agent_card, request_handler)
```
```

## Client Usage

### Basic Client Setup

```python
import asyncio
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types import TransportProtocol

async def main():
    agent_card = get_agent_card()  # Agent card with RabbitMQ config
    
    # Configure client for RabbitMQ
    config = ClientConfig(
        supported_transports=[TransportProtocol.rabbitmq],
        streaming=True,  # Enable streaming if needed
    )
    
    # Create client
    factory = ClientFactory(config)
    client = factory.create(agent_card)
    
    try:
        # Send a message
        message = create_message()  # Your message
        async for response in client.send_message(message):
            print(f"Response: {response}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Architecture

### Message Flow

1. **RPC Requests**: 
   - Client sends requests to the agent's `request_queue`
   - Server processes requests and sends responses to temporary callback queues
   - Correlation IDs link requests with responses

2. **Streaming Events**:
   - Server publishes events to the `streaming_exchange`
   - Each streaming session gets a unique routing key
   - Clients subscribe to temporary queues bound to their routing key

3. **Push Notifications**:
   - Long-running tasks can send notifications via `push_notification_exchange`
   - Clients can set up webhooks or subscribe to notification queues

### Infrastructure Components

- **Request Queue**: Durable queue for incoming RPC requests
- **Streaming Exchange**: Direct exchange for real-time event distribution
- **Push Notification Exchange**: Direct exchange for asynchronous notifications
- **Temporary Queues**: Auto-delete queues for RPC responses and streaming events

## Connection URLs

The RabbitMQ transport supports standard AMQP URLs:

```
amqp://username:password@hostname:port/vhost
amqps://username:password@hostname:port/vhost  # TLS/SSL
```

Examples:
- `amqp://localhost:5672` - Local RabbitMQ with default credentials
- `amqp://user:pass@rabbitmq.example.com:5672/myvhost` - Remote with credentials
- `amqps://user:pass@secure.rabbitmq.com:5671` - Secure connection

## Error Handling

The implementation includes robust error handling:

- **Connection Failures**: Automatic reconnection with exponential backoff
- **Message Failures**: Proper NACK handling and dead letter queues
- **Timeout Handling**: Configurable timeouts for RPC requests
- **Serialization Errors**: Graceful handling of malformed messages

## Performance Considerations

- **Prefetch Limits**: Server automatically sets prefetch limits for flow control
- **Connection Pooling**: Shared connections and channels where possible
- **Queue Durability**: Important queues are marked as durable
- **Message Persistence**: Critical messages can be made persistent

## Monitoring and Debugging

Enable logging to monitor RabbitMQ operations:

```python
import logging

# Enable A2A RabbitMQ logging
logging.getLogger('a2a.client.transports.rabbitmq').setLevel(logging.DEBUG)
logging.getLogger('a2a.server.apps.rabbitmq').setLevel(logging.DEBUG)
logging.getLogger('a2a.server.request_handlers.rabbitmq_handler').setLevel(logging.DEBUG)
```

## Security

- Use TLS/SSL connections (`amqps://`) in production
- Configure proper RabbitMQ user permissions
- Limit queue and exchange access through RabbitMQ ACLs
- Consider message encryption for sensitive data

## Troubleshooting

### Common Issues

1. **Connection Refused**:
   - Ensure RabbitMQ is running
   - Check connection URL and credentials
   - Verify network connectivity

2. **Queue Not Found**:
   - Ensure queue names match between client and server
   - Check that exchanges are properly declared

3. **Messages Not Received**:
   - Verify routing keys and bindings
   - Check exchange types and configuration
   - Monitor RabbitMQ management interface

4. **Performance Issues**:
   - Adjust prefetch limits
   - Monitor queue depths
   - Check connection and channel limits

### RabbitMQ Management

Use the RabbitMQ management interface to monitor:
- Queue depths and message rates
- Connection and channel counts
- Exchange bindings and routing
- Consumer status and acknowledgments

## Development

To contribute to the RabbitMQ transport implementation:

1. Install development dependencies:
   ```bash
   pip install -e ".[dev,rabbitmq]"
   ```

2. Run tests:
   ```bash
   pytest tests/client/test_rabbitmq_transport.py
   pytest tests/server/test_rabbitmq_handler.py
   ```

3. Start a local RabbitMQ instance for testing:
   ```bash
   docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
   ```
