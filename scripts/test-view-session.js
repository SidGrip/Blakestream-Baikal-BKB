'use strict';
const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const source = fs.readFileSync(path.join(__dirname, '../overlay/var/www/ng/controllers.js'), 'utf8');
const start = source.indexOf('  var bsViewDefaults =');
const body = source.slice(start, source.indexOf('  // Settings', start));
function createViews(storage = {}, login = 'login-one', blocked = false) {
  const events = {}, scope = {
    $on(name, callback) { events[name] = callback; },
    $broadcast(name) { if (events[name]) events[name](); }
  };
  const window = {document:{body:{getAttribute:()=>login}}, sessionStorage:{
    getItem(key) { if (blocked) throw Error('blocked'); return storage[key]; },
    setItem(key, value) { if (blocked) throw Error('blocked'); storage[key] = value; }
  }};
  vm.runInNewContext(body, {$scope:scope, $window:window});
  return scope;
}
module.exports = createViews;
if (require.main === module) {
  const storage = {}, scope = createViews(storage);
  assert.equal(scope.bsViewTab('miner'), 'pools');
  assert.equal(scope.bsViewTab('settings'), 'scripta');
  assert.equal(scope.bsViewTab('dev'), 'boards');
  scope.bsObserveMinerSession('boot:100:200');
  scope.bsRememberViewTab('miner', 'clocks');
  scope.bsRememberViewTab('settings', 'wifi');
  scope.bsRememberViewTab('dev', 'logs');
  scope.bsRememberViewTab('miner', 'invalid');
  let refreshed = createViews(storage);
  assert.equal(refreshed.bsViewTab('miner'), 'clocks');
  assert.equal(refreshed.bsViewTab('settings'), 'wifi');
  assert.equal(refreshed.bsViewTab('dev'), 'logs');
  refreshed.bsObserveMinerSession(null);
  refreshed.bsObserveMinerSession('boot:100:200');
  assert.equal(refreshed.bsViewTab('miner'), 'clocks', 'network loss/recovery is not a restart');
  let reset = 0;
  refreshed.$on('bsViewTabsReset', () => reset++);
  refreshed.bsObserveMinerSession('boot:101:300');
  assert.equal(reset, 1);
  assert.equal(refreshed.bsViewTab('miner'), 'pools');
  assert.equal(refreshed.bsViewTab('settings'), 'scripta');
  refreshed.bsRememberViewTab('miner', 'sgminer');
  assert.equal(createViews(storage, 'new-login').bsViewTab('miner'), 'pools');
  const blocked = createViews({}, 'login-one', true);
  blocked.bsRememberViewTab('miner', 'clocks');
  assert.equal(blocked.bsViewTab('miner'), 'clocks');
  assert.equal(createViews({bsViewState:'bad json'}).bsViewTab('miner'), 'pools');
  const php = fs.readFileSync(path.join(__dirname, '../overlay/var/www/f_status.php'), 'utf8');
  assert(php.indexOf("$stats=cgminer('stats')") < php.indexOf("$r['status']['minerSession']"));
  const requests = [], seen = [], fan = [];
  const polling = {settings:{liveMax:50}, status:{}, live:[], interval:1, intervalMax:20,
    bsHeaderHashrate:()=>100, bsObserveMinerSession:id=>seen.push(id), bsReceiveFanStatus:s=>fan.push(s), bsLoadSysStats(){}};
  const begin = source.indexOf('  var bsStatusRequestSerial =');
  const http = {get() {
    const response = {success(fn){this.ok=fn;return this;}, error(fn){this.fail=fn;return this;}, then(){return this;}};
    requests.push(response);
    return response;
  }};
  vm.runInNewContext(source.slice(begin, source.indexOf('  $scope.intervalSet =',begin)), {
    $scope:polling, $http:http, Date, $filter:()=>v=>v,
    angular:{forEach:(object,fn)=>Object.keys(object).forEach(k=>fn(object[k],k))}
  });
  polling.tick(); polling.tick();
  const data = id => ({status:{minerSession:id, fanControl:{request_revision:id}, dtot:{devices:3,MHS5s:100}}});
  requests[1].ok(data('new'));
  requests[0].ok(data('old'));
  requests[0].fail();
  assert.deepEqual(seen,['new']);
  assert.deepEqual(fan,[{request_revision:'new'}]);
  assert.equal(polling.title,'[100h] [3 dev]', 'late failures cannot overwrite a newer healthy response');
  console.log('PASS login-scoped tabs, refresh, miner restart and network recovery');
}
