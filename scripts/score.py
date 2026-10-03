"""Rescan the whole challenge window and rebuild the leaderboard.

Every run recounts from scratch, so deleted messages lose their points and edited
messages are judged on their current text. Scoring rules live in rules.py.

Usage:
  DISCORD_BOT_TOKEN=... python scripts/score.py               # scan, write site + admin files
  DISCORD_BOT_TOKEN=... python scripts/score.py --dry-run     # scan, print only
  DISCORD_BOT_TOKEN=... python scripts/score.py --post-admin  # also DM the admin report
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import admin_report  # noqa: E402
import discord_api  # noqa: E402
from discord_api import DiscordError, get  # noqa: E402
from rules import compute  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
LEADERBOARD_PATH = ROOT / "site" / "leaderboard.json"
ADMIN_DIR = ROOT / "admin"

DISCORD_EPOCH_MS = 1420070400000
CHAT_TYPES = {0, 19}      # DEFAULT, REPLY — skips joins, pins, boosts, thread-starter stubs
FORUM_TYPES = {15, 16}    # forum and media channels: every post is a thread
REQUIRED_CHANNELS = {"general", "wins", "content_review", "ugc_opportunities", "socials", "october_challenge"}


class EmptyBoardError(Exception):
    pass


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def parse_ts(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def snowflake_at(dt):
    """Smallest Discord ID that could exist at datetime `dt`."""
    return str((int(dt.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22)


def validate(cfg):
    problems = []
    missing = REQUIRED_CHANNELS - set(cfg["channels"])
    if missing:
        problems.append(f"channels missing: {sorted(missing)}")
    problems += [f"channel '{k}' has no real ID" for k, v in cfg["channels"].items() if not str(v).isdigit()]
    if not cfg.get("student_role") or "REPLACE" in cfg["student_role"]:
        problems.append("student_role is not set")
    for key in ("excluded_user_ids", "staff_user_ids"):
        problems += [f"{key} entry '{u}' is not a user ID" for u in cfg.get(key, []) if not str(u).isdigit()]
    if problems:
        raise SystemExit("config.json needs fixing:\n  - " + "\n  - ".join(problems))


# ---------------------------------------------------------------- fetching

def everyone_mode(cfg):
    return cfg["student_role"].lstrip("@").lower() == "everyone"


def fetch_students(guild_id, cfg):
    """Members who count as students. Returns (students, label), or (None, label) when every
    member is a student but the member list isn't readable; collect() then uses message authors."""
    if everyone_mode(cfg):
        role = {"id": None, "name": "everyone"}
    else:
        roles = get(f"/guilds/{guild_id}/roles")
        want = cfg["student_role"].lstrip("@").lower()
        role = next((r for r in roles if r["id"] == want or r["name"].lower() == want), None)
        if role is None:
            names = ", ".join(sorted(r["name"] for r in roles if r["name"] != "@everyone"))
            raise SystemExit(f"No role named '{cfg['student_role']}'. Roles on this server: {names}")

    excluded = set(cfg.get("excluded_user_ids", []))
    students, after = {}, "0"
    while True:
        try:
            batch = get(f"/guilds/{guild_id}/members", limit=1000, after=after)
        except DiscordError as e:
            if e.status == 403 and role["id"] is None:
                print("  member list not readable (Server Members Intent off); using message authors")
                return None, role["name"]
            if e.status == 403:
                raise DiscordError("Can't list server members. Turn on 'Server Members Intent' in the "
                                   "Developer Portal (Bot tab) and try again.", 403)
            raise
        for mem in batch:
            user = mem["user"]
            if user.get("bot") or user["id"] in excluded:
                continue
            if role["id"] is not None and role["id"] not in mem.get("roles", []):
                continue
            students[user["id"]] = {
                "id": user["id"],
                "display_name": mem.get("nick") or user.get("global_name") or user["username"],
                "avatar": user.get("avatar"),
                "joined_at": parse_ts(mem["joined_at"]),
            }
        if len(batch) < 1000:
            return students, role["name"]
        after = batch[-1]["user"]["id"]


