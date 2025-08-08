"""KafkaHandler 使用示例：
- 启动 KafkaServerApp（内部使用 KafkaHandler）
- 自定义 RequestHandler 处理 message_send（非流式与流式）
- 客户端通过 KafkaClientTransport 发送请求
- 演示服务器端推送通知 send_push_notification

运行方式：
  1) 启动服务端：
     python examples/kafka_handler_example.py server
  2) 启动客户端：
     python examples/kafka_handler_example.py client

注意：
  - 为避免与其它示例冲突，本示例使用 request_topic = 'a2a-requests-dev3'
  - Windows 控制台若出现中文乱码，可临时执行：chcp 65001
"""

import asyncio
import logging
import uuid
from typing import AsyncGenerator

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.apps.kafka import KafkaServerApp
from a2a.client.transports.kafka import KafkaClientTransport
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event

from a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskIdParams,
    TaskQueryParams,
    TaskPushNotificationConfig,
    GetTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigParams,
    DeleteTaskPushNotificationConfigParams,
    TextPart,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REQUEST_TOPIC = "a2a-requests-dev3"
BOOTSTRAP = "100.95.155.4:9094"  # 如需本地测试请改为 "localhost:9092"


class DemoRequestHandler(RequestHandler):
    async def on_message_send(self, params: MessageSendParams, context: ServerCallContext | None = None) -> Task | Message:
        logger.info(f"[Handler] 收到非流式消息: {params.message.parts[0].root.text}")
        return Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text=f"回声: {params.message.parts[0].root.text}"))],
            role=Role.agent,
        )

    async def on_message_send_stream(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event, None]:
        logger.info(f"[Handler] 收到流式消息: {params.message.parts[0].root.text}")
        for i in range(3):
            await asyncio.sleep(0.5)
            yield Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text=f"流式响应 {i+1}: {params.message.parts[0].root.text}"))],
                role=Role.agent,
            )

    # 其他必需抽象方法提供最小实现
    async def on_get_task(self, params: TaskQueryParams, context: ServerCallContext | None = None) -> Task | None:
        logger.info(f"[Handler] 获取任务: {params}")
        return None

    async def on_cancel_task(self, params: TaskIdParams, context: ServerCallContext | None = None) -> Task | None:
        logger.info(f"[Handler] 取消任务: {params}")
        return None

    async def on_set_task_push_notification_config(self, params: TaskPushNotificationConfig, context: ServerCallContext | None = None) -> TaskPushNotificationConfig:
        logger.info(f"[Handler] 设置推送配置: {params}")
        # 简单回显设置
        return params

    async def on_get_task_push_notification_config(self, params: TaskIdParams | GetTaskPushNotificationConfigParams, context: ServerCallContext | None = None) -> TaskPushNotificationConfig:
        logger.info(f"[Handler] 获取推送配置: {params}")
        # 返回一个默认的空配置示例
        return TaskPushNotificationConfig(task_id=getattr(params, 'task_id', ''), channels=[])

    async def on_resubscribe_to_task(self, params: TaskIdParams, context: ServerCallContext | None = None) -> AsyncGenerator[Task, None]:
        logger.info(f"[Handler] 重新订阅任务: {params}")
        if False:
            yield  # 占位，保持为异步生成器
        return

    async def on_list_task_push_notification_config(self, params: ListTaskPushNotificationConfigParams, context: ServerCallContext | None = None) -> list[TaskPushNotificationConfig]:
        logger.info(f"[Handler] 列出推送配置: {params}")
        return []

    async def on_delete_task_push_notification_config(self, params: DeleteTaskPushNotificationConfigParams, context: ServerCallContext | None = None) -> None:
        logger.info(f"[Handler] 删除推送配置: {params}")


async def run_server():
    logger.info("[Server] 启动 Kafka 服务器...")
    server = KafkaServerApp(
        request_handler=DemoRequestHandler(),
        bootstrap_servers=BOOTSTRAP,
        request_topic=REQUEST_TOPIC,
        consumer_group_id="a2a-kafkahandler-demo-server",
    )

    async with server:
        # 使用 KafkaHandler 发送一条主动推送，演示 push notification（延迟发送，等待客户端上线）
        handler = await server.get_handler()
        await asyncio.sleep(1.0)
        await handler.send_push_notification(
            reply_topic="a2a-reply-demo_client",  # 仅演示，实际应为客户端真实 reply_topic
            notification=Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text="这是一条来自服务器的主动推送示例"))],
                role=Role.agent,
            ),
        )

        logger.info("[Server] 服务器运行中，Ctrl+C 退出")
        try:
            await server.run()
        except KeyboardInterrupt:
            logger.info("[Server] 已收到中断信号，准备退出...")


async def run_client():
    logger.info("[Client] 启动 Kafka 客户端...")
    agent_card = AgentCard(
        name="demo_client",
        description="KafkaHandler 示例客户端",
        url="https://example.com/demo-client",
        version="1.0.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="echo",
                name="echo",
                description="回声技能",
                tags=["demo"],
                input_modes=["text/plain"],
                output_modes=["text/plain"],
            )
        ],
    )

    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers=BOOTSTRAP,
        request_topic=REQUEST_TOPIC,
        reply_topic_prefix="a2a-reply",
        consumer_group_id=None,
    )

    async with transport:
        # 非流式请求
        logger.info("[Client] 发送非流式消息...")
        req = MessageSendParams(
            message=Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text="你好，KafkaHandler！"))],
                role=Role.user,
            )
        )
        resp = await transport.send_message(req)
        logger.info(f"[Client] 收到响应: {resp.parts[0].root.text}")

        # 流式请求
        logger.info("[Client] 发送流式消息...")
        stream_req = MessageSendParams(
            message=Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text="你好，流式 KafkaHandler！"))],
                role=Role.user,
            )
        )
        async for ev in transport.send_message_streaming(stream_req):
            if isinstance(ev, Message):
                logger.info(f"[Client] 收到流式响应: {ev.parts[0].root.text}")
            else:
                logger.info(f"[Client] 收到事件: {type(ev).__name__}")


async def main():
    import sys
    if len(sys.argv) < 2:
        print("用法: python examples/kafka_handler_example.py [server|client]")
        return

    if sys.argv[1] == "server":
        await run_server()
    elif sys.argv[1] == "client":
        await run_client()
    else:
        print("无效模式。使用 'server' 或 'client'")


if __name__ == "__main__":
    asyncio.run(main())
