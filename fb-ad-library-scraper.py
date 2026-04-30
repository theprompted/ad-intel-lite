#!/usr/bin/env python3
"""
FB Ad Library Scraper
Usage: python3 fb-ad-library-scraper.py "brand name" [--max-scroll N] [--landing-pages] [--media] [--output path/to/out.json]

Scrapes facebook.com/ads/library for a given brand/keyword.
Extracts: library ID, page name, status, start date, variants, format, full copy, landing page URL.
Optionally downloads image and video creatives.
Saves results to JSON.
"""

import subprocess
import json
import sys
import time
import re
import argparse
import urllib.request
import urllib.parse
import concurrent.futures
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent / "scraped"
SCROLL_PAUSE = 2.5       # seconds between scrolls
SCROLL_PIXELS = 1200     # px per scroll
MAX_SCROLL_DEFAULT = 8   # number of scroll attempts before stopping
RETRY_LIMIT = 3          # retries for failed browser commands
MEDIA_WORKERS = 3        # parallel download threads (conservative to avoid CDN throttling)
MIN_IMAGE_SIZE = 5_000   # bytes — skip tiny icons/thumbnails
DOWNLOAD_JITTER = 0.3    # seconds jitter between download batches


# ── Browser helpers ───────────────────────────────────────────────────────────
def run(cmd: list, retries=RETRY_LIMIT) -> str:
    """Run an agent-browser command, return stdout. Retries on failure."""
    for attempt in range(retries):
        result = subprocess.run(
            ["npx", "agent-browser"] + cmd,
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
        if attempt < retries - 1:
            time.sleep(1.5)
    print(f"  [WARN] Command failed after {retries} attempts: {cmd[:3]}", file=sys.stderr)
    return ""


def eval_js(js: str, retries=RETRY_LIMIT) -> str:
    """Run JavaScript in the browser, return result string."""
    raw = run(["eval", js], retries=retries)
    # agent-browser wraps string results in double quotes — use json.loads to unescape
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            return parsed
        return str(parsed)
    except Exception:
        return raw


# ── JS templates ─────────────────────────────────────────────────────────────
EXTRACT_CARD_AT_INDEX_JS = """
(function(idx) {
  const allDivs = [...document.querySelectorAll('div')];
  const candidates = allDivs.filter(div => {
    const t = div.innerText || '';
    return t.includes('Library ID:') &&
           t.includes('Started running on') &&
           t.includes('Sponsored');
  });
  const cards = candidates.filter(card =>
    !candidates.some(other => other !== card && card.contains(other))
  );

  const card = cards[idx];
  if (!card) return 'null';

  const lines = card.innerText.split('\\n');

  const statusLine = lines.find(l => l === 'Active' || l === 'Inactive') || 'Unknown';

  const libIdLine = lines.find(l => l.startsWith('Library ID:')) || '';
  const libId = libIdLine.replace('Library ID: ', '').trim();

  const dateLine = lines.find(l => l.startsWith('Started running on')) || '';
  const startDate = dateLine.replace('Started running on ', '').trim();

  const variantLine = lines.find(l => /\\d+ ads use this creative/.test(l)) || '';
  const variantMatch = variantLine.match(/(\\d+) ads use this creative/);
  const variants = variantMatch ? parseInt(variantMatch[1]) : 1;

  const sponsoredIdx = lines.indexOf('Sponsored');
  const pageName = sponsoredIdx > 0 ? lines[sponsoredIdx - 1].trim() : '';

  // Copy: everything after 'Sponsored', stripping zero-width spaces and video timecodes
  // Timecodes appear as "0:00 / 0:48" injected by the video player into innerText
  const copyLines = lines.slice(sponsoredIdx + 1).filter(l => {
    if (!l || l === '\\u200b') return false;
    if (/^\\d+:\\d+\\s*\\/\\s*\\d+:\\d+$/.test(l.trim())) return false; // strip "0:00 / 0:48"
    return true;
  });
  const copy = copyLines.join('\\n').trim();

  // Media extraction

  // Fix 1: Upgrade image resolution — strip stp resize param and request 1080px
  // FB uses stp=dst-jpg_s600x600_tt6 to serve thumbnails; replacing gives full res
  const upgradeImgUrl = (src) => {
    if (!src) return null;
    return src.replace(/stp=dst-jpg_s\d+x\d+[^&]*/g, 'stp=dst-jpg_s1080x1080');
  };

  // Images: skip 60x60 page avatars, take the ad creative (naturalWidth >= 100)
  const imgEls = [...card.querySelectorAll('img')]
    .filter(i => i.naturalWidth >= 100 && i.src.includes('fbcdn'));
  const imgEls_sorted = imgEls.sort((a, b) => (b.naturalWidth * b.naturalHeight) - (a.naturalWidth * a.naturalHeight));
  const imageUrlOriginal = imgEls_sorted[0]?.src || null;
  const imageUrl = upgradeImgUrl(imageUrlOriginal);

  // Fix 2: Force video currentSrc to populate by triggering load
  // currentSrc is empty until browser starts loading — calling load() forces it
  const videoEl = card.querySelector('video');
  if (videoEl && !videoEl.currentSrc) {
    videoEl.load();
  }
  const videoUrl = videoEl ? (videoEl.currentSrc || videoEl.src || null) : null;
  const posterUrl = videoEl ? (videoEl.poster || null) : null;

  const hasVideo = !!videoEl && !!videoUrl;
  const hasImg = !!imageUrl;
  const format = hasVideo ? 'Video' : hasImg ? 'Image' : 'Text';

  const hasDetails = card.innerText.includes('See ad details');
  const hasSummary = card.innerText.includes('See summary details');

  // Extract domain mentioned in copy (e.g. "MELLOWSLEEP.COM" or "spnutrition-us.com")
  const domainMatch = copy.match(/(?:https?:\/\/)?([a-z0-9][a-z0-9\-]{1,61}[a-z0-9]\.[a-z]{2,}(?:\/[^\s]*)?)/i);
  const mentionedDomain = domainMatch ? domainMatch[1].toLowerCase().replace(/^www\./,'') : null;

  return JSON.stringify({ libId, status: statusLine, startDate, variants, pageName, format,
    copy, hasDetails, hasSummary, imageUrl, imageUrlOriginal, videoUrl, posterUrl, mentionedDomain });
})(IDX);
"""

GET_LANDING_PAGE_JS = """
(function() {
  const links = [...document.querySelectorAll('a[href]')];
  // Include l.facebook.com redirect links (they wrap the real destination in u= param)
  // Exclude pure FB nav links
  const skip = ['facebook.com/ads/library', 'facebook.com/ads/report', 'facebook.com/ads/api',
                'facebook.com/ads/about', 'facebook.com/policies', 'facebook.com/privacy',
                'facebook.com/language', 'metastatus.com', 'fb.com/'];
  const external = links
    .map(a => a.href)
    .filter(h => {
      if (!h || !h.startsWith('http')) return false;
      if (skip.some(s => h.includes(s))) return false;
      // Keep l.facebook.com (CTA redirect links) and direct external links
      if (h.includes('l.facebook.com') || h.includes('lm.facebook.com')) return true;
      if (!h.includes('facebook.com') && !h.includes('fb.com')) return true;
      return false;
    });
  return JSON.stringify([...new Set(external)]);
})();
"""

RESULT_COUNT_JS = """
(function() {
  const heading = document.querySelector('h3');
  return heading ? heading.innerText : '';
})();
"""

CARD_COUNT_JS = """
(function() {
  const allDivs = [...document.querySelectorAll('div')];
  const candidates = allDivs.filter(div => {
    const t = div.innerText || '';
    return t.includes('Library ID:') && t.includes('Started running on') && t.includes('Sponsored');
  });
  const cards = candidates.filter(card =>
    !candidates.some(other => other !== card && card.contains(other))
  );
  return String(cards.length);
})();
"""


# ── URL engagement (genius signal) ───────────────────────────────────────────
# FB Graph API exposes engagement for ANY public URL — no auth, no post ID needed.
# Every ad that drives traffic to a landing page accumulates its social proof there.
# query: graph.facebook.com/?id=URL&fields=engagement
# Returns: reaction_count, share_count, comment_count
# Works for competitor landing pages, advertorials, product pages — anything public.
# This IS the engagement signal for dark posts. Sort by share_count = impression rank
# cross-validated with actual human engagement.

FB_APP_TOKEN = None  # Set via --app-token or auto-detected from .env

def _load_app_token() -> str:
    """Load FB app token from .env or return None.
    Add FB_APP_ID and FB_APP_SECRET to a .env file in the same directory.
    """
    global FB_APP_TOKEN
    if FB_APP_TOKEN:
        return FB_APP_TOKEN
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        app_id = app_secret = None
        for line in env_path.read_text().splitlines():
            if line.startswith("FB_APP_ID="):
                app_id = line.split("=", 1)[1].strip()
            if line.startswith("FB_APP_SECRET="):
                app_secret = line.split("=", 1)[1].strip()
        try:
            FB_APP_TOKEN = f"{app_id}|{app_secret}"
            return FB_APP_TOKEN
        except Exception:
            pass
    return None


def fetch_url_engagement(url: str, token: str) -> dict:
    """Query FB Graph API for engagement on a public URL.
    Returns dict with reaction_count, share_count, comment_count (all int).
    No auth required beyond app token. Works on any public URL.
    """
    try:
        enc = urllib.parse.quote(url, safe='')
        api = f"https://graph.facebook.com/v19.0/?id={enc}&fields=engagement&access_token={token}"
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read())
        e = d.get("engagement", {})
        return {
            "urlReactions": e.get("reaction_count", 0),
            "urlShares": e.get("share_count", 0),
            "urlComments": e.get("comment_count", 0),
        }
    except Exception:
        return {"urlReactions": 0, "urlShares": 0, "urlComments": 0}


