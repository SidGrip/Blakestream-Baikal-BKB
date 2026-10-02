/* Blakestream dark/light theme toggle.
 * Reads localStorage.bsTheme on page load (default "dark"), applies/removes
 * `bs-dark` class on <body>, wires the toggle button click. Vanilla JS, no
 * dependencies — runs before Angular bootstraps so first-paint matches.
 */
(function () {
    'use strict';
    var KEY = 'bsTheme';

    function getStoredTheme() {
        try {
            return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark';
        } catch (e) {
            // localStorage may be blocked (private mode, file://, etc.)
            return 'dark';
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
        document.documentElement.classList.toggle('bs-dark', theme === 'dark');
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
            btns[i].removeAttribute('title');
            btns[i].setAttribute('data-tip',
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
        var current = document.body ? (document.body.classList.contains('bs-dark') ? 'dark' : 'light') : getStoredTheme();
        var next = current === 'dark' ? 'light' : 'dark';
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
        applyTheme(getStoredTheme());
    }

    init();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose for debugging / Angular integration
    window.BlakestreamTheme = {
        get: getStoredTheme,
        init: init,
        set: function (t) { t = t === 'light' ? 'light' : 'dark'; setStoredTheme(t); applyTheme(t); },
        toggle: toggleTheme
    };
})();
