#!/usr/bin/env python3
"""Setup script for Kafka development environment."""

import asyncio
import subprocess
import sys
import time
from pathlib import Path


def run_command(cmd: str, cwd: Path = None) -> int:
    """Run a shell command and return exit code."""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    return result.returncode


async def check_kafka_health() -> bool:
    """Check if Kafka is healthy and ready."""
    try:
        # Try to list topics as a health check
        result = subprocess.run(
            "docker exec a2a-kafka kafka-topics --list --bootstrap-server localhost:9092",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


async def wait_for_kafka(max_wait: int = 60) -> bool:
    """Wait for Kafka to be ready."""
    print("Waiting for Kafka to be ready...")
    
    for i in range(max_wait):
        if await check_kafka_health():
            print("✅ Kafka is ready!")
            return True
        
        print(f"⏳ Waiting... ({i+1}/{max_wait})")
        await asyncio.sleep(1)
    
    print("❌ Kafka failed to start within timeout")
    return False


def main():
    """Main setup function."""
    project_root = Path(__file__).parent.parent
    
    print("🚀 Setting up A2A Kafka development environment...")
    
    # Check if Docker is available
    if run_command("docker --version") != 0:
        print("❌ Docker is not available. Please install Docker first.")
        sys.exit(1)
    
    # Check if Docker Compose is available
    if run_command("docker compose version") != 0:
        print("❌ Docker Compose is not available. Please install Docker Compose first.")
        sys.exit(1)
    
    print("✅ Docker and Docker Compose are available")
    
    # Start Kafka services
    print("\n📦 Starting Kafka services...")
    if run_command("docker compose -f docker-compose.kafka.yml up -d", cwd=project_root) != 0:
        print("❌ Failed to start Kafka services")
        sys.exit(1)
    
    # Wait for Kafka to be ready
    print("\n⏳ Waiting for services to be ready...")
    if not asyncio.run(wait_for_kafka()):
        print("❌ Kafka services failed to start properly")
        print("Try running: docker compose -f docker-compose.kafka.yml logs")
        sys.exit(1)
    
    # Install Python dependencies
    print("\n📚 Installing Python dependencies...")
    if run_command("pip install aiokafka", cwd=project_root) != 0:
        print("⚠️  Warning: Failed to install aiokafka. You may need to install it manually.")
    else:
        print("✅ aiokafka installed successfully")
    
    # Show status
    print("\n📊 Service Status:")
    run_command("docker compose -f docker-compose.kafka.yml ps", cwd=project_root)
    
    print("\n🎉 Setup complete!")
    print("\n📋 Next steps:")
    print("1. Start the server: python examples/kafka_comprehensive_example.py server")
    print("2. In another terminal, run the client: python examples/kafka_comprehensive_example.py client")
    print("3. View Kafka UI at: http://localhost:8080")
    print("\n🛑 To stop services: docker compose -f docker-compose.kafka.yml down")


if __name__ == "__main__":
    main()
