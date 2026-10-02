'use strict';
var assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
var root = path.join(__dirname, '..');
function read(file) { return fs.readFileSync(path.join(root, file), 'utf8'); }
var source = read('overlay/var/www/ng/controllers.js');
var html = read('overlay/var/www/partials/dev-tools.html');
var logs = html.slice(html.indexOf('<div ng-switch-when="logs">'), html.indexOf('<!-- ============================== NETWORK'));
assert.equal((logs.match(/<div\b/g) || []).length, (logs.match(/<\/div>/g) || []).length, 'Logs divs balance');
for (var removed of ['recorders', 'diagnostics']) {
  assert(!html.includes('ng-switch-when="' + removed + '"'));
  assert(!html.includes("devTab='" + removed + "'"));
  assert(!source.includes(removed + ': true'));
}
assert(!logs.includes('bs-recorder-health') && !logs.includes('bs-diag-snapshot'), 'no duplicate panels');
assert.equal((logs.match(/<pre\b/g) || []).length, 1, 'one shared terminal');
assert(logs.indexOf('ng-click="devSnapshot()"') > logs.indexOf('bs-storage-df-row'));
assert(logs.indexOf('ng-click="devSnapshot()"') < logs.indexOf('bs-storage-tables'));
assert(!/>Health<\/th>/.test(logs) && !logs.includes('bs-storage-health'), 'no Health column or per-file status');
assert(!logs.includes('devRecorderRun(') && !logs.includes('fa-play'), 'history collectors have no manual run button');
assert(logs.includes('class="bs-recorder-warning" role="status" ng-if="devRecorderWarning()"'), 'compact collector status appears only when needed');
assert(logs.includes("devRecorderWaitingOnly() ? 'text-muted' : 'text-warning'"), 'normal waiting is not styled as a warning');
assert(logs.includes("devRecorderWaitingOnly() ? 'fa-clock-o' : 'fa-exclamation-triangle'"), 'waiting has a clock instead of a warning icon');
var toolbar = logs.slice(logs.indexOf('class="bs-storage-toolbar"'), logs.indexOf('class="bs-storage-tables'));
assert(toolbar.includes('devSnapshot()') && toolbar.includes('diagnostics_zip') && toolbar.includes('devRecorderWarning()'),
  'collector warnings share the diagnostics toolbar');
assert(toolbar.indexOf('devRecorderWarning()') > toolbar.indexOf('diagnostics_zip'), 'warning follows Download diagnostics');
assert(/\.bs-storage-toolbar\s*\{[^}]*grid-template-columns:\s*35% 32.5% 32.5%;/.test(read('overlay/var/www/css/blakestream-dark.css')));
assert(logs.includes('bs-storage-location-col'), 'RAM alignment uses shared fixed column widths');
assert(logs.includes('devStorageView(row.ram)') && logs.includes('devStorageClear(row.ram)'), 'View and Clear remain');
assert(logs.includes('devStorageView(row.sd)') && logs.includes('devStorageClear(row.sd)'), 'SD View and Clear remain');
assert(logs.includes('ng-click="devLogsRefresh()"') && logs.includes('{{devRecorderWarning()}}'));
assert(logs.includes('href="f_dev.php?action=diagnostics_zip"'));
assert(logs.includes('ng-click="devSnapshotCopy()"'));
assert(logs.includes('ng-if="devLog.mode === \'log\'"'));
assert(logs.includes('class="bs-measured-band"'));
assert(/\.bs-dev-log-lines\s*\{[^}]*word-break:\s*normal;/.test(read('overlay/var/www/css/blakestream-dark.css')),
  'readable log words and percentages stay together on mobile');
