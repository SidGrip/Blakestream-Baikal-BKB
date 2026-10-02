'use strict';

var assert = require('assert');
var api = require('../overlay/var/www/ng/dev-activity.js');

function epoch(over) {
  var e = {pid: '9', start_unix: '8', stat: '1', route: '1', enable: '1'};
  var k;
  over = over || {};
  for (k in over) {
    if (Object.prototype.hasOwnProperty.call(over, k)) e[k] = over[k];
  }
  return e;
}

function grid(fill) {
  var g = [];
  var u;
  var c;
  for (u = 0; u < 4; u++) {
    g[u] = [];
    for (c = 0; c < 16; c++) g[u][c] = fill;
  }
  return g;
}

function board(id, counters, ep) {
  counters = counters || {};
  return {
    id: id,
    epoch: ep,
    nonce: Object.prototype.hasOwnProperty.call(counters, 'nonce') ? counters.nonce : null,
    error: Object.prototype.hasOwnProperty.call(counters, 'error') ? counters.error : null,
    discard: {invalid_id: Object.prototype.hasOwnProperty.call(counters, 'invalid_id') ? counters.invalid_id : null},
    timing: {
      poll_gap_us_max: Object.prototype.hasOwnProperty.call(counters, 'poll_gap_us_max') ? counters.poll_gap_us_max : null,
      poll_gap_us_last: Object.prototype.hasOwnProperty.call(counters, 'poll_gap_us_last') ? counters.poll_gap_us_last : null,
      uptime_us: Object.prototype.hasOwnProperty.call(counters, 'uptime_us') ? counters.uptime_us : null
    },
    work: {requests: Object.prototype.hasOwnProperty.call(counters, 'requests') ? counters.requests : null},
    usb: {
      tx_error: Object.prototype.hasOwnProperty.call(counters, 'tx_error') ? counters.tx_error : null,
      rx_error: Object.prototype.hasOwnProperty.call(counters, 'rx_error') ? counters.rx_error : null
    }
  };
}

assert.strictEqual(api.subCount('5', '5'), '0');
assert.strictEqual(api.subCount('6', '5'), '1');
assert.strictEqual(api.subCount('5', '6'), null);
assert.strictEqual(api.subCount('1000', '1'), '999');
assert.strictEqual(api.subCount('18446744073709551615', '18446744073709551614'), '1');
assert.strictEqual(api.subCount('01', '0'), null);
assert.strictEqual(api.subCount('123456789012345678901', '0'), null);
assert.strictEqual(api.subCount(null, '1'), null);
assert.strictEqual(api.subCount('1', null), null);
assert.strictEqual(api.subCount(1.5, '0'), null);
assert.strictEqual(api.subCount(9007199254740993, '0'), null);
assert.strictEqual(api.subCount(9007199254740991, '1'), '9007199254740990');

assert.strictEqual(api.epochKey({pid: '1'}), null);
assert.strictEqual(api.epochKey({pid: '0', start_unix: '2', stat: '1', route: '1', enable: '1'}), null);
assert.strictEqual(api.epochKey({pid: '2', start_unix: null, stat: '1', route: '1', enable: '1'}), null);
assert.strictEqual(api.epochKey({pid: '2', start_unix: '3', stat: '1', route: '1', enable: null}), null);
assert.strictEqual(api.epochKey(epoch()), '9|8|1|1|1');

var first = board(0, {
  invalid_id: '5', tx_error: '1', uptime_us: '10', poll_gap_us_last: '4',
  poll_gap_us_max: '4', requests: '2', nonce: grid('3')
}, epoch());
var opened = api.deltaWindow(null, [first]);
assert.strictEqual(opened.deltas[0].discard.invalid_id, null);
assert.strictEqual(opened.deltas[0].nonce, null);
assert.strictEqual(opened.deltas[0].timing.uptime_us, null);

var second = board(0, {
  invalid_id: '7', tx_error: '1', uptime_us: '12', poll_gap_us_last: '8',
  poll_gap_us_max: '5', requests: '2', nonce: grid('4')
}, epoch());
var mid = api.deltaWindow(opened.baseline, [second]);
assert.strictEqual(mid.deltas[0].discard.invalid_id, '2');
assert.strictEqual(mid.deltas[0].nonce[1][2], '1');
assert.strictEqual(mid.deltas[0].timing.poll_gap_us_last, null);
assert.strictEqual(mid.deltas[0].timing.poll_gap_us_max, null);
assert.strictEqual(mid.deltas[0].timing.uptime_us, '2');
assert.strictEqual(mid.baseline[0], opened.baseline[0]);

