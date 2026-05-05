#!/usr/bin/env python3
"""
generate-brief.py - AI Collective Daily Brief Generator

Usage:
  python generate-brief.py --name "Ben Cope" --email "bcope1@gmail.com"
  python generate-brief.py --name "Jon Benson" --email "jb@bnsn.ai"

Scans source YouTube channels for last 48h videos, pulls transcripts,
synthesizes via Claude, generates the brief HTML, and saves to m/tldr/{email}/.
"""

import argparse
import subprocess
import json
import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from youtube_transcript_api import YouTubeTranscriptApi
import anthropic

REPO_ROOT = Path(__file__).parent

# Source channels: (handle, display_name, quality_tier)
# tier 1 = priority, tier 2 = secondary
CHANNELS = [
    ("@BrockMesarich", "Brock Mesarich", 1),
    ("@nateherk", "Nate Herk", 1),
    ("@jonocatliff", "Jono Catliff", 1),
    ("@Itssssss_Jack", "Jack Roberts", 1),
    ("@BenAI92", "Ben AI", 1),
    ("@princeeliot", "Eliot Prince", 1),
    ("@AnthropicAI", "Anthropic", 1),
    ("@DavidOndrej", "David Ondrej", 2),
    ("@AIExplained-tv", "AI Explained", 2),
    ("@matthew_berman", "Matthew Berman", 2),
]

LOOKBACK_DAYS = 2
MAX_TRANSCRIPT_CHARS = 12000
MAX_CHANNELS_WITH_CONTENT = 8


def get_recent_videos(handle: str, channel_name: str, lookback_days: int = LOOKBACK_DAYS) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                f"https://www.youtube.com/{handle}",
                "--playlist-end", "5",
                "--print", "%(upload_date)s|%(id)s|%(title)s|%(duration)s",
                "--no-download",
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            upload_date, vid_id, title, duration_str = parts
            if not upload_date or not vid_id:
                continue
            duration_sec = int(duration_str) if duration_str.strip().isdigit() else 0
            # Skip Shorts (under 90 seconds)
            if duration_sec > 0 and duration_sec < 90:
                continue
            if upload_date >= cutoff:
                videos.append({
                    "id": vid_id,
                    "title": title,
                    "channel": channel_name,
                    "handle": handle,
                    "upload_date": upload_date,
                    "duration_sec": duration_sec,
                    "duration_min": round(duration_sec / 60),
                })
        return videos
    except Exception as e:
        print(f"  SKIP {handle}: {e}", file=sys.stderr)
        return []


def fetch_description(video: dict) -> str:
    """Fallback: get video description via yt-dlp when transcripts are IP-blocked."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--print", "%(description).6000s", "--no-download",
             f"https://www.youtube.com/watch?v={video['id']}"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip()[:6000]
    except Exception:
        return ""


def fetch_transcript(video: dict) -> dict | None:
    # Try transcript API first
    try:
        api = YouTubeTranscriptApi()
        t = api.fetch(video["id"])
        text = " ".join([s.text for s in t])[:MAX_TRANSCRIPT_CHARS]
        return {**video, "transcript": text, "source": "transcript"}
    except Exception as transcript_err:
        err_name = type(transcript_err).__name__
        err_str = str(transcript_err)
        is_ip_blocked = ("IpBlocked" in err_name or "TooManyRequests" in err_name
                         or "429" in err_str or "blocked" in err_str.lower())
        if is_ip_blocked:
            # Fall back to description via yt-dlp
            desc = fetch_description(video)
            if desc:
                return {**video, "transcript": f"[Description only]\n\n{desc}", "source": "description"}
        return None


def scan_channels() -> list[dict]:
    print("Scanning channels for recent videos...")
    all_videos = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(get_recent_videos, h, n, LOOKBACK_DAYS): (h, n) for h, n, _ in CHANNELS}
        for fut in as_completed(futures):
            vids = fut.result()
            all_videos.extend(vids)

    # De-dupe by video ID, sort by date desc
    seen = set()
    unique = []
    for v in sorted(all_videos, key=lambda x: x["upload_date"], reverse=True):
        if v["id"] not in seen:
            seen.add(v["id"])
            unique.append(v)

    print(f"  Found {len(unique)} unique videos from last {LOOKBACK_DAYS} days")
    return unique


def fetch_transcripts(videos: list[dict]) -> list[dict]:
    print("Fetching transcripts (with description fallback)...")
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fetch_transcript, v): v for v in videos}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                results.append(result)
                src = result.get("source", "transcript")
                print(f"  {src[:4].upper()}  {result['channel']}: {result['title'][:55]}")
            else:
                v = futures[fut]
                print(f"  SKIP {v['channel']}: {v['title'][:55]}")

    return sorted(results, key=lambda x: x["upload_date"], reverse=True)


def synthesize_brief(videos: list[dict], member_name: str, member_context: str) -> dict:
    first_name = member_name.split()[0]
    today = datetime.now()
    date_str = today.strftime("%A, %B %-d, %Y")

    print(f"Synthesizing brief for {first_name} with {len(videos)} sources...")

    client = anthropic.Anthropic()

    source_blocks = []
    for v in videos[:MAX_CHANNELS_WITH_CONTENT]:
        source_blocks.append(
            f"### [{v['channel']}] {v['title']}\n"
            f"Video ID: {v['id']}\n"
            f"Duration: {v['duration_min']} min\n"
            f"Transcript: {v['transcript']}\n"
        )
    sources_text = "\n\n---\n\n".join(source_blocks)

    member_info = f"Name: {member_name}\nContext: {member_context}"

    prompt = f"""You are writing the AI Collective Daily Brief for {first_name}.

