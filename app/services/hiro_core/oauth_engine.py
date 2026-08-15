"""
oauth_engine.py — OAuth (Max aboneliği) yolu için claude-agent-sdk motoru
═══════════════════════════════════════════════════════════════════════════
apikey yolu LangChain + create_react_agent kullanır. OAuth yolu ise farklı bir
mimari ister: claude-agent-sdk (Claude Code CLI subprocess), ClaudeSDKClient,
ve tool'lar SDK'nın in-process MCP formatında.

Tool MANTIĞI tek kaynakta (tools.py'deki fonksiyonlar). Burada onları SDK
formatında sarıyoruz — iş mantığı tekrar yazılmıyor, sadece adapte ediliyor.

Auth: CLAUDE_CODE_OAUTH_TOKEN ortam değişkeni SDK tarafından otomatik okunur
(Claude Code CLI'nin auth mekanizması). Ayrıca sistemde `claude` CLI kurulu ve
`claude setup-token` ile token üretilmiş olmalı.
"""

import os
import json
from loguru import logger

from claude_agent_sdk import (
    tool, create_sdk_mcp_server, ClaudeSDKClient, ClaudeAgentOptions,
    AssistantMessage, TextBlock,
)

# tool iş mantığını mevcut modüllerden al (tek kaynak — tekrar yazma yok)
from app.services.hiro_core.tools import (
    web_search as _web_search,
    run_template as _run_template,
    schedule_task as _schedule_task,
    list_scheduled as _list_scheduled,
    missed_tasks as _missed_tasks,
    send_notification as _send_notification,
    hafiza_getir as _hafiza_getir,
    hafiza_kaydet as _hafiza_kaydet,
    kutuphaneye_ekle as _kutuphaneye_ekle,
    takibe_al as _takibe_al,
    kutuphane_getir as _kutuphane_getir,
)
from app.services.hiro_core.agent import build_prompt

oauth_log = logger.bind(module="oauth_engine")


# LangChain tool'unu çağırıp string sonuç döndüren yardımcı.
# .invoke ile çağırırız çünkü bunlar @tool ile sarılı.
def _call(lc_tool, **kwargs):
    return lc_tool.invoke(kwargs)


# ── SDK tool sarmalayıcıları — iş mantığı yukarıdaki LangChain tool'larından gelir ──
@tool("web_search", "İnternette güncel bilgi ara", {"query": str})
async def sdk_web_search(args):
    return {"content": [{"type": "text", "text": _call(_web_search, query=args["query"])}]}


@tool("run_template", "Browser otomasyon şablonu çalıştır", {"template_name": str, "params": dict})
async def sdk_run_template(args):
    out = _call(_run_template, template_name=args["template_name"], params=args.get("params", {}))
    return {"content": [{"type": "text", "text": out}]}


@tool("schedule_task", "Zamanlanmış görev kur", {
    "action": str, "when": str, "repeat": str, "at": str,
    "template": str, "params": dict, "message": str, "habit": str, "notify": str,
})
async def sdk_schedule_task(args):
    out = _call(_schedule_task,
                action=args.get("action", "notify"), when=args.get("when", ""),
                repeat=args.get("repeat", "once"), at=args.get("at", ""),
                template=args.get("template", ""), params=args.get("params") or {},
                message=args.get("message", ""), habit=args.get("habit", ""),
                notify=args.get("notify", ""))
    return {"content": [{"type": "text", "text": out}]}


@tool("list_scheduled", "Zamanlanmış görevleri listele", {})
async def sdk_list_scheduled(args):
    return {"content": [{"type": "text", "text": _call(_list_scheduled)}]}


@tool("missed_tasks", "Kaçan görevleri göster", {})
async def sdk_missed_tasks(args):
    return {"content": [{"type": "text", "text": _call(_missed_tasks)}]}


@tool("send_notification", "Anlık bildirim gönder", {"message": str})
async def sdk_send_notification(args):
    return {"content": [{"type": "text", "text": _call(_send_notification, message=args["message"])}]}


@tool("hafiza_getir", "Oktay hakkında kayıtlı bilgiyi çek", {"konu": str})
async def sdk_hafiza_getir(args):
    return {"content": [{"type": "text", "text": _call(_hafiza_getir, konu=args.get("konu", ""))}]}


@tool("hafiza_kaydet", "Oktay hakkında bilgi kaydet", {"konu": str, "veri": dict})
async def sdk_hafiza_kaydet(args):
    out = _call(_hafiza_kaydet, konu=args["konu"], veri=args.get("veri", {}))
    return {"content": [{"type": "text", "text": out}]}




@tool("kutuphaneye_ekle", "Film/dizi/anime'yi TMDB'den zenginleştirip izlenenler kütüphanesine ekle", {"isimler": list, "media_type": str})
async def sdk_kutuphaneye_ekle(args):
    out = _call(_kutuphaneye_ekle, isimler=args["isimler"], media_type=args.get("media_type", "tv"))
    return {"content": [{"type": "text", "text": out}]}


@tool("takibe_al", "Bir animeyi takip listesine ekle (yeni bölüm çıkınca haber ver)", {"anime_adi": str, "animecix_url": str, "son_bolum": int})
async def sdk_takibe_al(args):
    out = _call(_takibe_al, anime_adi=args["anime_adi"],
                animecix_url=args.get("animecix_url", ""), son_bolum=args.get("son_bolum", 0))
    return {"content": [{"type": "text", "text": out}]}


@tool("kutuphane_getir", "Medya kütüphanesini sorgula (izlenenler/takip/gecmis) — öneri için", {"ne": str})
async def sdk_kutuphane_getir(args):
    out = _call(_kutuphane_getir, ne=args.get("ne", "izlenenler"))
    return {"content": [{"type": "text", "text": out}]}


_SDK_TOOLS = [
    sdk_web_search, sdk_run_template, sdk_schedule_task, sdk_list_scheduled,
    sdk_missed_tasks, sdk_send_notification, sdk_hafiza_getir, sdk_hafiza_kaydet,
    sdk_kutuphaneye_ekle, sdk_takibe_al, sdk_kutuphane_getir,
]

# in-process MCP server — tool'lar buradan sunulur
_server = create_sdk_mcp_server(name="hiro", version="1.0.0", tools=_SDK_TOOLS)

# allowed_tools: mcp__<server>__<tool> formatında
_ALLOWED = [f"mcp__hiro__{t.name}" for t in _SDK_TOOLS]


def _options(model):
    return ClaudeAgentOptions(
        model=model,
        system_prompt=build_prompt(),
        mcp_servers={"hiro": _server},
        allowed_tools=_ALLOWED,
        permission_mode="acceptEdits",  # tool'ları otomatik onayla (kendi tool'larımız)
        max_turns=6,   # tool döngüsünü sınırla — aynı kontrolü 3-4 kez yapmasın
    )


async def oauth_chat_async(message: str, model: str) -> str:
    """OAuth üzerinden tek tur sohbet. claude-agent-sdk ClaudeSDKClient kullanır."""
    reply_parts = []
    async with ClaudeSDKClient(options=_options(model)) as client:
        await client.query(message)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        reply_parts.append(block.text)
    return "\n".join(reply_parts).strip() or "(boş cevap)"


def oauth_chat(message: str, model: str) -> str:
    """Senkron sarmalayıcı — ayrı thread'de kendi event loop'unda çalıştırır."""
    import threading, asyncio
    result = {}
    def worker():
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(oauth_chat_async(message, model))
        except Exception as e:
            result["value"] = f"OAuth hata: {e}"
        finally:
            loop.close()
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return result.get("value", "(boş)")