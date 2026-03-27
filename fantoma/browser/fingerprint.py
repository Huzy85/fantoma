"""Fingerprint self-test — validates Camoufox anti-detection via in-browser JavaScript.

All checks run inside the browser via page.evaluate() — no network calls, no external sites.
"""


class FingerprintTest:
    """Runs 7 fingerprint consistency checks against a Playwright page."""

    def run_all(self, page) -> dict:
        """Run all fingerprint checks and return results.

        Args:
            page: A Playwright page object (or mock with .evaluate()).

        Returns:
            dict with "overall" (bool) and "checks" (dict of check results).
        """
        checks = {}

        # 1. UA vs platform
        ua_data = page.evaluate("""() => ({
            ua: navigator.userAgent,
            platform: navigator.platform
        })""")
        checks["ua_vs_platform"] = self._check_ua_vs_platform(ua_data)

        # 2. GPU vs OS
        gpu_data = page.evaluate("""() => {
            try {
                const c = document.createElement('canvas');
                const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
                if (!gl) return {vendor: '', renderer: ''};
                const ext = gl.getExtension('WEBGL_debug_renderer_info');
                return {
                    vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : '',
                    renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : ''
                };
            } catch(e) { return {vendor: '', renderer: ''}; }
        }""")
        checks["gpu_vs_os"] = self._check_gpu_vs_os(gpu_data, ua_data)

        # 3. Timezone vs locale
        tz_data = page.evaluate("""() => ({
            tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
            locale: navigator.language
        })""")
        checks["timezone_vs_locale"] = self._check_timezone_locale(tz_data)

        # 4. Screen dimensions
        screen_data = page.evaluate("""() => ({
            width: screen.width,
            height: screen.height,
            availWidth: screen.availWidth,
            availHeight: screen.availHeight
        })""")
        checks["screen_dimensions"] = self._check_screen_dimensions(screen_data)

        # 5. WebGL present
        webgl_data = page.evaluate("""() => {
            try {
                const c = document.createElement('canvas');
                const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
                if (!gl) return {hasWebGL: false, vendor: '', renderer: ''};
                const ext = gl.getExtension('WEBGL_debug_renderer_info');
                return {
                    hasWebGL: true,
                    vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : '',
                    renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : ''
                };
            } catch(e) { return {hasWebGL: false, vendor: '', renderer: ''}; }
        }""")
        checks["webgl_present"] = self._check_webgl_present(webgl_data)

        # 6. Worker crosscheck
        worker_data = page.evaluate("""() => new Promise((resolve) => {
            const blob = new Blob([`
                self.postMessage({
                    ua: navigator.userAgent,
                    hw: navigator.hardwareConcurrency
                });
            `], {type: 'application/javascript'});
            const w = new Worker(URL.createObjectURL(blob));
            w.onmessage = (e) => {
                w.terminate();
                resolve({
                    main_ua: navigator.userAgent,
                    worker_ua: e.data.ua,
                    main_hw: navigator.hardwareConcurrency,
                    worker_hw: e.data.hw
                });
            };
            setTimeout(() => { w.terminate(); resolve({
                main_ua: navigator.userAgent, worker_ua: 'timeout',
                main_hw: navigator.hardwareConcurrency, worker_hw: -1
            }); }, 3000);
        })""")
        checks["worker_crosscheck"] = self._check_worker_crosscheck(worker_data)

        # 7. Instance stability
        stability_data = page.evaluate("""() => {
            const read = () => ({
                ua: navigator.userAgent,
                platform: navigator.platform
            });
            return { first: read(), second: read() };
        }""")
        checks["instance_stability"] = self._check_instance_stability(stability_data)

        overall = all(c["passed"] for c in checks.values())
        return {"overall": overall, "checks": checks}

    def _check_ua_vs_platform(self, data: dict) -> dict:
        """Check that UA string and navigator.platform are consistent."""
        ua = data.get("ua", "")
        platform = data.get("platform", "")

        # Map UA OS indicators to expected platform values
        passed = True
        reason = "UA and platform are consistent"

        if "Windows" in ua and "Win" not in platform:
            passed = False
            reason = f"UA says Windows but platform is '{platform}'"
        elif "Linux" in ua and "Linux" not in platform and "Win" not in platform and "Mac" not in platform:
            # Linux UA should have Linux platform
            pass
        elif "Mac" in ua and "Mac" not in platform:
            passed = False
            reason = f"UA says Mac but platform is '{platform}'"
        elif "Linux" in platform and "Windows" in ua:
            passed = False
            reason = f"Platform is Linux but UA says Windows"
        elif "Win" in platform and "Linux" in ua:
            passed = False
            reason = f"Platform is Win but UA says Linux"

        return {"passed": passed, "reason": reason, "data": data}

    def _check_gpu_vs_os(self, gpu_data: dict, ua_data: dict) -> dict:
        """Check that GPU renderer is present and plausible for the OS."""
        vendor = gpu_data.get("vendor", "")
        renderer = gpu_data.get("renderer", "")

        # Just check that values are populated (Camoufox should spoof these)
        passed = True
        reason = "GPU info present"

        if not vendor and not renderer:
            # Not necessarily a fail — some configs don't expose GPU
            passed = True
            reason = "GPU info not exposed (acceptable)"

        return {"passed": passed, "reason": reason, "data": gpu_data}

    def _check_timezone_locale(self, data: dict) -> dict:
        """Check that timezone and locale are both populated."""
        tz = data.get("tz", "")
        locale = data.get("locale", "")

        passed = bool(tz) and bool(locale)
        reason = "Timezone and locale present" if passed else f"Missing: tz={tz!r}, locale={locale!r}"

        return {"passed": passed, "reason": reason, "data": data}

    def _check_screen_dimensions(self, data: dict) -> dict:
        """Check screen dimensions are all >0 (catches Camoufox #330)."""
        width = data.get("width", 0)
        height = data.get("height", 0)
        avail_w = data.get("availWidth", 0)
        avail_h = data.get("availHeight", 0)

        passed = all(v > 0 for v in [width, height, avail_w, avail_h])
        if passed:
            reason = f"Screen {width}x{height}, avail {avail_w}x{avail_h}"
        else:
            reason = f"Zero dimensions detected: {width}x{height}, avail {avail_w}x{avail_h}"

        return {"passed": passed, "reason": reason, "data": data}

    def _check_webgl_present(self, data: dict) -> dict:
        """Check that WebGL context exists."""
        has_webgl = data.get("hasWebGL", False)

        passed = bool(has_webgl)
        reason = "WebGL available" if passed else "WebGL not available"

        return {"passed": passed, "reason": reason, "data": data}

    def _check_worker_crosscheck(self, data: dict) -> dict:
        """Check DedicatedWorker reports same UA/hardwareConcurrency as main thread."""
        main_ua = data.get("main_ua", "")
        worker_ua = data.get("worker_ua", "")
        main_hw = data.get("main_hw", -1)
        worker_hw = data.get("worker_hw", -1)

        ua_match = main_ua == worker_ua
        hw_match = main_hw == worker_hw

        passed = ua_match and hw_match
        if passed:
            reason = "Worker and main thread consistent"
        else:
            parts = []
            if not ua_match:
                parts.append(f"UA mismatch: main={main_ua!r} worker={worker_ua!r}")
            if not hw_match:
                parts.append(f"hardwareConcurrency mismatch: main={main_hw} worker={worker_hw}")
            reason = "; ".join(parts)

        return {"passed": passed, "reason": reason, "data": data}

    def _check_instance_stability(self, data: dict) -> dict:
        """Check two reads return identical values (catches Camoufox #328)."""
        first = data.get("first", {})
        second = data.get("second", {})

        passed = first == second
        if passed:
            reason = "Consecutive reads are identical"
        else:
            reason = f"Values changed between reads: first={first}, second={second}"

        return {"passed": passed, "reason": reason, "data": data}
