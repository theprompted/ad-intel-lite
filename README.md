# Ad Intel Lite

Free Facebook ad intelligence skill for Claude Code.

Analyze any brand's live Facebook ads and get a structured brief — hooks, avatar, thesis, what's working, what they never do — all in one HTML file you can hand directly to your AI agent.

## How to use it

Tell Claude Code:

> "Go read this repo and install all skills and dependencies: https://github.com/theprompted/ad-intel-lite"

Claude Code handles the rest.

## What you get

- **Q1** — What are they running and how much are they spending on each?
- **Q2** — What angles, formats, and ideas are they betting on?
- **Q3** — Who is the customer they're selling to?
- **Q4** — What do they believe about the customer and the problem?
- **Q5** — What hooks are working?
- **Q7** — What do they never do — and what does that tell you about the buyer?
- **Action section** — Fill-in-the-blank hook templates from the top patterns

Output is an HTML brief with a "Copy brief for your agent" button — one click puts the full analysis on your clipboard, ready to paste into Claude Code for creative production.

## What's in this repo

- `SKILL.md` — the skill file Claude Code installs
- `fb-ad-library-scraper.py` — the scraper that pulls live ads from the Facebook Ad Library

## Requirements

- [Claude Code](https://claude.ai/code)
- [agent-browser](https://agent-browser.dev) — `npm install -g agent-browser`

## Full version

The full version adds landing page scraping (offer, price, guarantee), per-ad video frame analysis via FFmpeg, and Whisper audio transcription. Available at [theprompted.com](https://theprompted.com).
