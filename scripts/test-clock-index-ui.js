'use strict';

var assert = require('assert');
var fs = require('fs');
var path = require('path');
var vm = require('vm');
var root = path.resolve(__dirname, '..');
var source = fs.readFileSync(path.join(root, 'overlay/var/www/ng/controllers.js'), 'utf8');
var start = source.indexOf('  $scope.bsClockLimits =');
var end = source.indexOf('  $scope.bsCreateClockProfileId =', start);
assert(start >= 0 && end > start);
var errors = [], writes = [], confirm, confirmationText;
var scope = {
  options: [{key: 'baikal-options', value: '400:40:55'}],
  settings: {},
  sync: function(key, value) { writes.push(JSON.parse(JSON.stringify(value))); },
  bsConfirm: function(text, apply) { confirm = apply; confirmationText = text; }
};
vm.runInNewContext(source.slice(start, end), {
  $scope: scope,
  angular: {isArray: Array.isArray, toJson: JSON.stringify, forEach: function(a, f) { a.forEach(f); }},
  Alertify: {log: {error: function(message) { errors.push(message); }}},
  console: console
});
scope.bsRefreshClockFromOptions();
assert.strictEqual(scope.bsClockSettingsChanged(), false);
assert.strictEqual(scope.bsGlobalClockIndex.mhz, 400, 'loading default must not expose the 480 bridge echo');
assert.deepStrictEqual([scope.bsBoardNominalMHz.board0, scope.bsBoardNominalMHz.board1, scope.bsBoardNominalMHz.board2], [400,400,400]);
scope.bsSelectClockMode('individual');
assert.strictEqual(scope.bsClockMode, 'individual');
scope.bsBoardClockIndex = {board0: 28, board1: 29, board2: 30};
scope.bsLinkedClockChanged(0, 'clock_index');
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, true, 'editing enables the override without a mode switch');
assert.deepStrictEqual(Array.from(scope.bsClockIndexValues()), [28, 29, 30]);
scope.bsSaveClockSettings();
assert.strictEqual(writes.length, 0, 'confirmation must precede experimental save');
assert.strictEqual(typeof confirm, 'function');
confirm();
assert.strictEqual(writes.length, 1);
assert.strictEqual(scope.options[scope.bsOptionIndex('baikal-board-clock-index')].value, '28:29:30');
assert.strictEqual(scope.options[scope.bsOptionIndex('baikal-experimental-clock-index')].value, true);
scope.bsRefreshClockFromOptions();
assert.strictEqual(typeof scope.bsBoardClockIndex.board0, 'number', 'number input requires numeric model');
assert.strictEqual(scope.bsClockSettingsChanged(), false);
scope.bsBoardClockIndex.board0 = 27;
assert.strictEqual(scope.bsClockSettingsChanged(), true);
scope.bsBoardClockIndex.board0 = 28;
assert.strictEqual(scope.bsClockSettingsChanged(), false);

