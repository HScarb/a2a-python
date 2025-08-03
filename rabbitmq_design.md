# A2A on RabbitMQ: Design Specification

## 1. 概要设计

本方案旨在为 A2A 协议添加 RabbitMQ 作为一种新的通信传输层。我们将利用 RabbitMQ 的高级消息队列功能来实现 A2A 协议中定义的三种核心交互模式。

- **技术选型**: 使用 `aio-pika` 库，因为它提供了对 `asyncio` 的原生支持，与现有项目的异步特性保持一致。
- **请求-响应模式**: 将采用 RabbitMQ 的 RPC 模式。客户端创建一个临时的、唯一的回复队列 (`reply_to`)，并将请求连同该队列名一起发送到服务端的固定请求队列。服务端处理请求后，将结果直接发送到客户端指定的 `reply_to` 队列。
- **流式 (SSE) 模式**: 模拟 SSE 的行为。使用一个共享的 `direct` exchange。客户端在发起流式请求时，会创建一个临时队列，并生成一个唯一的 `routing_key` 将队列绑定到该 exchange。服务端在处理长任务时，会持续将事件使用此 `routing_key` 发送到 `direct` exchange，从而实现精确的点对点流式通信。
- **推送通知模式**: 使用一个专用的 `direct` exchange。客户端通过 `tasks/pushNotificationConfig/set` 方法，向服务端注册一个 `routing_key`。当服务端任务状态发生需要推送的变更时，会将通知消息使用此 `routing_key` 发布到该 exchange，从而精确地将消息路由给目标客户端。

---

## 2. AgentCard 设计

为了让客户端能够发现并使用 RabbitMQ 进行通信，我们需要在 `AgentCard` 中添加新的字段来描述 RabbitMQ 的连接信息和能力。我们可以在 `AgentCard` 的顶层添加一个可选的 `rabbitmq` 字段。

```json
{
  "name": "Example RabbitMQ Agent",
  "description": "An agent accessible via RabbitMQ.",
  "url": "amqp://guest:guest@localhost:5672/",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true
  },
  "rabbitmq": {
      "vhost": "/",
      "request_queue": "a2a.rpc.requests",
      "streaming_exchange": "a2a.streams.direct",
      "push_notification_exchange": "a2a.push.notifications"
    },
  "skills": [ ]
}
```

**字段说明:**

*   `url`: (复用) 将原有的 `url` 字段用于存放 RabbitMQ 的连接 URI。
*   `rabbitmq`: (新增) 一个对象，包含 RabbitMQ 特定的配置。
    *   `vhost`: (可选) RabbitMQ 的 virtual host，默认为 `/`。
    *   `request_queue`: (必需) 服务端监听请求-响应（RPC）调用的主队列名称。
    *   `rpc_exchange`: (可选) 用于 RPC 的 exchange，如果未提供，则使用默认的 exchange。
    *   `streaming_exchange`: (必需, 如果 `streaming` 为 `true`) 用于流式传输的共享 `direct` exchange 的名称。
    *   `push_notification_exchange`: (必需, 如果 `pushNotifications` 为 `true`) 用于接收推送通知的 `direct` exchange 名称。

---

## 3. 类图设计

下面是将要实现的核心类的UML图。

```mermaid
classDiagram
    direction LR

    class ClientTransport {
        <<abstract>>
        +send_message()
        +send_message_streaming()
        +get_task()
        +cancel_task()
        +set_task_callback()
        +get_task_callback()
        +resubscribe()
        +get_card()
        +close()
    }

    class RabbitMQClientTransport {
        -connection: aio_pika.Connection
        -channel: aio_pika.Channel
        -callback_queue: aio_pika.Queue
        -futures: dict
        +send_message()
        +send_message_streaming()
        # ... other methods ...
        +close()
    }

    ClientTransport <|-- RabbitMQClientTransport

    class RequestHandler {
        <<abstract>>
        +on_message_send()
        +on_message_send_stream()
        # ... other methods ...
    }

    class RabbitMQHandler {
        -request_handler: RequestHandler
        +handle_rpc_request(message)
        +handle_stream_request(message)
        +handle_push_notification(task_id, event)
    }

    class RabbitMQServerApp {
        -handler: RabbitMQHandler
        -connection: aio_pika.Connection
        +run()
        +stop()
        -_listen_on_request_queue()
    }

    RequestHandler <--o RabbitMQHandler
    RabbitMQHandler <--o RabbitMQServerApp
```

