(function (window) {
    'use strict';

    var alertify = window.Alertify;
    if (!alertify || !alertify.log || alertify.log.__blakestreamTextOnly) return;

    function escapeText(value) {
        if (value === undefined || value === null) value = '';
        return String(value).replace(/[&<>"']/g, function (character) {
            return {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }[character];
        });
    }

    function wrap(method, messageIndex) {
        var original = alertify.log[method];
        if (typeof original !== 'function') return;
        alertify.log[method] = function () {
            var args = Array.prototype.slice.call(arguments);
            if (args.length > messageIndex) args[messageIndex] = escapeText(args[messageIndex]);
            return original.apply(alertify.log, args);
        };
    }

    wrap('create', 1);
    wrap('error', 0);
    wrap('info', 0);
    wrap('success', 0);

    // Some inherited controllers use log() as the neutral toast method even
    // though Alertify 0.4 only provides info(). Keep that API text-only too.
    if (typeof alertify.log.log !== 'function' && typeof alertify.log.info === 'function') {
        alertify.log.log = function () {
            return alertify.log.info.apply(alertify.log, arguments);
        };
    } else {
        wrap('log', 0);
    }

    alertify.log.escapeText = escapeText;
    alertify.log.__blakestreamTextOnly = true;
})(window);
