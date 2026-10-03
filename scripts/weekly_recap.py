"""Post a public leaderboard recap (top 5 of each prize) to a community channel.

Needs the bot to have Send Messages in that channel.

Usage:
  DISCORD_BOT_TOKEN=... RECAP_CHANNEL_ID=... [LEADERBOARD_URL=...] python scripts/weekly_recap.py
  python scripts/weekly_recap.py --preview   # print the message, post nothing
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discord_api  # noqa: E402

LEADERBOARD_PATH = Path(__file__).resolve().parent.parent / "site" / "leaderboard.json"
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def ranked(members, key, n=5):
    """Top n by `key`; ties share a rank, matching the web page."""
    lines, rank, prev = [], 0, None
    for i, m in enumerate(sorted(members, key=lambda m: -m[key])[:n], 1):
        if m[key] != prev:
            rank, prev = i, m[key]
        lines.append(f"{MEDALS.get(rank, f'`{rank}.`')} **{m['display_name']}**: {m[key]}")
    return lines


def format_recap(board, url=None):
    t = board["totals"]
    lines = [f"**{board['challenge_name']} leaderboard**", "",
             "🏆 **Most Consistent** (points)", *ranked(board["members"], "points"), "",
             "💬 **Most Active** (messages)", *ranked(board["members"], "messages"), "",
             f"Together we've sent **{t['pitches']} pitches**, **{t['applications']} applications** "
             f"and **{t['videos']} videos** so far."]
    if url:
        lines.append(f"Full board: <{url}>")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()

    board = json.loads(LEADERBOARD_PATH.read_text())
    if board.get("sample"):
        sys.exit("leaderboard.json is sample data; run score.py first.")
    content = format_recap(board, os.environ.get("LEADERBOARD_URL"))
    if args.preview:
        print(content)
        return
    channel_id = os.environ.get("RECAP_CHANNEL_ID")
    if not channel_id:
        sys.exit("RECAP_CHANNEL_ID is not set")
    discord_api.post_message(channel_id, content)
    print("Recap posted.")


if __name__ == "__main__":
    main()
