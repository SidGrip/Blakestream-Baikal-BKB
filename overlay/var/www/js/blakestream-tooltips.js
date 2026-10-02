/* Dismiss action tooltips without moving keyboard focus. */
(function () {
    'use strict';

    function actionTip(target) {
        return target && target.closest ? target.closest('button.bs-tip, a.bs-tip') : null;
    }

    document.addEventListener('click', function (event) {
        var tip = actionTip(event.target);
        if (tip) tip.classList.add('bs-tip-dismissed');
    });

    function resetTip(event) {
        var tip = actionTip(event.target);
        if (tip && !tip.contains(event.relatedTarget)) tip.classList.remove('bs-tip-dismissed');
    }

    document.addEventListener('mouseout', resetTip);
    document.addEventListener('focusout', resetTip);
})();
