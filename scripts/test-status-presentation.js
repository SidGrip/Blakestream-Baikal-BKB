'use strict';
const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const root = path.join(__dirname, '..', 'overlay/var/www');
const source = fs.readFileSync(path.join(root, 'ng/controllers.js'), 'utf8');
const header = {};
const headerStart = source.indexOf('  $scope.bsHeaderHashrate =');
vm.runInNewContext(source.slice(headerStart, source.indexOf('  // Get status and save in scope', headerStart)), {$scope:header});
const total = {EstimatedHashrate:168000, MHS5s:999999, Accepted15m:160000,
  Accepted15mMeasuring:true, Accepted1h:155000, Accepted1hMeasuring:false, Accepted1hFullWindow:false};
assert.equal(header.bsHeaderHashrate(total), 168000);
total.Accepted15mMeasuring = false;
assert.equal(header.bsHeaderHashrate(total), 160000);
total.Accepted1hFullWindow = true;
assert.equal(header.bsHeaderHashrate(total), 155000);
total.Accepted1h = 0;
assert.equal(header.bsHeaderHashrate(total), 0, 'zero measured work is not replaced with an estimate');
total.Accepted1hMeasuring = true;
total.Accepted15m = 0;
assert.equal(header.bsHeaderHashrate(total), 0);
for (const invalid of [null, undefined, NaN, Infinity, -1, '1200']) {
  total.Accepted15m = invalid;
  assert.equal(header.bsHeaderHashrate(total), 168000);
  assert.equal(header.bsHeaderHashrate({EstimatedHashrate:invalid}), null);
}
assert(source.includes('$scope.bsHeaderHashrate($scope.status.dtot)'));
const start = source.indexOf('  $scope.bsBoardExperimentalClockIndex =');
const end = source.indexOf('  // Live effective-pool name', start);
const scope = {status:{minerUp:true,devs:[{ID:1, ExperimentalClock:true, EstimatedHashrate:32100, Accepted15m:null}]}};
vm.runInNewContext(source.slice(start, end), {$scope:scope});
assert(scope.bsBoardExperimentalClockIndex(1));
assert.equal(scope.bsBoardHashrate(1), 32100, 'baseline headline does not wait for accepted work');
assert.equal(scope.bsBoardHashrate(0), null);
scope.status.devs[0].Accepted15m = 63250;
assert.equal(scope.bsBoardHashrate(1), 32100, 'accepted work stays separate from headline');
scope.status.devs[0].Enabled = 'N';
assert.equal(scope.bsBoardHashrate(1), 0);
scope.status.devs[0].Enabled = 'Y';
scope.status.devs[0].EffectivePool = -1;
assert.equal(scope.bsBoardHashrate(1), 0);
scope.status.devs[0].EffectivePool = 0;
for (const invalid of [null, undefined, '32100', -1, NaN, Infinity]) {
  scope.status.devs[0].EstimatedHashrate = invalid;
  assert.equal(scope.bsBoardHashrate(1), null);
}
scope.status.minerUp = false;
assert.equal(scope.bsBoardHashrate(1), 0, 'offline miner cannot reuse historical rate');
assert.equal(scope.bsAcceptedCoverage({Accepted15mSeconds:480}, '15m'), '8.0 / 15 min sampled (partial window)');
assert.equal(scope.bsAcceptedCoverage({Accepted15mSeconds:900}, '15m'), '');
assert.equal(scope.bsAcceptedCoverage({Accepted1hSeconds:3600}, '1h'), '');
assert.equal(scope.bsAcceptedCoverage({}, '1h'), 'Waiting for accepted work');
const html = fs.readFileSync(path.join(root, 'partials/status.html'), 'utf8');
assert(!html.includes('Index test') && !html.includes('bs-clock-index-test-tag'));
assert(html.includes('bsBoardHashrate(board.id)|mhs'));
assert(!html.includes('bsBoardAcceptedRate'));
assert(!/bs-device-clock-toggle[^>]*data-tip/.test(html));
assert(!/bs-device-clock-cell[\s\S]*?<\/td>/.exec(html)[0].includes('data-tip'));
assert(!html.includes('data-tip="Source:'));
assert(/<div class="bs-pools-wrapper">\s*<table/.test(html), 'pool table scrolls within the dashboard');
assert(!/bs-hashrate-graph[^>]+ng-if/.test(html));
assert(!/bs-range-tabs[^>]+ng-if/.test(html));
assert(!html.includes('data-tip="HW errors:') && !html.includes('data-tip="Rejected:'));
assert(!html.includes('<th title=') && !html.includes('<div title='));
const charts = [], definitions = {}, chain = {directive(name, factory) { definitions[name] = factory(); return chain; }};
vm.runInNewContext(fs.readFileSync(path.join(root, 'ng/directives.js'), 'utf8'), {
  angular:{module:()=>chain}, Date,
  document:{body:{classList:{contains:()=>true}}},
  window:{addEventListener(){}, removeEventListener(){}},
  Highcharts:{setOptions(){}, Chart:function(options) {
    this.options = options; this.xAxis = [{setExtremes(){}}];
    this.series = [{setData(data){ options.series[0].data = data; }}];
    this.destroy = function(){ this.destroyed = true; }; charts.push(this);
  }}, BlakestreamTZ:{format:()=>''}
});
const now = Math.floor(Date.now()/1000), watches = {};
const boardScope = {experimental:false, boardData:{id:1, range:'hour', samples:[[now,56000]], accepted_samples:[[now-60,null],[now,63250]]},
  $watch(key, fn){watches[key]=fn;}, $on(){}};
