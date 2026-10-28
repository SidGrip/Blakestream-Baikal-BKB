'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'overlay/var/www/ng/controllers.js'), 'utf8');
const indexSource = fs.readFileSync(path.join(root, 'overlay/var/www/index.php'), 'utf8');
const start = source.indexOf('  function bsHtmlEscape(');
const end = source.indexOf('  function bsFirmwareTerminalScrollBottom(', start);
assert(start >= 0 && end > start);
const scope = {};
vm.runInNewContext(source.slice(start, end), {$scope: scope});
for (const payload of ['<img src=x onerror=alert(1)>', '" onfocus="alert(1)', "'>&<script>"]) {
  const escaped = scope.bsHtmlEscape(payload);
  assert(!/[<>]/.test(escaped), 'attacker markup is encoded');
  assert(escaped.includes('&'), 'escaping changed the payload');
}
for (const expression of [
  '$scope.bsHtmlEscape(cat.name)',
  '$scope.bsHtmlEscape(pname)',
  '$scope.bsHtmlEscape(profile.name)',
  '$scope.bsHtmlEscape(label)',
  '$scope.bsHtmlEscape(f.path)',
  'bsHtmlEscape(title)',
  'bsHtmlEscape($scope.devFirmwareTimingText())',
  'bsHtmlEscape(text)',
  'bsHtmlEscape($scope.settingsFirmware.error)'
]) assert(source.includes(expression), 'dynamic confirmation sink escaped: ' + expression);
for (const removed of [
  '$scope.devApi.customCommand',
  '$scope.devApi.parameter',
  '$scope.devApiDisruptive'
]) assert(!source.includes(removed), 'removed unrestricted miner command UI stays absent: ' + removed);
assert(indexSource.includes('{{bsConfirmTitle}}'), 'confirmation title remains text-bound');
assert(!indexSource.includes('ng-bind-html-unsafe="bsConfirmTitle"'),
       'confirmation title never enters an unsafe HTML binding');
console.log('PASS confirmation HTML encoding for stored and runtime-supplied data');
