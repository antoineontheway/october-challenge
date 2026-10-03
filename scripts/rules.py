"""October Challenge scoring rules. Pure functions: no network, easy to test.

Prize 1, Most Consistent (max 5 pts/day):
  #wins               first top-level message of the day       1 pt
  #october-challenge  a message starting with "Done"           2 pts
  #share-your-socials own top-level post with a social link    2 pts
Prize 2, Most Active: qualifying messages (3+ real words, not filler) in
  #general, #content-review, #ugc-opportunities  any message
  #wins, #share-your-socials                     only replies to / threads of OTHER students' (or staff) posts
  #october-challenge                             never
"""
import re
from collections import Counter
from datetime import timezone
from urllib.parse import urlparse

POINTS = {"win": 1, "done": 2, "brand": 2}
OPEN_CHANNELS = {"general", "content_review", "ugc_opportunities"}
REPLY_ONLY_CHANNELS = {"wins", "socials"}

CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
MENTION = re.compile(r"<(?:@[!&]?|#)\d+>")
URL = re.compile(r"https?://[^\s<>]+", re.I)
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")  # letters/digits only, so emoji never count

FILLER = set("""
gm gn gmgm gngn lol lmao lmfao rofl haha hahaha hehe xd ok okay okk k kk ty tysm thx thanks
yes yep yup yeah ya no nope nice same wow omg fr frfr bro sis fam yall y'all guys everyone
all hi hey hello yo sup good morning night afternoon evening congrats congratulations w l
facts true real bet lit fire dope slay period ikr idk tbh smh
""".split())
LAUGH = re.compile(r"^(?:(?:ha|he|ah)+h?|l+o+l+|lm+f?a+o+|x+d+)$")

SOCIAL_DOMAINS = (
    "instagram.com", "instagr.am", "tiktok.com", "youtube.com", "youtu.be", "x.com",
    "twitter.com", "facebook.com", "fb.com", "fb.watch", "threads.net", "threads.com",
    "linkedin.com", "snapchat.com", "pinterest.com", "pin.it", "lemon8-app.com",
    "bsky.app", "twitch.tv",
)

ACTIVITY_WORDS = {
    "pitches": re.compile(r"^pitch(?:es|ed|ing)?$"),
    "applications": re.compile(r"^(?:apps?|applications?|applied|apply|applies)$"),
    "videos": re.compile(r"^(?:videos?|vids?)$"),
}
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
# Separators between items: "3 videos, 5 applications + 2 pitches" -> three segments.
SEGMENT_SPLIT = re.compile(r"[,;+|/•\n]|\band\b|&", re.I)


# ---------------------------------------------------------------- text checks

def words(content):
    text = URL.sub(" ", MENTION.sub(" ", CUSTOM_EMOJI.sub(" ", content or "")))
    return WORD.findall(text)


def is_filler(word):
    w = word.lower().replace("’", "'")
    return w in FILLER or bool(LAUGH.match(w))


def qualifies(content):
    """Prize 2 test: 3+ words (emoji, links and @mentions don't count), not all filler."""
    ws = words(content)
    return len(ws) >= 3 and not all(is_filler(w) for w in ws)


def is_done(content):
    """Starts with 'Done' in any capitalization; leading emoji/markdown allowed."""
    text = CUSTOM_EMOJI.sub("", content or "")
    text = re.sub(r"^[\W_]+", "", text)
    return bool(re.match(r"done\b", text, re.I))


def parse_activity(content):
    """'Done 3 trybe videos, 5 applications + 2 pitches' -> {'videos': 3, 'applications': 5, 'pitches': 2}.

    Each item word takes the nearest unused number in its segment, allowing a couple of words in
    between ("1 brand video", "Pitches sent: 10"). Missing keys are omitted.
    """
    text = (content or "").replace("\ufe0f", "").replace("\u20e3", "")  # 5️⃣ -> 5
    found = {}
    for segment in SEGMENT_SPLIT.split(text):
        tokens = re.findall(r"\d+|[^\W\d_]+", segment.lower())
        nums = {i: int(t) if t.isdigit() else NUMBER_WORDS[t]
                for i, t in enumerate(tokens) if t.isdigit() or t in NUMBER_WORDS}
        used = set()
        for i, tok in enumerate(tokens):
            key = next((k for k, rx in ACTIVITY_WORDS.items() if rx.match(tok)), None)
            if key is None or key in found:
                continue
            # Closest first; "5 pitches" (before) wins over "pitches 5" (after) at equal distance.
            for j in (i - 1, i + 1, i - 2, i + 2, i - 3):
                if j in nums and j not in used:
                    found[key] = nums[j]
                    used.add(j)
                    break
    return found


def classify_links(content):
    """Return (social_links, other_links) found in a message."""
    social, other = [], []
    for url in URL.findall(content or ""):
        url = url.rstrip(").,!>")
        host = (urlparse(url).hostname or "").lower()
        (social if any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS) else other).append(url)
    return social, other


# ---------------------------------------------------------------- scoring

