"""Answer for the site while the instance is asleep, and wake it up.

The instance stops itself when nobody has used the site for a while (see
deploy/sleep-when-idle.sh), which takes the bill from about $21 a month to
about $7. The cost of that is a cold start: for roughly 40 seconds after a
visitor arrives, there is nothing at kalys.eraliev.com to answer them.

This function is what answers instead. CloudFront sits in front of the site
with two origins - the instance, and this function - and fails over to this one
when the instance does not respond. It starts the instance and returns a page
that waits and then reloads.

Two endpoints, both GET:

    /status   JSON: {"state": "stopped"|"pending"|"running", "starting": bool}
    anything  the waiting page

The page polls /status on this function's own URL, so the Function URL needs
CORS allowing the site's origin. Polling the site itself instead would be
circular: while the instance is down, every path returns this page.

It is deliberately dependency-free (boto3 is already in the Lambda runtime) and
deliberately small: it is the only thing standing between a visitor and a blank
error page.
"""

import json
import os

import boto3

INSTANCE_ID = os.environ["KALYS_INSTANCE_ID"]
SITE_URL = os.environ.get("KALYS_SITE_URL", "https://kalys.eraliev.com")
# Measured at Step 17: 17 seconds from launch to the app answering, plus the
# time EC2 takes to act on StartInstances. Promising a little more than it
# takes is better than the other way round.
EXPECTED_SECONDS = int(os.environ.get("KALYS_EXPECTED_SECONDS", "45"))

ec2 = boto3.client("ec2")

# Substrings that appear in the User-Agent of things that are not a person.
# Googlebot and friends all carry "Mozilla" too, so the allow-list below is not
# enough on its own.
BOT_HINTS = (
    "bot", "crawl", "spider", "slurp", "curl", "wget", "python-requests",
    "scanner", "scrapy", "masscan", "zgrab", "httpx", "nuclei", "semrush",
    "ahrefs", "headlesschrome", "go-http-client", "libwww", "okhttp",
)

ROBOTS = """User-agent: *
Disallow: /
"""


def looks_like_a_person(headers: dict) -> bool:
    """Whether this request is a browser opening the page, rather than a robot.

    Waking the instance costs money, and it happens before the Turnstile gate
    can refuse anyone - the gate lives in the app, which is asleep. So the
    filter has to be here, and it has to be cheap and conservative.

    Three signals, all from headers the error-page request still carries:
    a top-level page load asks for HTML, every real browser says "Mozilla",
    and robots usually announce themselves. Getting this wrong in the strict
    direction is harmless: a request that fails the test is still offered a
    link that wakes the instance when clicked, and no robot clicks a link.
    """
    accept = headers.get("accept", "")
    agent = headers.get("user-agent", "").lower()
    if "text/html" not in accept:
        return False
    if "mozilla" not in agent:
        return False
    return not any(hint in agent for hint in BOT_HINTS)


def instance_state() -> str:
    """One of pending, running, stopping, stopped, shutting-down, terminated."""
    answer = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    return answer["Reservations"][0]["Instances"][0]["State"]["Name"]


def wake() -> tuple[str, bool]:
    """Start the instance if it is stopped. Returns (state, did_we_start_it).

    Starting an instance that is already running or pending is harmless, but it
    is still worth not calling: StartInstances on a `stopping` instance fails,
    and the honest answer there is "wait", not an error page.
    """
    state = instance_state()
    if state == "stopped":
        ec2.start_instances(InstanceIds=[INSTANCE_ID])
        return "pending", True
    return state, False


def json_response(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        "body": json.dumps(body),
    }


def html_response(body: str, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "text/html; charset=utf-8",
            # Never let CloudFront or a browser keep this page: the whole point
            # is that it stops being true in about 40 seconds.
            "cache-control": "no-store, no-cache, must-revalidate",
        },
        "body": body,
    }


def lambda_handler(event, context):  # noqa: ARG001 - the runtime passes both
    path = event.get("requestContext", {}).get("http", {}).get("path", "/")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    query = event.get("queryStringParameters") or {}

    if path.rstrip("/").endswith("/robots.txt"):
        # Answered here rather than by the app, so a crawler gets a real answer
        # without the instance having to be awake to give it. Disallow, because
        # anything a crawler indexes while the instance sleeps would be the
        # waiting page, and because crawling costs wake-ups.
        return {
            "statusCode": 200,
            "headers": {"content-type": "text/plain", "cache-control": "max-age=86400"},
            "body": ROBOTS,
        }

    if path.rstrip("/").endswith("/status"):
        try:
            state = instance_state()
        except Exception as error:  # noqa: BLE001 - the page must still work
            print(f"describe failed: {error}")
            return json_response({"state": "unknown"}, status=200)
        return json_response({"state": state})

    # `?wake=1` is the link on the page below: an explicit request from
    # somebody who is actually looking at a screen.
    if query.get("wake") != "1" and not looks_like_a_person(headers):
        print(f"not waking for {headers.get('user-agent', '-')[:80]!r}")
        return html_response(asleep_page())

    try:
        state, started = wake()
    except Exception as error:  # noqa: BLE001 - never show a stack trace
        print(f"wake failed: {error}")
        state, started = "unknown", False

    print(f"serving the waiting page; instance is {state}, started={started}")
    return html_response(waiting_page())