function block(start, end) {
  var a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a, start); return source.slice(a, b);
}
function harness() {
  var calls = [], watchers = [], timers = [];
  var scope = {settings:{}, devStorageGen:0, devLogSources:[{id:'sgminer',label:'sgminer'}],
    devLogSourceList:[{id:'sgminer',label:'sgminer'}], $watch:function(name, fn) {watchers.push(fn);}};
  function request(method, url, data) {
    var call = {method:method,url:url,data:data}; calls.push(call);
    call.success = function(fn) {call.ok=fn;return call;};
    call.error = function(fn) {call.fail=fn;return call;}; return call;
  }
  var context = {$scope:scope, devToolsDestroyed:false, devLogDestroyed:false,
    angular:{isArray:Array.isArray, forEach:function(a,fn){a.forEach(fn);}},
    $timeout:function(fn){timers.push(fn);},
    $http:{get:function(url,data){return request('GET',url,data);},post:function(url,data){return request('POST',url,data);}},
    bsDevTabs:{boards:true,logs:true,network:true,api:true,firmware:true},bsDevTabSave:function(){},
    devActivityStart:function(){},devActivityStop:function(){}};
  vm.createContext(context);
  vm.runInContext(block('  $scope.devStorage = {', '  // Storage keeps its own cadence.'),context);
  vm.runInContext(block('  $scope.devLog = {', '  $scope.devLogIsSecurity ='),context);
  vm.runInContext(block('  var devLogPoll = function', '  var devLogTimer ='),context);
  vm.runInContext(block('  $scope.devSnap = ', '  $scope.devProbeRunning = '),context);
  vm.runInContext(block('  // ---------- Recorders ----------', '\n})'),context);
  vm.runInContext(block("  $scope.$watch('devTab', function (tab)", '  // ---------- Board health actions'),context);
  return {s:scope,c:context,calls:calls,timers:timers,tab:function(t){scope.devTab=t;watchers.forEach(function(fn){fn(t);});},
    take:function(action){var c=calls.find(function(c){return !c.used && c.url.includes('action='+action);});assert(c,action);c.used=true;return c;}};
}
function file(side, name, recorder) {return {path:(side==='ram'?'/run/blakestream/':'/opt/scripta/var/')+name,name:name,recorder_id:recorder,present:true,size:50,clearable:true};}
function storage(h) {
  h.take('storage').ok({ok:true,ram:[file('ram','hashrate-history.json','hashrate'),file('ram','sgminer.log')],sd:[file('sd','hashrate-history.json','hashrate')],generated_at:10});
}
function health(h) {
  h.take('recorder_health').ok({ok:true,cron_up:true,all_ok:true,recorders:[{id:'hashrate',name:'Hashrate History',health:'ok'}]});
}
var h=harness(), s=h.s;
assert.equal(s.devRecorderWarning(),'','no initial warning');
h.tab('boards'); h.tab('network'); assert.equal(h.calls.length,0);
h.tab('logs'); assert.equal(h.calls.length,2); assert(h.calls.every(c=>c.method==='GET'));
assert.equal(s.devRecorderWarning(),'','initial health request is quiet');
s.devRecordersRefresh(); s.devRecorderRun({id:'hashrate'}); assert.equal(h.calls.length,2);
health(h); storage(h);
assert.equal(s.devStorage.unified.length,2,'RAM/SD history shares one row');
var row=s.devStorage.unified[0]; assert.equal(row.recorder_id,'hashrate');
assert.equal(s.devRecorderWarning(),'','healthy collectors are quiet');
var original=s.devRecorders;
s.devRecorders=[{id:'pool',name:'Pool History',health:'waiting'}];
assert.equal(s.devRecorderWarning(),'Pool History: waiting for update');
assert(s.devRecorderWaitingOnly(),'startup waiting is informational');
s.devRecorders.push({id:'clock',name:'Clock History',health:'error'});
assert(!s.devRecorderWaitingOnly(),'an actual error overrides waiting styling');
assert(s.devRecorderWarning().includes('Clock History: failed'));
s.devRecordersError='unavailable';
assert(!s.devRecorderWaitingOnly(),'unavailable status is not styled as waiting');
s.devRecordersError=''; s.devRecordersCron=false;
assert(!s.devRecorderWaitingOnly(),'stopped scheduler is not styled as waiting');
s.devRecordersCron=true;
s.devRecorders=[{id:'pool',name:'Pool History',health:'ok'}];
assert.equal(s.devRecorderWarning(),'','first sample clears the waiting notice');
assert(!s.devRecorderWaitingOnly());
s.devRecorders=[{id:'clock',name:'Clock History',health:'idle'},{id:'other',name:'Unknown',health:'unknown'}];
assert.equal(s.devRecorderWarning(),'','idle or unmonitored collectors are not called failed');
s.devRecorders=[{id:'pool',name:'Pool History',health:'error'}, {id:'hashrate',name:'Hashrate History',health:'stale'}];
assert.equal(s.devRecorderWarning(),'Pool History: failed; Hashrate History: not updating','each failing collector named once');
s.devRecordersLoading=true;
assert(s.devRecorderWarning().includes('Pool History: failed'),'warning stays during refresh');
s.devRecordersLoading=false; s.devRecorders=original;
h.tab('network'); h.tab('logs'); storage(h);
h.take('recorder_health').fail(); assert.equal(s.devRecorderWarning(),'Collector status unavailable.');
s.devRecordersRefresh();
assert.equal(s.devRecorderWarning(),'Collector status unavailable.','request failure stays visible until recovery');
h.take('recorder_health').ok({ok:true,cron_up:true,all_ok:true,recorders:[{id:'hashrate',name:'Hashrate History',health:'ok'}]});
assert.equal(s.devRecorderWarning(),'','successful retry clears warning');
s.devRecordersRefresh(); h.take('recorder_health').ok({ok:false,error:'unavailable'});
assert.equal(s.devRecordersError,'unavailable');
s.devRecordersRefresh(); health(h);
s.devRecordersCron=false; assert.equal(s.devRecorderWarning(),'History collection stopped: scheduler is not running.'); s.devRecordersCron=true;
s.devRecordersById.hashrate.health='stale'; assert.equal(s.devRecorderWarning(),'Hashrate History: not updating');
var before=h.calls.length; s.devLogsRefresh(); assert.equal(h.calls.length,before+2); storage(h); health(h);
assert.equal(s.devRecorderWarning(),'','stalled collector warning disappears after recovery');