Today: {date_str}

Member profile:
{member_info}

Source videos from today's AI creator channels:

{sources_text}

Generate a JSON brief with this structure. Be specific and concrete - name exact features, real use cases, specific numbers. Personalize the "for_you" lines to {first_name}'s context. No em dashes (use plain dash or colon). No vague statements.

{{
  "total_scanned": <total videos reviewed>,
  "focus_count": <2-4, items worth acting on>,
  "one_thing": {{
    "channel": "<creator name>",
    "video_id": "<youtube id>",
    "duration_min": <minutes>,
    "headline": "<compelling action headline - what to BUILD or DO today>",
    "subtitle": "<what it unlocks in one concrete line>",
    "body": "<3-4 sentences. What it does. Why it matters to {first_name} specifically. What to do with it today.>",
    "for_you": "<one sentence, specific to {first_name}'s role and tools>"
  }},
  "hot_items": [
    {{
      "channel": "<creator name>",
      "video_id": "<id>",
      "duration_min": <minutes>,
      "for_you": "<one sentence why this matters to {first_name}>",
      "use_case": "<one sentence: specific thing they can do with it>",
      "headline": "<article-style headline>",
      "what": "<2-3 sentence explanation of what it actually is>",
      "tldr": [
        "<Bold term: specific point>",
        "<Bold term: specific point>",
        "<Bold term: specific point>",
        "<Bold term: specific point>"
      ],
      "verify_note": "<source + upload date>"
    }}
  ],
  "implement_items": [
    {{
      "channel": "<creator name>",
      "video_id": "<id>",
      "duration_min": <minutes>,
      "for_you": "<why this one matters>",
      "use_case": "<specific use case>",
      "headline": "<headline>",
      "what": "<2 sentence summary>",
      "tldr": [
        "<Bold term: specific point>",
        "<Bold term: specific point>",
        "<Bold term: specific point>"
      ]
    }}
  ],
  "save_items": [
    {{
      "channel": "<creator name>",
      "video_id": "<id>",
      "headline": "<title>",
      "why_later": "<one sentence why not today>"
    }}
  ],
  "skipped_count": <number>,
  "skipped_note": "<brief note on what was skipped>",
  "read_time_min": <estimated read time>
}}

Rules:
- hot_items: 1-2 max (the freshest, most urgent)
- implement_items: 2-3 items
- save_items: 1-2 items
- tldr bullets must start with **Bold phrase** colon then detail
- be specific about tools, features, and outcomes
- personalize every "for_you" line to {first_name}'s actual role"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    start = text.find("{")
    end = text.rfind("}") + 1
    return json.loads(text[start:end])