def enrich_with_url_engagement(ads: list, token: str):
    """Add urlReactions/urlShares/urlComments to each ad based on its landing page URL.
    Groups ads by unique landing page URL to avoid duplicate API calls.
    Prints a sorted engagement table when done.
    """
    # Collect unique landing page URLs
    url_to_ads = {}
    for ad in ads:
        lp = ad.get("landingPage")
        if lp and lp.startswith("http"):
            url_to_ads.setdefault(lp, []).append(ad)
        else:
            ad.update({"urlReactions": 0, "urlShares": 0, "urlComments": 0})

    if not url_to_ads:
        print("  [NOTE] No landing page URLs to enrich — run with --landing-pages to capture them")
        for ad in ads:
            ad.setdefault("urlReactions", 0)
            ad.setdefault("urlShares", 0)
            ad.setdefault("urlComments", 0)
        return

    print(f"\n  Fetching URL engagement for {len(url_to_ads)} unique landing pages...")
    url_engagement = {}
    for i, url in enumerate(url_to_ads.keys(), 1):
        eng = fetch_url_engagement(url, token)
        url_engagement[url] = eng
        if eng["urlShares"] > 0:
            print(f"  [{i}/{len(url_to_ads)}] {url[:60]} → {eng['urlShares']:,} shares / {eng['urlReactions']:,} reactions")
        time.sleep(0.3)  # gentle rate limiting

    # Apply to ads
    for url, eng in url_engagement.items():
        for ad in url_to_ads[url]:
            ad.update(eng)

    # Print top URLs by share count
    top = sorted(url_engagement.items(), key=lambda x: x[1]["urlShares"], reverse=True)[:10]
    if top:
        print(f"\n  Top landing pages by FB share count:")
        print(f"  {'URL':<55} {'shares':>8} {'reactions':>10} {'comments':>9}")
        print(f"  {'-'*85}")
        for url, eng in top:
            print(f"  {url:<55} {eng['urlShares']:>8,} {eng['urlReactions']:>10,} {eng['urlComments']:>9,}")


