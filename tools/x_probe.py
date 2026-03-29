#!/usr/bin/env python3
"""X Defence Probe — captures what X checks during login.

Monitors:
1. Network requests to anti-fraud/telemetry endpoints
2. JS API probes (canvas, WebGL, navigator, fonts, etc.)
3. Cookie/storage writes
4. What changes between "clean visit" and "login attempt"

Usage:
    python3 tools/x_probe.py                # Just visit, don't log in
    python3 tools/x_probe.py --login        # Visit + attempt login
    python3 tools/x_probe.py --compare      # Run both, diff the results
"""

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("x_probe")

# X's known anti-fraud / telemetry domains and paths
FRAUD_PATTERNS = [
    "client_event", "jot", "telemetry", "csp_report",
    "account/access", "1.1/onboarding", "live_pipeline",
    "i/api/1.1", "i/api/graphql", "i/api/2",
    "arkose", "funcaptcha",  # Arkose Labs (X's CAPTCHA provider)
]

# Browser APIs that fingerprinting scripts typically probe
PROBE_JS = """() => {
    const probes = {};

    // What navigator properties are accessed
    const navProps = [
        'userAgent', 'platform', 'language', 'languages', 'hardwareConcurrency',
        'deviceMemory', 'maxTouchPoints', 'vendor', 'appVersion', 'oscpu',
        'cookieEnabled', 'doNotTrack', 'webdriver', 'pdfViewerEnabled',
        'connection', 'plugins', 'mimeTypes', 'buildID', 'product',
        'productSub', 'vendorSub', 'userAgentData', 'scheduling',
        'globalPrivacyControl', 'storage'
    ];
    probes.navigator = {};
    for (const prop of navProps) {
        try {
            const val = navigator[prop];
            if (val !== undefined) {
                if (typeof val === 'object' && val !== null) {
                    probes.navigator[prop] = JSON.stringify(val).substring(0, 200);
                } else {
                    probes.navigator[prop] = String(val);
                }
            }
        } catch(e) {
            probes.navigator[prop] = 'ERROR: ' + e.message;
        }
    }

    // WebGL fingerprint
    try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (gl) {
            probes.webgl = {
                vendor: gl.getParameter(gl.VENDOR),
                renderer: gl.getParameter(gl.RENDERER),
                version: gl.getParameter(gl.VERSION),
                shadingVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
                unmaskedVendor: '',
                unmaskedRenderer: '',
            };
            const dbg = gl.getExtension('WEBGL_debug_renderer_info');
            if (dbg) {
                probes.webgl.unmaskedVendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
                probes.webgl.unmaskedRenderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
            }
        } else {
            probes.webgl = 'NOT_AVAILABLE';
        }
    } catch(e) {
        probes.webgl = 'ERROR: ' + e.message;
    }

    // Canvas fingerprint
    try {
        const canvas = document.createElement('canvas');
        canvas.width = 200;
        canvas.height = 50;
        const ctx = canvas.getContext('2d');
        ctx.textBaseline = 'top';
        ctx.font = '14px Arial';
        ctx.fillStyle = '#f60';
        ctx.fillRect(125, 1, 62, 20);
        ctx.fillStyle = '#069';
        ctx.fillText('Fantoma probe', 2, 15);
        ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
        ctx.fillText('Fantoma probe', 4, 17);
        probes.canvas_hash = canvas.toDataURL().length;
        probes.canvas_supported = true;
    } catch(e) {
        probes.canvas_supported = false;
    }

    // AudioContext fingerprint
    try {
        const ac = new (window.AudioContext || window.webkitAudioContext)();
        probes.audio = {
            sampleRate: ac.sampleRate,
            state: ac.state,
            baseLatency: ac.baseLatency,
            outputLatency: ac.outputLatency,
            channelCount: ac.destination.channelCount,
        };
        ac.close();
    } catch(e) {
        probes.audio = 'ERROR: ' + e.message;
    }

    // Screen properties
    probes.screen = {
        width: screen.width,
        height: screen.height,
        availWidth: screen.availWidth,
        availHeight: screen.availHeight,
        colorDepth: screen.colorDepth,
        pixelDepth: screen.pixelDepth,
        devicePixelRatio: window.devicePixelRatio,
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        outerWidth: window.outerWidth,
        outerHeight: window.outerHeight,
    };

    // Timezone
    probes.timezone = {
        offset: new Date().getTimezoneOffset(),
        name: Intl.DateTimeFormat().resolvedOptions().timeZone,
        locale: Intl.DateTimeFormat().resolvedOptions().locale,
    };

    // Font detection (check for common fonts)
    try {
        const testFonts = ['Arial', 'Helvetica', 'Times New Roman', 'Courier New',
                           'Georgia', 'Verdana', 'Comic Sans MS', 'Impact',
                           'Lucida Console', 'Tahoma', 'Trebuchet MS'];
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        const baseline = ctx.measureText('mmmmmmmmli').width;
        ctx.font = '72px monospace';
        const baseWidth = ctx.measureText('mmmmmmmmli').width;
        probes.fonts = {};
        for (const font of testFonts) {
            ctx.font = '72px "' + font + '", monospace';
            const width = ctx.measureText('mmmmmmmmli').width;
            probes.fonts[font] = width !== baseWidth;
        }
    } catch(e) {
        probes.fonts = 'ERROR: ' + e.message;
    }

    // Permissions API
    try {
        probes.permissions_api = typeof navigator.permissions !== 'undefined';
    } catch(e) {
        probes.permissions_api = false;
    }

    // WebRTC
    try {
        probes.webrtc = typeof RTCPeerConnection !== 'undefined';
    } catch(e) {
        probes.webrtc = false;
    }

    // Battery API
    try {
        probes.battery_api = typeof navigator.getBattery !== 'undefined';
    } catch(e) {
        probes.battery_api = false;
    }

    // Automation markers
    probes.automation = {
        webdriver: navigator.webdriver,
        __webdriver_evaluate: typeof window.__webdriver_evaluate !== 'undefined',
        __selenium_unwrapped: typeof window.__selenium_unwrapped !== 'undefined',
        __fxdriver_unwrapped: typeof window.__fxdriver_unwrapped !== 'undefined',
        _phantom: typeof window._phantom !== 'undefined',
        callPhantom: typeof window.callPhantom !== 'undefined',
        domAutomation: typeof window.domAutomation !== 'undefined',
        __nightmare: typeof window.__nightmare !== 'undefined',
        cdc_adoQpoasnfa76pfcZLmcfl: typeof document.cdc_adoQpoasnfa76pfcZLmcfl !== 'undefined',
    };

    // Check if specific properties are overridden (fingerprint spoofing detection)
    probes.spoofing = {};
    try {
        const desc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
        probes.spoofing.webdriver_descriptor = desc ? JSON.stringify({
            configurable: desc.configurable,
            enumerable: desc.enumerable,
            get: desc.get ? desc.get.toString().substring(0, 100) : null,
        }) : 'no descriptor';
    } catch(e) {
        probes.spoofing.webdriver_descriptor = 'ERROR';
    }

    // Performance.now() resolution (fingerprinting countermeasure detection)
    try {
        const times = [];
        for (let i = 0; i < 20; i++) {
            times.push(performance.now());
        }
        const diffs = [];
        for (let i = 1; i < times.length; i++) {
            diffs.push(times[i] - times[i-1]);
        }
        probes.performance_resolution = Math.min(...diffs.filter(d => d > 0));
    } catch(e) {
        probes.performance_resolution = 'ERROR';
    }

    return probes;
}"""


