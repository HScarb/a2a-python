"""Comprehensive example demonstrating A2A Kafka transport features."""

import asyncio
import logging
from typing import AsyncGenerator

from a2a.client.transports.kafka import KafkaClientTransport
from a2a.server.apps.kafka import KafkaServerApp
from a2a.server.request_handlers.default_request_handler import DefaultRequestHandler
from a2a.types import (
    AgentCard, 
    Message, 
    MessageSendParams, 
    Task, 
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    TaskQueryParams,
    TaskIdParams,
    AgentCapabilities,
    AgentSkill
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ComprehensiveRequestHandler(DefaultRequestHandler):
    """Comprehensive request handler demonstrating all features."""

    def __init__(self):
        super().__init__()
        self.tasks = {}  # Simple in-memory task storage
        self.task_counter = 0

    async def on_message_send(self, params: MessageSendParams, context=None) -> Task | Message:
        """Handle message send request."""
        logger.info(f"Received message: {params.content}")
        
        # Simulate different response types based on content
        if "task" in params.content.lower():
            # Create a task
            self.task_counter += 1
            task_id = f"task-{self.task_counter}"
            
            task = Task(
                id=task_id,
                status="running",
                input=params.content,
                output=None
            )
            self.tasks[task_id] = task
            
            logger.info(f"Created task: {task_id}")
            return task
        else:
            # Return a simple message
            response = Message(
                content=f"Echo: {params.content}",
                role="assistant"
            )
            return response

    async def on_message_send_streaming(
        self, 
        params: MessageSendParams, 
        context=None
    ) -> AsyncGenerator[Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent, None]:
        """Handle streaming message send request."""
        logger.info(f"Received streaming message: {params.content}")
        
        # Create initial task
        self.task_counter += 1
        task_id = f"stream-task-{self.task_counter}"
        
        task = Task(
            id=task_id,
            status="running",
            input=params.content,
            output=None
        )
        self.tasks[task_id] = task
        yield task
        
        # Simulate processing with status updates
        for i in range(3):
            await asyncio.sleep(1)  # Simulate processing time
            
            # Send status update
            status_update = TaskStatusUpdateEvent(
                task_id=task_id,
                status="running",
                progress=f"Step {i+1}/3 completed"
            )
            yield status_update
            
            # Send intermediate message
            message = Message(
                content=f"Processing step {i+1}: {params.content}",
                role="assistant"
            )
            yield message
        
        # Final completion
        task.status = "completed"
        task.output = f"Completed processing: {params.content}"
        self.tasks[task_id] = task
        
        final_status = TaskStatusUpdateEvent(
            task_id=task_id,
            status="completed",
            progress="All steps completed"
        )
        yield final_status

    async def on_get_task(self, params: TaskQueryParams, context=None) -> Task | None:
        """Get a task by ID."""
        logger.info(f"Getting task: {params.task_id}")
        return self.tasks.get(params.task_id)

    async def on_cancel_task(self, params: TaskIdParams, context=None) -> Task:
        """Cancel a task."""
        logger.info(f"Cancelling task: {params.task_id}")
        task = self.tasks.get(params.task_id)
        if task:
            task.status = "cancelled"
            self.tasks[params.task_id] = task
            return task
        else:
            # Return a cancelled task even if not found
            return Task(
                id=params.task_id,
                status="cancelled",
                input="Unknown",
                output="Task not found"
            )


async def run_server():
    """Run the comprehensive Kafka server."""
    logger.info("Starting comprehensive Kafka server...")
    
    # Create request handler
    request_handler = ComprehensiveRequestHandler()
    
    # Create and run Kafka server
    server = KafkaServerApp(
        request_handler=request_handler,
        bootstrap_servers="localhost:9092",
        request_topic="a2a-comprehensive-requests",
        consumer_group_id="a2a-comprehensive-server"
    )
    
    try:
        await server.run()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        await server.stop()


async def run_client():
    """Run comprehensive client examples."""
    logger.info("Starting comprehensive Kafka client...")
    
    # Create agent card
    agent_card = AgentCard(
        name="Comprehensive Agent",
        description="A comprehensive example A2A agent",
        url="https://example.com/comprehensive-agent",
        version="1.0.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="test_skill",
                name="test_skill", 
                description="Test skill",
                tags=["test"],
                input_modes=["text/plain"],
                output_modes=["text/plain"]
            )
        ]
    )
    
    # Create Kafka client transport
    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="localhost:9092",
        request_topic="a2a-comprehensive-requests"
    )
    
    try:
        async with transport:
            # Test 1: Simple message
            logger.info("=== Test 1: Simple Message ===")
            request = MessageSendParams(
                content="Hello, Kafka!",
                role="user"
            )
            
            response = await transport.send_message(request)
            logger.info(f"Response: {response.content}")
            
            # Test 2: Task creation
            logger.info("=== Test 2: Task Creation ===")
            task_request = MessageSendParams(
                content="Create a task for processing data",
                role="user"
            )
            
            task_response = await transport.send_message(task_request)
            if isinstance(task_response, Task):
                logger.info(f"Created task: {task_response.id} (status: {task_response.status})")
                
                # Test 3: Get task
                logger.info("=== Test 3: Get Task ===")
                get_task_request = TaskQueryParams(task_id=task_response.id)
                retrieved_task = await transport.get_task(get_task_request)
                logger.info(f"Retrieved task: {retrieved_task.id} (status: {retrieved_task.status})")
                
                # Test 4: Cancel task
                logger.info("=== Test 4: Cancel Task ===")
                cancel_request = TaskIdParams(task_id=task_response.id)
                cancelled_task = await transport.cancel_task(cancel_request)
                logger.info(f"Cancelled task: {cancelled_task.id} (status: {cancelled_task.status})")
            
            # Test 5: Streaming
            logger.info("=== Test 5: Streaming ===")
            streaming_request = MessageSendParams(
                content="Stream process this data",
                role="user"
            )
            
            logger.info("Starting streaming request...")
            async for stream_response in transport.send_message_streaming(streaming_request):
                if isinstance(stream_response, Task):
                    logger.info(f"Stream - Task: {stream_response.id} (status: {stream_response.status})")
                elif isinstance(stream_response, TaskStatusUpdateEvent):
                    logger.info(f"Stream - Status Update: {stream_response.progress}")
                elif isinstance(stream_response, Message):
                    logger.info(f"Stream - Message: {stream_response.content}")
                else:
                    logger.info(f"Stream - Other: {type(stream_response)} - {stream_response}")
            
            logger.info("Streaming completed!")
                
    except Exception as e:
        logger.error(f"Client error: {e}")
        import traceback
        traceback.print_exc()


