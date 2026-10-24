"""Tests for the robot gate.

The cookie tests matter most: the cookie is the only thing standing between
the internet and a chat that spends money, so a forged or expired one must
never pass. The siteverify tests use a fake HTTP client, except for one that
calls Cloudflare's published always-fails test key for real.
"""

import httpx
import pytest

from kalys import gate


def test_cookie_round_trip():
    value = gate.make_cookie("s3cret")
    assert gate.check_cookie(value, "s3cret")


def test_a_cookie_signed_with_another_secret_fails():
    value = gate.make_cookie("s3cret")
    assert not gate.check_cookie(value, "different")


def test_an_expired_cookie_fails():
    value = gate.make_cookie("s3cret", now=1000, ttl=60)
    assert gate.check_cookie(value, "s3cret", now=1030)
    assert not gate.check_cookie(value, "s3cret", now=1100)


def test_an_edited_expiry_fails():
    # The expiry is inside the signed part, so extending it breaks the
    # signature - a visitor cannot grant themselves a longer pass.
    value = gate.make_cookie("s3cret", now=1000, ttl=60)
    _, _, signature = value.rpartition(".")
    assert not gate.check_cookie(f"99999999999.{signature}", "s3cret")


@pytest.mark.parametrize("value", ["", "nodot", ".", "abc.def", "1000."])
def test_malformed_cookies_fail(value):
    assert not gate.check_cookie(value, "s3cret")


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status
        self.sent = None

    def post(self, url, data):
        self.sent = (url, data)
        return FakeResponse(self._payload, self._status)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(gate, "SECRET", "test-secret")
    monkeypatch.setattr(gate, "HOSTNAMES", ("kalys.eraliev.com",))


def _ok(**overrides):
    payload = {"success": True, "action": gate.ACTION, "hostname": "kalys.eraliev.com"}
    payload.update(overrides)
    return payload


def test_a_good_token_passes(configured):
    client = FakeClient(_ok())
    assert gate.verify_token("tok", "1.2.3.4", client=client) is True
    url, data = client.sent
    assert url == gate.SITEVERIFY_URL
    assert data["response"] == "tok"
    assert data["remoteip"] == "1.2.3.4"


def test_success_false_fails(configured):
    assert gate.verify_token("tok", None, client=FakeClient(_ok(success=False))) is False


def test_a_token_for_another_action_fails(configured):
    # A token minted by a different widget action must not open this door.
    assert gate.verify_token("tok", None, client=FakeClient(_ok(action="signup"))) is False


def test_a_token_from_another_hostname_fails(configured):
    assert gate.verify_token("tok", None, client=FakeClient(_ok(hostname="evil.test"))) is False


def test_a_server_error_fails_closed(configured):
    # Breaking the check must not be a way to remove the gate.
    assert gate.verify_token("tok", None, client=FakeClient({}, status=500)) is False


def test_an_empty_or_huge_token_is_rejected_without_a_call(configured):
    client = FakeClient(_ok())
    assert gate.verify_token("", None, client=client) is False
    assert gate.verify_token("x" * 5000, None, client=client) is False
    assert client.sent is None


def test_verification_is_refused_when_unconfigured(monkeypatch):
    monkeypatch.setattr(gate, "SECRET", "")
    assert gate.verify_token("tok", None, client=FakeClient(_ok())) is False


def test_client_ip_prefers_the_proxy_header():
    headers = {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}
    assert gate.client_ip(headers, "10.0.0.1") == "9.9.9.9"


def test_client_ip_falls_back_to_the_socket_address():
    assert gate.client_ip({}, "10.0.0.1") == "10.0.0.1"


def test_the_gate_is_off_without_keys(monkeypatch):
    for name in ("SITEKEY", "SECRET", "COOKIE_SECRET"):
        monkeypatch.setattr(gate, name, "")
    monkeypatch.setattr(gate, "HOSTNAMES", ())
    assert gate.is_enabled() is False
    assert "OFF" in gate.describe()
    assert "TURNSTILE_SITEKEY" in gate.describe()


def test_the_gate_is_on_with_every_key(monkeypatch):
    monkeypatch.setattr(gate, "SITEKEY", "site")
    monkeypatch.setattr(gate, "SECRET", "sec")
    monkeypatch.setattr(gate, "COOKIE_SECRET", "cookie")
    monkeypatch.setattr(gate, "HOSTNAMES", ("kalys.eraliev.com",))
    assert gate.is_enabled() is True
    assert "ON" in gate.describe()


def test_install_does_nothing_when_unconfigured(monkeypatch):
    monkeypatch.setattr(gate, "SITEKEY", "")
    assert gate.install(object()) is False


def test_the_gate_page_carries_the_sitekey_and_action():
    html = gate.gate_html("0x4AAA")
    assert 'data-sitekey="0x4AAA"' in html
    assert f'data-action="{gate.ACTION}"' in html
    assert "challenges.cloudflare.com/turnstile/v0/api.js" in html


def test_the_gate_page_explains_itself():
    # A challenge with no reason looks broken.
    html = gate.gate_html("x").lower()
    assert "student project" in html and "law" in html
