# A2A Transport Layer Examples

This directory contains examples demonstrating the new transport layer architecture for the A2A Python SDK.

## Prerequisites

1. **Python 3.10+** with the A2A SDK installed
2. **RabbitMQ Server** (for RabbitMQ examples)
   ```bash
   # Using Docker
   docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
   
   # Or install locally (varies by OS)
   # Ubuntu/Debian: sudo apt-get install rabbitmq-server
   # macOS: brew install rabbitmq  
   # Windows: Download from https://www.rabbitmq.com/install-windows.html
   ```

## Installation

Install the A2A SDK with transport dependencies:

```bash
# Basic installation
pip install a2a-sdk

# With RabbitMQ support
pip install 'a2a-sdk[rabbitmq]'

# With all optional dependencies
pip install 'a2a-sdk[rabbitmq,grpc,sql,encryption]'
```

## Examples

### 1. HTTP Transport Client (`http_transport_client.py`)

Demonstrates the HTTP transport using the new transport layer architecture:

```bash
python examples/http_transport_client.py
```

This example shows:
- Basic request/response messaging
- Streaming requests (Server-Sent Events)
- Push notification setup
- Health checks and error handling

### 2. RabbitMQ Client (`rabbitmq_client.py`)

Shows how to use RabbitMQ as a transport protocol:

```bash
python examples/rabbitmq_client.py
```

Features demonstrated:
- RabbitMQ request/response using direct reply-to
- Streaming via fanout exchanges
- Push notification consumption
- Connection management and health checks

### 3. RabbitMQ Server (`rabbitmq_server.py`)

A simple A2A agent server using RabbitMQ:

```bash
python examples/rabbitmq_server.py
```

This server:
- Listens for requests on RabbitMQ queues
- Processes A2A protocol messages
- Supports push notifications
- Handles streaming responses

### 4. Multi-Transport Server (`multi_transport_server.py`)

An advanced example showing a server that supports multiple transport protocols simultaneously:

```bash
python examples/multi_transport_server.py
```

This server provides:
- HTTP endpoint at `http://localhost:8000/a2a`
- RabbitMQ queue `agent.requests.multi-transport-agent`
- Agent card at `http://localhost:8000/.well-known/agent.json`
- Unified request handling across transports

## Usage Patterns

### Client-Side Transport Selection

```python
# HTTP Transport
from a2a.client.transport.http_transport import HttpTransport
transport = HttpTransport(base_url="http://agent.example.com/a2a")

# RabbitMQ Transport  
from a2a.client.transport.rabbitmq_transport import RabbitMQTransport, RabbitMQTransportConfig
config = RabbitMQTransportConfig(
    connection_url="amqp://user:pass@rabbitmq.example.com",
    agent_id="target-agent"
)
transport = RabbitMQTransport(config)

# Use with client
from a2a.client.transport_client import TransportA2AClient
client = TransportA2AClient(transport)
```

### Server-Side Multi-Transport Support

```python
# HTTP Listener
from a2a.server.listeners.http_listener import HTTPListener
http_listener = HTTPListener(agent_card, host="0.0.0.0", port=8000)

# RabbitMQ Listener
from a2a.server.listeners.rabbitmq_listener import RabbitMQListener, RabbitMQListenerConfig
rabbitmq_config = RabbitMQListenerConfig(
    connection_url="amqp://localhost",
    agent_id="my-agent"
)
rabbitmq_listener = RabbitMQListener(rabbitmq_config)

# Start both listeners
await http_listener.start(handler)
await rabbitmq_listener.start(handler)
```

## Testing the Examples

1. **Start RabbitMQ** (if using RabbitMQ examples):
   ```bash
   # Using Docker
   docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
   ```

2. **Run the multi-transport server**:
   ```bash
   python examples/multi_transport_server.py
   ```

3. **In separate terminals, test both transports**:
   ```bash
   # Test HTTP transport
   python examples/http_transport_client.py
   
   # Test RabbitMQ transport  
   python examples/rabbitmq_client.py
   ```

4. **Check the RabbitMQ Management UI** (optional):
   - Open http://localhost:15672
   - Login with guest/guest
   - View queues and exchanges created by the examples

## Architecture Benefits

The new transport layer architecture provides:

1. **Protocol Flexibility**: Easy switching between HTTP, RabbitMQ, and future protocols
2. **Unified API**: Same client interface regardless of transport
3. **Multi-Transport Servers**: Single server supporting multiple protocols
4. **Extensibility**: Simple to add new transport implementations
5. **Backward Compatibility**: Existing HTTP-based code continues to work

## Troubleshooting

### RabbitMQ Connection Issues
- Ensure RabbitMQ is running: `sudo systemctl status rabbitmq-server` (Linux)
- Check connection URL format: `amqp://user:pass@host:port/vhost`
- Verify firewall allows connections on port 5672

### HTTP Transport Issues  
- Ensure target server is running and accessible
- Check for correct base URL format: `http://host:port/path`
- Verify SSL/TLS configuration for HTTPS endpoints

### General Issues
- Check Python version (3.10+ required)
- Verify all dependencies are installed
- Review logs for detailed error messages
- Ensure proper async/await usage in your code