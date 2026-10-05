"""A polite HTTP client for the Ministry of Justice legal database (cbd.minjust.gov.kg).

Rules this client follows:
- At most 1 request per second.
- A clear User-Agent with contact info.
- Retries with exponential backoff for server errors and network errors.
- Every response is cached on disk, so we never download the same thing twice.
- If the site blocks us (403, 429, or a captcha page), we stop. We never try to bypass it.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

BASE_URL = "https://cbd.minjust.gov.kg"
USER_AGENT = "Kalys/0.1 (student project; +https://eraliev.com; ezhanbolot@gmail.com)"
DEFAULT_CACHE_DIR = Path("data/cache/minjust")

MIN_SECONDS_BETWEEN_REQUESTS = 1.0
MAX_RETRIES = 4  # waits 2, 4, 8, 16 seconds between tries
BLOCKED_STATUS_CODES = {403, 429}
CAPTCHA_MARKERS = ("captcha", "g-recaptcha", "hcaptcha", "cf-challenge")


class BlockedError(Exception):
    """The site refused us. We must stop and ask a human what to do."""


@dataclass
class Response:
    """One saved answer from the site."""

    url: str
    status_code: int
    content_type: str
    body: bytes
    from_cache: bool

    def json(self):
        return json.loads(self.body)

    def raise_for_status(self) -> None:
        """Fail loudly if the answer is not 200 OK."""
        if self.status_code != 200:
            raise RuntimeError(f"{self.url} returned {self.status_code}")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def cache_key(method: str, path: str, params: dict | None, json_body: dict | None) -> str:
    """Make a stable file name for one request. Same request -> same name."""
    request = {
        "method": method,
        "path": path,
        "params": params or {},
        "json": json_body,
    }
    text = json.dumps(request, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def looks_like_captcha(body: bytes) -> bool:
    start = body[:5000].decode("utf-8", errors="ignore").lower()
    return any(marker in start for marker in CAPTCHA_MARKERS)


class MinjustClient:
    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        http: httpx.Client | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.cache_dir = Path(cache_dir)
        self.http = http or httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=60.0,
        )
        # sleep and clock are parameters so tests can run without real waiting.
        self.sleep = sleep
        self.clock = clock
        self.last_request_at: float | None = None

    def get(self, path: str, params: dict | None = None) -> Response:
        return self.request("GET", path, params=params)

    def post(
        self, path: str, params: dict | None = None, json_body: dict | None = None
    ) -> Response:
        return self.request("POST", path, params=params, json_body=json_body)

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Response:
        key = cache_key(method, path, params, json_body)
        cached = self._read_cache(key)
        if cached is not None:
            return cached

        for attempt in range(MAX_RETRIES + 1):
            self._wait_for_rate_limit()
            try:
                http_response = self.http.request(method, path, params=params, json=json_body)
            except httpx.TransportError as error:
                if attempt == MAX_RETRIES:
                    raise
                print(f"Network error ({error!r}), retrying...")
                self.sleep(2 ** (attempt + 1))
                continue

            if http_response.status_code in BLOCKED_STATUS_CODES:
                raise BlockedError(
                    f"{method} {http_response.url} returned {http_response.status_code}. "
                    "Stopping. Do not retry without asking."
                )
            if looks_like_captcha(http_response.content):
                raise BlockedError(f"{method} {http_response.url} returned a captcha page.")

            if http_response.status_code >= 500 and attempt < MAX_RETRIES:
                print(f"Server error {http_response.status_code}, retrying...")
                self.sleep(2 ** (attempt + 1))
                continue

            response = Response(
                url=str(http_response.url),
                status_code=http_response.status_code,
                content_type=http_response.headers.get("content-type", ""),
                body=http_response.content,
                from_cache=False,
            )
            # Cache only real answers. A 5xx after all retries is not saved.
            if response.status_code < 500:
                self._write_cache(key, method, path, params, json_body, response)
            return response

        raise RuntimeError("unreachable")

    def _wait_for_rate_limit(self) -> None:
        if self.last_request_at is not None:
            elapsed = self.clock() - self.last_request_at
            if elapsed < MIN_SECONDS_BETWEEN_REQUESTS:
                self.sleep(MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
        self.last_request_at = self.clock()

    def _read_cache(self, key: str) -> Response | None:
        meta_file = self.cache_dir / f"{key}.json"
        body_file = self.cache_dir / f"{key}.body"
        if not (meta_file.exists() and body_file.exists()):
            return None
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        return Response(
            url=meta["url"],
            status_code=meta["status_code"],
            content_type=meta["content_type"],
            body=body_file.read_bytes(),
            from_cache=True,
        )

    def _write_cache(self, key, method, path, params, json_body, response: Response) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # The body is saved byte for byte, untouched.
        (self.cache_dir / f"{key}.body").write_bytes(response.body)
        meta = {
            "method": method,
            "path": path,
            "params": params,
            "json": json_body,
            "url": response.url,
            "status_code": response.status_code,
            "content_type": response.content_type,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        text = json.dumps(meta, ensure_ascii=False, indent=2)
        (self.cache_dir / f"{key}.json").write_text(text, encoding="utf-8")