def fetch_window(channel_id, start_dt, end_dt, max_pages):
    """All messages in [start_dt, end_dt) for one channel or thread, oldest first."""
    cursor, out = snowflake_at(start_dt), []
    for _ in range(max_pages):
        page = discord_api.get_messages_after(channel_id, cursor)
        for m in page:
            if parse_ts(m["timestamp"]) >= end_dt:
                return out
            out.append(m)
        if len(page) < 100:
            return out
        cursor = page[-1]["id"]
    # Never publish a partial count: a missing page would silently drop points.
    raise DiscordError(f"channel {channel_id} has more than {max_pages * 100} messages in the window; "
                       "raise max_pages_per_channel in config.json")


def archived_threads(channel_id, start_dt):
    """Public archived threads that were still open at some point after the challenge started."""
    out, before = [], None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        resp = get(f"/channels/{channel_id}/threads/archived/public", **params)
        for t in resp.get("threads", []):
            if parse_ts(t["thread_metadata"]["archive_timestamp"]) < start_dt:
                return out
            out.append(t)
        if not resp.get("has_more") or not resp.get("threads"):
            return out
        before = resp["threads"][-1]["thread_metadata"]["archive_timestamp"]


def thread_starter_author(thread, parent_id, parent_msgs):
    """Who wrote the post a thread hangs off (not necessarily who opened the thread)."""
    if thread["id"] in parent_msgs:
        return parent_msgs[thread["id"]]["author"]["id"]
    try:  # starter is older than the window
        return get(f"/channels/{parent_id}/messages/{thread['id']}")["author"]["id"]
    except DiscordError as e:
        if e.status in (403, 404):  # thread not started from a message, or starter deleted
            return thread.get("owner_id")
        raise


def normalize(raw, key, tz, guild_id, channel_id, top_level, thread_owner):
    if raw.get("type", 0) not in CHAT_TYPES or raw.get("webhook_id") or raw["author"].get("bot"):
        return None
    ts = parse_ts(raw["timestamp"])
    ref = raw.get("referenced_message") if raw.get("type") == 19 else None
    return {
        "id": raw["id"],
        "author": raw["author"]["id"],
        "author_name": raw["author"].get("global_name") or raw["author"]["username"],
        "author_avatar": raw["author"].get("avatar"),
        "channel": key,
        "ts": ts,
        "day": ts.astimezone(tz).date().isoformat(),
        "content": raw.get("content", ""),
        "top_level": top_level,
        "reply_to": ref["author"]["id"] if ref else None,
        "thread_owner": thread_owner,
        "jump": f"https://discord.com/channels/{guild_id}/{channel_id}/{raw['id']}",
    }


def collect(cfg, start_dt, end_dt, tz):
    max_pages = cfg.get("max_pages_per_channel", 300)
    infos = {key: get(f"/channels/{cid}") for key, cid in cfg["channels"].items()}
    guild_id = next(iter(infos.values()))["guild_id"]
    students, role_name = fetch_students(guild_id, cfg)
    active = get(f"/guilds/{guild_id}/threads/active").get("threads", [])

    messages = []
    for key, cid in cfg["channels"].items():
        is_forum = infos[key]["type"] in FORUM_TYPES
        parent_msgs = {}
        if not is_forum:
            for raw in fetch_window(cid, start_dt, end_dt, max_pages):
                parent_msgs[raw["id"]] = raw
                n = normalize(raw, key, tz, guild_id, cid, top_level=raw.get("type") == 0, thread_owner=None)
                if n:
                    messages.append(n)

        threads = {t["id"]: t for t in active if t.get("parent_id") == cid}
        threads.update({t["id"]: t for t in archived_threads(cid, start_dt)})
        for t in threads.values():
            owner = t.get("owner_id") if is_forum else thread_starter_author(t, cid, parent_msgs)
            for raw in fetch_window(t["id"], start_dt, end_dt, max_pages):
                starter = is_forum and raw["id"] == t["id"]  # a forum post's opening message
                n = normalize(raw, key, tz, guild_id, t["id"], top_level=starter,
                              thread_owner=None if starter else owner)
                if n:
                    messages.append(n)
        print(f"#{key}: {sum(m['channel'] == key for m in messages)} messages "
              f"({len(threads)} threads{', forum' if is_forum else ''})")

    if students is None:  # every member is a student: anyone who posted (minus exclusions)
        excluded = set(cfg.get("excluded_user_ids", []))
        students = {}
        for m in messages:
            if m["author"] not in excluded:
                students.setdefault(m["author"], {
                    "id": m["author"], "display_name": m["author_name"], "avatar": m["author_avatar"],
                    "joined_at": datetime.min.replace(tzinfo=timezone.utc)})
    print(f"{len(students)} students ({role_name})")
    return students, messages, guild_id


