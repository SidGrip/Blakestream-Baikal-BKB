'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const createViews = require('./test-view-session');
const root = path.resolve(__dirname, '..');
const read = p => fs.readFileSync(path.join(root, p), 'utf8');
const code = read('overlay/var/www/ng/controllers.js');
const start = code.indexOf(".controller('CtrlMiner'");
assert(start >= 0);
const body = code.slice(code.indexOf('{', start) + 1, code.indexOf('  $scope.minerCompat', start));
const storage = {};
const window = {localStorage: {
  getItem: key => storage[key],
  setItem: (key, value) => { storage[key] = value; }
}};
function controller(browser) {
  const scope = Object.assign(createViews(storage), {status: {}});
  vm.runInNewContext(body, {$scope: scope, $window: browser});
  return scope;
}
const scope = controller(window);
assert.equal(scope.bsClockControlsEnabled(), false);
scope.settings = {userAdjustableClocks: true};
assert.equal(scope.bsClockControlsEnabled(), true, 'server-filtered settings allow clocks before firmware details load');
scope.devFirmware = {status: {custom_active: false, last_identity: {identity: 'STOCK'}}};
assert.equal(scope.bsClockControlsEnabled(), false, 'stock hides clocks even with a stale enabled setting');
scope.devFirmware.status.custom_active = true;
assert.equal(scope.bsClockControlsEnabled(), true);
scope.settings.userAdjustableClocks = false;
assert.equal(scope.bsClockControlsEnabled(), false, 'V1 with clocks disabled is Fan only');
assert.equal(scope.bsMinerTab, 'pools');
for (const tab of ['clocks', 'sgminer', 'pools']) {
  scope.bsSelectMinerTab(tab);
  assert.equal(scope.bsMinerTab, tab);
}
scope.bsSelectMinerTab('invalid');
assert.equal(scope.bsMinerTab, 'pools');
scope.bsSelectMinerTab('sgminer');
storage.bsMinerTab = 'clocks';
assert.equal(controller(window).bsMinerTab, 'sgminer', 'entering Miner remembers its tab');
storage.bsMinerTab = 'invalid';
assert.equal(controller(window).bsMinerTab, 'sgminer', 'legacy local storage does not override session tabs');
const unavailable = controller({get localStorage() { throw Error('storage blocked'); }});
unavailable.bsSelectMinerTab('clocks');
assert.equal(unavailable.bsMinerTab, 'clocks');
const html = read('overlay/var/www/partials/miner.html');
assert(html.includes('placeholder="e.g. Blakecoin Pool"'), 'pool name example uses Pool');
assert(!html.includes('Blakecoin Devnet'), 'pool name example must not imply a devnet');
assert(!/ng-(?:if|switch).*bsMinerTab/.test(html), 'forms must stay mounted');
assert.equal((html.match(/role="tab"/g) || []).length, 3);
assert.equal((html.match(/role="tabpanel"/g) || []).length, 3);
for (const tab of ['pools', 'clocks', 'sgminer']) {
  assert(html.includes('aria-controls="miner-' + tab + '"'));
  assert(html.includes('aria-labelledby="miner-' + tab + '-tab"'));
  assert(html.includes("ng-show=\"bsMinerTab === '" + tab + "'\""));
}
assert(html.includes('Clocks &amp; Fan'));
assert(html.includes("bsClockControlsEnabled() ? 'Clocks &amp; Fan' : 'Fan'"));
assert.equal((html.match(/ng-show="bsClockControlsEnabled\(\)"/g) || []).length, 2);
assert(!html.includes('bsSgminerOptionsOpen'));
const sgminer = html.indexOf('<section id="miner-sgminer"');
assert(html.indexOf('cgminerHardCtl') > sgminer);
assert(html.indexOf('name="optionForm"') > sgminer);
const clocks = html.indexOf('<section id="miner-clocks"');
assert(html.indexOf('name="clockForm"') > clocks);
assert(html.indexOf('name="clockForm"') < sgminer);
console.log('PASS Miner default tab, unavailable storage, mounted forms and command ownership');
const fanStart = code.indexOf('  $scope.bsClampManualFan = function');
const fanEnd = code.indexOf('  // Manual fan:', fanStart);
const limits = code.match(/\$scope\.bsManualFanLimits = (\{[^;]+\});/);
assert(limits);
vm.runInNewContext('$scope.bsManualFanLimits = ' + limits[1] + ';' + code.slice(fanStart, fanEnd), {$scope:scope});
for (const [value, expected] of [[0,40],[-1,40],[39,40],[40,40],[70,70],[100,100],[101,100],['bad',70]]) {
  assert.equal(scope.bsClampManualFan(value), expected);
}
const manual = html.slice(html.indexOf('<form name="manualFanForm"'), html.indexOf('<form name="clockProfileForm"'));
assert(manual.includes('min="40" max="100"'), 'manual slider starts at 40%');
const pattern = new RegExp(manual.match(/ng-pattern="\/(.*?)\/"/)[1]);
for (const value of ['0','39','101','40.5','']) assert(!pattern.test(value), 'invalid manual percentage: '+value);
for (const value of ['40','70','100']) assert(pattern.test(value));
console.log('PASS manual fan slider, typed values and clamp enforce 40-100%');
const autoLimits = code.match(/\$scope\.bsFanCurveLimits = (\{[^;]+\});/);
const autoStart = code.indexOf('  $scope.bsClampFanPercent = function');
const autoEnd = code.indexOf('  $scope.bsNormalizeFanCurve = function', autoStart);
scope.bsParseInt = (value, fallback) => isNaN(parseInt(value, 10)) ? fallback : parseInt(value, 10);
vm.runInNewContext('$scope.bsFanCurveLimits = ' + autoLimits[1] + ';' + code.slice(autoStart, autoEnd), {$scope:scope});
assert.equal(scope.bsNormalizeFanCurveValues({}).min, 40, 'automatic default stays 40');
for (const [min, expected] of [[0,20],[19,20],[20,20],[30,30],[40,40],[101,100]]) {
  assert.equal(scope.bsNormalizeFanCurveValues({min}).min, expected);
}
assert(code.includes('settings.userAutoFanMin = Math.max(20, $scope.bsFanYToPct(pt.y))'));
console.log('PASS automatic fan allows 20% without changing default or manual minimum');
assert.deepEqual(JSON.parse(JSON.stringify(scope.bsNormalizeFanCurveValues({}))),
  {min:40, target:36, midTemp:43, midFan:75, full:50});