[null, undefined, true, '', '08', '29x', '29.5', ' 29', '29\n', 7, 33, 29.5, NaN, Infinity].forEach(function(value) {
  scope.bsBoardClockIndex.board0 = value;
  assert.strictEqual(scope.bsClockIndexValues(), null, 'must reject ' + String(value));
  scope.bsSaveClockSettings();
  assert.strictEqual(writes.length, 1, 'invalid input must not save');
});
scope.bsBoardClockIndex = {board0: 8, board1: 32, board2: '30'};
assert.deepStrictEqual(Array.from(scope.bsClockIndexValues()), [8, 32, 30]);
scope.bsSelectClockMode('global');
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, false, 'scope selection follows that scope\'s draft');
scope.bsDefaultClock('global');
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, false);
assert.deepStrictEqual(Array.from(scope.bsBoardClockIndex && [scope.bsBoardClockIndex.board0, scope.bsBoardClockIndex.board1, scope.bsBoardClockIndex.board2]), [8, 32, '30'], 'global default preserves individual drafts');
scope.bsSaveClockSettings();
assert.strictEqual(writes.length, 1, 'normal mode also requires confirmation');
confirm();
assert.strictEqual(writes.length, 2);
assert.strictEqual(scope.bsOptionIndex('baikal-board-clock-index'), -1);
assert.strictEqual(scope.bsOptionIndex('baikal-experimental-clock-index'), -1);
assert.strictEqual(scope.options[scope.bsOptionIndex('baikal-options')].value, '400:40:55');
scope.status = {minerUp: true, devs: [
  {ID: 0, SentClockIndex: 26, CommandVerified: true},
  {ID: 1, SentClockIndex: 30, CommandVerified: false},
  {ID: 2, SentClockIndex: 16, CommandVerified: true}
]};
scope.bsStatusReceivedAt = Date.now();
scope.bsSelectClockMode('individual');
assert.deepStrictEqual(Array.from(scope.bsClockIndexValues()), [8, 32, 30], 'scope changes preserve drafts instead of replacing them with telemetry');
assert.strictEqual(scope.bsClockCommandState(0), 'Sent Index 26 / confirmed');
assert.strictEqual(scope.bsClockCommandState(1), 'Sent Index 30 / unconfirmed');
scope.bsBoardClockIndex.board0 = 31;
assert.strictEqual(scope.bsClockIndexTrialState(0), 'Untested on this board');
scope.bsSaveClockSettings();
assert(!confirmationText.includes('(untested on this board)') && confirmationText.includes('Index 26 &rarr; Index <b>31</b>'));
assert(!/\(un?confirmed\)|\(confirmed\)|\(unverified\)|\/ experimental/.test(confirmationText));
scope.bsBoardClockIndex.board0 = 32;
confirm();
assert.strictEqual(writes.length, 2, 'changed fields cannot alter a previously confirmed request');
scope.bsDefaultClock('individual');
assert.strictEqual(writes.length, 2, 'default only stages values');
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, false);
scope.bsSaveClockSettings();
assert.strictEqual(writes.length, 2, 'default save requires confirmation');
confirm();
assert.strictEqual(writes.length, 3);
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, false);
assert.strictEqual(scope.bsOptionIndex('baikal-board-clock-index'), -1);
scope.bsStatusReceivedAt -= 121000;
assert.strictEqual(scope.bsClockCommandState(0), 'Command unavailable', 'stale commands are not confirmed');

