"""Local web UI for Promo Finder: standard library only (no pip install).

    python3 -m promo_finder serve            # -> http://127.0.0.1:8765

Pick or upload a show, choose models, run with live progress, then review the ranked
promos (✓ post / ✗ skip + notes) and export. Runs are the same as the CLI's and land in
outputs/<run>/, so CLI runs show up in the UI too.

Safety: binds to 127.0.0.1 by default. Uploaded zips are untrusted: only text files are
extracted, paths are flattened, sizes are capped. The server-side API key (.env) is never
sent to a provider URL typed into the UI; and with PROMO_UI_REQUIRE_KEY=1 (set this when
hosting publicly) users must always bring their own key.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import traceback
import zipfile
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import pipeline
from .clips import Promo, Segment
from .llm import DEFAULT_BASE_URL, DEFAULT_MODEL, list_models
from .runner import SetupError, build_llms, fresh_log_path, parse_episode_spec
from .screenplay import Episode, blocks_between, load_show

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SHOW_ROOTS = ("input_pack", "uploads", "examples")
OUTPUTS = "outputs"
TEXT_EXT = (".txt", ".md", ".fountain")
MAX_BODY = 60 * 1024 * 1024
MAX_UNZIPPED = 100 * 1024 * 1024
DOWNLOADABLE = {"promos.csv", "promos.json", "promos.md", "report.html", "run_summary.json"}
REQUIRE_USER_KEY = os.environ.get("PROMO_UI_REQUIRE_KEY", "") == "1"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "show"


# ---------------------------------------------------------------- shows and runs on disk

def discover_shows() -> List[Dict[str, Any]]:
    shows = []
    for root in SHOW_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = os.path.relpath(dirpath, root).count(os.sep)
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "__"))] if depth < 2 else []
            eps = [f for f in filenames if f.lower().endswith(TEXT_EXT) and not f.startswith(".")]
            if eps:
                shows.append({"id": os.path.relpath(dirpath), "name": os.path.basename(dirpath),
                              "files": len(eps), "source": root})
    return sorted(shows, key=lambda s: (s["source"], s["name"]))


def resolve_show(show_id: str) -> str:
    for s in discover_shows():
        if s["id"] == show_id:
            return s["id"]
    raise ValueError("unknown show")


_show_cache: Dict[str, Any] = {}
_show_lock = threading.Lock()


def episodes_for(path: str) -> Dict[int, Episode]:
    key = os.path.abspath(path)
    try:
        stamp = max(os.path.getmtime(os.path.join(path, f)) for f in os.listdir(path))
    except (OSError, ValueError):
        stamp = 0
    with _show_lock:
        hit = _show_cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    eps = {e.number: e for e in load_show(path)}
    with _show_lock:
        _show_cache[key] = (stamp, eps)
    return eps


def list_runs() -> List[Dict[str, Any]]:
    runs = []
    if not os.path.isdir(OUTPUTS):
        return runs
    for name in os.listdir(OUTPUTS):
        d = os.path.join(OUTPUTS, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        summ_p, prom_p = os.path.join(d, "run_summary.json"), os.path.join(d, "promos.json")
        log_p = os.path.join(d, "raw", "llm_calls.jsonl")
        if not (os.path.exists(summ_p) or os.path.exists(log_p)):
            continue
        item: Dict[str, Any] = {"name": name, "ready": os.path.exists(prom_p)}
        mtime = max((os.path.getmtime(p) for p in (summ_p, prom_p, log_p) if os.path.exists(p)), default=0)
        item["modified"] = mtime
        if os.path.exists(summ_p):
            try:
                s = json.load(open(summ_p, encoding="utf-8"))
                llm = s.get("llm") or {}
                fresh = llm.get("per_show_if_run_fresh") or {}
                item.update({"show": s.get("show"), "episodes": s.get("episodes"), "promos": s.get("promos_ranked"),
                             "model": llm.get("model") or ("rules-only" if s.get("mode") == "rules-only" else None),
                             "cost": fresh.get("cost_usd", llm.get("cost_usd")), "seconds": s.get("wall_clock_seconds"),
                             "mode": s.get("mode")})
            except (OSError, ValueError):
                pass
        if not item["ready"] and os.path.exists(log_p):
            item["active"] = time.time() - os.path.getmtime(log_p) < 240
            try:
                tags = [json.loads(line).get("tag", "") for line in open(log_p, encoding="utf-8")]
                item["progress_hint"] = {"mined": sum(t.startswith("mine:") for t in tags),
                                         "judged": sum(t.startswith("judge:") for t in tags)}
            except (OSError, ValueError):
                pass
        runs.append(item)
    return sorted(runs, key=lambda r: -r["modified"])


def resolve_run(name: str) -> str:
    name = unquote(name)
    if "/" in name or "\\" in name or name.startswith(".") or not name:
        raise ValueError("bad run name")
    d = os.path.join(OUTPUTS, name)
    if not os.path.isdir(d):
        raise ValueError("unknown run")
    return d


def run_input_dir(run_dir: str, data: Dict) -> Optional[str]:
    s = data.get("summary") or {}
    cand = s.get("input_dir")
    if cand and os.path.isdir(cand):
        return cand
    title = data.get("show") or s.get("show")
    for sh in discover_shows():
        if sh["name"] == title:
            return sh["id"]
    return None


def load_reviews(run_dir: str) -> Dict[str, Dict]:
    p = os.path.join(run_dir, "editor_review.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


_review_lock = threading.Lock()


def save_review(run_dir: str, pid: str, verdict: str, note: str) -> Dict:
    with _review_lock:
        reviews = load_reviews(run_dir)
        if verdict or note:
            reviews[pid] = {"verdict": verdict, "note": note, "time": time.strftime("%Y-%m-%d %H:%M")}
        else:
            reviews.pop(pid, None)
        tmp = os.path.join(run_dir, "editor_review.json.tmp")
        json.dump(reviews, open(tmp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        os.replace(tmp, os.path.join(run_dir, "editor_review.json"))
        return reviews


def clip_payload(run_dir: str, pid: str) -> Dict:
    data = json.load(open(os.path.join(run_dir, "promos.json"), encoding="utf-8"))
    p = next((x for x in data["promos"] + data.get("near_duplicates", []) if x["id"] == pid), None)
    if p is None:
        raise ValueError("unknown promo")
    src = run_input_dir(run_dir, data)
    if not src:
        raise ValueError("the scripts for this run are no longer on disk")
    eps = episodes_for(src)
    lines, offset = [], 0.0
    for k, s in enumerate(p["segments"], start=1):
        ep = eps.get(s["episode"])
        if ep is None:
            continue
        if len(p["segments"]) > 1:
            lines.append({"kind": "SEGMENT", "t": round(offset, 1), "label": f"Segment {k} · Episode {s['episode']}", "text": ""})
        for b in blocks_between(ep, s["start_seconds"], s["end_seconds"]):
            lines.append({"kind": b.kind, "t": round(max(0.0, b.start - s["start_seconds"]) + offset, 1),
                          "t_end": round(b.end - s["start_seconds"] + offset, 1), "label": b.label(), "text": b.text})
        offset += s["end_seconds"] - s["start_seconds"]
    last = p["segments"][-1]
    ep = eps.get(last["episode"])
    after = []
    if ep:
        nxt = [b for b in ep.blocks if last["end_seconds"] - 0.05 <= b.start < last["end_seconds"] + 10][:8]
        after = [{"kind": b.kind, "t": round(b.start - last["end_seconds"], 1), "label": b.label(), "text": b.text} for b in nxt]
    return {"id": pid, "lines": lines, "after": after}


# ---------------------------------------------------------------- uploads (untrusted data)

def _safe_name(base: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._()\-]+", "_", base).strip(" .")[:120] or "episode.txt"


def save_upload(show_name: str, filename: str, data: bytes) -> Dict:
    dest = os.path.join("uploads", _slug(show_name))
    os.makedirs(dest, exist_ok=True)
    written = 0

    def write(base: str, content: bytes):
        nonlocal written
        name = _safe_name(base)
        target = os.path.join(dest, name)
        stem, ext = os.path.splitext(name)
        n = 1
        while os.path.exists(target):
            n += 1
            target = os.path.join(dest, f"{stem} ({n}){ext}")
        with open(target, "wb") as f:
            f.write(content)
        written += 1

    if filename.lower().endswith(".zip"):
        total = 0
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                raw = info.filename.replace("\\", "/")
                base = raw.rsplit("/", 1)[-1]
                if info.is_dir() or not base or base.startswith(".") or "__MACOSX" in raw:
                    continue
                if not base.lower().endswith(TEXT_EXT) or info.file_size > 5 * 1024 * 1024:
                    continue
                total += info.file_size
                if total > MAX_UNZIPPED or written >= 2000:
                    raise ValueError("zip too large (max 100 MB of text, 2000 files)")
                with zf.open(info) as src:
                    content = src.read(5 * 1024 * 1024 + 1)
                if len(content) > 5 * 1024 * 1024:
                    continue
                write(base, content)
    elif filename.lower().endswith(TEXT_EXT):
        write(os.path.basename(filename.replace("\\", "/")), data)
    else:
        raise ValueError("upload a .zip of episode scripts or .txt files")
    return {"show": os.path.relpath(dest), "written": written}


def parse_summary(path: str) -> Dict:
    eps = load_show(path)
    return {
        "show": path, "episodes": len(eps),
        "blocks": sum(len(e.blocks) for e in eps),
        "minutes": round(sum(e.duration for e in eps) / 60, 1),
        "warnings": sum(len(e.warnings) for e in eps),
        "list": [{"n": e.number, "seconds": round(e.duration, 1), "blocks": len(e.blocks), "scenes": len(e.scenes),
                  "logline": e.logline[:160], "warnings": e.warnings[:3]} for e in eps],
    }


# ---------------------------------------------------------------- the one background job

class Job:
    def __init__(self, params: Dict, out_dir: str):
        self.params = params
        self.out_dir = out_dir
        self.state = "running"
        self.phase = "parse"
        self.progress: Dict[str, List[int]] = {}
        self.logs: deque = deque(maxlen=500)
        self.started = time.time()
        self.finished: Optional[float] = None
        self.error: Optional[str] = None
        self.llms: List = []
        self.thread: Optional[threading.Thread] = None

    def on_event(self, ev: Dict):
        if ev.get("type") == "log":
            self.logs.append(f"{ev.get('time', '')} {ev.get('msg', '')}")
        elif ev.get("type") == "progress":
            self.phase = ev["phase"]
            self.progress[ev["phase"]] = [ev.get("done", 0), ev.get("total", 0)]

    def stats(self) -> Dict:
        seen, out = set(), {"live_calls": 0, "cache_hits": 0, "prompt_tokens": 0, "completion_tokens": 0,
                            "cost_usd": 0.0, "refusals": 0, "models": []}
        stack = list(self.llms)
        while stack:
            m = stack.pop()
            if m is None or id(m) in seen:
                continue
            seen.add(id(m))
            out["live_calls"] += m.calls
            out["cache_hits"] += m.cache_hits
            out["prompt_tokens"] += m.prompt_tokens
            out["completion_tokens"] += m.completion_tokens
            out["cost_usd"] += m.spent()
            out["refusals"] += m.refusals
            out["models"].append(m.cfg.model)
            stack.append(m.fallback)
        out["cost_usd"] = round(out["cost_usd"], 4)
        return out

    def view(self) -> Dict:
        return {"state": self.state, "phase": self.phase, "progress": self.progress,
                "logs": list(self.logs)[-200:], "elapsed": round((self.finished or time.time()) - self.started, 1),
                "run": os.path.basename(self.out_dir), "error": self.error, "stats": self.stats(),
                "params": {k: v for k, v in self.params.items() if k != "api_key"}}

    def cancel(self):
        for m in self.llms:
            if m is not None:
                m.cancel_event.set()
                if m.fallback is not None:
                    m.fallback.cancel_event.set()


JOB: Optional[Job] = None
JOB_LOCK = threading.Lock()


def _num(v, cast, lo, hi, default):
    if v in (None, ""):
        return default
    x = cast(v)
    if not lo <= x <= hi:
        raise ValueError(f"value {x} out of range {lo}-{hi}")
    return x


def start_job(body: Dict) -> Dict:
    global JOB
    with JOB_LOCK:
        if JOB is not None and JOB.state == "running":
            raise RuntimeError("a run is already in progress")
        show = resolve_show(str(body.get("show", "")))
        rules_only = bool(body.get("rules_only"))
        params = {
            "show": show, "rules_only": rules_only,
            "model": (body.get("model") or "").strip() or None,
            "base_url": (body.get("base_url") or "").strip() or None,
            "judge_model": (body.get("judge_model") or "").strip() or None,
            "fallback_model": (body.get("fallback_model") or "").strip() or None,
            "workers": _num(body.get("workers"), int, 1, 32, 1),
            "max_cost": _num(body.get("max_cost"), float, 0.0, 100.0, None),
            "per_episode": _num(body.get("per_episode"), int, 1, 5, 3),
            "trailers": _num(body.get("trailers"), int, 0, 8, 4),
            "episodes": (body.get("episodes") or "").strip(),
            "recut_hooks": bool(body.get("recut_hooks")),
        }
        if params["episodes"] and not re.fullmatch(r"[0-9,\- ]+", params["episodes"]):
            raise ValueError("episodes must look like 1-10 or 1,5,9")
        if params["base_url"] and not re.match(r"^https?://", params["base_url"]):
            raise ValueError("provider URL must start with http:// or https://")
        api_key = (body.get("api_key") or "").strip() or None
        custom_url = params["base_url"] and params["base_url"].rstrip("/") != os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        if not rules_only and not api_key and (REQUIRE_USER_KEY or custom_url):
            raise SetupError("Enter an API key for this provider (the server's own key is only used with its default provider).")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tag = "rules" if rules_only else _slug((params["model"] or DEFAULT_MODEL).split("/")[-1])
        out_dir = os.path.join(OUTPUTS, f"{_slug(os.path.basename(show))}-{tag}-{stamp}")
        job = Job(params, out_dir)
        llm = judge_llm = None
        if not rules_only:
            llm, judge_llm = build_llms(model=params["model"], base_url=params["base_url"], api_key=api_key,
                                        judge_model=params["judge_model"], fallback_model=params["fallback_model"],
                                        workers=params["workers"], max_cost=params["max_cost"],
                                        log_path=fresh_log_path(out_dir), say=lambda m: job.logs.append(m))
            job.llms = [llm, judge_llm]
        else:
            os.makedirs(out_dir, exist_ok=True)

        def work():
            pipeline.LISTENERS.append(job.on_event)
            try:
                pipeline.run(show, out_dir, llm, per_episode=params["per_episode"], trailers=params["trailers"],
                             episodes_filter=parse_episode_spec(params["episodes"]), judge=True,
                             workers=params["workers"] if llm else 1, rules_pool=1, judge_llm=judge_llm,
                             recut_hooks=params["recut_hooks"])
                job.state = "done"
            except BaseException as e:  # noqa: BLE001
                cancelled = any(m is not None and m.cancel_event.is_set() for m in job.llms)
                job.state = "cancelled" if cancelled else "error"
                job.error = f"{type(e).__name__}: {e}"
                job.logs.append("ERROR " + job.error)
                traceback.print_exc()
            finally:
                job.finished = time.time()
                try:
                    pipeline.LISTENERS.remove(job.on_event)
                except ValueError:
                    pass

        job.thread = threading.Thread(target=work, daemon=True)
        JOB = job
        job.thread.start()
        return job.view()


# ---------------------------------------------------------------- HTTP

_models_cache: Dict[str, Any] = {}


class Handler(BaseHTTPRequestHandler):
    server_version = "PromoFinder/1.0"

    def log_message(self, fmt, *args):  # quieter console
        if "/api/job" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    # -- helpers
    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _err(self, code: int, msg: str):
        self._json({"error": msg}, code)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("upload too large (max 60 MB)")
        return self.rfile.read(n) if n else b""

    def _json_body(self) -> Dict:
        raw = self._body()
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise ValueError("body must be JSON")
        if not isinstance(obj, dict):
            raise ValueError("body must be a JSON object")
        return obj

    # -- routes
    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        q = parse_qs(url.query)
        try:
            if not parts or parts == ["index.html"]:
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if parts[0] != "api":
                return self._err(404, "not found")
            if parts[1:] == ["config"]:
                active = [r["name"] for r in list_runs() if r.get("active")]
                return self._json({"default_model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
                                   "default_base_url": os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
                                   "has_server_key": bool(os.environ.get("LLM_API_KEY") or os.environ.get("QUBRID_API_KEY")) and not REQUIRE_USER_KEY,
                                   "require_user_key": REQUIRE_USER_KEY, "active_runs": active})
            if parts[1:] == ["shows"]:
                return self._json({"shows": discover_shows()})
            if parts[1:] == ["models"]:
                base = (q.get("base_url", [""])[0] or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
                key = self.headers.get("X-Api-Key") or ""
                default_base = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
                if not key and base == default_base and not REQUIRE_USER_KEY:
                    key = os.environ.get("LLM_API_KEY") or os.environ.get("QUBRID_API_KEY", "")
                ck = base + "|" + str(hash(key))
                hit = _models_cache.get(ck)
                if not hit or time.time() - hit[0] > 600:
                    rows = []
                    for m in list_models(base, key):
                        pr = m.get("pricing") or {}
                        try:
                            pin, pout = float(pr.get("input")), float(pr.get("output"))
                        except (TypeError, ValueError):
                            pin = pout = None
                        rows.append({"id": m.get("id"), "in": pin, "out": pout, "context": m.get("context_length")})
                    rows.sort(key=lambda r: (r["in"] is None, (r["in"] or 0) + (r["out"] or 0), str(r["id"])))
                    hit = (time.time(), rows)
                    _models_cache[ck] = hit
                return self._json({"models": hit[1]})
            if parts[1:] == ["runs"]:
                return self._json({"runs": list_runs()})
            if parts[1:] == ["job"]:
                return self._json({"job": JOB.view() if JOB else None})
            if len(parts) >= 4 and parts[1] == "runs":
                run_dir = resolve_run(parts[2])
                what = parts[3]
                if what == "promos":
                    p = os.path.join(run_dir, "promos.json")
                    if not os.path.exists(p):
                        return self._err(404, "this run has not finished yet")
                    data = json.load(open(p, encoding="utf-8"))
                    src = run_input_dir(run_dir, data)
                    durations = {}
                    if src:
                        try:
                            durations = {n: round(e.duration, 2) for n, e in episodes_for(src).items()}
                        except OSError:
                            pass
                    data["reviews"] = load_reviews(run_dir)
                    data["episode_seconds"] = durations
                    data["run"] = os.path.basename(run_dir)
                    data["scripts_available"] = bool(src)
                    return self._json(data)
                if what == "clip" and len(parts) == 5:
                    return self._json(clip_payload(run_dir, parts[4]))
                if what == "file" and len(parts) == 5 and parts[4] in DOWNLOADABLE:
                    p = os.path.join(run_dir, parts[4])
                    if not os.path.exists(p):
                        return self._err(404, "file not found")
                    ctype = {"csv": "text/csv", "json": "application/json", "md": "text/markdown", "html": "text/html"}[parts[4].rsplit(".", 1)[1]]
                    with open(p, "rb") as f:
                        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(run_dir))
                        disp = "inline" if parts[4].endswith(".html") else f'attachment; filename="{safe}-{parts[4]}"'
                        return self._send(200, f.read(), ctype + "; charset=utf-8", {"Content-Disposition": disp})
                if what == "export.csv":
                    return self._export(run_dir)
            return self._err(404, "not found")
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._err(500, f"{type(e).__name__}: {e}")

    def _export(self, run_dir: str):
        data = json.load(open(os.path.join(run_dir, "promos.json"), encoding="utf-8"))
        reviews = load_reviews(run_dir)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["rank", "id", "type", "title", "segments", "duration_s", "hook", "cliffhanger", "why_it_works",
                    "judge_hook", "judge_cliffhanger", "judge_standalone", "judge_verdict", "flags", "score",
                    "editor_verdict", "editor_note"])
        for p in data["promos"]:
            j = p.get("judge") or {}
            r = reviews.get(p["id"], {})
            segs = "; ".join(f"Ep {s['episode']} {s['start']}-{s['end']}" for s in p["segments"])
            w.writerow([p["rank"], p["id"], p["kind"], p["title"], segs, p["duration_seconds"], p["hook"], p["cliffhanger"],
                        p["why_it_works"], j.get("hook_score"), j.get("cliffhanger_score"), j.get("standalone_score"),
                        j.get("verdict"), " | ".join(p.get("flags", [])), p["score"], r.get("verdict", ""), r.get("note", "")])
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(run_dir))
        return self._send(200, buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                          {"Content-Disposition": f'attachment; filename="{name}-editor-review.csv"'})

    def do_POST(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        q = parse_qs(url.query)
        try:
            if parts[:1] != ["api"]:
                return self._err(404, "not found")
            if parts[1:] == ["upload"]:
                name = (q.get("show", [""])[0] or "uploaded show").strip()
                filename = (q.get("filename", [""])[0] or "").strip()
                out = save_upload(name, filename, self._body())
                return self._json(out)
            if parts[1:] == ["parse"]:
                body = self._json_body()
                return self._json(parse_summary(resolve_show(str(body.get("show", "")))))
            if parts[1:] == ["run"]:
                return self._json({"job": start_job(self._json_body())})
            if parts[1:] == ["job", "cancel"]:
                if JOB is None or JOB.state != "running":
                    return self._err(409, "no run in progress")
                JOB.cancel()
                return self._json({"job": JOB.view()})
            if len(parts) == 4 and parts[1] == "runs" and parts[3] == "review":
                run_dir = resolve_run(parts[2])
                body = self._json_body()
                verdict = str(body.get("verdict", ""))
                if verdict not in ("post", "skip", ""):
                    raise ValueError("verdict must be post, skip or empty")
                pid = str(body.get("id", ""))
                if not re.fullmatch(r"[A-Za-z0-9\-_]{1,40}", pid):
                    raise ValueError("bad promo id")
                return self._json({"reviews": save_review(run_dir, pid, verdict, str(body.get("note", ""))[:1000])})
            return self._err(404, "not found")
        except (ValueError, SetupError, zipfile.BadZipFile) as e:
            return self._err(400, str(e))
        except RuntimeError as e:
            return self._err(409, str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._err(500, f"{type(e).__name__}: {e}")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    os.makedirs(OUTPUTS, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    print(f"Promo Finder UI running at {url}  (Ctrl+C to stop)")
    if host not in ("127.0.0.1", "localhost") and not REQUIRE_USER_KEY:
        print("WARNING: reachable from other machines and using the server's API key. "
              "Set PROMO_UI_REQUIRE_KEY=1 so visitors must bring their own key.")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
