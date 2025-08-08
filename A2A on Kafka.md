# A2A on Kafka

## 1. 概要设计 (High-Level Design)

<p style="font-family: SimSun; font-size: 16px;">本方案旨在为 A2A 协议添加 Kafka 作为一种新的、高吞吐量的通信传输层。我们将利用 Kafka 的持久化日志和发布-订阅模型来构建一个可靠、可扩展的 A2A 通信基础。</p>

<img alt="" src="https://clouddocs.huawei.com/koopage/v1/app/api/documents/doc/preview/c23116c5-b912-40e5-b9e3-cfc73185fd87?document_id=bfc94c12-6806-438b-b64e-ae9835d084bf" title="" align="left"/>

* <b>核心挑战</b>: Kafka 本身是一个流式平台，并非为请求-响应 (RPC) 模式原生设计。本方案的核心是设计一个健壮的机制来模拟 RPC。

* <b>公共请求主题 (Public Request Topic)+私有响应主题 (Private Reply Topic)：</b><span style="font-weight: normal;">所有客户端都向同一个公共请求主题发送请求，每个客户端都在请求中指定了不同的私有响应主题，并且每个客户端只在自己的私有信箱门口等信，所以它们只会收到属于自己的响应。</span>

* <b>请求-响应模式</b>: 我们将采用 <b>“专属响应主题 (Reply Topic) + 关联ID (Correlation ID)”</b> 的经典模式。

    * <b>客户端 (Client)</b>：在发起请求时，会指定一个自己专属的回调主题 (<code>replyToTopic</code>)，并生成一个唯一的 <code>correlationId</code>。

    * <b>服务端 (Server)</b>：在固定的请求主题 (<code>requestTopic</code>) 上监听。处理完请求后，将携带相同 <code>correlationId</code> 的响应消息发送到客户端指定的 <code>replyToTopic</code>。

* <b>流式 (Streaming) 模式</b>: 可以通过在请求-响应模式上扩展来实现。客户端发起一个初始请求，服务端接受后，在任务执行期间，持续地向客户端的 <code>replyToTopic</code> 发送带有相同 <code>correlationId</code> 的流式数据块。

* <b>推送通知模式</b>: 客户端可以调用一个特定任务 (如 <code>configurePushNotifications</code>)，向服务端注册自己的 <code>replyToTopic</code>。服务端在需要推送时，直接向该主题发送消息。本质上与请求-响应的“响应”部分共享同一机制。

<p style="text-align: left; font-family: SimSun; font-size: 16px;"></p><br />

## <br />

## 2. 总体设计

<p style="font-family: SimSun; font-size: 16px;">下面是将要实现的核心类的 UML 图</p>

<img alt="" src="https://clouddocs.huawei.com/koopage/v1/app/api/documents/doc/preview/682711ee-a83e-4f3a-9f48-ef66dade6216?document_id=bfc94c12-6806-438b-b64e-ae9835d084bf" title="" align="left"/>

#### <br />

#### 抽象层

* <b><code>ClientTransport</code></b>:

    * 这是一个抽象基类，定义了所有客户端传输层必须实现的通用接口。它确保了无论底层通信技术是什么（HTTP, Kafka 等），上层应用代码都能以统一的方式发送请求和处理响应。

* <b><code>RequestHandler</code></b>:

    * 这是一个<span style="font-size: 12pt;">服务端业务逻辑的抽象接口。它定义了诸如 </span>on_message_send<span style="font-size: 12pt;"> 等方法，封装了实际的业务处理能力。该类的设计与具体的网络协</span>议完全解耦。

#### 客户端组件 (<code>Client</code> Side)

