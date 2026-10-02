'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const file = path.join(__dirname, '../overlay/var/www/ng/wifi-settings.js');
let controller, watch;
const events = {}, requests = [], timers = [];
let confirmFn = null;
let now = 100000;
const scope = {
  bsSettingsTab: 'network',
  $watch: (key, fn) => { watch = fn; },
  $on: (key, fn) => { events[key] = fn; },
  bsConfirm: (msg, yes) => { confirmFn = yes; scope.lastConfirm = msg; }
};
function request(method, url, data) {
  const r = {method, url, data, success(fn) { this.successFn = fn; return this; }, error(fn) { this.errorFn = fn; return this; }, resolve(body) { this.successFn(body); }, fail() { this.errorFn(); }};
  requests.push(r);
  return r;
}
const timeout = (fn, delay) => { fn.delay = delay; timers.push(fn); return fn; };
timeout.cancel = fn => { const i = timers.indexOf(fn); if (i >= 0) timers.splice(i, 1); };
vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
  Date: {now: () => now},
  angular: {module: () => ({controller: (name, fn) => { controller = fn; }}), copy: x => JSON.parse(JSON.stringify(x))}
});
controller(scope, {get: url => request('GET', url), post: (url, data) => request('POST', url, data)}, timeout);
watch('network');
assert.equal(requests.length, 0, 'no Wi-Fi traffic outside the Wi-Fi tab');
scope.bsSettingsTab = 'wifi';
watch('wifi');
assert.equal(requests[0].method, 'GET');
assert.equal(requests[0].url, 'f_wifi.php?action=status');
function status(extra) {
  return Object.assign({
    ok: true, csrf: 'a'.repeat(64),
    adapter: {present: true, interface: 'wlan0', managed: true, firmware_missing: false, state: 30},
    radio: {enabled: false, hardware: true, blocked: false, persisted: true, source: 'default'},
    connection: null, saved: [], networks: [], pending: null, last_result: null, ethernet_connected: true
  }, extra || {});
}
requests[0].resolve(status());
assert.equal(scope.wifiConnectLabel(), 'Connect');
assert.equal(scope.wifiConnecting(), false);
assert.equal(scope.wifiConnected, false);
assert.equal(scope.wifi.radio.enabled, false);
assert(scope.wifiRadioOff());
assert(scope.wifi.ethernet_connected);
assert.equal(scope.wifiCanMutate(), false, 'radio off blocks scan and connect');
scope.wifiScan();
assert.equal(requests.length, 1, 'scan is not sent while the radio is off');
scope.wifiDraft.ssid = 'LabNet';
scope.wifiDraft.psk = 'secretpass';
scope.wifiConnect();
assert.equal(requests.length, 1, 'connect is not sent while the radio is off');
scope.wifiSetRadio(true);
assert(scope.wifiAdapterStarting(), 'enabling immediately shows adapter startup');
assert.equal(requests[1].method, 'POST');
assert.equal(requests[1].url, 'f_wifi.php?action=radio');
assert.equal(requests[1].data.enabled, true);
assert.equal(requests[1].data.csrf, 'a'.repeat(64));
assert(!('psk' in requests[1].data));
requests[1].resolve(status({radio: {enabled: true, hardware: true, blocked: false, persisted: true, source: 'user'}}));
assert(scope.wifiCanMutate());
assert.equal(scope.wifiPasswordVisible, false, 'password starts masked');
const beforeToggle = requests.length;
const typedPassword = scope.wifiDraft.psk;
scope.wifiTogglePassword();
assert.equal(scope.wifiPasswordVisible, true);
assert.equal(scope.wifiDraft.psk, typedPassword, 'show does not change typed password');
scope.wifiTogglePassword();
assert.equal(scope.wifiPasswordVisible, false);
assert.equal(requests.length, beforeToggle, 'password toggle never sends a request');
scope.wifiTogglePassword();
scope.wifiSecurityChanged();
assert.equal(scope.wifiPasswordVisible, false, 'security changes mask the password');
assert(!scope.wifiAdapterStarting(), 'ready adapter ends the startup message');
assert.equal(scope.wifiScanWait, 15);
scope.wifiScan();
assert.equal(requests.length, 2, 'enable cooldown blocks immediate Scan');
scanTick(15);
const openNet = {ssid: 'OpenNet', security: 'open', supported: true, strength: 70};
const pskNet = {ssid: 'LabNet', security: 'wpa-psk', supported: true, strength: 80};
const badNet = {ssid: 'Corp', security: 'unsupported', supported: false, strength: 90};
scope.wifi.networks = [openNet, pskNet, badNet];
scope.wifiDraft.ssid = '';
scope.wifiSelectNetwork(badNet);
assert.equal(scope.wifiDraft.ssid, '', 'unsupported security cannot be selected');
scope.wifiSelectNetwork(pskNet);
assert.equal(scope.wifiDraft.ssid, 'LabNet');
assert.equal(scope.wifiDraft.security, 'wpa-psk');
assert(scope.wifiValid());
scope.wifiTogglePassword();
scope.wifiConnect();
assert.equal(scope.wifiPasswordVisible, false, 'connecting masks the password');
assert.equal(requests[2].method, 'POST');
assert(scope.wifiConnecting(), 'progress starts before the HTTP acknowledgement');
assert.equal(scope.wifiConnectLabel(), 'Connecting...');
assert.equal(requests[2].url, 'f_wifi.php?action=connect');
assert.equal(requests[2].data.csrf, 'a'.repeat(64));
assert.equal(requests[2].data.ssid, 'LabNet');
assert.equal(requests[2].data.psk, 'secretpass');
assert.equal(requests[2].data.security, 'wpa-psk');
assert(requests[2].url.indexOf('psk') < 0, 'password is not on the query string');
scope.wifiConnect();
assert.equal(requests.length, 3, 'busy blocks double connect');
requests[2].resolve(status({
  adapter: {present:false}, radio: {enabled:false}, networks:[], ethernet_connected:false,
  pending: {id: 'txn', state: 'applying', ssid: 'LabNet'},
  ok: true
}));
assert.equal(scope.wifiDraft.psk, '', 'password cleared after connect');
assert.equal(scope.wifiPendingLabel(), 'Connecting to LabNet');
assert(scope.wifiConnecting(), 'progress continues while the server applies the connection');
assert.equal(scope.wifiConnected, false);
assert(scope.wifi.radio.enabled, 'queued connection cannot turn the displayed radio Off');
assert(scope.wifi.adapter.present, 'queued connection cannot remove the adapter');
assert(scope.wifi.ethernet_connected, 'queued connection preserves Ethernet status');
assert.equal(scope.wifi.networks.length, 3, 'queued connection preserves the discovered network list');
assert.equal(scope.wifiAbsent(), false);
events.bsWifiReload();
requests[3].resolve(status({
  radio: {enabled: true, hardware: true, blocked: false, persisted: true, source: 'user'},
  pending: null,
  last_result: {action: 'activate', ok: false, error: 'Wi-Fi connection failed: association failed'},
  connection: {active: true, ssid: 'OldNet', uuid: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', security: 'wpa-psk'}
}));
assert.match(scope.wifiError, /connection failed/i);
assert.equal(scope.wifiReady, true, 'failed connect keeps the page ready');
assert.equal(scope.wifiConnectLabel(), 'Connect', 'failed activation restores the button');
assert.equal(scope.wifiConnected, false, 'rollback to an old connection cannot show success');
scope.wifiDraft.ssid = ' LabNet ';
scope.wifiDraft.security = 'wpa-psk';
scope.wifiDraft.psk = 'secretpass';
assert(scope.wifiValid(), 'SSID may have leading and trailing spaces');
scope.wifiConnect();
assert.equal(requests[4].data.ssid, ' LabNet ');
requests[4].resolve(status({
  radio: {enabled: true, hardware: true, blocked: false, persisted: true, source: 'user'},
  pending: {id: 'txn2', state: 'applying', ssid: ' LabNet '}
}));
scope.wifiBusy = false;
scope.wifi.pending = null;
scope.wifiDraft.ssid = '测'.repeat(11);
scope.wifiDraft.psk = 'secretpass';
assert.equal(scope.wifiValid(), false, 'SSID is limited by UTF-8 bytes');
scope.wifiDraft.ssid = '测'.repeat(10);
assert(scope.wifiValid(), '32-byte UTF-8 SSID is accepted');
const absent = status({adapter: {present: false, interface: null, managed: false, firmware_missing: false}});
events.bsWifiReload();
requests[5].resolve(absent);
assert(scope.wifiAbsent());
assert.equal(scope.wifiCanMutate(), false);
const blocked = status({
  adapter: {present: true, interface: 'wlan0', managed: true, firmware_missing: false},
  radio: {enabled: true, hardware: false, blocked: true, persisted: true, source: 'user'}
});
events.bsWifiReload();
requests[6].resolve(blocked);
assert(scope.wifiHardwareBlocked());
assert.equal(scope.wifiCanMutate(), false, 'hardware-blocked radio rejects scan/connect');
scope.wifi.saved = [{uuid: '12345678-1234-1234-1234-123456789abc', ssid: '<img src=x onerror=alert(1)>', security: 'wpa-psk'}];
scope.wifiReady = true;
scope.wifiBusy = false;
scope.wifi.pending = null;
scope.wifiForget(scope.wifi.saved[0]);
assert.equal(scope.lastConfirm, 'Forget this saved Wi-Fi network?', 'SSID never enters an HTML confirmation');
assert.equal(requests.length, 7, 'forget waits for confirmation');
confirmFn();
assert.equal(requests[7].url, 'f_wifi.php?action=forget');
assert.equal(requests[7].data.uuid, '12345678-1234-1234-1234-123456789abc');
requests[7].fail();
assert.match(scope.wifiError, /interrupted/i);
const radioOn = {enabled:true, hardware:true, blocked:false, persisted:true, source:'user'};
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn}));
assert(scope.wifiCanScan());
scope.wifiScan();
const scanRequest = requests[requests.length - 1], scanCount = requests.length;
assert.equal(scanRequest.url, 'f_wifi.php?action=scan');
assert.equal(scope.wifiScanWait, 15);
assert.equal(scope.wifiCanScan(), false);
scope.wifiScan();
assert.equal(requests.length, scanCount, 'double click sends only one scan');
scanRequest.resolve(status({radio:radioOn, scan_retry_after:15, networks:[pskNet], scan_deferred:false}));
assert(scope.wifiScanned);
assert(scope.wifiReady);
assert.equal(scope.wifiCanScan(), false, 'cooldown survives the completed request');
assert(scope.wifiCanMutate(), 'scan cooldown does not block connection controls');
function scanTick(seconds) {
  now += seconds * 1000;
  const index = timers.findIndex(fn => fn.delay === 1000);
  assert(index >= 0, 'countdown timer exists');
  timers.splice(index, 1)[0]();
}
scanTick(14);
assert.equal(scope.wifiScanWait, 1);
assert.equal(scope.wifiCanScan(), false);
now += 1000;
assert.equal(scope.wifiCanScan(), false, 'button stays disabled until the displayed countdown reaches zero');
scanTick(0);
assert.equal(scope.wifiScanWait, 0);
assert(scope.wifiCanScan(), 'scan re-enabled at the deadline');
scope.wifiScanned = false;
scope.wifiScan();
requests[requests.length - 1].resolve(status({radio:radioOn, scan_retry_after:15, scan_deferred:true, networks:[pskNet]}));
assert.equal(scope.wifiError, '');
assert.equal(scope.wifiScanned, false, 'deferred request is not a completed scan');
assert.equal(scope.wifi.networks[0].ssid, 'LabNet');
scanTick(15);
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, adapter: {
  present:true, managed:true, firmware_missing:false, state:20
}, scan_retry_after:15}));
assert(scope.wifiAdapterStarting());
assert(scope.wifiAdapterUnavailable());
assert.equal(scope.wifiCanScan(), false);
assert.equal(scope.wifiCanMutate(), false, 'unavailable adapter cannot connect');
scanTick(15);
assert.equal(scope.wifiAdapterStarting(), false, 'startup message has a bounded wait');
assert(scope.wifiAdapterUnavailable(), 'failed hardware stays unavailable after cooldown');
assert.equal(scope.wifiCanScan(), false, 'timer expiry cannot enable an unavailable adapter');
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, networks:[pskNet]}));
assert(scope.wifiCanScan(), 'ready status enables scan');
scope.wifiScan();
requests[requests.length - 1].resolve(status({ok:false, error:'Access denied'}));
assert.equal(scope.wifiError, 'Access denied');
assert(scope.wifiReady, 'failed mutation preserves readiness');
assert(scope.wifi.radio.enabled, 'failed mutation cannot turn the displayed radio off');
assert(scope.wifi.adapter.present, 'failed mutation cannot hide the adapter');
assert.equal(scope.wifi.networks[0].ssid, 'LabNet');
assert(scope.wifi.ethernet_connected);
scanTick(15);
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, scan_retry_after:6}));
assert.equal(scope.wifiScanWait, 6, 'status restores the server cooldown');
scope.bsSettingsTab = 'network';
watch('network');
assert.equal(timers.length, 0, 'tab change pauses all timers');
scope.bsSettingsTab = 'wifi';
watch('wifi');
requests[requests.length - 1].resolve(status({radio:radioOn, scan_retry_after:6}));
const savedNet = {uuid:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', ssid:'LabNet', security:'wpa-psk', saved:true, supported:true};
scope.wifi.saved = [savedNet];
scope.wifiDraft.psk = 'unused-password';
const beforeReconnect = requests.length;
scope.wifiSelectNetwork(pskNet);
assert.equal(requests.length, beforeReconnect + 1, 'clicking a discovered saved network reconnects immediately');
const reconnect = requests[requests.length - 1];
assert.equal(reconnect.url, 'f_wifi.php?action=connect');
assert(scope.wifiConnecting(), 'saved-profile activation shows the same progress');
assert.equal(scope.wifiDraft.ssid, savedNet.ssid, 'the form reflects the selected saved network');
assert.deepEqual(reconnect.data, {uuid:savedNet.uuid, csrf:'a'.repeat(64)}, 'reconnect sends UUID and CSRF, never credentials');
assert.equal(scope.wifiDraft.psk, '');
scope.wifiSelectNetwork(pskNet);
scope.wifiReconnect(savedNet);
assert.equal(requests.length, beforeReconnect + 1, 'busy blocks clicks from both network lists');
reconnect.resolve(status({adapter:{present:false}, radio:{enabled:false}, pending:{state:'pending', ssid:'LabNet'}}));
assert(scope.wifi.radio.enabled && !scope.wifiAbsent(), 'saved reconnect preserves adapter and radio while pending');
scope.wifiSelectNetwork(pskNet);
assert.equal(requests.length, beforeReconnect + 1, 'pending blocks repeat activation after acknowledgement');
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, saved:[savedNet], connection:{uuid:savedNet.uuid, ssid:savedNet.ssid}}));
assert.equal(scope.wifiConnectLabel(), 'Connected');
assert(scope.wifiConnected && !scope.wifiConnecting());
const confirmationTimer = timers.findIndex(fn => fn.delay === 5000);
assert(confirmationTimer >= 0, 'connected confirmation has a bounded duration');
timers.splice(confirmationTimer, 1)[0]();
assert.equal(scope.wifiConnectLabel(), 'Connect');
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, saved:[savedNet], connection:{uuid:savedNet.uuid, ssid:savedNet.ssid}}));
assert.equal(scope.wifiConnected, false, 'ordinary polling does not replay the confirmation');
const connectedCount = requests.length;
scope.wifiSelectNetwork(pskNet);
scope.wifiReconnect(savedNet);
assert.equal(requests.length, connectedCount, 'clicking the current connection is a no-op');
scope.wifi.connection = null;
scope.wifiReconnect(savedNet);
assert.equal(requests.length, connectedCount + 1, 'saved-list name also reconnects without a password');
requests[requests.length - 1].resolve(status({radio:radioOn, saved:[savedNet]}));
const unsavedCount = requests.length;
scope.wifiSelectNetwork(openNet);
assert.equal(requests.length, unsavedCount, 'an unsaved network still opens the credential form');
assert.equal(scope.wifiDraft.ssid, 'OpenNet');
scope.wifiSelectNetwork({ssid:'LabNet', security:'open', supported:true});
assert.equal(requests.length, unsavedCount, 'SSID alone cannot match a profile with different security');
scope.wifi.pending = {state:'pending'};
scope.wifiSelectNetwork(openNet);
assert.equal(requests.length, unsavedCount);
scope.wifi.pending = {state:'rollback-failed'};
assert.equal(scope.wifiConnectLabel(), 'Restoring...');
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn}));
scope.wifiSelectNetwork(openNet);
scope.wifiConnect();
assert(scope.wifiConnecting());
requests[requests.length - 1].fail();
assert.equal(scope.wifiConnectLabel(), 'Connect', 'HTTP failure stops the request spinner');
assert.equal(scope.wifiConnected, false);
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, pending:{state:'applying', ssid:'OpenNet', security:'open'}}));
assert(scope.wifiConnecting(), 'status recovers a pending connection after an interrupted request');
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, connection:{ssid:'WrongNet', security:'open'}}));
assert.equal(scope.wifiConnected, false, 'a different active network is not the requested connection');
scope.wifiSelectNetwork(openNet);
scope.wifiConnect();
requests[requests.length - 1].resolve(status({radio:radioOn, connection:{ssid:'OpenNet', security:'open'}, last_result:{action:'connect',ok:true}}));
assert(scope.wifiConnected, 'a confirmed immediate connection also shows success');
scope.wifiClearConnectFeedback();
assert.equal(scope.wifiConnected, false, 'editing a draft clears success feedback');
scope.wifiConnect();
requests[requests.length - 1].resolve(status({ok:false, error:'Activation refused'}));
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, connection:{ssid:'OpenNet', security:'open'}, last_result:{action:'connect',ok:true}}));
assert.equal(scope.wifiConnected, false, 'a failed request cannot reuse a prior successful connection');
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, pending:{state:'applying', ssid:'OtherNet', security:'open'}}));
events.bsWifiReload();
requests[requests.length - 1].resolve(status({radio:radioOn, connection:{ssid:'OtherNet', security:'open'}}));
assert(scope.wifiConnected, 'pending work restored from status can complete successfully');
events.$destroy();
assert.equal(timers.length, 0, 'timers cleaned up on destroy');
const template = fs.readFileSync(path.join(__dirname, '../overlay/var/www/partials/wifi-settings.html'), 'utf8');
assert(template.indexOf('wifiSetRadio(false)') >= 0 && template.indexOf('wifiSetRadio(true)') >= 0, 'On/Off radio control');
assert(template.indexOf('bs-btn-outline') < 0, 'does not use a nonexistent outline class');
assert(template.indexOf('bs-save-pool-btn') >= 0, 'Connect uses the shared save button');
assert(template.includes('bs-wifi-connect') && template.includes('{{wifiConnectLabel()}}'), 'connection feedback is inside the button');
assert(template.includes('aria-busy="{{wifiConnecting()}}"') && template.includes('aria-live="polite"'), 'progress is accessible');
assert(template.includes('ng-disabled="!wifiValid() || wifiConnected"'), 'pending and confirmation states prevent duplicate clicks');
assert(template.indexOf('option value="wpa3"') < 0 && template.indexOf('option value="wep"') < 0, 'no WPA3/WEP options');
assert(template.indexOf('option value="open"') >= 0 && template.indexOf('wpa-psk') >= 0);
assert(template.indexOf('Ethernet connected') >= 0, 'Ethernet connected notice');
assert(template.indexOf('ng-trim="false"') >= 0, 'Angular preserves SSID whitespace');
assert(template.indexOf('No Wi-Fi adapter') >= 0);
assert(/<p class="text-danger" role="status" ng-show="wifiAbsent\(\)">No Wi-Fi adapter<\/p>/.test(template), 'missing adapter uses a compact red status line');
assert(!/class="alert\b/.test(template), 'Wi-Fi status messages never use full-width banners');
assert(template.includes('<p class="text-danger" role="alert" ng-show="wifiError">'), 'service errors remain accessible compact text');
assert(template.indexOf('Radio off') < 0, 'Off selection replaces the redundant radio-off banner');
assert(/<form\b[^>]*name="wifiForm"[^>]*ng-show="wifi\.adapter\.present && wifi\.radio\.enabled && !wifiAdapterStarting\(\) && !wifiAdapterUnavailable\(\)"/.test(template), 'network options wait for radio and adapter readiness');
assert(template.includes('Adapter starting...') && template.includes('Adapter unavailable'), 'compact startup and failure statuses');
assert(template.indexOf('Hardware blocked') >= 0);
assert(template.indexOf('maxlength') < 0, 'SSID is not limited by JS character maxlength');
assert(template.indexOf('type="password"') >= 0 && template.indexOf('id="wifiPsk"') >= 0);
assert(/id="wifiPsk"[^>]*type="password"[^>]*autocomplete="off"/.test(template), 'Wi-Fi requests no autofill while remaining masked');
assert(template.includes('ng-attr-type="{{wifiPasswordVisible ? \'text\' : \'password\'}}"'));
assert(template.includes('wifiTogglePassword()') && template.includes('fa-eye-slash'));
assert(template.includes('aria-controls="wifiPsk"') && template.includes('aria-pressed="{{wifiPasswordVisible}}"'));
assert(template.includes("title=\"{{wifiPasswordVisible ? 'Hide password' : 'Show password'}}\""));
assert(template.indexOf('Hidden network') >= 0);
assert(template.indexOf('ng-disabled="!wifiCanScan()"') >= 0, 'Scan observes the cooldown');
assert(template.indexOf('Retry in {{wifiScanWait}}s') >= 0, 'compact scan countdown');
assert(template.indexOf('btn-group bs-setting-choices') >= 0, 'radio buttons use shared choice styling');
const settingsTemplate = fs.readFileSync(path.join(__dirname, '../overlay/var/www/partials/settings.html'), 'utf8');
assert(settingsTemplate.indexOf('bs-interval-buttons bs-setting-choices') >= 0, 'Graph interval uses the same choice styling');
console.log('PASS Wi-Fi polling, off visibility, scan cooldown, CSRF, SSID bytes, confirmation and cleanup');
