/* Blakestream dark/light theme toggle.
 * Reads localStorage.bsTheme on page load (default "light"), applies/removes
 * `bs-dark` class on <body>, wires the toggle button click. Vanilla JS, no
 * dependencies — runs before Angular bootstraps so first-paint matches.
 */
(function () {
    'use strict';
    console.log('[blakestream-theme] script loaded');

    var KEY = 'bsTheme';

    function getStoredTheme() {
        try {
            return localStorage.getItem(KEY) || 'light';
        } catch (e) {
            // localStorage may be blocked (private mode, file://, etc.)
            return 'light';
        }
    }

    function setStoredTheme(theme) {
        try {
            localStorage.setItem(KEY, theme);
        } catch (e) {
            // best-effort, ignore failures
        }
    }

    function applyTheme(theme) {
        var body = document.body;
        if (!body) return;
        if (theme === 'dark') {
            body.classList.add('bs-dark');
        } else {
            body.classList.remove('bs-dark');
        }
        // Update toggle button icon if present (icon-only, no text)
        var btns = document.querySelectorAll('.bs-theme-toggle');
        for (var i = 0; i < btns.length; i++) {
            btns[i].textContent = theme === 'dark' ? '☀' : '☾';
            btns[i].setAttribute('aria-label',
                theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
            btns[i].setAttribute('title',
                theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
        }
        // Broadcast for subscribers (e.g. chart palette swap).
        try {
            var ev;
            if (typeof CustomEvent === 'function') {
                ev = new CustomEvent('bsThemeChanged', {detail: {theme: theme}});
            } else {
                ev = document.createEvent('CustomEvent');
                ev.initCustomEvent('bsThemeChanged', false, false, {theme: theme});
            }
            window.dispatchEvent(ev);
        } catch (e) { /* best-effort */ }
    }

    function toggleTheme() {
        var current = getStoredTheme();
        var next = current === 'dark' ? 'light' : 'dark';
        console.log('[blakestream-theme] toggle:', current, '->', next);
        setStoredTheme(next);
        applyTheme(next);
    }

    // Apply stored theme as early as possible to avoid a flash of light theme.
    // The script tag is in <head>, so document.body may not exist yet.
    //
    // Note: we deliberately do NOT add click listeners here. The toggle button
    // in index.php uses an inline onclick="window.BlakestreamTheme.toggle()" so
    // adding another listener would cause double-firing.
    function init() {
        console.log('[blakestream-theme] init, body=', document.body, 'stored=', getStoredTheme());
        applyTheme(getStoredTheme());
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose for debugging / Angular integration
    window.BlakestreamTheme = {
        get: getStoredTheme,
        set: function (t) { setStoredTheme(t); applyTheme(t); },
        toggle: toggleTheme
    };
})();