for (const target of [0, 19, 20, 120, 999]) {
  const curve = scope.bsNormalizeFanCurveValues({target, midTemp:0});
  assert(curve.target >= 20 && curve.midTemp > curve.target && curve.midTemp < curve.full);
}
const normalizeStart = code.indexOf('  $scope.bsNormalizeFanCurve = function');
const normalizeEnd = code.indexOf("  $scope.$on('bsOptionsLoaded'", normalizeStart);
vm.runInNewContext(code.slice(normalizeStart, normalizeEnd), {$scope:scope});
scope.settings = {userAutoFanMin:'40', userAutoFanTarget:'36', userAutoFanMidTemp:'43',
  userAutoFanMidFan:'75', userAutoFanFull:'50'};
const autoHtml = html.slice(html.indexOf('<form name="fanCurveForm"'), html.indexOf('<form name="manualFanForm"'));
assert(!autoHtml.includes('ng-change="bsStageFanCurve()"'), 'typing must not normalize partial values');
assert.equal((autoHtml.match(/bs-commit-on-blur="bsStageFanCurve\(\)"/g) || []).length, 5);
scope.bsStageFanCurve();
assert.equal(scope.settings.userAutoFanMin, 40);
assert.equal(scope.settings.userAutoFanMidFan, 75);
scope.settings.userAutoFanTarget = '19';
scope.settings.userAutoFanMidTemp = '0';
scope.bsStageFanCurve();
assert.equal(scope.settings.userAutoFanTarget, 20);
assert.equal(scope.settings.userAutoFanMidTemp, 21);
let saves = 0;
scope.sync = () => { saves++; };
scope.fanCurveForm = {$invalid:true, $setPristine() {}};
scope.bsSaveFanCurve();
assert.equal(saves, 0, 'invalid drafts cannot be saved programmatically');
scope.fanCurveForm.$invalid = false;
scope.bsSaveFanCurve();
assert.equal(saves, 1);
for (const model of ['bsClock.recover', 'bsClock.cutoff']) {
  const input = html.match(new RegExp('<input[^>]+ng-model="' + model.replace('.', '\\.') + '"[^>]*>'))[0];
  assert(input.includes('maxlength="2"'));
  const valid = new RegExp(input.match(/ng-pattern="\/(.*?)\/"/)[1]);
  assert(valid.test('40') && valid.test('55') && !valid.test('100'));
}
console.log('PASS fan defaults, blur-only normalization, temperature floors and two-digit protection fields');
