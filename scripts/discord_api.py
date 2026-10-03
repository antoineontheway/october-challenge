"""The single throttled path for every Discord API call.

All requests go through `request()`, which:
  - sends the bot token from the DISCORD_BOT_TOKEN env var
  - sleeps between calls so we never burst
  - honours HTTP 429 `retry_after` and retries a bounded number of times
"""
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode

API_BASE = "https://discord.com/api/v10"
MIN_INTERVAL = 0.5  # seconds between any two calls
MAX_RETRIES = 5
USER_AGENT = "DiscordBot (challenge-leaderboard, 2.0)"

_last_call = 0.0
# Swappable for tests: (method, url, headers, body) -> (status, headers, body_bytes)
_transport = None
_sleep = time.sleep


class DiscordError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _http(method, url, headers, body):
    if _transport is not None:
        return _transport(method, url, headers, body)
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def request(method, path, params=None, payload=None, body=None, content_type=None):
    global _last_call
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise DiscordError("DISCORD_BOT_TOKEN is not set")

    url = API_BASE + path + ("?" + urlencode(params) if params else "")
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    if payload is not None:
        content_type = "application/json"
        body = json.dumps(payload).encode()
    if content_type:
        headers["Content-Type"] = content_type

    for attempt in range(MAX_RETRIES):
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            _sleep(wait)
        status, resp_headers, data = _http(method, url, headers, body)
        _last_call = time.monotonic()

        if status == 429:
            try:
                retry_after = float(json.loads(data).get("retry_after", 1))
            except (ValueError, AttributeError):
                retry_after = float(resp_headers.get("Retry-After", 1))
            print(f"  rate limited, sleeping {retry_after:.1f}s (attempt {attempt + 1})")
            _sleep(retry_after + 0.25)
            continue
        if status >= 400:
            raise DiscordError(f"{method} {path} -> HTTP {status}: {data[:300]!r}", status)
        return json.loads(data) if data else None

    raise DiscordError(f"{method} {path} still rate limited after {MAX_RETRIES} attempts")


def get(path, **params):
    return request("GET", path, params or None)


def get_messages_after(channel_id, after_id):
    """One page (up to 100) of messages newer than after_id, sorted oldest first."""
    msgs = get(f"/channels/{channel_id}/messages", after=after_id, limit=100)
    return sorted(msgs or [], key=lambda m: int(m["id"]))


def open_dm(user_id):
    """Channel ID for a direct message with `user_id` (they must share a server with the bot)."""
    return request("POST", "/users/@me/channels", payload={"recipient_id": user_id})["id"]


def post_message(channel_id, content, files=None):
    """Post a message; `files` is an optional list of (filename, bytes) attachments."""
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    if not files:
        return request("POST", f"/channels/{channel_id}/messages", payload=payload)

    boundary = uuid.uuid4().hex
    parts = [(
        'Content-Disposition: form-data; name="payload_json"\r\n'
        "Content-Type: application/json\r\n\r\n", json.dumps(payload).encode())]
    for i, (name, data) in enumerate(files):
        parts.append((
            f'Content-Disposition: form-data; name="files[{i}]"; filename="{name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n", data))
    body = b"".join(f"--{boundary}\r\n{head}".encode() + data + b"\r\n" for head, data in parts)
    body += f"--{boundary}--\r\n".encode()
    return request("POST", f"/channels/{channel_id}/messages", body=body,
                   content_type=f"multipart/form-data; boundary={boundary}")