def compute(students, messages, cfg, now):
    """Score everything from scratch.

    students: {user_id: {"id", "display_name", "avatar", "joined_at" (aware datetime)}}
    messages: normalized dicts with keys
        id, author, channel (config key), ts (aware datetime), day ('YYYY-MM-DD'),
        content, top_level (bool), reply_to (author id or None),
        thread_owner (author id of the thread's starter post, or None), jump (url)
    Returns (public_board, admin_details).
    """
    start, end = cfg["challenge_start"], cfg["challenge_end"]
    staff = set(cfg.get("staff_user_ids", []))  # replies to their posts count like replies to students
    state = {sid: {"daily": {}, "messages": 0, "activity": Counter(), "activity_days": set()}
             for sid in students}
    links, activity_rows, unrecognized, done_posts = [], [], [], []

    for m in sorted(messages, key=lambda m: int(m["id"])):
        sid, ch, day = m["author"], m["channel"], m["day"]
        s = state.get(sid)
        if s is None or not (start <= day <= end) or m["ts"] < students[sid]["joined_at"]:
            continue
        day_pts = s["daily"].get(day, {})
        name = students[sid]["display_name"]

        # Prize 1
        if ch == "wins" and m["top_level"] and "win" not in day_pts:
            day_pts["win"] = POINTS["win"]
        elif ch == "october_challenge" and is_done(m["content"]):
            day_pts.setdefault("done", POINTS["done"])
            nums = parse_activity(m["content"])
            counted = bool(nums) and day not in s["activity_days"]  # first post of the day with numbers
            if counted:
                s["activity_days"].add(day)
                s["activity"].update(nums)
                activity_rows.append({"student": name, "user_id": sid, "day": day, **nums, "link": m["jump"]})
            done_posts.append({"day": day, "student": name, "user_id": sid, **nums,
                               "numbers_counted": "yes" if counted else ("no numbers found" if not nums
                                                                          else "no, already counted today"),
                               "text": m["content"][:500], "link": m["jump"]})
        elif ch == "socials" and m["top_level"]:
            social, other = classify_links(m["content"])
            if social and "brand" not in day_pts:
                day_pts["brand"] = POINTS["brand"]
                links.append({"student": name, "user_id": sid, "day": day,
                              "links": " ".join(social), "message": m["jump"]})
            elif other and not social:
                unrecognized.append({"student": name, "user_id": sid, "day": day,
                                     "links": " ".join(other), "message": m["jump"]})
        if day_pts:
            s["daily"][day] = day_pts

        # Prize 2
        if ch in OPEN_CHANNELS:
            counts = qualifies(m["content"])
        elif ch in REPLY_ONLY_CHANNELS:
            to_other = lambda uid: uid is not None and uid != sid and (uid in students or uid in staff)  # noqa: E731
            counts = (to_other(m["reply_to"]) or to_other(m["thread_owner"])) and qualifies(m["content"])
        else:
            counts = False
        if counts:
            s["messages"] += 1

    # Manual rulings: points Antoine reviewed and removed (e.g. a UGC video posted as a brand post).
    for o in cfg.get("removed_points", []):
        day_pts = state.get(o["user_id"], {}).get("daily", {}).get(o["day"])
        if day_pts and day_pts.pop(o["category"], None) is not None:
            for row in links:
                if o["category"] == "brand" and row["user_id"] == o["user_id"] and row["day"] == o["day"]:
                    row["removed"] = o.get("note", "removed by admin")
            if not day_pts:
                del state[o["user_id"]]["daily"][o["day"]]

    rows = []
    for sid, s in state.items():
        daily = dict(sorted(s["daily"].items()))
        rows.append({
            "id": sid,
            "display_name": students[sid]["display_name"],
            "avatar": students[sid].get("avatar"),
            "points": sum(sum(d.values()) for d in daily.values()),
            "win_days": sum("win" in d for d in daily.values()),
            "done_days": sum("done" in d for d in daily.values()),
            "brand_days": sum("brand" in d for d in daily.values()),
            "messages": s["messages"],
            "daily": daily,
        })
    rows.sort(key=lambda r: (-r["points"], -r["messages"], r["display_name"].lower()))

    standings = decide_prizes(rows)
    totals = Counter()
    for s in state.values():
        totals.update(s["activity"])
    board = {
        "generated_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "challenge_name": cfg["challenge_name"],
        "community_name": cfg.get("community_name", ""),
        "challenge_start": start,
        "challenge_end": end,
        "timezone": cfg["timezone"],
        "final": now.date().isoformat() > end,
        "max_points_per_day": sum(POINTS.values()),
        "totals": {
            "students": len(rows),
            "points": sum(r["points"] for r in rows),
            "messages": sum(r["messages"] for r in rows),
            "pitches": totals["pitches"],
            "applications": totals["applications"],
            "videos": totals["videos"],
        },
        "standings": standings,
        "members": rows,
    }
    admin = {
        "links": links,
        "unrecognized_links": unrecognized,
        "activity": activity_rows,
        "done_posts": done_posts,
        "member_activity": {sid: dict(s["activity"]) for sid, s in state.items()},
    }
    return board, admin


def decide_prizes(rows):
    """Prize 1 first; its winner(s) can't also take Prize 2. Ties share (and get flagged)."""
    top1 = max((r["points"] for r in rows), default=0)
    p1 = [r["id"] for r in rows if top1 > 0 and r["points"] == top1]
    eligible = [r for r in rows if r["id"] not in p1]
    top2 = max((r["messages"] for r in eligible), default=0)
    p2 = [r["id"] for r in eligible if top2 > 0 and r["messages"] == top2]
    return {
        "most_consistent": {"leaders": p1, "score": top1, "tie": len(p1) > 1},
        "most_active": {"leaders": p2, "score": top2, "tie": len(p2) > 1, "ineligible": p1},
    }
