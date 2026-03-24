# Fantoma-AI Competitive Analysis (March 2026)

## Fantoma's Current Advantages

| Feature | Fantoma | browser-use (82K) | Stagehand (21K) | Skyvern (20K) | LaVague (6K) |
|---------|---------|-------------------|-----------------|---------------|--------------|
| Anti-detection (Camoufox) | Built-in, free | Cloud only (paid) | Cloud only | Cloud only | None |
| DOM-first (no vision needed) | Yes | Partial | Yes | No (vision-first) | Partial |
| Works with 8B local models | Yes (tested) | Technically yes | No | No | No |
| Model escalation chain | Yes (unique) | No | No | No | No |
| Checkpoint/rollback | Yes (unique) | No | No | No | No |
| CAPTCHA solving (3 tiers) | Free + API + human | Cloud only | Cloud only | Cloud only | None |
| Cookie consent auto-dismiss | Yes | No | No | No | No |
| Smart element cap (15) | Yes | No | No | No | No |
| Autocomplete handler (spatial) | Yes | No | No | No | No |
| Reactive mode (no planner) | Yes | No | No | No | No |

## Gaps to Fill

### v0.1.0 (before launch)
1. **Accessibility mode** — emulate assistive tech, use ARIA accessibility tree for DOM extraction. Legal protection (sites can't block assistive tech without risking ADA/WCAG/Equality Act violations). Better structured data. Nobody else does this.
2. **VPN/proxy support** — `proxy="socks5://vpn:1080"` routes through VPN. Triggers CAPTCHAs (validates our solvers), provides IP rotation, more realistic stealth profile. Camoufox already supports proxy params. Build alongside accessibility mode — both change how Fantoma presents to websites.
3. **Structured extraction** — `agent.extract("products", schema={"name": str, "price": float})` returns validated JSON. Table stakes for scraping use cases.
4. **Weekly anti-detection monitor** — automated weekly test against 20 bot-protected sites. Compares against baseline, alerts on regressions via Telegram. "Nike started blocking Fantoma — investigate." Runs on M5 via systemd timer (Sunday 02:30). Companies will build countermeasures once Fantoma is public — this detects them early. Essential for a product that claims anti-detection.

### v0.2.0 (post-launch)
3. **Proxy rotation** — Camoufox supports proxy config. Add `proxy_rotation=["http://proxy1", "http://proxy2"]` to rotate per session. Solves IP rate limiting (Reddit is the only site that blocks, and it's IP-based not fingerprint-based — proven by 8-hour stress test: 422 tests, only Reddit blocked, all after 2+ hours from same IP).
4. **Action caching / deterministic replay** — first run uses LLM, repeat runs replay cached paths. 80% cost reduction. Self-healing when selectors break. (Stagehand has this.)
5. **REST API** — `POST /run` with task description. Makes Fantoma usable as a service.

### v0.3.0 (growth)
5. **Parallel multi-agent** — multiple tabs/instances for 10x speed. (browser-use, Skyvern have this.)
6. **Credential vault** — Bitwarden/1Password integration, passwords never exposed to LLM. (Skyvern has this.)
7. **TOTP/2FA code generation** — for authenticated workflows.

### Watch (not build yet)
- **WebMCP** — W3C standard, Chrome 146. Sites expose structured tools to agents. Too early.
- **Fine-tuned browser model** — browser-use is working on this. Massive effort.
- **Visual workflow builder** — wrong audience for a dev-first tool.

## Accessibility Mode Design Notes

The ARIA accessibility tree is a cleaner representation than raw DOM:
- Websites are legally required to support it (WCAG 2.1, ADA, Equality Act 2010)
- ARIA roles, labels, and states give structured element descriptions
- Screen readers already work this way — Fantoma would use the same interface
- Playwright has `page.accessibility.snapshot()` built in
- Setting `prefers-reduced-motion` and screen-reader flags prevents animation interference

Implementation:
- Use `page.accessibility.snapshot()` instead of manual DOM extraction
- Set Camoufox to present as assistive technology
- ARIA tree gives: role, name, value, description, focusable, checked, disabled
- Produces cleaner numbered element lists with less noise
- Falls back to DOM extraction only when ARIA tree is empty

## Test Results Summary (for context)

- 6 LLMs tested (122B, 45B, 8B local + Claude, Kimi, GPT-4o-mini cloud)
- 10/10 anti-detection on bot-protected sites
- 95% pass rate across all models
- 8-hour stress tests running against 20 sites (results pending)

## Real-World API Costs (measured during stress testing)

| Provider | Model | Tests | Total Cost | Per Task | Speed |
|----------|-------|-------|-----------|----------|-------|
| Local (Homer 122B) | Qwen3.5-122B | 8 | £0 | Free | 20-70s |
| Local (Hercules 45B) | Qwen3-Coder | 3 | £0 | Free | 27-47s |
| Local (Llama 8B) | Llama-3.1-8B | 5 | £0 | Free | 5-7s |
| Kimi | moonshot-v1-auto | 178 | $0.45 | **$0.003** | 8-16s |
| OpenAI | gpt-4o-mini | 3+ | TBD | ~$0.01 | 9-13s |
| Anthropic | Claude Sonnet | 260 | £4.20 | **£0.016** | 14-58s |

**Key finding: Kimi is 50x cheaper than Claude with comparable results.**

### Cost guide for README
- **Free**: Use a local model (7B+ via Ollama, llama.cpp, vLLM). Works on 8GB+ RAM.
- **Budget** ($0.003/task): Kimi moonshot-v1-auto. Best value for money.
- **Mid-range** (~$0.01/task): GPT-4o-mini or DeepSeek. Fast and reliable.
- **Premium** (~$0.02/task): Claude Sonnet. Most reliable instruction following.
- **Pick by use case**: scraping 1000 pages → Kimi ($3) or local (free). Complex login flows → Claude ($20) for reliability.