# ── Landing page extraction ───────────────────────────────────────────────────
def _resolve_redirect(url: str) -> str:
    """Follow l.facebook.com redirect to get the real destination URL."""
    if 'l.facebook.com' not in url and 'lm.facebook.com' not in url:
        return url
    try:
        # Extract the 'u' param which holds the encoded destination
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if 'u' in params:
            return unquote(params['u'][0])
        # Fall back to following the redirect via HTTP
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.url
    except Exception:
        return url


def get_landing_page(lib_id: str):
    """Open the ad detail page and extract the external CTA URL.
    Handles both 'See ad details' and 'See summary details' button types.
    Follows l.facebook.com redirects to get the real destination.
    """
    run(["open", f"https://www.facebook.com/ads/library/?id={lib_id}"])
    run(["wait", "--load", "networkidle"])
    time.sleep(1.5)

    # Click through to detail/summary modal if button exists
    clicked = eval_js("""
    (function() {
      const btns = [...document.querySelectorAll('button')];
      const detail = btns.find(b => b.innerText.includes('See ad details') || b.innerText.includes('See summary details'));
      if (detail) { detail.click(); return 'clicked'; }
      return 'none';
    })();
    """)
    if clicked == 'clicked':
        time.sleep(1.5)

    raw = eval_js(GET_LANDING_PAGE_JS)
    try:
        links = json.loads(raw)
        # Filter out noise
        skip = {'privacy', 'metastatus.com', 'facebook.com/ads', 'facebook.com/policies',
                'facebook.com/about', 'facebook.com/language', 'facebook.com/ads/library'}
        good = [l for l in links if not any(s in l for s in skip)]
        if not good:
            return None
        # Resolve l.facebook.com redirects to real destination
        return _resolve_redirect(good[0])
    except Exception:
        return None


