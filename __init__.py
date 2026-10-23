"""check-in — same-session check-ins that fire when the conversation goes quiet.

/check-in 10m "prompt"   arm a check-in for THIS conversation
/check-in                show it
/check-in stop           close it
checkin_arm              model-facing: arm one itself when the user asks for
                         periodic reports on a task
checkin_close            model-facing close (called when the work is done)

Each fire injects the prompt into the same session as a user turn, so the wake
lands in the live conversation with its full context. The timer resets on every
model call for that session, so "10m" means ten minutes of silence — it never
fires while a turn is running.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger("plugin.check_in")

STATE = Path.home() / ".hermes" / "check-ins.json"
_LOCK = threading.Lock()
_ARMED: dict[str, dict] = {}  # session_key -> {prompt, seconds, armed_at, timer}
_CTX = None

_HELP = (
    'Usage:\n'
    '  /check-in 10m "Give me a progress report; if everything is done, send a\n'
    '                 final report and close the check-in"\n'
    "  /check-in            (show the armed check-in)\n"
    "  /check-in stop       (close it)\n"
    "It fires after that much silence and repeats until closed."
)


def _bound_key() -> str:
    """Route key of the session this call runs for; "" outside a session."""
    try:
        from gateway.session_context import bound_session_key
        return bound_session_key() or ""
    except Exception:
        return ""


def _parse_interval(text: str) -> int | None:
    m = re.fullmatch(r"(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs)?", (text or "").strip().lower())
    if not m:
        return None
    unit = (m.group(2) or "m")[0]
    secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[unit]
    return secs if secs >= 10 else None


def _save() -> None:
    with _LOCK:
        payload = {k: {"prompt": v["prompt"], "seconds": v["seconds"]} for k, v in _ARMED.items()}
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(payload, indent=1))
    except Exception:
        logger.debug("check-in: could not persist state", exc_info=True)


def _start_timer(key: str, seconds: int) -> threading.Timer:
    timer = threading.Timer(seconds, _fire, args=(key,))
    timer.daemon = True
    timer.start()
    return timer


def _arm(key: str, seconds: int, prompt: str) -> None:
    with _LOCK:
        old = _ARMED.pop(key, None)
        if old and old.get("timer"):
            old["timer"].cancel()
        _ARMED[key] = {"prompt": prompt, "seconds": seconds, "armed_at": time.time(),
                       "timer": _start_timer(key, seconds)}
    _save()


def _reset(key: str) -> None:
    """Restart the countdown: activity means the conversation is not idle."""
    with _LOCK:
        entry = _ARMED.get(key)
        if not entry:
            return
        if entry.get("timer"):
            entry["timer"].cancel()
        entry["armed_at"] = time.time()
        entry["timer"] = _start_timer(key, entry["seconds"])


def _cancel(key: str) -> bool:
    with _LOCK:
        entry = _ARMED.pop(key, None)
    if entry and entry.get("timer"):
        entry["timer"].cancel()
    if entry:
        _save()
    return bool(entry)


def _fire(key: str) -> None:
    with _LOCK:
        entry = _ARMED.get(key)
    if entry is None or _CTX is None:
        return
    ok = False
    try:
        ok = bool(_CTX.inject_message(entry["prompt"], role="user", session_key=key))
    except Exception:
        logger.warning("check-in: injection raised for %s", key, exc_info=True)
    if not ok:
        # Denied (consent missing) or the session is gone: stop rather than spin.
        logger.warning("check-in: could not inject into %s; closing the check-in", key)
        _cancel(key)
        return
    _reset(key)


def _status(key: str) -> str:
    with _LOCK:
        entry = _ARMED.get(key)
    if not entry:
        return 'No check-in armed.\n' + _HELP
    left = max(0, int(entry["seconds"] - (time.time() - entry["armed_at"])))
    return (f"Check-in armed: fires after {entry['seconds']}s of silence "
            f"(next in ~{left}s).\nPrompt: {entry['prompt']}\n"
            "Close with /check-in stop or the checkin_close tool.")


def _handle(ctx, raw: str) -> str:
    key = _bound_key()
    if not key:
        return "check-in: no session bound here — run it in a chat, not a bare CLI."
    args = (raw or "").strip()
    if args.lower() in {"stop", "off", "close", "cancel"}:
        return "Check-in closed." if _cancel(key) else "No check-in was armed."
    if not args:
        return _status(key)
    m = re.match(r"^(\S+)\s+(.+)$", args, re.S)
    if not m:
        return _HELP
    seconds = _parse_interval(m.group(1))
    if not seconds:
        return _HELP
    prompt = m.group(2).strip().strip('"').strip("'")
    if len(prompt) < 5:
        return _HELP
    _arm(key, seconds, prompt)
    return (f"Check-in armed: fires after {seconds}s of silence, resets on every turn.\n"
            f"Prompt: {prompt}\nClose with /check-in stop or the checkin_close tool.")


def _tool_arm(args: dict, **_kwargs) -> str:
    key = _bound_key()
    if not key:
        return "checkin_arm: no session bound — arm it from a chat turn."
    seconds = _parse_interval(str((args or {}).get("interval", "")))
    prompt = str((args or {}).get("prompt", "")).strip()
    if not seconds:
        return "checkin_arm: interval must look like '10m', '90s' or '2h' (minimum 10 seconds)."
    if len(prompt) < 5:
        return "checkin_arm: prompt too short — say what to report and what done looks like."
    _arm(key, seconds, prompt)
    return (f"Check-in armed: fires after {seconds}s of silence, resets on every turn, "
            f"repeats until checkin_close.\nPrompt: {prompt}")


def _on_post_llm_call(**_kwargs) -> None:
    key = _bound_key()
    if key:
        _reset(key)


def register(ctx) -> None:
    global _CTX
    _CTX = ctx

    ctx.register_command("check-in", handler=lambda raw: _handle(ctx, raw),
                         description="Arm a same-session check-in: /check-in 10m \"prompt\"",
                         args_hint='<interval> "<prompt>" | stop')
    ctx.register_command("check-in-stop", handler=lambda raw: _handle(ctx, "stop"),
                         description="Close the armed check-in")

    ctx.register_tool(
        name="checkin_arm",
        toolset="check_in",
        schema={
            "name": "checkin_arm",
            "description": (
                "Arm a recurring check-in for THIS conversation. Use it when the user asks for "
                "periodic reports or progress updates on a task ('report every 10 minutes', "
                "'check in on this while it runs'). After each period of silence, `prompt` is "
                "injected into this chat as a user turn, with the full conversation context. It "
                "repeats until checkin_close. Arming replaces any existing check-in here."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "interval": {
                        "type": "string",
                        "description": "Silence before each fire: '10m', '90s', '2h' (minimum 10s).",
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "What to do on each wake — the report to produce, and what counts as "
                            "done so the wake can close the check-in itself."
                        ),
                    },
                },
                "required": ["interval", "prompt"],
            },
        },
        handler=_tool_arm,
        description="Arm a recurring same-session check-in (e.g. a progress report every 10 minutes)",
    )

    ctx.register_tool(
        name="checkin_close",
        toolset="check_in",
        schema={
            "name": "checkin_close",
            "description": ("Close the armed check-in for this conversation. Call it once the work the "
                            "check-in was watching is finished, before giving the final report."),
            "parameters": {"type": "object", "properties": {}},
        },
        handler=lambda args, **_kw: (
            "Check-in closed." if _cancel((args or {}).get("session_key") or _bound_key())
            else "No check-in was armed."),
        description="Close the armed check-in for this conversation",
    )

    ctx.register_hook("post_llm_call", _on_post_llm_call)

    # Re-arm anything that was live before a gateway restart.
    try:
        saved = json.loads(STATE.read_text())
    except Exception:
        saved = {}
    for key, val in (saved or {}).items():
        if isinstance(val, dict) and val.get("prompt") and val.get("seconds"):
            _arm(key, int(val["seconds"]), str(val["prompt"]))
