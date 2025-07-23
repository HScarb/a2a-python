## A2A 通信层重构与 RabbitMQ 集成设计文档

### 1. 目标与背景

当前 A2A Python SDK 的通信实现与具体的协议（HTTP, gRPC）紧密耦合。为了支持 RabbitMQ 及未来可能的其他通信方式（如 Kafka、MQTT 等），我们需要对现有代码进行重构，引入一个抽象的通信层。

本次设计的核心目标是：
1.  **解耦**: 将核心业务逻辑与底层通信协议完全分离。
2.  **可扩展**: 创建一个可插拔的架构，使添加新的通信方式变得简单。
3.  **实现 RabbitMQ**: 作为第一个新的通信方式，完整实现基于 RabbitMQ 的 A2A 协议通信。

### 2. 总体设计

我们将引入两个核心的抽象概念：客户端的 **`Transport`** 和服务端的 **`Listener`**。

#### 2.1 客户端架构

`A2AClient` 将不再直接依赖 `httpx` 或 `grpc`。取而代之，它将通过一个抽象的 `ClientTransport` 接口进行所有网络通信。我们可以根据需要，在初始化 `A2AClient` 时注入不同的 `Transport` 实现。

```mermaid
classDiagram
    direction LR
    class A2AClient {
        -transport: ClientTransport
        +send_message()
        +send_message_streaming()
        +get_task()
    }
    class ClientTransport {
        <<Interface>>
        +send_request(method, params)
        +stream_request(method, params)
    }
    class HttpTransport {
        +send_request()
        +stream_request()
    }
    class GrpcTransport {
        +send_request()
        +stream_request()
    }
    class RabbitMQTransport {
        +send_request()
        +stream_request()
    }

    A2AClient o-- ClientTransport
    ClientTransport <|-- HttpTransport
    ClientTransport <|-- GrpcTransport
    ClientTransport <|-- RabbitMQTransport
```

#### 2.2 服务端架构

服务端的改动较小，因为已经有了良好的分层。我们将引入一个 `ServerListener` 抽象。每个 `Listener` 负责监听特定的协议（如 HTTP, gRPC, RabbitMQ），接收原始请求，然后将其传递给现有的、统一的 `RequestHandler` 进行处理。

```mermaid
classDiagram
    direction LR
    class ServerListener {
        <<Interface>>
        +start(handler: RequestHandler)
        +stop()
    }
    class HttpListener {
        -app: FastAPI
        +start(handler)
        +stop()
    }
    class GrpcListener {
        -server: grpc.Server
        +start(handler)
        +stop()
    }
    class RabbitMQListener {
        -connection: aio_pika.Connection
        +start(handler)
        +stop()
    }
    class RequestHandler {
        +on_message_send()
        +on_get_task()
    }

    ServerListener ..> RequestHandler : uses
    HttpListener --|> ServerListener
    GrpcListener --|> ServerListener
    RabbitMQListener --|> ServerListener
```

### 3. 抽象层接口设计

#### 3.1 ClientTransport 接口

```python
# src/a2a/client/transport.py

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, Dict, Optional

class ClientTransport(ABC):
    """Abstract base class for client-side communication transports."""

    @abstractmethod
    async def send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Sends a single request and expects a single response.
        
        Used for: message/send, tasks/get, tasks/cancel, tasks/pushNotificationConfig/set|get
        """
        pass

    @abstractmethod
    async def stream_request(
        self, method: str, params: Dict[str, Any]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Sends a request and streams responses back.
        
        Used for: message/stream, tasks/resubscribe
        """
        yield {} # Required for async generator
        
    @abstractmethod
    async def setup_push_notifications(
        self, callback_url: str, auth_token: Optional[str] = None
    ) -> bool:
        """Sets up push notification configuration for the transport.
        
        Args:
            callback_url: The URL to receive push notifications
            auth_token: Optional authentication token for the callback
            
        Returns:
            True if setup successful, False otherwise
        """
        pass
        
    @abstractmethod
    async def close(self) -> None:
        """Closes the transport connection and cleans up resources."""
        pass
```

#### 3.2 ServerListener 接口

