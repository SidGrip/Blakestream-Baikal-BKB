'use strict';
const assert = require('assert'), fs = require('fs'), vm = require('vm'), path = require('path');
const source = fs.readFileSync(path.join(__dirname, '../overlay/var/www/ng/controllers.js'), 'utf8');
const start = source.indexOf(".controller('CtrlBackup'");
const end = source.indexOf('\nfunction GetDateTime()', start);
let factory, confirmation;
const requests = [], timers = [], messages = [], syncs = [];
const scope = {bsConfirm: (message, callback) => { confirmation = callback; }, syncDelay: (...args) => syncs.push(args)};
const window = {location:{href:''}};
const moduleStub = {controller: (name, fn) => { factory = fn; return moduleStub; }};
function request(method, url, data) {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  const row = {method, url, data, resolve: body => { if (row.success) row.success(body); resolve({data:body}); }};
  promise.success = fn => { row.success = fn; return promise; };
  promise.error = fn => { row.error = fn; return promise; };
  requests.push(row); return promise;
}
const angular = {forEach: (items, fn) => Object.keys(items).forEach(k => fn(items[k], k)), identity: v => v};
vm.runInNewContext('moduleStub' + source.slice(start, end), {
  moduleStub, angular, window, GetDateTime: () => 'fixture', FormData: class { append() {} },
  document:{getElementById:()=>({click(){}})}, Alertify:{log:{create:(type,text)=>messages.push({type,text})}}
});
factory(scope, {get:(url, options)=>request('GET',url,options), post:(url, data)=>request('POST',url,data)}, fn => timers.push(fn));
const settle = async () => { for (let i=0;i<8;i++) await Promise.resolve(); };
(async () => {
  timers.shift()();
  requests.shift().resolve({ok:true,csrf:'token',data:[{dir:'fixture',items:['etc/scripta.conf']}]});
  assert.equal(scope.backupCsrf,'token');
  scope.backupExport();
  assert(scope.backupBusy);
  assert.equal(scope.backupLocal(),null,'ignore duplicate clicks');
  let req = requests.shift();
  assert.equal(req.data.params.backup,'[]','helper captures the entire allowlisted application dataset');
  assert.equal(req.data.params.csrf,'token');
  req.resolve({ok:false,error:'disk full'}); await settle();
  assert.equal(window.location.href,'','failed snapshot cannot download a stale backup');
  assert.equal(scope.backupBusy,false);
  scope.backupName='first'; scope.backupExport(); scope.backupName='second';
  requests.shift().resolve({ok:true}); await settle();
  assert.equal(window.location.href,'f_backup.php?export=first','export uses the name actually backed up');
  timers.shift()(); requests.shift().resolve({ok:true,data:[{dir:'fixture',items:['etc/scripta.conf']}]});
  scope.restore(); assert.equal(requests.length,0,'restore requires explicit confirmation');
  confirmation(); requests.shift().resolve({ok:false,error:'bad archive'}); await settle();
  assert.equal(timers.length,0,'failed restore cannot schedule a restart');
  assert.equal(syncs.length,0);
  scope.restore(); confirmation(); requests.shift().resolve({ok:true,network_staged:true}); await settle();
  assert.equal(syncs.length,3); assert.equal(timers.length,1);
  timers.shift()(); assert.equal(requests.shift().url,'f_miner.php?command=restart');
  scope.backupImport({size:17*1024*1024}); assert.equal(requests.length,0,'reject oversized archives before upload');
  scope.backupImport({size:10}); assert.equal(requests.shift().method,'POST');
  const template = fs.readFileSync(path.join(__dirname,'../overlay/var/www/partials/backup.html'),'utf8');
  assert(!template.includes('reload(500)') && !template.includes('<iframe'),'import completion is not guessed with a timer');
  console.log('PASS backup busy guard, complete dataset, failed export/restore and completed import handling');
})().catch(error => { console.error(error); process.exit(1); });
