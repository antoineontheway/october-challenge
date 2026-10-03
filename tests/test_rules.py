"""Scoring-rule tests: feed normalized messages straight into rules.compute."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import rules  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
CFG = {
    "challenge_name": "October Challenge",
    "community_name": "UGC Buildout",
    "challenge_start": "2026-10-02",
    "challenge_end": "2026-10-31",
    "timezone": "America/Los_Angeles",
    "staff_user_ids": ["99"],
}
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=PT)
JOINED = datetime(2026, 9, 1, tzinfo=timezone.utc)
STUDENTS = {
    sid: {"id": sid, "display_name": name, "avatar": None, "joined_at": JOINED}
    for sid, name in [("1", "Ana"), ("2", "Ben"), ("3", "Cam")]
}
ANTOINE = "99"  # staff: not a student, but replies to his posts count

_seq = 0


def m(author, channel, content="", day=5, hour=12, top_level=True, reply_to=None, thread_owner=None):
    global _seq
    _seq += 1
    ts = datetime(2026, 10, day, hour, 0, tzinfo=PT) + timedelta(seconds=_seq)
    return {
        "id": str(1_000_000 + _seq), "author": author, "channel": channel, "ts": ts,
        "day": ts.date().isoformat(), "content": content, "top_level": top_level,
        "reply_to": reply_to, "thread_owner": thread_owner, "jump": f"https://x/{_seq}",
    }


def score(messages, students=STUDENTS, now=NOW):
    board, admin = rules.compute(students, messages, CFG, now)
    return {r["id"]: r for r in board["members"]}, board, admin


class TextChecks(unittest.TestCase):
    def test_qualifies(self):
        yes = ["this is great", "great job everyone 🔥", "Love this edit, the hook is strong",
               "thanks so much", "<@123> that's really helpful advice"]
        no = ["gm", "gm gm gm", "good morning everyone", "lol lol lol", "hahaha lmao ok",
              "🔥🔥🔥", "🔥 great job 🔥", "<:pog:123> <:pog:123> <:pog:123>", "",
              "https://tenor.com/view/happy-gif-123", "check this https://instagram.com/p/x",
              "<@1> <@2> <@3>"]
        for t in yes:
            self.assertTrue(rules.qualifies(t), t)
        for t in no:
            self.assertFalse(rules.qualifies(t), t)

    def test_is_done(self):
        for t in ["Done", "done ✅", "DONE!!", "✅ Done 5 pitches", "**Done** today",
                  "<:check:1> done", "Done✅"]:
            self.assertTrue(rules.is_done(t), t)
        for t in ["Donezo", "I'm done", "almost done", "", "Not done yet"]:
            self.assertFalse(rules.is_done(t), t)

    def test_parse_activity(self):
        self.assertEqual(rules.parse_activity("Done ✅ 5 pitches, 3 applications, 2 videos submitted"),
                         {"pitches": 5, "applications": 3, "videos": 2})
        self.assertEqual(rules.parse_activity("done - pitches: 4, apps 6, 1 video"),
                         {"pitches": 4, "applications": 6, "videos": 1})
        self.assertEqual(rules.parse_activity("Done! 10 activities"), {})

    def test_classify_links(self):
        social, other = rules.classify_links(
            "new post https://www.instagram.com/reel/abc and https://vm.tiktok.com/xyz "
            "https://youtube.com/shorts/q (also https://mysite.com/a)")
        self.assertEqual(len(social), 3)
        self.assertEqual(other, ["https://mysite.com/a"])


class PrizeOne(unittest.TestCase):
    def test_daily_win_first_top_level_only(self):
        r, _, _ = score([m("1", "wins", "landed a brand deal"), m("1", "wins", "and another"),
                         m("1", "wins", "x", day=6)])
        self.assertEqual(r["1"]["points"], 2)
        self.assertEqual(r["1"]["win_days"], 2)

    def test_reply_or_thread_message_in_wins_is_not_a_daily_win(self):
        r, _, _ = score([m("1", "wins", "congrats!!", top_level=False, reply_to="2"),
                         m("1", "wins", "so proud", top_level=False, thread_owner="2")])
        self.assertEqual(r["1"]["points"], 0)

    def test_done_two_points_once_per_day(self):
        r, b, admin = score([m("1", "october_challenge", "Done ✅ 5 pitches, 3 applications, 2 videos"),
                             m("1", "october_challenge", "done again 9 pitches")])
        self.assertEqual(r["1"]["points"], 2)
        self.assertEqual(b["totals"]["pitches"], 5)
        self.assertEqual(admin["member_activity"]["1"], {"pitches": 5, "applications": 3, "videos": 2})

    def test_done_without_numbers_counts_and_later_numbers_are_kept(self):
        r, b, _ = score([m("1", "october_challenge", "done"), m("1", "october_challenge", "Done: 4 pitches")])
        self.assertEqual(r["1"]["points"], 2)
        self.assertEqual(b["totals"]["pitches"], 4)

    def test_not_done_scores_nothing(self):
        r, _, _ = score([m("1", "october_challenge", "working on it, 3 pitches so far")])
        self.assertEqual(r["1"]["points"], 0)

    def test_brand_post(self):
        r, _, admin = score([
            m("1", "socials", "new reel https://instagram.com/reel/1 https://tiktok.com/@a/video/2"),
            m("1", "socials", "another https://tiktok.com/@a/video/3"),
        ])
        self.assertEqual(r["1"]["points"], 2)
        self.assertEqual(len(admin["links"]), 1)
        self.assertIn("instagram.com", admin["links"][0]["links"])

    def test_brand_needs_social_link_top_level(self):
        r, _, admin = score([
            m("1", "socials", "my site https://ana.design"),
            m("1", "socials", "https://instagram.com/x", top_level=False, reply_to="2"),
        ])
        self.assertEqual(r["1"]["points"], 0)
        self.assertEqual(len(admin["unrecognized_links"]), 1)

    def test_max_five_per_day_and_150_total(self):
        msgs = []
        for d in range(2, 32):
            msgs += [m("1", "wins", "w", day=d), m("1", "wins", "w2", day=d),
                     m("1", "october_challenge", "Done", day=d), m("1", "october_challenge", "done", day=d),
                     m("1", "socials", "https://instagram.com/p", day=d),
                     m("1", "socials", "https://tiktok.com/p", day=d)]
        r, _, _ = score(msgs, now=datetime(2026, 11, 1, 1, 0, tzinfo=PT))
        self.assertEqual(r["1"]["points"], 150)
        self.assertTrue(all(sum(d.values()) == 5 for d in r["1"]["daily"].values()))

    def test_pacific_day_window(self):
        r, _, _ = score([m("1", "wins", "too early", day=1, hour=23), m("2", "wins", "ok", day=2, hour=0)])
        self.assertEqual(r["1"]["points"], 0)
        self.assertEqual(r["2"]["points"], 1)

    def test_mid_month_joiner_starts_from_join_time(self):
        students = dict(STUDENTS, **{"4": {"id": "4", "display_name": "Dee", "avatar": None,
                                           "joined_at": datetime(2026, 10, 10, tzinfo=PT)}})
        r, _, _ = score([m("4", "wins", "pre-join", day=5), m("4", "wins", "hi all", day=12)], students)
        self.assertEqual(r["4"]["points"], 1)

    def test_non_students_ignored(self):
        r, b, _ = score([m(ANTOINE, "wins", "w"), m(ANTOINE, "general", "this is great everyone")])
        self.assertNotIn(ANTOINE, r)
        self.assertEqual(b["totals"]["points"], 0)


class PrizeTwo(unittest.TestCase):
    def test_open_channels_count_qualifying_messages(self):
        r, _, _ = score([m("1", "general", "this is great"), m("1", "content_review", "love the hook here"),
                         m("1", "ugc_opportunities", "applied to this one"), m("1", "general", "gm")])
        self.assertEqual(r["1"]["messages"], 3)

    def test_october_challenge_never_counts(self):
        r, _, _ = score([m("1", "october_challenge", "Done with all my tasks today")])
        self.assertEqual(r["1"]["messages"], 0)

    def test_wins_and_socials_only_replies_to_other_students(self):
        r, _, _ = score([
            m("1", "wins", "signed my first brand deal today"),                            # own win
            m("1", "socials", "my new reel https://instagram.com/reel/1"),                  # own link
            m("1", "wins", "congrats that is huge", top_level=False, reply_to="2"),         # yes
            m("1", "socials", "love this edit so much", top_level=False, thread_owner="2"), # yes
            m("1", "wins", "thanks so much everyone", top_level=False, reply_to="1"),       # own post
            m("1", "wins", "thank you all so much", top_level=False, thread_owner="1"),     # own thread
            m("1", "wins", "congrats on this one", top_level=False, reply_to=ANTOINE),     # yes, staff
            m("1", "wins", "great point from you", top_level=False, reply_to="77"),        # outsider
            m("1", "wins", "congrats", top_level=False, reply_to="2"),                     # too short
            m("1", "wins", "another top level message here"),                              # top-level
        ])
        self.assertEqual(r["1"]["messages"], 3)


class Prizes(unittest.TestCase):
    def test_prize_one_winner_cannot_take_prize_two(self):
        msgs = [m("1", "wins", "w"), m("1", "october_challenge", "done")]
        msgs += [m("1", "general", "this is great") for _ in range(5)]
        msgs += [m("2", "general", "this is great") for _ in range(3)]
        msgs += [m("3", "general", "this is great") for _ in range(1)]
        _, b, _ = score(msgs)
        st = b["standings"]
        self.assertEqual(st["most_consistent"]["leaders"], ["1"])
        self.assertEqual(st["most_active"]["leaders"], ["2"])
        self.assertEqual(st["most_active"]["ineligible"], ["1"])

    def test_ties_are_shared_and_flagged(self):
        _, b, _ = score([m("1", "wins", "w"), m("2", "wins", "w"),
                         m("3", "general", "this is great"), m("3", "general", "this is great")])
        st = b["standings"]
        self.assertEqual(sorted(st["most_consistent"]["leaders"]), ["1", "2"])
        self.assertTrue(st["most_consistent"]["tie"])
        self.assertEqual(st["most_active"]["leaders"], ["3"])
        self.assertFalse(st["most_active"]["tie"])

    def test_nobody_leads_with_zero(self):
        _, b, _ = score([])
        self.assertEqual(b["standings"]["most_consistent"]["leaders"], [])
        self.assertEqual(b["standings"]["most_active"]["leaders"], [])
        self.assertEqual(b["totals"]["students"], 3)


if __name__ == "__main__":
    unittest.main()