var third = board(0, {
  invalid_id: '8', tx_error: '1', uptime_us: '10', poll_gap_us_last: '1',
  poll_gap_us_max: '4', requests: '2', nonce: grid('3')
}, epoch());
var later = api.deltaWindow(mid.baseline, [third]);
assert.strictEqual(later.deltas[0].discard.invalid_id, '3');
assert.strictEqual(later.deltas[0].nonce[0][0], '0');
assert.strictEqual(later.baseline[0].snap.discard.invalid_id, '5');

var wrapped = board(0, {invalid_id: '4', nonce: grid('3')}, epoch());
var wrap = api.deltaWindow(opened.baseline, [wrapped]);
assert.strictEqual(wrap.deltas[0].discard.invalid_id, null);

var dipped = grid('5');
dipped[0][1] = '1';
var baseGrid = board(0, {nonce: grid('4'), invalid_id: '1'}, epoch());
var nextGrid = board(0, {nonce: dipped, invalid_id: '1'}, epoch());
var cells = api.deltaWindow(api.deltaWindow(null, [baseGrid]).baseline, [nextGrid]);
assert.strictEqual(cells.deltas[0].nonce[0][0], '1');
assert.strictEqual(cells.deltas[0].nonce[0][1], null);
assert.strictEqual(cells.deltas[0].discard.invalid_id, '0');

var missingNonce = board(0, {invalid_id: '1', nonce: null}, epoch());
var missingNonce2 = board(0, {invalid_id: '2', nonce: null}, epoch());
var nullGrid = api.deltaWindow(api.deltaWindow(null, [missingNonce]).baseline, [missingNonce2]);
assert.strictEqual(nullGrid.deltas[0].nonce, null);
assert.strictEqual(nullGrid.deltas[0].discard.invalid_id, '1');

var e0 = epoch();
var left = board(0, {invalid_id: '5'}, e0);
var right = board(1, {invalid_id: '10'}, e0);
var both = api.deltaWindow(null, [left, right]);
var both2 = api.deltaWindow(both.baseline, [
  board(0, {invalid_id: '6'}, e0),
  board(1, {invalid_id: '12'}, e0)
]);
assert.strictEqual(both2.deltas[0].discard.invalid_id, '1');
assert.strictEqual(both2.deltas[1].discard.invalid_id, '2');
var changed = api.deltaWindow(both2.baseline, [
  board(0, {invalid_id: '100'}, epoch({stat: '2'})),
  board(1, {invalid_id: '15'}, e0)
]);
assert.strictEqual(changed.deltas[0].discard.invalid_id, null);
assert.strictEqual(changed.deltas[1].discard.invalid_id, '5');
assert.ok(changed.baseline[0].key !== both2.baseline[0].key);
assert.strictEqual(changed.baseline[1], both2.baseline[1]);

var partial = board(0, {invalid_id: '5'}, {pid: null, start_unix: '1', stat: '1', route: '1', enable: '1'});
var partial2 = board(0, {invalid_id: '8'}, {pid: null, start_unix: '1', stat: '1', route: '1', enable: '1'});
var partialWin = api.deltaWindow(api.deltaWindow(null, [partial]).baseline, [partial2]);
assert.strictEqual(partialWin.deltas[0].discard.invalid_id, null);

assert.strictEqual(api.viewState(null), 'failed');
assert.strictEqual(api.viewState({ok: false, support: 'unreachable', error: 'sgminer api unreachable'}), 'failed');
assert.strictEqual(api.viewState({ok: true, support: 'legacy', boards: [{asic_count: '64'}]}), 'unsupported');
assert.strictEqual(api.viewState({ok: true, support: 'ok', boards: [{id: 0}], trace: {}}), 'empty');
assert.strictEqual(api.viewState({ok: true, support: 'ok', boards: [{asic_count: '0'}], trace: {}}), '');
assert.strictEqual(api.viewState({ok: true, support: 'ok', boards: [], trace: {enabled: false}}), '');