# ── Media downloading ─────────────────────────────────────────────────────────
def download_file(url: str, dest: Path, fallback_url: str = None) -> bool:
    """Download a URL to dest. Falls back to fallback_url on 403.
    Fix 3: CDN URLs are signed and expire within hours — must download in same session.
    Fix 1b: Upgraded resolution URLs may 403 — fall back to original if so.
    """
    if not url or dest.exists():
        return dest.exists()

    for attempt_url in filter(None, [url, fallback_url]):
        try:
            req = urllib.request.Request(attempt_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if len(data) < MIN_IMAGE_SIZE:
                continue
            dest.write_bytes(data)
            return True
        except Exception as e:
            if fallback_url and attempt_url == url:
                continue  # try fallback
            print(f"    [WARN] Download failed {dest.name}: {e}", file=sys.stderr)

    return False


def download_media(ads: list, media_dir: Path):
    """Download image and video creatives for all ads. Adds localImage/localVideo/localPoster/duplicateOf fields.

    Fix 4: 3 workers + jitter to avoid CDN throttling.
    Fix 5: Never delete files — flag duplicates in JSON with duplicateOf instead.
    """
    import hashlib
    import random

    media_dir.mkdir(parents=True, exist_ok=True)

    def _download_ad(ad):
        lib_id = ad.get('libId', 'unknown')
        fmt = ad.get('format', 'Text')
        result = {'localImage': None, 'localVideo': None, 'localPoster': None}

        # Fix 4: small random jitter per worker to stagger requests
        time.sleep(random.uniform(0, DOWNLOAD_JITTER))

        if fmt == 'Image' and ad.get('imageUrl'):
            dest = media_dir / f"{lib_id}.jpg"
            # Pass original (non-upgraded) URL as fallback in case 1080p returns 403
            fallback = ad.get('imageUrlOriginal')
            if download_file(ad['imageUrl'], dest, fallback_url=fallback):
                result['localImage'] = str(dest)

        elif fmt == 'Video':
            if ad.get('videoUrl'):
                dest = media_dir / f"{lib_id}.mp4"
                if download_file(ad['videoUrl'], dest):
                    result['localVideo'] = str(dest)
            if ad.get('posterUrl'):
                dest = media_dir / f"{lib_id}-poster.jpg"
                if download_file(ad['posterUrl'], dest):
                    result['localPoster'] = str(dest)

        return lib_id, result

    print(f"\n  Downloading media for {len(ads)} ads → {media_dir}")
    downloaded = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MEDIA_WORKERS) as executor:
        futures = {executor.submit(_download_ad, ad): ad for ad in ads}
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            lib_id, result = future.result()
            ad = futures[future]
            ad.update(result)
            if result['localImage'] or result['localVideo']:
                downloaded += 1
            if i % 10 == 0:
                print(f"  [{i}/{len(ads)}] {downloaded} files downloaded so far")

    print(f"  Downloaded {downloaded} media files")

    # Fix 5: Flag duplicates in JSON — don't delete files
    # Two ads may share the same creative but have different copy — both files stay
    hash_to_first = {}  # md5 -> libId of first ad seen with this hash
    dupes = 0
    for ad in ads:
        for field, path_val in [('localImage', ad.get('localImage')), ('localVideo', ad.get('localVideo'))]:
            if not path_val:
                continue
            p = Path(path_val)
            if not p.exists():
                continue
            h = hashlib.md5(p.read_bytes()).hexdigest()
            if h in hash_to_first:
                ad['duplicateOf'] = hash_to_first[h]
                dupes += 1
            else:
                hash_to_first[h] = ad['libId']
                ad['duplicateOf'] = None

    if dupes:
        print(f"  Flagged {dupes} duplicate creatives (files kept, duplicateOf field set)")