async def run_load_test():
    """Run a simple load test."""
    logger.info("Starting load test...")
    
    agent_card = AgentCard(
        name="Load Test Agent",
        description="Load testing agent",
        url="https://example.com/load-test-agent",
        version="1.0.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="test_skill",
                name="test_skill", 
                description="Test skill",
                tags=["test"],
                input_modes=["text/plain"],
                output_modes=["text/plain"]
            )
        ]
    )
    
    transport = KafkaClientTransport(
        agent_card=agent_card,
        bootstrap_servers="localhost:9092",
        request_topic="a2a-comprehensive-requests"
    )
    
    async with transport:
        # Send multiple concurrent requests
        tasks = []
        for i in range(10):
            request = MessageSendParams(
                content=f"Load test message {i}",
                role="user"
            )
            task = asyncio.create_task(transport.send_message(request))
            tasks.append(task)
        
        # Wait for all responses
        responses = await asyncio.gather(*tasks)
        logger.info(f"Load test completed: {len(responses)} responses received")


async def main():
    """Main function to demonstrate usage."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python kafka_comprehensive_example.py [server|client|load]")
        return
    
    mode = sys.argv[1]
    
    if mode == "server":
        await run_server()
    elif mode == "client":
        await run_client()
    elif mode == "load":
        await run_load_test()
    else:
        print("Invalid mode. Use 'server', 'client', or 'load'")


if __name__ == "__main__":
    asyncio.run(main())