# ---------------------------------------------------------------- run

def run(cfg, leaderboard_path=LEADERBOARD_PATH, admin_dir=ADMIN_DIR, dry_run=False,
        post_admin=False, now=None, force=False):
    validate(cfg)
    tz = ZoneInfo(cfg["timezone"])
    now = now or datetime.now(tz)
    start = date.fromisoformat(cfg["challenge_start"])
    end = date.fromisoformat(cfg["challenge_end"])
    if now.date() > end + timedelta(days=1) and not force:
        print("Challenge is over and the final board is published; not rescanning (use --force).")
        return None, None

    start_dt = datetime.combine(start, time.min, tz)
    end_dt = datetime.combine(end + timedelta(days=1), time.min, tz)
    students, messages, guild_id = collect(cfg, start_dt, min(end_dt, now.astimezone(timezone.utc)), tz)

    if not students:
        raise EmptyBoardError("0 students found. Check student_role in config.json and that the bot "
                              "can see the channels. Not publishing.")
    if not messages and now.date() > start:
        raise EmptyBoardError("0 messages found in any channel after the challenge started. The bot "
                              "probably can't see the channels. Not publishing.")

    board, admin = compute(students, messages, cfg, now)
    s = board["standings"]
    print(f"{board['totals']['points']} points, {board['totals']['messages']} qualifying messages")
    print("Most Consistent:", [(r["display_name"], r["points"]) for r in board["members"][:5]])
    by_msgs = sorted(board["members"], key=lambda r: -r["messages"])
    print("Most Active:    ", [(r["display_name"], r["messages"]) for r in by_msgs[:5]])
    if s["most_consistent"]["tie"] or s["most_active"]["tie"]:
        print("TIE flagged — see admin report")

    if not dry_run:
        write_json(leaderboard_path, board)
        admin_report.write_files(board, admin, admin_dir)
    if post_admin:
        channel = os.environ.get("ADMIN_CHANNEL_ID") or cfg.get("admin_channel_id")
        if not channel and cfg.get("admin_user_id"):
            channel = discord_api.open_dm(cfg["admin_user_id"])  # no admin channel: DM the report instead
        if not channel:
            raise SystemExit("Set admin_user_id (DM) or admin_channel_id in config.json to send the admin report.")
        admin_report.post(board, admin, channel)
        print("Admin report sent.")
    return board, admin


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print results, write nothing")
    parser.add_argument("--post-admin", action="store_true", help="send the admin report on Discord")
    parser.add_argument("--force", action="store_true", help="rescan even after the challenge is final")
    args = parser.parse_args()
    if not os.environ.get("DISCORD_BOT_TOKEN") and sys.stdin.isatty():
        import getpass
        os.environ["DISCORD_BOT_TOKEN"] = getpass.getpass(
            "Paste your bot token and press Enter (it stays hidden as you paste): ").strip()
        token = os.environ["DISCORD_BOT_TOKEN"]
        if not token:
            sys.exit("No token received. Run the command again, paste the token, then press Enter.")
        print(f"Token received ({len(token)} characters, should be about 70). Scanning Discord...")
    try:
        run(load_json(CONFIG_PATH, None), dry_run=args.dry_run, post_admin=args.post_admin, force=args.force)
    except (EmptyBoardError, DiscordError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