* <b><code>KafkaClientTransport</code></b>:

    * <code>ClientTransport</code> 接口针对 Kafka 的具体实现。它是客户端与 Kafka 集群交互的入口。

    * <code>-producer: KafkaProducer</code>: 一个 Kafka 生产者实例，负责将客户端的请求发送到服务端指定的 <code>requestTopic</code>。

    * <code>-consumer: KafkaConsumer</code>: 一个 Kafka 消费者实例，持续监听客户端自己专属的 <code>reply_topic</code>，以便接收服务端的响应。

    * <code>-reply_topic: str</code>: 每个客户端实例独有的 Kafka 主题名称。所有发往此客户端的响应（包括 RPC 结果、流数据和推送通知）都会被发送到这个主题。

    * <code>+send_message()</code>: 实现发送单次请求并等待单个响应的 RPC 逻辑。

    * <code>+send_message_streaming()</code>: 实现发送初始请求后，接收一个或多个后续事件流的逻辑。

* <b><code>CorrelationManager</code></b>:

    * 一个辅助类，作为 <code>KafkaClientTransport</code> 的核心组件。它专门负责在 Kafka 上实现请求-响应模式。

    * <code>+register()</code>: 当客户端发送请求时，该方法会生成一个唯一的 <code>correlationId</code>，并创建一个 <code>asyncio.Future</code> 对象来代表未来的响应。它将这两者关联并存储起来。

    * <code>+complete()</code>: 当客户端的 <code>consumer</code> 收到响应时，会调用此方法。它根据响应中的 <code>correlationId</code> 查找到对应的 <code>Future</code> 并设置其结果，从而唤醒等待该响应的调用者。

#### 服务端组件 (<code>Server</code> Side)

* <b><code>KafkaServerApp</code></b>:

    * 服务端应用的顶层封装和入口点。它负责管理整个服务的生命周期。

    * <code>-consumer: KafkaConsumer</code>: 服务端的主消费者，它连接到 Kafka 并监听一个公共的 <code>requestTopic</code>，所有客户端的请求都发往此主题。

    * <code>-handler: KafkaHandler</code>: 持有一个消息处理器的实例。

    * <code>+run()</code>: 启动服务，开始从 <code>requestTopic</code> 消费消息，并交由 <code>handler</code> 处理。

* <b><code>KafkaHandler</code></b>:

    * 扮演着“协议适配器”的角色，连接了底层的 Kafka 消息和上层的业务逻辑。

    * <code>-producer: KafkaProducer</code>: 持有一个共享的 Kafka 生产者实例，用于将处理结果发送回客户端指定的 <code>reply_topic</code>。

    * <code>-request_handler: RequestHandler</code>: 持有业务逻辑处理器的实例。

    * <code>+handle_request()</code>: 这是消费循环中的核心回调函数。它的职责是：

        1. 解析传入的 Kafka 消息（包括消息头和消息体）。

        2. 从消息头中提取出 <code>reply_topic</code> 和 <code>correlationId</code>。

        3. 将消息体传递给 <code>request_handler</code> 进行实际的业务处理。

        4. 获取处理结果，并使用 <code>producer</code> 将其连同 <code>correlationId</code> 一起发送到 <code>reply_topic</code>。

#### <p style="line-height: 1.3; margin-top: 1.5em; margin-bottom: 1em;">关系说明</p>

## 3. AgentCard 设计

<p style="font-family: SimSun; font-size: 16px;">为了让客户端能够发现并使用 Kafka 进行通信，我们需要在 <code>AgentCard</code> 中添加新的字段。我们将复用/扩展现有结构，并添加一个顶层的 <code>kafka</code> 字段。</p>

```json
{
  "name": "Example Kafka Agent",
  "description": "An agent accessible via Kafka.",
  "preferred_transport": str | None = Field(
        default='JSONRPC', examples=['JSONRPC', 'GRPC', 'HTTP+JSON',]
    ),
  "kafka": {
      "bootstrapServers": "kafka1:9092,kafka2:9092",
      "securityConfig": { /* SASL/SSL 配置 */ },
      "requestTopic": "a2a.requests.example-agent",
      "serializationFormat": "json"
    },
  "capabilities": {
    "streaming": true,
    "pushNotifications": true
  },
  "skills": [ ]
}
```

<p style="font-family: SimSun; font-size: 16px;"><b>字段说明:</b></p>