```python
# src/a2a/server/listeners/base.py

from abc import ABC, abstractmethod
from typing import Optional
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.tasks.push_notification_sender import BasePushNotificationSender

class ServerListener(ABC):
    """Abstract base class for server-side listeners."""

    @abstractmethod
    async def start(
        self, 
        handler: RequestHandler,
        push_notification_sender: Optional[BasePushNotificationSender] = None
    ):
        """Starts the listener and begins accepting requests.
        
        Args:
            handler: The request handler to process incoming requests
            push_notification_sender: Optional push notification sender for task updates
        """
        pass

    @abstractmethod
    async def stop(self):
        """Stops the listener and cleans up resources."""
        pass
        
    @abstractmethod
    def get_transport_info(self) -> dict:
        """Returns transport-specific information like connection URLs."""
        pass
```

### 4. RabbitMQ 实现方案

我们将使用 `aio-pika` 库来实现异步的 RabbitMQ 通信。

#### 4.1 简单请求/响应模式 (Request/Response)

我们将使用 RabbitMQ 的 **Direct reply-to** 模式来实现标准的请求/响应。

```mermaid
sequenceDiagram
    participant Client as A2AClient (Transport)
    participant RabbitMQ
    participant Server as A2AListener

    Client->>RabbitMQ: Publish to 'agent.requests' queue <br> (props: reply_to='amq.rabbitmq.reply-to...', correlation_id='xyz')
    RabbitMQ->>Server: Consume message
    Server->>Server: Process request via RequestHandler
    Server->>RabbitMQ: Publish to 'amq.rabbitmq.reply-to...' queue <br> (props: correlation_id='xyz')
    RabbitMQ->>Client: Consume response
```

**支持的方法**: `message/send`, `tasks/get`, `tasks/cancel`, `tasks/pushNotificationConfig/set`, `tasks/pushNotificationConfig/get`

#### 4.2 流式响应模式 (Streaming)

流式响应稍微复杂一些。初始请求仍使用请求/响应模式来建立流。一旦建立，后续事件将通过一个专用的 `fanout` 交换机进行广播。

1.  **流的建立**: 客户端发送一个 `message/stream` 请求（使用上述请求/响应模式）。服务器返回一个确认响应，表示流已开始。
2.  **事件广播**:
    *   客户端在收到确认后，创建一个专用的、临时的队列，并将其绑定到一个为该任务（`task_id`）创建的 `fanout` 交换机上。交换机名称可以是 `stream.events.<task_id>`。
    *   服务器在处理任务并生成事件（`TaskStatusUpdateEvent` 等）时，会将这些事件发布到这个 `fanout` 交换机。
    *   客户端从其临时队列中持续消费事件，直到收到一个带有 `final: true` 标记的事件。

```mermaid
sequenceDiagram
    participant Client as A2AClient (Transport)
    participant RabbitMQ
    participant Server as A2AListener

    Client->>RabbitMQ: 1. Send 'message/stream' request (Req/Res pattern)
    Server->>RabbitMQ: 2. Send confirmation response (Req/Res pattern)

    Client->>RabbitMQ: 3. Create queue 'client.stream.abc'
    Client->>RabbitMQ: 4. Bind 'client.stream.abc' to exchange 'stream.events.task123'

    loop For each stream event
        Server->>RabbitMQ: 5. Publish event to 'stream.events.task123' exchange
        RabbitMQ->>Client: 6. Consume event from 'client.stream.abc' queue
    end
```

**支持的方法**: `message/stream`, `tasks/resubscribe`

#### 4.3 推送通知模式 (Push Notifications)

推送通知允许服务器在任务状态发生变化时主动通知客户端，无需客户端轮询。在RabbitMQ实现中，我们使用专用的队列和交换机来处理推送通知。

1. **通知配置**: 客户端通过 `tasks/pushNotificationConfig/set` 方法配置推送通知，指定一个RabbitMQ队列名称作为回调地址。
2. **通知发送**: 服务器在任务状态更新时，将通知消息发布到指定的队列。
3. **通知接收**: 客户端监听其配置的队列，接收推送通知。

```mermaid
sequenceDiagram
    participant Client as A2AClient
    participant RabbitMQ
    participant Server as A2AListener

    Note over Client,Server: 1. 配置推送通知
    Client->>RabbitMQ: Set push notification config <br> (callback_queue='client.notifications.xyz')
    Server->>Server: Store notification config
    Server->>RabbitMQ: Confirmation response
    
    Note over Client,Server: 2. 任务执行和通知
    Server->>Server: Task status changes
    Server->>RabbitMQ: Publish notification to 'client.notifications.xyz'
    RabbitMQ->>Client: Consume notification
```