# ── Main scraper ──────────────────────────────────────────────────────────────
def scrape(brand: str, max_scroll: int = MAX_SCROLL_DEFAULT,
           fetch_landing_pages: bool = False, fetch_media: bool = False,
           filter_domain: str = None, status: str = "active",
           fetch_engagement: bool = False, app_token: str = None,
           page_id: str = None) -> list:
    """Scrape FB Ad Library for brand, return list of ad dicts.

    status: 'active' (default) — only currently running ads
            'all'             — active + inactive/historical ads
    Note: FB Ad Library's 'all' still won't surface the full 50k+ ads visible in
    Meta Ads Manager. That count includes every draft, test, and paused ad ever
    created internally. Ad Library is the public transparency database — it has
    its own retention limits and typically surfaces a few hundred ads max per brand.
    """

    active_status = "all" if status == "all" else "active"
    if page_id:
        # Page-based search: bypasses keyword noise, hits exact advertiser page
        search_url = (
            f"https://www.facebook.com/ads/library/"
            f"?active_status={active_status}&ad_type=all&country=US"
            f"&id={page_id}&is_targeted_country=false&media_type=all"
            f"&search_type=page"
            f"&sort_data%5Bmode%5D=total_impressions&sort_data%5Bdirection%5D=desc"
            f"&view_all_page_id={page_id}"
        )
    else:
        search_url = (
            f"https://www.facebook.com/ads/library/"
            f"?active_status={active_status}&ad_type=all&country=US"
            f"&q={brand.replace(' ', '+')}&search_type=keyword_unordered"
            f"&sort_data%5Bmode%5D=total_impressions&sort_data%5Bdirection%5D=desc"
        )
    # sort_data[mode]=total_impressions is the key:
    # FB sorts cards server-side by total impressions descending.
    # Card position = impression rank. Card #1 = highest spend/reach ad.
    # This IS the engagement signal for US ads — Meta doesn't expose raw impression
    # numbers for non-political US ads, but they do rank by them.
    # The first ~50 cards from a sort_data=total_impressions scrape are the proven winners.
    if status == "all":
        print(f"  [NOTE] Fetching active + inactive/historical ads (active_status=all)")

    print(f"\n[1/5] Opening FB Ad Library: {search_url}")
    run(["open", search_url])
    run(["wait", "--load", "networkidle"])
    time.sleep(2)

    count_text = eval_js(RESULT_COUNT_JS)
    print(f"[2/5] Results header: {count_text or '(not found)'}")

    print(f"[3/5] Scrolling {max_scroll}x to load ads...")
    seen_card_counts = []
    stall_window = 5   # scrolls with no new cards before declaring a true plateau
                       # FB sometimes needs 2-3 extra scrolls to fire the next batch,
                       # so 2 was too aggressive for deep scrapes (50+ scrolls)
    for i in range(max_scroll):
        card_count_raw = eval_js(CARD_COUNT_JS)
        card_count = int(card_count_raw) if card_count_raw.strip().isdigit() else 0
        seen_card_counts.append(card_count)
        print(f"  Scroll {i+1}/{max_scroll} — {card_count} cards loaded")
        run(["scroll", "down", str(SCROLL_PIXELS)])
        time.sleep(SCROLL_PAUSE)
        # True plateau: no new cards for stall_window consecutive scrolls
        if len(seen_card_counts) >= stall_window and len(set(seen_card_counts[-stall_window:])) == 1:
            print(f"  Plateau: no new cards in last {stall_window} scrolls — stopping")
            break

    print(f"[4/5] Extracting ad data...")
    total_raw = eval_js(CARD_COUNT_JS)
    total = int(total_raw) if total_raw.strip().isdigit() else 0
    print(f"  {total} cards to extract...")

    cards = []
    for idx in range(total):
        js = EXTRACT_CARD_AT_INDEX_JS.replace("IDX", str(idx))
        raw = eval_js(js)
        if not raw or raw == 'null':
            continue
        try:
            card = json.loads(raw)
            if card:
                cards.append(card)
        except Exception:
            continue

    print(f"  Extracted {len(cards)} ads")

    # Deduplicate by libId
    seen = set()
    unique_cards = []
    for card in cards:
        if card['libId'] and card['libId'] not in seen:
            seen.add(card['libId'])
            unique_cards.append(card)
    print(f"  {len(unique_cards)} unique ads after dedup")

    # Domain filter — exclude ads that explicitly mention a different domain in copy
    # Ads with no domain mention are kept but flagged as domainUnverified
    if filter_domain:
        domain_clean = filter_domain.lower().replace('https://','').replace('www.','').rstrip('/')
        before = len(unique_cards)
        kept = []
        for c in unique_cards:
            mentioned = c.get('mentionedDomain')
            if mentioned and domain_clean not in mentioned:
                # Explicitly mentions a different domain — exclude
                continue
            c['domainUnverified'] = (mentioned is None)
            kept.append(c)
        removed = before - len(kept)
        unverified = sum(1 for c in kept if c.get('domainUnverified'))
        unique_cards = kept
        if removed:
            print(f"  Filtered {removed} confirmed off-brand ads (domain != {domain_clean})")
        if unverified:
            print(f"  {unverified} ads have no domain in copy — kept but flagged domainUnverified=True")
    else:
        for c in unique_cards:
            c['domainUnverified'] = False

    # Fetch landing pages
    if fetch_landing_pages:
        print(f"\n  Fetching landing pages for {len(unique_cards)} ads...")
        for i, card in enumerate(unique_cards):
            if (card.get('hasDetails') or card.get('hasSummary')) and card['libId']:
                print(f"  [{i+1}/{len(unique_cards)}] {card['libId']}...")
                card['landingPage'] = get_landing_page(card['libId'])
                time.sleep(1)
            else:
                card['landingPage'] = None
    else:
        for card in unique_cards:
            card['landingPage'] = None

    # Download media
    # Fix 3: CDN URLs in imageUrl/videoUrl expire within hours — download NOW in this session.
    # Never rely on re-running from a saved JSON to download later; URLs will be dead.
    if fetch_media:
        slug = re.sub(r'[^a-z0-9]+', '-', brand.lower()).strip('-')
        media_dir = OUTPUT_DIR / slug
        print(f"\n[5/5] Downloading media (must complete this session — URLs expire)...")
        download_media(unique_cards, media_dir)
    else:
        print(f"\n[5/5] Skipping media download (use --media to enable)")
        print(f"  NOTE: imageUrl/videoUrl in JSON will expire within hours — download soon if needed")
        for card in unique_cards:
            card['localImage'] = None
            card['localVideo'] = None
            card['localPoster'] = None
            card['duplicateOf'] = None

    # URL engagement enrichment (genius signal — no post ID needed)
    if fetch_engagement:
        token = app_token or _load_app_token()
        if token:
            enrich_with_url_engagement(unique_cards, token)
        else:
            print("  [WARN] --engagement requires FB app token. Set BB_FACEBOOK_APP_ID + BB_FACEBOOK_APP_SECRET in .env or pass --app-token")

    # Add metadata + impression rank
    # impressionRank = position in the total_impressions-sorted results.
    # Rank 1 = highest impressions = most spend = Meta's own signal that it's their best performer.
    # This is the US equivalent of the EU impressions number — you don't get the count,
    # but you get the ORDER, which is what matters for finding winners.
    for i, card in enumerate(unique_cards):
        card['impressionRank'] = i + 1
        card['scrapedAt'] = datetime.now().isoformat()
        card['brand'] = brand

    # Compute scaling scores
    score_ads(unique_cards)

    return unique_cards


