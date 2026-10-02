'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'overlay/var/www/ng/controllers.js'), 'utf8');
const start = source.indexOf(".controller('CtrlMain'");
const body = source.slice(source.indexOf('{', start) + 1, source.indexOf('  // Footer system stats', start));
const keys = ['userAdjustableClocks', 'userAutoFan', 'userDev'];
const copy = value => JSON.parse(JSON.stringify(value));
const scope = {}, requests = [], messages = [];
const angular = {
  copy, toJson: JSON.stringify, equals: (a, b) => JSON.stringify(a) === JSON.stringify(b),
  forEach: (value, fn) => Object.keys(value).forEach(key => fn(value[key], key))
};
function request(url) {
  const success = [], error = [];
  const req = {url, success(fn) { success.push(fn); return req; }, error(fn) { error.push(fn); return req; },
    resolve(data) { success.forEach(fn => fn(data)); }, reject() { error.forEach(fn => fn()); }};
  requests.push(req);
  return req;
}
function timeout() {}
timeout.cancel = () => {};
const window = {document:{body:{getAttribute:() => 'test'}}, sessionStorage:{getItem:() => null}};
vm.runInNewContext(body, {$scope:scope, $rootScope:{}, $http:{get:request}, $window:window,
  $timeout:timeout, angular, Alertify:{log:{create:(type, text) => messages.push({type, text})}}});
scope.tick = () => {};
const off = {userAdjustableClocks:false, userAutoFan:false, userDev:false, userDevLogTarget:'SD'};
const on = Object.assign({}, off, {userAdjustableClocks:true, userAutoFan:true, userDev:true});
function load(data) { scope.sync('settings').resolve({data:copy(data)}); }
function assertFeatures(expected, message) {
  keys.forEach(key => assert.strictEqual(scope.settings[key], expected, message + ': ' + key));
}
load(off);
keys.forEach(key => { scope.settingsFeatures[key] = true; });
assertFeatures(false, 'checking draft does not activate');
assert(scope.bsUserSettingsDirty());
assert.strictEqual(scope.settings.userDevLogTarget, 'SD', 'draft does not change logging');
scope.adjustableClocksToggleChanged();
scope.bsConfirmCancel();
assert.strictEqual(scope.settingsFeatures.userAdjustableClocks, false);
scope.autoFanToggleChanged();
scope.bsConfirmCancel();
assert.strictEqual(scope.settingsFeatures.userAutoFan, false);
assertFeatures(false, 'cancelling stays disabled');

// Plain saves must not activate until their response confirms persistence.
scope.settingsFeatures.userAutoFan = true;
scope.saveUserSettings();
const pending = requests[requests.length - 1];
const submitted = JSON.parse(pending.url.split('?settings=')[1]);
assert.strictEqual(submitted.userDev, true);
assert.strictEqual(submitted.userAutoFan, true);
assertFeatures(false, 'pending save stays disabled');
assert(scope.settingsFirmwareBusy());
const count = requests.length;
scope.saveUserSettings();
assert.strictEqual(requests.length, count, 'no duplicate pending save');
pending.resolve({data:submitted, settingsSaved:false, info:[{type:'error', text:'write failed'}]});
assertFeatures(false, 'failed write never publishes response draft');
assert(scope.bsUserSettingsDirty(), 'failed save keeps draft dirty');
assert(!scope.settingsFirmwareBusy());
scope.saveUserSettings();
requests[requests.length - 1].reject();
assertFeatures(false, 'HTTP failure does not activate');
assert(!scope.settingsFirmwareBusy());
scope.saveUserSettings();
requests[requests.length - 1].resolve(false);
assertFeatures(false, 'expired login does not activate');
assert(scope.bsUserSettingsDirty());
scope.saveUserSettings();
requests[requests.length - 1].resolve({data:submitted, settingsSaved:true});
assert.strictEqual(scope.settings.userAutoFan, true);
assert.strictEqual(scope.settings.userDev, true);
assert(!scope.bsUserSettingsDirty());

// Firmware checks and their result are distinct from saving the toggle.
load(off);
scope.settingsFeatures.userAdjustableClocks = true;
scope.saveUserSettings();
assert(requests[requests.length - 1].url.includes('firmware_status'));
assertFeatures(false, 'firmware check does not activate');
requests[requests.length - 1].resolve({ok:true, custom_present:true, encoder_ready:true, custom_active:true});
const clockSave = requests[requests.length - 1];
assert(clockSave.url.includes('?settings='));
assertFeatures(false, 'firmware readiness does not activate before save');
clockSave.resolve({data:off, settingsSaved:false});
assertFeatures(false, 'failed clock save stays disabled');
assert(scope.bsUserSettingsDirty());
scope.saveUserSettings();
requests[requests.length - 1].resolve({ok:true, custom_present:true, encoder_ready:true, custom_active:true});
requests[requests.length - 1].resolve({data:Object.assign({}, off, {userAdjustableClocks:true}), settingsSaved:true});
assert.strictEqual(scope.settings.userAdjustableClocks, true);
assert(!scope.bsUserSettingsDirty());

load(on);
keys.forEach(key => { scope.settingsFeatures[key] = false; });
assertFeatures(true, 'unchecking draft does not deactivate');
scope.saveUserSettings();
assertFeatures(true, 'pending disable retains applied state');
requests[requests.length - 1].resolve({data:off, settingsSaved:true});
assertFeatures(false, 'successful save disables');
keys.forEach(key => { scope.settingsFeatures[key] = true; });
load(off);
assert(!scope.bsUserSettingsDirty(), 'reload discards draft');
assertFeatures(false, 'reload uses saved state');

scope.settingsFeatures.userDev = true;
scope.sync('settings', scope.settings);
const otherSave = JSON.parse(requests[requests.length - 1].url.split('?settings=')[1]);
assert.strictEqual(otherSave.userDev, false, 'unrelated settings save excludes feature draft');
scope.settings.userAdjustableClocks = true;
scope.bsSaveUserSettingsBaselineField('userAdjustableClocks');
assert.strictEqual(scope.settingsFeatures.userAdjustableClocks, true, 'persisted firmware action updates its draft');
assert.strictEqual(scope.settingsFeatures.userDev, true, 'field update preserves other drafts');

const html = fs.readFileSync(path.join(root, 'overlay/var/www/partials/settings.html'), 'utf8');
keys.forEach(key => {
  assert(html.includes('ng-model="settingsFeatures.' + key + '"'));
  assert(!html.includes('ng-model="settings.' + key + '"'));
});
console.log('PASS feature drafts, save success/failure, firmware check, cancellation, reload and unrelated saves');