def build_html(member_name: str, member_email: str, brief: dict) -> str:
    first_name = member_name.split()[0]
    today = datetime.now()
    date_str = today.strftime("%A, %B %-d, %Y")
    date_short = today.strftime("%b %-d")

    def yt_url(vid_id: str) -> str:
        return f"https://www.youtube.com/watch?v={vid_id}"

    def tldr_html(bullets: list[str]) -> str:
        items = []
        for b in bullets:
            if b.startswith("**"):
                end_bold = b.find("**", 2)
                if end_bold > 0:
                    bold = b[2:end_bold]
                    rest = b[end_bold + 2:]
                    items.append(f"<li><strong>{bold}</strong>{rest}</li>")
                    continue
            items.append(f"<li>{b}</li>")
        return "\n".join(items)

    one = brief.get("one_thing", {})
    hot = brief.get("hot_items", [])
    impl = brief.get("implement_items", [])
    save = brief.get("save_items", [])

    # Build hot items HTML
    hot_html = ""
    for i, item in enumerate(hot):
        panel_id = f"hot-panel-{i}"
        hot_html += f"""
  <div class="card" style="border-color:rgba(247,181,137,0.35);background:linear-gradient(180deg,rgba(247,181,137,0.06),var(--surface) 40%)">
    <div class="why-kicker" style="color:var(--accent-peach)">For you</div>
    <div class="why-line">{item.get('for_you','')}</div>
    <div class="use-line">{item.get('use_case','')}</div>
    <div class="creator">{item.get('channel','')} . posted today</div>
    <h3>{item.get('headline','')}</h3>
    <p class="what">{item.get('what','')}</p>
    <ul class="tldr">
{tldr_html(item.get('tldr', []))}
    </ul>
    <div class="btn-row-label">Consume</div>
    <div class="btn-row">
      <a class="btn btn-primary" href="{yt_url(item['video_id'])}" target="_blank">&#9654; Watch {item.get('channel','')} ({item.get('duration_min','')} min)</a>
    </div>
    <div class="verify-note">{item.get('verify_note','')}</div>
  </div>"""

    # Build implement items HTML
    impl_html = ""
    for i, item in enumerate(impl):
        panel_id = f"impl-panel-{i}"
        impl_html += f"""
  <div class="card">
    <div class="why-kicker">For you</div>
    <div class="why-line">{item.get('for_you','')}</div>
    <div class="use-line">{item.get('use_case','')}</div>
    <div class="creator">{item.get('channel','')}</div>
    <h3>{item.get('headline','')}</h3>
    <p class="what">{item.get('what','')}</p>
    <ul class="tldr">
{tldr_html(item.get('tldr', []))}
    </ul>
    <div class="btn-row-label">Consume</div>
    <div class="btn-row">
      <a class="btn btn-primary" href="{yt_url(item['video_id'])}" target="_blank">&#9654; Watch {item.get('channel','')} ({item.get('duration_min','')} min)</a>
    </div>
  </div>"""

    # Build save items HTML
    save_html = ""
    for item in save:
        save_html += f"""
      <div class="src-row">
        <span class="src-name">
          <a href="{yt_url(item['video_id'])}" target="_blank" style="color:var(--ink-2);text-decoration:none">{item.get('channel','')} - {item.get('headline','')}</a>
        </span>
        <span class="src-badge skip">Later</span>
      </div>
      <div style="font-size:14px;color:var(--ink-3);margin:0 0 12px 0">{item.get('why_later','')}</div>"""

    one_action_label = f"{one.get('channel','')} walkthrough"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your Daily Brief - {first_name} - {today.strftime('%A, %b %-d')}</title>