* <code>kafka</code>: (新增) 一个对象，包含 Kafka 特定的连接和端点信息。

    * <code>bootstrapServers</code>: (必需) Kafka 集群的连接地址列表。

    * <code>securityConfig</code>: (可选) 连接 Kafka 所需的安全配置，如 SASL、SSL 等。

    * <code>requestTopic</code>: (必需) 服务端用于监听请求-响应调用的主请求主题。

    * <code>serializationFormat</code>: (可选, 推荐) 消息体序列化格式，如 "json", "avro", "protobuf"。默认为 "json"。

## <span style="font-size: 18pt;font-weight: 600;">4.三种通信方式</span>

### a. 请求-响应 (RPC) 交互

<img alt="" src="https://clouddocs.huawei.com/koopage/v1/app/api/documents/doc/preview/48b86408-4cf5-48bb-b236-e8fa01d0dad6?document_id=bfc94c12-6806-438b-b64e-ae9835d084bf" title="" align="left"/>

1. 为本次请求创建一个全新的、唯一的 correlation_id。

2. <span style="font-weight: normal;">调用 self.correlation_manager.register_rpc(correlation_id) 来获取一个 Future 对象。</span>

3. <span style="font-weight: normal;">客户端向公共 </span><span style="font-size: 12pt;"><code>requestTopic</code></span><span style="font-weight: normal;"> 发送包含 payload 的消息，并在消息头中附上 </span><span style="font-size: 12pt;"><code>correlationId</code></span><span style="font-weight: normal;"> 和私有的 </span><span style="font-size: 12pt;"><code>reply_topic</code></span><span style="font-weight: normal;">。</span>

4. <span style="font-weight: normal;">客户端使用 </span><span style="font-size: 12pt;"><code>asyncio.wait_for(future, timeout=...) </code></span><span style="font-weight: normal;">异步等待结果，内置超时处理。</span>

5. <span style="font-weight: normal;">服务端 </span><span style="font-size: 12pt;"><code>KafkaHandler</code></span><span style="font-weight: normal;"> 处理请求，并将携带相同 </span><span style="font-size: 12pt;"><code>correlationId</code></span><span style="font-weight: normal;"> 的响应发送到</span><span style="font-size: 12pt;"><code> reply_topic</code></span><span style="font-weight: normal;">。</span>

6. <span style="font-weight: normal;">客户端消费者收到响应，调用</span><span style="font-size: 12pt;"><code> CorrelationManager.complete()</code></span><span style="font-weight: normal;">，设置 </span><span style="font-size: 12pt;"><code>future </code></span><span style="font-weight: normal;">的结果，唤醒等待的调用。</span>

<p style="text-align: left; font-family: SimSun; font-size: 16px;"></p><br />

### b. 流式 (Streaming) 交互

<p style="font-family: SimSun; font-size: 16px;">流式交互利用了<b>一个请求对应多个响应</b>的能力。客户端通过 <code>correlationId</code> 将这些分散的响应消息重组成一个连续的事件流。</p>

<img alt="" src="https://clouddocs.huawei.com/koopage/v1/app/api/documents/doc/preview/06f3542d-4cc7-4ed0-9c7d-5bad96e9994e?document_id=bfc94c12-6806-438b-b64e-ae9835d084bf" title="" align="left"/>

<p style="font-family: SimSun; font-size: 16px;"><b>关键设计点</b>:</p>

* <b>共享 </b><b><code>correlationId</code></b>: 同一个流的所有消息共享同一个 <code>correlationId</code>。这是客户端聚合流的关键。

* <b>客户端逻辑</b>: <code>KafkaClientTransport</code> 的 <code>send_message_streaming</code> 方法会返回一个异步生成器。该方法在内部注册一个特殊的回调或队列，<code>CorrelationManager</code> 在收到带有特定<code>correlationId</code> 的消息时，会把消息放入该队列，供异步生成器 <code>yield</code>。

* <b>流结束</b>: 需要<span style="color: rgb(239, 81, 0);">一个明确的机制</span>来告知客户端流已结束，以便 <code>async for</code> 循环可以正常退出。这可以是在最后一条消息中加一个标志，或者发送一条专用的控制消息。

