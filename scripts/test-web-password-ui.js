'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.join(__dirname, '../overlay/var/www/ng/controllers.js'), 'utf8');
const start = source.indexOf('$scope.sync = function(');
const end = source.indexOf('$scope.syncDelay = ', start);
assert(start >= 0 && end > start);
const calls = [];
const pending = {success: () => pending};
const $scope = {};
vm.runInNewContext(source.slice(start, end), {
  $scope,
  angular: {toJson: JSON.stringify},
  $http: {
    get: url => { calls.push({method: 'GET', url}); return pending; },
    post: (url, body, options) => { calls.push({method: 'POST', url, body, options}); return pending; }
  }
});
for (const password of ['Synthetic-password', 'symbols +&?=#%"\\', '0', '']) {
  assert.strictEqual($scope.sync('pass', password), pending);
  const call = calls.pop();
  assert.strictEqual(call.method, 'POST');
  assert.strictEqual(call.url, 'f_settings.php');
  assert.strictEqual(call.options.headers['Content-Type'], 'application/x-www-form-urlencoded');
  assert.strictEqual(new URLSearchParams(call.body).get('pass'), JSON.stringify(password));
}
$scope.sync('settings', 'load');
assert.deepStrictEqual(calls.pop(), {method: 'GET', url: 'f_settings.php?settings=%22load%22'});
console.log('PASS password POST body encoding, no password in URL, unchanged settings reads');