definitions.bsHashrateChart.link(boardScope, [{}], {});
watches['boardData.samples']();
assert.equal(charts[0].options.series[0].data[0][1], 56000);
boardScope.experimental = true; watches.experimental();
assert(charts[0].destroyed);
assert.equal(charts[1].options.series[0].data[0][1], null);
assert.equal(charts[1].options.series[0].data[1][1], 63250);
assert(charts[1].options.tooltip.formatter.call({x:now, y:63250}).includes('Accepted 15m:'));
assert.equal(charts[1].options.tooltip.borderColor, '#4fc3f7');
const listeners = {}, classes = new Set();
const tip = {
  classList:{add:name=>classes.add(name), remove:name=>classes.delete(name)},
  contains:node=>node===tip || node===icon
};
const icon = {closest:()=>tip};
vm.runInNewContext(fs.readFileSync(path.join(root, 'js/blakestream-tooltips.js'), 'utf8'), {
  document:{addEventListener:(name, handler)=>{ listeners[name]=handler; }}
});
listeners.click({target:icon});
assert(classes.has('bs-tip-dismissed'), 'clicking a nested icon dismisses its action tooltip');
listeners.mouseout({target:icon, relatedTarget:tip});
assert(classes.has('bs-tip-dismissed'), 'moving within the action does not reopen the tooltip');
listeners.mouseout({target:icon, relatedTarget:null});
assert(!classes.has('bs-tip-dismissed'), 'leaving the action enables the next hover');
listeners.click({target:icon, detail:0});
assert(classes.has('bs-tip-dismissed'), 'keyboard activation also dismisses the tooltip');
listeners.focusout({target:icon, relatedTarget:null});
assert(!classes.has('bs-tip-dismissed'), 'returning keyboard focus can show the tooltip again');
listeners.click({target:{closest:()=>null}});
const css = fs.readFileSync(path.join(root, 'css/blakestream-dark.css'), 'utf8');
assert(!/\.bs-tip[^\n]*:focus::/.test(css), 'pointer focus must not keep tooltips open');
assert(css.includes('.bs-tip[data-tip]:focus-visible::after'));
assert(fs.readFileSync(path.join(root, 'index.php'), 'utf8').includes('js/blakestream-tooltips.js?v='));
console.log('PASS clock-adjusted headlines, accepted history, tooltip-free clocks/rates and action-tooltip dismissal');
