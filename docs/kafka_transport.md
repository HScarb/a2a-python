# A2A Kafka Transport

This document describes the Kafka transport implementation for the A2A (Agent-to-Agent) protocol.

## Overview

The Kafka transport provides a high-throughput, scalable messaging solution for A2A communication using Apache Kafka as the underlying message broker. It implements the request-response pattern using correlation IDs and dedicated reply topics.

## Architecture

### Client Side

- **KafkaClientTransport**: Main client transport class that implements the `ClientTransport` interface
- **CorrelationManager**: Manages correlation IDs and futures for request-response matching
- **Reply Topics**: Each client has a dedicated reply topic for receiving responses

### Server Side

- **KafkaServerApp**: Top-level server application that manages the Kafka consumer lifecycle
- **KafkaHandler**: Protocol adapter that connects Kafka messages to business logic
- **Request Topic**: Single topic where all client requests are sent

## Features

- **Request-Response Pattern**: Synchronous-style communication over asynchronous Kafka
- **Streaming Support**: Handle streaming responses from server to client
- **Push Notifications**: Server can send unsolicited messages to clients
- **Error Handling**: Comprehensive error handling and timeout management
- **Async/Await**: Full async/await support using aiokafka

## Installation

Install the Kafka transport dependencies:

```bash
pip install a2a-sdk[kafka]
```

This will install the required `aiokafka` dependency.

## Usage

### Client Usage

```python
import asyncio
from a2a.client.transports.kafka import KafkaClientTransport
from a2a.types import AgentCard, MessageSendParams

async def main():
    # Create agent card
    agent_card = AgentCard(
        id="my-agent",
        name="My Agent",
        description="Example agent"
    )
    
    # Create Kafka client transport
    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="localhost:9092",
        request_topic="a2a-requests"
    )
    
    async with transport:
        # Send a message
        request = MessageSendParams(
            content="Hello, world!",
            role="user"
        )
        
        response = await transport.send_message(request)
        print(f"Response: {response.content}")

asyncio.run(main())
```

### Server Usage

```python
import asyncio
from a2a.server.apps.kafka import KafkaServerApp
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler

async def main():
    # Create request handler
    request_handler = DefaultRequestHandler()
    
    # Create Kafka server
    server = KafkaServerApp(
        request_handler=request_handler,
        bootstrap_servers="localhost:9092",
        request_topic="a2a-requests"
    )
    
    # Run server
    await server.run()

asyncio.run(main())
```

### Streaming Example

```python
# Client side - streaming request
async for response in transport.send_message_streaming(request):
    print(f"Streaming response: {response.content}")
```

## Configuration

### Client Configuration

```python
transport = KafkaClientTransport(
    agent_card=agent_card,
    bootstrap_servers=["kafka1:9092", "kafka2:9092"],  # Multiple brokers
    request_topic="a2a-requests",
    reply_topic_prefix="a2a-reply",  # Prefix for reply topics
    consumer_group_id="my-client-group",
    # Additional Kafka configuration
    security_protocol="SASL_SSL",
    sasl_mechanism="PLAIN",
    sasl_plain_username="username",
    sasl_plain_password="password"
)
```

### Server Configuration

```python
server = KafkaServerApp(
    request_handler=request_handler,
    bootstrap_servers=["kafka1:9092", "kafka2:9092"],
    request_topic="a2a-requests",
    consumer_group_id="a2a-server-group",
    # Additional Kafka configuration
    auto_offset_reset="earliest",
    enable_auto_commit=True
)
```

## Message Format

### Request Message

```json
{
    "method": "message_send",
    "params": {
        "content": "Hello, world!",
        "role": "user"
    },
    "streaming": false,
    "agent_card": {
        "id": "agent-123",
        "name": "My Agent",
        "description": "Example agent"
    }
}
```

### Response Message

```json
{
    "type": "message",
    "data": {
        "content": "Hello back!",
        "role": "assistant"
    }
}
```

### Headers

- `correlation_id`: Unique identifier linking requests and responses
- `reply_topic`: Client's reply topic for responses
- `agent_id`: ID of the requesting agent
- `trace_id`: Optional tracing identifier

## Error Handling

The transport includes comprehensive error handling:

- **Connection Errors**: Automatic retry logic for Kafka connection issues
- **Timeout Handling**: Configurable timeouts for requests
- **Serialization Errors**: Proper error responses for malformed messages
- **Consumer Failures**: Automatic consumer restart on failures

## Limitations

1. **Streaming Implementation**: The current streaming implementation is basic and may need enhancement for complex streaming scenarios
2. **Topic Management**: Topics must be created manually or through Kafka's auto-creation feature
3. **Exactly-Once Semantics**: The implementation provides at-least-once delivery semantics

## Performance Considerations

- **Topic Partitioning**: Use multiple partitions for the request topic to increase throughput
- **Consumer Groups**: Scale servers by adding more instances to the consumer group
- **Batch Processing**: Configure appropriate batch sizes for producers and consumers
- **Memory Usage**: Monitor memory usage for high-throughput scenarios

## Security

- **SASL/SSL**: Support for SASL and SSL authentication and encryption
- **ACLs**: Use Kafka ACLs to control topic access
- **Network Security**: Deploy in secure network environments

## Monitoring

Monitor the following metrics:

- **Message Throughput**: Requests per second
- **Response Latency**: Time from request to response
- **Consumer Lag**: Lag in processing requests
- **Error Rates**: Failed requests and responses
- **Topic Partition Distribution**: Even distribution across partitions

## Troubleshooting

### Common Issues

1. **Consumer Group Rebalancing**: May cause temporary delays
2. **Topic Auto-Creation**: Ensure topics exist or enable auto-creation
3. **Serialization Errors**: Check message format compatibility
4. **Network Connectivity**: Verify Kafka broker accessibility

### Debug Logging

Enable debug logging to troubleshoot issues:

```python
import logging
logging.getLogger('a2a.client.transports.kafka').setLevel(logging.DEBUG)
logging.getLogger('a2a.server.apps.kafka').setLevel(logging.DEBUG)
```

## Future Enhancements

- **Enhanced Streaming**: Better support for long-running streams
- **Dead Letter Queues**: Handle failed messages
- **Schema Registry**: Support for Avro/Protobuf schemas
- **Metrics Integration**: Built-in metrics collection
- **Topic Management**: Automatic topic creation and management
