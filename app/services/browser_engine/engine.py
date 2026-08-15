from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from datetime import datetime
from pathlib import Path
from loguru import logger
import random
import time
import json
import re

from app.core.settings import settings
from app.services.browser_engine.video_detector import capture_video_urls
from app.services.browser_engine.downloader import download as ytdlp_download

engine_log = logger.bind(module="browser_engine")

CDP_URL = "http://127.0.0.1:9222"
STATE_DIR = Path("hiro_state")


# Run context: holds params, extracted data, notifications, errors, diffs
class Context:
    def __init__(self, params: dict):
        self.vars = dict(params)
        self.notifications = []
        self.matched = []
        self.data = {}
        self.errors = []
        self.changes = {}

    def resolve(self, text):
        if not isinstance(text, str):
            return text
        def repl(m):
            return str(self.vars.get(m.group(1), m.group(0)))
        # supports {param}, {var} and dotted {obj.field}
        return re.sub(r"\{([a-zA-Z0-9_.]+)\}", repl, text)

    def set(self, k, v):
        self.vars[k] = v

    def get(self, k, d=None):
        return self.vars.get(k, d)

    def notify(self, msg):
        self.notifications.append(str(msg))

    def record_error(self, where, msg):
        self.errors.append({"where": where, "error": str(msg)[:300]})


def _pick_video(videos, pick="m3u8"):
    if not videos:
        return None
    if pick == "all":
        return videos[0]
    for v in videos:
        if v["type"] in ("M3U8", "HLS Master", "DASH"):
            return v
    return videos[0]


async def resolve_locator(page, selectors, ctx):
    if isinstance(selectors, str):
        selectors = [{"type": "css", "value": selectors}]
    for sel in selectors:
        stype = sel.get("type", "css")
        sval = ctx.resolve(sel.get("value", ""))
        try:
            if stype == "xpath":
                loc = page.locator(f"xpath={sval}")
            elif stype == "text":
                loc = page.get_by_text(sval, exact=False)
            else:
                loc = page.locator(sval)
            if await loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


# field spec: ".title" -> text | ".title @href" -> attr | "@src" -> scope attr
async def read_field(scope, field_spec, ctx):
    field_spec = ctx.resolve(field_spec)
    attr = "text"
    sel = field_spec
    if " @" in field_spec:
        sel, attr = field_spec.rsplit(" @", 1)
        sel = sel.strip()
    elif field_spec.startswith("@"):
        sel, attr = "", field_spec[1:]
    try:
        target = scope if sel == "" else scope.locator(sel).first
        if attr == "text":
            return (await target.inner_text(timeout=1500)).strip()
        return await target.get_attribute(attr, timeout=1500)
    except Exception:
        return ""


def eval_condition(check):
    for op in [" contains ", " == ", " != ", " >= ", " <= ", " > ", " < "]:
        if op in check:
            left, right = check.split(op, 1)
            left, right = left.strip().strip("'\""), right.strip().strip("'\"")
            if op == " contains ":
                return right.lower() in left.lower()
            if op == " == ":
                return left == right
            if op == " != ":
                return left != right
            try:
                l, r = float(left), float(right)
                return {" > ": l > r, " < ": l < r, " >= ": l >= r, " <= ": l <= r}[op]
            except ValueError:
                return False
    return bool(check) and check not in ("[]", "{}", "None", "")


