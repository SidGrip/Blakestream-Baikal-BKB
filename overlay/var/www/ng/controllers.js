'use strict';

/* Controllers */

angular.module('Scripta.controllers', [])


// Main: stores status
.controller('CtrlMain', function($scope,$http,$timeout,$window,$filter) {
  // Settings
  $scope.settings={};
  $scope.settingsMaster={};
  // Pools
  $scope.pools={};
  $scope.options={};
  // Status
  $scope.status={};
  $scope.status.extra=true; // Request extra data
  $scope.title="Miner interface initialization";
  // Refresh
  $scope.intervalAuto = true; // Automatically adjust interval
  $scope.intervalMax = 20; // Default refresh rate
  // Live graph
  $scope.live=[];
  $scope.settings.liveMax=50;
  $scope.upLast=0;
  $scope.downLast=0;
  // Alerts
  Alertify.log.delay=10000;

  // Sync settings
  // Note: not possible to remove settings!
  $scope.sync = function(action,data,alert) {
    action = action || 'settings';
    data = data || 'load';
    $http.get('f_settings.php?'+action+'='+angular.toJson(data)).success(function(d){    
      if(d.info){
        angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
      }
      if(action=='settings'){
        $scope.settings=angular.copy(d['data']);
      }
      else if(action=='pools'){
        $scope.pools=d['data'];
      }
      else if(action=='options'){
        $scope.options=d['data'];
      }
      else if(action=='timezone'){
        $scope.settings.date=d.data.date;
      }
    });
  }
  $scope.syncDelay = function(ms,action,data,alert) {
    action = action || 'settings';
    data = data || false;
    ms = ms || 1000;
    var syncNow = function(){
      $scope.sync(action,data,alert);
    }
    return $timeout(syncNow, ms);
  }

  // ============================================================================
  // Blakestream-GaintB: in-page confirm modal (replaces browser confirm/alert)
  // Bound to overlay in index.php. Use bsConfirm(msg, onYes [, opts]) anywhere.
  // ============================================================================
  $scope.bsConfirmShow = false;
  $scope.bsConfirmMsg = '';
  $scope.bsConfirmTitle = '';
  $scope.bsConfirmYesLabel = '';
  $scope.bsConfirmNoLabel = '';
  $scope.bsConfirmShowCancel = true;
  $scope._bsConfirmOnYes = null;
  $scope._bsConfirmOnNo = null;

  $scope.bsConfirm = function(msg, onYes, opts) {
    opts = opts || {};
    $scope.bsConfirmMsg = msg;
    $scope.bsConfirmTitle = opts.title || '';
    $scope.bsConfirmYesLabel = opts.yesLabel || 'OK';
    $scope.bsConfirmNoLabel = opts.noLabel || 'Cancel';
    $scope.bsConfirmShowCancel = (opts.showCancel !== false);
    $scope._bsConfirmOnYes = onYes || null;
    $scope._bsConfirmOnNo = opts.onNo || null;
    $scope.bsConfirmShow = true;
  };
  // bsAlert: info-only, no Cancel button
  $scope.bsAlert = function(msg, opts) {
    opts = opts || {};
    opts.showCancel = false;
    opts.yesLabel = opts.yesLabel || 'OK';
    $scope.bsConfirm(msg, null, opts);
  };
  $scope.bsConfirmOk = function() {
    $scope.bsConfirmShow = false;
    var cb = $scope._bsConfirmOnYes;
    $scope._bsConfirmOnYes = null;
    $scope._bsConfirmOnNo = null;
    if (typeof cb === 'function') cb();
  };
  $scope.bsConfirmCancel = function() {
    $scope.bsConfirmShow = false;
    var cb = $scope._bsConfirmOnNo;
    $scope._bsConfirmOnYes = null;
    $scope._bsConfirmOnNo = null;
    if (typeof cb === 'function') cb();
  };

  // Footer system stats (memory, load).
  $scope.bsSysStats = {mem_pct: 0, mem_used_mb: 0, mem_total_mb: 0};
  $scope.bsLoadSysStats = function() {
    $http.get('f_blakestream.php?action=sysstats').success(function(d) {
      if (d) $scope.bsSysStats = d;
    });
  };
  $scope.bsLoadSysStats();

  // Timezone: push settings.userTimezone into BlakestreamTZ so charts and
  // bsTzDate-filtered timestamps re-render in the chosen zone.
  $scope.$watch('settings.userTimezone', function(tz) {
    if (typeof BlakestreamTZ === 'undefined' || !tz) return;
    BlakestreamTZ.setZone(tz);
    $scope.$broadcast('bsTzChanged');
  });

  // Per-board hashrate history. Poller fetches the full 7-day window once
  // a minute; each card filters to its own selected range client-side.
  $scope.bsHashrate = {boards: []};
  $scope.bsHashrateUpdated = 0;
  $scope.bsHashrateWindow = 168;
  // Per-board range selection persists in localStorage across reloads / re-logins.
  var BS_RANGES_KEY = 'bsHashrateRanges';
  var bsHashrateRanges = (function () {
    try { return JSON.parse(localStorage.getItem(BS_RANGES_KEY) || '{}') || {}; }
    catch (e) { return {}; }
  })();
  var saveRanges = function () {
    try { localStorage.setItem(BS_RANGES_KEY, JSON.stringify(bsHashrateRanges)); }
    catch (e) { /* best-effort */ }
  };
  var bsHashrateTimer = null;

  var bsChartsEnabled = function () {
    return !$scope.settings || $scope.settings.userCharts !== false;
  };

  $scope.bsLoadHashrateHistory = function(hours) {
    if (!bsChartsEnabled()) return;
    var h = hours || $scope.bsHashrateWindow || 168;
    $http.get('f_blakestream.php?action=load_hashrate_history&hours=' + h).success(function(d) {
      if (!d || !d.boards) return;
      $scope.bsHashrateUpdated = d.updated || 0;
      // Mutate in place to keep ng-repeat track-by-id stable.
      var prevById = {};
      angular.forEach($scope.bsHashrate.boards, function(b) { prevById[b.id] = b; });
      var next = [];
      angular.forEach(d.boards, function(srv) {
        var prev = prevById[srv.id];
        var b = prev || {id: srv.id, range: bsHashrateRanges[srv.id] || 'hour'};
        b.id = srv.id;
        b.enabled = srv.enabled;
        b.primary_pool_no = srv.primary_pool_no;
        b.effective_pool_no = srv.effective_pool_no;
        b.failover_active = srv.failover_active;
        b.pool_url = srv.pool_url;
        b.pool_name = srv.pool_name;
        b.samples = srv.samples;
        b.current_mhs5s = (srv.samples && srv.samples.length > 0) ? srv.samples[srv.samples.length - 1][1] : 0;
        if (!b.range) b.range = bsHashrateRanges[srv.id] || 'hour';
        next.push(b);
      });
      $scope.bsHashrate.boards = next;
    });
  };

  $scope.bsSetBoardRange = function(board, range) {
    board.range = range;
    bsHashrateRanges[board.id] = range;
    saveRanges();
  };

  var bsHashratePoll = function() {
    if (!bsChartsEnabled()) {
      bsHashrateTimer = null;
      return;
    }
    $scope.bsLoadHashrateHistory();
    bsHashrateTimer = $timeout(bsHashratePoll, 60000);
  };
  bsHashratePoll();

  // Settings checkbox handler. Flips userCharts (undefined = ON), updates
  // poll/cards, persists.
  $scope.bsToggleCharts = function() {
    if (!$scope.settings) return;
    var nowEnabled = $scope.settings.userCharts !== false;
    $scope.settings.userCharts = !nowEnabled;
    if (!$scope.settings.userCharts) {
      if (bsHashrateTimer) { $timeout.cancel(bsHashrateTimer); bsHashrateTimer = null; }
      $scope.bsHashrate = {boards: []};
    } else if (!bsHashrateTimer) {
      bsHashratePoll();
    }
    $scope.sync('settings', $scope.settings, 1);
  };

  // Sync settings
  $scope.sync('settings')

  // Get status and save in scope
  $scope.tick = function(once,all) {
    $http.get('f_status.php?'+($scope.settings.userDeveloper?'dev=1&':'')+(all||$scope.status.extra?'all=1':'')).success(function(d){
      if(d.info){
        angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
      }
      // Update status
      angular.forEach(d.status, function(v,k) {$scope.status[k]=v;});
      // Title
      $scope.title=$scope.status.minerDown?'Miner DOWN -':'['+$filter('mhs')($scope.status.dtot.MHS5s)+'h] ['+$scope.status.dtot.devices+' dev]';
      // Live Graphs
      $scope.live.push([Date.now(),1000000*$scope.status.dtot.MHS5s]);
      // Stop requesting extra data
      $scope.status.extra=false;
    })
    .error(function(){
      // Title
      $scope.title='Scripta DOWN -';
      // Live Graphs
      $scope.live.push([Date.now(),0]);
    })
    .then(function(){
      if($scope.live.length>$scope.settings.liveMax){
        $scope.live=$scope.live.slice(-$scope.settings.liveMax);
      }
      // Manage interval
      if($scope.interval<$scope.intervalMax){
        $scope.interval++;
      }
      // Refresh memory stats on the same cadence as the dashboard
      $scope.bsLoadSysStats();
    });
  }

  $scope.intervalSet = function(num) {
    if(num<2){
      $scope.intervalAuto=!$scope.intervalAuto;
      if($scope.intervalAuto) $scope.interval=1;
    }
    else{
      $scope.intervalMax=num;
      $scope.interval=num;
    }
  };
  var count = function () {
    $timeout(count, 1000);
    if($scope.counter>0){
      $scope.counter--;
    }
    else{
      $scope.counter=$scope.interval-1;
      $scope.tick();
    }
  };
  count();
  
  $scope.$watch('title', function(b,a) {
    $window.document.title=b+' Scripta';
  });
  
  $scope.$watch('intervalAuto', function(b,a) {
    Alertify.log.info('Automatic refresh rate '+(b?'en':'dis')+'abled');
  });
  
  $scope.$watch('intervalMax', function(b,a) {
    Alertify.log.info('Refresh rate is now '+b);
    if($scope.counter>b){
      $scope.counter=0;
    }
    $scope.status.extra=true;
  });

  $scope.$watch('status.minerDown', function(b,a) {
    if(b){
      $scope.upLast=Date.now();
      Alertify.log.error('Miner seems down');
    }
    else{
      $scope.downLast=Date.now();
      Alertify.log.success('Miner is up!');
    }
    $scope.interval=1;
    $scope.counter=0;
    $scope.status.extra=true;
  });
})


