# UGC Buildout October Challenge Leaderboard

Tracks the 30-day October Challenge (Oct 2 to 31, 2026, Pacific time) from Discord and
publishes a leaderboard every morning. It covers two prizes, and each student can win only one.

| Prize | How it's decided |
|---|---|
| **Most Consistent** | Daily points, max 5/day (150 total): daily win in #wins +1, "Done" post in #october-challenge +2, own social link in #share-your-socials +2 |
| **Most Active** | Qualifying messages (3+ words excluding emoji, not filler) in #general, #content-review, #ugc-opportunities, plus replies/threads on *other students'* (or Antoine's) posts in #wins and #share-your-socials. #october-challenge never counts. |

Prize 1 is decided first. If its winner also leads Prize 2, Prize 2 goes to the next student. Ties split the prize and are flagged in the admin report.

## How it works
```
Discord (all 6 channels + their threads)
   └─ scripts/score.py   full rescan of Oct 2–31 every run (deleted messages drop out)
        ├─ scripts/rules.py        all scoring rules (pure functions, unit-tested)
        ├─ site/leaderboard.json   public data → site/index.html on GitHub Pages
        └─ admin/                  private report + CSVs (never published)
```
- **Daily** (`update.yml`, 08:00 UTC = 1am Pacific): rescan, rebuild, publish.
- **Admin report** (`admin-report.yml`, Mondays plus final results on Nov 1): DMs Antoine (`admin_user_id`) the standings,
  tie flags and `standings.csv`, `links.csv` (brand posts for spot checks), `activity.csv` (pitch/application/video
  numbers).
- **After Oct 31** the board freezes. Runs from Nov 2 on leave the final board alone (`--force` overrides).

## Setup checklist
1. **Developer Portal → Bot**: turn on **Message Content Intent** *and* **Server Members Intent**.
2. Bot permissions in the server: View Channels + Read Message History on all 6 channels and their threads.
   For the admin report DM, allow DMs from server members (Server → Privacy Settings → Direct Messages).
3. `config.json`: `student_role` is `"everyone"` (every member is a student). To limit it to one role later,
   put that role's name there instead. Excluded: Antoine and Tbo. Staff (replies count): Antoine.
4. Push to GitHub, then add secret `DISCORD_BOT_TOKEN`.
5. **Settings → Pages → Source: GitHub Actions**, then **Actions → Update leaderboard → Run workflow**.

## Commands
```bash
python3 -m unittest discover tests                       # run all tests
DISCORD_BOT_TOKEN=... python3 scripts/score.py --dry-run  # live check, writes nothing
python3 scripts/make_sample.py                           # fake data for previewing the page
python3 -m http.server -d site 8000                      # preview at http://localhost:8000
python3 scripts/weekly_recap.py --preview                # public top-5 recap message
```

## Rule details and edge cases
- **Day** = midnight to 11:59pm America/Los_Angeles, by the message's post time.
- **Students** = every server member (or members with `student_role`, if set to a role name), minus excluded IDs and bots.
  Without Server Members Intent, "everyone" mode falls back to everyone who posted in a challenge channel.
  Mid-month joiners only score from their join time. Members who leave or lose the role drop off the board.
- **Daily win** = first top-level (not a reply, not in a thread) message in #wins that day.
- **Done** = message starts with "done" in any capitalization, ignoring leading emoji or formatting ("✅ Done", "**Done**").
  "Donezo" and "I'm done" don't count. Numbers like "5 pitches, 3 applications, 2 videos" (or "pitches: 5")
  are stored from the first Done post of the day that has them.
- **Brand post** = own top-level message in #share-your-socials linking to Instagram, TikTok, YouTube, X/Twitter,
  Facebook, Threads, LinkedIn, Snapchat, Pinterest, Lemon8, Bluesky or Twitch. Posts with other links get
  no points and appear in `unrecognized_links.csv` for review.
- **Qualifying message** = 3+ words. Emoji, links and @mentions aren't words. Messages made only of
  filler ("gm", "good morning everyone", "lol lol lol") don't count. Neither do stickers or GIFs.
- **"Other students' posts"** = the replied-to message, or the post the thread hangs off, was written by a different
  student or by someone in `staff_user_ids` (Antoine). Replies to Tbo or other non-students don't count.
- Edited messages are judged by their current text. Deleted messages disappear on the next run.
- Forum-style channels work too: each forum post counts as top-level, and replies inside it as thread messages.
- Private threads are not scanned.
