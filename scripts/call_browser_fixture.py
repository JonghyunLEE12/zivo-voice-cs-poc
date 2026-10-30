"""Test-only call UI server: real call pipeline, fake STT/LLM/TTS, no model access."""
from fastapi import FastAPI
from server.call_web import router as web_router
from server.pipeline.api import create_router
from tests.voice_stubs import runtime, StubTTS
app = FastAPI()
app.include_router(web_router)
app.include_router(create_router(runtime('reports/t10/stub', tts=StubTTS(chunks=30))))