def run_probe(headless="virtual", do_login=False, profile_dir=None):
    """Run the probe against X and capture all signals."""
    from fantoma.browser.engine import BrowserEngine

    results = {
        "timestamp": datetime.now().isoformat(),
        "headless": str(headless),
        "login_attempted": do_login,
        "network_requests": [],
        "fraud_requests": [],
        "browser_fingerprint": {},
        "cookies_before": [],
        "cookies_after": [],
        "console_messages": [],
    }

    browser = BrowserEngine(
        headless=headless,
        profile_dir=profile_dir,
        browser_engine="camoufox",
    )
    browser.start()
    page = browser.get_page()

    # Capture console messages (X sometimes logs detection info)
    page.on("console", lambda msg: results["console_messages"].append({
        "type": msg.type,
        "text": msg.text[:500],
    }))

    # Capture ALL network requests
    def on_request(request):
        entry = {
            "url": request.url[:300],
            "method": request.method,
            "resource_type": request.resource_type,
            "headers": {k: v[:200] for k, v in list(request.headers.items())[:20]},
        }
        # Check POST body for telemetry data
        if request.method == "POST":
            try:
                body = request.post_data
                if body:
                    entry["post_body_preview"] = body[:1000]
            except Exception:
                pass

        results["network_requests"].append(entry)

        # Flag fraud/telemetry requests
        url_lower = request.url.lower()
        if any(p in url_lower for p in FRAUD_PATTERNS):
            entry["flagged"] = True
            results["fraud_requests"].append(entry)

    page.on("request", on_request)

    # Navigate to X login
    log.info("Navigating to x.com/i/flow/login...")
    page.goto("https://x.com/i/flow/login", wait_until="networkidle", timeout=30000)
    time.sleep(5)

    # Capture cookies before login
    ctx = page.context
    results["cookies_before"] = [
        {"name": c["name"], "domain": c["domain"], "path": c["path"],
         "secure": c["secure"], "httpOnly": c["httpOnly"],
         "sameSite": c.get("sameSite", ""),
         "value_length": len(c.get("value", ""))}
        for c in ctx.cookies()
    ]

    # Run fingerprint probe
    log.info("Running browser fingerprint probe...")
    results["browser_fingerprint"] = page.evaluate(PROBE_JS)

    if do_login:
        config = json.load(open(os.path.expanduser("~/.config/xbot/config.json")))
        email = config.get("x_email", "")

        log.info("Attempting login with email: %s***", email[:3])

        # Type email
        text_input = page.locator('input[name="text"]')
        if text_input.count() > 0:
            text_input.click()
            time.sleep(0.5)
            for char in email:
                page.keyboard.type(char)
                time.sleep(0.08)
            time.sleep(1)

            # Click Next
            next_btn = page.locator('button:has-text("Next")')
            if next_btn.count() > 0:
                next_btn.click()
                log.info("Clicked Next, waiting for response...")
                time.sleep(8)

                # Capture what page shows after Next
                results["post_login_text"] = page.inner_text("body")[:2000]
                results["post_login_url"] = page.url

        # Capture cookies after login attempt
        results["cookies_after"] = [
            {"name": c["name"], "domain": c["domain"], "path": c["path"],
             "secure": c["secure"], "httpOnly": c["httpOnly"],
             "sameSite": c.get("sameSite", ""),
             "value_length": len(c.get("value", ""))}
            for c in ctx.cookies()
        ]
    else:
        time.sleep(3)

    # Wait a bit more to capture delayed telemetry
    time.sleep(5)

    browser.stop()
    return results