var maxBoard = board(0, {}, epoch());
maxBoard.timing.work_wait_us_max = '1000';
maxBoard.timing.work_wait_us_sum = '1000';
maxBoard.timing.work_wait_count = '4';
maxBoard.timing.poll_gap_us_last = '9';
var maxOpen = api.deltaWindow(null, [maxBoard]);
var maxSame = board(0, {}, epoch());
maxSame.timing.work_wait_us_max = '1000';
maxSame.timing.work_wait_us_sum = '1000';
maxSame.timing.work_wait_count = '6';
maxSame.timing.poll_gap_us_last = '9';
var maxHeld = api.deltaWindow(maxOpen.baseline, [maxSame]);
assert.strictEqual(maxHeld.deltas[0].timing.work_wait_us_max, null);
assert.strictEqual(maxHeld.deltas[0].timing.work_wait_us_sum, '0');
assert.strictEqual(maxHeld.deltas[0].timing.work_wait_count, '2');
assert.strictEqual(api.timingText('deltas', maxSame, maxHeld.deltas[0], 'work_wait_us_max'), '1000 us');
assert.strictEqual(api.timingText('deltas', maxSame, maxHeld.deltas[0], 'work_wait_us_sum'), '0 us');
assert.strictEqual(api.timingText('deltas', maxSame, maxHeld.deltas[0], 'work_wait_count'), '2');
assert.strictEqual(api.timingText('totals', maxSame, null, 'work_wait_us_sum'), '1000 us');
assert.strictEqual(api.timingText('deltas', maxSame, maxHeld.deltas[0], 'poll_gap_us_last'), '\u2014');
var maxUp = board(0, {}, epoch());
maxUp.timing.work_wait_us_max = '1500';
maxUp.timing.work_wait_us_sum = '1000';
var maxRose = api.deltaWindow(maxOpen.baseline, [maxUp]);
assert.strictEqual(maxRose.deltas[0].timing.work_wait_us_max, null);
assert.strictEqual(api.timingText('deltas', maxUp, maxRose.deltas[0], 'work_wait_us_max'), '1500 us');
assert.notStrictEqual(api.timingText('deltas', maxUp, maxRose.deltas[0], 'work_wait_us_max'), '500 us');

var csrf = new Array(33).join('ab');
function lockState(over) {
  var base = {busy: false, traceApi: true, csrf: csrf, support: 'ok', state: ''};
  var k;
  for (k in over) {
    if (Object.prototype.hasOwnProperty.call(over, k)) base[k] = over[k];
  }
  return base;
}
assert.strictEqual(api.traceLocked(lockState({})), false);
assert.strictEqual(api.traceLocked(lockState({support: 'legacy', state: 'unsupported'})), true);
assert.strictEqual(api.traceLocked(lockState({support: 'unreachable', state: 'failed'})), true);
assert.strictEqual(api.traceLocked(lockState({state: 'loading'})), true);
assert.strictEqual(api.traceLocked(lockState({state: 'failed'})), true);
assert.strictEqual(api.traceLocked(lockState({state: 'unsupported'})), true);
assert.strictEqual(api.traceLocked(lockState({csrf: ''})), true);
assert.strictEqual(api.traceLocked(lockState({traceApi: false})), true);
assert.strictEqual(api.traceLocked(lockState({busy: true})), true);

function pollHarness() {
  var log = [];
  var pending = null;
  var timers = [];
  var poller = api.createPoll({
    schedule: function (fn, ms) {
      var timer = {fn: fn, ms: ms, cancelled: false};
      timers.push(timer);
      log.push('arm');
      return timer;
    },
    cancel: function (timer) {
      timer.cancelled = true;
      log.push('cancel');
    },
    request: function (done) {
      log.push('get');
      pending = done;
    },
    apply: function (payload, err) {
      log.push('apply:' + (err || (payload && payload.tag) || ''));
    },
    onStart: function () { log.push('reset'); }
  });
  return {
    log: log,
    timers: timers,
    poller: poller,
    respond: function (payload, err) {
      var done = pending;
      pending = null;
      done(payload, err);
    }
  };
}

var left = pollHarness();
left.poller.start();
assert.deepEqual(left.log, ['reset', 'get']);
left.log.length = 0;
left.poller.poll();
assert.deepEqual(left.log, []);
left.poller.leave();
left.respond({tag: 'late'}, null);
assert.strictEqual(left.log.indexOf('apply:late'), -1);
assert.strictEqual(left.log.indexOf('get'), -1);

var again = pollHarness();
again.poller.start();
again.log.length = 0;
again.poller.leave();
again.poller.start();
assert.deepEqual(again.log, ['reset']);
again.respond({tag: 'stale'}, null);
assert.strictEqual(again.log.indexOf('apply:stale'), -1);
assert.ok(again.log.indexOf('get') !== -1);
again.respond({tag: 'fresh'}, null);
assert.ok(again.log.indexOf('apply:fresh') !== -1);
assert.ok(again.log.indexOf('arm') !== -1);
assert.strictEqual(again.poller.queue, undefined);

var dead = pollHarness();
dead.poller.start();
dead.respond({tag: 'ok'}, null);
assert.strictEqual(dead.timers.length, 1);
assert.strictEqual(dead.timers[0].ms, 5000);
dead.poller.destroy();
assert.strictEqual(dead.timers[0].cancelled, true);
dead.log.length = 0;
dead.timers[0].fn();
assert.deepEqual(dead.log, []);

var torn = pollHarness();
torn.poller.start();
torn.log.length = 0;
torn.poller.destroy();
torn.respond({tag: 'late'}, null);
assert.strictEqual(torn.log.indexOf('apply:late'), -1);
assert.strictEqual(torn.log.indexOf('get'), -1);

console.log('ok');
