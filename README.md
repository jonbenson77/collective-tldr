# collective-tldr

AI Collective Daily Brief - personalized AI news brief delivered weekdays at 5pm CT to paid AI Collective members.

**Live URL:** https://collective.bnsn.ai
**Member URL pattern:** `https://collective.bnsn.ai/m/tldr/{member_email}`

## Status

Holding page live. Brief generator + GH Actions cron not built yet.

## Architecture (planned)

- **Sources:** 12 YouTube channels + 6 LLM vendor blogs (see `sources.md`, not yet committed)
- **Synthesis:** Python script reads sources, pulls transcripts, uses Anthropic API to synthesize per-member
- **Profile:** stored in Supabase Isaac project, one row per member
- **Voice/install rules:** locked in `BRIEF-VOICE-SPEC.md` (not yet committed) and loaded into every synthesis prompt
- **Cron:** GitHub Actions, weekdays 5pm CT, fans out per-member work
- **Hosting:** GitHub Pages (this repo) for V1; Fly machine for V2 once member count crosses 50
- **Email:** Mailvio campaign with per-member `member_url` merge tag
- **Member sync:** `/collective-sync` skill (not yet built) lives in member's Claude Code, pushes profile snapshot to Supabase nightly

## DNS

`collective.bnsn.ai` is a CNAME to `jonbenson77.github.io`, managed via GoDaddy API.

## First test customer

Mike Filsaime (`mike@groovedigital.com`).