// Pending responses must not replace the user's newer viewer selection.
h.c.devLogPoll(); var oldTail=h.take('log_tail');
s.devMeasuredVisible=true; s.devSnapshot(); var snap=h.take('state_snapshot');
assert.equal(s.devLog.mode,'snapshot'); assert(!s.devMeasuredVisible); assert.equal(s.devLog.text,'Collecting...');
before=h.calls.length; s.devSnapshot(); h.c.devLogPoll(); assert.equal(h.calls.length,before);
oldTail.ok({ok:true,lines:['stale tail'],next_offset:50}); assert.equal(s.devLog.text,'Collecting...');
snap.ok({ok:true,text:'Snapshot fixture'}); assert.equal(s.devLog.text,'Snapshot fixture');
h.c.devLogPoll(); assert.equal(h.calls.length,before);
s.devLog.paused=true; s.devStorageView(file('ram','hashrate-history.json','hashrate'));
assert.equal(s.devLog.mode,'log'); assert(!s.devLog.paused); h.c.devLogPoll();
var fileRead=h.take('file_view');
s.devSnapshot(); var late=h.take('state_snapshot');
fileRead.ok({ok:true,text:'stale file'}); assert.equal(s.devLog.text,'Collecting...');
s.devStorageView({present:true,view:'sgminer'}); h.c.devLogPoll();
h.take('log_tail').ok({ok:true,lines:['new tail'],path:'sgminer',next_offset:10});
late.ok({ok:true,text:'late snapshot'}); assert.equal(s.devLog.text,'new tail\n');
s.devSnapshot(); h.take('state_snapshot').fail(); assert(s.devLog.text.includes('failed'));
s.devSnapshot(); h.take('state_snapshot').ok({ok:false,error:'unavailable'}); assert(s.devLog.text.includes('unavailable'));
s.devStorageView({present:true,view:'measured-rates'}); assert(s.devMeasuredVisible);
s.devSnapshot(); h.take('state_snapshot').ok({ok:true,text:'from measured'}); assert(!s.devMeasuredVisible);

