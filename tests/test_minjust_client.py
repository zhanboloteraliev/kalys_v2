import httpx
import pytest

from kalys.minjust_client import USER_AGENT, BlockedError, MinjustClient, cache_key


class FakeTime:
    """A fake clock. sleep() moves time forward instantly, so tests are fast."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(tmp_path, handler):
    fake_time = FakeTime()
    http = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))
    client = MinjustClient(
        cache_dir=tmp_path, http=http, sleep=fake_time.sleep, clock=fake_time.clock
    )
    return client, fake_time


def test_cache_key_is_stable_and_ignores_dict_order():
    a = cache_key("GET", "/x", {"a": "1", "b": "2"}, None)
    b = cache_key("GET", "/x", {"b": "2", "a": "1"}, None)
    assert a == b
    assert a != cache_key("GET", "/x", {"a": "1", "b": "3"}, None)


def test_second_request_comes_from_disk_cache(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    client, _ = make_client(tmp_path, handler)
    first = client.get("/api/v1/GetMajorDocuments")
    second = client.get("/api/v1/GetMajorDocuments")

    assert len(calls) == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.json() == {"ok": True}


def test_body_is_saved_byte_for_byte(tmp_path):
    raw = b'{"name": "\xd0\x9a\xd0\xbe\xd0\xb4\xd0\xb5\xd0\xba\xd1\x81"}'

    client, _ = make_client(tmp_path, lambda request: httpx.Response(200, content=raw))
    client.get("/x")

    saved = list(tmp_path.glob("*.body"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == raw


def test_waits_one_second_between_requests(tmp_path):
    client, fake_time = make_client(tmp_path, lambda request: httpx.Response(200, json={}))
    client.get("/a")
    client.get("/b")
    client.get("/c")
    assert fake_time.sleeps == [1.0, 1.0]


def test_retries_server_errors_with_backoff(tmp_path):
    answers = [500, 503, 200]

    def handler(request):
        return httpx.Response(answers.pop(0), json={})

    client, fake_time = make_client(tmp_path, handler)
    response = client.get("/x")

    assert response.status_code == 200
    # Backoff waits 2 s, then 4 s. These waits are longer than 1 s, so no extra rate-limit wait.
    assert fake_time.sleeps == [2, 4]


@pytest.mark.parametrize("status", [403, 429])
def test_stops_when_blocked(tmp_path, status):
    client, _ = make_client(tmp_path, lambda request: httpx.Response(status))
    with pytest.raises(BlockedError):
        client.get("/x")
    assert list(tmp_path.glob("*")) == []  # nothing cached


def test_stops_on_captcha_page(tmp_path):
    page = b"<html><div class='g-recaptcha'></div></html>"
    client, _ = make_client(tmp_path, lambda request: httpx.Response(200, content=page))
    with pytest.raises(BlockedError):
        client.get("/x")


def test_post_sends_json_body(tmp_path):
    seen = {}

    def handler(request):
        seen["body"] = request.content
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={})

    client, _ = make_client(tmp_path, handler)
    client.post("/api/v1/GetDocuments", params={"pageNumber": 1}, json_body={"lang": "ru"})

    assert seen["params"] == {"pageNumber": "1"}
    assert b'"lang"' in seen["body"]


def test_default_client_sends_user_agent_with_contact_info():
    client = MinjustClient()
    assert client.http.headers["User-Agent"] == USER_AGENT
    assert "@" in USER_AGENT
