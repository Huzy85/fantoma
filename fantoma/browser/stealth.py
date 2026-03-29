"""Stealth patches — fix detectable fingerprint gaps that Camoufox misses.

Camoufox handles the big stuff (canvas, WebGL renderer, UA string). These
patches fix the smaller signals that fingerprinting scripts like X's
LoginJsInstrumentationSubtask check:

Two layers:
- Native: Camoufox config + firefox_user_prefs (hardwareConcurrency, buildID)
- JS init script: performance.now precision, plugins, webdriver reinforcement

Applied via Camoufox launch config and context.add_init_script().
"""

import logging

log = logging.getLogger("fantoma.stealth")

# Firefox 135.0 was released 2025-02-04. BuildID format: YYYYMMDDHHMMSS
_FIREFOX_BUILD_ID = "20250204120000"

# Realistic core count for a desktop Linux machine
_HW_CONCURRENCY = 8


def get_camoufox_config():
    """Return Camoufox kwargs for native-level stealth patches.

    These override values at the C++ level inside Firefox — JS can't detect
    or bypass them. Merge into Camoufox() constructor kwargs.
    """
    return {
        "config": {
            "navigator.hardwareConcurrency": _HW_CONCURRENCY,
        },
        "i_know_what_im_doing": True,
        "firefox_user_prefs": {
            "general.buildID.override": _FIREFOX_BUILD_ID,
            # Reduce timer precision to realistic level (100 microseconds)
            # Default Camoufox sets this very high (causes Infinity in some checks)
            "privacy.reduceTimerPrecision": True,
            "privacy.resistFingerprinting.reduceTimerPrecision.microseconds": 100,
        },
    }


def get_stealth_script():
    """Return JS that patches detectable fingerprint gaps.

    Must run before any page scripts (via add_init_script).
    Only patches things that can't be fixed at the native level.
    """
    return """(() => {
    // 1. Fix performance.now() precision
    // Even with reduced timer precision config, ensure the output looks
    // natural — consistent rounding, no Infinity, monotonically increasing.
    const _perfNow = performance.now.bind(performance);
    let _lastTime = 0;
    Object.defineProperty(performance, 'now', {
        value: function() {
            let t = _perfNow();
            // Round to ~0.1ms like real Firefox on Linux
            t = Math.round(t * 10) / 10;
            t += Math.random() * 0.05;
            if (t <= _lastTime) t = _lastTime + 0.1;
            _lastTime = t;
            return t;
        },
        writable: false,
        configurable: true,
        enumerable: true,
    });

    // 2. Reinforce navigator.webdriver = false
    // Some scripts check this via multiple paths or after page load
    try {
        Object.defineProperty(Navigator.prototype, 'webdriver', {
            get: function() { return false; },
            configurable: true,
            enumerable: true,
        });
    } catch(e) {}

    // 3. Fix navigator.plugins (empty array = automation signal)
    try {
        if (navigator.plugins.length === 0) {
            const fakePlugins = {
                length: 5,
                0: { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                1: { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: '' },
                2: { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: '' },
                3: { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: '' },
                4: { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: '' },
                item: function(i) { return this[i] || null; },
                namedItem: function(n) {
                    for (let i = 0; i < this.length; i++) {
                        if (this[i] && this[i].name === n) return this[i];
                    }
                    return null;
                },
                refresh: function() {},
                [Symbol.iterator]: function*() {
                    for (let i = 0; i < this.length; i++) yield this[i];
                },
            };
            Object.defineProperty(Navigator.prototype, 'plugins', {
                get: function() { return fakePlugins; },
                configurable: true,
                enumerable: true,
            });
        }
    } catch(e) {}
})();"""


def apply_stealth(context):
    """Apply JS stealth patches to a browser context.

    Call this AFTER creating the context but BEFORE navigating to any page.
    Uses add_init_script so patches run before any page JavaScript.

    Note: native patches (config, firefox_user_prefs) must be applied at
    Camoufox launch time via get_camoufox_config(). This function only
    handles the JS layer.
    """
    try:
        script = get_stealth_script()
        context.add_init_script(script)
        log.debug("Stealth JS patches applied")
    except Exception as e:
        log.warning("Failed to apply stealth patches: %s", e)