<meta name="robots" content="noindex, nofollow">
<link rel="stylesheet" href="https://use.typekit.net/jwl7pvv.css">
<link rel="preconnect" href="https://use.typekit.net" crossorigin>
<style>
  :root {{
    --brand-blue: #5a6cff;
    --brand-blue-lift: #7a88ff;
    --brand-blue-soft: rgba(90, 108, 255, 0.14);
    --brand-blue-line: rgba(90, 108, 255, 0.28);
    --accent-peach: #f7b589;
    --accent-peach-soft: rgba(247, 181, 137, 0.14);
    --green: #6fcf97;
    --green-soft: rgba(111, 207, 151, 0.14);
    --yellow: #f2c94c;
    --yellow-soft: rgba(242, 201, 76, 0.12);
    --skip: #6d7788;
    --skip-soft: rgba(109, 120, 136, 0.12);
    --bg: #070a10;
    --bg-gradient: radial-gradient(ellipse at 30% 0%, #182138 0%, #0a0d14 55%, #070a10 100%);
    --surface: #10141e;
    --surface-2: #161c28;
    --surface-3: #1f2734;
    --surface-hi: #262f3e;
    --ink: #f4f7fc;
    --ink-2: #d4dbe8;
    --ink-3: #9ca5b7;
    --ink-faint: #6d7688;
    --line: rgba(255, 255, 255, 0.08);
    --line-soft: rgba(255, 255, 255, 0.04);
    --line-strong: rgba(255, 255, 255, 0.14);
    --radius-btn: 7px;
    --radius-card: 14px;
    --shadow-card: 0 1px 2px rgba(0,0,0,0.4), 0 14px 32px rgba(0,0,0,0.28);
    --font-sans: "helvetica-neue-lt-pro", "Helvetica Neue", Helvetica, Arial, sans-serif;
    --font-mono: ui-monospace, "SF Mono", Menlo, monospace;
  }}
  *{{box-sizing:border-box}}
  html{{scroll-behavior:smooth}}
  body{{margin:0;font-family:var(--font-sans);font-weight:400;font-size:18px;line-height:1.7;color:var(--ink-2);background:var(--bg);background-image:var(--bg-gradient);background-attachment:fixed;-webkit-font-smoothing:antialiased}}
  h1,h2,h3,h4{{color:var(--ink);margin:0 0 .5em}}
  h1{{font-size:38px;font-weight:800;letter-spacing:-.01em;line-height:1.15}}
  h2{{font-size:13px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);margin:48px 0 18px}}
  h3{{font-size:23px;font-weight:700;line-height:1.3}}
  p{{margin:0 0 1em}}
  a{{color:var(--brand-blue-lift)}}
  .wrap{{max-width:920px;margin:0 auto;padding:48px 28px 96px}}
  .kicker{{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--accent-peach);background:var(--accent-peach-soft);padding:6px 12px;border-radius:999px;margin-bottom:14px}}
  .meta{{color:var(--ink-3);font-size:15px;margin-top:6px}}
  .status-strip{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:28px 0 0}}
  .status-card{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 18px}}
  .status-card .label{{font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);margin-bottom:6px}}
  .status-card .value{{font-size:28px;font-weight:800;color:var(--ink);line-height:1}}
  .status-card .note{{font-size:13px;color:var(--ink-faint);margin-top:4px}}
  .onething{{background:var(--surface-2);border:1px solid var(--brand-blue-line);border-radius:var(--radius-card);padding:32px 36px;margin:36px 0}}
  .onething-kicker{{font-size:12px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--brand-blue-lift);margin-bottom:12px}}
  .onething h3{{font-size:26px;margin-bottom:14px}}
  .card{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-card);padding:28px 32px;margin-bottom:18px;box-shadow:var(--shadow-card)}}
  .why-kicker{{font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:var(--accent-peach);margin-bottom:4px}}
  .why-line{{font-size:17px;font-weight:700;color:var(--ink);line-height:1.35;margin-bottom:6px}}
  .use-line{{font-size:15px;color:var(--ink-3);margin-bottom:16px}}
  .creator{{font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:8px}}
  .what{{font-size:16px;color:var(--ink-2);margin:14px 0}}
  ul.tldr{{margin:0 0 20px;padding:0 0 0 20px}}
  ul.tldr li{{font-size:15px;color:var(--ink-2);margin-bottom:6px;line-height:1.5}}
  ul.tldr li strong{{color:var(--ink)}}
  .btn-row{{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 4px}}
  .btn-row-label{{font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-faint);margin-top:16px;margin-bottom:4px}}
  .btn{{display:inline-flex;align-items:center;gap:7px;font-family:var(--font-sans);font-size:14px;font-weight:700;padding:9px 16px;border-radius:var(--radius-btn);border:none;cursor:pointer;text-decoration:none;white-space:nowrap;transition:opacity .15s}}
  .btn:hover{{opacity:.85}}
  .btn-primary{{background:var(--brand-blue);color:#fff}}
  .btn-ghost{{background:transparent;color:var(--ink-2);border:1px solid var(--line-strong)}}
  .btn-install{{background:var(--green-soft);color:var(--green);border:1px solid rgba(111,207,151,0.3)}}
  .btn-action{{background:var(--accent-peach-soft);color:var(--accent-peach);border:1px solid rgba(247,181,137,0.3)}}
  .verify-note{{font-size:12px;color:var(--ink-faint);margin-top:14px;padding-top:12px;border-top:1px solid var(--line-soft)}}
  .src-row{{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line-soft)}}
  .src-name{{font-size:15px;color:var(--ink-2)}}
  .src-badge{{font-size:11px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap}}
  .src-badge.skip{{background:var(--skip-soft);color:var(--skip)}}
  .sources-box{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-card);padding:20px 24px;margin-top:32px}}
  footer{{margin-top:64px;padding-top:24px;border-top:1px solid var(--line-soft);text-align:center;font-size:13px;color:var(--ink-faint)}}
  @media(max-width:640px){{
    .status-strip{{grid-template-columns:1fr}}
    .card{{padding:22px 20px}}
    .onething{{padding:24px 22px}}
    h1{{font-size:30px}}
  }}
</style>
</head>
<body>