* 流式 (Streaming) 流式交互采用信封协议 (Envelope Protocol) 来包装消息，以明确区分数据和控制信号。

<p style="font-family: SimSun; font-size: 16px;">消息信封格式:</p>

    * 数据消息: { "type": "data", "payload": { ... } }

    * 结束信号: { "type": "control", "signal": "end_of_stream" }

    * 错误信号: { "type": "error", "error": { "code": ..., "message": ... } }

### c. 推送通知 (Push Notification) 交互

<p style="font-family: SimSun; font-size: 16px;">推送通知本质上是服务端作为发起方，向一个或多个之前已注册的客户端发送消息。</p>

<img alt="" src="https://clouddocs.huawei.com/koopage/v1/app/api/documents/doc/preview/b77f4f55-5083-45d9-a670-8e4853da7c8f?document_id=bfc94c12-6806-438b-b64e-ae9835d084bf" title="" align="left"/>

<p style="font-family: SimSun; font-size: 16px;"><b>关键设计点</b>:</p>

* <b>注册机制</b>: 推送功能依赖于一个前置的“注册”步骤。客户端通过一次标准的 RPC 调用，将自己的“联系方式” (<code>reply_topic</code>) 告知服务端。

* <b>服务端发起</b>: 推送是由服务端主动发起的，它直接向目标客户端的 <code>reply_topic</code> 生产消息。

* <b>一对多</b>: 服务端可以维护一个 <code>reply_topic</code> 列表，实现向多个订阅了相同事件的客户端进行广播式推送。

* <b><code>correlationId</code></b><b> 的作用</b>: 在推送场景下，<code>correlationId</code> 不是必需的，因为客户端没有一个等待中的 <code>Future</code>。但可以发送一个 UUID 作为事件ID，用于去重或追踪。

## 5. 核心类实现细节

### a. <code>KafkaClientTransport</code>

* 类的属性

<table align="left" widthType="abs" columns="[232.0,286.66666666666663,338.0]" style="width: 856.6666666666666px; border-collapse: collapse;">
<colgroup>
<col style="width: 232.0px;" />
<col style="width: 286.66666666666663px;" />
<col style="width: 338.0px;" />
</colgroup>
<tr>
<th style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">
<p style="text-align: center; font-family: SimSun; font-size: 16px;">属性</p>
</th>
<th style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">
<p style="text-align: center; font-family: SimSun; font-size: 16px;">类型</p>
</th>
<th style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">
<p style="text-align: center; font-family: SimSun; font-size: 16px;">描述</p>
</th>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">agent_card</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">AgentCard</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">包含 Kafka 集群连接信息和公共请求主题。</p>
</td>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">session_store</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">SessionStore</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">用于持久化会话数据的外部存储组件。</p>
</td>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">session_id</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">str | None</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">当前会话的ID。由start_new_session或 resume_session 设置。</p>
</td>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">reply_topic</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">str | None</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">当前会话用于接收回复的私有主题。</p>
</td>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">producer</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">AIOKafkaProducer</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">用于发送消息的 Kafka 生产者实例。</p>
</td>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">consumer</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">AIOKafkaConsumer</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">用于接收回复的 Kafka 消费者实例。</p>
</td>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">correlation_manager</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">CorrelationManager</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">管理短生命周期的 correlation_id 到Future/Queue 对象的映射。</p>
</td>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">consumer_task</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">asyncio.Task</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">在后台持续轮询 reply_topic 的任务。</p>
</td>
</tr>
<tr>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">is_connected</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">bool</p>
</td>
<td style="border-style: solid solid solid solid; border-width: 1px 1px 1px 1px; border-color: rgb(204,204,204) rgb(204,204,204) rgb(204,204,204) rgb(204,204,204); height: 48px;">

<p style="text-align: center; font-family: SimSun; font-size: 16px;">用于追踪连接状态的内部标志位。</p>
</td>
</tr>
</table>
<div style="clear: left;"></div>