def waiting_page() -> str:
    """The page a visitor sees while the server wakes up.

    No images: the visitor is already waiting, and the cost of waking the
    server should not be paid twice. Everything here is text and CSS.
    """
    status_url = os.environ.get("KALYS_STATUS_URL", "/status")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kalys is waking up</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    background: #000; color: #ededed;
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 24px;
  }}
  main {{ max-width: 30rem; text-align: center; }}
  h1 {{ font-size: 2rem; margin: 0 0 .5rem; letter-spacing: -.02em; }}
  .dot {{
    display: inline-block; width: .5rem; height: .5rem; border-radius: 50%;
    background: #e11d62; margin-left: .35rem; vertical-align: middle;
    animation: pulse 1.4s ease-in-out infinite;
  }}
  @keyframes pulse {{ 0%, 100% {{ opacity: .25; }} 50% {{ opacity: 1; }} }}
  p {{ color: #a1a1a1; margin: .75rem 0; }}
  .bar {{
    height: 3px; border-radius: 3px; background: #1c1c1c;
    overflow: hidden; margin: 1.75rem 0 .75rem;
  }}
  .bar i {{
    display: block; height: 100%; width: 0%; background: #e11d62;
    transition: width .6s linear;
  }}
  .count {{ font-variant-numeric: tabular-nums; font-size: .85rem; color: #6c6c6c; }}
  .why {{
    margin-top: 2.5rem; padding-top: 1.5rem; border-top: 1px solid #1c1c1c;
    font-size: .85rem; color: #6c6c6c; text-align: left;
  }}
  @media (prefers-reduced-motion: reduce) {{
    .dot {{ animation: none; opacity: 1; }}
    .bar i {{ transition: none; }}
  }}
</style>
</head>
<body>
<main>
  <h1>Waking up<span class="dot"></span></h1>
  <p>The server was asleep. It takes about {EXPECTED_SECONDS} seconds.</p>
  <div class="bar"><i id="bar"></i></div>
  <div class="count" id="count">starting the server&hellip;</div>
  <p class="why">
    Kalys runs on one small server that stops itself when nobody is using it,
    and starts again when someone arrives. It is a student project, so it is
    cheaper to wait forty seconds than to pay for a server that is idle
    twenty-three hours a day.
  </p>
</main>
<script>
  var EXPECTED = {EXPECTED_SECONDS};
  var started = Date.now();
  var bar = document.getElementById('bar');
  var count = document.getElementById('count');

  function tick() {{
    var seconds = (Date.now() - started) / 1000;
    // Approach 95% over the expected time and then crawl, so the bar never
    // claims to be finished before the server actually answers.
    var share = Math.min(0.95, seconds / EXPECTED) + Math.min(0.04, seconds / 1200);
    bar.style.width = (share * 100).toFixed(1) + '%';
    count.textContent = Math.round(seconds) + 's';
  }}
  setInterval(tick, 500);
  tick();

  function check() {{
    fetch({status_url!r}, {{ cache: 'no-store' }})
      .then(function (r) {{
        // A non-JSON or error answer means something other than this function
        // replied - which is only possible once the instance is up and
        // CloudFront has stopped failing over. That counts as awake. It is
        // what makes polling through the site's own origin work as well as
        // polling this function's URL directly.
        // ...but not in the first seconds: no instance boots that fast, so an
        // error this early means the status URL is wrong, not that the server
        // is up. Without this guard a bad URL bounces the visitor to a site
        // that is still down, which fails over back to here, for ever.
        var tooEarly = (Date.now() - started) / 1000 < 12;
        if (!r.ok) {{ return {{ state: tooEarly ? 'stopped' : 'running' }}; }}
        return r.json().catch(function () {{
          return {{ state: tooEarly ? 'stopped' : 'running' }};
        }});
      }})
      .then(function (data) {{
        if (data.state === 'running') {{
          // Running means EC2 has started it, not that the app is listening
          // yet. The reload itself is the real test: if the app is still
          // coming up, CloudFront fails over and this page comes back.
          count.textContent = 'almost there\\u2026';
          setTimeout(function () {{ location.replace({SITE_URL!r}); }}, 4000);
        }}
      }})
      .catch(function () {{ /* keep waiting; the next poll may work */ }});
  }}
  setInterval(check, 3000);
  setTimeout(check, 1500);
</script>
</body>
</html>"""


def asleep_page() -> str:
    """Shown to a request that does not look like a person opening the page.

    Deliberately not an error: a robot gets something cheap and harmless, and
    anybody the filter judged wrongly gets a link that wakes the instance. One
    click is a small price for never turning a real visitor away, and a robot
    does not click.
    """
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Kalys is asleep</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; min-height: 100vh; display: flex;
    align-items: center; justify-content: center;
    background: #000; color: #ededed; padding: 24px; text-align: center;
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  h1 { font-size: 1.6rem; margin: 0 0 .5rem; }
  p { color: #a1a1a1; max-width: 26rem; }
  a.wake {
    display: inline-block; margin-top: 1.5rem; padding: .7rem 1.4rem;
    background: #e11d62; color: #fff; text-decoration: none; border-radius: 999px;
    font-weight: 600;
  }
  a.wake:hover { background: #c4164f; }
</style>
</head>
<body>
<main>
  <h1>The server is asleep</h1>
  <p>
    Kalys runs on one small server that stops itself when nobody is using it.
    Starting it takes about 45 seconds.
  </p>
  <a class="wake" href="?wake=1">Wake the server</a>
</main>
</body>
</html>"""
