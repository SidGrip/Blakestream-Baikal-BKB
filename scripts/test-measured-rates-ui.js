'use strict';
var assert = require('assert'), fs = require('fs'), vm = require('vm'), path = require('path');
var source = fs.readFileSync(path.join(__dirname, '../overlay/var/www/ng/controllers.js'), 'utf8');
var begin = source.indexOf('  $scope.devStorage = {'), end = source.indexOf('  // Storage keeps its own cadence.', begin);
assert(begin > 0 && end > begin);
var calls = [], errors = [], confirm, confirmText, confirmOptions, reloads = 0;
var scope = {bsConfirm: function(text, yes, options) { confirm = yes; confirmText = text; confirmOptions = options; }};
var context = {$scope: scope, devToolsDestroyed: false, angular: {
  forEach: function(xs, fn) { xs.forEach(fn); }
}, $http: {post: function(url, body) {
  var req = {url: url, body: body}; calls.push(req);
  req.success = function(fn) { req.ok = fn; return req; };
  req.error = function(fn) { req.fail = fn; return req; }; return req;
}}, Alertify: {log: {error: function(e) { errors.push(e); }, success: function() {}}},
  $timeout: function(fn) { fn(); }};
vm.createContext(context); vm.runInContext(source.slice(begin, end), context);
scope.devStorageLoad = function() { reloads++; };
var initialRow = {target_key: 'measured-rates', target: 'SD'};
scope.devStorageSetTarget(initialRow);
assert.equal(initialRow.target, 'RAM', 'missing saved target defaults to RAM before confirmation');
assert.equal(calls.length, 0);
assert(confirmText.includes('SD card'));
scope.devMeasured = {target: 'SD'}; scope.devMeasuredCsrf = 'csrf-token';
var row = {target_key: 'measured-rates', target: 'RAM'};
scope.devStorageSetTarget(row);
assert.equal(row.target, 'SD', 'selector remains on committed destination before confirmation');
assert.equal(calls.length, 0);
assert(confirmText.includes('lost on reboot'));
confirm();
assert.equal(calls.length, 1);
assert.equal(calls[0].body.op, 'target'); assert.equal(calls[0].body.argument, 'RAM');
assert.equal(calls[0].body.csrf, 'csrf-token');
scope.devMeasuredUpdate('clear'); assert.equal(calls.length, 1, 'busy blocks overlap');
calls[0].fail(); assert.equal(scope.devMeasuredBusy, false); assert.equal(reloads, 1);
scope.devMeasuredClear(); assert.equal(calls.length, 1, 'clear requires confirmation');
assert(confirmText.includes('Miner counters, clocks and lab references stay unchanged'));
confirm(); calls[1].ok({ok: true});
scope.devMeasuredResetBoard(3); assert.equal(calls.length, 2);
scope.devMeasuredResetBoard(1); assert(confirmText.includes('Other boards stay unchanged'));
confirm(); assert.equal(calls[2].body.argument, '1'); calls[2].ok({ok: true});
scope.devStorageClearAll('RAM');
assert(confirmText.includes('saved to SD first'));
confirm(); assert.equal(calls[3].body.csrf, 'csrf-token');
context.devToolsDestroyed = true;
scope.devMeasuredUpdate('flush'); assert.equal(calls.length, 4);
var html = fs.readFileSync(path.join(__dirname, '../overlay/var/www/partials/status.html'), 'utf8');
assert(!html.includes('d.EstimateSource') && html.includes('d.Accepted15mSeconds'));
assert.equal((html.match(/class="bs-tip bs-rate-detail" tabindex="0"/g) || []).length, 8,
  'accepted coverage and error percentages retain their existing details');