* 类的方法 (Methods)

    * <span style="background-color: rgb(235, 235, 235);font-weight: bold;"><b>初始化</b></span>

        * <b>init</b>(self, agent_card: AgentCard, session_store: SessionStore)

            * 描述: 构造 Transport 对象。这是一个非常轻量的操作，不执行任何网络I/O。

            * 存储agent_card和 session_store。

            * 将 session_id, reply_topic, producer, consumer 等属性初始化为 None。

            * 将 is_connected 初始化为 False。

            * 创建一个 CorrelationManager 实例。

    * <span style="background-color: rgb(235, 235, 235);font-weight: bold;"><b>会话生命周期管理</b></span>

        * async start_new_session(self) -> str

            * 描述: 创建一个全新的、可持久化的会话。

            * 返回: 新创建的 session_id。

            * 生成一个唯一的 session_id (例如 uuid.uuid4())。

            * 生成一个唯一的 reply_topic 名称。

            * 调用 await self.session_store.save_session(session_id, reply_topic) 来持久化这个会话。

            * 设置 self.session_id 和 self.reply_topic。

            * 返回这个 session_id。

        * async resume_session(self, session_id: str)

            * 描述: 恢复一个之前创建的会话。

            * 调用 reply_topic = await self.session_store.get_reply_topic(session_id)。

            * 如果 reply_topic 为 None，则抛出 SessionNotFoundError 异常。

            * 将 self.session_id 设置为传入的 session_id。

            * 将 self.reply_topic 设置为查找到的主题。

        * async terminate_session(self)

            * 描述: 关闭连接，并从持久化存储中永久删除该会话记录。

            * 调用 await self.close() 来关闭网络组件。

            * 如果 self.session_id 存在，则调用 await self.session_store.delete_session(self.session_id)。

    * <span style="background-color: rgb(235, 235, 235);font-weight: bold;"><b>连接管理</b></span>

        * async connect(self)

            * 描述: 建立到 Kafka 的网络连接。此方法必须在会话被启动或恢复后才能调用。

            * 检查 self.reply_topic 是否已设置，否则抛出异常。

            * 初始化并启动 self.producer。

            * 初始化 self.consumer 并使其订阅 self.reply_topic。

            * 启动 self.consumer。

            * 创建并启动 consumertask 来运行后台轮询循环。

            * 设置 isconnected 为 True。

        * async close(self)

            * 描述: 关闭网络连接，但不会删除 SessionStore 中的会话记录。

            * 如果 isconnected 为 False，则直接返回。

            * 取消 consumertask。

            * 调用 await self.consumer.stop() 和 await self.producer.stop()。

            * 设置 isconnected 为 False。

    * <span style="background-color: rgb(235, 235, 235);font-weight: bold;"><b>通信</b></span>

        * async send_message(self, payload: dict, timeout: int) -> dict

            * 描述: 发送单个请求并等待单个响应 (RPC模式)。

            * 为本次请求创建一个全新的、唯一的 correlation_id。

            * 调用 self.correlation_manager.register_rpc(correlation_id) 来获取一个 Future 对象。

            * 构建 Kafka 消息，包含 payload，并设置 correlationId 和 reply_topic 的消息头。

            * 使用 self.producer 发送消息。

            * 在指定的 timeout 内 await 那个 Future 对象。

            * 返回从 Future 中获取的结果。

        * async send_message_streaming(self, payload: dict) -> AsyncGenerator[dict, None]

            * 描述: 发送单个请求，并返回一个用于接收多个响应的异步生成器。

            * 为本次流式请求创建一个全新的、唯一的 correlation_id。

            * 调用 self.correlation_manager.register_stream(correlation_id) 来获取一个 asyncio.Queue。

            * 构建并发送 Kafka 消息 (同 send_message)。

            * 从 Queue 中 yield 消息，直到收到特殊的流结束标记。

### b. <code>KafkaHandler</code>

