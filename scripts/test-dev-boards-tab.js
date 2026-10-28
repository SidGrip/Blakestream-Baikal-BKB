'use strict';

// Controller/template regression for the unified Dev Tools Boards tab.
var assert = require('assert');
var fs = require('fs');
var path = require('path');
var pollApi = require('../overlay/var/www/ng/dev-activity.js');

var root = path.join(__dirname, '..');
function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8');
}

var ctrl = read('overlay/var/www/ng/controllers.js');
var html = read('overlay/var/www/partials/dev-tools.html');
var app = read('overlay/var/www/ng/app.js');
var index = read('overlay/var/www/index.php');
var css = read('overlay/var/www/css/blakestream-dark.css');
var tag = '20260930-ui-backup';

function extractFunction(src, signature) {
  var start = src.indexOf(signature);
  assert.notStrictEqual(start, -1, 'missing ' + signature);
  var open = src.indexOf('{', start);
  var depth = 0;
  var i;
  for (i = open; i < src.length; i++) {
    if (src.charAt(i) === '{') depth++;
    else if (src.charAt(i) === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error('unclosed ' + signature);
}

function sliceBetween(src, startSig, endSig) {
  var start = src.indexOf(startSig);
  var end = src.indexOf(endSig, start + startSig.length);
  assert.ok(start !== -1 && end !== -1, startSig);
  return src.slice(start, end);
}

var boardsAt = html.indexOf('ng-switch-when="boards"');
var logsAt = html.indexOf('ng-switch-when="logs"');
assert.ok(boardsAt !== -1 && logsAt > boardsAt, 'boards panel precedes logs');
var panel = html.slice(boardsAt, logsAt);
var openDiv = (panel.match(/<div\b/g) || []).length;
var closeDiv = (panel.match(/<\/div>/g) || []).length;
assert.strictEqual(openDiv, closeDiv, 'boards panel divs balance');
assert.strictEqual((html.match(/ng-switch-when="boards"/g) || []).length, 1);
assert.strictEqual((html.match(/ng-switch-when="activity"/g) || []).length, 0);
assert.strictEqual((html.match(/bs-activity-page/g) || []).length, 1);
assert.strictEqual((html.match(/bs-activity-grid/g) || []).length, 1);
assert.strictEqual((html.match(/bs-board-table/g) || []).length, 1);
assert.strictEqual(html.indexOf('>Activity<'), -1);
assert.ok(panel.indexOf('devActivityShowBoard(b)') !== panel.lastIndexOf('devActivityShowBoard(b)'), 'filter is shared');
assert.ok(panel.indexOf('ng-show="devActivityShowBoard(b)"') !== -1, 'health rows use the board filter');
assert.ok(panel.indexOf('bs-activity-health') !== -1);
assert.ok(panel.indexOf('>State<') !== -1 && panel.indexOf('>Temp<') !== -1 && panel.indexOf('>Fan<') !== -1);
assert.ok(panel.indexOf('>Acc<') !== -1 && panel.indexOf('>Rej<') !== -1 && panel.indexOf('>HW<') !== -1);
assert.ok(panel.indexOf('Last share') !== -1);
assert.strictEqual(panel.indexOf('devBoardSet(b, !b.enabled)'), -1);
assert.strictEqual(panel.indexOf('devMinerRestart()'), -1);
assert.ok(panel.indexOf("devActivity.mode='totals'") !== -1);
assert.ok(panel.indexOf("devActivity.mode='deltas'") !== -1);
assert.ok(panel.indexOf('devActivityRefresh()') !== -1);
assert.ok(panel.indexOf('devActivityClear()') !== -1);
assert.ok(panel.indexOf('devActivityExport()') !== -1);
assert.ok(panel.indexOf('devActivityTraceToggle($event)') !== -1);
assert.ok(panel.indexOf('bs-activity-nonce') !== -1 && panel.indexOf('bs-activity-err') !== -1);
assert.ok(panel.indexOf('Locally valid nonces / local errors · not pool-accepted') !== -1);
assert.ok(panel.indexOf('No board data — sgminer API not reachable or no boards reporting.') !== -1);
assert.ok(panel.indexOf('ascenable') === -1 && panel.indexOf('ascdisable') === -1, 'template does not mine by itself');
assert.ok(panel.indexOf("command: 'restart'") === -1);

assert.strictEqual(ctrl.indexOf('activity: true'), -1);
assert.strictEqual(ctrl.indexOf('devBoardsGen'), -1);
assert.strictEqual(ctrl.indexOf('$timeout(btick, 5000)'), -1);
assert.ok(ctrl.indexOf("return $scope.bsViewTab('dev');") !== -1);
assert.ok(ctrl.indexOf("if (tab === 'boards') devActivityStart();") !== -1);
assert.ok(ctrl.indexOf('else devActivityStop();') !== -1);
assert.ok(ctrl.indexOf('devActivityPoller.start()') !== -1);
assert.ok(ctrl.indexOf('devActivityPoller.leave()') !== -1);
assert.ok(ctrl.indexOf('devActivityPoller.poll()') !== -1);
assert.ok(ctrl.indexOf('devActivityPoller.destroy()') !== -1);
assert.ok(ctrl.indexOf("get('f_dev.php?action=activity')") !== -1);
assert.ok(ctrl.indexOf("get('f_dev.php?action=board_health')") !== -1);
assert.ok(ctrl.indexOf("post('f_dev.php?action=activity_trace'") !== -1);
assert.strictEqual(ctrl.indexOf("command: enable ? 'ascenable' : 'ascdisable'"), -1);
assert.strictEqual(ctrl.indexOf("command: 'restart'"), -1);
assert.strictEqual(ctrl.indexOf('Restart sgminer now? This briefly interrupts mining on all boards.'), -1);
assert.strictEqual(ctrl.indexOf("title: 'Restart miner'"), -1);
assert.ok(ctrl.indexOf('Clear the trace?') !== -1);
assert.strictEqual(ctrl.indexOf("window.confirm('Restart sgminer now?')"), -1);
assert.ok(ctrl.indexOf("window.confirm('Clear the trace?')") !== -1);
assert.ok(ctrl.indexOf("|| 'no board data'") !== -1);
assert.ok(ctrl.indexOf("devBoards.error = 'request failed'") !== -1);

var startFn = extractFunction(ctrl, 'function devActivityStart()');
assert.strictEqual(startFn.indexOf('ascenable'), -1);
assert.strictEqual(startFn.indexOf('ascdisable'), -1);
assert.strictEqual(startFn.indexOf('restart'), -1);
assert.strictEqual(startFn.indexOf('sgminer'), -1);
assert.strictEqual(startFn.indexOf('devMinerRestart'), -1);
assert.strictEqual(startFn.indexOf('devBoardSet'), -1);
var stopFn = extractFunction(ctrl, 'function devActivityStop()');
assert.ok(stopFn.indexOf('devHealthDrop()') !== -1);
assert.ok(stopFn.indexOf('devActivityPoller.leave()') !== -1);
assert.strictEqual(stopFn.indexOf('ascenable'), -1);
assert.strictEqual(stopFn.indexOf('restart'), -1);
var watchFn = sliceBetween(ctrl, "$scope.$watch('devTab'", 'Firmware tab');
assert.strictEqual(watchFn.indexOf('devMinerRestart'), -1);
assert.strictEqual(watchFn.indexOf('devBoardSet'), -1);
assert.strictEqual(watchFn.indexOf('sgminer_api'), -1);
assert.ok(watchFn.indexOf('devActivityStop();') !== -1);
var destroyFn = extractFunction(ctrl, "$scope.$on('$destroy', function ()");
assert.ok(destroyFn.indexOf('devToolsDestroyed = true') !== -1);
assert.ok(destroyFn.indexOf('devHealthDrop()') !== -1);
assert.ok(destroyFn.indexOf('devActivityPoller.destroy()') !== -1);
var requestFn = sliceBetween(ctrl, 'request: function (done)', 'apply: function');
assert.ok(requestFn.indexOf('devBoardsLoad()') !== -1);
assert.ok(requestFn.indexOf("get('f_dev.php?action=activity')") !== -1);
assert.strictEqual(requestFn.indexOf('sgminer_api'), -1);
assert.strictEqual(requestFn.indexOf('ascenable'), -1);

var tabs = {boards: true, logs: true, network: true, api: true, firmware: true};
var loadSrc = extractFunction(ctrl, 'function bsDevTabLoad()');
function runLoad(stored, opts) {
  var scope = require('./test-view-session')();
  scope.bsRememberViewTab('dev', stored);
  var fn = new Function('$scope', loadSrc + '\nreturn bsDevTabLoad();');
  return fn(scope);
}
assert.equal(runLoad('activity'), 'boards');
assert.equal(runLoad('logs'), 'logs');
assert.equal(runLoad('firmware'), 'firmware');
assert.equal(runLoad('nope'), 'boards');
assert.equal(runLoad(null), 'boards');
assert.strictEqual(tabs.activity, undefined);

var showSrc = extractFunction(ctrl, '$scope.devActivityShowBoard = function (b)');
function showBoard(board, row) {
  var $scope = {devActivity: {board: board}};
  var fn = new Function('$scope', showSrc + '\nreturn $scope.devActivityShowBoard;');
  return fn($scope)(row);
}
assert.strictEqual(showBoard('all', {id: 1}), true);
assert.strictEqual(showBoard(1, {id: 1}), true);
assert.strictEqual(showBoard(1, {id: '1'}), true);
assert.strictEqual(showBoard('2', {id: 2}), true);
assert.strictEqual(showBoard(0, {id: 1}), false);
assert.strictEqual(showBoard(0, null), false);

function mockHttp() {
  var calls = [];
  return {
    calls: calls,
    get: function (url) {
      var rec = {url: url, ok: null, err: null};
      calls.push(rec);
      var chain = {
        success: function (fn) { rec.ok = fn; return chain; },
        error: function (fn) { rec.err = fn; return chain; }
      };
      return chain;
    }
  };
}

var healthSrc = sliceBetween(ctrl, 'var devHealthGen = 0;', 'function devActivityApply');
function healthHarness(tab) {
  var $scope = {
    devTab: tab,
    devBoards: {rows: [{id: 0, enabled: true}], updated: 0, error: 'old', loading: false}
  };
  var $http = mockHttp();
  var api = new Function('$scope', '$http', healthSrc + '\n$scope.devBoardsLoad = devBoardsLoad;\nreturn {drop: devHealthDrop, destroy: function () { devToolsDestroyed = true; }};');
  var handle = api($scope, $http);
  return {$scope: $scope, $http: $http, drop: handle.drop, destroy: handle.destroy};
}
function deliver(http, index, payload, failed) {
  var rec = http.calls[index];
  if (failed) rec.err();
  else rec.ok(payload);
}

var okHealth = healthHarness('boards');
okHealth.$scope.devBoardsLoad();
assert.strictEqual(okHealth.$http.calls.length, 1);
assert.strictEqual(okHealth.$http.calls[0].url, 'f_dev.php?action=board_health');
assert.strictEqual(okHealth.$scope.devBoards.loading, true);
deliver(okHealth.$http, 0, {ok: true, boards: [{id: 2, enabled: false}], generated_at: 9});
assert.strictEqual(okHealth.$scope.devBoards.loading, false);
assert.strictEqual(okHealth.$scope.devBoards.error, '');
assert.strictEqual(okHealth.$scope.devBoards.updated, 9);
assert.strictEqual(okHealth.$scope.devBoards.rows[0].id, 2);

var badHealth = healthHarness('boards');
badHealth.$scope.devBoardsLoad();
deliver(badHealth.$http, 0, {ok: false, error: 'no board data'});
assert.strictEqual(badHealth.$scope.devBoards.error, 'no board data');
assert.strictEqual(badHealth.$scope.devBoards.rows[0].id, 0, 'failed read keeps prior rows');

var httpFail = healthHarness('boards');
httpFail.$scope.devBoardsLoad();
deliver(httpFail.$http, 0, null, true);
assert.strictEqual(httpFail.$scope.devBoards.error, 'request failed');
assert.strictEqual(httpFail.$scope.devBoards.loading, false);
assert.strictEqual(httpFail.$scope.devBoards.rows[0].id, 0);

var left = healthHarness('boards');
left.$scope.devBoardsLoad();
left.$scope.devTab = 'logs';
left.drop();
deliver(left.$http, 0, {ok: true, boards: [{id: 9}], generated_at: 3});
assert.strictEqual(left.$scope.devBoards.rows[0].id, 0, 'left tab ignores health');
assert.strictEqual(left.$scope.devBoards.updated, 0);
assert.strictEqual(left.$scope.devBoards.loading, false);

var raced = healthHarness('boards');
raced.$scope.devBoardsLoad();
raced.$scope.devBoardsLoad();
deliver(raced.$http, 0, {ok: true, boards: [{id: 8}], generated_at: 1});
assert.strictEqual(raced.$scope.devBoards.rows[0].id, 0, 'older health read does not paint');
deliver(raced.$http, 1, {ok: true, boards: [{id: 4}], generated_at: 2});
assert.strictEqual(raced.$scope.devBoards.rows[0].id, 4);

var dead = healthHarness('boards');
dead.$scope.devBoardsLoad();
dead.destroy();
deliver(dead.$http, 0, {ok: true, boards: [{id: 7}], generated_at: 5});
assert.strictEqual(dead.$scope.devBoards.rows[0].id, 0, 'destroyed controller ignores health');

var away = healthHarness('logs');
away.$scope.devBoardsLoad();
assert.strictEqual(away.$http.calls.length, 0, 'health read does not start off the boards tab');

var applied = [];
var scope = {devTab: 'boards', devActivity: {loading: false, boards: ['kept']}};
var http = mockHttp();
var healthLoads = 0;
var requestSrc = extractFunction(ctrl, 'request: function (done)');
var applySrc = extractFunction(ctrl, 'apply: function (d, err)');
var wired = new Function('$scope', '$http', 'devBoardsLoad', 'devActivityApply',
  'var devToolsDestroyed = false;\n' +
  'var api = {' + requestSrc + ',\n' + applySrc + '};\n' +
  'return {request: api.request, apply: api.apply, destroy: function () { devToolsDestroyed = true; }};');
var callbacks = wired(scope, http, function () { healthLoads++; }, function (d, err) { applied.push([d, err]); });
var pending = null;
var timers = [];
var poller = pollApi.createPoll({
  schedule: function (fn, ms) {
    var timer = {fn: fn, ms: ms, cancelled: false};
    timers.push(timer);
    return timer;
  },
  cancel: function (timer) { timer.cancelled = true; },
  request: function (done) {
    pending = done;
    callbacks.request(done);
  },
  apply: callbacks.apply,
  onStart: function () { scope.devActivity.boards = []; }
});
poller.start();
assert.strictEqual(healthLoads, 1);
assert.strictEqual(http.calls.length, 1);
assert.strictEqual(http.calls[0].url, 'f_dev.php?action=activity');
assert.strictEqual(scope.devActivity.loading, true);
poller.leave();
http.calls[0].ok({tag: 'stale', ok: true, boards: [{id: 1}]});
assert.strictEqual(applied.length, 0, 'leave drops the activity response');
assert.deepEqual(scope.devActivity.boards, [], 'stale activity does not repaint');
poller.start();
assert.ok(http.calls.length >= 1);
var freshAt = http.calls.length - 1;
http.calls[freshAt].ok({tag: 'fresh', ok: true, boards: [{id: 1}]});
assert.strictEqual(applied.length, 1);
assert.strictEqual(applied[0][0].tag, 'fresh');
assert.strictEqual(scope.devActivity.loading, false);
poller.start();
var off = http.calls[http.calls.length - 1];
scope.devTab = 'logs';
off.ok({tag: 'offtab', ok: true, boards: [{id: 3}]});
assert.strictEqual(applied.length, 1, 'off-tab response does not apply');
poller.leave();
callbacks.destroy();
poller.destroy();
assert.ok(timers.length > 0 && timers[timers.length - 1].cancelled === true || timers.some(function (timer) { return timer.cancelled; }));

assert.ok(app.indexOf("?v=" + tag) !== -1);
assert.ok(index.indexOf('ng/app.js?v=https-terminal-1') !== -1);
assert.ok(index.indexOf('ng/controllers.js?v=pool-catalog-status-1') !== -1);
assert.ok(index.indexOf('css/blakestream-dark.css?v=collector-wait-1') !== -1);
assert.ok(index.indexOf('ng/dev-activity.js?v=clock-index-1') !== -1);
assert.ok(css.indexOf('.bs-activity-health') !== -1);
assert.ok(css.indexOf('width: 7.5rem;') !== -1 && css.indexOf('width: 127.5rem;') !== -1);
assert.strictEqual(css.indexOf('4.75rem'), -1);

console.log('ok');