# ── Ad Intelligence Score ────────────────────────────────────────────────────
# Eight signals across 3 tiers, normalized 0–100, weighted into aiScore 0–100.
#
# TIER 1: Platform signals — what Facebook tells us (40%)
#   impressionRank    25%   FB's impression ordering. Steep sqrt curve: top 20 matter most.
#   longevity         15%   Days running. <7d = testing phase (capped at 20).
#                           7d+ scales linearly. Survived the kill = proven creative.
#
# TIER 2: Budget behavior — what the advertiser reveals (30%)
#   compound          10%   Rank × Longevity interaction. Top rank + long running =
#                           disproportionate signal. Geometric mean, sqrt-spread.
#   iteration         10%   Variant count / max. High = actively iterating = budget.
#   funnelCommit      10%   LP cluster density. 25% of all ads → same LP = 100.
#
# TIER 3: Social proof + deployment — what the audience + structure tell us (30%)
#   pageDeploy        10%   How many different pages serve this LP. Multi-page =
#                           scaling across audience segments.
#   engagement        15%   Per-ad LP engagement (LP total / ads on that LP), log-scaled.
#                           Approximates per-ad signal from aggregate data.
#   format             5%   Video = 80, Image = 30. Video signals higher creative intent.
#
# Grade: S=80+, A=70–79, B=55–69, C=40–54, D<40
#
# Also retains legacy scalingScore/scalingGrade for backwards compat.

import math as _math

