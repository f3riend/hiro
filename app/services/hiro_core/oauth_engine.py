"""
oauth_engine.py — OAuth (Max aboneliği) yolu, claude-agent-sdk
═══════════════════════════════════════════════════════════════════════════
TEK KAYNAK: tools.py'deki TOOLS listesini OTOMATİK olarak SDK formatına sarar.
Yeni tool eklerken artık iki yere yazmak yok — tools.py'ye ekle, oauth otomatik
görür. LangChain @tool decorator'ının .name/.description/.args bilgisinden
SDK MCP tool'u üretiyoruz.

apikey yolu LangChain + create_react_agent kullanır (agent.py). oauth yolu bu.
İkisi de AYNI TOOLS listesini kullanır — tek kaynak.
"""

import os
from loguru import logger

from claude_agent_sdk import (
    tool, create_sdk_mcp_server, ClaudeSDKClient, ClaudeAgentOptions,
    AssistantMessage, TextBlock,
)

from app.services.hiro_core.tools import TOOLS
from app.services.hiro_core.agent import build_prompt

oauth_log = logger.bind(module="oauth_engine")

_TYPE_MAP = {
    "string": str, "integer": int, "number": float,
    "boolean": bool, "array": list, "object": dict,
}


def _lc_tool_to_schema(lc_tool):
    """LangChain tool'unun args'ından SDK input_schema (dict) üret."""
    schema = {}
    try:
        for name, spec in lc_tool.args.items():
            jtype = spec.get("type", "string")
            schema[name] = _TYPE_MAP.get(jtype, str)
    except Exception:
        pass
    return schema


def _make_sdk_tool(lc_tool):
    """Bir LangChain tool'unu SDK tool'una otomatik sar."""
    name = lc_tool.name
    description = (lc_tool.description or name).split("\n")[0][:200]
    schema = _lc_tool_to_schema(lc_tool)

    @tool(name, description, schema)
    async def _wrapped(args, _lc=lc_tool):
        try:
            out = _lc.invoke(args)
        except Exception as e:
            out = f'{{"ok": false, "error": "{str(e)[:200]}"}}'
        return {"content": [{"type": "text", "text": str(out)}]}

    return _wrapped


# TÜM tool'ları otomatik sar — tek kaynak TOOLS listesi
_SDK_TOOLS = [_make_sdk_tool(t) for t in TOOLS]
_server = create_sdk_mcp_server(name="hiro", version="1.0.0", tools=_SDK_TOOLS)
_ALLOWED = [f"mcp__hiro__{t.name}" for t in TOOLS]


def _options(model):
    return ClaudeAgentOptions(
        model=model,
        system_prompt=build_prompt(),
        mcp_servers={"hiro": _server},
        allowed_tools=_ALLOWED,
        permission_mode="acceptEdits",
        max_turns=6,
    )


async def oauth_chat_async(message: str, model: str, history: list = None, summary: str = "") -> str:
    reply_parts = []
    async with ClaudeSDKClient(options=_options(model)) as client:
        # geçmiş varsa, konuşmayı geçmişle birlikte kur (son mesaj hariç geçmiş,
        # son mesaj asıl sorgu). SDK tek query alır, o yüzden geçmişi metne göm.
        parts = []
        if summary:
            parts.append(f"[ÖNCEKİ KONUŞMALARIN ÖZETİ]\n{summary}")
        if history and len(history) > 1:
            ctx_lines = []
            for m in history[:-1]:  # son hariç (o zaten message)
                who = "Oktay" if m["role"] == "user" else "Sen (Hiro)"
                ctx_lines.append(f"{who}: {m['content']}")
            parts.append("[ÖNCEKİ KONUŞMA — bağlam için, tekrar cevaplama]\n" + "\n".join(ctx_lines))
        if parts:
            full = "\n\n".join(parts) + f"\n\n[ŞİMDİKİ MESAJ]\n{message}"
            await client.query(full)
        else:
            await client.query(message)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        reply_parts.append(block.text)
    return "\n".join(reply_parts).strip() or "(boş cevap)"


def oauth_chat(message: str, model: str, history: list = None, summary: str = "") -> str:
    """Senkron sarmalayıcı — ayrı thread'de kendi event loop'unda çalıştırır."""
    import threading, asyncio
    result = {}

    def worker():
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(oauth_chat_async(message, model, history, summary))
        except Exception as e:
            result["value"] = f"OAuth hata: {e}"
        finally:
            loop.close()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return result.get("value", "(boş)")