---

## 4. 核心交互时序图设计

### a. 请求-响应 (RPC) 交互

```mermaid
sequenceDiagram
    participant Client as RabbitMQClientTransport
    participant RabbitMQ
    participant Server as RabbitMQHandler

    Client->>RabbitMQ: 1. 创建临时回调队列 (e.g., amq.gen-xyz)
    Client->>Client: 2. 生成 correlation_id
    Client->>RabbitMQ: 3. 发送消息到 request_queue<br> (携带 reply_to 和 correlation_id)
    RabbitMQ-->>Server: 4. 消费消息
    Server->>Server: 5. 处理请求 (调用 RequestHandler)
    Server->>RabbitMQ: 6. 将结果发送到 reply_to 队列<br>(携带 correlation_id)
    RabbitMQ-->>Client: 7. 从回调队列消费响应
    Client->>Client: 8. 根据 correlation_id 匹配请求和响应
```

### b. 流式 (SSE) 交互 (优化后)

```mermaid
sequenceDiagram
    participant Client as RabbitMQClientTransport
    participant RabbitMQ
    participant Server as RabbitMQHandler

    Client->>Client: 1. 生成唯一的 routing_key (e.g., stream-uuid-123)
    Client->>RabbitMQ: 2. 创建一个临时队列
    Client->>RabbitMQ: 3. 将临时队列绑定到共享的 `streaming_exchange`<br> (使用唯一的 routing_key)
    Client->>RabbitMQ: 4. 发送初始 RPC 请求到 `request_queue`<br> (在参数中携带 `routing_key`)

    Server->>RabbitMQ: 5. 消费 RPC 请求
    Server->>Server: 6. 存储 task_id 与 routing_key 的映射关系
    Server->>Server: 7. 开始处理长任务 (调用 RequestHandler)

    loop 对于每个事件
        Server->>RabbitMQ: 8. 发布事件到 `streaming_exchange`<br>(使用存储的 routing_key)
    end

    loop 对于每个事件
        RabbitMQ-->>Client: 9. 从临时队列消费流式事件
    end
```

### c. 推送通知交互

```mermaid
sequenceDiagram
    participant Client as RabbitMQClientTransport
    participant RabbitMQ
    participant Server as RabbitMQHandler

    Note over Client,Server: 前置步骤: 客户端已创建<br>一个 direct exchange 和一个队列，<br>并用 routing_key 绑定。

    Client->>RabbitMQ: 1. 发送 RPC 请求 tasks/pushNotificationConfig/set<br>(携带 exchange, routing_key)
    Server->>Server: 2. 消费并处理请求，存储 exchange 和 routing_key

    Note over Server: ...一段时间后，任务状态更新...

    Server->>Server: 3. 触发推送通知逻辑
    Server->>RabbitMQ: 4. 发布通知到 push_notification_exchange<br>(使用存储的 routing_key)
    RabbitMQ-->>Client: 5. 客户端的队列接收到通知
```

---

## 5. 核心类实现细节

### a. `RabbitMQClientTransport` (位于 `src/a2a/client/transports/rabbitmq.py`)

*   **`__init__(self, agent_card)`**:
    *   解析 `agent_card` 获取 RabbitMQ 连接信息 (`url`, `vhost`, `request_queue` 等)。
    *   调用 `aio_pika.connect_robust` 建立连接和 channel。
    *   创建一个专用的、临时的回调队列用于接收 RPC 响应。
    *   启动一个 `consumer` 监听该回调队列。当收到消息时，根据 `correlation_id` 找到对应的 `future` 并设置结果。
    *   用一个字典 `self.futures` 来存储 `correlation_id` 到 `asyncio.Future` 的映射。

*   **`send_message(self, request)`**:
    *   创建一个 `asyncio.Future`。
    *   生成一个唯一的 `correlation_id`，并将 `future` 存入 `self.futures`。
    *   将 `request` 序列化为 JSON 字符串。
    *   使用 `channel.default_exchange.publish` 发送消息到 `agent_card.rabbitmq.request_queue`。
    *   消息属性 (properties) 必须包含 `reply_to=self.callback_queue.name` 和 `correlation_id`。
    *   `await future` 等待结果返回。

