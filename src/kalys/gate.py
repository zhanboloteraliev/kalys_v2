"""A "prove you are not a robot" gate in front of the chat.

Every question costs money at Bedrock, and the daily cap (Step 11) protects the
budget but not the service: automated traffic that found the site would spend
the 100 questions and leave nothing for real visitors. Robots find any new
public address within hours, so an open chat endpoint needs a door.

The door is Cloudflare Turnstile. A visitor loads a small page, the widget
there gives the browser a token, this server checks that token with Cloudflare,
and on success the browser gets a short-lived signed cookie. Nothing reaches
the chat - not even its websocket - without that cookie.

This is a gate, not an account: a visitor passes once and is then bounded by
the 15-question session cap. It stops automated traffic, not a determined
person, which is the point.

The gate turns itself off when TURNSTILE_SITEKEY and TURNSTILE_SECRET are not
set, so local development and the SSM tunnel keep working with no keys at all.
"""

import base64
import hashlib
import hmac
import logging
import os
import time

import httpx

SITEKEY = os.environ.get("TURNSTILE_SITEKEY", "")
SECRET = os.environ.get("TURNSTILE_SECRET", "")
# The hostnames siteverify is allowed to report. Never include localhost in a
# production value: a token minted against a local page would then be accepted
# by the real site.
HOSTNAMES = tuple(
    h.strip() for h in os.environ.get("TURNSTILE_HOSTNAMES", "").split(",") if h.strip()
)
# Signs the cookie. Without it the gate cannot be trusted, so it stays off.
COOKIE_SECRET = os.environ.get("KALYS_GATE_SECRET", "")

log = logging.getLogger("kalys.gate")

ACTION = "chat"
COOKIE_NAME = "kalys_gate"
COOKIE_TTL_SECONDS = 12 * 60 * 60
SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
SITEVERIFY_TIMEOUT_SECONDS = 10
MAX_TOKEN_LENGTH = 2048

# Paths that must work before a visitor has passed: the form's own target, and
# the icons the gate page shows. There is deliberately no GET /gate - Chainlit
# registers a catch-all for its single-page app before we get here, so a GET
# route of ours would be shadowed by it and serve the chat shell instead. The
# middleware below answers any blocked path with the gate page, which makes a
# dedicated URL unnecessary.
OPEN_PATHS = ("/gate/verify", "/favicon", "/logo")


def is_enabled() -> bool:
    """Whether the gate is configured. Missing keys mean no gate at all."""
    return bool(SITEKEY and SECRET and COOKIE_SECRET and HOSTNAMES)


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_cookie(secret: str, now: float | None = None, ttl: int = COOKIE_TTL_SECONDS) -> str:
    """A cookie value saying "this browser passed, until then"."""
    expires = int((now if now is not None else time.time()) + ttl)
    payload = str(expires)
    return f"{payload}.{_sign(payload, secret)}"


def check_cookie(value: str, secret: str, now: float | None = None) -> bool:
    """Whether a cookie is genuine and still valid.

    The signature is compared with `compare_digest` so that a wrong guess takes
    the same time as a right one, and the expiry is inside the signed part so a
    visitor cannot extend their own pass by editing it.
    """
    if not value or "." not in value:
        return False
    payload, _, signature = value.rpartition(".")
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return False
    try:
        expires = int(payload)
    except ValueError:
        return False
    return expires > (now if now is not None else time.time())