var profileEnd = source.indexOf('  $scope.bsClampFanPercent =', end);
assert(profileEnd > end);
scope.bsNormalizeFanCurveValues = function() { return {min: 40, target: 45, midTemp: 47, midFan: 70, full: 50}; };
var profilePosts = [];
vm.runInNewContext(source.slice(end, profileEnd), {
  $scope: scope,
  angular: {isArray: Array.isArray},
  $http: {post: function(url, value) {
    profilePosts.push(JSON.parse(JSON.stringify(value)));
    return {success: function(fn) { fn({ok: true, profiles: value.profiles}); return {error: function() {}}; }};
  }},
  Alertify: {log: {error: function(message) { errors.push(message); }, success: function() {}}}
});
scope.bsClockProfileName = 'Board trial';
scope.bsClockMode = 'individual';
scope.bsExperimentalClockIndex.enabled = true;
scope.bsBoardClockIndex = {board0: 8, board1: 30, board2: 32};
scope.bsSaveClockProfile();
var saved = profilePosts[0].profiles[0];
assert.strictEqual(saved.version, 2);
assert.strictEqual(saved.mapping, 'bkb-v1-clock-index-1');
assert.strictEqual(saved.experimental, true);
assert.deepStrictEqual(saved.clock_indices, [8, 30, 32]);
assert.strictEqual(writes.length, 3, 'saving a profile does not apply clocks');
scope.bsBoardClockIndex.board0 = 20;
scope.bsApplyClockProfile();
assert.strictEqual(writes.length, 3, 'profile requires confirmation');
confirm();
assert.strictEqual(writes.length, 5, 'settings and options are saved only after confirmation');
assert.strictEqual(scope.options[scope.bsOptionIndex('baikal-board-clock-index')].value, '8:30:32');
scope.bsClockProfiles[0].mapping = 'unknown';
scope.bsApplyClockProfile();
assert.strictEqual(writes.length, 5);
assert(errors[errors.length - 1].includes('unsupported mapping'));
scope.bsClockProfiles = [{id: 'old', name: 'Normal legacy profile', clock: 400, mode: 'global'}];
scope.bsSelectedClockProfileId = 'old';
scope.bsApplyClockProfile();
assert.strictEqual(writes.length, 5);
confirm();
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, false);
assert.strictEqual(scope.bsOptionIndex('baikal-board-clock-index'), -1);
assert.strictEqual(writes.length, 7);
var status = fs.readFileSync(path.join(root, 'overlay/var/www/partials/status.html'), 'utf8');
var history = fs.readFileSync(path.join(root, 'overlay/var/www/partials/clock-history.html'), 'utf8');
var miner = fs.readFileSync(path.join(root, 'overlay/var/www/partials/miner.html'), 'utf8');
var css = fs.readFileSync(path.join(root, 'overlay/var/www/css/blakestream-dark.css'), 'utf8');
var rateTooltip = css.match(/#statusDevs \.bs-rate-detail\.bs-tip\[data-tip\]::after\s*\{([^}]+)\}/);
assert(rateTooltip && /width:\s*max-content;/.test(rateTooltip[1]), 'rate tooltips fit their content');
assert(/white-space:\s*nowrap;/.test(rateTooltip[1]), 'rate tooltips remain on one line');
var deviceTable = status.slice(status.indexOf('id="statusDevs"'), status.indexOf('</table>', status.indexOf('id="statusDevs"')));
assert(!deviceTable.includes('bs-rate-source'), 'device rates and error counts have no permanent sublines');
assert(!deviceTable.includes('data-tip="Rejected:'));
assert(!deviceTable.includes('data-tip="HW errors:'));
assert(!miner.includes('name="clockPreset"'), 'no Normal/Custom selector');
assert(!miner.includes('bsRestoreNormalClock'), 'no separate Restore Normal button');
assert(miner.includes('aria-label="Default global clock"'));
assert(miner.includes('aria-label="Default individual clocks"'));
var clockOutputs = miner.match(/<output\b[^>]*class="bs-clock-estimate"[^>]*>/g) || [];
assert.strictEqual(clockOutputs.length, 6, 'global and both board modes retain labeled readouts');
clockOutputs.forEach(function(output) {
  assert(output.includes('aria-label='), 'readout remains accessible');
  assert(!/\bbs-tip\b|data-tip=|\btitle=|tabindex=/.test(output), 'readout has no hover or focus explanation');
});
assert(!miner.includes('bsClockEstimateTip'), 'clock explanation is no longer rendered');
assert(!miner.includes('Unsaved settings'), 'amber save-button pulse is the only visible dirty-state indicator');
assert(miner.includes("'bs-needs-save': bsClockSettingsChanged()"), 'clock save-button pulse remains');
assert.strictEqual((miner.match(/class="bs-clock-index"/g) || []).length, 3, 'Index has its own frame in all modes');
assert(/\.bs-clock-readouts,\s*\.bs-linked-clock-pair \.bs-clock-index\s*\{[^}]*border: 1px solid/.test(css), 'Index and estimates share frame styling');
assert(!miner.includes('bsClockIndexTrialState'), 'no board qualification subline in clock controls');
assert.strictEqual((miner.match(/class="bs-clock-readouts"/g) || []).length, 3, 'global and both board modes have bordered estimates');
assert(miner.indexOf('aria-label="Global clock index"') < miner.indexOf('aria-label="Global estimated GH/s"'));
assert(miner.indexOf('aria-label="Global estimated GH/s"') < miner.indexOf('aria-label="Global estimated MHz"'));
assert(!miner.includes('ng-disabled="!bsExperimentalClockIndex.enabled"'), 'active fields are always editable');
assert(status.includes('bsToggleDeviceClockView()'));
assert(status.includes('bsDeviceClockText(d)'));
var clockToggle = status.match(/<button[^>]*class="bs-device-clock-toggle"[\s\S]*?<\/button>/);
assert(clockToggle && !clockToggle[0].includes('data-tip='), 'clock toggle has no tooltip');
assert(!clockToggle[0].includes(' title='), 'clock toggle has no native tooltip');
var clockCell = status.match(/<td[^>]*class="bs-device-clock-cell"[\s\S]*?<\/td>/);
assert(clockCell && !clockCell[0].includes('data-tip='));
assert(!clockCell[0].includes(' title='), 'clock values have no native tooltip');
assert(history.includes('Index {{b.requested_clock_index}}'));
assert(history.includes('ng-if="!b.experimental"'));

for (var index = 8; index <= 32; index++) {
  assert.strictEqual(scope.bsNominalFromClockIndex(index), index === 30 ? 400 : null);
}
assert.strictEqual(scope.bsClockIndexFromNominal(400), 30);
assert.strictEqual(scope.bsClockIndexFromNominal('400'), 30);
[null, undefined, true, '', '0400', '400x', '400.0', ' 400', '400\n', 260, 390, 410, 440, 480, 500, NaN].forEach(function(value) {
  assert.strictEqual(scope.bsClockIndexFromNominal(value), null, 'invalid nominal MHz ' + String(value));
});
scope.bsExperimentalClockIndex.enabled = true;
scope.bsSelectClockMode('global');
scope.bsGlobalClockIndex.index = 26;
scope.bsLinkedClockChanged('global', 'clock_index');
assert.strictEqual(scope.bsGlobalClockIndex.index, 26);
assert.strictEqual(scope.bsGlobalClockIndex.mhz, null, 'unmapped Index must not borrow a bridge echo as MHz');
assert.deepStrictEqual(Array.from(scope.bsClockIndexValues()), [26, 26, 26]);
assert.strictEqual(scope.bsClockIndexTrialState(0), 'Preliminary board data');
assert.strictEqual(scope.bsClockIndexTrialState(1), 'Untested on this board');
assert.strictEqual(scope.bsClockIndexTrialState(2), 'Untested on this board');
scope.bsGlobalClockIndex.index = 30;
scope.bsLinkedClockChanged('global', 'clock_index');
assert.strictEqual(scope.bsGlobalClockIndex.mhz, 400);
scope.bsGlobalClockIndex.mhz = 400;
scope.bsLinkedClockChanged('global', 'mhz');
assert.strictEqual(scope.bsGlobalClockIndex.index, 30, '400 must always select the default Index, never 22');
scope.bsGlobalClockIndex.index = 22;
scope.bsLinkedClockChanged('global', 'clock_index');
var beforeGlobal = writes.length;
scope.bsSaveClockSettings();
assert.strictEqual(writes.length, beforeGlobal, 'editing and opening confirmation must not write');
assert(confirmationText.includes('individual commands'));
assert(confirmationText.includes('not measured frequency.<br>Instability or hardware damage is possible.</p>'));
assert(confirmationText.includes('MHz unmapped') && !confirmationText.includes('null nominal'));
confirm();
assert.strictEqual(writes.length, beforeGlobal + 1);
assert.strictEqual(scope.options[scope.bsOptionIndex('baikal-clock-mode')].value, 'individual');
assert.strictEqual(scope.options[scope.bsOptionIndex('blakestream-clock-scope')].value, 'global');
assert.strictEqual(scope.options[scope.bsOptionIndex('baikal-board-clock-index')].value, '22:22:22');
assert.strictEqual(scope.options[scope.bsOptionIndex('baikal-options')].value, '400:40:55');
scope.bsRefreshClockFromOptions();
assert.strictEqual(scope.bsClockMode, 'global');
assert.strictEqual(scope.bsGlobalClockIndex.mhz, null, 'saved canonical Index 22 must stay 22 with unknown MHz');
assert.strictEqual(scope.bsGlobalClockIndex.index, 22);
assert.strictEqual(scope.bsClockSettingsChanged(), false);
scope.bsGlobalClockIndex.mhz = 441;
scope.bsLinkedClockChanged('global', 'mhz');
assert.strictEqual(scope.bsClockIndexValues(), null);
scope.bsSaveClockSettings();
assert.strictEqual(writes.length, beforeGlobal + 1, 'invalid MHz cannot leave an old Index ready to save');
scope.bsGlobalClockIndex.mhz = 400;
scope.bsLinkedClockChanged('global', 'mhz');
scope.bsSaveClockSettings();
scope.bsGlobalClockIndex.mhz = null;
scope.bsLinkedClockChanged('global', 'mhz');
confirm();
assert.strictEqual(writes.length, beforeGlobal + 1, 'stale confirmation cannot apply after paired edit');
scope.bsSelectClockMode('individual');
scope.bsBoardClockIndex.board1 = 21;
scope.bsLinkedClockChanged(1, 'clock_index');
assert.strictEqual(scope.bsBoardClockIndex.board1, 21);
assert.strictEqual(scope.bsBoardClockIndex.board0, 22);
scope.bsBoardClockIndex.board2 = 16;
scope.bsLinkedClockChanged(2, 'clock_index');
assert.strictEqual(scope.bsBoardNominalMHz.board2, null);
assert.deepStrictEqual(Array.from(scope.bsClockIndexValues()), [22, 21, 16]);
scope.bsSelectClockMode('global');
scope.bsGlobalClockIndex.index = 26;
scope.bsLinkedClockChanged('global', 'clock_index');
scope.bsClockProfileName = 'All boards Index 26';
scope.bsNewClockProfile();
scope.bsSaveClockProfile();
var globalProfile = scope.bsSelectedClockProfile();
assert.strictEqual(globalProfile.scope, 'global');
assert.strictEqual(globalProfile.mode, 'individual');
assert.deepStrictEqual(Array.from(globalProfile.clock_indices), [26, 26, 26]);
scope.bsApplyClockProfile();
confirm();
assert.strictEqual(scope.bsClockMode, 'global');
assert.strictEqual(scope.bsGlobalClockIndex.mhz, null);
assert.strictEqual(scope.bsGlobalClockIndex.index, 26, 'profile indices must not be remapped by a label correction');
var beforeBadProfile = writes.length;
globalProfile.clock_indices = [26, 30, 26];
scope.bsApplyClockProfile();
assert.strictEqual(writes.length, beforeBadProfile);
assert(errors[errors.length - 1].includes('unsupported mapping'));
var resetWrites = writes.length;
scope.bsClock.recover = 41;
scope.bsClock.cutoff = 54;
scope.bsDefaultClock('individual');
assert.strictEqual(scope.bsGlobalClockIndex.index, 26, 'individual default preserves global draft');
assert.deepStrictEqual(Array.from(scope.bsClockIndexValues()), [30, 30, 30]);
assert.strictEqual(scope.bsClock.recover, 41);
assert.strictEqual(scope.bsClock.cutoff, 54);
scope.bsSelectClockMode('global');
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, true, 'switching back to a non-default draft requires the override');
scope.bsDefaultClock('global');
assert.strictEqual(writes.length, resetWrites);
scope.bsSaveClockSettings();
assert.strictEqual(writes.length, resetWrites);
confirm();
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, false);
assert.strictEqual(scope.bsGlobalClockIndex.index, 30);
assert.strictEqual(scope.bsGlobalClockIndex.mhz, 400);
assert.strictEqual(scope.options[scope.bsOptionIndex('baikal-options')].value, '400:41:54');
assert.strictEqual(scope.bsOptionIndex('baikal-board-clock-index'), -1);
scope.bsGlobalClockIndex.mhz = '';
scope.bsLinkedClockChanged('global', 'mhz');
assert.strictEqual(scope.bsExperimentalClockIndex.enabled, true, 'blank fields cannot silently select default');
scope.bsSaveClockSettings();
assert.strictEqual(writes.length, resetWrites + 1);

