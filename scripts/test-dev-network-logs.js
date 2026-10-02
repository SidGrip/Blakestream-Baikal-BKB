'use strict';
var assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
var source = fs.readFileSync(path.join(__dirname, '../overlay/var/www/ng/controllers.js'), 'utf8');
function section(start, end) {
  var a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a); return source.slice(a, b);
}
var calls = [], callbacks = {}, errors = [];
var scope = {$on: function(event, callback) { callbacks[event] = callback; }};
var context = {
  $scope: scope, devToolsDestroyed: false,
  angular: {isArray: Array.isArray, forEach: function(a, f) { a.forEach(f); }, extend: Object.assign},
  Alertify: {log: {error: function(e) { errors.push(e); }}},
  $http: {get: function(url, options) {
    var request = {url: url, options: options}; calls.push(request);
    request.success = function(fn) { request.ok = fn; return request; };
    request.error = function(fn) { request.fail = fn; return request; }; return request;
  }}
};
vm.createContext(context);
vm.runInContext(section('  $scope.devProbeRunning = false;', '  // ---------- API console'), context);
scope.devProbeRun(); scope.devProbeRun();
assert.equal(calls.length, 1, 'double click cannot launch a second run');
var endpoints = [{key: 'v1|pool.example|3334', name: 'A, B', names: ['A', 'B'], host: 'pool.example', port: 3334},
  {key: 'v1|pool.example|3335', name: 'C', names: ['C'], host: 'pool.example', port: 3335}];
calls[0].ok({ok: true, pools: endpoints});
assert.equal(calls.length, 3, 'one request per unique endpoint');
assert(calls[1].url.includes(encodeURIComponent(endpoints[0].key)));
calls[1].ok({ok: true, result: Object.assign({tcp: {ok: true}}, endpoints[0])});
calls[2].fail();
assert.equal(scope.devProbeRunning, false);
assert.equal(scope.devProbeResults[0].names.length, 2);
assert.equal(scope.devProbeResults[1].tcp.ok, false);
scope.devProbeRun();
var old = calls[calls.length - 1];
callbacks.$destroy(); context.devToolsDestroyed = true;
var count = calls.length; old.ok({ok: true, pools: endpoints});
assert.equal(calls.length, count, 'late list response cannot launch probes after destroy');

scope.devTab = 'logs'; context.devLogDestroyed = false; context.devToolsDestroyed = false;
context.devLogGeneration = 0; context.devLogFilePending = null;
scope.settings = {};
vm.runInContext(section('  $scope.devLog = {', '  $scope.devLogIsSecurity ='), context);
vm.runInContext(section('  var devLogPoll = function', '  var devLogTimer ='), context);
scope.devLog.source = 'path:/run/blakestream/clock-history.json';
scope.devLogReset(); context.devLogPoll(); context.devLogPoll();
var readable = calls[calls.length - 1];
assert.equal(calls.filter(function(r) { return r.url.includes('file_view'); }).length, 1, 'no overlapping read for same format');
assert.equal(readable.options.params.format, 'readable');
scope.devLogSetFormat('raw'); context.devLogPoll();
var raw = calls[calls.length - 1];
raw.ok({ok: true, path: 'clock-history.json', format: 'raw', text: '{"version":2}', shown: 13, size: 13});
readable.ok({ok: true, format: 'readable', text: 'STALE', rows: 1});
assert.equal(scope.devLog.text, '{"version":2}', 'old readable response cannot overwrite raw selection');
scope.devLogSetFormat('readable'); context.devLogPoll();
calls[calls.length - 1].ok({ok: false, error: 'History exceeds readable limit'});
assert.equal(scope.devLog.text, '', 'parse error does not leave previous content masquerading as current');
assert.equal(scope.devLog.status, 'History exceeds readable limit');
context.devLogPoll();
var pending = calls[calls.length - 1];
scope.devLog.paused = true;
pending.ok({ok: true, format: 'readable', text: 'PAUSED', rows: 1});
assert.equal(scope.devLog.text, '');
scope.devLog.paused = false; context.devLogPoll();
var gone = calls[calls.length - 1]; context.devLogDestroyed = true;
gone.ok({ok: true, format: 'readable', text: 'DESTROYED', rows: 1});
assert.equal(scope.devLog.text, '');
context.devLogDestroyed = false;
scope.devMeasuredVisible = true;
var beforeMeasured = calls.length;
context.devLogPoll();
assert.equal(calls.length, beforeMeasured, 'hidden log viewer does not poll');
scope.devMeasuredVisible = false;
scope.devLog.source = 'sgminer'; context.devLogPoll();
var oldTail = calls[calls.length - 1];
scope.devMeasuredVisible = true; scope.devLogReset();
oldTail.ok({ok:true, lines:['old tail'], next_offset:10});
assert.equal(scope.devLog.text, '', 'tail response cannot reappear in measured view');
assert.deepEqual(errors, []);
console.log('PASS Network request grouping and clock-log format/pause/destroy races');
