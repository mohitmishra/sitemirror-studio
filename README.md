# SiteMirror Studio v2.3

## Card clarity + contact discovery + typed-domain input

This release keeps the v2.2 cancellation/stability fixes and improves the Website object UX.

### Recent project cards

- Completed/failed/stopped status is displayed only once per card.
- The status/progress object moved to the lower metadata row, leaving more width for the website title.
- Website titles can use up to two lines instead of being forced into a single ellipsis.
- Domains use a stable one-line ellipsis and retain the complete value in the tooltip.
- Existing in-place polling remains, so favicons are not reloaded on every active-project refresh.

### Contact discovery

During the crawl SiteMirror now extracts and de-duplicates:

- email addresses from `mailto:` links, JSON-LD / ContactPoint data and visible page text;
- phone numbers from `tel:` links, JSON-LD / ContactPoint data and visible page text.

The Website object shows discovered emails and phone numbers with hover provenance. Page details also show contact values found on that specific page.

Exports now include:

- `contacts.json`
- `contacts.csv`

Each aggregated contact records the discovery source, source pages and number of crawled pages where it appeared.

### Typed domains automatically become URLs

Users can type any of the following:

- `example.com`
- `www.example.com`
- `example.com/contact`
- `https://example.com`
- `http://example.com`

When no scheme is supplied, SiteMirror automatically uses `https://` before creating the crawl. The backend validates this as well, so direct API usage behaves the same as the UI.

## Protected websites

The v2.0 single dedicated browser-session model remains unchanged:

1. direct discovery/HTTP first;
2. one isolated crawler browser only when needed;
3. verify once in that exact session;
4. reuse that same session for every remaining page;
5. close the temporary browser/profile when the job ends.

## Mac installation

Run once:

```bash
./INSTALL-MAC-APP.command
```

Then launch:

`~/Applications/SiteMirror Studio.app`

## Storage

Projects:

`~/Library/Application Support/SiteMirror Studio/data/`

Exports:

`~/Library/Application Support/SiteMirror Studio/exports/`

Logs:

`~/Library/Logs/SiteMirror Studio/`


## v2.5 — original-script browser handoff

- Protected sites now use the same handoff pattern as the original working Python/BAT scraper: launch the real browser first, wait for the user to confirm the real site is visible, then attach to that exact process/profile/tab.
- No browser extension or profile-to-profile cookie transfer is used.
- Direct discovery stops after robots/homepage on a WAF/CDN block instead of sending a burst of guessed sitemap/feed requests.
- Target-tab selection avoids accidentally controlling `about:blank`.
- Browser-side `fetch()` recovers robots/sitemaps in the accepted session.
- Failed recent-project cards display the concrete failure reason.

## v2.4 — CDN/WAF fallback

- Classifies static CloudFront/Akamai/Fastly-style access denials separately from interactive human-verification pages.
- On a direct 401/403/429/503 edge denial, SiteMirror launches one dedicated real browser directly at the target URL before attaching crawler control.
- If the page loads there, the exact same browser profile/context is reused for the entire crawl.
- Static CDN denial pages no longer wait in a fake verification loop.
- If the dedicated browser itself is denied, SiteMirror reports the CDN/WAF denial clearly and preserves discovery data collected so far.