assert(!/<small[^>]*>[^<]*\{\{(?:d|status\.dtot)\.(?:EstimateSource|Accepted(?:15m|1h)Seconds)/.test(html),
  'source and coverage do not add visible second lines');
assert(html.includes("bsAcceptedCoverage(status.dtot, '15m')") && html.includes('aria-label="Total accepted'),
  'partial coverage and totals stay accessible');
assert(!html.includes('bs-measured-status') && !html.includes('Measured Rates:'),
  'storage status stays off the main dashboard');
var devHtml = fs.readFileSync(path.join(__dirname, '../overlay/var/www/partials/dev-tools.html'), 'utf8');
assert(devHtml.includes('devMeasured.error'), 'storage errors remain visible in Dev Tools');
assert(devHtml.includes("devMeasuredDownload('readable')") && devHtml.includes('Download report'));
assert(devHtml.includes("devMeasuredDownload('json')") && devHtml.includes('Clear All Data'));
assert(devHtml.includes('ng-model="devMeasuredFilter.board"') && devHtml.includes('devMeasuredRows() | orderBy:'));
var boardsPanel = devHtml.slice(devHtml.indexOf('ng-switch-when="boards"'), devHtml.indexOf('ng-switch-when="logs"'));
var logsPanel = devHtml.slice(devHtml.indexOf('ng-switch-when="logs"'), devHtml.indexOf('ng-switch-when="network"'));
assert(boardsPanel.includes('devMeasuredResetBoard(b.id)') && boardsPanel.includes('bs-activity-board-heading'));
assert(boardsPanel.includes('ng-disabled="devMeasuredBusy || !devActivity.csrf"'));
assert(!logsPanel.includes('devMeasuredResetBoard'), 'replacement controls belong only to Boards');
assert(devHtml.includes('Write buffer') && !devHtml.includes('Save now'));
assert(devHtml.includes('ng-disabled="devMeasuredBusy || !devMeasured.unsaved"'));
assert(devHtml.includes('class="bs-history-scroll bs-measured-scroll" tabindex="0" role="region"'),
  'measured rows use the keyboard-accessible shared sticky-header scroll area');
var css = fs.readFileSync(path.join(__dirname, '../overlay/var/www/css/blakestream-dark.css'), 'utf8');
assert(/\.bs-measured-scroll\s*\{[^}]*max-height:\s*420px;[^}]*overflow:\s*auto;/.test(css));
scope.devLogSources = [{id:'sgminer', label:'sgminer'}]; scope.devLog = {};
var resets = 0; scope.devLogReset = function() { resets++; };
scope.devStorageView({present:true, view:'measured-rates'});
assert.equal(scope.devMeasuredVisible, true);
scope.devStorageView({present:true, view:'sgminer'});
assert.equal(scope.devMeasuredVisible, false);
scope.devStorageView({present:true, view:'measured-rates'});
scope.devStorageView({present:true, path:'/run/blakestream/clock-history.json', name:'clock-history.json'});
assert.equal(scope.devMeasuredVisible, false);
assert(resets >= 4);
assert(devHtml.includes('class="bs-measured-band" ng-if="devMeasuredVisible"'));
assert(devHtml.includes('ng-if="!devMeasuredVisible" class="bs-log-viewer"'));
assert(!html.includes('data-tip="Rejected:') && !html.includes('data-tip="HW errors:'));
assert(html.includes('Accepted shares per minute') && html.includes('d.Utility|number:2'),
  'utility remains share rate with compact display precision');
context.devToolsDestroyed = false;
scope.devMeasuredBusy = false;
scope.devMeasuredCsrf = '';
scope.devActivity = {csrf:'boards-token'};
var beforeReplace = calls.length;
scope.devMeasuredResetBoard(0);
assert.equal(calls.length, beforeReplace, 'opening/cancelling the confirmation never resets history');
assert.equal(confirmOptions.title, 'Board 0 Replaced');
assert(confirmText.includes('Board 0') && confirmText.includes('cannot be undone') && confirmText.includes('Mining, miner counters'));
assert.equal(confirmOptions.yesLabel, 'Reset board history');
confirm();
assert.equal(calls[beforeReplace].body.argument, '0');
assert.equal(calls[beforeReplace].body.csrf, 'boards-token', 'Boards reset does not depend on visiting Logs');
var lastConfirm = confirm;
scope.devMeasuredResetBoard(1);
assert.equal(confirm, lastConfirm, 'busy reset cannot open another confirmation');
calls[beforeReplace].ok({ok:true});
scope.devMeasured.contexts = [0,1,2,10].map(function(id) {return {context:{board:id}, context_id:'row-' + id};});
var beforeFilter = calls.length;
assert.equal(scope.devMeasuredFilter.board, 'all');
assert.equal(scope.devMeasuredRows().length, 4);
for (var board of ['0','1','2']) {
  scope.devMeasuredFilter.board = board;
  assert.equal(scope.devMeasuredRows().length, 1);
  assert.equal(scope.devMeasuredRows()[0].context.board, Number(board));
  for (var format of ['readable','json']) {
    assert.equal(scope.devMeasuredDownload(format), 'f_dev.php?action=measured_rates_download&format=' + format + '&board=' + board);
  }
}
scope.devMeasured.contexts = [];
assert.equal(scope.devMeasuredRows().length, 0, 'empty selection has no rows');
scope.devMeasuredFilter.board = 'all';
assert(scope.devMeasuredDownload('readable').endsWith('&board=all'));
assert.equal(calls.length, beforeFilter, 'filtering never writes data');
console.log('PASS measured rates confirmation, committed target, busy guard, scope and provenance');
