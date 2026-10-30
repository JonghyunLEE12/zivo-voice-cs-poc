"""Explicit test-only uvicorn target for the real TCP/WebSocket smoke client."""
import os
from fastapi import FastAPI
from server.pipeline.api import create_router
from tests.voice_stubs import runtime

app = FastAPI()
app.include_router(create_router(runtime(os.environ.get('T9_STUB_LOG_DIR', 'reports/t9/stub'))))
