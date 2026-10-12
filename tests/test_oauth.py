"""OAuth sign-in for the hosted endpoint (ChatGPT-style connectors)."""

import base64
import hashlib
import os
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

os.environ["CLOUDHPC_MCP_MODE"] = "local"

from cloudhpc_mcp import oauth  # noqa: E402

BASE = "https://mcp.example.test"
CHATGPT = "https://chatgpt.com/connector_platform_oauth_redirect"
GOOD_KEY = "good-api-key"


async def _validate(key):
    return key == GOOD_KEY


def _pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    return verifier, oauth._s256(verifier)


@pytest.fixture
def setup():
    seen = {}

    async def mcp_endpoint(request):
        seen["x-api-key"] = request.headers.get("x-api-key")
        seen["authorization"] = request.headers.get("authorization")
        return JSONResponse({"ok": True})

    async def cimd(url):
        return {"client_id": url, "client_name": "ChatGPT", "redirect_uris": [CHATGPT]}

    provider = oauth.OAuthProvider("s3cret", BASE, validate_key=_validate, fetch_json=cimd)
    inner = Starlette(routes=provider.routes() + [Route("/mcp", mcp_endpoint, methods=["POST"])])
    client = TestClient(oauth.AuthGate(inner, provider), base_url=BASE, follow_redirects=False)
    return client, provider, seen


def _authorize(client, client_id, key=GOOD_KEY, redirect=CHATGPT):
    verifier, challenge = _pkce()
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
              "code_challenge": challenge, "code_challenge_method": "S256",
              "state": "xyz", "scope": "cloudhpc", "resource": BASE + "/mcp"}
    page = client.get("/oauth/authorize", params=params)
    assert page.status_code == 200, page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    token = re.search(r'name="form_token" value="([^"]+)"', page.text).group(1)
    r = client.post("/oauth/authorize", data={"form_token": token, "api_key": key,
                                              "action": "allow"})
    return r, verifier


def test_metadata(setup):
    client, provider, _ = setup
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == BASE + "/mcp" and prm["authorization_servers"] == [BASE]
    assert client.get("/.well-known/oauth-protected-resource").json()["resource"] == BASE
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == BASE and asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["client_id_metadata_document_supported"] is True
    assert asm["token_endpoint_auth_methods_supported"] == ["none"]


def test_unauthenticated_mcp_gets_challenge(setup):
    client, _, seen = setup
    r = client.post("/mcp", json={})
    assert r.status_code == 401
    assert 'resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource/mcp"' \
        in r.headers["www-authenticate"]
    # existing clients: header key or raw bearer key pass through untouched
    assert client.post("/mcp", json={}, headers={"X-API-Key": "k1"}).status_code == 200
    assert seen["x-api-key"] == "k1"
    assert client.post("/mcp", json={}, headers={"Authorization": "Bearer raw"}).status_code == 200
    assert seen["authorization"] == "Bearer raw"


def test_full_flow_with_dcr(setup):
    client, provider, seen = setup
    reg = client.post("/oauth/register", json={"client_name": "ChatGPT", "redirect_uris": [CHATGPT]})
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]

    r, verifier = _authorize(client, client_id)
    assert r.status_code == 303
    loc = urlsplit(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.geturl().startswith(CHATGPT) and q["state"] == ["xyz"] and q["iss"] == [BASE]
    code = q["code"][0]
    assert GOOD_KEY not in r.headers["location"]          # key never in clear

    tok = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CHATGPT,
        "client_id": client_id, "code_verifier": verifier, "resource": BASE + "/mcp"})
    assert tok.status_code == 200, tok.text
    t = tok.json()
    assert t["token_type"] == "Bearer" and t["expires_in"] == 3600
    assert tok.headers["cache-control"] == "no-store"
    assert GOOD_KEY not in tok.text

    r = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {t['access_token']}"})
    assert r.status_code == 200
    assert seen["x-api-key"] == GOOD_KEY and seen["authorization"] is None

    ref = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                            "refresh_token": t["refresh_token"]})
    assert ref.status_code == 200 and ref.json()["access_token"] != t["access_token"]


def test_cimd_client(setup):
    client, _, _ = setup
    r, verifier = _authorize(client, "https://chatgpt.com/oauth/client.json")
    assert r.status_code == 303 and "code=" in r.headers["location"]


