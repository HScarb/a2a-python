"""示例演示 A2A Kafka 传输使用方法。"""

import asyncio
import logging
from typing import AsyncGenerator

from a2a.client.transports.kafka import KafkaClientTransport
from a2a.server.apps.kafka import KafkaServerApp
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.types import AgentCard, Message, MessageSendParams, Task

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ExampleRequestHandler(DefaultRequestHandler):
    """示例请求处理器。"""

    async def on_message_send(self, params: MessageSendParams, context=None) -> Task | Message:
        """处理消息发送请求。"""
        logger.info(f"收到消息: {params.content}")
        
        # 创建简单的响应消息
        response = Message(
            content=f"回声: {params.content}",
            role="assistant"
        )
        return response

    async def on_message_send_streaming(
        self, 
        params: MessageSendParams, 
        context=None
    ) -> AsyncGenerator[Message | Task, None]:
        """处理流式消息发送请求。"""
        logger.info(f"收到流式消息: {params.content}")
        
        # 模拟流式响应
        for i in range(3):
            await asyncio.sleep(1)  # 模拟处理时间
            response = Message(
                content=f"流式响应 {i+1}: {params.content}",
                role="assistant"
            )
            yield response


async def run_server():
    """运行 Kafka 服务器。"""
    logger.info("启动 Kafka 服务器...")
    
    # 创建请求处理器
    request_handler = ExampleRequestHandler()
    
    # 创建并运行 Kafka 服务器
    server = KafkaServerApp(
        request_handler=request_handler,
        bootstrap_servers="localhost:9092",
        request_topic="a2a-requests",
        consumer_group_id="a2a-example-server"
    )
    
    try:
        await server.run()
    except KeyboardInterrupt:
        logger.info("服务器被用户停止")
    except Exception as e:
        logger.error(f"服务器错误: {e}")
    finally:
        await server.stop()


async def run_client():
    """运行 Kafka 客户端示例。"""
    logger.info("启动 Kafka 客户端...")
    
    # 创建智能体卡片
    agent_card = AgentCard(
        name="示例智能体",
        description="一个示例 A2A 智能体",
        url="https://example.com/example-agent",
        version="1.0.0",
        capabilities={},
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[]
    )
    
    # 创建 Kafka 客户端传输
    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="localhost:9092",
        request_topic="a2a-requests"
    )
    
    try:
        async with transport:
            # 测试单个消息
            logger.info("发送单个消息...")
            request = MessageSendParams(
                content="你好，Kafka！",
                role="user"
            )
            
            response = await transport.send_message(request)
            logger.info(f"收到响应: {response.content}")
            
            # 测试流式消息
            logger.info("发送流式消息...")
            streaming_request = MessageSendParams(
                content="你好，流式 Kafka！",
                role="user"
            )
            
            async for stream_response in transport.send_message_streaming(streaming_request):
                logger.info(f"收到流式响应: {stream_response.content}")
                
    except Exception as e:
        logger.error(f"客户端错误: {e}")


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