class Engine:
    def __init__(self, page, ctx, browser_ctx, mode="test"):
        self.page = page
        self.ctx = ctx
        self.browser_ctx = browser_ctx
        self.mode = mode
        self.steps_run = 0

    async def human_pause(self, base=0.0):
        if self.mode == "human":
            await self.page.wait_for_timeout(int((base + random.uniform(0.4, 1.4)) * 1000))

    async def run_steps(self, steps):
        for step in steps:
            sig = await self.run_block(step)
            if sig in ("stop", "abort"):
                return sig
        return None

    async def run_block(self, block):
        do = block.get("do")
        method = getattr(self, f"blk_{do}", None)
        if not method:
            engine_log.warning(f"unknown block: {do}")
            self.ctx.record_error(do, "unknown block")
            return None
        self.steps_run += 1
        try:
            result = await method(block)
            await self.human_pause()
            return result
        except PWTimeout:
            engine_log.warning(f"{do}: timeout")
            self.ctx.record_error(do, "timeout")
            return "abort" if block.get("required") else None
        except Exception as e:
            engine_log.warning(f"{do}: {str(e)[:120]}")
            self.ctx.record_error(do, e)
            return "abort" if block.get("required") else None

    # navigation
    async def blk_navigate(self, b):
        url = self.ctx.resolve(b["url"])
        engine_log.info(f"navigate -> {url}")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=b.get("timeout_ms", 30000))

    async def blk_wait(self, b):
        sel = self.ctx.resolve(b["selector"])
        await self.page.wait_for_selector(sel, timeout=b.get("timeout_ms", 10000))

    # poll until present; tolerant to slow networks
    async def blk_wait_smart(self, b):
        sel = self.ctx.resolve(b["selector"])
        max_ms = b.get("max_ms", 30000)
        interval = b.get("interval_ms", 1000)
        elapsed = 0
        while elapsed < max_ms:
            try:
                if await self.page.locator(sel).count() > 0:
                    return
            except Exception:
                pass
            await self.page.wait_for_timeout(interval)
            elapsed += interval
        self.ctx.record_error("wait_smart", f"{sel} not found")
        return "abort" if b.get("required") else None

    async def blk_delay(self, b):
        await self.page.wait_for_timeout(int(b.get("seconds", 1) * 1000))

    async def blk_new_tab(self, b):
        url = self.ctx.resolve(b.get("url", ""))
        if "{" in url and "}" in url:
            engine_log.warning(f"new_tab skipped, unresolved ref: {url}")
            return
        self.page = await self.browser_ctx.new_page()
        if url:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

    async def blk_close_page(self, b):
        try:
            await self.page.close()
        except Exception:
            pass

    # interaction
    async def blk_click(self, b):
        loc = await resolve_locator(self.page, b.get("selectors") or b.get("selector"), self.ctx)
        if not loc:
            self.ctx.record_error("click", "not found")
            return
        try:
            await loc.click(timeout=b.get("timeout_ms", 5000))
        except PWTimeout:
            await loc.click(timeout=3000, force=True)

    async def blk_fill(self, b):
        loc = await resolve_locator(self.page, b.get("selectors") or b.get("selector"), self.ctx)
        val = self.ctx.resolve(b.get("value", ""))
        if not loc:
            return
        await loc.fill(val, timeout=b.get("timeout_ms", 5000))
        if b.get("submit"):
            await loc.press("Enter")
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PWTimeout:
                pass

    # real keyboard events; use instead of fill for live-search / disabled buttons
    async def blk_type(self, b):
        loc = await resolve_locator(self.page, b.get("selectors") or b.get("selector"), self.ctx)
        val = self.ctx.resolve(b.get("value", ""))
        if not loc:
            return
        await loc.click()
        await loc.fill("")
        delay = b.get("delay_ms", 60) if self.mode == "test" else random.randint(90, 180)
        await loc.press_sequentially(val, delay=delay)
        await self.page.wait_for_timeout(b.get("after_ms", 800))
        if b.get("submit"):
            await loc.press("Enter")
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            except PWTimeout:
                pass

    async def blk_select_option(self, b):
        loc = await resolve_locator(self.page, b.get("selector"), self.ctx)
        val = self.ctx.resolve(b.get("value", ""))
        if loc:
            try:
                await loc.select_option(value=val, timeout=5000)
            except Exception:
                await loc.select_option(label=val, timeout=5000)

    async def blk_keypress(self, b):
        keys = b.get("keys", "")
        pw = keys.replace("Ctrl", "Control").replace("Cmd", "Meta")
        await self.page.keyboard.press(pw)

    async def blk_capture_video(self, b):
        # Extension'ın yerini alan video yakalayıcı: oynatmayı tetikle, ağ
        # trafiğinden video URL'ini yakala. download:true ise hemen yt-dlp'ye verir.
        trigger = self.ctx.resolve(b.get("trigger") or b.get("selector") or "")
        wait_ms = b.get("wait_ms", 8000)
        videos = await capture_video_urls(self.page, trigger_selector=trigger, wait_ms=wait_ms)

        if not videos:
            self.ctx.notify("video URL yakalanamadı (oynatma tetiklenmedi olabilir)")
            self.ctx.set("captured_videos", [])
            return []

        pick = b.get("pick", "m3u8")
        chosen = _pick_video(videos, pick)
        self.ctx.set("captured_videos", videos)
        self.ctx.set("video_url", chosen["url"] if chosen else None)
        self.ctx.notify(f"video yakalandi: {chosen['type'] if chosen else '?'} ({len(videos)} aday)")

        # zamanlanmamış otomatik indirme: template download:true derse hemen indir
        if b.get("download") and chosen:
            title = self.ctx.resolve(b.get("title") or "") or self.ctx.get("anime_adi") or None
            if not title or title == "":
                title = None
            res = ytdlp_download(chosen["url"], title=title)
            self.ctx.set("download_result", res)
            if res.get("done"):
                self.ctx.notify(f"indirildi: {chosen['url'][:60]}")
            else:
                self.ctx.notify(f"indirme yarim kaldi (kod {res.get('returncode')}), tekrar denenebilir")
            return res
        return videos

    async def blk_scroll(self, b):
        d = b.get("direction", "down")
        a = b.get("amount", 3)
        dy = a * 400 * (1 if d == "down" else -1)
        await self.page.mouse.wheel(0, dy)
        await self.page.wait_for_timeout(600)

    # data
    async def blk_extract(self, b):
        as_name = b.get("as", "extracted")
        val = await read_field(self.page, b["selector"] + " @" + b.get("attr", "text"), self.ctx)
        self.ctx.set(as_name, val)
        self.ctx.data[as_name] = val

    async def blk_extract_list(self, b):
        sel = self.ctx.resolve(b["selector"])
        fields = b.get("fields", {})
        as_name = b.get("as", "list")
        limit = b.get("limit", 100)
        items = self.page.locator(sel)
        count = min(await items.count(), limit)
        result = []
        for i in range(count):
            item = items.nth(i)
            result.append({k: await read_field(item, fs, self.ctx) for k, fs in fields.items()})
        self.ctx.set(as_name, result)
        self.ctx.data[as_name] = result
        engine_log.info(f"extract_list -> {as_name} ({len(result)} items)")

    async def blk_get_attribute(self, b):
        loc = await resolve_locator(self.page, b.get("selector"), self.ctx)
        attr = b.get("attr", "href")
        as_name = b.get("as", "attr_val")
        if loc:
            val = await loc.get_attribute(attr)
            self.ctx.set(as_name, val)
            self.ctx.data[as_name] = val

    async def blk_screenshot(self, b):
        path = self.ctx.resolve(b.get("path", "screenshot.png"))
        sel = b.get("selector")
        if sel:
            loc = await resolve_locator(self.page, sel, self.ctx)
            if loc:
                await loc.screenshot(path=path)
        else:
            await self.page.screenshot(path=path)

    # control flow
    async def blk_loop_pages(self, b):
        next_btn = self.ctx.resolve(b["next_btn"])
        max_p = b.get("max", 5)
        body = b.get("body", [])
        for pg in range(1, max_p + 1):
            sig = await self.run_steps(body)
            if sig in ("stop", "abort"):
                return sig
            try:
                btn = self.page.locator(next_btn)
                if not await btn.is_visible(timeout=1500) or await btn.is_disabled(timeout=500):
                    return
                await btn.click()
                await self.page.wait_for_timeout(1000)
            except PWTimeout:
                return

    async def blk_loop_url_pattern(self, b):
        url_tpl = b["url"]
        i_from = int(self.ctx.resolve(str(b.get("from", 1))))
        i_to = int(self.ctx.resolve(str(b.get("to", 1))))
        body = b.get("body", [])
        for i in range(i_from, i_to + 1):
            self.ctx.set("i", i)
            url = self.ctx.resolve(url_tpl)
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                self.ctx.record_error("loop_url_pattern", e)
                continue
            sig = await self.run_steps(body)
            if sig in ("stop", "abort"):
                return sig

    async def blk_foreach(self, b):
        list_name = b.get("list", "").strip("{}")
        items = self.ctx.get(list_name, [])
        body = b.get("body", [])
        var = b.get("as", "item")
        for idx, item in enumerate(items):
            self.ctx.set(var, item)
            self.ctx.set(f"{var}._index", idx)
            if isinstance(item, dict):
                for k, v in item.items():
                    self.ctx.set(f"{var}.{k}", v)
                    self.ctx.set(k, v)
            sig = await self.run_steps(body)
            if sig in ("stop", "abort"):
                return sig

    async def blk_condition(self, b):
        check = self.ctx.resolve(b.get("check", ""))
        res = eval_condition(check)
        return await self.run_steps(b.get("then", []) if res else b.get("else", []))

    async def blk_try_catch(self, b):
        try:
            sig = await self.run_steps(b.get("try", []))
            if sig == "abort":
                await self.run_steps(b.get("catch", []))
        except Exception as e:
            self.ctx.record_error("try_catch", e)
            await self.run_steps(b.get("catch", []))

    # retry body N times, run on_fail if all attempts fail
    async def blk_retry(self, b):
        times = b.get("times", 3)
        wait_s = b.get("wait_s", 2)
        body = b.get("body", [])
        for attempt in range(1, times + 1):
            eb = len(self.ctx.errors)
            sig = await self.run_steps(body)
            if sig != "abort" and len(self.ctx.errors) == eb:
                return sig
            if attempt < times:
                await self.page.wait_for_timeout(int(wait_s * 1000))
        return await self.run_steps(b.get("on_fail", []))

    # matching
    async def blk_find_match(self, b):
        list_name = b.get("list", "").strip("{}")
        items = self.ctx.get(list_name, [])
        field = b.get("field", "title")
        contains_raw = self.ctx.resolve(b.get("contains", ""))
        targets = [t.strip().lower() for t in contains_raw.split(",") if t.strip()]
        on_match = b.get("on_match", "stop")
        extract = b.get("extract", {})
        found = False
        for item in items:
            fval = str(item.get(field, "")).lower()
            if any(t in fval for t in targets):
                found = True
                row = ({o: item.get(i, "") for o, i in extract.items()} if extract else item)
                self.ctx.matched.append(row)
                if on_match == "stop":
                    self.ctx.set("matched_results", self.ctx.matched)
                    self.ctx.data["matched_results"] = self.ctx.matched
                    return "stop"
        self.ctx.set("matched_results", self.ctx.matched)
        self.ctx.data["matched_results"] = self.ctx.matched

    async def blk_assert(self, b):
        check = self.ctx.resolve(b.get("check", ""))
        if not eval_condition(check):
            msg = b.get("message", f"assert failed: {check}")
            self.ctx.record_error("assert", msg)
            return "abort" if b.get("required", True) else None

    # state + diff: write var to disk and compare with previous run
    async def blk_save(self, b):
        key = self.ctx.resolve(b.get("key", "default"))
        var_name = b.get("value", "").strip("{}")
        new_val = self.ctx.get(var_name)
        compare = b.get("compare", True)
        STATE_DIR.mkdir(exist_ok=True)
        fpath = STATE_DIR / f"{key}.json"
        old_val = None
        if fpath.exists():
            try:
                old_val = json.loads(fpath.read_text(encoding="utf-8")).get("value")
            except Exception:
                pass
        fpath.write_text(json.dumps({"value": new_val, "saved_at": datetime.now().isoformat()},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
        if compare and old_val is not None:
            self.ctx.changes[key] = {"changed": old_val != new_val, "old": old_val, "new": new_val}
        elif compare:
            self.ctx.changes[key] = {"changed": True, "old": None, "new": new_val, "first_run": True}

    # output
    async def blk_notify(self, b):
        raw = b.get("message", "")
        def pretty(m):
            val = self.ctx.get(m.group(1))
            if isinstance(val, list):
                lines = []
                for it in val:
                    if isinstance(it, dict):
                        lines.append("  - " + ", ".join(f"{k}: {v}" for k, v in it.items()))
                    else:
                        lines.append(f"  - {it}")
                return "\n" + "\n".join(lines) if lines else " (empty)"
            return str(val) if val is not None else m.group(0)
        msg = re.sub(r"\{([a-zA-Z0-9_.]+)\}", pretty, raw)
        self.ctx.notifications.append(msg)

    # media
    async def blk_play_video(self, b):
        sel = b.get("selector", "video")
        seconds = b.get("seconds", 5)
        wait_ms = b.get("wait_ms", 8000)
        try:
            await self.page.wait_for_selector(sel, state="attached", timeout=wait_ms)
        except PWTimeout:
            return
        try:
            await self.page.evaluate(
                "s => { const v=document.querySelector(s); if(v){v.muted=true; v.play().catch(()=>{});} }", sel)
        except Exception:
            pass
        await self.page.wait_for_timeout(seconds * 1000)

    # escape hatch
    async def blk_js_eval(self, b):
        code = self.ctx.resolve(b.get("code", ""))
        result = await self.page.evaluate(code)
        if b.get("as"):
            self.ctx.set(b["as"], result)
            self.ctx.data[b["as"]] = result


# public entry point: given a template dict + params, run it and return structured result
async def run_template(template: dict, params: dict, cdp_url: str = CDP_URL, mode: str = "test") -> dict:
    ctx = Context(params)
    t0 = time.time()
    name = template.get("name", "unnamed")
    engine_log.info(f"run template: {name} params={params} mode={mode}")

    ok = True
    engine = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            bctx = browser.contexts[0]
            page = await bctx.new_page()
            engine = Engine(page, ctx, bctx, mode=mode)
            sig = await engine.run_steps(template.get("steps", []))
            if sig == "abort":
                ok = False
    except Exception as e:
        ok = False
        ctx.record_error("engine", e)
        engine_log.error(f"engine error: {e}")

    result = {
        "ok": ok and len(ctx.errors) == 0,
        "template": name,
        "params": params,
        "data": ctx.data,
        "notifications": ctx.notifications,
        "changes": ctx.changes,
        "errors": ctx.errors,
        "steps_run": engine.steps_run if engine else 0,
        "duration_s": round(time.time() - t0, 1),
    }
    engine_log.info(f"done: {name} ok={result['ok']} steps={result['steps_run']} {result['duration_s']}s")
    return result