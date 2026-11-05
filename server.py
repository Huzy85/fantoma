"""Fantoma HTTP API — runs inside the Docker container.

Tool API: /start, /stop, /state, /click, /type, /navigate, etc.
Convenience: /run (uses Agent wrapper), /login, /extract, /read.
Single session at a time.
"""
import hmac
import ipaddress
import json
import logging
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from flask import Flask, request, jsonify, send_file
from io import BytesIO

from fantoma.browser.domains import DomainBlocked, DomainPolicy, _is_private_host, _resolves_private
from fantoma.browser_tool import Fantoma
from fantoma.agent import Agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
log = logging.getLogger("fantoma.server")

app = Flask(__name__)

# ── Auth ─────────────────────────────────────────────────────
# Shared-secret gate. When FANTOMA_API_KEY is set, every endpoint except
# /health requires it via the X-API-Key header (or "Authorization: Bearer").
# When unset the API is OPEN — a loud warning is logged at startup. This is a
# browser tool that can read logged-in sessions, so on any shared network set
# the key. /evaluate is additionally gated by FANTOMA_ALLOW_EVAL (see below).
API_KEY = os.environ.get("FANTOMA_API_KEY", "")
ALLOW_EVAL = os.environ.get("FANTOMA_ALLOW_EVAL", "").lower() in ("1", "true", "yes")
# A proxy named in a /run or /login body sends every byte of browser traffic,
# logins included, through that host. Only an authenticated caller may ask
# for it, or set FANTOMA_ALLOW_REQUEST_PROXY=1 on a network you trust.
ALLOW_REQUEST_PROXY = bool(API_KEY) or os.environ.get(
    "FANTOMA_ALLOW_REQUEST_PROXY", "").lower() in ("1", "true", "yes")
# Persistent profiles (cookies, logins) must stay under one directory; a
# request cannot point the browser at an arbitrary path.
PROFILE_BASE = os.path.realpath(os.environ.get(
    "FANTOMA_PROFILE_BASE", os.path.join(os.path.expanduser("~"), ".local", "share", "fantoma")))


# Private, loopback, link-local and cloud-metadata hosts are refused for every
# URL this server is asked to open (and, once the server is running, for the
# browser's own requests and redirects). The API key does not change that: a
# caller who can drive the browser could otherwise read the machine's own
# services through it. Set FANTOMA_ALLOW_PRIVATE=1 to open them up, for
# example when the target is an app on your own network.
ALLOW_PRIVATE = os.environ.get("FANTOMA_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")
# Explicit opt-out of the "no key on a non-loopback address" refusal.
INSECURE_NO_AUTH = os.environ.get("FANTOMA_INSECURE_NO_AUTH", "").lower() in ("1", "true", "yes")


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _startup_problem(host: str, api_key: str, insecure: bool) -> str | None:
    """Reason the server must not start, or None. Listening beyond loopback
    with no API key hands the browser (and any logged-in session in it) to
    every host that can reach the port."""
    if api_key or insecure or _is_loopback_host(host):
        return None
    return (f"refusing to start: FANTOMA_HOST={host} is not loopback and FANTOMA_API_KEY is empty. "
            "Set FANTOMA_API_KEY, bind to 127.0.0.1, or set FANTOMA_INSECURE_NO_AUTH=1 "
            "if you really want an open API on this address.")


_HOST_CHARS = re.compile(r"^(\*\.)?[^\s/:@?#\\]{1,253}$")


def _clean_allowed_domains(value):
    """Validate a request's allowed_domains. Returns (list | None, error | None)."""
    if value is None:
        return None, None
    if not isinstance(value, list) or len(value) > 100:
        return None, "'allowed_domains' must be a list of at most 100 host names"
    out = []
    for item in value:
        if not isinstance(item, str) or not _HOST_CHARS.match(item.strip()):
            return None, "'allowed_domains' entries must be host names such as example.com or *.example.com"
        item = item.strip().lower()
        if not ALLOW_PRIVATE and _is_private_host(item.removeprefix("*.")):
            return None, "'allowed_domains' may not name a private or loopback host"
        # No DNS lookup here: 100 entries at 3 s each would outlast the /start
        # watchdog. DomainPolicy.permits() resolves per request instead.
        out.append(item)
    return (out or None), None