**支持的方法**: 基于 `tasks/pushNotificationConfig/set|get` 配置的异步推送通知

#### 4.4 队列和交换机命名约定

为了确保不同实例和任务之间的隔离，我们定义以下命名约定：

- **请求队列**: `agent.requests.{agent_id}` - 服务器监听的主要请求队列
- **流事件交换机**: `stream.events.{task_id}` - 用于特定任务的流式事件广播  
- **推送通知队列**: `notifications.{client_id}.{task_id}` - 客户端指定的推送通知接收队列
- **临时响应队列**: 使用RabbitMQ的 `amq.rabbitmq.reply-to` 自动生成

### 5. 代码修改计划

#### 5.1 新增文件

**客户端传输层**:
*   `src/a2a/client/transport/base.py`: 定义 `ClientTransport` 接口
*   `src/a2a/client/transport/http_transport.py`: 基于HTTP的传输实现（重构现有逻辑）
*   `src/a2a/client/transport/grpc_transport.py`: 基于gRPC的传输实现（重构现有逻辑）
*   `src/a2a/client/transport/rabbitmq_transport.py`: 基于RabbitMQ的传输实现

**服务端监听器**:
*   `src/a2a/server/listeners/base.py`: 定义 `ServerListener` 接口
*   `src/a2a/server/listeners/http_listener.py`: 基于HTTP/FastAPI的监听器实现
*   `src/a2a/server/listeners/grpc_listener.py`: 基于gRPC的监听器实现
*   `src/a2a/server/listeners/rabbitmq_listener.py`: 基于RabbitMQ的监听器实现

**推送通知支持**:
*   `src/a2a/server/tasks/rabbitmq_push_notification_sender.py`: RabbitMQ推送通知发送器
*   `src/a2a/client/push_notification_receiver.py`: 客户端推送通知接收器

#### 5.2 修改文件

*   `src/a2a/client/client.py`: 重构 `A2AClient`，使其依赖于 `ClientTransport` 接口
*   `src/a2a/client/grpc_client.py`: 废弃或重构为使用新的传输层架构
*   `src/a2a/server/apps/jsonrpc/fastapi_app.py`: 重构为使用新的 `HttpListener`
*   `src/a2a/server/tasks/push_notification_sender.py`: 扩展以支持不同的传输协议
*   `pyproject.toml`: 添加 `rabbitmq = ["aio-pika>=9.0.0"]` 到可选依赖

#### 5.3 配置和示例

*   `examples/rabbitmq_client.py`: RabbitMQ客户端使用示例
*   `examples/rabbitmq_server.py`: RabbitMQ服务器使用示例
*   `examples/multi_transport_server.py`: 支持多种传输协议的服务器示例

### 6. 详细实现考虑

#### 6.1 连接管理和错误处理

**连接池**: RabbitMQ传输层将使用连接池来管理连接，避免频繁创建和销毁连接。

**错误恢复**: 实现自动重连机制，当连接丢失时自动重建连接和队列。

**超时处理**: 为请求/响应模式设置合理的超时时间，避免无限等待。

```python
# 错误处理示例
class RabbitMQTransportConfig:
    connection_url: str = "amqp://localhost"
    max_retries: int = 3
    retry_delay: float = 1.0
    request_timeout: float = 30.0
    connection_pool_size: int = 10
```

#### 6.2 消息序列化和安全

**消息格式**: 所有消息将使用JSON格式序列化，与现有HTTP实现保持一致。

**消息压缩**: 对于大型消息，可选择启用消息压缩以减少网络传输开销。

**认证安全**: 支持RabbitMQ的各种认证机制（用户名/密码、TLS证书等）。

#### 6.3 性能优化

**消息预取**: 配置合理的预取数量以平衡吞吐量和内存使用。

**队列持久化**: 根据需要配置队列和消息的持久化策略。

**负载均衡**: 支持多个服务器实例消费同一个请求队列，实现水平扩展。

#### 6.4 监控和日志

**指标收集**: 集成OpenTelemetry以收集连接状态、消息处理时间等指标。

**结构化日志**: 添加详细的日志记录，包括连接事件、消息传输、错误情况等。

### 7. 迁移策略

