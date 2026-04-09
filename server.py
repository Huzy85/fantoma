"""Fantoma HTTP API — runs inside the Docker container.

Tool API: /start, /stop, /state, /click, /type, /navigate, etc.
Convenience: /run (uses Agent wrapper), /login, /extract.
Single session at a time.
"""
import json
import logging
import os
from pathlib import Path

from flask import Flask, request, jsonify, send_file
from io import BytesIO

from fantoma.browser_tool import Fantoma
from fantoma.agent import Agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
log = logging.getLogger("fantoma.server")

app = Flask(__name__)

# ── Config from environment ──────────────────────────────────
LOCAL_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://host.docker.internal:8081/v1")
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "auto")
BACKUP_LLM_URL = os.environ.get("BACKUP_LLM_URL", "http://host.docker.internal:8082/v1")
BACKUP_LLM_MODEL = os.environ.get("BACKUP_LLM_MODEL", "auto")
CLOUD_LLM_URL = os.environ.get("CLOUD_LLM_URL", "")
CLOUD_LLM_KEY = os.environ.get("CLOUD_LLM_KEY", "")
CLOUD_LLM_MODEL = os.environ.get("CLOUD_LLM_MODEL", "auto")
CAPTCHA_API = os.environ.get("CAPTCHA_API", "capsolver")
CAPTCHA_KEY = os.environ.get("CAPTCHA_KEY", "")
PROXY_URL = os.environ.get("FANTOMA_PROXY", None)
HEADLESS_MODE = os.environ.get("FANTOMA_HEADLESS", "virtual")

# ── Session state ────────────────────────────────────────────
_fantoma: Fantoma | None = None
_manual_fantoma: Fantoma | None = None  # headless=False, visible via noVNC


def _get_fantoma_defaults() -> dict:
    return {
        "llm_url": LOCAL_LLM_URL or None,
        "headless": HEADLESS_MODE,
        "proxy": PROXY_URL,
        "captcha_api": CAPTCHA_API,
        "captcha_key": CAPTCHA_KEY,
        "browser": "camoufox",
    }


def _require_session():
    if _fantoma is None:
        return jsonify({"error": "No active session. POST /start first."}), 400
    return None


# ── Lifecycle endpoints ──────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "session_active": _fantoma is not None,
                     "engine": "camoufox", "display": os.environ.get("DISPLAY", "none")})


@app.route("/start", methods=["POST"])
def start():
    global _fantoma
    if _fantoma is not None:
        return jsonify({"error": "session active", "url": "unknown"}), 409

    # Reset asyncio event loop — prevents 'Sync API inside asyncio loop' error
    # after a previous session ends with a stale/running loop (greenlet residue).
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if not loop.is_running() and not loop.is_closed():
            loop.close()
    except Exception:
        pass
    asyncio.set_event_loop(asyncio.new_event_loop())

    data = request.get_json(force=True) or {}
    defaults = _get_fantoma_defaults()
    _fantoma = Fantoma(**defaults)

    try:
        state = _fantoma.start(data.get("url"))
        return jsonify(state)
    except Exception as e:
        _fantoma = None
        return jsonify({"error": str(e)}), 500


@app.route("/stop", methods=["POST"])
def stop():
    global _fantoma
    if _fantoma:
        _fantoma.stop()
        _fantoma = None
    return jsonify({"status": "stopped"})


# ── State endpoints ──────────────────────────────────────────

@app.route("/state", methods=["GET"])
def state():
    err = _require_session()
    if err:
        return err
    mode = request.args.get("mode", "navigate")
    return jsonify(_fantoma.get_state(mode=mode))


@app.route("/evaluate", methods=["POST"])
def evaluate():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    script = data.get("script", "")
    if not script:
        return jsonify({"error": "Missing 'script'"}), 400
    try:
        result = _fantoma.evaluate(script)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/fill", methods=["POST"])
def fill():
    """Fill an input by CSS selector — bypasses ARIA tree 15-element limit."""
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    selector = data.get("selector")
    value = data.get("value", "")
    if not selector:
        return jsonify({"error": "Missing 'selector'"}), 400
    return jsonify(_fantoma.fill_by_selector(selector, value))


@app.route("/screenshot", methods=["GET"])
def screenshot():
    err = _require_session()
    if err:
        return err
    img = _fantoma.screenshot()
    return send_file(BytesIO(img), mimetype="image/png")


# ── Action endpoints ─────────────────────────────────────────

@app.route("/click", methods=["POST"])
def click():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.click(data["element_id"]))


@app.route("/type", methods=["POST"])
def type_text():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.type_text(data["element_id"], data["text"]))


@app.route("/navigate", methods=["POST"])
def navigate():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.navigate(data["url"]))


@app.route("/select", methods=["POST"])
def select():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.select(data["element_id"], data["value"]))


@app.route("/scroll", methods=["POST"])
def scroll():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.scroll(data.get("direction", "down")))


@app.route("/press_key", methods=["POST"])
def press_key():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.press_key(data["key"]))


# ── High-level endpoints ─────────────────────────────────────