def verify_token(token: str, remote_ip: str | None, client=None) -> bool:
    """Ask Cloudflare whether this token is real. Fails closed.

    Checks all three things Cloudflare returns, not just success: the action
    must be the one this page asked for, and the hostname must be a site we
    serve. Without those two, a token minted on any other page protected by the
    same key would be accepted here.

    Note when testing: Cloudflare's published "always passes" test secret
    returns no `action` at all and a hostname of example.com, so it cannot
    exercise this success path. A real widget returns both.
    """
    if not SECRET or not HOSTNAMES:
        return False
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return False

    data = {"secret": SECRET, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip

    try:
        http = client or httpx.Client(timeout=SITEVERIFY_TIMEOUT_SECONDS)
        response = http.post(SITEVERIFY_URL, data=data)
        response.raise_for_status()
        result = response.json()
    except Exception:
        # A network error, a non-2xx, or a body that is not JSON. Letting the
        # visitor through on an error would make the gate removable by
        # breaking it, so an unanswered check is a failed check.
        return False

    passed = bool(
        result.get("success")
        and result.get("action") == ACTION
        and result.get("hostname") in HOSTNAMES
    )
    if not passed:
        # A gate that rejects everyone looks exactly like a gate that is
        # working, so say why. If the action or hostname is wrong, every real
        # visitor is locked out and this line is the only way to find out.
        # None of these fields is a secret; the token and secret are not logged.
        log.warning(
            "Turnstile check failed: success=%s action=%r (want %r) hostname=%r "
            "(want one of %s) error-codes=%s",
            result.get("success"),
            result.get("action"),
            ACTION,
            result.get("hostname"),
            list(HOSTNAMES),
            result.get("error-codes"),
        )
    return passed


def client_ip(headers, fallback: str | None) -> str | None:
    """The visitor's address, trusting Caddy's header when it is there.

    Caddy sets X-Forwarded-For; the first entry is the original client. The
    socket address is the proxy itself, which is useless to Cloudflare.
    """
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return fallback


def gate_html(sitekey: str) -> str:
    """The page a visitor sees before the chat.

    It explains itself: a blank challenge with no reason looks broken, and the
    honest reason - a student is paying for each question - is a better first
    impression than silence.
    """
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kalys</title>
<link rel="icon" href="/favicon">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #000; color: #e9e9e9;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    padding: 16px;
  }}
  main {{ max-width: 26rem; text-align: center; }}
  img {{ width: 190px; height: auto; margin-bottom: 1.5rem; }}
  h1 {{ font-size: 1.05rem; font-weight: 600; margin: 0 0 .6rem; }}
  p {{ font-size: .9rem; line-height: 1.55; color: #a6a6a6; margin: 0 0 1.5rem; }}
  .cf-turnstile {{ display: flex; justify-content: center; }}
  noscript {{ display: block; margin-top: 1rem; font-size: .85rem; color: #a6a6a6; }}
</style>
</head>
<body>
<main>
  <img src="/logo?theme=dark" alt="Kalys">
  <h1>One check before we start</h1>
  <p>
    Kalys answers questions about Kyrgyz law. Every answer costs a little money,
    and this is a student project, so there is a short check first to keep
    automated traffic out. It usually passes on its own.
  </p>
  <form id="gate" action="/gate/verify" method="POST">
    <div class="cf-turnstile"
         data-sitekey="{sitekey}"
         data-action="{ACTION}"
         data-theme="dark"
         data-callback="onPass"></div>
  </form>
  <noscript>This check needs JavaScript. Please turn it on and reload.</noscript>
</main>
<script>
  // Submit as soon as the challenge passes, so a visitor who passes silently
  // never has to find and press a button.
  function onPass() {{ document.getElementById("gate").submit(); }}
</script>
</body>
</html>
"""


def install(app) -> bool:
    """Put the gate in front of `app`. Returns whether it was installed.

    Everything lives in the app the chat already runs in - one more route and
    one middleware - rather than a separate proxy to deploy and keep alive.
    """
    if not is_enabled():
        return False

    from fastapi import Form, Request
    from fastapi.responses import HTMLResponse, RedirectResponse

    @app.post("/gate/verify")
    async def gate_verify(request: Request, token: str = Form(alias="cf-turnstile-response")):
        ip = client_ip(request.headers, request.client.host if request.client else None)
        if not verify_token(token, ip):
            return HTMLResponse(gate_html(SITEKEY), status_code=403)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            make_cookie(COOKIE_SECRET),
            max_age=COOKIE_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    @app.middleware("http")
    async def require_gate(request: Request, call_next):
        path = request.url.path
        if path.startswith(OPEN_PATHS) or check_cookie(
            request.cookies.get(COOKIE_NAME, ""), COOKIE_SECRET
        ):
            return await call_next(request)
        # Serving the gate in place of whatever was asked for means one page
        # covers the chat, its assets and its websocket handshake.
        return HTMLResponse(gate_html(SITEKEY), status_code=401)

    return True


def describe() -> str:
    """One line for the log, so it is obvious which mode the server is in."""
    if is_enabled():
        return f"Turnstile gate ON for {', '.join(HOSTNAMES)}"
    missing = [
        name
        for name, value in [
            ("TURNSTILE_SITEKEY", SITEKEY),
            ("TURNSTILE_SECRET", SECRET),
            ("TURNSTILE_HOSTNAMES", HOSTNAMES),
            ("KALYS_GATE_SECRET", COOKIE_SECRET),
        ]
        if not value
    ]
    return f"Turnstile gate OFF (not set: {', '.join(missing)})"
