"""Private admin report: prize standings, tie flags, and spot-check CSVs.

Never published to the website. Written to admin/ locally, and optionally posted
(with the CSVs attached) to a private Discord channel only staff can see.
"""
import csv
import io
from pathlib import Path

import discord_api


def _csv(rows, fields):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def names(board, ids):
    by_id = {m["id"]: m["display_name"] for m in board["members"]}
    return " & ".join(by_id[i] for i in ids) or "nobody yet"


def summary(board, admin):
    st, t = board["standings"], board["totals"]
    p1, p2 = st["most_consistent"], st["most_active"]
    status = "FINAL RESULTS" if board["final"] else "standings so far"
    lines = [f"**{board['challenge_name']} admin report ({status})**", ""]

    lines.append(f"🏆 **Most Consistent:** {names(board, p1['leaders'])}, {p1['score']} pts")
    if p1["tie"]:
        lines.append(f"⚠️ **TIE for Most Consistent** between {len(p1['leaders'])} students. Prize splits.")
    lines.append(f"💬 **Most Active:** {names(board, p2['leaders'])}, {p2['score']} messages")
    if p2["tie"]:
        lines.append(f"⚠️ **TIE for Most Active** between {len(p2['leaders'])} students. Prize splits.")
    if p2["ineligible"]:
        by_msgs = sorted(board["members"], key=lambda m: -m["messages"])
        if by_msgs and by_msgs[0]["id"] in p2["ineligible"]:
            lines.append(f"↪️ {by_msgs[0]['display_name']} has the most messages ({by_msgs[0]['messages']}) "
                         "but holds Most Consistent, so Most Active goes to the next student.")

    lines += ["", "**Top 5 points**"]
    lines += [f"{i}. {m['display_name']}: {m['points']} pts "
              f"(wins {m['win_days']}, done {m['done_days']}, brand {m['brand_days']})"
              for i, m in enumerate(board["members"][:5], 1)]
    lines += ["", "**Top 5 messages**"]
    lines += [f"{i}. {m['display_name']}: {m['messages']}"
              for i, m in enumerate(sorted(board["members"], key=lambda m: -m["messages"])[:5], 1)]
    lines += ["", f"Community: {t['pitches']} pitches, {t['applications']} applications, {t['videos']} videos "
                  f"from {t['students']} students."]
    lines.append(f"Spot checks: {len(admin['links'])} brand posts in links.csv"
                 + (f", plus {len(admin['unrecognized_links'])} posts with links that weren't recognized as "
                    "social (no points given; check unrecognized_links.csv)" if admin["unrecognized_links"] else "")
                 + ".")
    text = "\n".join(lines)
    return text if len(text) <= 1990 else text[:1980] + "\n…"


def files(board, admin):
    standings = [{
        "rank_points": i, "student": m["display_name"], "user_id": m["id"], "points": m["points"],
        "win_days": m["win_days"], "done_days": m["done_days"], "brand_days": m["brand_days"],
        "messages": m["messages"], **admin["member_activity"].get(m["id"], {}),
    } for i, m in enumerate(board["members"], 1)]
    out = [
        ("standings.csv", _csv(standings, ["rank_points", "student", "user_id", "points", "win_days",
                                           "done_days", "brand_days", "messages", "pitches",
                                           "applications", "videos"])),
        ("links.csv", _csv(admin["links"], ["day", "student", "user_id", "links", "message"])),
        ("activity.csv", _csv(admin["activity"], ["day", "student", "user_id", "pitches",
                                                  "applications", "videos", "link"])),
    ]
    if admin["unrecognized_links"]:
        out.append(("unrecognized_links.csv",
                    _csv(admin["unrecognized_links"], ["day", "student", "user_id", "links", "message"])))
    return out


def write_files(board, admin, admin_dir):
    admin_dir = Path(admin_dir)
    admin_dir.mkdir(parents=True, exist_ok=True)
    (admin_dir / "report.md").write_text(summary(board, admin) + "\n")
    for name, data in files(board, admin):
        (admin_dir / name).write_bytes(data)


def post(board, admin, channel_id):
    discord_api.post_message(channel_id, summary(board, admin), files=files(board, admin))