<div class="wrap">

  <span class="kicker">AI Collective . Daily Brief</span>
  <h1>Good morning, {first_name}.</h1>
  <p class="meta">{date_str} . {brief.get('focus_count', 0)} items to focus on . synthesized from {len(CHANNELS)} channels</p>

  <div class="status-strip">
    <div class="status-card">
      <div class="label">Stories to focus on</div>
      <div class="value">{brief.get('focus_count', 0)}</div>
      <div class="note">{brief.get('skipped_count', 0)} skipped . {brief.get('total_scanned', 0)} total</div>
    </div>
    <div class="status-card">
      <div class="label">One thing today</div>
      <div class="value" style="font-size:18px;line-height:1.3">{one.get('headline', '')[:50]}</div>
      <div class="note">{one.get('subtitle', '')}</div>
    </div>
    <div class="status-card">
      <div class="label">Time to read</div>
      <div class="value">{brief.get('read_time_min', 8)} min</div>
      <div class="note">Saves you ~2-3 hours of YouTube</div>
    </div>
  </div>

  <!-- ONE THING TODAY -->
  <div class="onething">
    <div class="onething-kicker">If you only do one thing today</div>
    <h3>{one.get('headline', '')}</h3>
    <p>{one.get('body', '')}</p>
    <div class="btn-row" style="margin-top:18px">
      <a class="btn btn-install" href="https://claude.ai" target="_blank">&#9889; Open Claude</a>
      <a class="btn btn-ghost" href="{yt_url(one.get('video_id',''))}" target="_blank">&#9654; {one_action_label}</a>
    </div>
  </div>

  <!-- JUST DROPPED TODAY -->
  <h2 style="color:var(--accent-peach)">Just dropped today</h2>
{hot_html}

  <!-- IMPLEMENT NOW -->
  <h2>What you should do today</h2>
{impl_html}

  <!-- SAVE FOR LATER -->
  <h2>Save for later</h2>
  <div class="sources-box">
{save_html}
    <div style="margin-top:14px;font-size:13px;color:var(--ink-faint)">{brief.get('skipped_note','')}</div>
  </div>

</div>

<footer>
  AI Collective Daily Brief for {member_name} &middot; {date_str} &middot; <a href="https://collective.bnsn.ai" style="color:var(--ink-faint)">collective.bnsn.ai</a>
</footer>

</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(description="Generate AI Collective Daily Brief")
    parser.add_argument("--name", required=True, help="Member full name")
    parser.add_argument("--email", required=True, help="Member email (used for URL path)")
    parser.add_argument("--context", default="", help="Member context/profile notes")
    parser.add_argument("--dry-run", action="store_true", help="Generate HTML but don't save")
    args = parser.parse_args()

    # Default contexts per known member
    if not args.context:
        contexts = {
            "jb@bnsn.ai": "Founder and CEO of BNSN.AI. Builds AI-powered copywriting tools. Uses Claude Code, Cursor, Obsidian, BNSN, NotebookLM, ElevenLabs daily. Runs AI Collective mastermind. Currently building: YouCloned course updates, AIC Skills Library, SLIDR (VSL editor), Customer Service dashboard.",
            "bcope1@gmail.com": "CMO at BNSN.AI. Manages CRM, email marketing (Mailvio), and business operations. Technical - has code access. Focuses on conversion, customer journeys, and team tooling. Uses Claude Code and Cursor.",
        }
        args.context = contexts.get(args.email, f"{args.name} is an AI Collective member using the YouCloned stack.")

    # Scan channels
    videos = scan_channels()

    if not videos:
        print("No recent videos found. Check channel handles or expand lookback window.", file=sys.stderr)
        sys.exit(1)

    # Fetch transcripts
    videos_with_transcripts = fetch_transcripts(videos)

    if not videos_with_transcripts:
        print("Could not get any transcripts.", file=sys.stderr)
        sys.exit(1)

    # Synthesize
    brief = synthesize_brief(videos_with_transcripts, args.name, args.context)

    # Build HTML
    html = build_html(args.name, args.email, brief)

    if args.dry_run:
        out_path = REPO_ROOT / "brief-preview.html"
        out_path.write_text(html)
        print(f"\nDry run - saved to {out_path}")
        return

    # Save to member path
    out_dir = REPO_ROOT / "m" / "tldr" / args.email
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "index.html"
    out_path.write_text(html)
    print(f"\nSaved: {out_path}")
    print(f"URL:   https://collective.bnsn.ai/m/tldr/{args.email}/")


if __name__ == "__main__":
    main()
