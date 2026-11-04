# Wake on demand: let the server sleep

The instance costs about **$21/month** running all day. Most of the day nobody
is on the site. This makes it stop itself when idle and start again when a
visitor arrives, for about **$7/month**.

| | always on | wake on demand |
| --- | --- | --- |
| `t3.small` compute | $15.18 | ~$1.25 (about 2 h/day) |
| 30 GB disk | $2.40 | $2.40 |
| Public IPv4 address | $3.65 | $3.65 |
| CloudFront + Lambda | - | $0 (free tier) |
| **Total** | **~$21** | **~$7** |

Note what does *not* go away: the disk and the IP address are charged whether
the instance runs or not, so **$6/month is the floor** for this design. The
saving is the compute, about $14/month. Prices are us-east-1, checked
2026-11-04; confirm them before relying on the figures.

## How it works

Sleeping is the easy half. The instance's `InstanceInitiatedShutdownBehavior`
is `stop`, so `shutdown -h now` from inside stops it and keeps the disk - no
AWS credentials, no IAM permission, no scheduler. A systemd timer checks every
5 minutes whether anyone has used the site and shuts down when nobody has.

Waking is the hard half, because **while the instance is asleep nothing answers
at kalys.eraliev.com**. Something always-on has to. So:

```
visitor -> CloudFront ---> origin.eraliev.com  (the instance)   <- normally
                      \
                       -> Lambda function URL  (the waker)      <- when asleep
```

CloudFront holds an *origin group*: it tries the instance, and when the
instance does not answer it fails over to the Lambda. The Lambda calls
`StartInstances` and returns a page that waits and reloads. The visitor sees
"waking up, about 45 seconds" instead of an error.

## Order matters

**Build the waker first.** If the timer is enabled before CloudFront can serve
the waiting page, the site is simply down the first time it sleeps. The last
step of this runbook is the one that turns sleeping on.

## 1. A second name for the instance (Route 53)

CloudFront must dial the instance by a name whose certificate matches, and
`kalys.eraliev.com` is about to point at CloudFront instead. So the instance
needs a second name of its own.

Route 53 -> hosted zone `eraliev.com` -> Create record:

| Field | Value |
| --- | --- |
| Record name | `origin` |
| Type | `A` |
| Value | `184.194.188.123` (the Elastic IP) |
| TTL | 300 |

Check it before going on: `dig +short origin.eraliev.com` must print the
Elastic IP.

## 2. Teach Caddy the second name

`deploy/Caddyfile` already lists both names and writes an access log. On the
instance:

```bash
cd ~/kalys_v2 && git pull
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

The access log goes in `/var/lib/caddy`, beside Caddy's certificates, rather
than in `/var/log/caddy`. The conventional path was tried first and Caddy
refused it with `permission denied` although the directory was `caddy:caddy`
and mode 755 - so something other than file permissions, most likely an
AppArmor profile or the namespace of the already-running process. Rather than
chase that, the log went where Caddy demonstrably writes already.

Note that the failed reload did **not** take the site down: Caddy keeps serving
the previous configuration when a new one will not load. That is the reason to
prefer `reload` over `restart` on a live machine.

Caddy will ask Let's Encrypt for a certificate covering `origin.eraliev.com`.
That only works because step 1 points the name straight at the Elastic IP.
Confirm with `curl -sI https://origin.eraliev.com | head -1` - a `200` or a
`401` from the Turnstile gate both prove the certificate is good.

**Do step 1 and 2 before step 6**, and never point `origin` at CloudFront: the
certificate renewal needs the name to reach the machine directly.

## 3. A certificate for CloudFront (ACM)

CloudFront needs its own certificate, and it must be in **us-east-1**.

ACM (N. Virginia) -> Request certificate -> Public:

- Domain name: `kalys.eraliev.com`
- Validation: DNS
- After requesting, press **Create records in Route 53** - ACM writes the
  validation record itself. Status becomes "Issued" in a few minutes.

## 4. The waker (Lambda)

IAM -> Policies -> Create policy -> JSON, paste `deploy/waker/policy.json`,
name it `KalysWakeInstance`. It allows exactly two things: start *this one
instance*, and read instance state. No stop (the instance stops itself), no
launch, no terminate.

`ec2:DescribeInstances` has `"Resource": "*"` because AWS does not support
resource-level permissions on it - it is read-only, so the exposure is seeing
instance state.

IAM -> Roles -> Create role -> AWS service -> Lambda, attach
`KalysWakeInstance` **and** `AWSLambdaBasicExecutionRole` (for logs), name it
`KalysWakerRole`.

Lambda -> Create function:

| Field | Value |
| --- | --- |
| Name | `kalys-waker` |
| Runtime | Python 3.12 |
| Architecture | arm64 (cheaper, and this never leaves the free tier anyway) |
| Execution role | `KalysWakerRole` |

Paste `deploy/waker/lambda_function.py` into the editor and Deploy. Then:

- **Configuration -> General -> Timeout**: 10 seconds (the default 3 is tight
  for two EC2 API calls).
- **Configuration -> Environment variables**:

  | Key | Value |
  | --- | --- |
  | `KALYS_INSTANCE_ID` | `i-0aa39205f5024aa85` |
  | `KALYS_SITE_URL` | `https://kalys.eraliev.com` |
  | `KALYS_STATUS_URL` | *(set it in the next step)* |

- **Configuration -> Function URL -> Create**: auth type **NONE**, and
  configure CORS with allow-origin `https://kalys.eraliev.com`, allow-method
  `GET`.

Copy the Function URL (it looks like
`https://abc123.lambda-url.us-east-1.on.aws/`) and set
`KALYS_STATUS_URL` to that URL plus `status`, for example
`https://abc123.lambda-url.us-east-1.on.aws/status`.

