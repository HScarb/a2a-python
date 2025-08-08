"""示例演示 基于 Kafka 的 A2A 通信（含调用外部 Frankfurter 汇率 API）。

包含三种场景：
- 完整信息：客户端提供完整的 amount/from/to，服务端经由“Agent”调用 Frankfurter API 返回结果
- 信息不完整：Agent 要求补充信息（例如缺少目标币种），客户端再次发送补充信息后获得结果
- 流式：Agent 在处理期间向客户端推送实时状态更新
"""

import asyncio
import logging
import re
import uuid
from typing import AsyncGenerator

import httpx

from a2a.server.events.event_queue import Event
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.apps.kafka import KafkaServerApp
from a2a.client.transports.kafka import KafkaClientTransport
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
    TaskPushNotificationConfig,
    TaskQueryParams,
    TextPart,
    GetTaskPushNotificationConfigParams,
    ListTaskPushNotificationConfigParams,
    DeleteTaskPushNotificationConfigParams,
)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CurrencyAgent:
    """一个极简的“代理”层，用于调用 Frankfurter 汇率 API。

    仅为 demo：
    - 解析文本中的 amount/from/to
    - 支持缺失信息时返回需要补充的字段
    - 调用 https://api.frankfurter.app/latest
    """

    CURRENCY_RE = re.compile(
        r"(?P<amount>\d+(?:\.\d+)?)\s*(?P<from>[A-Za-z]{3})(?:\s*(?:to|->)\s*(?P<to>[A-Za-z]{3}))?",
        re.IGNORECASE,
    )

    async def parse(self, text: str) -> tuple[float | None, str | None, str | None]:
        m = self.CURRENCY_RE.search(text)
        if not m:
            return None, None, None
        amount = float(m.group("amount")) if m.group("amount") else None
        from_ccy = m.group("from").upper() if m.group("from") else None
        to_ccy = m.group("to").upper() if m.group("to") else None
        return amount, from_ccy, to_ccy

    async def get_exchange(self, amount: float, from_ccy: str, to_ccy: str) -> dict:
        url = "https://api.frankfurter.app/latest"
        params = {"amount": amount, "from": from_ccy, "to": to_ccy}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()