def score_ads(ads: list) -> list:
    """Compute aiScore (0–100) and grade (S/A/B/C/D) for each ad. Mutates in place."""
    if not ads:
        return ads

    today = datetime.now()

    # ── Pre-compute per-ad raw values ─────────────────────────────────────────
    n = len(ads)

    # Days running
    days_list = []
    for ad in ads:
        try:
            d = datetime.strptime(ad.get('startDate', ''), '%b %d, %Y')
            days_list.append((today - d).days)
        except Exception:
            days_list.append(0)

    # LP cluster density + multi-page deployment
    from collections import Counter, defaultdict
    lp_counts = Counter()
    lp_pages = defaultdict(set)
    for ad in ads:
        lp = (ad.get('landingPage') or 'none').split('?')[0].rstrip('/')
        lp_counts[lp] += 1
        lp_pages[lp].add(ad.get('pageName', ''))

    # Per-ad engagement approximation: LP engagement / ads sharing that LP
    engagement_per_ad = []
    for ad in ads:
        lp = (ad.get('landingPage') or 'none').split('?')[0].rstrip('/')
        ads_on_lp = max(lp_counts[lp], 1)
        s = (ad.get('urlShares') or 0) / ads_on_lp
        r = (ad.get('urlReactions') or 0) / ads_on_lp
        c = (ad.get('urlComments') or 0) / ads_on_lp
        engagement_per_ad.append(s + r + c)

    eng_log = [_math.log1p(e) for e in engagement_per_ad]
    sorted_eng = sorted(eng_log)
    p95_eng = sorted_eng[int(0.95 * len(sorted_eng))] if sorted_eng else 1

    max_days = max(days_list) if days_list else 1
    max_variants = max(ad.get('variants') or 1 for ad in ads)
    max_rank = max(ad.get('impressionRank', 1) for ad in ads)
    max_pages_per_lp = max(len(v) for v in lp_pages.values()) if lp_pages else 1

    # ── Score each ad ─────────────────────────────────────────────────────────
    for i, ad in enumerate(ads):
        rank = ad.get('impressionRank', n)
        days = days_list[i]
        variants = ad.get('variants') or 1
        lp = (ad.get('landingPage') or 'none').split('?')[0].rstrip('/')
        pages_for_lp = len(lp_pages[lp])

        # T1: Impression rank — steep sqrt curve, top ranks matter most
        rank_score = max(0, (1 - (rank / max_rank) ** 0.5)) * 100

        # T1: Longevity — <7d capped at 20 (testing phase), 7d+ scales to 100
        if days < 7:
            longevity_score = (days / 7) * 20
        else:
            longevity_score = 20 + (min(days, max_days) / max_days) * 80

        # T2: Compound rank × longevity — geometric mean, sqrt-spread
        rank_pct = max(0, 1 - rank / max_rank)
        days_pct = min(days / 90, 1.0)  # caps at 90 days
        compound_score = (rank_pct * days_pct) ** 0.5 * 100

        # T2: Creative iteration — variant count normalized
        variant_score = (variants / max_variants) * 100

        # T2: Funnel commitment — LP cluster density
        lp_density = lp_counts[lp] / n
        density_score = min(lp_density * 400, 100)  # 25% density = 100

        # T3: Multi-page deployment
        page_deploy_score = min((pages_for_lp / max(max_pages_per_lp, 1)) * 100, 100)

        # T3: Per-ad engagement — log-scaled, p95-normalized
        eng_score = min(eng_log[i] / max(p95_eng, 0.01), 1.0) * 100

        # T3: Format signal — video = higher intent
        format_score = 80 if ad.get('format') == 'Video' else 30

        # ── Weighted sum ──────────────────────────────────────────────────────
        ai_score = (
            0.25 * rank_score +
            0.15 * longevity_score +
            0.10 * compound_score +
            0.10 * variant_score +
            0.10 * density_score +
            0.10 * page_deploy_score +
            0.15 * eng_score +
            0.05 * format_score
        )
        ai_score = round(min(max(ai_score, 0), 100), 1)

        # ── Grade ─────────────────────────────────────────────────────────────
        if ai_score >= 80:
            grade = 'S'
        elif ai_score >= 70:
            grade = 'A'
        elif ai_score >= 55:
            grade = 'B'
        elif ai_score >= 40:
            grade = 'C'
        else:
            grade = 'D'

        # ── Signal breakdown (human-readable) ─────────────────────────────────
        signals = []
        if rank_score >= 70:
            signals.append(f'rank #{rank}')
        if longevity_score >= 60:
            signals.append(f'{days}d running')
        if variant_score >= 70:
            signals.append(f'{variants} variants')
        if density_score >= 50:
            signals.append(f'{lp_counts[lp]} ads→LP')
        if page_deploy_score >= 50:
            signals.append(f'{pages_for_lp} pages')
        if eng_score >= 60:
            eng_k = engagement_per_ad[i]
            signals.append(f'{eng_k:,.0f} eng/ad')
        signal_str = ' · '.join(signals) if signals else 'low signal'

        # Write new fields
        ad['aiScore'] = ai_score
        ad['aiGrade'] = grade
        ad['aiSignal'] = signal_str
        ad['aiBreakdown'] = {
            'rank': round(rank_score, 1),
            'longevity': round(longevity_score, 1),
            'compound': round(compound_score, 1),
            'iteration': round(variant_score, 1),
            'funnelCommit': round(density_score, 1),
            'pageDeploy': round(page_deploy_score, 1),
            'engagement': round(eng_score, 1),
            'format': round(format_score, 1),
        }
        ad['daysRunning'] = days
        ad['lpClusterCount'] = lp_counts[lp]

        # Legacy compat
        ad['scalingScore'] = ai_score
        ad['scalingGrade'] = grade
        ad['scalingSignal'] = signal_str

    return ads


