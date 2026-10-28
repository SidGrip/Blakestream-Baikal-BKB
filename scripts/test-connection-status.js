'use strict';
const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'overlay/var/www/ng/controllers.js'), 'utf8');
const start = source.indexOf('  $scope.bsSysStats =');
const end = source.indexOf('  $scope.bsLoadSysStats();', start);
const scope = {};
let success, failure;
const response = {success: fn => { success = fn; return response; }, error: fn => { failure = fn; return response; }};
vm.runInNewContext(source.slice(start, end), {$scope:scope, $http:{get: () => response}});
assert.equal(scope.bsConnectionIcon(), 'fa-question-circle');
scope.bsLoadSysStats();
for (const [type, icon, label] of [
  ['ethernet','bs-ethernet-icon','Ethernet: eth0'], ['wifi','fa-wifi','Wi-Fi: wlan0'],
  ['other','fa-exchange','Network: tun0'], ['none','fa-unlink','No default network route']
]) {
  const intf = {ethernet:'eth0', wifi:'wlan0', other:'tun0'}[type] || '';
  success({connection:{type,interface:intf}});
  assert.equal(scope.bsConnectionIcon(), icon);
  assert.equal(scope.bsConnectionLabel(), label);
}
failure();
assert.equal(scope.bsConnectionIcon(), 'fa-question-circle');
assert.equal(scope.bsConnectionLabel(), 'Connection status unavailable');
const html = fs.readFileSync(path.join(root, 'overlay/var/www/index.php'), 'utf8');
const theme = html.indexOf('bs-theme-toggle'), indicator = html.indexOf('bs-connection-indicator'), logout = html.indexOf('ng-click="bsLogout()"');
assert(theme < indicator && indicator < logout);
assert(html.includes('ng-class="bsConnectionIcon()"'));
const indicatorTag = html.match(/<span\b[^>]*class="[^"]*bs-connection-indicator[^>]*>/);
assert(indicatorTag && indicatorTag[0].includes('role="img"'));
assert(!/\b(?:tabindex|ng-click|onclick|href)=/.test(indicatorTag[0]), 'status is not focusable or actionable');
const indicatorClasses = indicatorTag[0].match(/class="([^"]+)"/)[1].split(/\s+/);
assert(!indicatorClasses.some(name => /^(?:btn|btn-default|btn-flat|bs-navbar-action)$/.test(name)), 'no button interaction styles');
assert(indicatorTag[0].includes('aria-label="{{bsConnectionLabel()}}"'));
const css = fs.readFileSync(path.join(root, 'overlay/var/www/css/blakestream-dark.css'), 'utf8');
assert(css.includes("mask: url('../img/ethernet.png') center / contain no-repeat;"));
assert(fs.readFileSync(path.join(root,'overlay/var/www/img/ethernet.png')).subarray(1,4).equals(Buffer.from('PNG')));
assert(!/\.bs-connection-indicator:(?:hover|active|focus)/.test(css), 'status has no pressed or focus styles');
const styles = [css];
for (const file of ['login.php','partials/miner.html']) {
  const text = fs.readFileSync(path.join(root, 'overlay/var/www', file), 'utf8');
  for (const match of text.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)) styles.push(match[1]);
}
for (const style of styles) {
  for (const match of style.matchAll(/\/\*[\s\S]*?\*\//g)) {
    const lineStart = style.lastIndexOf('\n', match.index) + 1;
    const end = match.index + match[0].length;
    const lineEnd = style.indexOf('\n', end);
    assert(!match[0].includes('\n'), 'style comments are concise section headings');
    assert(!style.slice(lineStart, match.index).trim(), 'no comments following style declarations');
    assert(!style.slice(end, lineEnd < 0 ? style.length : lineEnd).trim(), 'no declarations following style comments');
  }
}
const logoutButton = html.match(/<button\b[^>]*ng-click="bsLogout\(\)"[^>]*>/);
assert(logoutButton, 'Logout button exists');
assert(!/bs-tip|data-tip|\btitle=/.test(logoutButton[0]), 'Logout label needs no tooltip');
console.log('PASS read-only connection indicator, labels, section-only style comments and tooltip-free Logout');
