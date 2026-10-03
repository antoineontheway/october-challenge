"""End-to-end tests of score.run against a fake Discord API."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import discord_api  # noqa: E402
import score  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
CH = {"general": "101", "wins": "102", "content_review": "103",
      "ugc_opportunities": "104", "socials": "105", "october_challenge": "106"}
CFG = {
    "challenge_name": "October Challenge", "community_name": "UGC Buildout",
    "challenge_start": "2026-10-02", "challenge_end": "2026-10-31",
    "timezone": "America/Los_Angeles", "student_role": "Student",
    "excluded_user_ids": ["90"], "staff_user_ids": ["90"], "admin_user_id": "90", "channels": CH,
    "max_pages_per_channel": 300,
}
NOW = datetime(2026, 10, 10, 9, 0, tzinfo=PT)

_seq = 0


def user(uid, name=None, bot=False):
    return {"id": uid, "username": name or f"user{uid}", "global_name": name, "bot": bot, "avatar": None}


def msg(uid, when, content, type_=0, reply_to=None):
    """`when` is a UTC ISO string."""
    global _seq
    _seq += 1
    dt = datetime.fromisoformat(when).replace(tzinfo=timezone.utc)
    m = {"id": str(int(score.snowflake_at(dt)) + _seq), "timestamp": dt.isoformat(),
         "type": type_, "content": content, "author": user(uid)}
    if reply_to:
        m["type"] = 19
        m["referenced_message"] = {"author": user(reply_to)}
    return m


class FakeDiscord:
    def __init__(self):
        self.channels = {cid: {"type": 0, "messages": []} for cid in CH.values()}
        self.threads = []        # dicts with id, parent_id, owner_id, archived, archive_timestamp
        self.members = [
            {"user": user("1", "Ana"), "roles": ["R"], "nick": "Ana B", "joined_at": "2026-09-01T00:00:00Z"},
            {"user": user("2", "Ben"), "roles": ["R"], "nick": None, "joined_at": "2026-09-01T00:00:00Z"},
            {"user": user("3", "Lurker"), "roles": [], "nick": None, "joined_at": "2026-09-01T00:00:00Z"},
            {"user": user("90", "Antoine"), "roles": ["R"], "nick": None, "joined_at": "2026-01-01T00:00:00Z"},
            {"user": user("91", "Bot", bot=True), "roles": ["R"], "nick": None, "joined_at": "2026-01-01T00:00:00Z"},
        ]
        self.members_status = 200
        self.posts = []

    def add_thread(self, tid, parent, owner, messages, archived=False):
        self.threads.append({"id": tid, "parent_id": parent, "owner_id": owner, "type": 11,
                             "thread_metadata": {"archived": archived,
                                                 "archive_timestamp": "2026-10-08T00:00:00+00:00"}})
        self.channels[tid] = {"type": 11, "messages": messages}

    def __call__(self, method, url, headers, body):
        u = urlparse(url)
        path = u.path.replace("/api/v10", "")
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = path.strip("/").split("/")
        ok = lambda data: (200, {}, json.dumps(data).encode())  # noqa: E731

        if method == "POST" and path == "/users/@me/channels":
            return ok({"id": "DM" + json.loads(body)["recipient_id"]})
        if method == "POST":
            self.posts.append((path, headers.get("Content-Type", ""), body))
            return ok({"id": "1"})
        if parts[:1] == ["guilds"]:
            if parts[2] == "roles":
                return ok([{"id": "R", "name": "Student"}, {"id": "E", "name": "@everyone"}])
            if parts[2] == "members":
                if self.members_status != 200:
                    return self.members_status, {}, b'{"message": "Missing Access"}'
                after = int(q["after"])
                return ok([mm for mm in self.members if int(mm["user"]["id"]) > after])
            if parts[2:] == ["threads", "active"]:
                return ok({"threads": [t for t in self.threads if not t["thread_metadata"]["archived"]]})
        if parts[0] == "channels":
            cid = parts[1]
            if len(parts) == 2:
                return ok({"id": cid, "type": self.channels[cid]["type"], "guild_id": "G"})
            if parts[2:] == ["threads", "archived", "public"]:
                return ok({"threads": [t for t in self.threads
                                       if t["parent_id"] == cid and t["thread_metadata"]["archived"]],
                           "has_more": False})
            if len(parts) == 4:  # single message
                found = [m for m in self.channels[cid]["messages"] if m["id"] == parts[3]]
                return ok(found[0]) if found else (404, {}, b'{"message": "Unknown Message"}')
            after = int(q["after"])
            newer = sorted((m for m in self.channels[cid]["messages"] if int(m["id"]) > after),
                           key=lambda m: int(m["id"]))[: int(q["limit"])]
            return ok(list(reversed(newer)))
        raise AssertionError(f"unexpected call {method} {path}")


class RunTest(unittest.TestCase):
    def setUp(self):
        os.environ["DISCORD_BOT_TOKEN"] = "test-token"
        discord_api._sleep = lambda s: None
        self.fake = FakeDiscord()
        discord_api._transport = self.fake
        self.tmp = tempfile.TemporaryDirectory()
        self.lb = Path(self.tmp.name) / "site" / "leaderboard.json"
        self.admin = Path(self.tmp.name) / "admin"

    def tearDown(self):
        discord_api._transport = None
        self.tmp.cleanup()

    def run_score(self, **kw):
        kw.setdefault("now", NOW)
        return score.run(dict(CFG, **kw.pop("cfg", {})), self.lb, self.admin, **kw)

    def by_id(self, board):
        return {m["id"]: m for m in board["members"]}

    def test_full_flow(self):
        win = msg("1", "2026-10-05T18:00:00", "Landed my first paid UGC deal!")
        self.fake.channels["102"]["messages"] += [
            win,
            msg("2", "2026-10-05T19:00:00", "congrats that is huge news", reply_to="1"),
            msg("2", "2026-10-05T19:05:00", "system", type_=18),  # thread created notice
        ]
        # Thread hanging off Ana's win: Ben's message counts for Prize 2, Ana's own doesn't.
        self.fake.add_thread(win["id"], "102", owner="2", messages=[
            msg("2", "2026-10-05T20:00:00", "so proud of you for this"),
            msg("1", "2026-10-05T20:10:00", "thank you so much Ben"),
        ], archived=True)
        self.fake.channels["106"]["messages"] += [msg("1", "2026-10-05T18:30:00", "Done ✅ 5 pitches, 2 videos")]
        self.fake.channels["105"]["messages"] += [msg("2", "2026-10-05T18:30:00", "https://tiktok.com/@ben/1")]
        self.fake.channels["101"]["messages"] += [
            msg("1", "2026-10-06T06:30:00", "late night grind session here"),  # Oct 5 in Pacific
            msg("90", "2026-10-06T06:30:00", "welcome to the challenge everyone"),
            msg("3", "2026-10-06T06:30:00", "not a student but chatting"),
        ]
        board, admin = self.run_score()
        r = self.by_id(board)

        self.assertEqual(set(r), {"1", "2"})          # role filter, excluded user, bot
        self.assertEqual(r["1"]["display_name"], "Ana B")
        self.assertEqual(r["1"]["points"], 3)          # win + done
        self.assertEqual(r["1"]["daily"], {"2026-10-05": {"win": 1, "done": 2}})
        self.assertEqual(r["1"]["messages"], 1)        # #general only
        self.assertEqual(r["2"]["points"], 2)          # brand
        self.assertEqual(r["2"]["messages"], 2)        # reply + thread on Ana's win
        self.assertEqual(board["totals"]["pitches"], 5)
        self.assertTrue(self.lb.exists())
        self.assertIn("tiktok.com", (self.admin / "links.csv").read_text())
        self.assertIn("Most Consistent", (self.admin / "report.md").read_text())

    def test_forum_channel_posts_are_top_level(self):
        self.fake.channels["105"]["type"] = 15
        opener = msg("1", "2026-10-05T18:00:00", "new reel https://instagram.com/reel/1")
        self.fake.add_thread(opener["id"], "105", owner="1", messages=[
            opener,
            msg("2", "2026-10-05T18:10:00", "this edit is so clean"),
            msg("1", "2026-10-05T18:20:00", "thanks a lot friend"),
        ])
        r = self.by_id(self.run_score()[0])
        self.assertEqual(r["1"]["points"], 2)
        self.assertEqual(r["1"]["messages"], 0)
        self.assertEqual(r["2"]["messages"], 1)

    def test_deleted_messages_disappear_on_next_run(self):
        post = msg("1", "2026-10-05T18:00:00", "big win today")
        self.fake.channels["102"]["messages"].append(post)
        self.assertEqual(self.by_id(self.run_score()[0])["1"]["points"], 1)
        self.fake.channels["102"]["messages"].remove(post)
        self.fake.channels["101"]["messages"].append(msg("2", "2026-10-05T18:00:00", "keep it up all"))
        self.assertEqual(self.by_id(self.run_score()[0])["1"]["points"], 0)

    def test_missing_members_intent_explained(self):
        self.fake.members_status = 403
        with self.assertRaisesRegex(discord_api.DiscordError, "Server Members Intent"):
            self.run_score()

    def test_everyone_mode_counts_all_members_except_excluded(self):
        self.fake.channels["102"]["messages"].append(msg("3", "2026-10-05T18:00:00", "my first win"))
        board, _ = self.run_score(cfg={"student_role": "everyone"})
        self.assertEqual(set(self.by_id(board)), {"1", "2", "3"})
        self.assertEqual(self.by_id(board)["3"]["points"], 1)

    def test_everyone_mode_without_members_intent_uses_posters(self):
        self.fake.members_status = 403
        self.fake.channels["102"]["messages"] += [msg("3", "2026-10-05T18:00:00", "my first win"),
                                                  msg("90", "2026-10-05T18:00:00", "staff post")]
        board, _ = self.run_score(cfg={"student_role": "everyone"})
        self.assertEqual(set(self.by_id(board)), {"3"})
        self.assertEqual(self.by_id(board)["3"]["display_name"], "user3")

    def test_unknown_role_lists_real_roles(self):
        with self.assertRaisesRegex(SystemExit, "Roles on this server: Student"):
            self.run_score(cfg={"student_role": "Students2"})

    def test_placeholders_rejected(self):
        with self.assertRaisesRegex(SystemExit, "student_role is not set"):
            self.run_score(cfg={"student_role": "REPLACE_WITH_STUDENT_ROLE_NAME"})

    def test_empty_guard_keeps_previous_board(self):
        self.lb.parent.mkdir(parents=True)
        self.lb.write_text('{"sentinel": true}')
        with self.assertRaises(score.EmptyBoardError):
            self.run_score()
        self.assertIn("sentinel", self.lb.read_text())

    def test_page_cap_fails_instead_of_publishing_partial(self):
        self.fake.channels["101"]["messages"] += [msg("1", "2026-10-05T18:00:00", "hello there all")
                                                  for _ in range(150)]
        with self.assertRaisesRegex(discord_api.DiscordError, "max_pages_per_channel"):
            self.run_score(cfg={"max_pages_per_channel": 1})

    def test_frozen_after_challenge(self):
        board, _ = self.run_score(now=datetime(2026, 11, 3, 9, 0, tzinfo=PT))
        self.assertIsNone(board)

    def test_dry_run_writes_nothing(self):
        self.fake.channels["102"]["messages"].append(msg("1", "2026-10-05T18:00:00", "win"))
        self.run_score(dry_run=True)
        self.assertFalse(self.lb.exists())
        self.assertFalse(self.admin.exists())

    def test_post_admin_dms_csvs_when_no_admin_channel(self):
        self.fake.channels["102"]["messages"].append(msg("1", "2026-10-05T18:00:00", "win"))
        self.run_score(post_admin=True)
        path, ctype, body = self.fake.posts[0]
        self.assertEqual(path, "/channels/DM90/messages")
        self.assertIn("multipart/form-data", ctype)
        self.assertIn(b'filename="standings.csv"', body)
        self.assertIn(b"admin report", body)

    def test_rate_limit_retried(self):
        self.fake.channels["102"]["messages"].append(msg("1", "2026-10-05T18:00:00", "win"))
        real, calls = self.fake, {"n": 0}

        def flaky(*a):
            calls["n"] += 1
            if calls["n"] == 3:
                return 429, {}, b'{"retry_after": 0.01}'
            return real(*a)
        discord_api._transport = flaky
        self.assertEqual(self.by_id(self.run_score()[0])["1"]["points"], 1)


if __name__ == "__main__":
    unittest.main()