.controller('CtrlStatus', function($scope,$http,$timeout) {
  $scope.status.extra=true;
  $scope.num=0;

  // Blakestream-GaintB: load saved pools + per-board assignments so the
  // Devices table can render a per-board pool dropdown.
  $scope.savedPools = $scope.savedPools || {categories: []};
  // Per-board assignment shape: {primary: "pool-id"|null, failover: "pool-id"|null}
  $scope.assignments = $scope.assignments || {
    "0": {primary: null, failover: null},
    "1": {primary: null, failover: null},
    "2": {primary: null, failover: null}
  };
  // Snapshot of last-saved assignments — used to detect unsaved changes and revert
  $scope.bsAssignmentsOriginal = null;
  $scope.bsAssignmentsDirty = false;
  // Header dropdown lane: 'primary' (default) or 'failover'. Persists across reloads.
  $scope.bsAssignmentsView = (function() {
    try { return localStorage.getItem('bsAssignmentsView') || 'primary'; }
    catch (e) { return 'primary'; }
  })();
  $scope.bsSetAssignmentsView = function(v) {
    $scope.bsAssignmentsView = (v === 'failover') ? 'failover' : 'primary';
    try { localStorage.setItem('bsAssignmentsView', $scope.bsAssignmentsView); }
    catch (e) { /* best-effort */ }
  };
  // Per-board, per-lane "current category selection" — drives the cascading
  // pool dropdown's filter. Independent for primary vs failover view.
  $scope.boardCategories = {
    primary:  {"0": "", "1": "", "2": ""},
    failover: {"0": "", "1": "", "2": ""}
  };
  // Coerce legacy string-shape assignments to {primary, failover:null}.
  $scope.bsCoerceAssignments = function(raw) {
    var out = {};
    var ids = ['0','1','2'];
    for (var i = 0; i < ids.length; i++) {
      var b = ids[i];
      var v = raw && raw[b];
      if (v && typeof v === 'object') {
        out[b] = {
          primary:  (typeof v.primary  === 'string' && v.primary)  ? v.primary  : null,
          failover: (typeof v.failover === 'string' && v.failover) ? v.failover : null
        };
      } else if (typeof v === 'string' && v) {
        out[b] = {primary: v, failover: null};
      } else {
        out[b] = {primary: null, failover: null};
      }
    }
    return out;
  };

  // Look up which category contains a given pool id
  $scope.bsCategoryOfPool = function(poolId) {
    if (!poolId || !$scope.savedPools || !$scope.savedPools.categories) return '';
    for (var c = 0; c < $scope.savedPools.categories.length; c++) {
      var cat = $scope.savedPools.categories[c];
      for (var p = 0; p < (cat.pools || []).length; p++) {
        if (cat.pools[p].id === poolId) return cat.id;
      }
    }
    return '';
  };

  // Sync boardCategories from current assignments (call after loading) for both lanes.
  $scope.bsRebuildBoardCategories = function() {
    ['0','1','2'].forEach(function(b) {
      var slot = $scope.assignments && $scope.assignments[b];
      var pp = slot && slot.primary;
      var fp = slot && slot.failover;
      $scope.boardCategories.primary[b]  = pp ? $scope.bsCategoryOfPool(pp) : '';
      $scope.boardCategories.failover[b] = fp ? $scope.bsCategoryOfPool(fp) : '';
    });
  };

  // Pools belonging to a given category id (for the second dropdown)
  $scope.bsPoolsInCategory = function(catId) {
    if (!catId || !$scope.savedPools || !$scope.savedPools.categories) return [];
    for (var c = 0; c < $scope.savedPools.categories.length; c++) {
      if ($scope.savedPools.categories[c].id === catId) {
        return $scope.savedPools.categories[c].pools || [];
      }
    }
    return [];
  };

  // Effective category for a board+lane: single-category installs auto-select;
  // multi-category uses the user's per-lane category dropdown.
  $scope.bsEffectiveCategory = function(boardId, lane) {
    lane = (lane === 'failover') ? 'failover' : 'primary';
    if ($scope.savedPools && $scope.savedPools.categories &&
        $scope.savedPools.categories.length === 1) {
      return $scope.savedPools.categories[0].id;
    }
    return $scope.boardCategories[lane][String(boardId)];
  };

  // Category dropdown changed — clear the pool selection in the same lane
  // so the user picks afresh from the new category. Mark dirty.
  $scope.bsCategoryChanged = function(boardId, lane) {
    lane = (lane === 'failover') ? 'failover' : 'primary';
    var bid = String(boardId);
    if ($scope.assignments && $scope.assignments[bid]) {
      $scope.assignments[bid][lane] = null;
    }
    $scope.bsAssignmentsDirty = true;
  };

  // Pools available in the FAILOVER dropdown for a board: only same-algo as
  // that board's primary, excluding the primary itself. Returns [] if the
  // primary isn't set yet (UI disables the dropdown in that case).
  $scope.bsFailoverPoolsForBoard = function(boardId) {
    var bid = String(boardId);
    var slot = $scope.assignments && $scope.assignments[bid];
    var primary = slot && slot.primary;
    if (!primary || !$scope.savedPools || !$scope.savedPools.categories) return [];
    // Look up primary's algo
    var primaryAlgo = null;
    var cats = $scope.savedPools.categories;
    for (var c = 0; c < cats.length && !primaryAlgo; c++) {
      var pools = cats[c].pools || [];
      for (var p = 0; p < pools.length; p++) {
        if (pools[p].id === primary) { primaryAlgo = pools[p].algo; break; }
      }
    }
    if (!primaryAlgo) return [];
    // Collect all same-algo pools in the user-selected failover category,
    // dropping the primary itself.
    var catId = $scope.bsEffectiveCategory(bid, 'failover');
    var inCat = $scope.bsPoolsInCategory(catId);
    var out = [];
    for (var i = 0; i < inCat.length; i++) {
      if (inCat[i].id !== primary && inCat[i].algo === primaryAlgo) {
        out.push(inCat[i]);
      }
    }
    return out;
  };

  // True if the board has a primary pool set (so the failover dropdown should
  // be enabled). Used by the partial's ng-disabled binding.
  $scope.bsHasPrimary = function(boardId) {
    var slot = $scope.assignments && $scope.assignments[String(boardId)];
    return !!(slot && slot.primary);
  };

  // True if any board's primary is this pool AND the board has failed away
  // from it (currently mining on its backup).
  $scope.bsPoolIsActiveFailover = function(poolNo) {
    var devs = $scope.status && $scope.status.devs;
    if (!devs) return false;
    for (var i = 0; i < devs.length; i++) {
      var d = devs[i];
      if (d && d.FailoverActive && d.PrimaryPool === poolNo) return true;
    }
    return false;
  };

  // Live failover state for a specific board (drives the per-board chart's
  // FAILOVER chip). Reads status.devs which updates on the fast tick cycle,
  // not the 60s hashrate-history poll.
  $scope.bsBoardFailoverActive = function(boardId) {
    var devs = $scope.status && $scope.status.devs;
    if (!devs) return false;
    var bid = parseInt(boardId, 10);
    for (var i = 0; i < devs.length; i++) {
      var d = devs[i];
      if (d && d.ID === bid) return !!d.FailoverActive;
    }
    return false;
  };

  $scope.bsTempState = {};
  $scope.bsTempConfig = {enabled: true, disable_at: 80, recover_at: 70};

  $scope.bsLoadTempState = function() {
    $http.get('f_blakestream.php?action=load_temp_state').success(function(d) {
      if (d) {
        if (d.state) $scope.bsTempState = d.state;
        if (d.config) $scope.bsTempConfig = d.config;
      }
    });
  };

  $scope.bsBoardDisabled = function(boardId) {
    var s = $scope.bsTempState && $scope.bsTempState[String(boardId)];
    return !!(s && s.disabled);
  };
  $scope.bsBoardDisabledReason = function(boardId) {
    var s = $scope.bsTempState && $scope.bsTempState[String(boardId)];
    return (s && s.reason) || '';
  };

  $scope.bsLoadStatusPools = function() {
    $http.get('f_blakestream.php?action=load_pools').success(function(d) {
      if (d && d.categories) {
        $scope.savedPools = d;
        $scope.bsRebuildBoardCategories();
      }
    });
    $http.get('f_blakestream.php?action=load_assignments').success(function(d) {
      if (d) {
        $scope.assignments = $scope.bsCoerceAssignments(d);
        $scope.bsAssignmentsOriginal = angular.copy($scope.assignments);
        $scope.bsAssignmentsDirty = false;
        $scope.bsRebuildBoardCategories();
      }
    });
    $scope.bsLoadTempState();
  };
  $scope.bsLoadStatusPools();

  // Refresh temp state on each tick (piggybacks on the existing timer)
  $scope.$on('$destroy', function(){});
  var origTick = $scope.tick;
  if (typeof origTick === 'function') {
    $scope.tick = function(once, all) {
      $scope.bsLoadTempState();
      return origTick(once, all);
    };
  }

  // Flat list of saved pools, used by the per-board dropdown
  $scope.bsFlatPools = function() {
    var out = [];
    if (!$scope.savedPools || !$scope.savedPools.categories) return out;
    $scope.savedPools.categories.forEach(function(cat) {
      (cat.pools || []).forEach(function(p) {
        out.push({id: p.id, name: p.name, category: cat.name});
      });
    });
    return out;
  };

  // Per-board pool dropdown change — defer the save. The user batches up
  // multiple changes and clicks "Apply changes" below the table to commit.
  // When the primary changes, silently clear the existing failover if its
  // algo no longer matches the new primary's algo.
  $scope.bsAssignBoard = function(boardId, lane) {
    lane = (lane === 'failover') ? 'failover' : 'primary';
    var bid = String(boardId);
    if (lane === 'primary') {
      var slot = $scope.assignments && $scope.assignments[bid];
      if (slot && slot.failover) {
        var primaryAlgo = null, failoverAlgo = null;
        var cats = ($scope.savedPools && $scope.savedPools.categories) || [];
        for (var c = 0; c < cats.length; c++) {
          var pools = cats[c].pools || [];
          for (var p = 0; p < pools.length; p++) {
            if (pools[p].id === slot.primary)  primaryAlgo  = pools[p].algo;
            if (pools[p].id === slot.failover) failoverAlgo = pools[p].algo;
          }
        }
        if (!primaryAlgo || (failoverAlgo && primaryAlgo !== failoverAlgo)) {
          slot.failover = null;
          $scope.boardCategories.failover[bid] = '';
        }
      }
    }
    $scope.bsAssignmentsDirty = true;
  };

  // Apply all pending board assignment changes at once.
  $scope.bsApplyAssignments = function() {
    if (!$scope.assignments) return;
    $http.post('f_blakestream.php?action=save_assignments', $scope.assignments)
      .success(function(d) {
        if (d.ok) {
          $scope.bsAssignmentsOriginal = angular.copy($scope.assignments);
          $scope.bsAssignmentsDirty = false;
          Alertify.log.success('Assignments saved, miner restarting');
        } else {
          Alertify.log.error('Save failed: ' + (d.error || 'unknown'));
        }
      })
      .error(function() {
        Alertify.log.error('Failed to save assignments');
      });
  };

  // Revert pending changes back to the last-saved snapshot.
  $scope.bsRevertAssignments = function() {
    if (!$scope.bsAssignmentsOriginal) return;
    $scope.assignments = angular.copy($scope.bsAssignmentsOriginal);
    $scope.bsRebuildBoardCategories();
    $scope.bsAssignmentsDirty = false;
  };

  // Helper to look up the pool name for a given pool id (used in templates)
  $scope.bsPoolName = function(pid) {
    if (!pid) return '— idle —';
    var pools = $scope.bsFlatPools();
    for (var i = 0; i < pools.length; i++) {
      if (pools[i].id === pid) return pools[i].name;
    }
    return '(deleted)';
  };

  // Restart all (used by the global Restart button on the Status page)
  $scope.bsRestart = function() {
    $http.get('f_blakestream.php?action=restart').success(function(d) {
      if (d.ok) Alertify.log.success('Miner restarting');
    });
  };

  // Match a sgminer-API pool object back to one of our saved pools.
  // sgminer prefixes the URL with `http://quota:N;` (see cleanPoolUrl filter),
  // so we strip those before comparing.
  $scope.bsFindSavedByUrl = function(sgPool) {
    if (!sgPool || !sgPool.URL) return null;
    var cleanUrl = sgPool.URL.replace(/^https?:\/\//, '').replace(/^quota:\d+;/, '');
    if (!$scope.savedPools || !$scope.savedPools.categories) return null;
    for (var c = 0; c < $scope.savedPools.categories.length; c++) {
      var cat = $scope.savedPools.categories[c];
      for (var p = 0; p < (cat.pools || []).length; p++) {
        if (cat.pools[p].url === cleanUrl) return cat.pools[p];
      }
    }
    return null;
  };

  // SWITCH TO button: assign ALL 3 boards to this pool as their primary.
  // Preserves existing failover assignments (cleared only if wrong-algo).
  $scope.bsSwitchAllToPool = function(sgPool) {
    var match = $scope.bsFindSavedByUrl(sgPool);
    if (!match) {
      Alertify.log.error('Could not find saved pool matching this row');
      return;
    }
    $scope.assignments = $scope.assignments || {};
    ['0','1','2'].forEach(function(b) {
      if (!$scope.assignments[b]) $scope.assignments[b] = {primary: null, failover: null};
      $scope.assignments[b].primary = match.id;
      $scope.bsAssignBoard(b, 'primary');  // re-validate failover algo
    });
    $http.post('f_blakestream.php?action=save_assignments', $scope.assignments)
      .success(function(d) {
        if (d.ok) Alertify.log.success('All 3 boards switched to ' + match.name);
        else Alertify.log.error('Save failed: ' + (d.error || 'unknown'));
      });
  };

  // DISABLE button: any board currently using this pool (as primary OR failover)
  // gets that lane cleared.
  $scope.bsDisablePool = function(sgPool) {
    var match = $scope.bsFindSavedByUrl(sgPool);
    if (!match) {
      Alertify.log.error('Could not find saved pool matching this row');
      return;
    }
    var changed = 0;
    $scope.assignments = $scope.assignments || {};
    ['0','1','2'].forEach(function(b) {
      var slot = $scope.assignments[b];
      if (!slot) return;
      if (slot.primary === match.id)  { slot.primary  = null; changed++; }
      if (slot.failover === match.id) { slot.failover = null; changed++; }
    });
    if (changed === 0) {
      Alertify.log.log('No boards were using ' + match.name);
      return;
    }
    $http.post('f_blakestream.php?action=save_assignments', $scope.assignments)
      .success(function(d) {
        if (d.ok) Alertify.log.success('Disabled ' + match.name + ' on ' + changed + ' board(s)');
        else Alertify.log.error('Save failed: ' + (d.error || 'unknown'));
      });
  };

  $scope.graphUpdate = function() {
    $http.get('f_graph.php').success(function(d){
		
      if(d){
        Alertify.log.success("Graphs updated");
      } 
      else{
        Alertify.log.error("Update graph ended in error");
      }
      $scope.num++;
    }).error(function(){
      Alertify.log.error("Update graph ended in error?");
    });
  } 
  
  $scope.graphReset = function() {
  	$http.get('f_miner.php?command=restart').success(function(d){
      if(d.info){
        angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
      }
      $scope.tick();
    });
  	
    $http.get('f_graphReset.php').success(function(d){
		
      if(d){
        Alertify.log.success("Graphs reset");
      } 
      else{
        Alertify.log.error("Reset graph ended in error");
      }
      $scope.num++;
    }).error(function(){
      Alertify.log.error("Reset graph ended in error?");
    });
  } 
  
  $scope.cgminer = function(command,parameter) {
    $scope.tick();

    var execute = function(){
        $http.get('f_miner.php?command='+(command || 'summary')+'&parameter='+parameter).success(function(d){
            if(d.info){
                angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
            }
            $scope.tick();
        });
    }
    $timeout(execute, 1000);
  };


})


.controller('CtrlMiner', function($scope,$http,$timeout) {
  $scope.status.extra=true;
  $scope.sync('pools');
  $scope.sync('options');

  $scope.minerCompat = function(command,parameter) {
    $http.get('f_minercompat.php').success(function(d){
      if(d.info){
        angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
      }
      if(d.data.pools){
        $scope.pools=d.data.pools;
      }
      if(d.data.options){
        $scope.options=d.data.options;
      }
    });
  }

  $scope.cgminer = function(command,parameter) {
    $scope.tick();

    var execute = function(){
      $http.get('f_miner.php?command='+(command || 'summary')+'&parameter='+parameter).success(function(d){
        if(d.info){
          angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
        }
        $scope.tick();
      });
    }
    $timeout(execute, 1000);
  };

 $scope.cgminerHardCtl = function(command) {
    $scope.tick();

    var execute = function(){
      $http.get('f_minerHardCtl.php?command='+(command)).success(function(d){
        if(d.info){
          angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
        }
        $scope.tick();
      });
    }
    $timeout(execute, 1000);
  };

 $scope.hostHardCtl = function(command) {
   $scope.tick();			
   var execute = function(){
    $http.get('f_hostHardCtl.php?command='+(command)+'&pass=p0c4t0p4').success(function(d){
        if(d.info){
          angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
        }
        $scope.tick();
      });
    }
    $timeout(execute, 1000);
};	
 
  $scope.poolAdd = function(a) {
    a = a || {};
    $scope.pools.push(a);
    $scope.poolForm.$setDirty()
  };
  $scope.poolRemove = function(index) {
    $scope.pools.splice(index,1);
    $scope.poolForm.$setDirty()
  };
  $scope.poolSave = function() {
    $scope.sync('pools',$scope.pools,1);
    $scope.poolForm.$setPristine();
  };
  $scope.poolBack = function() {
    $scope.sync('pools',0,1);
    $scope.poolForm.$setPristine();
  };

  // ============================================================================
  // Blakestream-GaintB: saved-pools / categories / board-assignments
  // Talks to f_blakestream.php (separate from f_settings.php to keep additive
  // code isolated from upstream Scripta).
  // ============================================================================

  $scope.savedPools = {categories: []};
  $scope.assignments = {};
  $scope.editingPool = null;
  $scope.newCategoryName = '';

  // Returns a fresh poolDraft for the Add Pool form. Auto-selects the
  // category when there is exactly one (no point making the user click
  // through a single-option dropdown).
  $scope.bsFreshPoolDraft = function() {
    var d = {algo: 'blake256r8', pass: 'x', extranonce: false};
    var cats = ($scope.savedPools && $scope.savedPools.categories) || [];
    if (cats.length === 1 && cats[0] && cats[0].id) {
      d.categoryId = cats[0].id;
    }
    return d;
  };

  $scope.poolDraft = $scope.bsFreshPoolDraft();

  // UUID-ish helper (no crypto needed, just unique enough within the catalog)
  $scope.bsUuid = function(prefix) {
    return (prefix || 'id') + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2,8);
  };

  $scope.bsLoadPools = function() {
    $http.get('f_blakestream.php?action=load_pools').success(function(d) {
      if (d && d.categories) {
        $scope.savedPools = d;
        // After categories load, if the Add Pool form is open with no
        // category yet picked, auto-select the only category if there's
        // exactly one. Doesn't override an in-progress edit.
        if (!$scope.editingPool && $scope.poolDraft && !$scope.poolDraft.categoryId
            && d.categories.length === 1 && d.categories[0].id) {
          $scope.poolDraft.categoryId = d.categories[0].id;
        }
      }
    }).error(function() {
      Alertify.log.error('Failed to load saved pools');
    });
  };

  $scope.bsLoadAssignments = function() {
    $http.get('f_blakestream.php?action=load_assignments').success(function(d) {
      if (d) $scope.assignments = d;
    });
  };

  $scope.bsSavePoolsBlob = function(silent) {
    return $http.post('f_blakestream.php?action=save_pools', $scope.savedPools)
      .success(function(d) {
        if (!silent) {
          if (d.ok) Alertify.log.success('Pools saved, miner restarted');
          else Alertify.log.error('Save failed: ' + (d.error || 'unknown'));
        }
      })
      .error(function() {
        Alertify.log.error('Failed to save pools');
      });
  };

  $scope.bsAddCategory = function() {
    var name = ($scope.newCategoryName || '').trim();
    if (!name) return;
    if (!$scope.savedPools.categories) $scope.savedPools.categories = [];
    // Check duplicate name
    for (var i = 0; i < $scope.savedPools.categories.length; i++) {
      if ($scope.savedPools.categories[i].name.toLowerCase() === name.toLowerCase()) {
        Alertify.log.error('Category "' + name + '" already exists');
        return;
      }
    }
    $scope.savedPools.categories.push({
      id: $scope.bsUuid('cat'),
      name: name.toUpperCase(),
      pools: []
    });
    $scope.newCategoryName = '';
    $scope.bsSavePoolsBlob();
  };

  $scope.bsRemoveCategory = function(cat) {
    if (cat.pools && cat.pools.length > 0) {
      Alertify.log.error('Category not empty');
      return;
    }
    $scope.bsConfirm('Delete category <b>' + cat.name + '</b>?', function() {
      $scope.savedPools.categories = $scope.savedPools.categories.filter(function(c) {
        return c.id !== cat.id;
      });
      $scope.bsSavePoolsBlob();
    }, {title: 'Delete category', yesLabel: 'Delete'});
  };

  // Blakestream-GaintB: multi-pool batch edit.
  // pendingEdits is a map of poolId -> draftSnapshot for pools the user has
  // edited and then SWITCHED AWAY from (without clicking Update Pool yet).
  // The currently-loaded poolDraft is the "live" edit. When the user clicks
  // Update Pool / Update All Pools, we commit the live draft to pendingEdits
  // (if dirty) and then apply all entries in batch.
  $scope.pendingEdits = {};

  // Snapshot helper: extract just the editable fields from a saved pool object
  // so we can compare apples to apples against poolDraft.
  $scope.bsPoolFingerprint = function(p) {
    if (!p) return '';
    return JSON.stringify({
      name: p.name || '',
      url: p.url || '',
      algo: p.algo || 'blake256r8',
      user: p.user || '',
      pass: p.pass || 'x',
      extranonce: !!p.extranonce,
      categoryId: p.categoryId || ''
    });
  };

  // Locate the original (saved-on-disk) pool by id and return its category id too.
  $scope.bsFindOriginalPool = function(poolId) {
    if (!poolId || !$scope.savedPools || !$scope.savedPools.categories) return null;
    for (var i = 0; i < $scope.savedPools.categories.length; i++) {
      var cat = $scope.savedPools.categories[i];
      var pools = cat.pools || [];
      for (var j = 0; j < pools.length; j++) {
        if (pools[j].id === poolId) {
          return angular.extend({}, pools[j], {categoryId: cat.id});
        }
      }
    }
    return null;
  };

  // Is the current poolDraft different from the on-disk version?
  $scope.bsCurrentDraftDirty = function() {
    if (!$scope.editingPool || !$scope.poolDraft || !$scope.poolDraft.id) return false;
    var orig = $scope.bsFindOriginalPool($scope.poolDraft.id);
    return $scope.bsPoolFingerprint($scope.poolDraft) !== $scope.bsPoolFingerprint(orig);
  };

  // Has this specific pool got pending edits (either committed to pendingEdits
  // or it's the live draft and currently dirty)?
  $scope.bsPoolHasPendingEdits = function(poolId) {
    if (!poolId) return false;
    if ($scope.pendingEdits[poolId]) return true;
    if ($scope.editingPool && $scope.editingPool.id === poolId &&
        $scope.bsCurrentDraftDirty()) return true;
    return false;
  };

  // Commit current poolDraft to pendingEdits if dirty. Called whenever the
  // user is about to switch away from the current edit.
  $scope.bsCommitCurrentToPending = function() {
    if ($scope.bsCurrentDraftDirty()) {
      $scope.pendingEdits[$scope.poolDraft.id] = angular.copy($scope.poolDraft);
    }
  };

  // How many pools have pending edits in total (committed + currently dirty)?
  $scope.bsPendingEditCount = function() {
    var n = 0;
    for (var k in $scope.pendingEdits) { if ($scope.pendingEdits.hasOwnProperty(k)) n++; }
    if ($scope.editingPool && $scope.bsCurrentDraftDirty() &&
        !$scope.pendingEdits[$scope.editingPool.id]) n++;
    return n;
  };

  // Label the save button accordingly.
  $scope.bsUpdatePoolLabel = function() {
    if (!$scope.editingPool) return 'Save Pool';
    var n = $scope.bsPendingEditCount();
    if (n > 1) return 'Update All Pools (' + n + ')';
    return 'Update Pool';
  };

  $scope.bsEditPool = function(pool, cat) {
    // Commit the previous edit (if any) before switching
    $scope.bsCommitCurrentToPending();

    $scope.editingPool = pool;

    // If this pool already has pending edits, restore them. Otherwise load fresh.
    if ($scope.pendingEdits[pool.id]) {
      $scope.poolDraft = angular.copy($scope.pendingEdits[pool.id]);
    } else {
      $scope.poolDraft = {
        id: pool.id,
        name: pool.name,
        categoryId: cat.id,
        url: pool.url,
        algo: pool.algo,
        user: pool.user,
        pass: pool.pass,
        extranonce: pool.extranonce
      };
    }
  };

  $scope.bsCancelEdit = function() {
    var n = $scope.bsPendingEditCount();
    var doReset = function() {
      $scope.pendingEdits = {};
      $scope.editingPool = null;
      $scope.poolDraft = $scope.bsFreshPoolDraft();
    };
    if (n > 1) {
      $scope.bsConfirm(
        'Discard pending edits to <b>' + n + '</b> pools?',
        doReset,
        {title: 'Discard changes', yesLabel: 'Discard', noLabel: 'Keep editing'}
      );
    } else {
      doReset();
    }
  };

  // Apply a single draft snapshot back into savedPools.categories.
  // Mutates savedPools in place. Returns true on success.
  $scope.bsApplyDraftToTree = function(d) {
    if (!d || !d.name || !d.url || !d.categoryId) return false;
    var newPool = {
      id: d.id || $scope.bsUuid('pool'),
      name: d.name,
      url: d.url,
      algo: d.algo || 'blake256r8',
      user: d.user || '',
      pass: d.pass || 'x',
      extranonce: !!d.extranonce
    };
    if (d.id) {
      // Update in place. Move between categories if needed.
      for (var i = 0; i < $scope.savedPools.categories.length; i++) {
        var cat = $scope.savedPools.categories[i];
        for (var j = 0; j < cat.pools.length; j++) {
          if (cat.pools[j].id === d.id) {
            cat.pools.splice(j, 1);
            break;
          }
        }
      }
    }
    var targetCat = $scope.savedPools.categories.find(function(c){return c.id === d.categoryId;});
    if (!targetCat) return false;
    if (!targetCat.pools) targetCat.pools = [];
    targetCat.pools.push(newPool);
    return true;
  };

  $scope.bsSavePoolDraft = function() {
    var d = $scope.poolDraft;
    if (!d.name || !d.url || !d.categoryId) {
      Alertify.log.error('Name, URL and Category required');
      return;
    }

    if (!$scope.editingPool) {
      // ADD path. New pools are not batched — saved immediately, alone.
      if (!$scope.bsApplyDraftToTree(d)) {
        Alertify.log.error('Could not save pool (category not found)');
        return;
      }
      $scope.bsSavePoolsBlob();
      $scope.pendingEdits = {};
      $scope.editingPool = null;
      $scope.poolDraft = $scope.bsFreshPoolDraft();
      return;
    }

    // UPDATE path. Commit the live draft if dirty, then apply ALL pending
    // edits across pools in a single save.
    $scope.bsCommitCurrentToPending();

    var failed = [];
    for (var pid in $scope.pendingEdits) {
      if (!$scope.pendingEdits.hasOwnProperty(pid)) continue;
      if (!$scope.bsApplyDraftToTree($scope.pendingEdits[pid])) {
        failed.push(pid);
      }
    }

    if (failed.length > 0) {
      Alertify.log.error('Could not apply ' + failed.length + ' pool change(s)');
    }

    $scope.bsSavePoolsBlob();
    $scope.pendingEdits = {};
    $scope.editingPool = null;
    $scope.poolDraft = $scope.bsFreshPoolDraft();
  };

  $scope.bsDeletePool = function() {
    if (!$scope.editingPool) return;
    var pid = $scope.editingPool.id;
    var pname = $scope.editingPool.name;

    // Find any board referencing this pool as primary or failover
    var assignedBoards = [];
    for (var bid in $scope.assignments) {
      var slot = $scope.assignments[bid];
      if (slot && (slot.primary === pid || slot.failover === pid)) assignedBoards.push(bid);
    }

    var doDelete = function() {
      // Auto-unassign affected boards (clear whichever lane referenced the pool)
      if (assignedBoards.length > 0) {
        assignedBoards.forEach(function(b){
          var slot = $scope.assignments[b];
          if (!slot) return;
          if (slot.primary === pid)  slot.primary  = null;
          if (slot.failover === pid) slot.failover = null;
        });
        $http.post('f_blakestream.php?action=save_assignments', $scope.assignments);
      }
      // Remove from saved-pools
      $scope.savedPools.categories.forEach(function(cat) {
        cat.pools = (cat.pools || []).filter(function(p){return p.id !== pid;});
      });
      // Drop any pending edits for the deleted pool so they don't resurrect it
      delete $scope.pendingEdits[pid];
      $scope.bsSavePoolsBlob();
      $scope.editingPool = null;
      $scope.poolDraft = $scope.bsFreshPoolDraft();
    };

    if (assignedBoards.length > 0) {
      $scope.bsConfirm(
        'Pool <b>' + pname + '</b> is currently assigned to board(s) <b>' +
        assignedBoards.join(', ') + '</b>.<br>Delete it anyway? Those board(s) will become idle.',
        doDelete,
        {title: 'Delete pool', yesLabel: 'Delete'}
      );
    } else {
      $scope.bsConfirm(
        'Delete pool <b>' + pname + '</b>?',
        doDelete,
        {title: 'Delete pool', yesLabel: 'Delete'}
      );
    }
  };

  $scope.bsRestart = function() {
    $http.get('f_blakestream.php?action=restart').success(function(d) {
      if (d.ok) Alertify.log.success('Miner restarted');
      else Alertify.log.error('Restart failed: ' + (d.error || 'unknown'));
    });
  };

  // Auto-load on controller init
  $scope.bsLoadPools();
  $scope.bsLoadAssignments();

  $scope.optionAdd = function(a) {
    a = a || {};
    $scope.options.push(a);
    $scope.optionForm.$setDirty()
  };
  $scope.optionRemove = function(index) {
    $scope.options.splice(index,1);
    $scope.optionForm.$setDirty()
  };
  $scope.optionSave = function() {
    $scope.sync('options',$scope.options,1);
    $scope.optionForm.$setPristine();
  };
  $scope.optionBack = function() {
    $scope.sync('options',0,1);
    $scope.optionForm.$setPristine();
  };

  $scope.filterOption = function(){
      return function(option) {
          return option.key.indexOf("api") == -1;
      }
  }
})


