# SiteMirror Studio v2.5 Validation

Validated in the build environment:

- Python source compilation: PASS
- Frontend JavaScript syntax: PASS
- FastAPI startup/health: PASS (`2.5.0`)
- CloudFront-style discovery short-circuit fixture: PASS
  - only `/robots.txt` and homepage were requested directly
  - `edge_block=true`, provider `CloudFront`
  - no common sitemap/feed request burst before browser handoff
- Dedicated-browser target-tab selection fixture: PASS
  - matching website tab selected instead of `about:blank`
- Original-script style browser handoff fixture: PASS
  - browser launch enters `browser_waiting_user`
  - crawler does not attach until user confirmation
  - same pending browser process is attached after confirmation
- `/api/projects/{pid}/browser-ready` endpoint: PASS
- Browser-side sitemap recovery path now prefers same-origin page `fetch()` over Playwright APIRequestContext on protected sites
- Existing URL normalization/contact/history/stop logic retained

Live CDN/WAF behavior varies by network, region and site policy and cannot be fully reproduced from the isolated build container. v2.5 therefore surfaces the concrete failure reason if the dedicated real browser itself is still denied.
