# Ad Intelligence Brief Lite

Analyze a brand's Facebook ads and produce a structured HTML brief organized around seven questions — the same questions a good ad practitioner already asks silently when they look at a competitor. The brief is designed to be handed directly to an AI agent for creative production.

This is the lite version. It runs entirely in one session with Claude Code and a browser. No paid tools, no special setup. The only thing you need is Claude Code installed and a brand to analyze.

**What you get:** Q1 through Q5 and Q7 — six of the seven questions answered with real data from the brand's live ads. Q6 (the actual offer from the landing page) requires the full version.

**What you do NOT need:** FFmpeg, Whisper, a proxy, or any API keys beyond what Claude Code already has.

---

## Prerequisites — set these up once

### 1. Install agent-browser (the scraper that pulls the ads)

agent-browser is a browser automation CLI built for AI agents. It opens the Facebook Ad Library, scrolls through it automatically, and saves every ad to a file.

```bash
npm install -g agent-browser
```

### 2. Download the scraper script

Save this file to your project as `fb-ad-library-scraper.py`:

```
https://github.com/theprompted/ad-intel-lite/blob/main/fb-ad-library-scraper.py
```

That script does three things: scrapes the ads, scores every ad using 8 signals, and saves the output as JSON.

### 3. Make sure you have Claude Code

This skill runs inside Claude Code (claude.ai/code or the CLI). If you're reading this inside Claude Code, you're already set.

---

## How to run it

Tell Claude Code:

> "Run an ad intel lite brief on [brand name]"

Or if you know the Facebook page ID:

> "Run an ad intel lite brief on [brand name], page ID [id]"

Claude Code will handle everything from there.

---

## What Claude Code does — step by step

### Step 1 — Scrape the ads

```bash
python3 fb-ad-library-scraper.py "BRAND NAME" --page-id PAGE_ID --max-scroll 15 --output scraped/SLUG.json --status active
```

This pulls ~50–100 ads sorted by `impressions_high_to_low` — the same order Facebook's API returns them. Rank 1 is being shown more than rank 10. You only get the rank position, not raw impression counts (Meta doesn't publish those for commercial ads). The rank is still the best public proxy for spend that exists.

**If you get 0 results:** Facebook rate-limited you. Wait 12–24 hours and try again. This is normal. It lifts on its own.

**If you don't have the page ID:** Claude Code will search the Ad Library by brand name to find it.

### Step 2 — Score every ad

The scraper runs a scoring function on every ad using 8 signals:

1. Impression rank — position in the API sort order
2. Days running — longer = the brand keeps paying for it
3. Variant count — more versions = active testing
4. Copy duplication — same copy across many ads = proven message
5. Funnel commitment — how many ads point to the same landing page
6. Cross-page deployment — running from multiple brand pages
7. Format distribution — matched to what's scoring highest in the account
8. Recency weighting — fresh tests vs. sustained bets

**Grades:** A = 70+, B = 60–69, C = 50–59

**Note:** Engagement data (likes, comments, shares) is deliberately NOT a signal. Engagement rewards entertainment and controversy, not conversion. Run time and variant count are more honest indicators of what the brand actually believes in.

### Step 3 — Download media for the top 10 ads

Poster images for all top 10. Video mp4 files where available. Image ads get the full creative downloaded.

**Important:** CDN URLs from Facebook expire within hours. Claude Code downloads these in the same session as the scrape. If you come back later, the URLs will be dead — you'd need to re-scrape.

No FFmpeg frame extraction in lite. No Whisper audio transcription. The full version adds both — that's how it can describe exactly what happens in the first 3 seconds of every video.

### Step 4 — Build the HTML brief

Claude Code writes an HTML file with all seven sections. Same visual design as the full version — PromptedOS design system (teal `#53DBC9`, offset shadows, monospace headers, black borders). Visually indistinguishable from the full version, fewer sections.

Output: `[brand-slug]-brief-lite.html`

---

## What's in the brief

### Header — How to use this document

Short framing note explaining this is context for your agent, not a document to memorize. Includes the "Copy brief for your agent" button — one click puts the entire brief on your clipboard, ready to paste into Claude Code alongside your creative production prompt.

### Q1 — What are they running, and how much are they spending on each one?

- Five stat boxes at the top: total ad count, % video, oldest ad still running, how many ads share the same copy, how many launched in the last 30 days
- Velocity callout: launches per day, whether they're scaling or holding steady
- Top 10 ads as embedded feed mockups — the actual creative, displayed the way it looks in the Facebook feed, with rank / days active / variant count / format annotations
- Note on the API vs UI distinction — why rank from the API is more useful than scrolling the website

### Q2 — Where are they putting their money? What angles, formats, and ideas are they betting on?

- Scoring methodology card (the 8 signals, the A/B/C grades)
- "Engagement lies" callout — why likes aren't a signal
- The pattern across the top-scoring ads: what format, hook type, copy length, and tone the brand keeps coming back to
- Creative format breakdown by rank tier