.controller('CtrlSettings', function($scope) {
  $scope.status.extra=true;
})


.controller('CtrlPoolHistory', function($scope, $http) {
  // Per-pool aggregate stats over a configurable window. Data is recorded
  // every 5 minutes by /opt/scripta/bin/pool-history-recorder.py and stored
  // in /opt/scripta/var/pool-history.json. Loaded via f_blakestream.php.
  $scope.bsPoolHistory = [];
  $scope.bsHistoryWindow = 24;
  $scope.bsHistoryUpdated = 0;

  $scope.bsLoadPoolHistory = function(hours) {
    var h = hours || $scope.bsHistoryWindow || 24;
    $http.get('f_blakestream.php?action=load_pool_history&hours=' + h).success(function(d) {
      if (d) {
        $scope.bsPoolHistory = d.pools || [];
        $scope.bsHistoryWindow = d.window_hours;
        $scope.bsHistoryUpdated = d.updated;
      }
    }).error(function() {
      Alertify.log.error('Failed to load pool history');
    });
  };

  $scope.bsLoadPoolHistory(24);
})




.controller('CtrlBackup', function($scope,$http,$timeout) {
  $scope.thisFolder = '/opt/scripta/';
  $scope.backupFolder = '/opt/scripta/backup/';
  $scope.backupName = GetDateTime();
  $scope.backups = [];
  $scope.restoring = 0;
  $scope.items = [
  {selected:true,name:'etc/miner.conf'},
  {selected:true,name:'etc/scripta.conf'},
  {selected:true,name:'etc/miner.pools.json'},
  {selected:true,name:'etc/miner.options.json'}
  ];
  
  $scope.addItem = function() {
    $scope.items.push({selected:true,name:$scope.newItem});
    $scope.newItem = '';
  };
  $scope.selItem = function() {
    var count = 0;
    angular.forEach($scope.items, function(item) {
      count += item.selected ? 1 : 0;
    });
    return count;
  };
  

  // Blakestream-GaintB: wrappers that fall back to a date/time name if the
  // user clears the field. Wired up from the Backup partial buttons.
  $scope.bsBackupNow = function() {
    if (!$scope.backupName) $scope.backupName = GetDateTime();
    return $scope.backupLocal();
  };
  $scope.bsBackupExportNow = function() {
    if (!$scope.backupName) $scope.backupName = GetDateTime();
    return $scope.backupExport();
  };

  $scope.backupLocal = function() {
    var promise = $http.get('f_backup.php?name='+$scope.backupName+'&backup='+angular.toJson($scope.items)).success(function(d){
      if(d.info){
        angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
      }
      angular.forEach(d.data, function(v,k) {
        if(v.success){
          $scope.items[k].bak=true;
          $scope.items[k].selected=false;
        }
        else{
          $scope.items[k].fail=true;
        }
      });// Add to existing
      $scope.reload();
    });
    return promise;
  };

  $scope.backupExport = function() {
    $scope.backupLocal().then(function(){
      $scope.exportZip($scope.backupName);
    });
  };

  $scope.exportZip = function(name) {
    name=name||$scope.backups[$scope.restoring].dir;
    window.location.href='f_backup.php?export='+name;
  };

  $scope.choose = function(i) {
    $scope.restoring=i;
  };

  $scope.restore = function() {
    $http.get('f_backup.php?restore='+$scope.backups[$scope.restoring].dir).success(function(d){
      if(d.info){
        angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
      }
      $scope.syncDelay(300,'settings');
      $scope.syncDelay(600,'pools');
      $scope.syncDelay(900,'options');
    });
    
    var restart = function() {
      $http.get('f_miner.php?command=restart').success(function(d){
        if(d.info){
          angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
        }
      });
    }
    $timeout(restart, 900);    
  };

  $scope.reload = function(wait) {
    wait=wait||0;
    var syncNow = function(){
      $http.get('f_backup.php').success(function(d){
        if(d.data){
          // Blakestream-GaintB: hide the factory Example and Nicehash-SMA
          // backups - they were Baikal demo configs that are no longer
          // relevant for a Blakecoin miner.
          $scope.backups = d.data.filter(function(b){
            return b.dir !== 'Example' && b.dir !== 'Nicehash-SMA';
          });
          if ($scope.restoring >= $scope.backups.length) {
            $scope.restoring = 0;
          }
          // Seed renameInput from the currently selected backup so the
          // user can edit it in place
          if ($scope.backups[$scope.restoring]) {
            $scope.renameInput = $scope.backups[$scope.restoring].dir;
          } else {
            $scope.renameInput = '';
          }
        }
      });
    }
    //return
    $timeout(syncNow, wait);
  };

  // Blakestream-GaintB: also seed renameInput when the user picks a different
  // backup from the list, so the rename field reflects the current selection.
  var origChoose = $scope.choose;
  $scope.choose = function(i) {
    origChoose(i);
    if ($scope.backups[i]) {
      $scope.renameInput = $scope.backups[i].dir;
    }
  };

  $scope.bsDeleteBackup = function() {
    var sel = $scope.backups[$scope.restoring];
    if (!sel) return;
    $scope.bsConfirm(
      'This cannot be undone.',
      function() {
        $http.get('f_blakestream.php?action=delete_backup&name=' + encodeURIComponent(sel.dir))
          .success(function(d) {
            if (d && d.info) {
              angular.forEach(d.info, function(v) { Alertify.log.create(v.type, v.text); });
            }
            $scope.restoring = 0;
            $scope.reload();
          })
          .error(function(d) {
            Alertify.log.create('danger',
              (d && d.error) ? d.error : 'Failed to delete backup');
          });
      },
      {title: 'Delete backup "' + sel.dir + '"', yesLabel: 'Delete', noLabel: 'Cancel'}
    );
  };

  $scope.bsRenameBackup = function() {
    var sel = $scope.backups[$scope.restoring];
    if (!sel) return;
    var newName = ($scope.renameInput || '').trim();
    if (!newName) {
      Alertify.log.create('danger', 'Rename: name cannot be empty');
      return;
    }
    if (newName === sel.dir) {
      return;  // no-op
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(newName)) {
      Alertify.log.create('danger',
        'Rename: only letters, numbers, dot, dash, underscore (max 64 chars)');
      return;
    }
    $http.get('f_blakestream.php?action=rename_backup' +
              '&name=' + encodeURIComponent(sel.dir) +
              '&to=' + encodeURIComponent(newName))
      .success(function(d) {
        if (d && d.info) {
          angular.forEach(d.info, function(v) { Alertify.log.create(v.type, v.text); });
        }
        $scope.reload();
      })
      .error(function(d) {
        Alertify.log.create('danger',
          (d && d.error) ? d.error : 'Failed to rename backup');
      });
  };

  $scope.reload();
});



function GetDateTime() {
  var now = new Date();
  return [[now.getFullYear(),AddZero(now.getMonth() + 1),AddZero(now.getDate())].join(''), [AddZero(now.getHours()), AddZero(now.getMinutes())].join('')].join('-');
}

function AddZero(num) {
  return (num >= 0 && num < 10) ? '0' + num : num + '';
}