var statusStart = source.indexOf('  $scope.bsDeviceClockView =');
var statusEnd = source.indexOf('  // Blakestream-GaintB: load saved pools', statusStart);
var storedView;
function statusScope(storage) {
  var s = {};
  vm.runInNewContext(source.slice(statusStart, statusEnd), {$scope:s, localStorage:storage});
  return s;
}
var storage = {getItem:function() { return storedView; }, setItem:function(key, value) { storedView = value; }};
var display = statusScope(storage);
assert.strictEqual(display.bsDeviceClockView, 'clock');
assert.strictEqual(display.bsDeviceClockText({Clock:400,SentClockIndex:30}), '400MHz');
display.bsToggleDeviceClockView();
assert.strictEqual(display.bsDeviceClockText({Clock:400,SentClockIndex:30,RequestedClockIndex:22}), '30');
assert.strictEqual(statusScope(storage).bsDeviceClockView, 'clock_index', 'display selection survives reload');
[undefined, null, 0, 7, 33, true, '30x', '030', '30\n'].forEach(function(value) {
  assert.strictEqual(display.bsDeviceClockText({SentClockIndex:value,RequestedClockIndex:30}), '\u2014');
});
display.bsToggleDeviceClockView();
for (var sent = 8; sent <= 32; sent++) {
  assert.strictEqual(display.bsDeviceClockText({ExperimentalClock:true,SentClockIndex:sent}), sent === 30 ? '400MHz' : '\u2014');
}
assert.strictEqual(display.bsDeviceClockText({ExperimentalClock:true,SentClockIndex:8,EstimatedClockMHz:210.4}), '210MHz');
assert.strictEqual(display.bsDeviceClockText({ExperimentalClock:true,SentClockIndex:32,EstimatedClockMHz:418.7}), '419MHz');
[null, undefined, '210', 0, -1, Infinity, NaN].forEach(function(mhz) {
  assert.strictEqual(display.bsDeviceClockText({ExperimentalClock:true,SentClockIndex:8,EstimatedClockMHz:mhz}), '\u2014');
});
assert.strictEqual(display.bsDeviceClockText({ExperimentalClock:true,SentClockIndex:0,EstimatedClockMHz:210}), '\u2014');
assert.strictEqual(display.bsDeviceClockText({Clock:0}), '\u2014');
storedView = 'invalid';
assert.strictEqual(statusScope(storage).bsDeviceClockView, 'clock');
var denied = statusScope({getItem:function() { throw Error('denied'); },setItem:function() { throw Error('denied'); }});
denied.bsToggleDeviceClockView();
assert.strictEqual(denied.bsDeviceClockView, 'clock_index');
scope.status = {minerUp: true, clockBaseline: {source: 'lab-example', algorithm: 'blake256r8', rows: [
  {clock_index: 30, ghs: 60, estimated_mhz: 400, measured_boards: [0, 1, 2], accepted_shares: 2400, board_windows: 3, hw_errors: 0},
  {clock_index: 32, ghs: 66, estimated_mhz: 440, measured_boards: [1], accepted_shares: 850, board_windows: 1, hw_errors: 0}
]}, devs: [0, 1, 2].map(function(id) { return {ID: id, ClockBaselineCompatible: true}; })};
scope.bsStatusReceivedAt = Date.now();
scope.bsClockMode = 'global';
scope.bsGlobalClockIndex.index = 32;
scope.bsLinkedClockChanged('global', 'clock_index');
assert.strictEqual(scope.bsClockMHzText('global'), '~440');
assert.strictEqual(scope.bsClockGHsText('global', true), '~198.0');
assert.strictEqual(scope.bsClockGHsText('global', false), '~66.0');
assert.strictEqual(scope.bsClockGHsText(0, false), '~66.0', 'shared example applies to other boards');
assert.strictEqual(scope.bsClockIndexTrialState(0), 'Untested on this board');
assert.strictEqual(scope.bsClockIndexTrialState(1), 'Preliminary board data');
var previewRows = scope.bsClockPreview([32, 32, 32], true).split('<br>');
assert(!previewRows[0].includes('untested on this board'));
assert(!previewRows[1].includes('untested on this board'), 'confirmation omits board qualification labels');
assert(!previewRows[2].includes('untested on this board'));
assert(previewRows[0].includes('Index <b>32</b> ~66.0 GH/s lab estimate / ~440 MHz'), 'compact estimated-rate preview');
assert(!/unverified|confirmed|experimental/.test(previewRows.join('')), 'qualification detail is not repeated in each board row');
scope.status.clockBaseline.rows[1].estimated_mhz = null;
assert(scope.bsClockPreview([32, 32, 32], true).includes('~66.0 GH/s lab estimate / MHz unmapped'), 'missing MHz is not invented');
scope.status.clockBaseline.rows[1].estimated_mhz = 440;
assert(scope.bsClockPreview([30, 30, 30], false).includes('~60.0 GH/s lab estimate / 400 MHz'), 'stock anchor remains nominal 400');
var guide = fs.readFileSync(path.join(root, 'TECHNICAL-GUIDE.md'), 'utf8');
assert(guide.includes('not a measured all-board result'), 'global extrapolation stays documented');
assert(guide.includes('`measured_boards`') && guide.includes('`board_windows`') && guide.includes('`hw_errors`'), 'baseline provenance stays documented');
assert(guide.includes('The **Est. MHz** readout is unverified'), 'frequency caveat stays documented');
var clockState = scope.bsClockSettingsState(), writeCount = writes.length;
scope.status.clockBaseline.rows[1].estimated_mhz = 430;
scope.status.clockBaseline.rows[1].ghs = 64;
assert.strictEqual(scope.bsClockMHzText('global'), '~430');
assert.strictEqual(scope.bsClockSettingsState(), clockState, 'estimate updates never dirty saved clocks');
assert.strictEqual(writes.length, writeCount, 'display helpers never write settings');
scope.status.devs[0].ClockBaselineCompatible = false;
assert.strictEqual(scope.bsClockGHsText('global', true), 'Unavailable', 'no partial global total');
assert.strictEqual(scope.bsClockGHsText(1, false), '~64.0');
scope.bsClockMode = 'individual';
scope.bsBoardClockIndex = {board0: 30, board1: 32, board2: 16};
assert.strictEqual(scope.bsClockMHzText(0), '400', 'nominal stock anchor survives missing baseline');
assert.strictEqual(scope.bsClockGHsText(0, false), 'Unavailable');
assert.strictEqual(scope.bsClockMHzText(2), 'Unavailable', 'never interpolate untested settings');
scope.bsStatusReceivedAt = 0;
assert.strictEqual(scope.bsClockMHzText(1), 'Unavailable', 'stale live compatibility cannot support an estimate');
scope.assignments = {
  '0': {primary: 'primary', failover: null},
  '1': {primary: 'primary', failover: 'backup'},
  '2': {primary: 'primary', failover: null}
};
scope.savedPools = {categories: [{pools: [{id: 'primary', algo: 'blake256r8'}]}]};
assert.strictEqual(scope.bsClockMHzText(1), '~430', 'saved algorithm supports a static lab reference without fresh telemetry');
scope.status.minerUp = false;
scope.status.devs = [];
scope.bsClockMode = 'global';
scope.bsGlobalClockIndex.index = 32;
assert.strictEqual(scope.bsClockGHsText('global', true), '~192.0', 'baseline survives an API outage');
assert.strictEqual(scope.bsClockCommandState(1), 'Command unavailable', 'reference does not invent a sent command');
scope.assignments['1'].primary = null;
assert.strictEqual(scope.bsClockGHsText('global', true), 'Unavailable', 'no global estimate for an unassigned board');
assert.strictEqual(scope.bsClockGHsText(1, false), 'Unavailable', 'failover alone does not enable a board');
scope.assignments['1'].primary = 'primary';
scope.savedPools.categories[0].pools[0].algo = 'x11';
assert.strictEqual(scope.bsClockGHsText('global', true), 'Unavailable', 'no estimate for an unsupported saved algorithm');
scope.savedPools.categories[0].pools[0].algo = 'blake256r8';
scope.bsStatusReceivedAt = Date.now();
scope.status.clockBaseline.algorithm = 'x11';
assert.strictEqual(scope.bsClockGHsText(1, false), 'Unavailable');
assert(miner.includes('aria-label="Global estimated MHz"') && miner.includes('bsClockGHsText(board, false)'));
assert(!miner.includes('ng-change="bsLinkedClockChanged(board, \'mhz\')"'), 'estimated MHz is read-only');
console.log('PASS Index controls, shared baseline estimates, compatibility, provenance, no writes and unchanged saved state');
