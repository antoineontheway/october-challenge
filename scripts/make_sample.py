"""Write a fake leaderboard.json so the page can be previewed without a bot.

Builds random messages and runs them through the real scoring rules. The output is
tagged "sample": true; the real daily run always overwrites it.

  python scripts/make_sample.py            # -> site/leaderboard.json
  python scripts/make_sample.py out.json   # -> custom path
"""
import json
import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import compute  # noqa: E402
from score import CONFIG_PATH, LEADERBOARD_PATH, write_json  # noqa: E402

NAMES = """Enya Marcus Priya Jordan Sofia Kenji Amara Lucas Zoe Mateo Nia Ethan Leila Omar
Hana Diego Ivy Noah Chloe Ravi Maya Felix Aisha Leo Tara Sam Yuki Ben Grace Malik
Elena Theo Rosa Kai Lena Andre Mila Jonah""".split()
CHAT = ["this hook is really strong", "anyone tried pitching skincare brands",
        "great question, I usually follow up after a week", "love the lighting on this one"]


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else LEADERBOARD_PATH
    tz = ZoneInfo(cfg["timezone"])
    rng = random.Random(11)
    # Pretend we're on day 9 so the page shows a challenge in progress.
    start = date.today() - timedelta(days=8)
    cfg = dict(cfg, challenge_start=start.isoformat(), challenge_end=(start + timedelta(days=29)).isoformat())
    now = datetime.now(tz)

    students = {str(1000 + i): {"id": str(1000 + i), "display_name": n, "avatar": None,
                                "joined_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
                for i, n in enumerate(NAMES)}
    messages, seq = [], 0

    def add(sid, ch, content, d, **kw):
        nonlocal seq
        seq += 1
        ts = datetime.combine(start + timedelta(days=d), time(9 + seq % 12), tz)
        messages.append({"id": str(seq), "author": sid, "channel": ch, "ts": ts,
                         "day": ts.date().isoformat(), "content": content, "top_level": True,
                         "reply_to": None, "thread_owner": None, "jump": "", **kw})

    ids = list(students)
    for i, sid in enumerate(ids):
        effort = 0.97 if i == 0 else rng.betavariate(1.4, 1.8)
        chatty = rng.betavariate(1.2, 3)
        for d in range(9):
            if rng.random() < effort:
                add(sid, "october_challenge",
                    f"Done ✅ {rng.randint(2, 8)} pitches, {rng.randint(0, 5)} applications, "
                    f"{rng.randint(0, 3)} videos", d)
            if rng.random() < effort * 0.9:
                add(sid, "wins", "got a reply from a brand today", d)
            if rng.random() < effort * 0.7:
                add(sid, "socials", "new post https://instagram.com/reel/x", d)
            for _ in range(int(chatty * 6)):
                add(sid, rng.choice(["general", "content_review", "ugc_opportunities"]), rng.choice(CHAT), d)
            if rng.random() < chatty:
                add(sid, "wins", "congrats this is amazing", d, top_level=False, reply_to=rng.choice(ids))

    board, _ = compute(students, messages, cfg, now)
    write_json(out, {**board, "sample": True})
    print(f"Wrote {len(students)} sample students to {out}")


if __name__ == "__main__":
    main()
