'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const createViews = require('./test-view-session');
const root = path.resolve(__dirname, '..');
const read = p => fs.readFileSync(path.join(root, p), 'utf8');
const code = read('overlay/var/www/ng/controllers.js');
const start = code.indexOf(".controller('CtrlSettings'");
assert(start >= 0);
const body = code.slice(code.indexOf('{', start) + 1, code.indexOf('\n})', start));
const storage = {};
const browser = {localStorage: {
  getItem: key => storage[key],
  setItem: (key, value) => { storage[key] = value; }
}};
function controller(window, section) {
  const scope = Object.assign(createViews(storage), {status: {}, sync: () => { throw Error('navigation must not save'); }});
  vm.runInNewContext(body, {$scope: scope, $window: window, $location:{search:()=>({section})}});
  return scope;
}
const scope = controller(browser);
assert.equal(scope.bsSettingsTab, 'scripta');
for (const tab of ['mining', 'network', 'wifi', 'scripta']) {
  scope.bsSelectSettingsTab(tab);
  assert.equal(scope.bsSettingsTab, tab);
}
for (const tab of [undefined, null, 'invalid', 'toString']) scope.bsSelectSettingsTab(tab);
assert.equal(scope.bsSettingsTab, 'scripta');
scope.bsSelectSettingsTab('mining');
storage.bsSettingsTab = 'network';
assert.equal(controller(browser).bsSettingsTab, 'mining', 'entering Settings remembers its tab');
assert.equal(controller(browser, 'network').bsSettingsTab, 'network', 'reconnect links still open Network');
assert.equal(controller(browser, 'wifi').bsSettingsTab, 'wifi');
storage.bsSettingsTab = 'invalid';
assert.equal(controller(browser).bsSettingsTab, 'wifi', 'legacy local storage does not override session tabs');
const blocked = controller({get localStorage() { throw Error('storage blocked'); }});
blocked.bsSelectSettingsTab('network');
assert.equal(blocked.bsSettingsTab, 'network');
const html = read('overlay/var/www/partials/settings.html');
assert(html.includes('nav nav-pills bs-dev-tabs'));
assert.equal((html.match(/role="tab"/g) || []).length, 4);
assert.equal((html.match(/role="tabpanel"/g) || []).length, 4);
assert.equal((html.match(/<\/section>/g) || []).length, 4);
assert(!/ng-(?:if|switch).*bsSettingsTab/.test(html), 'forms must remain mounted');
const forms = {scripta: ['formUser', 'formPass', 'formUser2'], mining: ['formMining', 'formMods'], network: [], wifi: []};
for (const tab of Object.keys(forms)) {
  const sectionStart = html.indexOf('<section id="settings-' + tab + '"');
  assert(sectionStart >= 0);
  const panel = html.slice(sectionStart, html.indexOf('</section>', sectionStart));
  assert(html.includes('aria-controls="settings-' + tab + '"'));
  assert(panel.includes('aria-labelledby="settings-' + tab + '-tab"'));
  assert(panel.includes("ng-show=\"bsSettingsTab === '" + tab + "'\""));
  assert.equal((panel.match(/<form /g) || []).length, forms[tab].length);
  for (const form of forms[tab]) assert(panel.includes('name="' + form + '"'));
  if (tab === 'mining') assert(panel.includes('Remote control') && panel.includes('E-mail alerts'));
  if (tab === 'network') {
    assert(panel.includes('CtrlNetworkSettings') && panel.includes('partials/network-settings.html?v='));
  }
  if (tab === 'wifi') assert(panel.includes('CtrlWifiSettings') && panel.includes('partials/wifi-settings.html?v='));
}
assert(html.indexOf('settings-wifi-tab') > html.indexOf('settings-network-tab'));
const events = [];
scope.$broadcast = name => events.push(name);
scope.bsSelectSettingsTab('wifi');
scope.bsReloadSettingsTab();
assert.deepEqual(events, ['bsWifiReload']);
assert(!html.includes('f_network.php'), 'network requests remain in their own controller');
console.log('PASS Settings default tab, reconnect deep links, mounted forms, Mining, Network and Wi-Fi');