s.devRecorderRun(s.devRecordersById.hashrate); var run=h.take('recorder_run');
assert(s.devRecorderRunning); assert.equal(s.devLog.mode,'recorder'); assert(s.devLog.text.includes('Running'));
before=h.calls.length; s.devRecorderRun({id:'pool'}); s.devRecordersRefresh(); h.c.devLogPoll(); assert.equal(h.calls.length,before);
run.ok({ok:true,output:'Recorded',exit:0}); assert(!s.devRecorderRunning); assert(s.devLog.text.includes('[exit 0]')); storage(h); health(h);
s.devRecorderRun(s.devRecordersById.hashrate); run=h.take('recorder_run');
s.devSnapshot(); h.take('state_snapshot').ok({ok:true,text:'current snapshot'});
run.fail(); assert.equal(s.devLog.text,'current snapshot','late recorder output cannot replace snapshot');
s.devRecorderRun(s.devRecordersById.hashrate); h.take('recorder_run').ok({ok:false,error:'busy'});
assert(s.devLog.text.includes('busy') && !s.devRecorderRunning);

var dead=harness(); dead.tab('logs'); dead.s.devSnapshot(); dead.c.devToolsDestroyed=true;
dead.take('recorder_health').ok({ok:true,recorders:[{id:'stale'}]});
dead.take('state_snapshot').ok({ok:true,text:'stale'});
before=dead.calls.length; dead.s.devLogsRefresh(); dead.s.devSnapshot(); dead.s.devRecorderRun({id:'clock'});
assert.equal(dead.calls.length,before); assert.equal(dead.s.devRecorders.length,0); assert.equal(dead.s.devLog.text,'Collecting...');
var targetHarness=harness(), ts=targetHarness.s;
ts.devMeasuredCsrf='test-csrf';
var diagnostic={name:'network-events.log',target_key:'network-events.log',target:'SD',ram:{view:'network_events'}};
var sgminerTarget={name:'sgminer.log',target_key:'sgminer.log',target:'SD',ram:{view:'sgminer'}};
var baselineFields=[];
ts.bsSaveUserSettingsBaselineField=function(key){baselineFields.push(key);};
ts.settings.userDevLogTarget='RAM';
ts.devStorageSetTarget(sgminerTarget);
targetHarness.take('storage_set_target').ok({ok:true,key:'userDevLogTarget',target:'SD'});
assert.equal(ts.settings.userDevLogTarget,'SD','sgminer choice updates the settings draft');
assert(baselineFields.includes('userDevLogTarget'),'sgminer choice updates the saved settings baseline');
targetHarness.calls.length=0;
ts.devStorageView({present:true,name:'network-events.log',path:'/run/blakestream/network-events.log',clearable:true});
ts.devStorageSetTarget(diagnostic); ts.devStorageSetTarget(diagnostic);
var targetCall=targetHarness.take('storage_set_target');
assert.equal(targetHarness.calls.length,1,'one destination request at a time');
assert.equal(targetCall.data.csrf,'test-csrf');
assert(diagnostic.saving);
targetCall.ok({ok:true,key:'userNetworkLogTarget',target:'SD'});
assert(!diagnostic.saving);
assert.equal(ts.settings.userNetworkLogTarget,'SD','later settings saves preserve chosen log target');
assert.equal(ts.devLog.source,'path:/opt/scripta/var/network-events.log','viewer follows moved file');
assert.equal(ts.devStorageDisplayName('network-events.log'),'Network / Wi-Fi');
assert.equal(ts.devStorageDisplayName('cooling-events.log'),'Fan / temperature');
assert.equal(ts.devStorageDisplayName('lighttpd-error.log'),'Web server errors');
ts.devLog.mode='snapshot'; ts.devLog.text='retained snapshot';
diagnostic.target='RAM';
ts.devStorageSetTarget(diagnostic);
targetHarness.take('storage_set_target').ok({ok:true,key:'userNetworkLogTarget',target:'RAM'});
assert.equal(ts.devLog.mode,'snapshot','changing storage does not replace a state snapshot');
assert.equal(ts.devLog.text,'retained snapshot');
ts.devStorageSetTarget(diagnostic);
targetHarness.take('storage_set_target').fail();
assert(!diagnostic.saving);
targetHarness.take('storage');
assert(read('overlay/var/www/ng/app.js').includes('partials/dev-tools.html?v=collector-wait-2'));
console.log('PASS shared Logs viewer, problem-only collector warnings, recovery, snapshots and response races');