### Q3 — Who is the customer they're selling to?

Reverse-engineered from the copy — not from targeting data, which is what the brand guessed before running ads. The copy is what they learned after running hundreds of them.

Four mini-cards:
- **Demographics** — who they are on paper
- **Identity** — how they see themselves
- **Attention** — what gets them to stop scrolling
- **Turn-offs** — what makes them scroll past

Plus a plain-text avatar prompt box formatted for pasting directly into your agent.

### Q4 — What do they believe about the customer and the problem? (The thesis)

Every brand running a lot of ads has a consistent point of view baked in. You don't see it in one ad. You see it when you look at a hundred of them together. Six mini-cards:

- **Core belief** — the one thing the brand believes is true about the customer's problem
- **Contrast** — what they're arguing against / for
- **The big idea** — the reframe that makes the brand's solution feel inevitable
- **Unique mechanism** — what makes their product the logical answer given the belief
- **The villain** — what they're blaming for the customer's problem
- **Strength assessment** — how entrenched this thesis is (are all ads consistent, or is it mixed?)

### Q5 — What hooks are working?

A hook is the first 3 seconds of a video — what's on screen before someone decides to keep watching or scroll past. It is not the caption. The caption is what someone reads after they've already stopped scrolling.

In lite, Claude Code reads the copy and metadata to infer hook patterns. The full version downloads and analyzes every frame of every video. Both name the same patterns — lite just can't show you the exact visual.

Ranked hook patterns with brand examples and awareness-level mapping (which hooks serve which stage of buyer awareness).

### *(Q6 — The offer — full version only)*

The full version scrapes the landing pages to find the actual price, deal, guarantee, and warranty. Lite skips this — the offer lives on the page, not in the ad, so you need LP access to answer it properly.

### Q7 — What do they never do — and what does that tell you about the buyer?

What a brand never does across hundreds of ads is a deliberate choice. A brand that never runs countdown timers in 200 ads isn't forgetting to — they know their buyer doesn't respond to pressure.

- DO vs DON'T two-column layout with counts (e.g. "No urgency language — 0 of 708 ads")
- Buyer inference below: what each zero reveals about who's on the other end of the ad

### Action section — Copy what works

4 fill-in-the-blank templates extracted from the top hook patterns. Black background, teal text, bracketed variables. Each template includes:
- The hook pattern name and which Q5 pattern it maps to
- The template with `[bracketed variables]` for your product
- One real example from the brand

---

## How to use the output

At the bottom of the brief: a "Copy brief for your agent" button. Click it. Paste into Claude Code. Then say:

> "Using this brief as context, write me 3 ads for [your product] in copy mode" 

or

> "Using this brief as context, write me 3 ads for [your product] in contrast mode — say the opposite of what this brand is doing"

**Copy mode:** take the brand's proven frame — hook pattern, angle, tone — and apply it to your product. You're not copying the ad. You're using an approach already proven to work on this type of person.

**Contrast mode:** every brand in a category converges on the same angles over time. The brief shows you exactly what that looks like. You point your agent at the opposite — something that feels completely unlike everything else in the feed, but is still true and credible.

**Decision rule:** same type of buyer as the brand you studied → copy the frame. Real point of difference, or the category has already converged on the same angles → contrast.

Your agent still needs creative production skills to do something useful with the brief. The brief is context — not a complete instruction. Tell it the mode, the product, and the format you want.

---

## Upgrade note

This is the lite version. The full version adds:

- **Landing page analysis** — the actual offer: price, compare-at, guarantee, warranty, funnel awareness (problem-aware vs product-aware ad distribution)
- **Per-ad breakdown cards** — for each of the top 10 ads: visual description (what the hook looks like frame by frame), hook pattern classification, copy analysis, offer detected, awareness level, and a hypothesis for why it's working
- **FFmpeg + Whisper** — frame-by-frame video analysis and audio transcription so the agent knows exactly what was said and shown in the first 3 seconds
- **Contrast templates** — 4 contrarian hooks built from inverting the brand's dominant angles

---

## Troubleshooting

**0 ads returned:** Rate limited by Facebook. Wait 12–24 hours. Don't retry immediately — it won't help.

**agent-browser not found:** Run `npm install -g agent-browser` and try again.

**CDN URLs expired on media:** Re-scrape. The URLs are only valid for a few hours after the scrape. Download in the same session.

**Brand not found by name:** Ask Claude Code to search the Ad Library for the page ID manually, then re-run with the ID.

---

## Sources

- Full version skill: `.claude/skills/ad-intel-brief/SKILL.md`
- Scraper: `scripts/fb-ad-library-scraper.py`
- Session s9 (2026-04-17): Updated question structure (Q3 avatar, Q5 hooks now explicit), parallel scraper as preferred, Whisper noted as full-version-only, self-contained onboarding rewrite