def test_rejections(setup):
    client, provider, _ = setup
    # redirect not on the allow-list: refused at registration
    bad = client.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"]})
    assert bad.status_code == 400
    reg = client.post("/oauth/register", json={"redirect_uris": [CHATGPT]}).json()
    # wrong API key: stays on the page, no redirect
    r, _ = _authorize(client, reg["client_id"], key="wrong")
    assert r.status_code == 400 and "not accepted" in r.text
    # wrong PKCE verifier
    r, verifier = _authorize(client, reg["client_id"])
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    t = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": CHATGPT, "client_id": reg["client_id"],
                                          "code_verifier": "x" * 43})
    assert t.json()["error"] == "invalid_grant"
    # code used by another client
    t = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": CHATGPT, "client_id": "other",
                                          "code_verifier": verifier})
    assert t.json()["error"] == "invalid_grant"
    # forged / expired access token -> 401 so the client refreshes
    r = client.post("/mcp", json={}, headers={"Authorization": "Bearer chpc1.AAAAforged"})
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    expired = provider.sealer.seal("access", {"k": GOOD_KEY, "aud": BASE + "/mcp",
                                              "exp": time.time() - 1})
    assert client.post("/mcp", json={}, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    # a code is not accepted as an access token (purpose binding)
    assert client.post("/mcp", json={}, headers={"Authorization": f"Bearer {code}"}).status_code == 401


def test_refresh_fails_after_key_regenerated(setup):
    client, provider, _ = setup
    reg = client.post("/oauth/register", json={"redirect_uris": [CHATGPT]}).json()
    r, verifier = _authorize(client, reg["client_id"])
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    t = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": CHATGPT, "client_id": reg["client_id"],
                                          "code_verifier": verifier}).json()

    async def revoked(key):
        return False
    provider.validate_key = revoked
    r = client.post("/oauth/token", data={"grant_type": "refresh_token",
                                          "client_id": reg["client_id"],
                                          "refresh_token": t["refresh_token"]})
    assert r.json()["error"] == "invalid_grant"


def test_cancel_returns_access_denied(setup):
    client, _, _ = setup
    reg = client.post("/oauth/register", json={"redirect_uris": [CHATGPT]}).json()
    _, challenge = _pkce()
    page = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": reg["client_id"], "redirect_uri": CHATGPT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s"})
    token = re.search(r'name="form_token" value="([^"]+)"', page.text).group(1)
    r = client.post("/oauth/authorize", data={"form_token": token, "action": "deny"})
    assert r.status_code == 303 and "error=access_denied" in r.headers["location"]


def test_redirect_allow_list_and_key_rotation():
    p = oauth.OAuthProvider("new,old", BASE)
    assert p.redirect_allowed("https://chatgpt.com/connector/oauth/abc123")
    assert not p.redirect_allowed("https://chatgpt.com/connector/oauth/")
    assert not p.redirect_allowed("https://chatgpt.com/connector/oauth/a/../../evil")
    assert p.redirect_allowed("http://localhost:33418/callback")
    assert not p.redirect_allowed("https://chatgpt.com.evil.example/connector/oauth/x")
    old = oauth.Sealer("old").seal("access", {"k": "x"})
    assert p.sealer.unseal("access", old) == {"k": "x"}      # still valid after rotation


def test_remote_app_wiring(monkeypatch):
    from cloudhpc_mcp import server
    monkeypatch.delenv("CLOUDHPC_OAUTH_SECRET", raising=False)
    assert not isinstance(server.remote_app(), oauth.AuthGate)
    monkeypatch.setenv("CLOUDHPC_OAUTH_SECRET", "x")
    monkeypatch.setenv("CLOUDHPC_PUBLIC_URL", BASE)
    monkeypatch.setattr(server, "HOST", "0.0.0.0")   # as on the server (no Host pinning)
    app = server.remote_app()
    assert isinstance(app, oauth.AuthGate)
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/.well-known/oauth-authorization-server").json()["issuer"] == BASE
        r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                   headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 401
        r = c.post("/mcp", headers={"X-API-Key": "k",
                                    "Accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "1"}}})
        assert r.status_code == 200 and "serverInfo" in r.text


def test_gemini_redirects():
    p = oauth.OAuthProvider("k", BASE)
    assert p.redirect_allowed("https://oauth-redirect.googleusercontent.com/r/user_bound_custom-mcp-abc-x_io")
    assert p.redirect_allowed("https://oauth-redirect-sandbox.googleusercontent.com/r/abc")
    assert p.redirect_allowed("https://vertexaisearch.cloud.google.com/oauth-redirect")
    assert not p.redirect_allowed("https://evil.googleusercontent.com/r/abc")
    assert not p.redirect_allowed("https://oauth-redirect.googleusercontent.com/r/a/../x")


def test_register_keeps_only_allowed_redirects(setup):
    client, _, _ = setup
    good = "https://oauth-redirect.googleusercontent.com/r/abc"
    r = client.post("/oauth/register", json={"redirect_uris": [good, "https://evil.example/cb"]})
    assert r.status_code == 201 and r.json()["redirect_uris"] == [good]
    _, verifier = None, None
    r, verifier = _authorize(client, r.json()["client_id"], redirect=good)
    assert r.status_code == 303 and r.headers["location"].startswith(good + "?")


def test_signin_page_branding(setup):
    client, _, _ = setup
    reg = client.post("/oauth/register", json={"redirect_uris": [CHATGPT]}).json()
    _, challenge = _pkce()
    page = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": reg["client_id"], "redirect_uri": CHATGPT,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    assert 'href="/favicon.png"' in page.text
    assert 'href="https://docs.cloudhpc.cloud/APIKEY/"' in page.text
    assert "img-src 'self'" in page.headers["content-security-policy"]
    for path in ("/favicon.png", "/favicon.ico", "/apple-touch-icon.png"):
        r = client.get(path)
        assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_smithery_redirect():
    p = oauth.OAuthProvider("k", BASE)
    assert p.redirect_allowed("https://connect.smithery.ai/oauth/callback")
    assert not p.redirect_allowed("https://connect.smithery.ai/oauth/callback/x")