Why a public URL: the waiting page polls it from the visitor's browser, so it
cannot be signed. The worst someone can do with it is keep the instance awake,
which costs at most what running it all day costs today - about $15/month - and
the role cannot do anything else. If that ever matters, put the status
endpoint behind the Turnstile gate instead.

Test it before going on:

```bash
curl -s "$KALYS_STATUS_URL"            # {"state": "running"}
curl -s "$FUNCTION_URL" | head -5      # the waiting page
```

## 5. CloudFront

CloudFront -> Create distribution.

**Origin 1 - the instance**

| Field | Value |
| --- | --- |
| Origin domain | `origin.eraliev.com` |
| Protocol | HTTPS only |
| Minimum origin SSL protocol | TLSv1.2 |
| Name | `kalys-instance` |
| Connection timeout | 3 seconds |
| Connection attempts | 1 |
| **Response timeout** | **60 seconds** |

The response timeout matters more than it looks. The default is 30 seconds,
and a Kyrgyz question takes about 40: it is answered in Russian and then
translated, so nothing streams until the whole answer exists. At 30 seconds
CloudFront would cut off every Kyrgyz answer.

Connection timeout 3s with 1 attempt is what makes the failover quick when the
instance is asleep.

**Origin 2 - the waker**

| Field | Value |
| --- | --- |
| Origin domain | the Function URL host, e.g. `abc123.lambda-url.us-east-1.on.aws` |
| Protocol | HTTPS only |
| Name | `kalys-waker` |

**Origin group**

Create an origin group containing both, primary `kalys-instance`, secondary
`kalys-waker`, with failover on **500, 502, 503, 504** (connection errors and
timeouts trigger it too).

Origin failover only applies to `GET`, `HEAD` and `OPTIONS`. That is enough:
the first page load is a GET, which is all that has to work while the instance
is asleep.

**Default behaviour**

| Field | Value |
| --- | --- |
| Origin | the origin group |
| Viewer protocol policy | Redirect HTTP to HTTPS |
| Allowed methods | GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE |
| Cache policy | **CachingDisabled** |
| Origin request policy | **AllViewer** |
| Compress objects | No (Caddy already compresses) |

`AllViewer` forwards the visitor's `Host` header, so the app sees
`kalys.eraliev.com` rather than `origin.eraliev.com`. That matters: Chainlit
builds its WebSocket URL from what it is told, and with the origin's name it
would send browsers straight to the instance, around CloudFront. Caddy serves
both names from one block, so the mismatch between the dialled name and the
`Host` header is fine.

`CachingDisabled` is required: this is a chat, nothing is cacheable, and
CloudFront needs it to pass WebSocket upgrades through.

**Settings**

- Alternate domain name (CNAME): `kalys.eraliev.com`
- Custom SSL certificate: the ACM certificate from step 3
- Price class: whatever you prefer; "North America and Europe" is cheapest.

Deploy, and wait for the distribution to finish (a few minutes). Test it by its
own name first, before touching DNS:

```bash
curl -sI https://d111111abcdef8.cloudfront.net | head -1
```

## 6. Point the site at CloudFront (Route 53)

Route 53 -> `eraliev.com` -> the `kalys` A record -> Edit:

- Turn **Alias** on
- Route traffic to: *Alias to CloudFront distribution*, pick the distribution

Leave `origin` pointing at the Elastic IP. Only the `kalys` record changes, as
CLAUDE.md requires.

Wait for DNS, then check the site still works end to end: open
<https://kalys.eraliev.com>, pass the Turnstile gate, ask a question, and watch
the answer stream. If the answer streams, WebSockets are getting through
CloudFront.

## 7. Turn sleeping on

Only now, and only once step 6 works. On the instance:

```bash
cd ~/kalys_v2 && git pull
sudo cp deploy/kalys-sleep.service deploy/kalys-sleep.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kalys-sleep.timer
systemctl list-timers kalys-sleep.timer
```

Watch one decision without acting on it:

```bash
sudo /home/ubuntu/kalys_v2/deploy/sleep-when-idle.sh   # prints why, or nothing
journalctl -t kalys-sleep -n 20
```

## 8. Test the whole cycle

1. Stop the instance by hand (console, or wait 20 idle minutes).
2. Open <https://kalys.eraliev.com>. You should get the waiting page within a
   few seconds, not an error.
3. It should reload into the chat in about 45 seconds.
4. Ask a question to confirm the app came back on its own.

## If something goes wrong

| Symptom | Likely cause |
| --- | --- |
| Waiting page never reloads | `KALYS_STATUS_URL` is wrong, or CORS on the Function URL does not allow `https://kalys.eraliev.com`. Open the browser console. |
| Waiting page shows while the site is up | CloudFront is failing over wrongly - the origin is returning 5xx, or the connection timeout is too short. |
| Kyrgyz answers cut off, others fine | CloudFront origin **response** timeout is still 30s. Raise it to 60. |
| Chat loads but never answers | WebSocket is not getting through: check the cache policy is CachingDisabled and the behaviour allows all methods. |
| Certificate error at the origin | `origin.eraliev.com` does not point at the Elastic IP, so Let's Encrypt could not issue. |
| The instance never sleeps | `/var/log/caddy/access.log` is missing, or an SSM session is open - both deliberately keep it awake. `journalctl -t kalys-sleep`. |
| The instance sleeps mid-conversation | Raise `KALYS_IDLE_MINUTES` in `kalys-sleep.service`. |

## Going back

Nothing here is one-way:

```bash
sudo systemctl disable --now kalys-sleep.timer   # stop it sleeping
```

and point the `kalys` A record back at `184.194.188.123`. The CloudFront
distribution and the Lambda can be left in place; they cost nothing when
unused.