# ── Output ────────────────────────────────────────────────────────────────────
def save_results(ads: list, brand: str, output_path=None) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if output_path:
        out_file = Path(output_path)
    else:
        slug = re.sub(r'[^a-z0-9]+', '-', brand.lower()).strip('-')
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        out_file = OUTPUT_DIR / f"{slug}-{timestamp}.json"
    with open(out_file, 'w') as f:
        json.dump(ads, f, indent=2)
    return out_file


def print_summary(ads: list):
    print(f"\n{'='*60}")
    print(f"RESULTS: {len(ads)} ads — sorted by total impressions (rank #1 = highest spend)")
    print(f"{'='*60}")
    for i, ad in enumerate(ads[:20], 1):
        lp = f"\n   LP: {ad['landingPage']}" if ad.get('landingPage') else ''
        media = ''
        if ad.get('localImage'):
            media = f"\n   IMG: {Path(ad['localImage']).name}"
        elif ad.get('localVideo'):
            media = f"\n   VID: {Path(ad['localVideo']).name}"
        rank = ad.get('impressionRank', i)
        eng = ''
        if ad.get('urlShares') or ad.get('urlReactions'):
            eng = f"\n   ENGAGEMENT: {ad.get('urlReactions',0):,} reactions | {ad.get('urlShares',0):,} shares | {ad.get('urlComments',0):,} comments"
        print(
            f"\n[#{rank}] {ad['status']} | {ad['pageName']} | {ad['format']} | "
            f"Started: {ad['startDate']} | Variants: {ad['variants']}\n"
            f"   ID: {ad['libId']}\n"
            f"   {ad['copy'][:200].replace(chr(10), ' ')}"
            f"{eng}{lp}{media}"
        )
    if len(ads) > 20:
        print(f"\n... and {len(ads) - 20} more (see JSON output)")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Scrape FB Ad Library for a brand')
    parser.add_argument('brand', help='Brand name or keyword to search')
    parser.add_argument('--max-scroll', type=int, default=MAX_SCROLL_DEFAULT,
                        help=f'Max scroll attempts (default: {MAX_SCROLL_DEFAULT})')
    parser.add_argument('--landing-pages', action='store_true',
                        help='Fetch landing page URLs (slow — one browser load per ad)')
    parser.add_argument('--media', action='store_true',
                        help='Download image and video creatives')
    parser.add_argument('--domain', type=str, default=None,
                        help='Filter to ads mentioning this domain in copy (e.g. mellowsleep.com)')
    parser.add_argument('--status', type=str, default='active', choices=['active', 'all'],
                        help='active (default) = only running ads; all = include inactive/historical')
    parser.add_argument('--engagement', action='store_true',
                        help='Fetch FB URL engagement (reactions/shares/comments) for each landing page. '
                             'Requires --landing-pages. Uses FB Graph API — no post ID needed.')
    parser.add_argument('--app-token', type=str, default=None,
                        help='FB App token (APP_ID|APP_SECRET). Auto-loaded from .env if not provided.')
    parser.add_argument('--output', type=str, default=None,
                        help='Output JSON path (default: auto-named in ad-swipe-file/scraped/)')
    parser.add_argument('--page-id', type=str, default=None,
                        help='FB page ID for exact page-based search (bypasses keyword noise). '
                             'Find it in the Ad Library URL after searching the brand by name.')
    args = parser.parse_args()

    ads = scrape(
        args.brand,
        max_scroll=args.max_scroll,
        fetch_landing_pages=args.landing_pages,
        fetch_media=args.media,
        filter_domain=args.domain,
        status=args.status,
        fetch_engagement=args.engagement,
        app_token=args.app_token,
        page_id=getattr(args, 'page_id', None),
    )

    if not ads:
        print("No ads found.", file=sys.stderr)
        sys.exit(1)

    out_file = save_results(ads, args.brand, args.output)
    print_summary(ads)
    print(f"\nSaved {len(ads)} ads → {out_file}")
