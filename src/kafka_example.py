"""示例演示 A2A Kafka 传输使用方法。"""

import asyncio
import logging
import uuid
from typing import AsyncGenerator

from a2a.server.events.event_queue import Event
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.apps.kafka import KafkaServerApp
from a2a.client.transports.kafka import KafkaClientTransport
from a2a.types import (
    AgentCard,
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskQueryParams,
    TextPart,
    TaskIdParams,
    TaskPushNotificationConfig,
    GetTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigParams,
    DeleteTaskPushNotificationConfigParams,
    AgentCapabilities,
    AgentSkill,
)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ExampleRequestHandler(RequestHandler):
    """示例请求处理器。"""

    async def on_message_send(self, params: MessageSendParams, context: ServerCallContext | None = None) -> Task | Message:
        """处理消息发送请求。"""
        logger.info(f"收到消息: {params.message.parts[0].root.text}")

        # 创建简单的响应消息
        response = Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text=f"回声: {params.message.parts[0].root.text}"))],
            role=Role.agent,
        )
        return response

    async def on_message_send_stream(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None
    ) -> AsyncGenerator[Event, None]:
        """处理流式消息发送请求。"""
        logger.info(f"收到流式消息: {params.message.parts[0].root.text}")

        # 模拟流式响应
        for i in range(3):
            await asyncio.sleep(0.5)  # 模拟处理时间

            # 创建消息事件 (Message 是 Event 类型的一部分)
            message = Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text=f"流式响应 {i+1}: {params.message.parts[0].root.text}"))],
                role=Role.agent,
            )
            yield message

    # 实现其他必需的抽象方法
    async def on_get_task(
        self,
        params: TaskQueryParams,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """获取任务状态。"""
        logger.info(f"获取任务: {params}")
        return None  # 简化实现

    async def on_cancel_task(
        self,
        params: TaskIdParams,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """取消任务。"""
        logger.info(f"取消任务: {params}")
        return None  # 简化实现

    async def on_set_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext | None = None,
    ) -> None:
        """设置任务推送通知配置。"""
        logger.info(f"设置推送通知配置: {params}")

    async def on_get_task_push_notification_config(
        self,
        params: TaskIdParams | GetTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig | None:
        """获取任务推送通知配置。"""
        logger.info(f"获取推送通知配置: {params}")
        return None  # 简化实现

    async def on_resubscribe_to_task(
        self,
        params: TaskIdParams,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Task, None]:
        """重新订阅任务。"""
        logger.info(f"重新订阅任务: {params}")
        # 简化实现，不返回任何内容
        return
        yield  # 使其成为异步生成器

    async def on_list_task_push_notification_config(
        self,
        params: ListTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> list[TaskPushNotificationConfig]:
        """列出任务推送通知配置。"""
        logger.info(f"列出推送通知配置: {params}")
        return []  # 简化实现

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> None:
        """删除任务推送通知配置。"""
        logger.info(f"删除推送通知配置: {params}")


async def run_server():
    """运行 Kafka 服务器。"""
    logger.info("启动 Kafka 服务器...")

    # 创建请求处理器
    request_handler = ExampleRequestHandler()

    # 创建并运行 Kafka 服务器
    server = KafkaServerApp(
        request_handler=request_handler,
        bootstrap_servers="100.95.155.4:9094",
        request_topic="a2a-requests-dev2",
        consumer_group_id="a2a-example-server"
    )

    try:
        await server.run()
    except KeyboardInterrupt:
        logger.info("服务器被用户停止")
    except Exception as e:
        logger.error(f"服务器错误: {e}", exc_info=True)
    finally:
        logger.info("服务器已停止")
        await server.stop()


async def run_client():
    """运行 Kafka 客户端示例。"""
    logger.info("启动 Kafka 客户端...")

    # 创建智能体卡片
    agent_card = AgentCard(
        name="example_name",
        description="一个示例 A2A 智能体",
        url="https://example.com/example-agent",
        version="1.0.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="echo_skill",
                name="echo_skill",
                description="回声技能",
                tags=["example"],
                input_modes=["text/plain"],
                output_modes=["text/plain"]
            )
        ]
    )

    # 创建 Kafka 客户端传输
    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="100.95.155.4:9094",
        request_topic="a2a-requests-dev2"
    )

    try:
        async with transport:
            # 测试单个消息
            logger.info("发送单个消息...")
            request = MessageSendParams(
                message=Message(
                    message_id=str(uuid.uuid4()),
                    parts=[Part(TextPart(text="你好，Kafka！"))],
                    role=Role.user,
                )
            )

            response = await transport.send_message(request)
            logger.info(f"收到响应: {response.parts[0].root.text}")

            # 测试流式消息
            logger.info("发送流式消息...")
            streaming_request = MessageSendParams(
                message=Message(
                    message_id=str(uuid.uuid4()),
                    parts=[Part(TextPart(text="你好，流式 Kafka！"))],
                    role=Role.user,
                )
            )

            async for stream_response in transport.send_message_streaming(streaming_request):
                logger.info(f"收到流式响应: {stream_response.parts[0].root.text}")

    except Exception as e:
        logger.error(f"客户端错误: {e}", exc_info=True)


async def main():
    """主函数演示用法。"""
    import sys

    if len(sys.argv) < 2:
        print("用法: python kafka_example.py [server|client]")
        return

    mode = sys.argv[1]

    if mode == "server":
        await run_server()
    elif mode == "client":
        await run_client()
    else:
        print("无效模式。使用 'server' 或 'client'")


if __name__ == "__main__":
    asyncio.run(main())