def _url_problem(url, required=False):
    """Why this URL may not be opened through the API, or None if it may."""
    if url is None or url == "":
        return "Missing 'url'" if required else None
    if not isinstance(url, str) or len(url) > 4096:
        return "'url' must be a string"
    if not DomainPolicy(block_private=not ALLOW_PRIVATE).permits(url):
        return ("url is not allowed: only http(s) addresses on public hosts can be opened "
                "(set FANTOMA_ALLOW_PRIVATE=1 to allow private hosts)")
    return None


def _bad_url(url, required=False):
    msg = _url_problem(url, required)
    return (jsonify({"error": msg}), 400) if msg else None


def _secret_values(*sources) -> list:
    vals = []
    for src in sources:
        if isinstance(src, dict):
            for v in src.values():
                vals.extend(_secret_values(v))
        elif isinstance(src, (str, int, float)) and not isinstance(src, bool) and str(src):
            vals.append(str(src))
    return vals


def _masked(text, *secrets) -> str:
    """Error text with any secret value replaced, safe to log or return."""
    text = str(text)
    for v in sorted(set(_secret_values(*secrets)), key=len, reverse=True):
        text = text.replace(v, "[REDACTED]")
    return text


def _server_error(e, *secrets, **extra):
    """A 500 that names no internals: generic text and a short id that
    matches the masked log line."""
    log_id = uuid.uuid4().hex[:8]
    log.error("request failed [%s] %s: %s", log_id, request.path, _masked(e, *secrets)[:500])
    body = {"success": False, "error": f"internal error (log id {log_id})", "log_id": log_id}
    body.update(extra)
    return jsonify(body), 500


class BadRequestBody(Exception):
    """The caller sent a body or field we cannot use. The message is ours,
    written for the caller, and is the only exception type answered as a 400."""


def _json_object(optional: bool = False) -> dict:
    """The request body, which must be a JSON object. An empty body is {} when
    `optional`. Malformed JSON raises werkzeug's BadRequest, an object of the
    wrong shape raises BadRequestBody; both are answered as JSON 400s."""
    if not request.get_data(cache=True).strip():
        if optional:
            return {}
        raise BadRequestBody("body must be a JSON object")
    data = request.get_json()
    if not isinstance(data, dict):
        raise BadRequestBody("body must be a JSON object")
    return data


def _field(data: dict, name: str, kind=None):
    """A required body field, optionally of one type (bool is never an int)."""
    if name not in data:
        raise BadRequestBody(f"missing field '{name}'")
    value = data[name]
    if kind is not None and (not isinstance(value, kind) or (kind is int and isinstance(value, bool))):
        raise BadRequestBody(f"field '{name}' has the wrong type")
    return value


def _apply_block_private_env() -> None:
    """Make the browser itself refuse private hosts (subrequests, redirects)
    unless FANTOMA_ALLOW_PRIVATE=1. Runs when this module is imported, so it
    holds under gunicorn and `flask run` as well as `python server.py`."""
    if not ALLOW_PRIVATE:
        os.environ["FANTOMA_BLOCK_PRIVATE"] = "1"


_apply_block_private_env()


def _bind_host(api_key: str, env_host: str | None) -> str:
    """Where the server listens. FANTOMA_HOST wins; otherwise an open
    (keyless) server stays on loopback and a keyed one serves all interfaces."""
    if env_host:
        return env_host
    return "0.0.0.0" if api_key else "127.0.0.1"