@app.route("/login", methods=["POST"])
def login():
    """Manages its own session — starts, logs in, leaves browser open."""
    global _fantoma
    data = request.get_json(force=True)
    url = data.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400

    if _fantoma is None:
        defaults = _get_fantoma_defaults()
        _fantoma = Fantoma(**defaults)
        _fantoma.start()

    try:
        result = _fantoma.login(
            url=url, email=data.get("email", ""), username=data.get("username", ""),
            password=data.get("password", ""), first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/extract", methods=["POST"])
def extract():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    query = data.get("query")
    if not query:
        return jsonify({"error": "Missing 'query'"}), 400

    schema = data.get("schema")
    if schema:
        type_map = {"str": str, "int": int, "float": float, "bool": bool,
                     "string": str, "integer": int, "number": float, "boolean": bool}
        schema = {k: type_map.get(v, str) for k, v in schema.items()}

    try:
        result = _fantoma.extract(query, schema=schema)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/run", methods=["POST"])
def run_task():
    """Convenience — uses Agent wrapper. Manages its own lifecycle."""
    data = request.get_json(force=True)
    task = data.get("task")
    if not task:
        return jsonify({"error": "Missing 'task'"}), 400

    defaults = _get_fantoma_defaults()
    escalation = [defaults["llm_url"]]
    escalation_keys = [""]
    escalation_models = [LOCAL_LLM_MODEL]
    if BACKUP_LLM_URL:
        escalation.append(BACKUP_LLM_URL)
        escalation_keys.append("")
        escalation_models.append(BACKUP_LLM_MODEL)
    if CLOUD_LLM_URL:
        escalation.append(CLOUD_LLM_URL)
        escalation_keys.append(CLOUD_LLM_KEY)
        escalation_models.append(CLOUD_LLM_MODEL)

    try:
        agent = Agent(
            llm_url=defaults["llm_url"], escalation=escalation,
            escalation_keys=escalation_keys,
            escalation_models=escalation_models,
            captcha_api=CAPTCHA_API, captcha_key=CAPTCHA_KEY,
            proxy=PROXY_URL, headless=HEADLESS_MODE, browser="camoufox",
            max_steps=data.get("max_steps", 50), timeout=data.get("timeout", 300),
            sensitive_data=data.get("sensitive_data"),
        )
        result = agent.run(task, start_url=data.get("url"))
        return jsonify({
            "success": result.success, "data": result.data,
            "steps_taken": result.steps_taken, "error": result.error,
            "escalations": result.escalations,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Manual intervention endpoints (visible via noVNC on :6080) ─

@app.route("/manual/open", methods=["POST"])
def manual_open():
    """Open a visible browser on the shared :99 display — watch via noVNC on :6080.
    Use this to manually log in to sites, solve CAPTCHAs, or handle anything
    that requires a real human interaction. Cookies are saved to the profile.

    POST body (JSON): {"url": "https://x.com/login", "profile": "/path/to/profile"} (profile optional)
    """
    global _manual_fantoma
    if _manual_fantoma is not None:
        return jsonify({"error": "Manual session already open. POST /manual/close first."}), 409

    data = request.get_json(force=True) or {}
    url = data.get("url", "about:blank")
    profile_dir = data.get("profile", "/root/.local/share/fantoma/chrome-x-profile")

    try:
        _manual_fantoma = Fantoma(
            headless=False,
            browser="chrome",
            profile_dir=profile_dir,
        )
        _manual_fantoma.start(url)
        log.info("Manual session opened — URL: %s, profile: %s", url, profile_dir)
        return jsonify({
            "success": True,
            "url": url,
            "profile": profile_dir,
            "novnc": "http://<M5-IP>:6080/vnc.html",
            "note": "Browser is visible on :99 — open noVNC to interact. POST /manual/close when done.",
        })
    except Exception as e:
        _manual_fantoma = None
        log.error("Manual session failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/manual/screenshot", methods=["GET"])
def manual_screenshot():
    """Screenshot of the manual browser session."""
    if _manual_fantoma is None:
        return jsonify({"error": "No manual session active."}), 400
    img = _manual_fantoma.screenshot()
    return send_file(BytesIO(img), mimetype="image/png")


@app.route("/manual/close", methods=["POST"])
def manual_close():
    """Close the manual browser session. Cookies are already saved to the profile."""
    global _manual_fantoma
    if _manual_fantoma:
        _manual_fantoma.stop()
        _manual_fantoma = None
        log.info("Manual session closed — cookies saved to profile")
    return jsonify({"success": True, "status": "closed"})


@app.route("/manual/status", methods=["GET"])
def manual_status():
    return jsonify({
        "active": _manual_fantoma is not None,
        "novnc_url": "http://localhost:6080/vnc.html",
    })


@app.route("/stop-benchmark", methods=["POST"])
def stop_benchmark():
    """Signal a running benchmark to halt. Creates a stop file the runner watches."""
    stop_file = Path("/tmp/fantoma_benchmark_stop")
    stop_file.touch()
    log.info("Benchmark stop signal received — stop file created")
    return jsonify({"status": "stop signal sent", "file": str(stop_file)})


if __name__ == "__main__":
    port = int(os.environ.get("FANTOMA_PORT", 7860))
    log.info("Fantoma server starting on port %d", port)
    app.run(host="0.0.0.0", port=port, threaded=False)