#### 7.1 向后兼容性

现有的HTTP和gRPC实现将继续工作，新的传输层架构完全向后兼容。

#### 7.2 渐进式迁移

1. **第一阶段**: 实现传输层抽象和HTTP传输实现
2. **第二阶段**: 添加RabbitMQ传输实现  
3. **第三阶段**: 重构现有代码以使用新的传输层架构
4. **第四阶段**: 添加gRPC传输实现并废弃旧代码

#### 7.3 测试策略

**单元测试**: 为每个传输实现添加独立的单元测试。

**集成测试**: 测试不同传输层之间的互操作性。

**性能测试**: 比较不同传输层的性能特征。

### 8. 配置示例

#### 8.1 客户端配置

```python
# HTTP 传输
from a2a.client.transport.http_transport import HttpTransport
from a2a.client.client import A2AClient

http_transport = HttpTransport(base_url="https://agent.example.com")
client = A2AClient(transport=http_transport)

# RabbitMQ 传输  
from a2a.client.transport.rabbitmq_transport import RabbitMQTransport

rabbitmq_transport = RabbitMQTransport(
    connection_url="amqp://user:pass@rabbitmq.example.com",
    agent_id="my-agent"
)
client = A2AClient(transport=rabbitmq_transport)
```

#### 8.2 服务器配置

```python
# 多传输协议服务器
from a2a.server.listeners.http_listener import HttpListener
from a2a.server.listeners.rabbitmq_listener import RabbitMQListener

# HTTP监听器
http_listener = HttpListener(host="0.0.0.0", port=8000)

# RabbitMQ监听器  
rabbitmq_listener = RabbitMQListener(
    connection_url="amqp://user:pass@rabbitmq.example.com",
    agent_id="my-agent"
)

# 同时启动两个监听器
await http_listener.start(handler)
await rabbitmq_listener.start(handler)
```

### 9. 技术注意事项和最佳实践

#### 9.1 RabbitMQ特定考虑

**消息确认**: 使用RabbitMQ的确认机制确保消息不丢失，特别是对于重要的任务状态更新。

**死信队列**: 为处理失败的消息配置死信队列，避免消息永久丢失。

**队列TTL**: 为临时队列（如流事件队列）设置适当的TTL，自动清理不再使用的资源。

```python
# RabbitMQ配置示例
RABBITMQ_CONFIG = {
    "request_queue_args": {
        "x-message-ttl": 300000,  # 5分钟TTL
        "x-dead-letter-exchange": "dlx.requests"
    },
    "stream_queue_args": {
        "x-message-ttl": 60000,   # 1分钟TTL
        "auto_delete": True       # 自动删除
    }
}
```

#### 9.2 协议兼容性验证

为确保RabbitMQ实现与A2A协议完全兼容，需要验证：

1. **消息格式**: JSON-RPC 2.0格式完全保持一致
2. **错误处理**: HTTP错误码映射到RabbitMQ消息状态
3. **超时行为**: 与HTTP实现的超时行为保持一致
4. **认证授权**: 支持与现有认证机制的集成

#### 9.3 扩展性考虑

**Kafka适配**: 设计的传输层抽象同样适用于Kafka等其他消息队列系统。

**自定义协议**: 接口设计支持未来添加自定义或专有协议。

**中间件支持**: 传输层支持中间件模式，可插入日志、监控、认证等功能。

### 10. 风险评估和缓解策略

#### 10.1 潜在风险

1. **性能风险**: RabbitMQ可能比直连HTTP有更高的延迟
2. **复杂性风险**: 增加了系统架构的复杂性
3. **依赖风险**: 引入了对RabbitMQ服务的依赖
4. **兼容性风险**: 可能与现有代码产生不兼容

#### 10.2 缓解策略

1. **性能测试**: 在不同负载下对比各种传输方式的性能
2. **渐进迁移**: 采用渐进式迁移策略，逐步验证和优化
3. **故障转移**: 实现传输层的故障转移机制
4. **全面测试**: 建立完整的测试套件确保兼容性

这个设计文档现在完整涵盖了：
- ✅ A2A协议的3种交互方式（请求/响应、SSE、推送通知）
- ✅ 清晰的抽象层设计
- ✅ 详细的RabbitMQ实现方案
- ✅ 实用的配置示例和最佳实践
- ✅ 风险评估和迁移策略