* <b><code>async def handle_request(self, message: KafkaMessage)</code></b>:

    * 解析元数据:

        * 从 msg.headers 中提取必要的路由信息：reply_topic 和 correlation_id。如果任一缺失，则记录错误并终止处理。 

    * 解析请求体: 

        * 反序列化 msg.value (JSON 格式) 得到请求体 dict。 

        * 从请求体中提取 method (要调用的方法名，如 'message/send') 和 params (该方法所需的参数 dict)。 

    * 动态调度与执行: 

        * 使用 method 字符串在 _method_map 调度表中查找对应的业务方法 handler_method。 

        * 将 params 这个 dict 实例化为 handler_method 所需的 Pydantic 模型，完成数据校验和类型转换。 

        * 在 try...except 块中，调用业务方法：result = await handler_method(params=validated_params, ...)。 

    * 处理与回传结果: 

        * 判断结果类型: 检查 result 是单个返回值还是一个异步生成器 (AsyncGenerator)。 

        * <span style="font-weight: bold;"><b>对于单个返回值 (RPC 模式)</b></span>: 调用私有方法 _handle_single_result，将结果包装在标准的信封协议 ({"type": "data", ...}) 中，并使用  producer  将其连同 correlation_id 一起发送到 reply_topic。 

        * <span style="font-weight: bold;"><b>对于异步生成器 (流式模式)</b></span>: 调用私有方法 _handle_stream_result，遍历生成器，将每个产生的事件都独立包装在信封中发送。 当流结束后，发送一个特殊的流结束控制消息 ({"type": "control", "signal": "end_of_stream"})。

    *  统一异常处理: 

        * 如果在上述任何步骤中捕获到异常，则调用私有方法 _send_error_response，将错误信息包装在标准的错误信封 ({"type": "error", ...}) 中发送给客户端，确保客户端不会无限期等待。

### c. <code>KafkaServerApp</code>

* <b><code>async def run(self)</code></b>:

    * 连接到 Kafka，初始化 <code>KafkaConsumer</code> 监听 <code>agent_card.kafka.requestTopic</code>。

    * 初始化一个共享的 <code>KafkaProducer</code>，并注入到 <code>KafkaHandler</code> 中。

    * 循环调用 <code>consumer.getmany()</code> 并将收到的消息分发给 <code>self.handler.handle_request</code> 处理。

### <span style="font-size: 15pt;font-weight: 600;"><code>d.CorrelationManager - 异步调用调度核心</code></span>

<p style="font-family: SimSun; font-size: 16px;">这个类是客户端实现异步 RPC 和流式处理的关键。它不直接与 Kafka 交互，而是作为一个内存中的状态管理器。</p>

<p style="font-family: SimSun; font-size: 16px;">属性:</p>

<p style="font-family: SimSun; font-size: 16px;"><span style="font-size: 12pt;"><code>pending_requests: dict[str, asyncio.Future]: </code></span>一个字典，用于存储 RPC 调用的<span style="font-size: 12pt;"><code> correlationId </code></span>到其对应 <span style="font-size: 12pt;"><code>Future</code></span> 对象的映射。</p>

<p style="font-family: SimSun; font-size: 16px;"><span style="font-size: 12pt;"><code>streamingqueues: dict[str, asyncio.Queue]</code></span>: 一个字典，用于存储流式调用的 <span style="font-size: 12pt;"><code>correlationId</code></span> 到其对应 <span style="font-size: 12pt;"><code>asyncio.Queue </code></span>的映射。</p>

<p style="font-family: SimSun; font-size: 16px;"></p><br />

<p style="text-align: unset; font-family: SimSun; font-size: 16px;">6。思考问题</p>

<p style="text-align: unset; font-family: SimSun; font-size: 16px;"><span style="font-weight: bold;"><b>请求响应和推送有什么区别？</b></span></p>

<p style="font-family: SimSun; font-size: 16px;">RPC 和推送通知的核心差异在于：RPC 是客户端主动发起请求并等待响应的同步模式，每个请求都有对应的响应，使用 correlationId 进行请求-响应匹配，生命周期短且自动清理；而推送通知是服务端主动向已注册客户端发送消息的异步模式，客户端无需等待，消息可能丢失需要容错处理，生命周期长且需要持久化存储注册信息，本质上是"先注册后推送"的事件驱动模式。</p>

<p style="font-family: SimSun; font-size: 16px;"><br></p>