def _safe_profile_dir(value) -> str | None:
    """Resolve a caller-supplied profile path under PROFILE_BASE, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value if os.path.isabs(value) else os.path.join(PROFILE_BASE, value)
    resolved = os.path.realpath(candidate)
    if resolved == PROFILE_BASE or resolved.startswith(PROFILE_BASE + os.sep):
        return resolved
    return None


def _parse_proxy(proxy, check_host=True):
    """Validate a request-supplied proxy and rebuild it from its parsed parts.
    Returns (playwright_proxy_dict | None, error | None).

    Only http, https and socks5 proxies, and not one on a private or loopback
    host (it would let a caller aim the browser's traffic at the local network)
    unless FANTOMA_ALLOW_PRIVATE=1. The string the caller sent is never passed
    on: a netloc with a second "@", a comma, a semicolon or whitespace is
    refused outright, because the URL parser and the browser can disagree
    about which host such a string names."""
    if proxy is None or proxy == "":
        return None, None
    if not isinstance(proxy, str) or len(proxy) > 2048:
        return None, "'proxy' must be a URL string"
    if re.search(r"[,;\s]", proxy):
        return None, "'proxy' is not a valid URL"
    text = proxy if "://" in proxy else "http://" + proxy
    try:
        parsed = urlparse(text)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return None, "'proxy' is not a valid URL"
    if parsed.netloc.count("@") > 1 or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return None, "'proxy' is not a valid URL"
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https", "socks5", "socks5h") or not host:
        return None, "'proxy' must be an http, https or socks5 URL with a host"
    if check_host and not ALLOW_PRIVATE and (_is_private_host(host) or _resolves_private(host)):
        return None, ("'proxy' may not point at a private or loopback host "
                      "(set FANTOMA_ALLOW_PRIVATE=1 to allow it)")
    shown = f"[{host}]" if ":" in host else host
    out = {"server": f"{scheme}://{shown}" + (f":{port}" if port else "")}
    if parsed.username is not None:
        out["username"] = unquote(parsed.username)
        if parsed.password is not None:
            out["password"] = unquote(parsed.password)
    return out, None


def _proxy_problem(proxy):
    """Why a request-supplied proxy is refused, or None."""
    return _parse_proxy(proxy)[1]


def _request_proxy(data: dict):
    proxy = data.get("proxy")
    if proxy and not ALLOW_REQUEST_PROXY:
        log.warning("Ignoring request-supplied proxy: set FANTOMA_API_KEY or "
                    "FANTOMA_ALLOW_REQUEST_PROXY=1 to allow it")
        return PROXY_URL
    parsed, _ = _parse_proxy(proxy, check_host=False) if proxy else (None, None)
    return parsed if parsed else PROXY_URL


@app.before_request
def _refuse_open_remote():
    """The start-up refusal only runs under `python server.py`. This covers
    gunicorn and `flask run`: with no API key, a client that is not on
    loopback gets 503 unless FANTOMA_INSECURE_NO_AUTH=1."""
    if API_KEY or INSECURE_NO_AUTH or request.path == "/health":
        return None
    if _is_loopback_host(request.remote_addr or ""):
        return None
    return jsonify({"error": "server misconfigured: FANTOMA_API_KEY is not set and the request "
                             "is not from loopback (set FANTOMA_API_KEY, or "
                             "FANTOMA_INSECURE_NO_AUTH=1 to allow it)"}), 503


_LOOPBACK_HOST_NAMES = ("localhost", "127.0.0.1", "::1")


def _host_name(host: str) -> str:
    """The host part of a Host header, port removed, lower-cased, no brackets."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host


_PROXY_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP", "Via")


@app.before_request
def _keyless_guard():
    """With no API key the only protection is "the caller is on loopback". Two
    ways round that are closed here.

    A browser on this machine can be told by any web page to send a request to
    127.0.0.1, so a keyless server refuses anything that looks like a browser
    page made it: an Origin header, or Sec-Fetch-Site other than none /
    same-origin (403). Scripts, curl and the MCP backend send neither.

    A reverse proxy on the same host makes every remote client look like
    loopback, so a keyless request that carries proxy headers is refused
    (503). Behind any proxy a key is mandatory. FANTOMA_INSECURE_NO_AUTH=1
    skips only this second check, because that operator chose an open API on a
    non-loopback address."""
    if API_KEY or request.path == "/health":
        return None
    if not INSECURE_NO_AUTH and _host_name(request.host) not in _LOOPBACK_HOST_NAMES:
        # DNS rebinding: a page on attacker.example whose name later resolves
        # to 127.0.0.1 is same-origin to the browser, so no Origin and no
        # cross-site Sec-Fetch-Site. Its Host header still says attacker.example.
        return jsonify({"error": "Host header is not a loopback name; the keyless server only "
                                 "answers localhost, 127.0.0.1 and [::1] (set FANTOMA_API_KEY "
                                 "to use another name)"}), 403
    if request.headers.get("Origin") is not None or \
            request.headers.get("Sec-Fetch-Site", "none").lower() not in ("none", "same-origin"):
        return jsonify({"error": "browser-originated requests are refused when FANTOMA_API_KEY "
                                 "is not set"}), 403
    if not INSECURE_NO_AUTH and any(h in request.headers for h in _PROXY_HEADERS):
        return jsonify({"error": "server misconfigured: request carries proxy headers but "
                                 "FANTOMA_API_KEY is not set. Behind a reverse proxy a key is "
                                 "mandatory"}), 503
    return None


@app.before_request
def _require_api_key():
    if not API_KEY or request.path == "/health":
        return None
    provided = request.headers.get("X-API-Key", "")
    if not provided:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            provided = auth[7:]
    if not hmac.compare_digest(provided, API_KEY):
        return jsonify({"error": "unauthorized"}), 401
    return None


@app.before_request
def _require_json_body():
    """Every body must be declared as application/json. A page cannot send
    that cross-origin without a CORS preflight, which this server never
    answers, so a text/plain "simple" request is refused (415). That holds
    for an empty POST too: a form post with no body is also a "simple"
    request, and /stop would run on it."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path != "/health":
        if request.mimetype != "application/json":
            return jsonify({"error": "Content-Type must be application/json"}), 415
    return None


# ── Request watchdog ─────────────────────────────────────────
#
# Playwright can hang rather than raise: the Firefox driver crashes on some
# pages (a page JS error with no location kills coreBundle.js) and the sync
# API then blocks forever waiting on a process that will never answer. A
# hung worker is worse than a crashed one — supervisord sees RUNNING and
# leaves it alone, so the container serves timeouts indefinitely. That is
# how three containers stayed dead for 24 days while /health said "ok".
#
# Flask runs threaded=False here, so exactly one request is in flight and a
# single module-level timer is enough. Any request that outlives its budget
# takes the process down; supervisord returns a clean one in ~2 s.

# Per-path ceilings. /run legitimately takes minutes; everything else is a
# single browser operation and should be quick.
_WATCHDOG_DEFAULT = 120
# A cold Camoufox start is ~15 s, so 75 s means "hung", not "slow". Keeping
# this tight matters: the worker cannot be reused until the watchdog fires,
# so this figure is the real recovery time a client waits through.
_WATCHDOG_BY_PATH = {"/run": 420, "/login": 240, "/extract": 240, "/read": 120, "/start": 75}
# /health must never arm a watchdog — it is how callers check liveness.
_WATCHDOG_EXEMPT = {"/health", "/manual/status"}
_WATCHDOG_MAX_REQUESTED = 3600

_watchdog_timer: threading.Timer | None = None


def _watchdog_fire(path: str, budget: int) -> None:
    log.error(
        "Request %s exceeded %ds — worker is hung, restarting", path, budget
    )
    os._exit(1)


@app.before_request
def _arm_watchdog():
    global _watchdog_timer
    if request.path in _WATCHDOG_EXEMPT:
        return None
    budget = _WATCHDOG_BY_PATH.get(request.path, _WATCHDOG_DEFAULT)
    # A caller may ask for a longer task than the default ceiling. Honour it,
    # with headroom for browser startup and the final extraction, so the
    # watchdog only ever fires on a genuine hang — never on slow-but-working.
    if request.path in ("/run", "/login", "/extract"):
        body = request.get_json(silent=True) or {}
        requested = body.get("timeout")
        if isinstance(requested, (int, float)) and requested > 0:
            # Capped: a huge timeout would park the single worker for as
            # long as the caller liked.
            budget = max(budget, min(int(requested), _WATCHDOG_MAX_REQUESTED) + 120)
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
    _watchdog_timer = threading.Timer(budget, _watchdog_fire, args=(request.path, budget))
    _watchdog_timer.daemon = True
    _watchdog_timer.start()
    return None


@app.teardown_request
def _disarm_watchdog(exc=None):
    global _watchdog_timer
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
        _watchdog_timer = None

# ── Config from environment ──────────────────────────────────
LOCAL_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://host.docker.internal:8081/v1")
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "auto")
LOCAL_LLM_API_KEY = os.environ.get("LOCAL_LLM_API_KEY", "")
BACKUP_LLM_URL = os.environ.get("BACKUP_LLM_URL", "http://host.docker.internal:8081/v1")
BACKUP_LLM_MODEL = os.environ.get("BACKUP_LLM_MODEL", "auto")
CLOUD_LLM_URL = os.environ.get("CLOUD_LLM_URL", "")
CLOUD_LLM_KEY = os.environ.get("CLOUD_LLM_KEY", "")
CLOUD_LLM_MODEL = os.environ.get("CLOUD_LLM_MODEL", "auto")
CAPTCHA_API = os.environ.get("CAPTCHA_API", "capsolver")
CAPTCHA_KEY = os.environ.get("CAPTCHA_KEY", "")
PROXY_URL = os.environ.get("FANTOMA_PROXY", None)
def _headless_from_env(raw: str):
    """FANTOMA_HEADLESS is text; the browser wants True, False or "virtual"."""
    v = (raw or "virtual").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return "virtual"


HEADLESS_MODE = _headless_from_env(os.environ.get("FANTOMA_HEADLESS", "virtual"))

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


# Signatures that mean this worker's Playwright machinery is dead for good.
# The Node.js driver can crash outright (a page JS error with no location
# takes down coreBundle.js), and once it does, the sync API in THIS process
# is bound to a dead transport. /start already tries pkill + a fresh event
# loop and it still fails, so there is nothing left to repair in-process.
_UNRECOVERABLE = (
    "sync api inside the asyncio loop",
    "playwright already stopped",
    "target page, context or browser has been closed",
    "connection closed while reading from the driver",
)


def _is_unrecoverable(err: str) -> bool:
    low = (err or "").lower()
    return any(sig in low for sig in _UNRECOVERABLE)


def _restart_worker(reason: str) -> None:
    """Exit so supervisord hands us a clean process (autorestart, startsecs=2).

    Crash-only recovery. A worker in this state fails every future request,
    which is how three containers stayed silently broken for 24 days while
    /health kept answering "ok" — /health never starts a browser.
    """
    log.error("Unrecoverable browser state (%s) — restarting worker", reason)

    def _die():
        os._exit(1)

    # Delay just long enough for the HTTP response to flush to the client.
    t = threading.Timer(0.5, _die)
    t.daemon = True
    t.start()


def _release_session() -> None:
    """Tear down the shared session before starting an independent browser.

    /login and /start leave a session open on purpose. /run builds its own
    Agent with its own browser, and Playwright's sync API cannot hold two
    live instances on one thread — the second start fails with "Sync API
    inside the asyncio loop" and the container stays broken until restart.
    Calling this first makes /run safe to use after /login.
    """
    global _fantoma
    if _fantoma is not None:
        try:
            _fantoma.stop()
        except Exception as e:
            log.warning("Failed to stop existing session cleanly: %s", e)
        finally:
            _fantoma = None


# ── Lifecycle endpoints ──────────────────────────────────────

@app.errorhandler(BadRequestBody)
def _bad_body(e):
    log.info("bad request %s: %s", request.path, _masked(e)[:200])
    return jsonify({"error": str(e)}), 400


@app.errorhandler(Exception)
def _unhandled(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        # Malformed JSON (BadRequest), wrong content type (415), unknown
        # route and so on: always a JSON body, never an HTML page.
        return jsonify({"error": e.name.lower() if e.code == 400 else (e.description or e.name)}), e.code
    # Library errors (KeyError, ValueError, ...) are server faults, not
    # something the caller can fix: generic 500, detail only in the masked log.
    return _server_error(e)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "session_active": _fantoma is not None,
                     "engine": "camoufox", "display": os.environ.get("DISPLAY", "none")})


@app.route("/start", methods=["POST"])
def start():
    global _fantoma
    if _fantoma is not None:
        return jsonify({"error": "session active", "url": "unknown"}), 409
    data = _json_object(optional=True)
    bad = _bad_url(data.get("url"))
    if bad:
        return bad
    allowed, problem = _clean_allowed_domains(data.get("allowed_domains"))
    if problem:
        return jsonify({"error": problem}), 400

    # Kill any lingering Playwright driver and Camoufox binary from a prior session.
    # Camoufox.__exit__ closes the browser context but does NOT terminate these processes.
    # They stay alive, so the next sync_playwright().start() finds a running asyncio loop
    # and throws "Sync API inside asyncio loop". Killing them before each /start ensures
    # a clean slate. The 1s sleep gives the OS time to reap them before we re-launch.
    import subprocess, time, asyncio
    # Don't nuke browser processes while a manual noVNC session is live — that
    # session is a separate Chrome holding the user's real logins.
    if _manual_fantoma is None:
        from fantoma.browser import reap
        reap.kill(reap.DRIVER)
        reap.kill(reap.CAMOUFOX)
        time.sleep(1)
    try:
        loop = asyncio.get_event_loop()
        if not loop.is_running() and not loop.is_closed():
            loop.close()
    except Exception:
        pass
    asyncio.set_event_loop(asyncio.new_event_loop())

    defaults = _get_fantoma_defaults()
    if allowed:
        defaults["allowed_domains"] = allowed
    if data.get("profile_dir"):
        safe = _safe_profile_dir(data["profile_dir"])
        if not safe:
            return jsonify({"error": f"profile_dir must sit under {PROFILE_BASE}"}), 400
        defaults["profile_dir"] = safe
    _fantoma = Fantoma(**defaults)

    try:
        state = _fantoma.start(data.get("url"))
        return jsonify(state)
    except Exception as e:
        _fantoma = None
        err = str(e)
        if _is_unrecoverable(err):
            _restart_worker("start: " + err[:120])
            return jsonify({
                "error": err,
                "retryable": True,
                "detail": "Browser driver died; this worker is restarting. "
                          "Retry in a few seconds.",
            }), 503
        if isinstance(e, DomainBlocked):
            return jsonify({"error": err}), 403
        return _server_error(e)


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
    if not ALLOW_EVAL:
        return jsonify({"error": "/evaluate is disabled. It runs arbitrary JS in the "
                                 "page context (cookie/token theft surface). Set "
                                 "FANTOMA_ALLOW_EVAL=1 to enable."}), 403
    err = _require_session()
    if err:
        return err
    data = _json_object()
    script = data.get("script", "")
    if not script:
        return jsonify({"error": "Missing 'script'"}), 400
    try:
        result = _fantoma.evaluate(script)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return _server_error(e)


@app.route("/fill", methods=["POST"])
def fill():
    """Fill an input by CSS selector — bypasses ARIA tree 15-element limit."""
    err = _require_session()
    if err:
        return err
    data = _json_object()
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
    data = _json_object()
    return jsonify(_fantoma.click(_field(data, "element_id")))


@app.route("/type", methods=["POST"])
def type_text():
    err = _require_session()
    if err:
        return err
    data = _json_object()
    return jsonify(_fantoma.type_text(_field(data, "element_id"), _field(data, "text", str)))


@app.route("/navigate", methods=["POST"])
def navigate():
    err = _require_session()
    if err:
        return err
    data = _json_object(optional=True)
    bad = _bad_url(data.get("url"), required=True)
    if bad:
        return bad
    return jsonify(_fantoma.navigate(data["url"]))


@app.route("/select", methods=["POST"])
def select():
    err = _require_session()
    if err:
        return err
    data = _json_object()
    return jsonify(_fantoma.select(_field(data, "element_id"), _field(data, "value")))


@app.route("/scroll", methods=["POST"])
def scroll():
    err = _require_session()
    if err:
        return err
    data = _json_object()
    return jsonify(_fantoma.scroll(data.get("direction", "down")))


@app.route("/press_key", methods=["POST"])
def press_key():
    err = _require_session()
    if err:
        return err
    data = _json_object()
    return jsonify(_fantoma.press_key(_field(data, "key", str)))


# ── High-level endpoints ─────────────────────────────────────

class _SecretOffSite(Exception):
    """A login value was about to be typed on a site it was not given for."""


def _login_guard(url: str, secrets: dict):
    """value_guard for /login: refuse to type the credentials unless the live
    page is still on the host the login URL named. A redirect to another site
    mid-login would otherwise receive the password."""
    from urllib.parse import urlsplit
    from fantoma.secrets_scope import SecretStore
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    pattern = f"http*://{host}{port}"
    entries = {pattern: {k: str(v) for k, v in secrets.items() if v}}
    try:
        store = SecretStore.from_any(entries)
    except ValueError:
        store = None

    def guard(text):
        if not text or store is None:
            if store is None and text:
                raise _SecretOffSite("login host could not be scoped")
            return text
        try:
            live = _fantoma._engine.get_page().url
        except Exception:
            live = ""
        if store.mask(text, live) != store.mask(text):
            raise _SecretOffSite(f"page is no longer on {host}; credentials not typed")
        return text
    return guard


@app.route("/login", methods=["POST"])
def login():
    """Manages its own session — starts, logs in, leaves browser open."""
    global _fantoma
    data = _json_object(optional=True)
    url = data.get("url")
    bad = _bad_url(url, required=True)
    if bad:
        return bad
    secrets = {k: data.get(k) for k in ("email", "username", "password")}
    guard = _login_guard(url, secrets)

    try:
        if _fantoma is None:
            defaults = _get_fantoma_defaults()
            _fantoma = Fantoma(**defaults)
            _fantoma.start()
        result = _fantoma.login(
            url=url, email=data.get("email", ""), username=data.get("username", ""),
            password=data.get("password", ""), first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""), value_guard=guard,
        )
        return jsonify(result)
    except Exception as e:
        return _server_error(e, secrets)


@app.route("/extract", methods=["POST"])
def extract():
    err = _require_session()
    if err:
        return err
    data = _json_object()
    query = data.get("query")
    if not query:
        return jsonify({"error": "Missing 'query'"}), 400

    # Passed through as given: extract() understands both a real JSON Schema
    # and the older flat {field: "type"} map. Converting here flattened a JSON
    # Schema into {"type": str, "properties": str}, so the documented MCP form
    # never worked.
    schema = data.get("schema")
    if schema is not None and not isinstance(schema, dict):
        return jsonify({"error": "'schema' must be a JSON object"}), 400

    try:
        result = _fantoma.extract(query, schema=schema or None)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return _server_error(e)


@app.route("/read", methods=["POST"])
def read_page():
    """Current page as clean Markdown, plus its links. No LLM involved.

    Body (all optional): url, main_only (default true), include_links
    (default true), selector, max_chars.
    """
    err = _require_session()
    if err:
        return err
    data = _json_object(optional=True)
    bad = _bad_url(data.get("url"))
    if bad:
        return bad
    max_chars = data.get("max_chars") or 0
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise BadRequestBody("field 'max_chars' must be an integer")
    try:
        result = _fantoma.read(
            url=data.get("url") or None,
            main_only=bool(data.get("main_only", True)),
            include_links=bool(data.get("include_links", True)),
            selector=str(data.get("selector") or ""),
            max_chars=max_chars,
        )
        return jsonify({"success": True, **result})
    except DomainBlocked as e:
        return jsonify({"success": False, "error": str(e)}), 403
    except Exception as e:
        return _server_error(e)


@app.route("/run", methods=["POST"])
def run_task():
    """Convenience — uses Agent wrapper. Manages its own lifecycle.

    Pass keep_session=true to leave the browser on the final page so the
    caller can inspect the end state via /state.
    """
    global _fantoma
    data = _json_object(optional=True)
    task = data.get("task")
    if not task or not isinstance(task, str):
        return jsonify({"error": "Missing 'task'"}), 400
    max_steps, timeout = data.get("max_steps", 50), data.get("timeout", 300)
    for name, val, hi in (("max_steps", max_steps, 500), ("timeout", timeout, 3600)):
        if isinstance(val, bool) or not isinstance(val, int) or not 1 <= val <= hi:
            return jsonify({"error": f"'{name}' must be an integer from 1 to {hi}"}), 400
    bad = _bad_url(data.get("url"))
    if bad:
        return bad
    allowed, problem = _clean_allowed_domains(data.get("allowed_domains"))
    if problem:
        return jsonify({"error": problem}), 400
    proxy_problem = _proxy_problem(data.get("proxy"))
    if proxy_problem:
        return jsonify({"error": proxy_problem}), 400

    # The Agent owns its own browser, so any session left open by /start or
    # A bad sensitive_data pattern is the caller's mistake: say so plainly,
    # before any browser is touched. The message names the pattern only,
    # never a secret value.
    try:
        from fantoma.secrets_scope import SecretStore
        SecretStore.from_any(data.get("sensitive_data"))
    except ValueError as e:
        return jsonify({"success": False, "error": f"Invalid sensitive_data: {e}"}), 400

    # /login has to go first — two sync Playwright instances on one thread
    # is what wedges the container.
    _release_session()

    defaults = _get_fantoma_defaults()
    escalation = [defaults["llm_url"]]
    escalation_keys = [LOCAL_LLM_API_KEY]
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
            llm_url=defaults["llm_url"], api_key=LOCAL_LLM_API_KEY,
            model=LOCAL_LLM_MODEL, escalation=escalation,
            escalation_keys=escalation_keys,
            escalation_models=escalation_models,
            captcha_api=CAPTCHA_API, captcha_key=CAPTCHA_KEY,
            proxy=_request_proxy(data), headless=HEADLESS_MODE, browser="camoufox",
            max_steps=max_steps, timeout=timeout,
            sensitive_data=data.get("sensitive_data"),
            **({"allowed_domains": allowed} if allowed else {}),
        )
        keep = bool(data.get("keep_session"))
        result = agent.run(task, start_url=data.get("url"),
                           deadline_s=timeout, keep_open=keep)
        if keep:
            # Hand the live browser to the shared session so /state and /stop
            # can reach it. This is how a caller checks what the run actually
            # did — whether the item reached the cart — instead of trusting
            # the agent's own account of it.
            _fantoma = agent.fantoma
        if _is_unrecoverable(result.error or ""):
            _restart_worker("run: " + (result.error or "")[:120])
            return jsonify({
                "success": False, "error": _masked(result.error, data.get("sensitive_data")), "retryable": True,
                "detail": "Browser driver died; this worker is restarting. "
                          "Retry in a few seconds.",
            }), 503
        return jsonify({
            "success": result.success, "data": result.data,
            "steps_taken": result.steps_taken, "error": result.error,
            "escalations": result.escalations,
            # Where the browser actually finished, so a caller can check the
            # run rather than take the agent's word for it.
            "final_url": result.final_url, "final_title": result.final_title,
            # Independent check of the end state against the parsed task.
            "verified": result.verified, "verify_reason": result.verify_reason,
            # What the agent actually did, step by step. Without this a failed
            # run can only be guessed at from its final state.
            "steps_detail": (result.steps_detail or [])[:40],
        })
    except Exception as e:
        err = str(e)
        if _is_unrecoverable(err):
            _restart_worker("run: " + err[:120])
            return jsonify({
                "success": False, "error": _masked(err, data.get("sensitive_data")), "retryable": True,
                "detail": "Browser driver died; this worker is restarting. "
                          "Retry in a few seconds.",
            }), 503
        if isinstance(e, DomainBlocked):
            return jsonify({"success": False, "error": str(e)}), 403
        return _server_error(e, data.get("sensitive_data"))


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

    data = _json_object(optional=True)
    url = data.get("url", "about:blank")
    bad = _bad_url(url)
    if bad:
        return bad
    profile_dir = _safe_profile_dir(data.get("profile") or os.path.join(PROFILE_BASE, "chrome-x-profile"))
    if not profile_dir:
        return jsonify({"error": f"profile must sit under {PROFILE_BASE}"}), 400

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
            "novnc": "http://<HOST-IP>:6080/vnc.html",
            "note": "Browser is visible on :99 — open noVNC to interact. POST /manual/close when done.",
        })
    except Exception as e:
        _manual_fantoma = None
        return _server_error(e)


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
    if not API_KEY:
        log.warning(
            "FANTOMA_API_KEY is not set — the API is UNAUTHENTICATED. Anyone who "
            "can reach this port can drive the browser and read logged-in sessions. "
            "Set FANTOMA_API_KEY and keep the port on a trusted network."
        )
    if ALLOW_EVAL:
        log.warning("FANTOMA_ALLOW_EVAL is on — /evaluate will run arbitrary JS in the page.")
    host = _bind_host(API_KEY, os.environ.get("FANTOMA_HOST"))
    problem = _startup_problem(host, API_KEY, INSECURE_NO_AUTH)
    if problem:
        log.error(problem)
        raise SystemExit(1)
    if INSECURE_NO_AUTH and not API_KEY and not _is_loopback_host(host):
        log.warning("FANTOMA_INSECURE_NO_AUTH is on: open API on %s.", host)
    if not API_KEY and host != "127.0.0.1":
        log.warning("Serving without a key on %s — every host that can reach the port can drive the browser.", host)
    log.info("Fantoma server starting on %s:%d (auth=%s)", host, port, "on" if API_KEY else "OFF")
    app.run(host=host, port=port, threaded=False)
