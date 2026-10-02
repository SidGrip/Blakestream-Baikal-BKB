(function (window, $) {
    'use strict';
    window.bsNetworkLoginRedirect = function (fallback) {
        $.ajax({url: 'f_network.php?action=pending', dataType: 'json', timeout: 5000, cache: false})
            .done(function (data) {
                window.location = data && data.pending ? 'index.php#/settings?section=network' : fallback;
            })
            .fail(function () { window.location = fallback; });
    };
})(window, jQuery);
