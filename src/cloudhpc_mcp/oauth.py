"""OAuth 2.1 sign-in for the hosted (remote) endpoint.

Lets web assistants that only support OAuth (e.g. ChatGPT connectors) use the
server. "Signing in" means pasting the cloudHPC API key on a page served by
this server; the assistant receives encrypted tokens, never the key.

Stateless by design: authorization codes, access and refresh tokens and
dynamically registered client ids are encrypted blobs (AES-GCM), so no
database is needed and any instance can verify them.

  access token   1 hour, contains the API key; the middleware turns it back
                 into the X-API-Key header, so the tools are unchanged.
  refresh token  30 days; the API key is checked again at every refresh, so
                 regenerating the key in the cloudHPC profile revokes access.

Enabled when CLOUDHPC_OAUTH_SECRET is set (comma-separated list: the first
secret encrypts, all are tried to decrypt, for key rotation).
Requests with X-API-Key keep working as before.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import time
from collections import deque
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .client import CloudHPCClient, CloudHPCError

PREFIX = "chpc1."
SCOPE = "cloudhpc"
CODE_TTL = 60
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
FORM_TTL = 15 * 60

# where the authorization code may be sent (exact URL or prefix ending in "/")
DEFAULT_REDIRECTS = (
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chatgpt.com/connector/oauth/",
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
_LOCAL_REDIRECT = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?/")


# ------------------------------------------------------------------ sealing

class Sealer:
    """Authenticated encryption of small JSON payloads, bound to a purpose."""

    def __init__(self, secrets_csv: str):
        keys = [s.strip() for s in secrets_csv.split(",") if s.strip()]
        if not keys:
            raise ValueError("empty OAuth secret")
        self._keys = [AESGCM(hashlib.sha256(k.encode()).digest()) for k in keys]

    def seal(self, purpose: str, payload: dict) -> str:
        nonce = os.urandom(12)
        ct = self._keys[0].encrypt(nonce, json.dumps(payload, separators=(",", ":")).encode(),
                                   purpose.encode())
        return PREFIX + base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")

    def unseal(self, purpose: str, token: str, now: float | None = None) -> dict | None:
        """Payload if the token is valid, of this purpose and not expired; else None."""
        if not token or not token.startswith(PREFIX):
            return None
        raw = token[len(PREFIX):]
        try:
            blob = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except ValueError:
            return None
        if len(blob) < 29:
            return None
        for key in self._keys:
            try:
                data = json.loads(key.decrypt(blob[:12], blob[12:], purpose.encode()))
            except Exception:
                continue
            if not isinstance(data, dict):
                return None
            if data.get("exp") is not None and (now or time.time()) > data["exp"]:
                return None
            return data
        return None


# ------------------------------------------------------------- the provider

class OAuthProvider:
    def __init__(self, secret: str, base_url: str, extra_redirects: str = "",
                 validate_key=None, fetch_json=None):
        self.sealer = Sealer(secret)
        self.base = base_url.rstrip("/")
        self.redirects = DEFAULT_REDIRECTS + tuple(
            r.strip() for r in extra_redirects.split(",") if r.strip())
        self.validate_key = validate_key or _validate_key
        self.fetch_json = fetch_json or _fetch_json
        self._cimd_cache: dict[str, tuple[float, dict]] = {}
        self._hits: dict[str, deque] = {}

    # ---------------------------------------------------------- helpers
    @property
    def resources(self) -> set[str]:
        return {self.base, self.base + "/", self.base + "/mcp"}

    def resource_metadata_url(self) -> str:
        return f"{self.base}/.well-known/oauth-protected-resource/mcp"

    def challenge(self, error: str | None = None) -> str:
        parts = [f'resource_metadata="{self.resource_metadata_url()}"', f'scope="{SCOPE}"']
        if error:
            parts.insert(0, f'error="{error}"')
        return "Bearer " + ", ".join(parts)

    def redirect_allowed(self, uri: str) -> bool:
        if not isinstance(uri, str) or len(uri) > 2000:
            return False
        if _LOCAL_REDIRECT.match(uri):
            return True
        for allowed in self.redirects:
            if allowed.endswith("/"):
                # prefix entry: one more path segment (e.g. ChatGPT's callback id)
                rest = uri[len(allowed):] if uri.startswith(allowed) else ""
                if re.fullmatch(r"[A-Za-z0-9._~-]+", rest):
                    return True
            elif uri == allowed:
                return True
        return False

    def _rate_ok(self, ip: str, limit: int = 10) -> bool:
        now = time.monotonic()
        q = self._hits.setdefault(ip, deque())
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        if len(self._hits) > 10_000:
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 60}
        return True

    async def client_info(self, client_id: str) -> dict | None:
        """{"name", "redirect_uris"} for a registered (DCR) or CIMD client."""
        if not client_id:
            return None
        if client_id.startswith(PREFIX):
            data = self.sealer.unseal("client", client_id)
            if not data:
                return None
            return {"name": data.get("n") or "Unnamed app", "redirect_uris": data.get("r", [])}
        if client_id.startswith("https://"):
            hit = self._cimd_cache.get(client_id)
            if hit and time.time() - hit[0] < 3600:
                return hit[1]
            try:
                doc = await self.fetch_json(client_id)
            except Exception:
                return None
            if not isinstance(doc, dict) or doc.get("client_id") != client_id:
                return None
            uris = [u for u in doc.get("redirect_uris") or [] if isinstance(u, str)]
            info = {"name": str(doc.get("client_name") or urlsplit(client_id).hostname),
                    "redirect_uris": uris}
            self._cimd_cache[client_id] = (time.time(), info)
            return info
        return None

    # ---------------------------------------------------------- metadata
    async def protected_resource(self, request: Request) -> Response:
        path = request.url.path
        resource = self.base + "/mcp" if path.endswith("/mcp") else self.base
        return JSONResponse({
            "resource": resource,
            "authorization_servers": [self.base],
            "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"],
            "resource_name": "cloudHPC",
            "resource_documentation": "https://github.com/CFD-FEA-SERVICE/cloudhpc-mcp",
        })

    async def authorization_server(self, request: Request) -> Response:
        return JSONResponse({
            "issuer": self.base,
            "authorization_endpoint": f"{self.base}/oauth/authorize",
            "token_endpoint": f"{self.base}/oauth/token",
            "registration_endpoint": f"{self.base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE],
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
            "service_documentation": "https://github.com/CFD-FEA-SERVICE/cloudhpc-mcp",
        })

    # ------------------------------------------------------ registration
    async def register(self, request: Request) -> Response:
        if not self._rate_ok("reg:" + _ip(request), 20):
            return _oauth_error("slow_down", "Too many registrations", 429)
        try:
            body = await request.json()
        except Exception:
            return _oauth_error("invalid_client_metadata", "Body must be JSON")
        uris = body.get("redirect_uris") if isinstance(body, dict) else None
        if not isinstance(uris, list) or not uris or not all(self.redirect_allowed(u) for u in uris):
            return _oauth_error("invalid_redirect_uri",
                                "redirect_uris missing or not allowed by this server")
        name = str(body.get("client_name") or "")[:80]
        now = int(time.time())
        client_id = self.sealer.seal("client", {"r": uris, "n": name, "iat": now})
        return JSONResponse({
            "client_id": client_id,
            "client_id_issued_at": now,
            "client_name": name,
            "redirect_uris": uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPE,
        }, status_code=201)

    # ----------------------------------------------------- authorization
    async def _check_request(self, p: dict) -> tuple[dict | None, str | None]:
        """Validate authorization parameters. (client info, error message)."""
        if p.get("response_type") != "code":
            return None, "Unsupported response_type (only 'code')."
        if p.get("code_challenge_method") != "S256" or not p.get("code_challenge"):
            return None, "PKCE with S256 is required."
        client = await self.client_info(p.get("client_id", ""))
        if not client:
            return None, "Unknown application (client_id)."
        uri = p.get("redirect_uri", "")
        if uri not in client["redirect_uris"] or not self.redirect_allowed(uri):
            return None, "This redirect address is not allowed."
        res = p.get("resource")
        if res and res.rstrip("/") not in {r.rstrip("/") for r in self.resources}:
            return None, "Unknown resource."
        return client, None

    async def authorize(self, request: Request) -> Response:
        if request.method == "GET":
            params = dict(request.query_params)
            client, err = await self._check_request(params)
            if err:
                return _page(_error_html(err), 400)
            form_token = self.sealer.seal("form", {"p": params, "exp": time.time() + FORM_TTL})
            return _page(_consent_html(client["name"], params["redirect_uri"], form_token))

        # POST: the user submitted the API key
        form = await request.form()
        data = self.sealer.unseal("form", str(form.get("form_token", "")))
        if not data:
            return _page(_error_html("This page has expired. Start the connection again from "
                                     "your assistant."), 400)
        params = data["p"]
        client, err = await self._check_request(params)
        if err:
            return _page(_error_html(err), 400)
        if form.get("action") == "deny":
            return _redirect(params["redirect_uri"], {"error": "access_denied",
                                                      "state": params.get("state"),
                                                      "iss": self.base})
        if not self._rate_ok("auth:" + _ip(request)):
            return _page(_consent_html(client["name"], params["redirect_uri"],
                                       str(form.get("form_token")),
                                       "Too many attempts: wait a minute and retry."), 429)
        key = str(form.get("api_key", "")).strip()
        if not key or not await self.validate_key(key):
            return _page(_consent_html(client["name"], params["redirect_uri"],
                                       str(form.get("form_token")),
                                       "The API key was not accepted by cloudHPC. Copy it "
                                       "again from your profile page."), 400)
        code = self.sealer.seal("code", {
            "k": key, "c": params["client_id"], "r": params["redirect_uri"],
            "ch": params["code_challenge"], "res": params.get("resource") or self.base + "/mcp",
            "s": SCOPE, "exp": time.time() + CODE_TTL})
        return _redirect(params["redirect_uri"], {"code": code, "state": params.get("state"),
                                                  "iss": self.base})

    # ------------------------------------------------------------- token
    async def token(self, request: Request) -> Response:
        form = await request.form()
        grant = form.get("grant_type")
        client_id = str(form.get("client_id", ""))
        if grant == "authorization_code":
            code = self.sealer.unseal("code", str(form.get("code", "")))
            if not code:
                return _oauth_error("invalid_grant", "Code invalid or expired")
            if code["c"] != client_id or code["r"] != form.get("redirect_uri"):
                return _oauth_error("invalid_grant", "client_id or redirect_uri mismatch")
            verifier = str(form.get("code_verifier", ""))
            if not verifier or _s256(verifier) != code["ch"]:
                return _oauth_error("invalid_grant", "PKCE verification failed")
            res = form.get("resource") or code["res"]
            return self._issue(code["k"], client_id, res)
        if grant == "refresh_token":
            rt = self.sealer.unseal("refresh", str(form.get("refresh_token", "")))
            if not rt or rt["c"] != client_id:
                return _oauth_error("invalid_grant", "Refresh token invalid or expired")
            if not await self.validate_key(rt["k"]):
                return _oauth_error("invalid_grant", "The API key is no longer valid")
            return self._issue(rt["k"], client_id, form.get("resource") or rt["res"])
        return _oauth_error("unsupported_grant_type", "Use authorization_code or refresh_token")

    def _issue(self, key: str, client_id: str, resource: str) -> Response:
        if resource.rstrip("/") not in {r.rstrip("/") for r in self.resources}:
            return _oauth_error("invalid_target", "Unknown resource")
        now = time.time()
        at = self.sealer.seal("access", {"k": key, "aud": resource, "s": SCOPE,
                                         "exp": now + ACCESS_TTL})
        rt = self.sealer.seal("refresh", {"k": key, "c": client_id, "res": resource,
                                          "exp": now + REFRESH_TTL})
        return JSONResponse({"access_token": at, "token_type": "Bearer",
                             "expires_in": ACCESS_TTL, "refresh_token": rt, "scope": SCOPE},
                            headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    def api_key_from_access_token(self, token: str) -> str | None:
        data = self.sealer.unseal("access", token)
        if not data or data.get("aud", "").rstrip("/") not in {r.rstrip("/") for r in self.resources}:
            return None
        return data.get("k")

    def routes(self) -> list[Route]:
        return [
            Route("/.well-known/oauth-protected-resource", self.protected_resource),
            Route("/.well-known/oauth-protected-resource/mcp", self.protected_resource),
            Route("/.well-known/oauth-authorization-server", self.authorization_server),
            Route("/oauth/register", self.register, methods=["POST"]),
            Route("/oauth/authorize", self.authorize, methods=["GET", "POST"]),
            Route("/oauth/token", self.token, methods=["POST"]),
        ]


# --------------------------------------------------------- MCP auth gate

class AuthGate:
    """ASGI middleware for /mcp when OAuth is enabled.

    - X-API-Key present: passed through unchanged (existing clients).
    - Authorization: Bearer chpc1...: decrypted into X-API-Key; 401 if invalid
      or expired (the client then refreshes the token).
    - Authorization: Bearer <raw API key>: passed through (existing clients).
    - nothing: 401 with the OAuth discovery challenge.
    """

    def __init__(self, app, provider: OAuthProvider, path: str = "/mcp"):
        self.app, self.provider, self.path = app, provider, path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").rstrip("/") == self.path:
            return await self.app(scope, receive, send)
        headers = [(k, v) for k, v in scope.get("headers") or []]
        names = {k for k, _ in headers}
        if b"x-api-key" not in names:
            auth = next((v.decode("latin-1") for k, v in headers if k == b"authorization"), "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not token:
                return await _unauthorized(send, self.provider.challenge())
            if token.startswith(PREFIX):
                key = self.provider.api_key_from_access_token(token)
                if not key:
                    return await _unauthorized(send, self.provider.challenge("invalid_token"))
                headers = [(k, v) for k, v in headers if k != b"authorization"]
                headers.append((b"x-api-key", key.encode()))
                scope = dict(scope, headers=headers)
        return await self.app(scope, receive, send)


async def _unauthorized(send, challenge: str):
    body = b'{"error": "unauthorized", "error_description": "Sign in or send X-API-Key"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"www-authenticate", challenge.encode()),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


# ------------------------------------------------------------------ utils

def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in fwd.split(",") if h.strip()]
    return hops[-1] if hops else (request.client.host if request.client else "unknown")


def _oauth_error(error: str, desc: str, status: int = 400) -> Response:
    return JSONResponse({"error": error, "error_description": desc}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _redirect(uri: str, params: dict) -> Response:
    q = urlencode({k: v for k, v in params.items() if v is not None})
    return RedirectResponse(uri + ("&" if "?" in uri else "?") + q, status_code=303)


async def _validate_key(key: str) -> bool:
    try:
        await CloudHPCClient(api_key=key).ram_options()
        return True
    except CloudHPCError:
        return False


async def _fetch_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as c:
        r = await c.get(url, headers={"Accept": "application/json"})
        r.raise_for_status()
        if len(r.content) > 20_000:
            raise ValueError("client metadata document too large")
        return r.json()


_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                               "form-action 'self' https: http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def _page(body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>cloudHPC sign-in</title><style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f6f9;color:#1b2533;margin:0}}
main{{max-width:440px;margin:6vh auto;background:#fff;border-radius:12px;padding:28px 24px;box-shadow:0 2px 12px #0001}}
h1{{font-size:20px;margin:0 0 4px}} p{{line-height:1.45}} .muted{{color:#5b6778;font-size:14px}}
.warn{{background:#fff6e5;border-left:4px solid #f6a623;padding:10px 12px;font-size:14px}}
.err{{background:#fdecec;border-left:4px solid #d64545;padding:10px 12px;font-size:14px}}
label{{display:block;font-weight:600;margin:18px 0 6px}}
input[type=password]{{width:100%;box-sizing:border-box;padding:10px;border:1px solid #c5cdd8;border-radius:8px;font-size:15px}}
.row{{display:flex;gap:10px;margin-top:18px}} button{{flex:1;padding:11px;border-radius:8px;border:0;font-size:15px;cursor:pointer}}
.ok{{background:#1f6feb;color:#fff}} .no{{background:#e7ebf0;color:#1b2533}}
</style></head><body><main>{body}</main></body></html>"""
    return HTMLResponse(doc, status_code=status, headers=_SECURITY_HEADERS)


