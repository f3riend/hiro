from langchain_core.tools import tool
from loguru import logger
from pathlib import Path
from openai import OpenAI
import subprocess
import asyncio
import json

from app.services.browser_engine import run_template as engine_run
from app.services.memory_engine import get_memory as _get_mem, save_memory as _save_mem, list_topics
from app.services.memory_engine.memory import add_episode, recent_episodes, set_working, compute_streaks
from app.services.media_engine import (get_metadata, add_watched, list_watched,
    add_tracking, list_tracking, add_history, recent_history)
from app.services.browser_engine.downloader import download as _ytdlp_download
from app.core.settings import settings
from app.services.hiro_core.conversation import search_history

tools_log = logger.bind(module="hiro_tools")

TEMPLATES_DIR = Path("templates")


def available_templates() -> list[dict]:
    out = []
    for p in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            out.append({"name": p.stem, "description": t.get("description", ""),
                        "params": t.get("params", [])})
        except Exception:
            out.append({"name": p.stem, "description": "", "params": []})
    return out


@tool
def web_search(query: str) -> str:
    """Search the web for current information. Use for anything that must be up to
    date or is outside your training data: whether a new season/episode is out,
    prices, latest news, release dates. Don't guess — search and verify."""
    import os
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return json.dumps({"error": "web_search needs OPENAI_API_KEY"}, ensure_ascii=False)
    client = OpenAI(api_key=key)
    try:
        resp = client.responses.create(
            model="gpt-4o",
            tools=[{"type": "web_search_preview"}],
            input=query,
        )
        tools_log.info(f"web_search: {query}")
        return resp.output_text
    except Exception as e:
        tools_log.warning(f"web_search failed: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# engine is async but this tool runs inside FastAPI's running loop;
# asyncio.run would fail, so run the coroutine in a dedicated thread.
def _run_async(coro):
    import threading
    result = {}
    def worker():
        loop = asyncio.new_event_loop()
        try:
            result["value"] = loop.run_until_complete(coro)
        finally:
            loop.close()
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return result.get("value")


@tool
def list_templates() -> str:
    """List every available browser-automation template with its description and
    params. Call this when you need to pick a template and aren't sure which fits,
    or to discover what automations exist. New templates are just JSON files — this
    always reflects what's on disk, so trust it over memory."""
    return json.dumps({"templates": available_templates()}, ensure_ascii=False)


@tool
def run_template(template_name: str, params: dict) -> str:
    """Run a browser automation template (Hiro engine) in the real browser.
    Use to actually do things: scan a site's grid, open pages, play a video, capture
    and download it. Read each template's description (see list_templates) and fill
    its params yourself from context — e.g. for the animecix template, pull the user's
    tracking list first (get_library 'tracking') and pass those names as favori_animeler.
    template_name must be an existing template. Returns structured JSON (ok, data,
    changes, notifications). If the name is wrong the result lists available templates."""
    tpl_path = TEMPLATES_DIR / f"{template_name}.json"
    if not tpl_path.exists():
        return json.dumps({"ok": False, "error": f"template '{template_name}' not found",
                           "available": available_templates()}, ensure_ascii=False)
    template = json.loads(tpl_path.read_text(encoding="utf-8"))
    try:
        result = _run_async(engine_run(template, params))
        tools_log.info(f"run_template: {template_name} ok={result.get('ok')}")
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        tools_log.warning(f"run_template failed: {e}")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


# schedules-engine is a CLI; call it as a subprocess so the AI produces the same
# dynamic params it would on the command line.
SCHEDULER = "app/services/schedules_engine/scheduler.py"


@tool
def schedule_task(action: str, when: str = "", repeat: str = "once", at: str = "",
                  template: str = "", params: dict = None, message: str = "",
                  habit: str = "", notify: str = "") -> str:
    """Schedule a task to run later (Hiro schedules-engine). Use for anything
    time-based: download tonight, remind every 3 days, weekly checks, daily briefing.
    action: 'browser_engine' (run a template) | 'notify' (send a reminder) | 'heartbeat'
            (daily briefing).
    when: ALWAYS relative — '+2min' '+3h' '+1d' 'tonight' 'tomorrow 09:00'. NEVER compute
          an ISO date yourself — the scheduler converts these.
    repeat: 'once'|'every:3d'|'every:1w'|'weekdays:1,3,5,6' (1=Mon..7=Sun).
    at: 'HH:MM' for repeating jobs. For browser_engine give template + params; for
    notify give message (+ optional habit key). Returns JSON with job id and next_run."""
    cmd = ["python", SCHEDULER, "add", "--action", action]
    if when:
        cmd += ["--when", when]
    if repeat and repeat != "once":
        cmd += ["--repeat", repeat]
    if at:
        cmd += ["--at", at]
    if template:
        cmd += ["--template", template]
    if params:
        cmd += ["--params", *[f"{k}={v}" for k, v in params.items()]]
    if message:
        cmd += ["--message", message]
    if habit:
        cmd += ["--habit", habit]
    if notify:
        cmd += ["--notify", notify]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        tools_log.info(f"schedule_task: {action} repeat={repeat}")
        return out.stdout.strip() or json.dumps({"ok": False, "error": out.stderr[:300]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
def list_scheduled() -> str:
    """List all scheduled tasks (pending, repeating). Use when the user asks what's
    scheduled or coming up, or before editing/cancelling a task."""
    try:
        out = subprocess.run(["python", SCHEDULER, "list"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
def missed_tasks() -> str:
    """Show tasks whose time passed while the machine was off. Use at startup or when
    the user asks what they missed, so they can reschedule or cancel."""
    try:
        out = subprocess.run(["python", SCHEDULER, "missed"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


NOTIFIER = "app/services/notification_engine/notifier.py"


@tool
def send_notification(message: str) -> str:
    """Send a notification to Oktay right now (desktop + log). For time-based reminders
    use schedule_task with action='notify' instead."""
    try:
        out = subprocess.run(["python", NOTIFIER, "send", "--message", message],
                             capture_output=True, text=True, timeout=15)
        tools_log.info(f"send_notification: {message[:40]}")
        return out.stdout.strip()
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@tool
def get_memory(topic: str = "") -> str:
    """Fetch stored facts about Oktay (lazy-fetch). Empty topic lists available topics;
    a topic returns its content. Topics: profile, goals, routines, favorites, etc.
    Call when something personal about Oktay (his prefs, goals, habits) is needed.
    Don't assume — fetch when needed."""
    if not topic:
        return json.dumps({"topics": list_topics()}, ensure_ascii=False)
    data = _get_mem(topic)
    if not data:
        return json.dumps({"topic": topic, "note": "no record yet"}, ensure_ascii=False)
    tools_log.info(f"get_memory: {topic}")
    return json.dumps({"topic": topic, "content": data}, ensure_ascii=False)


@tool
def save_memory_tool(topic: str, data: dict) -> str:
    """Save/update a durable fact about Oktay. topic: 'profile'|'goals'|'routines'|
    'favorites' etc. data: fields to store (dict). MERGES with existing content (doesn't
    overwrite). Call when Oktay states something lasting: a goal, preference, routine,
    favorite. Don't store transient things."""
    saved = _save_mem(topic, data, birlestir=True)
    tools_log.info(f"save_memory: {topic} <- {list(data.keys())}")
    return json.dumps({"ok": True, "topic": topic, "current": saved}, ensure_ascii=False)


@tool
def add_to_library(names: list, media_type: str = "tv") -> str:
    """Enrich a movie/show/anime from TMDB and add it to the watched library.
    names: list of names for matching — animecix keeps both Japanese and English titles,
    give BOTH (e.g. ["Tensei shitara Slime", "That Time I Got Reincarnated as a Slime"]).
    media_type: tv (show/anime) | movie. Pulls poster, cast, overview, genres."""
    meta = get_metadata(names, media_type)
    if not meta:
        return json.dumps({"ok": False, "note": "no TMDB match"}, ensure_ascii=False)
    add_watched(meta)
    add_history(meta["title"], meta.get("id"))
    tools_log.info(f"add_to_library: {meta['title']}")
    return json.dumps({"ok": True, "added": meta["title"], "year": meta.get("year"),
                       "genres": meta.get("genres"), "poster": meta.get("poster_url")},
                      ensure_ascii=False)


@tool
def track_anime(name: str, animecix_url: str = "", last_episode: int = 0, alt_names: str = "") -> str:
    """Add an anime to the tracking list — you'll be told when a new episode is out
    (not auto-downloaded; Oktay decides). name: tracking name Oktay uses (e.g. "Tensura").
    animecix_url: episode page URL. last_episode: current latest episode number.
    alt_names: OTHER names shown in the animecix grid / TMDB, comma-separated (e.g.
    "Tensei shitara Slime Datta Ken, That Time I Got Reincarnated as a Slime"). This is
    IMPORTANT for grid matching — the grid shows the full name, Oktay uses a short one."""
    add_tracking(name, animecix_url, last_episode, alt_adlar=alt_names)
    tools_log.info(f"track_anime: {name} (ep {last_episode})")
    return json.dumps({"ok": True, "tracking": name, "last_episode": last_episode,
                       "note": "will alert on new episode"}, ensure_ascii=False)


@tool
def get_library(what: str = "watched") -> str:
    """Query the media library. what: 'watched' (archive) | 'tracking' (tracking list)
    | 'history' (recently watched). Use for recommendations, 'what did I watch', or to
    get the tracking list before running the animecix template (pass those names as
    favori_animeler). For tracking, each entry has name + alt_names — pass both for
    grid matching."""
    if what == "tracking":
        rows = list_tracking()
        data = [{"name": r["anime_adi"], "alt_names": r.get("alt_adlar", ""),
                 "last_episode": r.get("son_bolum"), "url": r.get("animecix_url")}
                for r in rows]
    elif what == "history":
        data = recent_history()
    else:
        data = [{"title": w["title"], "type": w.get("type"), "year": w.get("year"),
                 "genres": w.get("genres"), "rating": w.get("rating")}
                for w in list_watched()]
    return json.dumps({"ok": True, "what": what, "records": data}, ensure_ascii=False)


@tool
def download_video(url: str, title: str = "") -> str:
    """Download a video via yt-dlp (YouTube and any yt-dlp-supported site). url: video
    link. title: filename (empty = video's own title). Resumes with --continue. Use when
    a direct video URL is given and Oktay says download. For animecix, use run_template
    instead (it captures the stream)."""
    res = _ytdlp_download(url, title=title or None)
    if res.get("done"):
        tools_log.info(f"download_video: done {url[:50]}")
        return json.dumps({"ok": True, "downloaded": url, "note": "saved to downloads/"}, ensure_ascii=False)
    return json.dumps({"ok": False, "url": url, "returncode": res.get("returncode"),
                       "note": "incomplete or failed"}, ensure_ascii=False)


@tool
def log_event(event: str, detail: dict = None) -> str:
    """Log an event to episodic memory — what Oktay did and when. For habit/progress
    tracking: "worked out", "showered", "finished X", "watched Tensura ep17". Feeds
    streaks and the daily briefing. Log when Oktay says he DID something (a past event,
    not a future plan)."""
    add_episode(event, detail)
    tools_log.info(f"log_event: {event}")
    return json.dumps({"ok": True, "logged": event}, ensure_ascii=False)


@tool
def get_progress(event: str = "") -> str:
    """Get habit/progress status — streaks and recent events. Empty event returns all
    streaks (days in a row) + recent events; a name returns that event's records. Use for
    "what's my streak", "how many days working out", "what did I do lately". Returns empty
    honestly if no records."""
    streaks = compute_streaks()
    recent = [{"event": e["olay"], "when": (e.get("ne_zaman") or "")[:10]}
              for e in recent_episodes(gun=7, limit=30)]
    if event:
        recent = [e for e in recent if event.lower() in e["event"].lower()]
        streak = streaks.get(event, 0)
        if not streak:
            for k, v in streaks.items():
                if event.lower() in k.lower():
                    streak = v
                    break
        return json.dumps({"ok": True, "event": event, "streak_days": streak,
                           "recent": recent}, ensure_ascii=False)
    tools_log.info("get_progress: all streaks")
    return json.dumps({"ok": True, "streaks": streaks, "recent": recent}, ensure_ascii=False)





@tool
def search_conversation(query: str) -> str:
    """Search older/archived conversation for something not in recent messages or the
    running summary. Use ONLY when the user refers to something from far back that you
    can't see in the current context — "what did we discuss weeks ago about X", "you
    mentioned Y before". Searches archived messages + summary by keyword. Don't use for
    recent things (those are already in context). Returns matches or says the topic may
    never have come up."""
    from app.services.hiro_core.conversation import search_history as _sh, get_active_user
    res = _sh(get_active_user(), query)  # aktif kullanıcının arşivini ara
    tools_log.info(f"search_conversation: {query[:40]}")
    return json.dumps(res, ensure_ascii=False)


TOOLS = [
    web_search, list_templates, run_template, schedule_task, list_scheduled,
    missed_tasks, send_notification, get_memory, save_memory_tool,
    add_to_library, track_anime, get_library, download_video,
    log_event, get_progress, search_conversation,
]