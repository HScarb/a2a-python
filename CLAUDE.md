# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is the A2A (Agent2Agent) Protocol Python SDK, which implements an open standard by Google that enables communication and interoperability between AI agents built on different frameworks. The SDK provides both client and server components for building A2A-compliant agents.

## Key Architecture Components

### Core Structure
- `src/a2a/` - Main SDK package
- `src/a2a/client/` - Client implementation for connecting to A2A agents
- `src/a2a/server/` - Server components for building A2A agents
- `src/a2a/types.py` - Pydantic models for A2A protocol messages
- `src/a2a/grpc/` - Generated gRPC protocol buffers (auto-generated, do not edit)

### Server Architecture
- `server/apps/jsonrpc/` - FastAPI/Starlette JSON-RPC server applications
- `server/request_handlers/` - Request routing and processing logic
- `server/tasks/` - Task management, storage, and push notifications
- `server/agent_execution/` - Agent execution context and request processing
- `server/events/` - Event queuing and streaming support

### Client Architecture
- `client/client.py` - Main A2A client for making requests to agents
- `client/grpc_client.py` - gRPC client implementation
- `client/auth/` - Authentication and credential management
- `client/middleware.py` - Request/response middleware

### Utilities
- `utils/` - Helper functions for messages, artifacts, tasks, and telemetry
- `auth/` - User authentication utilities
- `extensions/` - Common extension functionality

## Development Commands

### Testing
```bash
uv run pytest                    # Run all tests
uv run pytest tests/            # Run specific test directory
uv run pytest -v               # Verbose output
uv run pytest --cov=src/a2a    # Run with coverage
```

### Code Quality
```bash
uv run ruff check               # Lint code
uv run ruff check --fix         # Lint and auto-fix issues
uv run ruff format              # Format code
uv run mypy src/                # Type checking
```

### Development Setup
```bash
uv sync --dev                   # Install dependencies including dev group
uv add <package>                # Add new dependency
uv run <command>                # Run commands in the virtual environment
```

### Build and Distribution
```bash
uv build                        # Build wheel and sdist
uv publish                      # Publish to PyPI (requires authentication)
```

## Protocol Implementation Notes

### A2A Protocol Basics
- Uses JSON-RPC 2.0 over HTTP(S) for communication
- Supports both request/response and Server-Sent Events (SSE) for streaming
- Agent discovery via Agent Cards at `/.well-known/agent.json`
- Task lifecycle: submitted → working → [input_required] → completed/failed/canceled

### Key Methods
- `message/send` - Send message and get final task state
- `message/stream` - Send message with real-time updates via SSE
- `tasks/get` - Retrieve current task state
- `tasks/cancel` - Cancel a running task
- `tasks/pushNotificationConfig/set|get` - Configure push notifications

### Optional Features
- gRPC support via `[grpc]` extra
- Database persistence via `[sql]`, `[postgresql]`, `[mysql]`, `[sqlite]` extras
- Push notifications for asynchronous task updates
- Streaming updates for long-running tasks

## Testing Strategy

Tests are organized by component:
- `tests/client/` - Client functionality tests
- `tests/server/` - Server component tests
- `tests/auth/` - Authentication tests
- `tests/utils/` - Utility function tests

Use `pytest-asyncio` for async tests and `pytest-mock` for mocking dependencies.

## Code Generation

Some files are auto-generated:
- `src/a2a/grpc/a2a_pb2.py` and related - Generated from Protocol Buffer definitions
- Use `scripts/generate_types.sh` to regenerate types when protocol changes

## Dependencies

Core dependencies:
- FastAPI/Starlette for web framework
- Pydantic for data validation
- httpx for HTTP client
- OpenTelemetry for observability

Optional dependencies support gRPC, databases, and encryption features.