class CurrencyRequestHandler(RequestHandler):
    """货币查询请求处理器：演示与外部 Agent/API 的交互与流式更新。"""

    def __init__(self) -> None:
        self.agent = CurrencyAgent()

    async def on_message_send(
        self, params: MessageSendParams, context: ServerCallContext | None = None
    ) -> Task | Message:
        """处理非流式消息：优先演示“信息不完整 -> 补充 -> 返回结果”的交互。"""
        text = params.message.parts[0].root.text
        logger.info(f"收到消息: {text}")

        amount, from_ccy, to_ccy = await self.agent.parse(text)
        # 缺少任何一个关键字段都提示补充，这里主要体现“input-required”分支
        missing: list[str] = []
        if amount is None:
            missing.append("amount")
        if not from_ccy:
            missing.append("from")
        if not to_ccy:
            missing.append("to")

        if missing:
            msg = (
                "INPUT_REQUIRED: 请补充以下信息 -> "
                + ", ".join(missing)
                + "。例如：`100 USD to EUR`"
            )
            return Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text=msg))],
                role=Role.agent,
            )

        # 信息完整，调用 Frankfurter API
        try:
            data = await self.agent.get_exchange(amount, from_ccy, to_ccy)
            rates = data.get("rates", {})
            rate_val = rates.get(to_ccy)
            result_text = (
                f"{amount} {from_ccy} = {rate_val} {to_ccy} (date: {data.get('date')})"
            )
        except Exception as e:
            logger.exception("调用 Frankfurter API 失败")
            result_text = f"查询失败：{e}"

        return Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text=result_text))],
            role=Role.agent,
        )

    async def on_message_send_stream(
        self, params: MessageSendParams, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Event, None]:
        """处理流式消息发送请求：演示实时状态更新 + 最终结果。"""
        text = params.message.parts[0].root.text
        logger.info(f"收到流式消息: {text}")

        # 解析
        amount, from_ccy, to_ccy = await self.agent.parse(text)
        if not all([amount is not None, from_ccy, to_ccy]):
            yield Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text="INPUT_REQUIRED: 需要完整的 amount/from/to，例如：`100 USD to EUR`"))],
                role=Role.agent,
            )
            return

        # 流式状态 1
        yield Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text="Looking up exchange rates..."))],
            role=Role.agent,
        )
        await asyncio.sleep(0.3)

        # 流式状态 2
        yield Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text="Processing exchange rates..."))],
            role=Role.agent,
        )

        # 最终结果
        try:
            data = await self.agent.get_exchange(amount, from_ccy, to_ccy)
            rates = data.get("rates", {})
            rate_val = rates.get(to_ccy)
            result_text = (
                f"{amount} {from_ccy} = {rate_val} {to_ccy} (date: {data.get('date')})"
            )
        except Exception as e:
            logger.exception("调用 Frankfurter API 失败")
            result_text = f"查询失败：{e}"

        yield Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text=result_text))],
            role=Role.agent,
        )

    # 以下为简化的必要抽象方法实现
    async def on_get_task(
        self, params: TaskQueryParams, context: ServerCallContext | None = None
    ) -> Task | None:
        logger.info(f"获取任务: {params}")
        return None

    async def on_cancel_task(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> Task | None:
        logger.info(f"取消任务: {params}")
        return None

    async def on_set_task_push_notification_config(
        self, params: TaskPushNotificationConfig, context: ServerCallContext | None = None
    ) -> None:
        logger.info(f"设置推送通知配置: {params}")

    async def on_get_task_push_notification_config(
        self,
        params: TaskIdParams | GetTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig | None:
        logger.info(f"获取推送通知配置: {params}")
        return None

    async def on_resubscribe_to_task(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Task, None]:
        logger.info(f"重新订阅任务: {params}")
        if False:
            yield None  # 仅为类型满足，不实际产生
        return

    async def on_list_task_push_notification_config(
        self, params: ListTaskPushNotificationConfigParams, context: ServerCallContext | None = None
    ) -> list[TaskPushNotificationConfig]:
        logger.info(f"列出推送通知配置: {params}")
        return []

    async def on_delete_task_push_notification_config(
        self, params: DeleteTaskPushNotificationConfigParams, context: ServerCallContext | None = None
    ) -> None:
        logger.info(f"删除推送通知配置: {params}")


async def run_server():
    """运行 Kafka 服务器。"""
    logger.info("启动 Kafka 服务器...")

    # 使用货币查询处理器
    request_handler = CurrencyRequestHandler()

    # 创建并运行 Kafka 服务器
    server = KafkaServerApp(
        request_handler=request_handler,
        bootstrap_servers="100.95.155.4:9094",
        request_topic="a2a-requests-dev2",
        consumer_group_id="a2a-currency-server",
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
        name="currency_agent_demo",
        description="一个示例 A2A 货币查询智能体",
        url="https://example.com/currency-agent",
        version="1.0.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="currency_skill",
                name="currency_skill",
                description="货币汇率查询",
                tags=["demo", "currency"],
                input_modes=["text/plain"],
                output_modes=["text/plain"],
            )
        ],
    )

    # 创建 Kafka 客户端传输
    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="100.95.155.4:9094",
        request_topic="a2a-requests-dev2",
    )

    try:
        async with transport:
            # 场景 1：信息不完整 -> 要求补充 -> 补发完整信息
            logger.info("场景 1：发送缺少目标币种的查询 -> 期望收到 INPUT_REQUIRED")
            req1 = MessageSendParams(
                message=Message(
                    message_id=str(uuid.uuid4()),
                    parts=[Part(TextPart(text="100 USD"))],  # 缺少 to
                    role=Role.user,
                )
            )
            resp1 = await transport.send_message(req1)
            logger.info(f"响应1: {resp1.parts[0].root.text}")

            if resp1.parts[0].root.text.startswith("INPUT_REQUIRED"):
                logger.info("补充信息 -> 发送: 100 USD to EUR")
                req1b = MessageSendParams(
                    message=Message(
                        message_id=str(uuid.uuid4()),
                        parts=[Part(TextPart(text="100 USD to EUR"))],
                        role=Role.user,
                    )
                )
                resp1b = await transport.send_message(req1b)
                logger.info(f"最终结果: {resp1b.parts[0].root.text}")

            # 场景 2：完整信息（非流式）
            logger.info("场景 2：发送完整查询（非流式） -> 直接返回结果")
            req2 = MessageSendParams(
                message=Message(
                    message_id=str(uuid.uuid4()),
                    parts=[Part(TextPart(text="50 EUR to USD"))],
                    role=Role.user,
                )
            )
            resp2 = await transport.send_message(req2)
            logger.info(f"结果2: {resp2.parts[0].root.text}")

            # 场景 3：流式
            logger.info("场景 3：发送完整查询（流式） -> 实时状态 + 最终结果")
            req3 = MessageSendParams(
                message=Message(
                    message_id=str(uuid.uuid4()),
                    parts=[Part(TextPart(text="120 CNY to JPY"))],
                    role=Role.user,
                )
            )
            async for stream_resp in transport.send_message_streaming(req3):
                logger.info(f"流式: {stream_resp.parts[0].root.text}")

    except Exception as e:
        logger.error(f"客户端错误: {e}", exc_info=True)


async def main():
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m src.kafka_currency_demo [server|client]")
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