*   **`send_message_streaming(self, request)`**:
    *   这是一个异步生成器。
    *   生成一个唯一的 `routing_key` 用于该流。
    *   声明共享的 `direct` exchange: `agent_card.rabbitmq.streaming_exchange`。
    *   创建一个临时队列，并使用 `routing_key` 将其绑定到 `streaming_exchange`。
    *   发送初始的 RPC 请求（类似 `send_message`），但在消息体中附带上这个 `routing_key`。
    *   (可选) 等待并 `yield` 初始的 RPC 响应。
    *   开始 `consume` 绑定的临时队列，对于接收到的每一条消息，反序列化并 `yield`。

*   **`close(self)`**:
    *   关闭 aio-pika 的连接。

### b. `RabbitMQHandler` (位于 `src/a2a/server/request_handlers/rabbitmq_handler.py`)

*   **`__init__(self, request_handler)`**:
    *   注入一个标准的 `RequestHandler` 实例，所有业务逻辑都委托给它。

*   **`async def handle_rpc_request(self, message: aio_pika.IncomingMessage)`**:
    *   在一个 `async with message.process():` 块中处理。
    *   从 `message.properties` 中获取 `reply_to` 和 `correlation_id`。
    *   反序列化 `message.body` 为 A2A 的 `MessageSendParams` 或其他请求类型。
    *   根据请求的方法（可以在消息头或 body 中指定），调用 `self.request_handler` 的相应方法，例如 `on_message_send`。
    *   将 `request_handler` 返回的结果序列化为 JSON。
    *   将结果发布到 `reply_to` 指定的队列，并附上原始的 `correlation_id`。
    *   如果请求中包含 `routing_key`，则意味着这是一个流式请求的开始。需要将这个 `routing_key` 与 `task_id` 关联起来（例如，存在一个字典或数据库中）。
    
    *   **`async def handle_stream_events(self, task_id, event)`**:
        *   当 `RequestHandler` 产生流式事件时，这个方法被调用。
        *   它会根据 `task_id` 查找到对应的 `routing_key`。
        *   将事件序列化后，使用该 `routing_key` 将消息发布到共享的 `streaming_exchange`。

### c. `RabbitMQServerApp` (位于 `src/a2a/server/apps/rabbitmq/app.py`)

*   **`__init__(self, agent_card, request_handler)`**:
    *   初始化 `RabbitMQHandler`。

*   **`async def run(self)`**:
    *   连接到 RabbitMQ。
    *   声明核心的 RPC 请求队列 (`agent_card.rabbitmq.request_queue`)。
    *   声明用于推送通知的 exchange (`agent_card.rabbitmq.push_notification_exchange`)。
    *   设置 `prefetch_count` 来控制并发处理的请求数。
    *   调用 `queue.consume(self.handler.handle_rpc_request)` 开始监听 RPC 请求队列。
    *   保持运行，直到 `stop()` 被调用。

*   **`async def stop(self)`**:
    *   优雅地关闭 RabbitMQ 连接。

---

## 6. 设计决策附注

### 为什么流式和推送通知要使用专用的 Exchange？

方案中，我们为流式（`a2a.streams.direct`）和推送通知（`a2a.push.notifications`）都设计了专用的、具名的 Exchange，而不是依赖 RabbitMQ 的默认 Exchange（它能让消息看起来像是“直接发送到了队列”）。

这是基于以下几点核心的架构考量：

1.  **解耦**: Exchange 作为消息的路由中心，将生产者和消费者的实现完全解耦。生产者只需要知道将消息发送到哪个 Exchange 和使用哪个 `routing_key`，而完全不需要关心消息最终由哪个（或哪些）队列消费。这是 AMQP 协议的核心优势。
2.  **灵活性**: 使用专用 Exchange 提供了巨大的灵活性。例如，未来可以轻易地增加一个“监控”消费者，让它也监听同一个 `routing_key` 来获取消息副本，而无需改动任何生产者代码。也可以将 Exchange 类型从 `direct` 切换为 `topic` 以支持更复杂的订阅模式（如 `task.*`），同样对生产者透明。
3.  **明确性**: 在代码和配置中明确使用 `a2a.streams.direct` 这样的具名 Exchange，使得系统的消息流向一目了然，大大提高了代码的可读性和可维护性。
4.  **安全性**: 在生产环境中，可以对 Exchange 和队列进行独立的、细粒度的权限控制，从而构建更安全的系统。

虽然“直接发送到队列”的模式看起来更简单，但它牺牲了上述所有优点，导致系统耦合度增高、扩展性变差。因此，我们选择了一个更专业、更健壮的架构方案。