def _consent_html(client_name: str, redirect_uri: str, form_token: str, error: str = "") -> str:
    host = urlsplit(redirect_uri).hostname or redirect_uri
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<h1>Connect to cloudHPC</h1>
<p><strong>{html.escape(client_name)}</strong> ({html.escape(host)}) wants to use your cloudHPC
account.</p>
<p class="warn">It will be able to see your simulations and storage, upload files, and
<strong>launch or stop simulations, which are billed to your account</strong>.</p>
{err}
<form method="post" action="/oauth/authorize" autocomplete="off">
<input type="hidden" name="form_token" value="{html.escape(form_token)}">
<label for="k">cloudHPC API key</label>
<input id="k" name="api_key" type="password" required spellcheck="false">
<p class="muted">Copy it from your profile page on cloudHPC. The key is not shown to the
assistant. To revoke access later, regenerate the key in your profile.</p>
<div class="row"><button class="no" name="action" value="deny" formnovalidate>Cancel</button>
<button class="ok" name="action" value="allow">Connect</button></div>
</form>"""


def _error_html(message: str) -> str:
    return f"""<h1>Cannot connect to cloudHPC</h1><p class="err">{html.escape(message)}</p>
<p class="muted">Start the connection again from your assistant. If the problem persists,
contact support@cloudhpc.cloud.</p>"""


def from_env() -> OAuthProvider | None:
    secret = os.environ.get("CLOUDHPC_OAUTH_SECRET", "").strip()
    if not secret:
        return None
    base = os.environ.get("CLOUDHPC_PUBLIC_URL", "https://mcp.cloudhpc.cloud")
    return OAuthProvider(secret, base, os.environ.get("CLOUDHPC_OAUTH_EXTRA_REDIRECTS", ""))
