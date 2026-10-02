'use strict';
const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'overlay/var/www/ng/controllers.js'), 'utf8');
const parent = {settings: {userAdjustableClocks: true}};
let requests = [], timers = [], closed = 0, saved = [], callback;
function timeout(fn) { timers.push(fn); return fn; }
timeout.cancel = () => {};
const http = {get: url => request(url), post: (url, body) => request(url, body)};
function request(url, body) {
  const item = {url, body}; requests.push(item);
  const chain = {success: fn => { item.success = fn; return chain; }, error: fn => { item.error = fn; return chain; }};
  return chain;
}
const context = {
  $scope: parent, $http: http, $timeout: timeout, $window: {location: {reload: () => {}}},
  bsSaveUserSettingsBaselineField: field => saved.push(field),
  document: {querySelector: () => null}, Alertify: {log: {create: () => {}}},
  angular: {extend: Object.assign}, Date, console
};
const start = source.indexOf('  $scope.settingsFirmware =');
const end = source.indexOf('  $scope.saveUserSettings =', start);
assert(start > 0 && end > start);
vm.runInNewContext(source.slice(start, end), context);
assert.equal(parent.devFirmwareRuntimeLabel('revert'), '~164s');
assert.equal(parent.devFirmwareRuntimeLabel('flash-custom'), '~164s');
assert.equal(parent.devFirmwareRuntimeLabel('verify'), '~45s');
parent.devFirmware.status = {flash_runtimes: {revert: {seconds: 165, measured: true}}};
parent.bsFirmwareStartJob('revert', {confirm: 'REVERT'}, 'Starting');
assert(parent.bsConfirmMsg.includes('Estimated total: ~165s'));
assert(parent.bsConfirmMsg.includes('last successful run on this miner'));
assert(timers.length > 1, 'elapsed ticker starts during a flash');
requests.at(-1).success({ok: true, started: true});
requests.at(-1).success({ok: true, running: true, op: 'revert', elapsed: 190,
  started_at: 1, runtime: {seconds: 165, measured: true}, progress: 'Reading <chip>'});
assert(parent.bsConfirmMsg.includes('190s elapsed'), 'server elapsed avoids client clock skew');
assert(parent.bsConfirmMsg.includes('Estimate exceeded; waiting for completion.'));
assert(parent.bsConfirmMsg.includes('Reading &lt;chip&gt;'), 'terminal output escaped');
assert.equal(parent.bsConfirmShowCancel, false, 'flash cannot be aborted');
parent.bsSettingsFirmwareOverlayClose();

const child = Object.create(parent), shared = parent.devFirmware;
const devStart = source.indexOf('  // ---------- Firmware tab');
const devEnd = source.indexOf('  $scope.devLogJournalSources', devStart);
parent.bsSettingsFirmwareOverlayClose = () => closed++;
parent.bsFirmwareStartJob = (op, body, message, done, fail) => { callback = {op, done, fail}; };
parent.bsConfirm = (msg, go) => go();
vm.runInNewContext(source.slice(devStart, devEnd), Object.assign({}, context, {
  $scope: child, bsDevTabFirmware: () => {}
}));
assert.strictEqual(child.devFirmware, shared, 'Dev Tools retains parent firmware state');
for (const [op, invoke] of [['revert', 'devFirmwareRevert'], ['flash-custom', 'devFirmwareFlashCustom'], ['verify', 'devFirmwareVerify']]) {
  child.devFirmware.dryOk = child.devFirmware.dryCustomOk = true;
  child.devFirmware.confirmText = 'REVERT'; child.devFirmware.enableText = 'ENABLE';
  child[invoke](); assert.equal(callback.op, op);
  callback.done({result: {ok: true, op, elapsed: 170}, progress: 'verified'});
  callback.fail({result: {ok: false, finished_at: 123, message: 'fixture failure'}});
}
assert.equal(closed, 6, 'all completion and failure callbacks use the exported close action');
assert.equal(saved.length, 4, 'write completion and failure reconcile the saved settings baseline');
assert(source.includes('former 400 preset.<br>Nominal MHz'), 'warning has the requested line break');
console.log('PASS firmware timing, elapsed ticker, parent/child state, completion callbacks and warning layout');
