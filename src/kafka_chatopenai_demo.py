"""基于 Kafka 的 A2A 通信示例（Agent 使用 OpenAI 官方 SDK 作为决策层）。

场景覆盖：
- 信息不完整：由 Chat 模型判断缺少的字段并返回 INPUT_REQUIRED 提示
- 完整信息：由 Chat 模型/规则判断完整后，调用 Frankfurter API 返回结果
- 流式：服务端在处理时推送实时状态更新（非 OpenAI 流式），最终返回结果

运行：
  - 服务器：python src/kafka_chatopenai_demo.py server
  - 客户端：python src/kafka_chatopenai_demo.py client
依赖：
  - pip install openai httpx
  - 设置环境变量：OPENAI_API_KEY
"""

import asyncio
import json
import logging
import os
import re
import uuid
from typing import AsyncGenerator, Literal, TypedDict

import aiohttp
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


class ParseResult(TypedDict, total=False):
    status: Literal["ok", "input_required", "error"]
    missing: list[str]
    amount: float
    from_ccy: str
    to_ccy: str
    error: str


FRANKFURTER_URL = "https://api.frankfurter.app/latest"


class ChatOpenAIAgent:
    """使用 OpenRouter 作为"智能体"来决策：提取 amount/from/to 或提示补充。"""

    def __init__(self, model: str | None = None):
        self.api_key = os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "环境变量 OPENROUTER_API_KEY 未设置。请在运行前设置，例如 PowerShell: $env:OPENROUTER_API_KEY='your_key'"
            )
        self.model = model or os.getenv("OPENROUTER_MODEL", "openai/gpt-4-turbo")

    async def analyze(self, text: str) -> ParseResult:
        """调用 Chat 模型，要求输出 JSON，包含字段：status/missing/amount/from_ccy/to_ccy。"""
        system = (
            "你是一个助手，负责从用户自然语言中提取金额和货币兑换请求。\n"
            "请提取：amount(数字)、from_ccy(3字母货币，如 USD)、to_ccy(3字母货币，如 EUR)。\n"
            "如果信息不完整，返回 status='input_required'，并在 missing 中列出缺失字段。\n"
            "如果完整，返回 status='ok' 并给出字段值。\n"
            "只返回 JSON，不要包含其他文本。"
        )
        user = f"解析这句话并返回 JSON: {text}"

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://your-site.com",  # 替换为你的网站
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user}
                        ],
                        "temperature": 0,
                        "response_format": {"type": "json_object"}
                    }
                ) as resp:
                    resp_data = await resp.json()
                    content = resp_data["choices"][0]["message"]["content"]
                    data = json.loads(content)
        except Exception as e:
            logger.exception("OpenAI 解析失败")
            return {"status": "error", "error": str(e)}

        result: ParseResult = {"status": "input_required", "missing": ["amount", "from", "to"]}
        # 尝试读取字段
        status = str(data.get("status", "")).lower()
        if status in ("ok", "input_required"):
            result["status"] = status  # type: ignore
        if isinstance(data.get("missing"), list):
            result["missing"] = [str(x) for x in data.get("missing", [])]
        try:
            if "amount" in data:
                result["amount"] = float(data["amount"])  # type: ignore
        except Exception:
            pass
        if isinstance(data.get("from_ccy"), str):
            result["from_ccy"] = data["from_ccy"].upper()  # type: ignore
        if isinstance(data.get("to_ccy"), str):
            result["to_ccy"] = data["to_ccy"].upper()  # type: ignore
        return result

    async def get_exchange(self, amount: float, from_ccy: str, to_ccy: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(FRANKFURTER_URL, params={
                "amount": amount,
                "from": from_ccy,
                "to": to_ccy,
            })
            resp.raise_for_status()
            return resp.json()


class ChatOpenAIRequestHandler(RequestHandler):
    """使用 ChatOpenAI Agent 的服务端处理器。"""

    def __init__(self) -> None:
        self.agent = ChatOpenAIAgent()

    async def on_message_send(
        self, params: MessageSendParams, context: ServerCallContext | None = None
    ) -> Task | Message:
        text = params.message.parts[0].root.text
        logger.info(f"收到消息: {text}")

        parsed = await self.agent.analyze(text)
        if parsed.get("status") == "error":
            return Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text=f"解析失败：{parsed.get('error')}"))],
                role=Role.agent,
            )

        if parsed.get("status") == "input_required":
            missing = parsed.get("missing", [])
            hint = "INPUT_REQUIRED: 请补充以下信息 -> " + ", ".join(missing) + "。例如：`100 USD to EUR`"
            return Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text=hint))],
                role=Role.agent,
            )

        # 完整信息，调用 Frankfurter
        try:
            amount = float(parsed["amount"])  # type: ignore
            from_ccy = str(parsed["from_ccy"])  # type: ignore
            to_ccy = str(parsed["to_ccy"])  # type: ignore
            data = await self.agent.get_exchange(amount, from_ccy, to_ccy)
            rate = data.get("rates", {}).get(to_ccy)
            result_text = f"{amount} {from_ccy} = {rate} {to_ccy} (date: {data.get('date')})"
        except Exception as e:
            logger.exception("API 查询失败")
            result_text = f"查询失败：{e}"

        return Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text=result_text))],
            role=Role.agent,
        )

    async def on_message_send_stream(
        self, params: MessageSendParams, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Event, None]:
        text = params.message.parts[0].root.text
        logger.info(f"收到流式消息: {text}")

        parsed = await self.agent.analyze(text)
        if parsed.get("status") != "ok":
            yield Message(
                message_id=str(uuid.uuid4()),
                parts=[Part(TextPart(text="INPUT_REQUIRED: 需要完整的 amount/from/to，例如：`100 USD to EUR`"))],
                role=Role.agent,
            )
            return

        # 状态更新 1
        yield Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text="Looking up exchange rates..."))],
            role=Role.agent,
        )
        await asyncio.sleep(0.3)

        # 状态更新 2
        yield Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text="Processing exchange rates..."))],
            role=Role.agent,
        )

        # 最终结果
        try:
            amount = float(parsed["amount"])  # type: ignore
            from_ccy = str(parsed["from_ccy"])  # type: ignore
            to_ccy = str(parsed["to_ccy"])  # type: ignore
            data = await self.agent.get_exchange(amount, from_ccy, to_ccy)
            rate = data.get("rates", {}).get(to_ccy)
            result_text = f"{amount} {from_ccy} = {rate} {to_ccy} (date: {data.get('date')})"
        except Exception as e:
            logger.exception("API 查询失败")
            result_text = f"查询失败：{e}"

        yield Message(
            message_id=str(uuid.uuid4()),
            parts=[Part(TextPart(text=result_text))],
            role=Role.agent,
        )

    # 其余抽象方法做最小实现
    async def on_get_task(
        self, params: TaskQueryParams, context: ServerCallContext | None = None
    ) -> Task | None:
        return None

    async def on_cancel_task(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> Task | None:
        return None

    async def on_set_task_push_notification_config(
        self, params: TaskPushNotificationConfig, context: ServerCallContext | None = None
    ) -> None:
        return None

    async def on_get_task_push_notification_config(
        self,
        params: TaskIdParams | GetTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig | None:
        return None

    async def on_resubscribe_to_task(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Task, None]:
        if False:
            yield None  # 占位
        return

    async def on_list_task_push_notification_config(
        self, params: ListTaskPushNotificationConfigParams, context: ServerCallContext | None = None
    ) -> list[TaskPushNotificationConfig]:
        return []

    async def on_delete_task_push_notification_config(
        self, params: DeleteTaskPushNotificationConfigParams, context: ServerCallContext | None = None
    ) -> None:
        return None


async def run_server():
    logger.info("启动 Kafka 服务器（ChatOpenAI Agent）...")
    handler = ChatOpenAIRequestHandler()
    server = KafkaServerApp(
        request_handler=handler,
        bootstrap_servers="100.95.155.4:9094",
        request_topic="a2a-requests-dev2",
        consumer_group_id="a2a-chatopenai-server",
    )
    try:
        await server.run()
    finally:
        await server.stop()


async def run_client():
    logger.info("启动 Kafka 客户端（ChatOpenAI Agent）...")

    agent_card = AgentCard(
        name="chatopenai_currency_agent",
        description="A2A ChatOpenAI 货币查询智能体",
        url="https://example.com/chatopenai-agent",
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

    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="100.95.155.4:9094",
        request_topic="a2a-requests-dev2",
    )

    try:
        async with transport:
            # 1) 不完整 -> INPUT_REQUIRED -> 补充
            logger.info("场景 1：发送缺少目标币种的查询 -> 期望收到 INPUT_REQUIRED")
            req1 = MessageSendParams(
                message=Message(
                    message_id=str(uuid.uuid4()),
                    parts=[Part(TextPart(text="100 USD"))],
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

            # 2) 完整（非流式）
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

            # 3) 完整（流式）
            logger.info("场景 3：发送完整查询（流式） -> 状态 + 结果")
            req3 = MessageSendParams(
                message=Message(
                    message_id=str(uuid.uuid4()),
                    parts=[Part(TextPart(text="120 CNY to JPY"))],
                    role=Role.user,
                )
            )
            async for stream_resp in transport.send_message_streaming(req3):
                logger.info(f"流式: {stream_resp.parts[0].root.text}")

    finally:
        # 让异常在外层显示
        pass


async def main():
    import sys

    if len(sys.argv) < 2:
        print("用法: python -m src.kafka_chatopenai_demo [server|client]")
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
