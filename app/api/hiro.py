from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger

from app.services.hiro_core import build_agent, chat

hiro_log = logger.bind(module="hiro_api")

hiro = APIRouter()

agent = build_agent()


class ChatRequest(BaseModel):
    message: str


@hiro.post("/chat")
async def chat_endpoint(req: ChatRequest):
    reply = chat(agent, req.message)
    hiro_log.info(f"chat: {req.message[:60]}")
    return JSONResponse({"reply": reply})