def print_report(results):
    """Print a readable summary of what X is checking."""
    print("\n" + "=" * 70)
    print(f"X DEFENCE PROBE — {results['timestamp']}")
    print(f"Headless: {results['headless']} | Login: {results['login_attempted']}")
    print("=" * 70)

    # Network summary
    total = len(results["network_requests"])
    fraud = len(results["fraud_requests"])
    print(f"\nNetwork requests: {total} total, {fraud} flagged as anti-fraud/telemetry")

    if fraud > 0:
        print("\n--- Anti-fraud / telemetry requests ---")
        for req in results["fraud_requests"]:
            print(f"  {req['method']} {req['url'][:120]}")
            if req.get("post_body_preview"):
                # Try to parse and summarise
                body = req["post_body_preview"]
                print(f"    Body ({len(body)} chars): {body[:200]}...")

    # Request types breakdown
    types = {}
    for req in results["network_requests"]:
        rt = req["resource_type"]
        types[rt] = types.get(rt, 0) + 1
    print(f"\nRequest types: {dict(sorted(types.items(), key=lambda x: -x[1]))}")

    # Domains contacted
    domains = set()
    for req in results["network_requests"]:
        try:
            from urllib.parse import urlparse
            d = urlparse(req["url"]).netloc
            if d:
                domains.add(d)
        except Exception:
            pass
    print(f"\nDomains contacted ({len(domains)}):")
    for d in sorted(domains):
        count = sum(1 for r in results["network_requests"] if d in r["url"])
        print(f"  {d} ({count} requests)")

    # Browser fingerprint
    fp = results["browser_fingerprint"]
    print("\n--- Browser fingerprint ---")

    nav = fp.get("navigator", {})
    print(f"  User-Agent: {nav.get('userAgent', '?')[:80]}")
    print(f"  Platform: {nav.get('platform', '?')}")
    print(f"  Vendor: {nav.get('vendor', '?')}")
    print(f"  Language: {nav.get('language', '?')}")
    print(f"  HW Concurrency: {nav.get('hardwareConcurrency', '?')}")
    print(f"  Device Memory: {nav.get('deviceMemory', '?')}")
    print(f"  Max Touch: {nav.get('maxTouchPoints', '?')}")
    print(f"  Webdriver: {nav.get('webdriver', '?')}")
    print(f"  buildID: {nav.get('buildID', '?')}")

    webgl = fp.get("webgl", {})
    if isinstance(webgl, dict):
        print(f"\n  WebGL vendor: {webgl.get('vendor', '?')}")
        print(f"  WebGL renderer: {webgl.get('renderer', '?')}")
        print(f"  Unmasked vendor: {webgl.get('unmaskedVendor', '?')}")
        print(f"  Unmasked renderer: {webgl.get('unmaskedRenderer', '?')}")

    screen = fp.get("screen", {})
    print(f"\n  Screen: {screen.get('width')}x{screen.get('height')}")
    print(f"  Avail: {screen.get('availWidth')}x{screen.get('availHeight')}")
    print(f"  Window: {screen.get('innerWidth')}x{screen.get('innerHeight')}")
    print(f"  Outer: {screen.get('outerWidth')}x{screen.get('outerHeight')}")
    print(f"  DPR: {screen.get('devicePixelRatio')}")
    print(f"  Color depth: {screen.get('colorDepth')}")

    tz = fp.get("timezone", {})
    print(f"\n  Timezone: {tz.get('name')} (offset {tz.get('offset')})")
    print(f"  Locale: {tz.get('locale')}")

    print(f"\n  Canvas supported: {fp.get('canvas_supported')}")
    print(f"  Canvas hash length: {fp.get('canvas_hash')}")
    print(f"  WebRTC: {fp.get('webrtc')}")
    print(f"  Battery API: {fp.get('battery_api')}")
    print(f"  Perf resolution: {fp.get('performance_resolution')}")

    auto = fp.get("automation", {})
    print(f"\n--- Automation markers ---")
    for k, v in auto.items():
        flag = " <<<" if v else ""
        print(f"  {k}: {v}{flag}")

    spoof = fp.get("spoofing", {})
    print(f"\n--- Spoofing detection ---")
    for k, v in spoof.items():
        print(f"  {k}: {v}")

    fonts = fp.get("fonts", {})
    if isinstance(fonts, dict):
        detected = [f for f, v in fonts.items() if v]
        print(f"\n  Fonts detected: {len(detected)}/{len(fonts)} — {', '.join(detected)}")

    # Cookies
    cookies = results.get("cookies_before", [])
    print(f"\n--- Cookies ({len(cookies)}) ---")
    for c in cookies:
        print(f"  {c['name']} ({c['domain']}) — {c['value_length']} chars, "
              f"secure={c['secure']}, httpOnly={c['httpOnly']}, sameSite={c['sameSite']}")

    if results.get("post_login_text"):
        print(f"\n--- Post-login page ---")
        print(f"  URL: {results.get('post_login_url', '?')}")
        print(f"  Text: {results['post_login_text'][:300]}")

        new_cookies = results.get("cookies_after", [])
        old_names = {c["name"] for c in cookies}
        new = [c for c in new_cookies if c["name"] not in old_names]
        if new:
            print(f"\n  New cookies after login ({len(new)}):")
            for c in new:
                print(f"    {c['name']} ({c['domain']}) — {c['value_length']} chars")

    # Console messages
    console = results.get("console_messages", [])
    if console:
        print(f"\n--- Console messages ({len(console)}) ---")
        for msg in console[:20]:
            print(f"  [{msg['type']}] {msg['text'][:150]}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Probe X's anti-fraud defences")
    parser.add_argument("--login", action="store_true", help="Attempt login after probing")
    parser.add_argument("--compare", action="store_true", help="Run both visit-only and login, diff results")
    parser.add_argument("--headless", default="virtual", help="Headless mode: True, False, or virtual")
    parser.add_argument("--profile", default=None, help="Browser profile directory")
    parser.add_argument("--save", default=None, help="Save raw JSON to file")
    args = parser.parse_args()

    headless = args.headless
    if headless == "True":
        headless = True
    elif headless == "False":
        headless = False

    if args.compare:
        log.info("=== RUN 1: Visit only (no login) ===")
        r1 = run_probe(headless=headless, do_login=False, profile_dir=args.profile)
        print_report(r1)

        log.info("\n=== RUN 2: Visit + login attempt ===")
        r2 = run_probe(headless=headless, do_login=True, profile_dir=args.profile)
        print_report(r2)

        # Diff
        print("\n" + "=" * 70)
        print("COMPARISON")
        print("=" * 70)
        print(f"Visit-only requests: {len(r1['network_requests'])}")
        print(f"Login requests: {len(r2['network_requests'])}")
        print(f"Extra requests on login: {len(r2['network_requests']) - len(r1['network_requests'])}")

        # Find domains only in login attempt
        d1 = {urlparse(r["url"]).netloc for r in r1["network_requests"]}
        d2 = {urlparse(r["url"]).netloc for r in r2["network_requests"]}
        new_domains = d2 - d1
        if new_domains:
            print(f"New domains on login: {', '.join(sorted(new_domains))}")

        if args.save:
            with open(args.save, "w") as f:
                json.dump({"visit_only": r1, "with_login": r2}, f, indent=2)
            log.info(f"Saved to {args.save}")
    else:
        results = run_probe(headless=headless, do_login=args.login, profile_dir=args.profile)
        print_report(results)

        if args.save:
            with open(args.save, "w") as f:
                json.dump(results, f, indent=2)
            log.info(f"Saved to {args.save}")


if __name__ == "__main__":
    from urllib.parse import urlparse
    main()
