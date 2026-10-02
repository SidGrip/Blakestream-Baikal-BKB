/* Dev Tools activity window. Counters are decimal strings. Missing, wrapped,
   and non-exact values stay null — never a false 0. */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.bsDevActivity = api;
}(typeof window !== 'undefined' ? window : this, function () {
  var DISCARD_KEYS = ['invalid_id', 'invalid_workidx', 'missing_work', 'algo_mismatch', 'short_reply', 'reply_mismatch'];
  var TIMING_KEYS = [
    'work_wait_us_sum', 'work_wait_us_max', 'work_wait_count',
    'request_to_ack_us_sum', 'request_to_ack_us_max', 'request_to_ack_count',
    'ack_latency_us_sum', 'ack_latency_us_max', 'ack_latency_count',
    'poll_gap_us_max', 'poll_gap_us_last', 'uptime_us'
  ];
  var WORK_KEYS = ['requests', 'sends', 'skip_overheat', 'skip_no_target', 'skip_disabled', 'skip_empty'];
  var USB_KEYS = ['tx_error', 'rx_error'];
  var LIFETIME_MAX = {
    work_wait_us_max: 1,
    request_to_ack_us_max: 1,
    ack_latency_us_max: 1,
    poll_gap_us_max: 1
  };
  var DURATION_FIELDS = {
    work_wait_us_sum: 1,
    work_wait_us_max: 1,
    request_to_ack_us_sum: 1,
    request_to_ack_us_max: 1,
    ack_latency_us_sum: 1,
    ack_latency_us_max: 1,
    poll_gap_us_max: 1,
    poll_gap_us_last: 1,
    uptime_us: 1
  };
  var MAX_SAFE = 9007199254740991;
  var DASH = '\u2014';

  function asDec(v) {
    if (typeof v === 'number') {
      if (!isFinite(v) || Math.floor(v) !== v || v < 0 || v > MAX_SAFE) return null;
      return String(v);
    }
    if (typeof v !== 'string') return null;
    if (!/^(0|[1-9][0-9]{0,19})$/.test(v)) return null;
    return v;
  }

  function cmpDec(a, b) {
    var as = a.replace(/^0+/, '') || '0';
    var bs = b.replace(/^0+/, '') || '0';
    if (as.length !== bs.length) return as.length < bs.length ? -1 : 1;
    if (as === bs) return 0;
    return as < bs ? -1 : 1;
  }

  function subDigits(next, prev) {
    var a = next.split('');
    var b = prev.split('');
    var i;
    var d;
    var borrow = 0;
    var out = [];
    while (b.length < a.length) b.unshift('0');
    for (i = a.length - 1; i >= 0; i--) {
      d = (a[i].charCodeAt(0) - 48) - (b[i].charCodeAt(0) - 48) - borrow;
      if (d < 0) {
        d += 10;
        borrow = 1;
      } else {
        borrow = 0;
      }
      out.push(String(d));
    }
    return out.reverse().join('').replace(/^0+/, '') || '0';
  }

  function subCount(next, prev) {
    var a = asDec(next);
    var b = asDec(prev);
    if (a === null || b === null) return null;
    if (cmpDec(a, b) < 0) return null;
    if (a === b) return '0';
    return subDigits(a, b);
  }

  function epochKey(epoch) {
    var pid;
    var start;
    var stat;
    var route;
    var enable;
    if (!epoch || typeof epoch !== 'object') return null;
    pid = asDec(epoch.pid);
    start = asDec(epoch.start_unix);
    stat = asDec(epoch.stat);
    route = asDec(epoch.route);
    enable = asDec(epoch.enable);
    if (pid === null || pid === '0' || start === null || start === '0') return null;
    if (stat === null || route === null || enable === null) return null;
    return pid + '|' + start + '|' + stat + '|' + route + '|' + enable;
  }

  function cloneGroup(src, keys) {
    var out = {};
    var i;
    var key;
    for (i = 0; i < keys.length; i++) {
      key = keys[i];
      out[key] = asDec(src && src[key]);
    }
    return out;
  }

  function cloneGrid(grid) {
    var out = [];
    var u;
    var c;
    var cell;
    if (!grid || grid.length !== 4) return null;
    for (u = 0; u < 4; u++) {
      if (!grid[u] || grid[u].length !== 16) return null;
      out[u] = [];
      for (c = 0; c < 16; c++) {
        cell = asDec(grid[u][c]);
        if (cell === null) return null;
        out[u][c] = cell;
      }
    }
    return out;
  }

  function snapCounters(board) {
    board = board || {};
    return {
      nonce: cloneGrid(board.nonce),
      error: cloneGrid(board.error),
      discard: cloneGroup(board.discard, DISCARD_KEYS),
      timing: cloneGroup(board.timing, TIMING_KEYS),
      work: cloneGroup(board.work, WORK_KEYS),
      usb: cloneGroup(board.usb, USB_KEYS)
    };
  }

  function emptyGroup(keys) {
    var out = {};
    var i;
    for (i = 0; i < keys.length; i++) out[keys[i]] = null;
    return out;
  }

  function emptyDelta() {
    return {
      nonce: null,
      error: null,
      discard: emptyGroup(DISCARD_KEYS),
      timing: emptyGroup(TIMING_KEYS),
      work: emptyGroup(WORK_KEYS),
      usb: emptyGroup(USB_KEYS)
    };
  }

  function deltaGrid(prev, next) {
    var out = [];
    var u;
    var c;
    if (!prev || !next) return null;
    for (u = 0; u < 4; u++) {
      out[u] = [];
      for (c = 0; c < 16; c++) out[u][c] = subCount(next[u][c], prev[u][c]);
    }
    return out;
  }

  function deltaGroup(prev, next, keys) {
    var out = {};
    var i;
    var key;
    for (i = 0; i < keys.length; i++) {
      key = keys[i];
      out[key] = (key === 'poll_gap_us_last' || LIFETIME_MAX[key]) ? null : subCount(next[key], prev[key]);
    }
    return out;
  }

  function diffSnap(prev, next) {
    return {
      nonce: deltaGrid(prev.nonce, next.nonce),
      error: deltaGrid(prev.error, next.error),
      discard: deltaGroup(prev.discard, next.discard, DISCARD_KEYS),
      timing: deltaGroup(prev.timing, next.timing, TIMING_KEYS),
      work: deltaGroup(prev.work, next.work, WORK_KEYS),
      usb: deltaGroup(prev.usb, next.usb, USB_KEYS)
    };
  }

  function deltaWindow(baseline, boards) {
    var prev = (baseline && typeof baseline === 'object') ? baseline : {};
    var next = {};
    var deltas = {};
    var list = boards || [];
    var i;
    var board;
    var id;
    var key;
    var old;
    for (i = 0; i < list.length; i++) {
      board = list[i];
      if (!board || board.id === undefined || board.id === null) continue;
      id = board.id;
      key = epochKey(board.epoch);
      old = prev[id];
      if (!old || old.key !== key || key === null) {
        next[id] = {key: key, snap: snapCounters(board)};
        deltas[id] = emptyDelta();
      } else {
        next[id] = old;
        deltas[id] = diffSnap(old.snap, snapCounters(board));
      }
    }
    return {baseline: next, deltas: deltas};
  }

  function present(v) {
    return v !== null && v !== undefined;
  }

  function groupPresent(group, keys) {
    var i;
    if (!group) return false;
    for (i = 0; i < keys.length; i++) {
      if (present(group[keys[i]])) return true;
    }
    return false;
  }

  function boardPresent(board) {
    var keys = [
      'asic_count', 'asic_count_r', 'asic_ver', 'requested_clock', 'attempted_clock_index',
      'last_ack', 'bridge_status_echo', 'bridge_status_echo_code', 'command_verified',
      'last_info_ok', 'last_result_ok'
    ];
    var i;
    if (!board) return false;
    for (i = 0; i < keys.length; i++) {
      if (present(board[keys[i]])) return true;
    }
    if (present(board.nonce) || present(board.error)) return true;
    if (groupPresent(board.discard, DISCARD_KEYS)) return true;
    if (groupPresent(board.timing, TIMING_KEYS)) return true;
    if (groupPresent(board.work, WORK_KEYS)) return true;
    if (groupPresent(board.usb, USB_KEYS)) return true;
    return false;
  }

  function viewState(payload) {
    var i;
    var trace;
    var traceKeys;
    if (!payload || payload.ok === false || payload.support === 'unreachable') return 'failed';
    if (payload.support === 'legacy') return 'unsupported';
    if (payload.support !== 'ok') return 'failed';
    if (payload.boards) {
      for (i = 0; i < payload.boards.length; i++) {
        if (boardPresent(payload.boards[i])) return '';
      }
    }
    trace = payload.trace || {};
    traceKeys = ['enabled', 'live', 'used', 'capacity', 'overwritten', 'dropped', 'recorded', 'export'];
    for (i = 0; i < traceKeys.length; i++) {
      if (present(trace[traceKeys[i]])) return '';
    }
    return 'empty';
  }

  function timingText(mode, board, deltas, field) {
    var raw;
    if (LIFETIME_MAX[field]) {
      raw = board && board.timing ? board.timing[field] : null;
    } else if (mode === 'deltas') {
      if (field === 'poll_gap_us_last') return DASH;
      raw = deltas && deltas.timing ? deltas.timing[field] : null;
    } else {
      raw = board && board.timing ? board.timing[field] : null;
    }
    var text = subCount(raw, '0');
    if (text === null) return DASH;
    if (DURATION_FIELDS[field]) return text + ' us';
    return text;
  }

  function traceLocked(activity) {
    var csrfOk;
    if (!activity || activity.busy || !activity.traceApi) return true;
    csrfOk = typeof activity.csrf === 'string' && /^[0-9a-f]{64}$/.test(activity.csrf);
    if (!csrfOk) return true;
    if (activity.support !== 'ok') return true;
    if (activity.state === 'loading' || activity.state === 'failed' || activity.state === 'unsupported') return true;
    return false;
  }

  function createPoll(deps) {
    var gen = 0;
    var inflight = false;
    var destroyed = false;
    var active = false;
    var timer = null;
    deps = deps || {};

    function stop() {
      gen++;
      if (timer !== null) {
        if (typeof deps.cancel === 'function') deps.cancel(timer);
        timer = null;
      }
    }

    function arm() {
      if (destroyed || !active || typeof deps.schedule !== 'function') return;
      if (timer !== null && typeof deps.cancel === 'function') deps.cancel(timer);
      var seen = gen;
      timer = deps.schedule(function () {
        timer = null;
        if (destroyed || seen !== gen || !active) return;
        poll();
      }, 5000);
    }

    function onResponse(seen, payload, err) {
      inflight = false;
      if (destroyed || seen !== gen) {
        if (!destroyed && active) poll();
        return;
      }
      if (typeof deps.apply === 'function') deps.apply(payload, err);
      arm();
    }

    function poll() {
      if (destroyed || !active || inflight) return false;
      if (timer !== null && typeof deps.cancel === 'function') {
        deps.cancel(timer);
        timer = null;
      }
      if (typeof deps.request !== 'function') return false;
      inflight = true;
      var seen = gen;
      deps.request(function (payload, err) {
        onResponse(seen, payload, err);
      });
      return true;
    }

    function start() {
      stop();
      active = true;
      if (typeof deps.onStart === 'function') deps.onStart();
      poll();
    }

    function leave() {
      active = false;
      stop();
    }

    function destroy() {
      destroyed = true;
      active = false;
      stop();
    }

    return {
      start: start,
      leave: leave,
      destroy: destroy,
      poll: poll
    };
  }

  return {
    subCount: subCount,
    epochKey: epochKey,
    deltaWindow: deltaWindow,
    viewState: viewState,
    timingText: timingText,
    traceLocked: traceLocked,
    createPoll: createPoll
  };
}));
