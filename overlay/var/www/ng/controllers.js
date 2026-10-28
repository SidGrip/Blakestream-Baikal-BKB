'use strict';

/* Controllers */

angular.module('Scripta.controllers', [])


// Main: stores status
.controller('CtrlMain', function($scope,$rootScope,$http,$timeout,$window,$filter) {
  var bsViewDefaults = {miner:'pools', settings:'scripta', dev:'boards'};
  var bsViewAllowed = {miner:['pools','clocks','sgminer'], settings:['scripta','mining','network','wifi'],
    dev:['boards','logs','network','api','firmware']};
  var bsViewLogin = $window.document.body.getAttribute('data-view-session');
  var bsViews = {login:bsViewLogin, minerSession:null, tabs:{}};
  try {
    var savedViews = JSON.parse($window.sessionStorage.getItem('bsViewState') || 'null');
    if (savedViews && savedViews.login === bsViewLogin && savedViews.tabs && typeof savedViews.tabs === 'object') bsViews = savedViews;
  } catch (e) {}
  function bsSaveViews() {
    try { $window.sessionStorage.setItem('bsViewState', JSON.stringify(bsViews)); } catch (e) {}
  }
  $scope.bsViewTab = function(section) {
    var tab = bsViews.tabs[section];
    return bsViewAllowed[section].indexOf(tab) >= 0 ? tab : bsViewDefaults[section];
  };
  $scope.bsRememberViewTab = function(section, tab) {
    if (!bsViewAllowed[section] || bsViewAllowed[section].indexOf(tab) < 0) return;
    bsViews.tabs[section] = tab;
    bsSaveViews();
  };
  $scope.bsObserveMinerSession = function(session) {
    if (typeof session !== 'string' || !session) return;
    if (bsViews.minerSession && bsViews.minerSession !== session) {
      bsViews.tabs = {};
      $scope.$broadcast('bsViewTabsReset');
    }
    bsViews.minerSession = session;
    bsSaveViews();
  };

  // Settings
  $scope.settings={};
  $scope.settingsMaster=null;
  // Feature checkboxes stay drafts until the server confirms a settings save.
  $scope.settingsFeatures={userAdjustableClocks:false, userAutoFan:false, userDev:false};
  $scope.userSettingsSaving=false;
  // Pools
  $scope.pools={};
  $scope.options=[];
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

  $scope.bsLogout = function() {
    $http.post('f_logout.php', {}).then(function() {
      $window.location.href = 'login.php';
    }, function() {
      Alertify.log.error('Logout failed');
    });
  };

  // Sync settings
  // Note: not possible to remove settings!
  $scope.sync = function(action,data,alert) {
    action = action || 'settings';
    if (action !== 'pass') data = data || 'load';
    var loading = action !== 'pass' && data === 'load';
    var request = loading
      ? $http.get('f_settings.php?'+encodeURIComponent(action)+'='+encodeURIComponent(angular.toJson('load')))
      : $http.post('f_settings.php', encodeURIComponent(action) + '=' + encodeURIComponent(angular.toJson(data)), {
          headers: {'Content-Type': 'application/x-www-form-urlencoded'}
        });
    return request.success(function(d){
      if(d && d.info){
        angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
      }
      if(action=='settings'){
        if (!bsSettingsResponseApplied(d)) return;
        $scope.settings=angular.copy(d['data']);
        if ($scope.settings) {
          if ($scope.settings.liveMax === undefined) {
            $scope.settings.liveMax = 50;
          }
          if ($scope.settings.userCharts === undefined) {
            $scope.settings.userCharts = true;
          }
          if ($scope.settings.userChartsSaveTo === undefined) {
            $scope.settings.userChartsSaveTo = 'RAM';
          }
          if ($scope.settings.userPersistPoolHistory === undefined) {
            $scope.settings.userPersistPoolHistory = false;
          }
          if ($scope.settings.userPersistFailoverHistory === undefined) {
            $scope.settings.userPersistFailoverHistory = false;
          }
          if ($scope.settings.userPersistClockHistory === undefined) {
            $scope.settings.userPersistClockHistory = false;
          }
          if ($scope.settings.userAdjustableClocks === undefined) {
            $scope.settings.userAdjustableClocks = false;
          }
          if ($scope.settings.userAutoFan === undefined) {
            $scope.settings.userAutoFan = false;
          }
          if ($scope.settings.userAutoFanMin === undefined) {
            $scope.settings.userAutoFanMin = 40;
          }
          if ($scope.settings.userAutoFanTarget === undefined) {
            $scope.settings.userAutoFanTarget = 36;
          }
          if ($scope.settings.userAutoFanFull === undefined) {
            $scope.settings.userAutoFanFull = 50;
          }
          if ($scope.settings.userAutoFanMidTemp === undefined) {
            $scope.settings.userAutoFanMidTemp = bsDefaultAutoFanMidTemp($scope.settings);
          }
          if ($scope.settings.userAutoFanMidFan === undefined) {
            $scope.settings.userAutoFanMidFan = bsDefaultAutoFanMidFan($scope.settings);
          }
          if ($scope.settings.userAutoFanEmergency === undefined) {
            $scope.settings.userAutoFanEmergency = 60;
          }
          if ($scope.settings.userDev === undefined) {
            $scope.settings.userDev = false;
          }
          if ($scope.settings.userDevLogTarget === undefined) {
            $scope.settings.userDevLogTarget = 'RAM';
          }
        }
        $rootScope.settings = $scope.settings;
        bsSaveUserSettingsBaseline();
        if (d.fanRequest) {
          bsFanSavedRequest = {revision:d.fanRequest, deadline:Date.now() + 60000};
          $scope.tick(true);
        }
      }
      else if(action=='pools'){
        $scope.pools=d['data'];
      }
      else if(action=='options'){
        $scope.options=d['data'] || [];
        $scope.$broadcast('bsOptionsLoaded');
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

  function bsUserSettingsInt(value, fallback) {
    if (value === undefined || value === null || value === '') return '';
    var n = parseInt(value, 10);
    return isNaN(n) ? fallback : n;
  }

  function bsUserSettingsBool(value) {
    return !!value;
  }

  function bsFanSettingInt(value, fallback) {
    var n = parseInt(value, 10);
    return isNaN(n) ? fallback : n;
  }

  function bsDefaultAutoFanMidTemp(settings) {
    settings = settings || {};
    var target = bsFanSettingInt(settings.userAutoFanTarget, 36);
    var full = bsFanSettingInt(settings.userAutoFanFull, 50);
    return Math.round(target + ((full - target) / 2));
  }

  function bsDefaultAutoFanMidFan(settings) {
    settings = settings || {};
    var min = bsFanSettingInt(settings.userAutoFanMin, 40);
    return Math.max(min, 75);
  }

  function bsUserSettingsSnapshot(settings) {
    settings = settings || {};
    return {
      liveMax: bsUserSettingsInt(settings.liveMax, 50),
      userTimezone: settings.userTimezone || '',
      userAdjustableClocks: bsUserSettingsBool(settings.userAdjustableClocks),
      userAutoFan: bsUserSettingsBool(settings.userAutoFan),
      userDev: bsUserSettingsBool(settings.userDev),
      userDevLogTarget: settings.userDevLogTarget || 'RAM'
    };
  }

  function bsSettingsResponseApplied(data) {
    return !!(data && data.data && typeof data.data === 'object' && data.settingsSaved !== false);
  }

  function bsUserSettingsForSave() {
    var settings = angular.copy($scope.settings);
    angular.forEach($scope.settingsFeatures, function(value, key) {
      settings[key] = value;
    });
    return settings;
  }

  function bsSaveUserSettingsBaseline() {
    $scope.settingsMaster = angular.copy(bsUserSettingsSnapshot($scope.settings));
    angular.forEach($scope.settingsFeatures, function(value, key) {
      $scope.settingsFeatures[key] = $scope.settingsMaster[key];
    });
  }

  function bsSaveUserSettingsBaselineField(field) {
    var snap = bsUserSettingsSnapshot($scope.settings);
    if (!$scope.settingsMaster) {
      $scope.settingsMaster = angular.copy(snap);
      return;
    }
    if (snap.hasOwnProperty(field)) {
      $scope.settingsMaster[field] = snap[field];
    }
    if ($scope.settingsFeatures.hasOwnProperty(field)) {
      $scope.settingsFeatures[field] = snap[field];
    }
  }

  $scope.bsUserSettingsDirty = function() {
    if (!$scope.settingsMaster || !$scope.settings) return false;
    return !angular.equals(bsUserSettingsSnapshot(bsUserSettingsForSave()), $scope.settingsMaster);
  };

  function bsMarkUserSettingsFormsPristine() {
    $timeout(function() {
      if ($scope.formUser) {
        if ($scope.formUser.$setPristine) $scope.formUser.$setPristine();
        if ($scope.formUser.$setUntouched) $scope.formUser.$setUntouched();
      }
      if ($scope.formUser2) {
        if ($scope.formUser2.$setPristine) $scope.formUser2.$setPristine();
        if ($scope.formUser2.$setUntouched) $scope.formUser2.$setUntouched();
      }
    }, 50);
  }

  $scope.adjustableClocksToggleChanged = function() {
    if (!$scope.settings) return;
    if (!$scope.settingsFeatures.userAdjustableClocks) {
      return;
    }
    $scope.bsConfirm(
      '<p style="text-align:center">Normal uses <b>Index 30</b>, unchanged from the former 400 preset.<br>Nominal MHz follows the firmware display table, not a measured frequency.</p>' +
      '<p style="text-align:center">Clocks above factory speed can increase heat, errors, rejects, instability, or board wear.</p>' +
      '<p class="bs-clock-warn-note" style="text-align:center">First save may flash the STM32F407 bridge chip if stock firmware is detected.<br>Only the application area is written; stock firmware can be restored from Dev Tools.</p>',
      function() {},
      {
        title: 'Adjustable Clock Warning',
        boxClass: 'bs-modal-warn',
        lockBackdrop: true,
        yesLabel: 'Continue at your own risk',
        noLabel: 'Keep disabled',
        onNo: function() {
          $scope.settingsFeatures.userAdjustableClocks = false;
        }
      }
    );
  };

  $scope.settingsFirmware = {checking: false, message: '', error: '', overlay: false, phaseTitle: ''};
  $scope.devFirmware = {status: null, verifying: false, reverting: false, flashingCustom: false, jobRunning: false, flashRuntimeLabel: '~164s'};
  $scope.bsSaveUserSettingsBaselineField = bsSaveUserSettingsBaselineField;
  $scope.devFirmwareRuntime = function(op) {
    var f = $scope.devFirmware || {}, p = $scope.devFwProgress || {};
    var runtimes = f.status && f.status.flash_runtimes;
    var sample = p.op === op && p.runtime ? p.runtime : (runtimes && runtimes[op]);
    if (sample && Number(sample.seconds) >= 1 && Number(sample.seconds) <= 3600) return sample;
    return {seconds: op === 'verify' ? 45 : 164, measured: false};
  };
  $scope.devFirmwareRuntimeLabel = function(op) {
    return '~' + Math.round($scope.devFirmwareRuntime(op).seconds) + 's';
  };
  $scope.devFirmwareTimingText = function() {
    var p = $scope.devFwProgress || {};
    if (!p.op) return '';
    var sample = $scope.devFirmwareRuntime(p.op);
    var text = 'Estimated total: ' + $scope.devFirmwareRuntimeLabel(p.op);
    text += sample.measured ? ' (last successful run on this miner).' : ' (baseline; actual time varies).';
    if (p.elapsed > sample.seconds) text += ' Estimate exceeded; waiting for completion.';
    return text;
  };
  $scope.bsFirmwareResultActions = {reload: false};
  $scope.devFwProgress = {text: '', startMs: 0, elapsed: 0, cancelable: false};
  var bsSettingsFirmwareTimer = null;
  var bsFirmwarePollTimer = null;
  var bsFirmwareScrollTimer = null;
  var bsFirmwareJobDone = null;
  var bsFirmwareJobFail = null;
  $scope.devFwDfuBusy = function () {
    var f = $scope.devFirmware || {};
    return !!(f.verifying || f.reverting || f.flashingCustom || f.jobRunning);
  };
  $scope.devFwBusy = function () {
    var sf = $scope.settingsFirmware || {};
    return !!($scope.devFwDfuBusy() || sf.checking);
  };
  $scope.bsFirmwareRequestReloadAfterResult = function() {
    if (!$scope.bsFirmwareResultActions) {
      $scope.bsFirmwareResultActions = {reload: false};
    }
    $scope.bsFirmwareResultActions.reload = true;
  };
  $scope.bsFirmwareClearReloadAfterResult = function() {
    if (!$scope.bsFirmwareResultActions) {
      $scope.bsFirmwareResultActions = {reload: false};
      return;
    }
    $scope.bsFirmwareResultActions.reload = false;
  };
  $scope.bsFirmwareShouldReloadAfterResult = function(result) {
    return !!($scope.bsFirmwareResultActions &&
      $scope.bsFirmwareResultActions.reload &&
      result && result.ok &&
      (result.op === 'revert' || result.op === 'flash-custom'));
  };
  $scope.settingsFirmwareBusy = function() {
    var sf = $scope.settingsFirmware || {};
    var dfuBusy = (typeof $scope.devFwDfuBusy === 'function') ? $scope.devFwDfuBusy() : false;
    return !!($scope.userSettingsSaving || sf.checking || dfuBusy);
  };
  $scope.settingsFirmwareProgressTitle = function() {
    var sf = $scope.settingsFirmware || {};
    if (sf.overlay && sf.phaseTitle) return sf.phaseTitle;
    var f = $scope.devFirmware || {};
    if (sf.checking) return 'Checking Bridge Chip';
    if ($scope.devFwProgress && $scope.devFwProgress.phaseTitle) return $scope.devFwProgress.phaseTitle;
    if (f.verifying) return 'DFU verify in progress';
    if (f.flashingCustom) return 'DFU flash in progress';
    if (f.reverting) return 'DFU flash in progress';
    return 'Firmware operation in progress';
  };
  $scope.settingsFirmwareProgressText = function() {
    var p = $scope.devFwProgress || {};
    var sf = $scope.settingsFirmware || {};
    return p.text || sf.message || 'starting...';
  };
  function bsHtmlEscape(text) {
    return String(text || '').replace(/[&<>"']/g, function(c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  $scope.bsHtmlEscape = bsHtmlEscape;
  function bsFirmwareTerminalScrollBottom() {
    if (bsFirmwareScrollTimer) $timeout.cancel(bsFirmwareScrollTimer);
    bsFirmwareScrollTimer = $timeout(function() {
      bsFirmwareScrollTimer = null;
      var el = document.querySelector('.bs-modal-overlay .bs-fw-terminal-body');
      if (el) el.scrollTop = el.scrollHeight;
    }, 0, false);
  }
  function bsSettingsFirmwareOverlayUpdate() {
    var sf = $scope.settingsFirmware || {};
    if (!sf.overlay) return;
    var elapsed = ($scope.devFwProgress && $scope.devFwProgress.elapsed) ? $scope.devFwProgress.elapsed : 0;
    var title = $scope.settingsFirmwareProgressTitle();
    var text = $scope.settingsFirmwareProgressText();
    $scope.bsConfirmTitle = title;
    $scope.bsConfirmBoxClass = 'bs-modal-warn bs-modal-firmware-progress';
    $scope.bsConfirmTitleClass = '';
    var canAbort = !!($scope.devFwProgress && $scope.devFwProgress.cancelable);
    $scope.bsConfirmShowCancel = canAbort;
    $scope.bsConfirmShowOk = false;
    $scope.bsConfirmLockBackdrop = true;
    $scope._bsConfirmOnYes = null;
    $scope._bsConfirmOnNo = canAbort ? $scope.devFirmwareAbort : null;
    $scope.bsConfirmNoLabel = canAbort ? 'Abort Read' : 'Cancel';
    $scope.bsConfirmMsg =
      '<div class="bs-fw-terminal">' +
      '<div class="bs-fw-terminal-head"><i class="fa fa-spinner fa-spin"></i> ' +
      bsHtmlEscape(title) + ' — ' + elapsed + 's elapsed · mining may pause, do not power off</div>' +
      ($scope.devFirmwareTimingText() ? '<div class="bs-fw-terminal-timing">' + bsHtmlEscape($scope.devFirmwareTimingText()) + '</div>' : '') +
      '<pre class="bs-fw-terminal-body">' + bsHtmlEscape(text) + '</pre>' +
      '</div>' +
      '<div class="bs-fw-power-warning">DO NOT POWER OFF</div>';
    $scope.bsConfirmShow = true;
    bsFirmwareTerminalScrollBottom();
  }
  function bsSettingsFirmwareOverlayTick() {
    var sf = $scope.settingsFirmware || {};
    if (!sf.overlay) return;
    if ($scope.devFwProgress && $scope.devFwProgress.startMs) {
      $scope.devFwProgress.elapsed = Math.round((Date.now() - $scope.devFwProgress.startMs) / 1000);
    }
    bsSettingsFirmwareOverlayUpdate();
    bsSettingsFirmwareTimer = $timeout(bsSettingsFirmwareOverlayTick, 1000);
  }
  function bsSettingsFirmwareOverlayOpen(message) {
    $scope.settingsFirmware.overlay = true;
    bsSettingsFirmwareOverlayUpdate();
    if (bsSettingsFirmwareTimer) $timeout.cancel(bsSettingsFirmwareTimer);
    bsSettingsFirmwareTimer = $timeout(bsSettingsFirmwareOverlayTick, 1000);
  }
  function bsSettingsFirmwareOverlayClose() {
    if (bsSettingsFirmwareTimer) {
      $timeout.cancel(bsSettingsFirmwareTimer);
      bsSettingsFirmwareTimer = null;
    }
    if (bsFirmwareScrollTimer) {
      $timeout.cancel(bsFirmwareScrollTimer);
      bsFirmwareScrollTimer = null;
    }
    if ($scope.settingsFirmware) {
      $scope.settingsFirmware.overlay = false;
      $scope.settingsFirmware.checking = false;
      $scope.settingsFirmware.phaseTitle = '';
    }
    $scope.bsConfirmShow = false;
    $scope.bsConfirmLockBackdrop = false;
    $scope.bsConfirmBoxClass = '';
    $scope.bsConfirmTitleClass = '';
    $scope.bsConfirmTitle = '';
    $scope.bsConfirmMsg = '';
    $scope.bsConfirmShowCancel = true;
    $scope.bsConfirmShowOk = true;
    $scope.bsConfirmNoLabel = '';
    $scope._bsConfirmOnYes = null;
    $scope._bsConfirmOnNo = null;
  }
  function bsSettingsFirmwareStart(message, checking, title) {
    $scope.settingsFirmware.error = '';
    $scope.settingsFirmware.message = message || '';
    $scope.settingsFirmware.phaseTitle = title || '';
    $scope.settingsFirmware.checking = !!checking;
    $scope.devFwProgress = {text: message || '', startMs: Date.now(), elapsed: 0, cancelable: false};
    bsSettingsFirmwareOverlayOpen(message);
  }
  function bsSettingsFirmwareStop() {
    if ($scope.settingsFirmware) $scope.settingsFirmware.checking = false;
  }
  function bsSettingsFirmwareFail(message) {
    bsSettingsFirmwareStop();
    if ($scope.devFirmware) {
      $scope.devFirmware.verifying = false;
      $scope.devFirmware.flashingCustom = false;
    }
    $scope.settingsFeatures.userAdjustableClocks = false;
    $scope.settingsFirmware.error = message || 'Firmware check failed';
    $scope.settingsFirmware.phaseTitle = '';
    bsSettingsFirmwareOverlayClose();
    $scope.bsAlert('<p>' + bsHtmlEscape($scope.settingsFirmware.error) + '</p>', {
      title: 'Adjustable Clocks Not Enabled',
      boxClass: 'bs-modal-warn',
      yesLabel: 'OK'
    });
    Alertify.log.create('error', $scope.settingsFirmware.error);
  }
  function bsSettingsFirmwareUpdateStatus(d) {
    if (!$scope.devFirmware) $scope.devFirmware = {};
    if (d && d.ok) {
      $scope.devFirmware.status = d;
      $scope.devFirmware.flashRuntimeLabel = d.flash_runtime_label || $scope.devFirmware.flashRuntimeLabel || '~164s';
    }
  }
  function bsSaveAdjustableClockSettingOn(message) {
    bsSettingsFirmwareStop();
    $scope.settingsFeatures.userAdjustableClocks = true;
    $scope.settingsFirmware.phaseTitle = 'Saving Settings';
    $scope.settingsFirmware.message = 'Firmware ready; saving settings...';
    bsSettingsFirmwareOverlayUpdate();
    $scope.userSettingsSaving = true;
    var req = $scope.sync('settings', bsUserSettingsForSave(), 1);
    if (req && req.success) {
      req.success(function(d) {
        $scope.userSettingsSaving = false;
        bsSettingsFirmwareOverlayClose();
        if (!bsSettingsResponseApplied(d)) return;
        bsMarkUserSettingsFormsPristine();
        if (message && $scope.settings.userAdjustableClocks) Alertify.log.create('success', message);
      });
      req.error(function() {
        $scope.userSettingsSaving = false;
        bsSettingsFirmwareOverlayClose();
      });
    }
  }
  function bsWaitForCustomFirmwareThenSave(message, attemptsLeft) {
    attemptsLeft = (attemptsLeft === undefined) ? 4 : attemptsLeft;
    $scope.settingsFirmware.phaseTitle = 'Checking Bridge Chip';
    $scope.settingsFirmware.message = 'Confirming adjustable-clock firmware is active...';
    if (!$scope.settingsFirmware.overlay) {
      bsSettingsFirmwareOverlayOpen($scope.settingsFirmware.message);
    } else {
      bsSettingsFirmwareOverlayUpdate();
    }
    $http.get('f_dev.php?action=firmware_status').success(function(d) {
      bsSettingsFirmwareUpdateStatus(d);
      if (d && d.ok && d.custom_active) {
        bsSaveAdjustableClockSettingOn(message);
        return;
      }
      if (attemptsLeft > 0) {
        $timeout(function() {
          bsWaitForCustomFirmwareThenSave(message, attemptsLeft - 1);
        }, 1000);
        return;
      }
      bsSettingsFirmwareFail('Firmware flash finished, but adjustable-clock firmware was not confirmed active');
    }).error(function() {
      if (attemptsLeft > 0) {
        $timeout(function() {
          bsWaitForCustomFirmwareThenSave(message, attemptsLeft - 1);
        }, 1000);
        return;
      }
      bsSettingsFirmwareFail('firmware status request failed after flash');
    });
  }
  function bsSaveSettingsPlain() {
    $scope.userSettingsSaving = true;
    var req = $scope.sync('settings', bsUserSettingsForSave(), 1);
    if (req && req.success) {
      req.success(function(d) {
        $scope.userSettingsSaving = false;
        if (!bsSettingsResponseApplied(d)) return;
        bsMarkUserSettingsFormsPristine();
      });
      req.error(function() { $scope.userSettingsSaving = false; });
    }
  }
  function bsFirmwareTitleFor(op, phase) {
    if (phase === 'recovery-required') return 'Bridge Recovery Required';
    if (phase === 'restart') return 'Restarting Miner';
    if (op === 'verify') return 'Reading Bridge Chip';
    if (op === 'flash-custom') {
      return phase === 'starting' ? 'Starting Firmware Flash' : 'Flashing Firmware';
    }
    if (op === 'revert') {
      return phase === 'starting' ? 'Starting Stock Firmware Flash' : 'Flashing Stock Firmware';
    }
    return 'Firmware Operation';
  }
  function bsFirmwareSetBusy(op, running) {
    if (!$scope.devFirmware) $scope.devFirmware = {};
    $scope.devFirmware.jobRunning = !!running;
    $scope.devFirmware.jobOp = running ? op : '';
    $scope.devFirmware.verifying = !!(running && op === 'verify');
    $scope.devFirmware.flashingCustom = !!(running && op === 'flash-custom');
    $scope.devFirmware.reverting = !!(running && op === 'revert');
  }
  function bsFirmwareFinishJob(d) {
    var result = d && d.result ? d.result : null;
    var cb = (result && result.ok) ? bsFirmwareJobDone : bsFirmwareJobFail;
    var reloadAfterResult = $scope.bsFirmwareShouldReloadAfterResult(result);
    if (bsFirmwarePollTimer) {
      $timeout.cancel(bsFirmwarePollTimer);
      bsFirmwarePollTimer = null;
    }
    if (bsSettingsFirmwareTimer) {
      $timeout.cancel(bsSettingsFirmwareTimer);
      bsSettingsFirmwareTimer = null;
    }
    if (bsFirmwareScrollTimer) {
      $timeout.cancel(bsFirmwareScrollTimer);
      bsFirmwareScrollTimer = null;
    }
    bsSettingsFirmwareOverlayClose();
    bsFirmwareJobDone = null;
    bsFirmwareJobFail = null;
    bsFirmwareSetBusy((result && result.op) || (d && d.op) || '', false);
    try {
      if (cb) {
        cb(d);
      } else if (result) {
        bsSettingsFirmwareOverlayClose();
        if (result.ok && typeof $scope.devFirmwareStatus === 'function') $scope.devFirmwareStatus();
        if (!result.ok) Alertify.log.create('error', result.message || 'firmware operation failed');
      }
    } finally {
      if (reloadAfterResult) {
        $scope.bsFirmwareClearReloadAfterResult();
        $timeout(function() {
          $window.location.reload(true);
        }, 800);
      } else if (result) {
        $scope.bsFirmwareClearReloadAfterResult();
      }
    }
  }
  function bsFirmwarePollJob() {
    $http.get('f_dev.php?action=firmware_progress').success(function(d) {
      if (!(d && d.ok)) {
        bsFirmwarePollTimer = $timeout(bsFirmwarePollJob, 1500);
        return;
      }
      if (!$scope.devFwProgress) $scope.devFwProgress = {};
      $scope.devFwProgress.text = (typeof d.progress === 'string') ? d.progress : ($scope.devFwProgress.text || '');
      $scope.devFwProgress.cancelable = !!d.cancelable;
      $scope.devFwProgress.abortRequested = !!d.abort_requested;
      $scope.devFwProgress.phase = d.phase || '';
      $scope.devFwProgress.op = d.op || '';
      $scope.devFwProgress.runtime = d.runtime || null;
      $scope.devFwProgress.phaseTitle = bsFirmwareTitleFor(d.op || '', d.phase || '');
      if (typeof d.elapsed === 'number') $scope.devFwProgress.startMs = Date.now() - Math.max(0, d.elapsed) * 1000;
      else if (d.started_at) $scope.devFwProgress.startMs = d.started_at * 1000;
      $scope.devFwProgress.elapsed = Math.round((Date.now() - ($scope.devFwProgress.startMs || Date.now())) / 1000);
      if (d.running) {
        bsFirmwareSetBusy(d.op || '', true);
        $scope.settingsFirmware.overlay = true;
        $scope.settingsFirmware.checking = false;
        $scope.settingsFirmware.phaseTitle = $scope.devFwProgress.phaseTitle;
        bsSettingsFirmwareOverlayUpdate();
        bsFirmwarePollTimer = $timeout(bsFirmwarePollJob, 1500);
        return;
      }
      if (d.result) {
        $scope.devFwProgress.cancelable = false;
        $scope.devFwProgress.text = (typeof d.progress === 'string') ? d.progress : $scope.devFwProgress.text;
        bsSettingsFirmwareOverlayClose();
        bsFirmwareFinishJob(d);
        return;
      }
      bsFirmwareSetBusy('', false);
    }).error(function() {
      bsFirmwarePollTimer = $timeout(bsFirmwarePollJob, 2000);
    });
  }
  function bsFirmwareWaitForJob(onDone, onFail) {
    bsFirmwareJobDone = onDone || null;
    bsFirmwareJobFail = onFail || null;
    if (bsFirmwarePollTimer) $timeout.cancel(bsFirmwarePollTimer);
    bsFirmwarePollJob();
  }
  function bsFirmwareStartJob(op, body, startMessage, onDone, onFail) {
    var action = (op === 'verify') ? 'firmware_verify' : (op === 'revert' ? 'firmware_revert' : 'firmware_flash_custom');
    bsFirmwareSetBusy(op, true);
    $scope.devFwProgress = {op: op, text: startMessage || '', startMs: Date.now(), elapsed: 0, cancelable: false, phaseTitle: bsFirmwareTitleFor(op, 'starting')};
    $scope.settingsFirmware.phaseTitle = $scope.devFwProgress.phaseTitle;
    $scope.settingsFirmware.message = startMessage || '';
    $scope.settingsFirmware.overlay = true;
    bsSettingsFirmwareOverlayOpen(startMessage);
    $http.post('f_dev.php?action=' + action, body || {}).success(function(d) {
      if (d && d.ok && d.started) {
        bsFirmwareWaitForJob(onDone, onFail);
        return;
      }
      if (d && d.error === 'a firmware operation is already in progress') {
        bsFirmwareWaitForJob(onDone, onFail);
        return;
      }
      bsFirmwareSetBusy(op, false);
      if (onFail) onFail({result: {ok: false, op: op, message: (d && d.error) || 'firmware job did not start'}, progress: (d && d.raw) || ''});
    }).error(function() {
      bsFirmwareSetBusy(op, false);
      if (onFail) onFail({result: {ok: false, op: op, message: 'firmware job request failed'}, progress: ''});
    });
  }
  $scope.devFirmwareAbort = function () {
    if (!$scope.devFwProgress || !$scope.devFwProgress.cancelable) return;
    $scope.devFwProgress.cancelable = false;
    $scope.devFwProgress.text = ($scope.devFwProgress.text || '') + "\nAbort requested; waiting for safe read cleanup...";
    bsSettingsFirmwareOverlayOpen();
    $http.post('f_dev.php?action=firmware_abort', {abort: true}).success(function (d) {
      if (!(d && d.ok)) {
        $scope.devFwProgress.text = ($scope.devFwProgress.text || '') + "\nAbort request failed: " + ((d && d.error) || 'not abortable');
      }
      bsSettingsFirmwareOverlayUpdate();
      bsFirmwarePollJob();
    }).error(function () {
      $scope.devFwProgress.text = ($scope.devFwProgress.text || '') + "\nAbort request failed.";
      bsSettingsFirmwareOverlayUpdate();
      bsFirmwarePollJob();
    });
  };
  function bsFirmwareResumeJob() {
    $http.get('f_dev.php?action=firmware_progress').success(function (d) {
      if (!(d && d.ok && d.running)) return;
      if (bsFirmwareJobDone || bsFirmwareJobFail || bsFirmwarePollTimer ||
          ($scope.devFirmware && $scope.devFirmware.jobRunning)) {
        return;
      }
      if (!$scope.devFwProgress) $scope.devFwProgress = {};
      $scope.devFwProgress.text = d.progress || 'Firmware operation in progress...';
      $scope.devFwProgress.op = d.op || '';
      $scope.devFwProgress.runtime = d.runtime || null;
      $scope.devFwProgress.startMs = typeof d.elapsed === 'number' ? Date.now() - Math.max(0, d.elapsed) * 1000 : (d.started_at ? d.started_at * 1000 : Date.now());
      $scope.devFwProgress.cancelable = !!d.cancelable;
      $scope.devFwProgress.phaseTitle = bsFirmwareTitleFor(d.op || '', d.phase || '');
      $scope.settingsFirmware.overlay = true;
      $scope.settingsFirmware.phaseTitle = $scope.devFwProgress.phaseTitle;
      bsFirmwareSetBusy(d.op || '', true);
      bsSettingsFirmwareOverlayOpen();
      bsFirmwareWaitForJob(null, null);
    });
  }
  $scope.devFirmwareResumeJob = bsFirmwareResumeJob;
  $scope.bsFirmwarePollJob = bsFirmwarePollJob;
  $scope.bsFirmwareStartJob = bsFirmwareStartJob;
  $scope.bsSettingsFirmwareOverlayClose = bsSettingsFirmwareOverlayClose;
  $timeout(bsFirmwareResumeJob, 1000);
  function bsSettingsFirmwareVerifyThenEnable() {
    if (!$scope.devFirmware) $scope.devFirmware = {};
    $scope.devFirmware.error = '';
    bsFirmwareStartJob('verify', {}, 'Reading bridge-chip firmware...', function(d) {
      var result = d && d.result ? d.result : {};
      if (typeof $scope.devFirmwareStatus === 'function') $scope.devFirmwareStatus();
      var identity = ((result.identity || '') + '').toUpperCase();
      if (identity.indexOf('CUSTOM') !== -1) {
        bsSaveAdjustableClockSettingOn('Adjustable clock firmware is already active');
      } else if (identity === 'STOCK') {
        bsSettingsFirmwareFlashCustom();
      } else {
        bsSettingsFirmwareFail('Firmware identity is unknown; use Dev Tools to verify or restore firmware before enabling Adjustable Clocks');
      }
    }, function(d) {
      var result = d && d.result ? d.result : {};
      bsSettingsFirmwareFail(result.message || 'firmware verify failed');
    });
  }
  function bsSettingsFirmwareFlashCustom() {
    if (!$scope.devFirmware) $scope.devFirmware = {};
    $scope.devFirmware.enableError = '';
    $scope.devFirmware.enableOutput = '';
    bsFirmwareStartJob('flash-custom', {confirm: 'ENABLE'}, 'Flashing adjustable-clock firmware...', function(d) {
      var result = d && d.result ? d.result : {};
      if (result.elapsed) $scope.devFirmware.flashRuntimeLabel = '~' + result.elapsed + 's';
      $scope.settingsFeatures.userAdjustableClocks = true;
      bsWaitForCustomFirmwareThenSave('Adjustable clock firmware is active');
    }, function(d) {
      var result = d && d.result ? d.result : {};
      bsSettingsFirmwareFail(result.message || 'adjustable-clock firmware flash failed');
    });
  }
  $scope.saveUserSettings = function() {
    if (!$scope.settings || $scope.settingsFirmwareBusy()) return;
    if (!$scope.settingsFeatures.userAdjustableClocks) {
      bsSaveSettingsPlain();
      return;
    }
    bsSettingsFirmwareStart('Checking bridge-chip firmware state...', true, 'Checking Bridge Chip');
    $http.get('f_dev.php?action=firmware_status').success(function(d) {
      bsSettingsFirmwareStop();
      if (!(d && d.ok)) {
        bsSettingsFirmwareFail((d && d.error) || 'firmware status check failed');
        return;
      }
      bsSettingsFirmwareUpdateStatus(d);
      if (!d.custom_present) {
        bsSettingsFirmwareFail('adjustable-clock firmware image is not staged');
        return;
      }
      if (!d.encoder_ready) {
        bsSettingsFirmwareFail('corrected sgminer clock encoder is not deployed');
        return;
      }
      if (d.custom_active) {
        bsSaveAdjustableClockSettingOn('Adjustable clock firmware is already active');
        return;
      }
      var identity = d.last_identity && d.last_identity.identity ? (d.last_identity.identity + '').toUpperCase() : '';
      if (identity === 'STOCK') {
        bsSettingsFirmwareFlashCustom();
      } else {
        bsSettingsFirmwareVerifyThenEnable();
      }
    }).error(function() {
      bsSettingsFirmwareFail('firmware status request failed');
    });
  };

  $scope.autoFanToggleChanged = function() {
    if (!$scope.settings) return;
    if (!$scope.settingsFeatures.userAutoFan) {
      return;
    }
    $scope.bsConfirm(
      '<p>Auto Fan Control adjusts the BK-B fan from live board temperatures.</p>' +
      '<p>Minimum auto fan: <b>20%</b>. Default: <b>40%</b>.<br>Verify that the fan keeps turning at your selected minimum.</p>',
      function() {
        if ($scope.settings.userAutoFanMin < 20) $scope.settings.userAutoFanMin = 20;
      },
      {
        title: 'Auto Fan Control Warning',
        boxClass: 'bs-modal-warn',
        lockBackdrop: true,
        yesLabel: 'Enable Auto Fan',
        noLabel: 'Keep disabled',
        onNo: function() {
          $scope.settingsFeatures.userAutoFan = false;
        }
      }
    );
  };

  // ============================================================================
  // Blakestream-GaintB: in-page confirm modal (replaces browser confirm/alert)
  // Bound to overlay in index.php. Use bsConfirm(msg, onYes [, opts]) anywhere.
  // ============================================================================
  $scope.bsConfirmShow = false;
  $scope.bsConfirmMsg = '';
  $scope.bsConfirmTitle = '';
  $scope.bsConfirmBoxClass = '';
  $scope.bsConfirmTitleClass = '';
  $scope.bsConfirmYesLabel = '';
  $scope.bsConfirmNoLabel = '';
  $scope.bsConfirmShowCancel = true;
  $scope.bsConfirmShowOk = true;
  $scope.bsConfirmLockBackdrop = false;
  $scope._bsConfirmOnYes = null;
  $scope._bsConfirmOnNo = null;

  $scope.bsConfirm = function(msg, onYes, opts) {
    opts = opts || {};
    $scope.bsConfirmMsg = msg;
    $scope.bsConfirmTitle = opts.title || '';
    $scope.bsConfirmBoxClass = opts.boxClass || '';
    $scope.bsConfirmTitleClass = opts.titleClass || '';
    $scope.bsConfirmYesLabel = opts.yesLabel || 'OK';
    $scope.bsConfirmNoLabel = opts.noLabel || 'Cancel';
    $scope.bsConfirmShowCancel = (opts.showCancel !== false);
    $scope.bsConfirmShowOk = (opts.showOk !== false);
    $scope.bsConfirmLockBackdrop = (opts.lockBackdrop === true);
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
    $scope.bsConfirmLockBackdrop = false;
    $scope.bsConfirmBoxClass = '';
    $scope.bsConfirmTitleClass = '';
    $scope.bsConfirmShowOk = true;
    var cb = $scope._bsConfirmOnYes;
    $scope._bsConfirmOnYes = null;
    $scope._bsConfirmOnNo = null;
    if (typeof cb === 'function') cb();
  };
  $scope.bsConfirmBackdrop = function() {
    if ($scope.bsConfirmLockBackdrop) return;
    $scope.bsConfirmCancel();
  };
  $scope.bsConfirmCancel = function() {
    $scope.bsConfirmShow = false;
    $scope.bsConfirmLockBackdrop = false;
    $scope.bsConfirmBoxClass = '';
    $scope.bsConfirmTitleClass = '';
    $scope.bsConfirmShowOk = true;
    var cb = $scope._bsConfirmOnNo;
    $scope._bsConfirmOnYes = null;
    $scope._bsConfirmOnNo = null;
    if (typeof cb === 'function') cb();
  };

  // Footer system stats (memory, load).
  $scope.bsSysStats = {mem_pct: 0, mem_used_mb: 0, mem_total_mb: 0};
  $scope.bsConnectionIcon = function() {
    var type = ($scope.bsSysStats.connection || {}).type;
    return {wifi: 'fa-wifi', ethernet: 'bs-ethernet-icon', other: 'fa-exchange', none: 'fa-unlink'}[type] || 'fa-question-circle';
  };
  $scope.bsConnectionLabel = function() {
    var connection = $scope.bsSysStats.connection || {};
    var label = {wifi: 'Wi-Fi', ethernet: 'Ethernet', other: 'Network', none: 'No default network route'}[connection.type];
    return label ? label + (connection.interface ? ': ' + connection.interface : '') : 'Connection status unavailable';
  };
  $scope.bsLoadSysStats = function() {
    $http.get('f_blakestream.php?action=sysstats').success(function(d) {
      if (d) $scope.bsSysStats = d;
    }).error(function() { $scope.bsSysStats.connection = {type: 'unknown'}; });
  };
  $scope.bsLoadSysStats();

  // Timezone: push settings.userTimezone into BlakestreamTZ so charts and
  // bsTzDate-filtered timestamps re-render in the chosen zone.
  // bsTzNonce is passed as the third argument to the bsTzDate filter so the
  // filter re-runs (and cells re-render) when the timezone or UTC toggle
  // changes. Without this, the filter is cached on input identity and stale.
  $scope.bsTzNonce = 0;
  $scope.$watch('settings.userTimezone', function(tz) {
    if (typeof BlakestreamTZ === 'undefined' || !tz) return;
    BlakestreamTZ.setZone(tz);
    $scope.bsTzNonce++;
    $scope.$broadcast('bsTzChanged');
  });
  $scope.$watch('settings.userShowSystemTime', function() {
    $scope.bsTzNonce++;
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
      if (!d || !d.boards || d.boards.length === 0) return;
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
        b.accepted_samples = srv.accepted_samples || [];
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

  // Synthesise bsHashrate.boards from the live status.devs payload so the
  // Per-Board Hashrate cards render the moment sgminer reports devices,
  // instead of waiting up to 60s for the next cron-driven recorder sample.
  // The next bsLoadHashrateHistory poll merges real samples into these
  // placeholder cards (matched by id) without losing card identity.
  var bsSyncBoardsFromDevs = function() {
    if (!bsChartsEnabled()) return;
    if (!$scope.status || !$scope.status.devs || $scope.status.devs.length === 0) return;
    if ($scope.bsHashrate.boards && $scope.bsHashrate.boards.length > 0) return;
    var next = [];
    angular.forEach($scope.status.devs, function(d) {
      var bid = parseInt(d.ID, 10);
      next.push({
        id: bid,
        enabled: d.Enabled === 'Y',
        current_mhs5s: d.MHS5s || 0,
        pool_name: '',
        samples: [],
        range: bsHashrateRanges[bid] || 'hour'
      });
    });
    $scope.bsHashrate.boards = next;
  };

  // Watch for sgminer coming up (post-restart). When minerUp flips to true,
  // synthesise cards immediately and refetch history at +5s and +30s so the
  // first recorder sample is picked up without waiting for the regular poll.
  $scope.$watch('status.minerUp', function(newVal, oldVal) {
    if (newVal === true) {
      bsSyncBoardsFromDevs();
      if (oldVal === false) {
        $timeout(function() { $scope.bsLoadHashrateHistory(); }, 5000);
        $timeout(function() { $scope.bsLoadHashrateHistory(); }, 30000);
      }
    }
  });

  // Charts toggle is now purely visual — the ng-if on the chart cards
  // (status.html) hides them when userCharts is false. The hashrate poller
  // keeps running regardless so re-enabling Charts shows live data with no
  // gaps. No-op kept on ng-change so the form-dirty pattern still fires.
  $scope.bsToggleCharts = function() {};

  // Sync settings
  $scope.sync('settings')

  var bsIntOr = function(value, fallback) {
    var parsed = parseInt(value, 10);
    return isNaN(parsed) ? fallback : parsed;
  };

  var bsPositiveIntOr = function(value, fallback) {
    var parsed = bsIntOr(value, fallback);
    return parsed > 0 ? parsed : fallback;
  };

  $scope.bsAutoFanStatusVisible = function() {
    return !!($scope.settings && $scope.settings.userAutoFan);
  };

  var bsFanSavedRequest = null;
  $scope.bsReceiveFanStatus = function(fanControl) {
    if (!fanControl || !bsFanSavedRequest || fanControl.request_revision !== bsFanSavedRequest.revision) return;
    if (fanControl.pending === false) {
      bsFanSavedRequest = null;
    } else if (fanControl.pending === true) {
      var remaining = Math.max(0, Number(fanControl.wait_seconds) || 0) * 1000;
      bsFanSavedRequest.deadline = Math.min(bsFanSavedRequest.deadline, Date.now() + remaining);
    }
  };
  $scope.bsAutoFanPending = function() {
    return !!bsFanSavedRequest;
  };
  $scope.bsAutoFanCountdown = function() {
    var deadline = bsFanSavedRequest ? bsFanSavedRequest.deadline : 0;
    return Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
  };

  $scope.bsAutoFanMin = function() {
    var fanControl = $scope.status.fanControl || {};
    return bsPositiveIntOr(fanControl.min_fan, bsPositiveIntOr($scope.settings.userAutoFanMin, 40));
  };

  $scope.bsAutoFanActual = function() {
    var fanControl = $scope.status.fanControl || {};
    return bsPositiveIntOr(fanControl.fan, $scope.bsAutoFanMin());
  };

  $scope.bsAutoFanActualRaised = function() {
    return $scope.bsAutoFanActual() > $scope.bsAutoFanMin();
  };

  var bsFanFloatOrZero = function(value) {
    var parsed = parseFloat(value);
    return isNaN(parsed) ? 0 : parsed;
  };

  var bsFanTemp = function(value) {
    var temp = bsFanFloatOrZero(value);
    if (!temp) return '';
    return (Math.round(temp * 10) / 10) + ' C';
  };

  $scope.bsAutoFanState = function() {
    var fanControl = $scope.status.fanControl || {};
    var status = (fanControl.status || '').toLowerCase();
    var actual = $scope.bsAutoFanActual();
    var min = $scope.bsAutoFanMin();
    var maxTemp = bsFanFloatOrZero(fanControl.max_temp);
    var fullTemp = bsFanFloatOrZero(fanControl.full_temp);
    var emergencyTemp = bsFanFloatOrZero(fanControl.emergency_temp);
    if (status === 'emergency' || (emergencyTemp && maxTemp >= emergencyTemp)) return 'emergency';
    if ($scope.bsAutoFanPending() || status === 'starting') return 'ramping';
    if (actual >= 100 || (fullTemp && maxTemp >= fullTemp)) return 'full';
    if (actual > min) return 'ramping';
    return 'normal';
  };

  $scope.bsAutoFanStatusClass = function() {
    return 'bs-auto-fan-state-' + $scope.bsAutoFanState();
  };

  $scope.bsAutoFanLabel = function() {
    var fanControl = $scope.status.fanControl || {};
    if ($scope.bsAutoFanState() === 'emergency') return 'Emergency Fan';
    if ($scope.bsAutoFanPending() || fanControl.status === 'starting') return 'Updating Fan';
    if ((fanControl.status || '').toLowerCase() === 'error') return 'Fan Error';
    return 'Auto Fan';
  };

  $scope.bsAutoFanShowMin = function() {
    return !$scope.bsAutoFanPending() && $scope.bsAutoFanState() !== 'emergency';
  };

  $scope.bsAutoFanTooltip = function() {
    var fanControl = $scope.status.fanControl || {};
    var parts = ['Actual ' + $scope.bsAutoFanActual() + '%'];
    parts.push('min ' + $scope.bsAutoFanMin() + '%');
    var hottest = bsFanTemp(fanControl.max_temp);
    var target = bsFanTemp(fanControl.target_temp);
    var mid = bsFanTemp(fanControl.mid_temp);
    var midFan = bsPositiveIntOr(fanControl.mid_fan, 0);
    var full = bsFanTemp(fanControl.full_temp);
    var emergency = bsFanTemp(fanControl.emergency_temp);
    if (hottest) parts.push('hottest ' + hottest);
    if (target) parts.push('target ' + target);
    if (mid && midFan) parts.push('mid ' + midFan + '% at ' + mid);
    if (full) parts.push('full fan ' + full);
    if (emergency) parts.push('emergency ' + emergency);
    if (fanControl.reason) parts.push(fanControl.reason);
    return parts.join(' - ');
  };

  $scope.bsHeaderHashrate = function(total) {
    total = total || {};
    var valid = function(value) { return typeof value === 'number' && isFinite(value) && value >= 0; };
    if (total.Accepted1hFullWindow === true && total.Accepted1hMeasuring === false && valid(total.Accepted1h)) {
      return total.Accepted1h;
    }
    if (total.Accepted15mMeasuring === false && valid(total.Accepted15m)) return total.Accepted15m;
    return valid(total.EstimatedHashrate) ? total.EstimatedHashrate : null;
  };

  // Get status and save in scope
  var bsStatusRequestSerial = 0;
  var bsStatusAppliedSerial = 0;
  $scope.tick = function(once,all) {
    var serial = ++bsStatusRequestSerial;
    $http.get('f_status.php?'+($scope.settings.userDeveloper?'dev=1&':'')+(all||$scope.status.extra?'all=1':'')).success(function(d){
      if (serial < bsStatusAppliedSerial) return;
      bsStatusAppliedSerial = serial;
      if(d.info){
        angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
      }
      // Update status
      angular.forEach(d.status, function(v,k) {$scope.status[k]=v;});
      $scope.bsObserveMinerSession(d.status && d.status.minerSession);
      $scope.bsReceiveFanStatus(d.status && d.status.fanControl);
      if (d.status && d.status.devs) $scope.bsStatusReceivedAt = Date.now();
      // Title
      var headerRate = $scope.bsHeaderHashrate($scope.status.dtot);
      $scope.title=$scope.status.minerDown?'Miner DOWN -':'['+(headerRate === null ? '\u2014' : $filter('mhs')(headerRate)+'h')+'] ['+$scope.status.dtot.devices+' dev]';
      // Live Graphs
      $scope.live.push([Date.now(),1000000*$scope.status.dtot.MHS5s]);
      // Stop requesting extra data
      $scope.status.extra=false;
    })
    .error(function(){
      if (serial < bsStatusAppliedSerial) return;
      bsStatusAppliedSerial = serial;
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
    if (b === a) return;
    Alertify.log.info('Automatic refresh rate '+(b?'en':'dis')+'abled');
  });
  
  $scope.$watch('intervalMax', function(b,a) {
    if (b === a) return;
    Alertify.log.info('Refresh rate is now '+b);
    if($scope.counter>b){
      $scope.counter=0;
    }
    $scope.status.extra=true;
  });

  $scope.$watch('status.minerDown', function(b,a) {
    if (b === a) return;
    if(b){
      $scope.upLast=Date.now();
      if (a === false) Alertify.log.error('Miner seems down');
    }
    else{
      $scope.downLast=Date.now();
      if (a === true) Alertify.log.success('Miner is up!');
    }
    $scope.interval=1;
    $scope.counter=0;
    $scope.status.extra=true;
  });
})


.controller('CtrlStatus', function($scope,$http,$timeout) {
  $scope.status.extra=true;
  $scope.num=0;

  $scope.bsDeviceClockView = (function() {
    try { return localStorage.getItem('bsDeviceClockView') === 'clock_index' ? 'clock_index' : 'clock'; }
    catch (e) { return 'clock'; }
  })();
  $scope.bsToggleDeviceClockView = function() {
    $scope.bsDeviceClockView = $scope.bsDeviceClockView === 'clock_index' ? 'clock' : 'clock_index';
    try { localStorage.setItem('bsDeviceClockView', $scope.bsDeviceClockView); }
    catch (e) { /* best-effort */ }
  };
  $scope.bsDeviceClockText = function(device) {
    device = device || {};
    var raw = device.SentClockIndex;
    var valid = (typeof raw === 'number' || typeof raw === 'string') &&
      /^(?:[89]|[12][0-9]|3[0-2])$/.test(String(raw));
    if ($scope.bsDeviceClockView === 'clock_index') return valid ? String(raw) : '\u2014';
    if (device.ExperimentalClock) {
      if (!valid) return '\u2014';
      if (Number(raw) === 30) return '400MHz';
      var mhz = device.EstimatedClockMHz;
      return typeof mhz === 'number' && isFinite(mhz) && mhz > 0
        ? Math.round(mhz) + 'MHz' : '\u2014';
    }
    var clock = device.Clock;
    return (typeof clock === 'number' || typeof clock === 'string') &&
      /^[1-9][0-9]*$/.test(String(clock)) ? clock + 'MHz' : '\u2014';
  };

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
  $scope.bsPrimaryEditorOpen = {};
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
  $scope.bsShowPrimaryEditor = function(boardId, event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    $scope.bsPrimaryEditorOpen[String(boardId)] = true;
    $scope.bsSetAssignmentsView('primary');
  };
  $scope.bsPrimaryEditorVisible = function(boardId) {
    return !!($scope.bsPrimaryEditorOpen && $scope.bsPrimaryEditorOpen[String(boardId)]);
  };
  $scope.bsClearPrimaryEditors = function() {
    $scope.bsPrimaryEditorOpen = {};
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

  $scope.bsPoolById = function(poolId) {
    if (!poolId || !$scope.savedPools || !$scope.savedPools.categories) return null;
    for (var c = 0; c < $scope.savedPools.categories.length; c++) {
      var pools = $scope.savedPools.categories[c].pools || [];
      for (var p = 0; p < pools.length; p++) {
        if (pools[p].id === poolId) return pools[p];
      }
    }
    return null;
  };

  $scope.bsPoolEndpointKey = function(pool) {
    var url = pool && pool.url ? String(pool.url) : '';
    if (!url) return '';
    url = url.trim();
    url = url.replace(/^https?:\/\//i, '');
    url = url.replace(/^quota:\d+;/i, '');
    url = url.replace(/^stratum\+(tcp|ssl|tls):\/\//i, '');
    url = url.split('/')[0];
    if (url.indexOf('@') >= 0) url = url.split('@').pop();
    return url.replace(/\/+$/g, '').toLowerCase();
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
    // Look up primary's algo and endpoint. Failover must be a different
    // stratum endpoint, even if username/password differ.
    var primaryAlgo = null;
    var primaryEndpoint = '';
    var primaryPool = $scope.bsPoolById(primary);
    if (primaryPool) {
      primaryAlgo = primaryPool.algo;
      primaryEndpoint = $scope.bsPoolEndpointKey(primaryPool);
    }
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
      if (inCat[i].id !== primary &&
          inCat[i].algo === primaryAlgo &&
          (!primaryEndpoint || $scope.bsPoolEndpointKey(inCat[i]) !== primaryEndpoint)) {
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

  // True if any board has currently failed-over and is mining on this pool
  // (i.e. this pool is the *destination* of an active failover, not the
  // dead primary). Used to highlight the row yellow in the Pools table.
  $scope.bsPoolIsActiveFailover = function(poolNo) {
    var devs = $scope.status && $scope.status.devs;
    if (!devs) return false;
    for (var i = 0; i < devs.length; i++) {
      var d = devs[i];
      if (d && d.FailoverActive && d.EffectivePool === poolNo) return true;
    }
    return false;
  };

  // Pools-table filter predicate. Keep a pool visible if ANY of:
  //   - some board is currently routing to it (EffectivePool === POOL), OR
  //   - it's been administratively disabled (Status === 'Disabled') so the
  //     user can still see + re-enable it greyed out.
  // Hides backup pools that sgminer keeps stratum-subscribed (under
  // load-balance) but no board is actually consuming.
  $scope.bsPoolIsCurrentlyServing = function(p) {
    if (!p) return false;
    if (p.Status === 'Disabled') return true;
    var devs = $scope.status && $scope.status.devs;
    if (!devs) return false;
    for (var i = 0; i < devs.length; i++) {
      if (devs[i] && devs[i].EffectivePool === p.POOL) return true;
    }
    return false;
  };

  // Build the per-row list for the Pools table. A single physical pool may
  // appear TWICE — once as a board's primary (normal styling) and once as a
  // failover destination for a different board (yellow). Disabled pools
  // appear once with the muted/grey styling. This separates roles so the
  // user can see at a glance "AT1 is serving as both primary and failover".
  //
  // Keep this as cached scope data, not a function called by ng-repeat.
  // Returning fresh row objects during digest makes Angular's collection
  // watcher think the list changed forever, causing "$digest iterations
  // reached" on the main page.
  $scope.bsPoolRows = [];
  $scope.bsAllPoolsDisabled = false;
  $scope.bsRebuildPoolDisplayRows = function() {
    var pools = $scope.status && $scope.status.pools;
    var devs  = $scope.status && $scope.status.devs;
    if (!pools) {
      $scope.bsPoolRows = [];
      $scope.bsAllPoolsDisabled = false;
      return;
    }
    var rows = [];
    for (var i = 0; i < pools.length; i++) {
      var p = pools[i];
      if (!p) continue;
      if (p.Status === 'Disabled') {
        // Show the disabled row only when at least one of this pool's
        // primary-assigned boards is currently orphaned (EffectivePool < 0).
        // If failover absorbed every affected board, the row is just clutter.
        var orphans = [];
        if (devs) {
          for (var k = 0; k < devs.length; k++) {
            var d2 = devs[k];
            if (d2 && d2.PrimaryPool === p.POOL && (d2.EffectivePool === undefined || d2.EffectivePool < 0)) {
              orphans.push(d2.ID);
            }
          }
        }
        if (orphans.length > 0) rows.push({p: p, role: 'disabled', boards: orphans, key: p.POOL + ':disabled'});
        continue;
      }
      var primaryBoards = [];
      var failoverBoards = [];
      if (devs) {
        for (var j = 0; j < devs.length; j++) {
          var d = devs[j];
          if (!d || d.EffectivePool !== p.POOL) continue;
          if (d.FailoverActive) failoverBoards.push(d.ID);
          else primaryBoards.push(d.ID);
        }
      }
      if (primaryBoards.length)  rows.push({p: p, role: 'primary',  boards: primaryBoards, key: p.POOL + ':primary'});
      if (failoverBoards.length) rows.push({p: p, role: 'failover', boards: failoverBoards, key: p.POOL + ':failover'});
    }
    // Stable order by smallest board ID in the row, so bd0's row is always
    // first, bd1's next, etc. — regardless of which pool number is actually
    // hosting that board. Disabled (no-board) rows sink to the bottom.
    rows.sort(function (a, b) {
      var aDis = a.role === 'disabled', bDis = b.role === 'disabled';
      if (aDis && !bDis) return 1;
      if (bDis && !aDis) return -1;
      var aMin = (a.boards && a.boards.length) ? Math.min.apply(null, a.boards) : 999;
      var bMin = (b.boards && b.boards.length) ? Math.min.apply(null, b.boards) : 999;
      return aMin - bMin;
    });
    $scope.bsPoolRows = rows;
    $scope.bsAllPoolsDisabled = rows.length > 0;
    for (var r = 0; r < rows.length; r++) {
      if (rows[r].role !== 'disabled') {
        $scope.bsAllPoolsDisabled = false;
        break;
      }
    }
  };
  $scope.$watch('status.pools', function() {
    $scope.bsRebuildPoolDisplayRows();
  });
  $scope.$watch('status.devs', function() {
    $scope.bsRebuildPoolDisplayRows();
  });

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

  $scope.bsBoardExperimentalClockIndex = function(boardId) {
    var devs = ($scope.status && $scope.status.devs) || [];
    for (var i = 0; i < devs.length; i++) {
      if (devs[i] && devs[i].ID === Number(boardId)) return !!devs[i].ExperimentalClock;
    }
    return false;
  };

  $scope.bsBoardHashrate = function(boardId) {
    if (!$scope.status || !$scope.status.minerUp) return 0;
    var devs = ($scope.status && $scope.status.devs) || [];
    for (var i = 0; i < devs.length; i++) {
      var dev = devs[i];
      if (!dev || dev.ID !== Number(boardId)) continue;
      if (dev.Enabled === 'N' || (dev.EffectivePool != null && dev.EffectivePool < 0)) return 0;
      var rate = dev.EstimatedHashrate;
      return typeof rate === 'number' && isFinite(rate) && rate >= 0 ? rate : null;
    }
    return null;
  };

  $scope.bsAcceptedCoverage = function(device, window) {
    var seconds = device && device['Accepted' + window + 'Seconds'];
    var target = window === '15m' ? 15 : 60;
    if (!seconds) return 'Waiting for accepted work';
    if (seconds >= target * 60) return '';
    return (seconds / 60).toFixed(1) + ' / ' + target + ' min sampled (partial window)';
  };

  // Live effective-pool name for a board, looked up via the live status.devs
  // EffectivePool number → status.pools[i].URL → saved-pool name. Used in
  // the per-board hashrate card so the displayed pool flips to the failover
  // pool the moment sgminer routes a board there, instead of waiting up to
  // 60s for the next recorder sample.
  $scope.bsBoardCurrentPoolName = function(boardId) {
    var devs = $scope.status && $scope.status.devs;
    var pools = $scope.status && $scope.status.pools;
    if (!devs || !pools) return null;
    var bid = parseInt(boardId, 10);
    var dev = null;
    for (var i = 0; i < devs.length; i++) {
      if (devs[i] && devs[i].ID === bid) { dev = devs[i]; break; }
    }
    if (!dev) return null;
    var effNo = dev.EffectivePool;
    if (effNo === undefined || effNo === null || effNo < 0) return null;
    var pool = null;
    for (var j = 0; j < pools.length; j++) {
      if (pools[j] && pools[j].POOL === effNo) { pool = pools[j]; break; }
    }
    if (!pool) return null;
    // Strip the "quota:N;" / "http://" prefixes sgminer adds to the URL,
    // then look up by cleaned URL in savedPools for the user-friendly name.
    var url = String(pool.URL || '').replace(/^https?:\/\//, '').replace(/^quota:\d+;/, '');
    if ($scope.savedPools && $scope.savedPools.categories) {
      for (var c = 0; c < $scope.savedPools.categories.length; c++) {
        var cat = $scope.savedPools.categories[c];
        var ps = (cat && cat.pools) || [];
        for (var k = 0; k < ps.length; k++) {
          if (ps[k] && ps[k].url === url) return ps[k].name || url;
        }
      }
    }
    return pool.Name || url;
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
    var sameEndpoint = function(primaryId, failoverId) {
      var primaryPool = $scope.bsPoolById(primaryId);
      var failoverPool = $scope.bsPoolById(failoverId);
      var primaryEndpoint = $scope.bsPoolEndpointKey(primaryPool);
      var failoverEndpoint = $scope.bsPoolEndpointKey(failoverPool);
      return primaryEndpoint && failoverEndpoint && primaryEndpoint === failoverEndpoint;
    };
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
        if (!primaryAlgo ||
            (failoverAlgo && primaryAlgo !== failoverAlgo) ||
            sameEndpoint(slot.primary, slot.failover)) {
          slot.failover = null;
          $scope.boardCategories.failover[bid] = '';
        }
      }
    } else if (lane === 'failover') {
      var failoverSlot = $scope.assignments && $scope.assignments[bid];
      if (failoverSlot && failoverSlot.primary && failoverSlot.failover &&
          sameEndpoint(failoverSlot.primary, failoverSlot.failover)) {
        failoverSlot.failover = null;
        Alertify.log.error('Failover must use a different stratum endpoint than the main pool');
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
          if (d.assignments) {
            $scope.assignments = $scope.bsCoerceAssignments(d.assignments);
            $scope.bsRebuildBoardCategories();
          }
          $scope.bsAssignmentsOriginal = angular.copy($scope.assignments);
          $scope.bsAssignmentsDirty = false;
          $scope.bsClearPrimaryEditors();
          if (d.sanitized_failovers) {
            Alertify.log.log('Removed failover using the same stratum endpoint as main pool');
          }
          if (d.restarted === false) Alertify.log.log(d.message || 'Assignments saved; miner restart pending');
          else Alertify.log.success('Assignments saved, miner restarting');
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
    $scope.bsClearPrimaryEditors();
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
    $http.post('f_blakestream.php?action=restart', {}).success(function(d) {
      if (d.ok && d.restarted === false) Alertify.log.log(d.message || 'Miner restart pending');
      else if (d.ok) Alertify.log.success('Miner restarting');
      else Alertify.log.error('Restart failed: ' + (d.error || 'unknown'));
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
        if (d.ok && d.restarted === false) Alertify.log.log(d.message || 'Assignments saved; miner restart pending');
        else if (d.ok) Alertify.log.success('All 3 boards switched to ' + match.name);
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
        if (d.ok && d.restarted === false) Alertify.log.log(d.message || 'Assignments saved; miner restart pending');
        else if (d.ok) Alertify.log.success('Disabled ' + match.name + ' on ' + changed + ' board(s)');
        else Alertify.log.error('Save failed: ' + (d.error || 'unknown'));
      });
  };

  $scope.cgminer = function(command,parameter) {
    $scope.tick();

    var execute = function(){
        var cmd = command || 'summary', param = parameter || '';
        var reads = ['summary', 'devs', 'pools', 'stats', 'version', 'config', 'coin', 'check'];
        var request = reads.indexOf(cmd) >= 0
          ? $http.get('f_miner.php?command=' + encodeURIComponent(cmd))
          : $http.post('f_miner.php', {command:cmd, parameter:param});
        request.success(function(d){
            if(d.info){
                angular.forEach(d.info, function(v,k) {Alertify.log.create(v.type, v.text);});
            }
            $scope.tick();
        });
    }
    $timeout(execute, 1000);
  };


})


.controller('CtrlMiner', function($scope,$http,$timeout,$window) {
  $scope.status.extra=true;
  $scope.bsMinerTab = $scope.bsViewTab('miner');
  $scope.$on('bsViewTabsReset', function() { $scope.bsMinerTab = $scope.bsViewTab('miner'); });
  $scope.bsSelectMinerTab = function(tab) {
    if (['pools', 'clocks', 'sgminer'].indexOf(tab) < 0) return;
    $scope.bsMinerTab = tab;
    $scope.bsRememberViewTab('miner', tab);
  };

  $scope.bsClockControlsEnabled = function() {
    var firmware = $scope.devFirmware && $scope.devFirmware.status;
    return !!($scope.settings && $scope.settings.userAdjustableClocks &&
      (!firmware || firmware.custom_active === true));
  };

  $scope.cgminer = function(command,parameter) {
    $scope.tick();

    var execute = function(){
      var cmd = command || 'summary', param = parameter || '';
      var reads = ['summary', 'devs', 'pools', 'stats', 'version', 'config', 'coin', 'check'];
      var request = reads.indexOf(cmd) >= 0
        ? $http.get('f_miner.php?command=' + encodeURIComponent(cmd))
        : $http.post('f_miner.php', {command:cmd, parameter:param});
      request.success(function(d){
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
      $http.post('f_blakestream.php?action=restart', {}).success(function(d){
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
  $scope.bsPoolOrderDirty = {};

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
      if (d && angular.isArray(d.categories)) {
        $scope.savedPools = d;
        $scope.bsPoolOrderDirty = {};
        Alertify.log.create('info', 'Pools config loaded');
        // After categories load, if the Add Pool form is open with no
        // category yet picked, auto-select the only category if there's
        // exactly one. Doesn't override an in-progress edit.
        if (!$scope.editingPool && $scope.poolDraft && !$scope.poolDraft.categoryId
            && d.categories.length === 1 && d.categories[0].id) {
          $scope.poolDraft.categoryId = d.categories[0].id;
        }
      } else {
        Alertify.log.error('Failed to load saved pools');
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
        if (d && d.ok) {
          $scope.bsPoolOrderDirty = {};
        }
        if (!silent) {
          if (d.ok) Alertify.log.success(d.message || 'Pools saved');
          else Alertify.log.error('Save failed: ' + (d.error || 'unknown'));
        }
      })
      .error(function() {
        Alertify.log.error('Failed to save pools');
      });
  };

  $scope.bsPoolOrderIsDirty = function(catId) {
    return !!($scope.bsPoolOrderDirty && catId && $scope.bsPoolOrderDirty[catId]);
  };

  $scope.bsSavePoolOrder = function(cat) {
    if (!cat || !$scope.bsPoolOrderIsDirty(cat.id)) return;
    $scope.bsSavePoolsBlob();
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
    $scope.bsConfirm('Delete category <b>' + $scope.bsHtmlEscape(cat.name) + '</b>?', function() {
      $scope.savedPools.categories = $scope.savedPools.categories.filter(function(c) {
        return c.id !== cat.id;
      });
      $scope.bsSavePoolsBlob();
    }, {title: 'Delete category', yesLabel: 'Delete'});
  };

  $scope.bsMovePool = function(cat, index, delta, $event) {
    if ($event) {
      $event.preventDefault();
      $event.stopPropagation();
    }
    if (!cat || !angular.isArray(cat.pools)) return;
    var target = index + delta;
    if (index < 0 || target < 0 || target >= cat.pools.length) return;

    if ($scope.bsPendingEditCount && $scope.bsPendingEditCount() > 0) {
      Alertify.log.error('Update or cancel pending pool edits before reordering');
      return;
    }

    var moved = cat.pools.splice(index, 1)[0];
    cat.pools.splice(target, 0, moved);
    $scope.bsPoolOrderDirty[cat.id] = true;
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

  $scope.bsPoolDraftValid = function() {
    return !!($scope.poolDraft && $scope.poolDraft.name && $scope.poolDraft.url && $scope.poolDraft.categoryId);
  };

  $scope.bsSavePoolDraftDisabled = function() {
    if (!$scope.bsPoolDraftValid()) return true;
    if (!$scope.editingPool) return false;
    return $scope.bsPendingEditCount() < 1;
  };

  $scope.bsSavePoolAsNewDisabled = function() {
    if (!$scope.editingPool || !$scope.bsPoolDraftValid()) return true;
    return !$scope.bsCurrentDraftDirty();
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

  $scope.bsUniquePoolName = function(baseName, categoryId) {
    var base = (baseName || 'Pool').trim();
    var cats = ($scope.savedPools && $scope.savedPools.categories) || [];
    var targetCat = null;
    for (var i = 0; i < cats.length; i++) {
      if (cats[i].id === categoryId) {
        targetCat = cats[i];
        break;
      }
    }
    if (!targetCat) return base;

    var used = {};
    angular.forEach(targetCat.pools || [], function(pool) {
      used[String(pool.name || '').toLowerCase()] = true;
    });
    if (!used[base.toLowerCase()]) return base;

    var name = base + ' copy';
    var n = 2;
    while (used[name.toLowerCase()]) {
      name = base + ' copy ' + n;
      n++;
    }
    return name;
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

  $scope.bsSavePoolAsNew = function() {
    if (!$scope.editingPool) return;
    if (!$scope.bsCurrentDraftDirty()) {
      Alertify.log.error('Change the pool form before saving as new');
      return;
    }
    var d = angular.copy($scope.poolDraft || {});
    if (!d.name || !d.url || !d.categoryId) {
      Alertify.log.error('Name, URL and Category required');
      return;
    }

    var orig = $scope.bsFindOriginalPool($scope.editingPool.id);
    if (orig && (orig.name || '') === (d.name || '')) {
      d.name = $scope.bsUniquePoolName(d.name, d.categoryId);
    }
    delete d.id;

    if (!$scope.bsApplyDraftToTree(d)) {
      Alertify.log.error('Could not save new pool (category not found)');
      return;
    }

    delete $scope.pendingEdits[$scope.editingPool.id];
    $scope.bsSavePoolsBlob();
    $scope.editingPool = null;
    $scope.poolDraft = $scope.bsFreshPoolDraft();
    if ($scope.poolForm) $scope.poolForm.$setPristine();
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
        'Pool <b>' + $scope.bsHtmlEscape(pname) + '</b> is currently assigned to board(s) <b>' +
        assignedBoards.join(', ') + '</b>.<br>Delete it anyway? Those board(s) will become idle.',
        doDelete,
        {title: 'Delete pool', yesLabel: 'Delete'}
      );
    } else {
      $scope.bsConfirm(
        'Delete pool <b>' + $scope.bsHtmlEscape(pname) + '</b>?',
        doDelete,
        {title: 'Delete pool', yesLabel: 'Delete'}
      );
    }
  };

  $scope.bsRestart = function() {
    $http.post('f_blakestream.php?action=restart', {}).success(function(d) {
      if (d.ok && d.restarted === false) Alertify.log.log(d.message || 'Miner restart pending');
      else if (d.ok) Alertify.log.success('Miner restarting');
      else Alertify.log.error('Restart failed: ' + (d.error || 'unknown'));
    });
  };

  $scope.bsClockLimits = {min: 400, max: 400};
  $scope.bsValidatedClocks = [400];
  $scope.bsClockTempLimits = {min: 1, max: 99};
  $scope.bsFanCurveLimits = {minFan: 20, maxFan: 100, minTemp: 20, maxTemp: 120};
  $scope.bsClockMode = 'global';
  $scope.bsClock = {mhz: 400, recover: 40, cutoff: 55};
  $scope.bsBoardClocks = {board0: 400, board1: 400, board2: 400};
  $scope.bsExperimentalClockIndex = {enabled: false};
  $scope.bsBoardClockIndex = {board0: 30, board1: 30, board2: 30};
  $scope.bsGlobalClockIndex = {index: 30, mhz: 400};
  $scope.bsBoardNominalMHz = {board0: 400, board1: 400, board2: 400};
  $scope.bsClockMapping = 'bkb-v1-clock-index-1';
  $scope.bsClockIndexObserved = [[8, 16, 22, 26, 30], [8, 16, 22, 30], [8, 16, 22, 30]];
  $scope.bsManualFanLimits = {min: 40, max: 100};
  $scope.bsManualFan = 70;
  $scope.bsClockProfiles = [];
  $scope.bsSelectedClockProfileId = '';
  $scope.bsClockProfileName = '';
  $scope.bsClockProfileLimit = 24;

  $scope.bsOptionIndex = function(key) {
    if (!angular.isArray($scope.options)) return -1;
    for (var i = 0; i < $scope.options.length; i++) {
      if ($scope.options[i] && $scope.options[i].key === key) return i;
    }
    return -1;
  };

  $scope.bsMinerOptionsReady = function() {
    return angular.isArray($scope.options) && $scope.options.length > 0;
  };

  $scope.bsParseInt = function(value, fallback) {
    var parsed = parseInt(value, 10);
    return isNaN(parsed) ? fallback : parsed;
  };

  $scope.bsClampClock = function(value) {
    var clock = $scope.bsParseInt(value, 400);
    return $scope.bsValidatedClocks.indexOf(clock) >= 0 ? clock : 400;
  };

  $scope.bsNormalizeClockMode = function(value, hasLegacyBoardClocks) {
    if (value === 'global' || value === 'individual') return value;
    return hasLegacyBoardClocks ? 'individual' : 'global';
  };

  $scope.bsEffectiveGlobalClock = function() {
    return $scope.bsExperimentalClockIndex.enabled ? $scope.bsGlobalClockIndex.mhz : 400;
  };

  $scope.bsSelectClockMode = function(mode) {
    mode = $scope.bsNormalizeClockMode(mode, false);
    if ($scope.bsClockMode === mode) return;
    $scope.bsClockMode = mode;
    $scope.bsUpdateClockOverride();
    if ($scope.clockForm) $scope.clockForm.$setDirty();
  };

  $scope.bsClockModePanelKeydown = function(mode, ev) {
    if (!ev || (ev.keyCode !== 13 && ev.keyCode !== 32)) return;
    ev.preventDefault();
    $scope.bsSelectClockMode(mode);
  };

  $scope.bsNormalizeBoardClock = function(value, fallback) {
    var clock = $scope.bsParseInt(value, fallback);
    return $scope.bsValidatedClocks.indexOf(clock) >= 0 ? clock : 400;
  };

  $scope.bsUpdateClockOverride = function() {
    var values = $scope.bsClockIndexValues();
    // Invalid drafts stay on the strict validation path, never fall back to default.
    $scope.bsExperimentalClockIndex.enabled = !values || values.some(function(value) { return value !== 30; });
  };

  $scope.bsValidClockIndex = function(value) {
    return (typeof value === 'number' || typeof value === 'string') &&
      /^(?:[89]|[12][0-9]|3[0-2])$/.test(String(value));
  };

  $scope.bsClockIndexValues = function() {
    if ($scope.bsClockMode === 'global') {
      var index = $scope.bsGlobalClockIndex.index;
      return $scope.bsValidClockIndex(index) ? [Number(index), Number(index), Number(index)] : null;
    }
    var values = [];
    for (var i = 0; i < 3; i++) {
      var raw = $scope.bsBoardClockIndex['board' + i];
      if (!$scope.bsValidClockIndex(raw)) return null;
      values.push(Number(raw));
    }
    return values;
  };

  $scope.bsNominalFromClockIndex = function(value) {
    // Firmware echoes (including 480 for index 30) are not public MHz labels.
    return $scope.bsValidClockIndex(value) && Number(value) === 30 ? 400 : null;
  };

  $scope.bsClockIndexFromNominal = function(value) {
    return (typeof value === 'number' || typeof value === 'string') && String(value) === '400' ? 30 : null;
  };

  $scope.bsRefreshNominalClocks = function() {
    $scope.bsGlobalClockIndex.mhz = $scope.bsNominalFromClockIndex($scope.bsGlobalClockIndex.index);
    [0, 1, 2].forEach(function(i) {
      $scope.bsBoardNominalMHz['board' + i] = $scope.bsNominalFromClockIndex($scope.bsBoardClockIndex['board' + i]);
    });
  };

  $scope.bsLinkedClockChanged = function(board, source) {
    if (board === 'global') {
      if (source === 'mhz') $scope.bsGlobalClockIndex.index = $scope.bsClockIndexFromNominal($scope.bsGlobalClockIndex.mhz);
      else $scope.bsGlobalClockIndex.mhz = $scope.bsNominalFromClockIndex($scope.bsGlobalClockIndex.index);
    } else {
      var key = 'board' + board;
      if (source === 'mhz') $scope.bsBoardClockIndex[key] = $scope.bsClockIndexFromNominal($scope.bsBoardNominalMHz[key]);
      else $scope.bsBoardNominalMHz[key] = $scope.bsNominalFromClockIndex($scope.bsBoardClockIndex[key]);
    }
    $scope.bsUpdateClockOverride();
    if ($scope.clockForm) $scope.clockForm.$setDirty();
  };

  $scope.bsClockRuntimeMode = function() {
    // All-boards custom is a batch of existing per-board commands, not dest=1.
    return $scope.bsExperimentalClockIndex.enabled ? 'individual' : $scope.bsClockMode;
  };

  $scope.bsEnsureClockScopeOption = function(scope) {
    $scope.options = ($scope.options || []).filter(function(option) {
      return option.key !== 'blakestream-clock-scope';
    });
    $scope.options.push({key: 'blakestream-clock-scope', value: scope,
      description: 'Clock control scope (presentation only)'});
  };

  $scope.bsLiveClockBoard = function(board) {
    var age = Date.now() - ($scope.bsStatusReceivedAt || 0);
    var maxAge = Math.max(30000, Math.min(120000, (Number($scope.intervalMax) || 20) * 2000));
    if (age < 0 || age > maxAge || !$scope.status || !$scope.status.minerUp) return null;
    var devs = $scope.status.devs || [];
    for (var i = 0; i < devs.length; i++) {
      if (devs[i] && Number(devs[i].ID) === board) return devs[i];
    }
    return null;
  };

  $scope.bsClockCommandState = function(board) {
    var live = $scope.bsLiveClockBoard(board);
    if (!live || !$scope.bsValidClockIndex(live.SentClockIndex)) return 'Command unavailable';
    return 'Sent Index ' + live.SentClockIndex + (live.CommandVerified ? ' / confirmed' : ' / unconfirmed');
  };

  $scope.bsClockBaselineRow = function(index, board) {
    var data = $scope.status && $scope.status.clockBaseline;
    if (!$scope.bsValidClockIndex(index) || !data || data.source !== 'lab-example' ||
        data.algorithm !== 'blake256r8' || !angular.isArray(data.rows)) return null;
    var boards = board === 'global' ? [0, 1, 2] : [board];
    if (!boards.every(function(id) {
      var live = $scope.bsLiveClockBoard(id);
      if (live) return live.ClockBaselineCompatible === true;
      // A stopped miner has no live command telemetry; its published lab
      // reference can still be shown for the saved, supported pool algorithm.
      var assigned = ($scope.assignments || {})[String(id)];
      var primary = assigned && assigned.primary;
      if (!primary) return false;
      var categories = ($scope.savedPools && $scope.savedPools.categories) || [];
      return categories.some(function(category) {
        return (category.pools || []).some(function(pool) {
          var algo = String(pool.algo || 'blake256r8').trim().toLowerCase();
          return pool.id === primary && (algo === 'blake256r8' || algo === 'blakecoin');
        });
      });
    })) return null;
    for (var i = 0; i < data.rows.length; i++) {
      if (data.rows[i].clock_index === Number(index)) return data.rows[i];
    }
    return null;
  };

  $scope.bsClockDisplayIndex = function(board) {
    return board === 'global' || $scope.bsClockMode === 'global'
      ? $scope.bsGlobalClockIndex.index : $scope.bsBoardClockIndex['board' + board];
  };

  $scope.bsClockEstimate = function(board) {
    return $scope.bsClockBaselineRow($scope.bsClockDisplayIndex(board), board);
  };

  $scope.bsClockMHzText = function(board) {
    var index = $scope.bsClockDisplayIndex(board), row = $scope.bsClockEstimate(board);
    if ($scope.bsValidClockIndex(index) && Number(index) === 30) return '400';
    return row && typeof row.estimated_mhz === 'number' && isFinite(row.estimated_mhz)
      ? '~' + Math.round(row.estimated_mhz) : 'Unavailable';
  };

  $scope.bsClockGHsText = function(board, total) {
    var row = $scope.bsClockEstimate(board);
    return row && typeof row.ghs === 'number' && isFinite(row.ghs) && row.ghs > 0
      ? '~' + (row.ghs * (total ? 3 : 1)).toFixed(1) : 'Unavailable';
  };

  $scope.bsClockIndexTrialState = function(board) {
    var values = $scope.bsClockIndexValues();
    var value = values ? values[board] : null;
    if (!$scope.bsValidClockIndex(value)) return 'Enter 8-32';
    var row = $scope.bsClockBaselineRow(value, board);
    var observed = row ? row.measured_boards : null;
    return (observed ? observed.indexOf(board) >= 0 : $scope.bsClockIndexObserved[board].indexOf(Number(value)) >= 0)
      ? 'Preliminary board data' : 'Untested on this board';
  };

  $scope.bsClockPreview = function(indices, experimental) {
    var rows = [];
    for (var i = 0; i < 3; i++) {
      var live = $scope.bsLiveClockBoard(i);
      var previous = live && $scope.bsValidClockIndex(live.SentClockIndex)
        ? 'Index ' + live.SentClockIndex
        : 'unavailable';
      var nominal = $scope.bsNominalFromClockIndex(indices[i]);
      var estimate = $scope.bsClockBaselineRow(indices[i], i);
      var estimateText = estimate ? ' ~' + estimate.ghs.toFixed(1) + ' GH/s lab estimate' : '';
      var mhzText = nominal !== null ? nominal + ' MHz' :
        (estimate && estimate.estimated_mhz !== null ? '~' + Math.round(estimate.estimated_mhz) + ' MHz' : 'MHz unmapped');
      rows.push('Board ' + i + ': ' + previous + ' &rarr; Index <b>' + indices[i] + '</b>' +
        estimateText + ' / ' + mhzText);
    }
    return '<p>' + rows.join('<br>') + '</p>';
  };

  $scope.bsDefaultClock = function(mode) {
    $scope.bsClockMode = $scope.bsNormalizeClockMode(mode, false);
    if ($scope.bsClockMode === 'global') {
      $scope.bsGlobalClockIndex = {index: 30, mhz: 400};
    } else {
      $scope.bsBoardClockIndex = {board0: 30, board1: 30, board2: 30};
      $scope.bsBoardNominalMHz = {board0: 400, board1: 400, board2: 400};
    }
    $scope.bsClock.mhz = 400;
    $scope.bsBoardClocks = {board0: 400, board1: 400, board2: 400};
    $scope.bsUpdateClockOverride();
    if ($scope.clockForm) $scope.clockForm.$setDirty();
  };

  $scope.bsEnsureExperimentalOptions = function(enabled, values) {
    $scope.options = ($scope.options || []).filter(function(option) {
      return option.key !== 'baikal-experimental-clock-index' && option.key !== 'baikal-board-clock-index';
    });
    if (enabled) {
      $scope.options.push({key: 'baikal-experimental-clock-index', value: true,
                          description: 'Explicit experimental clock index control'});
      $scope.options.push({key: 'baikal-board-clock-index', value: values.join(':'),
                          description: 'Board 0:1:2 clock indices, not MHz'});
    }
  };

  $scope.bsBoardClockArray = function(boardClocks, fallback) {
    boardClocks = boardClocks || {};
    fallback = $scope.bsClampClock(fallback);
    return [
      $scope.bsNormalizeBoardClock(boardClocks.board0, fallback),
      $scope.bsNormalizeBoardClock(boardClocks.board1, fallback),
      $scope.bsNormalizeBoardClock(boardClocks.board2, fallback)
    ];
  };

  $scope.bsBoardClockObject = function(values, fallback) {
    values = angular.isArray(values) ? values : [];
    fallback = $scope.bsClampClock(fallback);
    return {
      board0: $scope.bsNormalizeBoardClock(values[0], fallback),
      board1: $scope.bsNormalizeBoardClock(values[1], fallback),
      board2: $scope.bsNormalizeBoardClock(values[2], fallback)
    };
  };

  $scope.bsParseBaikalBoardClocks = function(value, fallback) {
    var text = (value === undefined || value === null) ? '' : String(value);
    var parts = text.split(':');
    if (parts.length !== 3) parts = [fallback, fallback, fallback];
    return $scope.bsBoardClockObject(parts, fallback);
  };

  $scope.bsClampTemp = function(value, fallback) {
    var temp = $scope.bsParseInt(value, fallback);
    if (temp < $scope.bsClockTempLimits.min) temp = $scope.bsClockTempLimits.min;
    if (temp > $scope.bsClockTempLimits.max) temp = $scope.bsClockTempLimits.max;
    return temp;
  };

  $scope.bsNormalizeClockTemps = function(parsed) {
    parsed.recover = $scope.bsClampTemp(parsed.recover, 40);
    parsed.cutoff = $scope.bsClampTemp(parsed.cutoff, 55);
    if (parsed.recover >= parsed.cutoff) {
      parsed.recover = 40;
      parsed.cutoff = 55;
    }
    return parsed;
  };

  $scope.bsParseBaikalOptions = function(value) {
    var parsed = {clock: 400, recover: 40, cutoff: 55};
    var text = (value === undefined || value === null) ? '' : String(value);
    var parts = text.split(':');

    if (parts.length >= 3) {
      parsed.clock = $scope.bsParseInt(parts[0], parsed.clock);
      parsed.recover = $scope.bsParseInt(parts[1], parsed.recover);
      parsed.cutoff = $scope.bsParseInt(parts[2], parsed.cutoff);
    } else if (parts.length >= 2 && text !== '') {
      // Backward-compatible old format: recover:cutoff.
      parsed.recover = $scope.bsParseInt(parts[0], parsed.recover);
      parsed.cutoff = $scope.bsParseInt(parts[1], parsed.cutoff);
    } else if (parts.length === 1 && parts[0] !== '') {
      parsed.clock = $scope.bsParseInt(parts[0], parsed.clock);
    }

    parsed.clock = $scope.bsClampClock(parsed.clock);
    return $scope.bsNormalizeClockTemps(parsed);
  };

  $scope.bsClockSettingsState = function() {
    if (!$scope.bsClock || !$scope.bsBoardClocks) return null;
    var text = function(value) {
      return (value === undefined || value === null) ? '' : String(value).trim();
    };
    return angular.toJson([
      $scope.bsNormalizeClockMode($scope.bsClockMode, false),
      text($scope.bsClock.mhz),
      text($scope.bsClock.recover),
      text($scope.bsClock.cutoff),
      text($scope.bsBoardClocks.board0),
      text($scope.bsBoardClocks.board1),
      text($scope.bsBoardClocks.board2),
      !!$scope.bsExperimentalClockIndex.enabled,
      $scope.bsExperimentalClockIndex.enabled ? $scope.bsClockIndexValues() : null,
      $scope.bsExperimentalClockIndex.enabled ? ($scope.bsClockMode === 'global'
        ? text($scope.bsGlobalClockIndex.mhz)
        : [0, 1, 2].map(function(i) { return text($scope.bsBoardNominalMHz['board' + i]); })) : null
    ]);
  };

  $scope.bsCaptureClockSettingsBaseline = function() {
    $scope.bsClockSettingsBaseline = $scope.bsClockSettingsState();
    if ($scope.clockForm) $scope.clockForm.$setPristine();
  };

  $scope.bsClockSettingsChanged = function() {
    var current = $scope.bsClockSettingsState();
    return $scope.bsClockSettingsBaseline !== undefined &&
           current !== null &&
           current !== $scope.bsClockSettingsBaseline;
  };

  $scope.bsRefreshClockFromOptions = function() {
    var idx = $scope.bsOptionIndex('baikal-options');
    var parsed = idx >= 0 ? $scope.bsParseBaikalOptions($scope.options[idx].value)
                          : $scope.bsParseBaikalOptions('');
    var midx = $scope.bsOptionIndex('baikal-clock-mode');
    var bidx = $scope.bsOptionIndex('baikal-board-clocks');
    $scope.bsClockMode = $scope.bsNormalizeClockMode(
      midx >= 0 ? $scope.options[midx].value : '',
      bidx >= 0
    );
    $scope.bsClock = {
      mhz: parsed.clock,
      recover: parsed.recover,
      cutoff: parsed.cutoff
    };
    $scope.bsBoardClocks = $scope.bsParseBaikalBoardClocks(
      bidx >= 0 ? $scope.options[bidx].value : '',
      400
    );
    var eidx = $scope.bsOptionIndex('baikal-experimental-clock-index');
    var pidx = $scope.bsOptionIndex('baikal-board-clock-index');
    $scope.bsExperimentalClockIndex.enabled = eidx >= 0 &&
      ($scope.options[eidx].value === true || $scope.options[eidx].value === 'true');
    var clock_index = pidx >= 0 ? String($scope.options[pidx].value).split(':') : ['30', '30', '30'];
    clock_index = clock_index.map(function(value) {
      return $scope.bsValidClockIndex(value) ? Number(value) : null;
    });
    $scope.bsBoardClockIndex = {board0: clock_index[0], board1: clock_index[1], board2: clock_index[2]};
    var sidx = $scope.bsOptionIndex('blakestream-clock-scope');
    if (sidx >= 0 && ($scope.options[sidx].value === 'global' || $scope.options[sidx].value === 'individual'))
      $scope.bsClockMode = $scope.options[sidx].value;
    $scope.bsGlobalClockIndex.index = clock_index.length === 3 && clock_index.every(function(value) { return value === clock_index[0]; }) ? clock_index[0] : 30;
    $scope.bsRefreshNominalClocks();
    var fidx = $scope.bsOptionIndex('baikal-fan');
    if (fidx >= 0) $scope.bsManualFan = $scope.bsClampManualFan($scope.options[fidx].value);
    $scope.bsCaptureClockSettingsBaseline();
  };

  $scope.bsClampManualFan = function(value) {
    var n = parseInt(value, 10);
    if (isNaN(n)) n = 70;
    if (n < $scope.bsManualFanLimits.min) n = $scope.bsManualFanLimits.min;
    if (n > $scope.bsManualFanLimits.max) n = $scope.bsManualFanLimits.max;
    return n;
  };

  // Manual fan: persist baikal-fan. Live manual fan control is disabled until
  // the sgminer driver path is proven safe.
  $scope.bsApplyManualFan = function() {
    var fan = $scope.bsClampManualFan($scope.bsManualFan);
    $scope.bsManualFan = fan;
    $http.post('f_blakestream.php?action=set_manual_fan', {fan: fan}).success(function(d) {
      if (d && d.info) {
        angular.forEach(d.info, function(i) {
          var level = (i.type === 'success') ? 'success' : 'error';
          if (Alertify && Alertify.log && Alertify.log[level]) Alertify.log[level](i.text);
        });
      }
      var fidx = $scope.bsOptionIndex('baikal-fan');
      if (fidx >= 0) { $scope.options[fidx].value = String(fan); }
      else if (angular.isArray($scope.options)) {
        $scope.options.push({key: 'baikal-fan', value: String(fan), description: 'BK-B manual fan percent'});
      }
      if ($scope.manualFanForm) $scope.manualFanForm.$setPristine();
    }).error(function() {
      if (Alertify && Alertify.log) Alertify.log.error('Fan apply request failed');
    });
  };

  // ---- Option C: visual fan-curve graph (temp X, fan% Y, draggable points) ----
  $scope.bsFanGraph = {w: 360, h: 126, padL: 38, padR: 14, padT: 10, padB: 22, tMin: 20, tMax: 80};
  $scope.bsFanGraphYTicks = [0, 25, 50, 75, 100];
  $scope.bsFanGraphXTicks = [20, 35, 50, 65, 80];

  $scope.bsFanGraphPlot = function() {
    var g = $scope.bsFanGraph;
    return {x: g.padL, y: g.padT, w: g.w - g.padL - g.padR, h: g.h - g.padT - g.padB};
  };
  $scope.bsFanTempToX = function(t) {
    var g = $scope.bsFanGraph, p = $scope.bsFanGraphPlot();
    t = Math.max(g.tMin, Math.min(g.tMax, t));
    return p.x + (t - g.tMin) / (g.tMax - g.tMin) * p.w;
  };
  $scope.bsFanPctToY = function(f) {
    var p = $scope.bsFanGraphPlot();
    f = Math.max(0, Math.min(100, f));
    return p.y + (100 - f) / 100 * p.h;
  };
  $scope.bsFanXToTemp = function(x) {
    var g = $scope.bsFanGraph, p = $scope.bsFanGraphPlot();
    return Math.round(Math.max(g.tMin, Math.min(g.tMax, g.tMin + (x - p.x) / p.w * (g.tMax - g.tMin))));
  };
  $scope.bsFanYToPct = function(y) {
    var p = $scope.bsFanGraphPlot();
    return Math.round(Math.max(0, Math.min(100, 100 - (y - p.y) / p.h * 100)));
  };
  $scope.bsFanCurveModel = function() {
    return $scope.bsNormalizeFanCurveValues({
      min: $scope.settings.userAutoFanMin,
      target: $scope.settings.userAutoFanTarget,
      midTemp: $scope.settings.userAutoFanMidTemp,
      midFan: $scope.settings.userAutoFanMidFan,
      full: $scope.settings.userAutoFanFull
    });
  };
  $scope.bsFanCurvePoints = function() {
    var p = $scope.bsFanGraphPlot(), m = $scope.bsFanCurveModel();
    var yMin = $scope.bsFanPctToY(m.min), yMid = $scope.bsFanPctToY(m.midFan), yTop = $scope.bsFanPctToY(100);
    return p.x + ',' + yMin + ' ' +
           $scope.bsFanTempToX(m.target) + ',' + yMin + ' ' +
           $scope.bsFanTempToX(m.midTemp) + ',' + yMid + ' ' +
           $scope.bsFanTempToX(m.full) + ',' + yTop + ' ' +
           (p.x + p.w) + ',' + yTop;
  };
  $scope.bsFanHandleTarget = function() {
    var m = $scope.bsFanCurveModel();
    return {x: $scope.bsFanTempToX(m.target), y: $scope.bsFanPctToY(m.min)};
  };
  $scope.bsFanHandleFull = function() {
    var m = $scope.bsFanCurveModel();
    return {x: $scope.bsFanTempToX(m.full), y: $scope.bsFanPctToY(100)};
  };
  $scope.bsFanHandleMid = function() {
    var m = $scope.bsFanCurveModel();
    return {x: $scope.bsFanTempToX(m.midTemp), y: $scope.bsFanPctToY(m.midFan)};
  };
  $scope.bsFanGraphYLabelY = function(pct) { return $scope.bsFanPctToY(pct) + 3; };
  $scope.bsFanGraphXLabelX = function(t) { return $scope.bsFanTempToX(t); };

  $scope.bsFanCurveDragStart = function(which, ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    var svg = ev.target.ownerSVGElement || ev.target;
    var toSvg = function(e) {
      var rect = svg.getBoundingClientRect();
      var vw = (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width) || rect.width;
      var vh = (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.height) || rect.height;
      var cx = (e.touches ? e.touches[0].clientX : e.clientX);
      var cy = (e.touches ? e.touches[0].clientY : e.clientY);
      return {x: (cx - rect.left) * vw / rect.width, y: (cy - rect.top) * vh / rect.height};
    };
    var move = function(e) {
      var pt = toSvg(e);
      $scope.$apply(function() {
        if (which === 'target') {
          $scope.settings.userAutoFanTarget = $scope.bsFanXToTemp(pt.x);
          $scope.settings.userAutoFanMin = Math.max(20, $scope.bsFanYToPct(pt.y));
          if ((parseInt($scope.settings.userAutoFanMidTemp, 10) || 0) <= $scope.settings.userAutoFanTarget) {
            $scope.settings.userAutoFanMidTemp = $scope.settings.userAutoFanTarget + 1;
          }
          if ((parseInt($scope.settings.userAutoFanFull, 10) || 0) <= $scope.settings.userAutoFanMidTemp) {
            $scope.settings.userAutoFanFull = $scope.settings.userAutoFanMidTemp + 1;
          }
        } else if (which === 'mid') {
          var midTemp = $scope.bsFanXToTemp(pt.x);
          var target = parseInt($scope.settings.userAutoFanTarget, 10) || 36;
          var full = parseInt($scope.settings.userAutoFanFull, 10) || 50;
          if (midTemp <= target) midTemp = target + 1;
          if (midTemp >= full) midTemp = full - 1;
          $scope.settings.userAutoFanMidTemp = midTemp;
          $scope.settings.userAutoFanMidFan = Math.max($scope.settings.userAutoFanMin || 40,
                                                       Math.min(100, $scope.bsFanYToPct(pt.y)));
          if ((parseInt($scope.settings.userAutoFanMidFan, 10) || 0) < (parseInt($scope.settings.userAutoFanMin, 10) || 40)) {
            $scope.settings.userAutoFanMidFan = $scope.settings.userAutoFanMin;
          }
        } else {
          var f = $scope.bsFanXToTemp(pt.x);
          var mid = parseInt($scope.settings.userAutoFanMidTemp, 10) || bsDefaultAutoFanMidTemp($scope.settings);
          if (f <= mid) f = mid + 1;
          $scope.settings.userAutoFanFull = f;
        }
        if ($scope.bsStageFanCurve) $scope.bsStageFanCurve();
        if ($scope.fanCurveForm) $scope.fanCurveForm.$setDirty();
      });
    };
    var up = function() {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      document.removeEventListener('touchmove', move);
      document.removeEventListener('touchend', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
    document.addEventListener('touchmove', move, {passive: false});
    document.addEventListener('touchend', up);
  };

  $scope.bsBaikalOptionsValue = function() {
    $scope.bsClock = $scope.bsNormalizeClockTemps({
      mhz: $scope.bsClampClock($scope.bsClock.mhz),
      recover: $scope.bsClock.recover,
      cutoff: $scope.bsClock.cutoff
    });
    return $scope.bsClampClock($scope.bsClock.mhz) + ':' +
           $scope.bsParseInt($scope.bsClock.recover, 40) + ':' +
           $scope.bsParseInt($scope.bsClock.cutoff, 55);
  };

  $scope.bsPrepareClockValues = function(mhz, recover, cutoff) {
    var rawClock = $scope.bsParseInt(mhz, 400);
    var clock = $scope.bsClampClock(rawClock);
    var normalized = $scope.bsNormalizeClockTemps({
      mhz: clock,
      recover: recover,
      cutoff: cutoff
    });
    normalized.mhz = clock;
    return {
      rawClock: rawClock,
      clock: clock,
      recover: normalized.recover,
      cutoff: normalized.cutoff,
      clockClamped: rawClock !== clock,
      value: clock + ':' + normalized.recover + ':' + normalized.cutoff
    };
  };

  $scope.bsPreparedClockSave = function() {
    return $scope.bsPrepareClockValues($scope.bsClock.mhz, $scope.bsClock.recover, $scope.bsClock.cutoff);
  };

  $scope.bsApplyPreparedClockSettingsSave = function(prepared, boardPrepared, mode) {
    if (!$scope.bsMinerOptionsReady()) {
      Alertify.log.error('Miner options are still loading');
      return;
    }
    mode = $scope.bsNormalizeClockMode(mode, false);
    var clock_index = $scope.bsClockIndexValues();
    if ($scope.bsExperimentalClockIndex.enabled && !clock_index) {
      Alertify.log.error('Only default 400 MHz is mapped. Enter Index 8-32 for other settings.');
      return;
    }
    $scope.bsClock = {
      mhz: prepared.clock,
      recover: prepared.recover,
      cutoff: prepared.cutoff
    };
    $scope.bsClockMode = mode;
    $scope.bsBoardClocks = $scope.bsBoardClockObject(boardPrepared.values, 400);
    $scope.bsEnsureClockModeOption($scope.bsClockRuntimeMode());
    $scope.bsEnsureClockScopeOption(mode);
    $scope.bsEnsureBaikalOption(prepared.value);
    $scope.bsEnsureBoardClockOption(boardPrepared.value);
    $scope.bsEnsureExperimentalOptions($scope.bsExperimentalClockIndex.enabled, clock_index);
    $scope.sync('options',$scope.options,1);
    if ($scope.optionForm) $scope.optionForm.$setPristine();
    if ($scope.clockForm) $scope.clockForm.$setPristine();
  };

  $scope.bsEnsureClockModeOption = function(value) {
    if (!angular.isArray($scope.options)) $scope.options = [];
    var idx = $scope.bsOptionIndex('baikal-clock-mode');
    if (idx >= 0) {
      $scope.options[idx].value = value;
      if (!$scope.options[idx].description) {
        $scope.options[idx].description = 'BK-B clock application mode (global or individual)';
      }
    } else {
      $scope.options.push({
        key: 'baikal-clock-mode',
        value: value,
        description: 'BK-B clock application mode (global or individual)'
      });
    }
  };

  $scope.bsEnsureBaikalOption = function(value) {
    if (!angular.isArray($scope.options)) $scope.options = [];
    var idx = $scope.bsOptionIndex('baikal-options');
    if (idx >= 0) {
      $scope.options[idx].value = value;
      if (!$scope.options[idx].description) {
        $scope.options[idx].description = 'BK-B ASIC clock MHz:recover temp:cutoff temp';
      }
    } else {
      $scope.options.push({
        key: 'baikal-options',
        value: value,
        description: 'BK-B ASIC clock MHz:recover temp:cutoff temp'
      });
    }
  };

  $scope.bsEnsureBoardClockOption = function(value) {
    if (!angular.isArray($scope.options)) $scope.options = [];
    var idx = $scope.bsOptionIndex('baikal-board-clocks');
    if (idx >= 0) {
      $scope.options[idx].value = value;
      if (!$scope.options[idx].description) {
        $scope.options[idx].description = 'Advanced per-board applied clocks MHz (board0:board1:board2)';
      }
    } else {
      $scope.options.push({
        key: 'baikal-board-clocks',
        value: value,
        description: 'Advanced per-board applied clocks MHz (board0:board1:board2)'
      });
    }
  };

  $scope.bsPrepareBoardClockValues = function() {
    var fallback = $scope.bsClampClock($scope.bsClock.mhz);
    var raw = [
      $scope.bsParseInt($scope.bsBoardClocks.board0, fallback),
      $scope.bsParseInt($scope.bsBoardClocks.board1, fallback),
      $scope.bsParseInt($scope.bsBoardClocks.board2, fallback)
    ];
    var values = [
      $scope.bsNormalizeBoardClock(raw[0], fallback),
      $scope.bsNormalizeBoardClock(raw[1], fallback),
      $scope.bsNormalizeBoardClock(raw[2], fallback)
    ];
    var adjusted = [];
    for (var i = 0; i < values.length; i++) {
      if (raw[i] !== values[i]) adjusted.push('Board ' + i + ': ' + raw[i] + ' to ' + values[i] + ' MHz');
    }
    return {
      raw: raw,
      values: values,
      adjusted: adjusted,
      value: values.join(':')
    };
  };

  $scope.bsSaveClockSettings = function() {
    var prepared = $scope.bsPreparedClockSave();
    var boardPrepared = $scope.bsPrepareBoardClockValues();
    var mode = $scope.bsNormalizeClockMode($scope.bsClockMode, false);
    var experimental = !!$scope.bsExperimentalClockIndex.enabled;
    var clock_index = experimental ? $scope.bsClockIndexValues() : [30, 30, 30];
    var requestedState = $scope.bsClockSettingsState();
    var saveClockSettings = function() {
      if (requestedState !== $scope.bsClockSettingsState()) {
        Alertify.log.error('Clock settings changed. Review and confirm again.');
        return;
      }
      $scope.bsApplyPreparedClockSettingsSave(prepared, boardPrepared, mode);
    };
    if (experimental && !clock_index) {
      Alertify.log.error('Only default 400 MHz is mapped. Enter Index 8-32 for other settings.');
      return;
    }
    if (prepared.clockClamped || boardPrepared.adjusted.length) {
      Alertify.log.error('Use the Index fields to adjust clocks. Default uses Index 30.');
      return;
    }
    $scope.bsConfirm($scope.bsClockPreview(clock_index, experimental) +
      (experimental ? '<p>MHz estimates are performance-equivalent, not measured frequency.<br>Instability or hardware damage is possible.</p>' +
        (mode === 'global' ? '<p>All boards receive the same setting through individual commands.</p>' : '') :
        '<p>Default Index 30 is unchanged; the old 400 preset label used this same setting.</p>') +
      '<p>Temperature limits: ' + prepared.recover + ' / ' + prepared.cutoff + ' C.</p>',
      saveClockSettings, {title: experimental ? 'Apply Custom Clock' : 'Apply Clock Settings',
        titleClass: experimental ? 'bs-modal-title-warning' : '',
        yesLabel: 'Apply settings', noLabel: 'Cancel', lockBackdrop: true});
  };

  $scope.bsStageClockOption = function() {
    // Input edits are local until explicitly confirmed.
  };

  $scope.bsOptionChanged = function(option) {
    if (option && (option.key === 'baikal-options' ||
                   option.key === 'baikal-board-clocks' ||
                   option.key === 'baikal-clock-mode' ||
                   option.key === 'blakestream-clock-scope' ||
                   option.key === 'baikal-board-clock-index' || option.key === 'baikal-experimental-clock-index')) {
      $scope.bsRefreshClockFromOptions();
    }
  };

  $scope.bsCreateClockProfileId = function() {
    return 'cp-' + (new Date().getTime()).toString(36) + '-' + Math.floor(Math.random() * 10000).toString(36);
  };

  $scope.bsNormalizeClockProfile = function(profile) {
    profile = profile || {};
    var clock = $scope.bsClampClock(profile.clock);
    var hasBoardClocks = profile.board_clocks !== undefined && profile.board_clocks !== null;
    var mode = $scope.bsNormalizeClockMode(profile.mode, hasBoardClocks);
    var boardClockSource = angular.isArray(profile.board_clocks)
      ? $scope.bsBoardClockObject(profile.board_clocks, clock)
      : (profile.board_clocks || {});
    var boardClocks = $scope.bsBoardClockArray(boardClockSource, clock);
    var temps = $scope.bsNormalizeClockTemps({
      recover: profile.recover,
      cutoff: profile.cutoff
    });
    var fanCurve = $scope.bsNormalizeFanCurveValues({
      min: profile.fan_min,
      target: profile.fan_target,
      midTemp: profile.fan_mid_temp,
      midFan: profile.fan_mid_fan,
      full: profile.fan_full
    });
    var name = ((profile.name === undefined || profile.name === null) ? '' : String(profile.name)).replace(/\s+/g, ' ').trim();
    if (!name) name = profile.experimental === true ? 'Custom Clock' : 'Normal';
    if (name.length > 48) name = name.substr(0, 48);
    return {
      version: 2,
      mapping: profile.mapping || $scope.bsClockMapping,
      experimental: profile.experimental === true,
      clock_indices: profile.experimental === true ? profile.clock_indices : [30, 30, 30],
      id: profile.id || $scope.bsCreateClockProfileId(),
      name: name,
      mode: mode,
      scope: profile.scope || mode,
      clock: clock,
      board_clocks: boardClocks,
      recover: temps.recover,
      cutoff: temps.cutoff,
      fan_min: fanCurve.min,
      fan_target: fanCurve.target,
      fan_mid_temp: fanCurve.midTemp,
      fan_mid_fan: fanCurve.midFan,
      fan_full: fanCurve.full,
      updated: $scope.bsParseInt(profile.updated, Math.floor(Date.now() / 1000))
    };
  };

  $scope.bsFindClockProfileIndex = function(id) {
    if (!angular.isArray($scope.bsClockProfiles)) return -1;
    for (var i = 0; i < $scope.bsClockProfiles.length; i++) {
      if ($scope.bsClockProfiles[i] && $scope.bsClockProfiles[i].id === id) return i;
    }
    return -1;
  };

  $scope.bsSelectedClockProfile = function() {
    var idx = $scope.bsFindClockProfileIndex($scope.bsSelectedClockProfileId);
    return idx >= 0 ? $scope.bsClockProfiles[idx] : null;
  };

  $scope.bsClockProfileSummary = function(profile) {
    profile = $scope.bsNormalizeClockProfile(profile);
    return profile.name + ' - ' + (profile.scope === 'global' ? 'all boards' : 'individual') + ', ' +
           (profile.experimental ? 'Index ' + (profile.clock_indices || []).join('/') : 'Normal (Index 30)') + ', ' +
           profile.recover + '/' + profile.cutoff + ' C, fan ' +
           profile.fan_min + '% ' + profile.fan_target + ' C > ' +
           profile.fan_mid_fan + '% @ ' + profile.fan_mid_temp + ' C > 100% @ ' +
           profile.fan_full + ' C';
  };

  $scope.bsLoadClockProfiles = function() {
    $http.get('f_blakestream.php?action=load_clock_profiles').success(function(d) {
      if (d && d.error) Alertify.log.error('Clock profiles: ' + d.error);
      $scope.bsClockProfiles = (d && angular.isArray(d.profiles)) ? d.profiles : [];
      if ($scope.bsFindClockProfileIndex($scope.bsSelectedClockProfileId) < 0) {
        $scope.bsSelectedClockProfileId = '';
      }
    });
  };

  $scope.bsPersistClockProfiles = function(successText) {
    $http.post('f_blakestream.php?action=save_clock_profiles', {
      profiles: $scope.bsClockProfiles || []
    }).success(function(d) {
      if (!d || !d.ok) {
        Alertify.log.error('Clock profiles save failed');
        return;
      }
      if (d && angular.isArray(d.profiles)) {
        $scope.bsClockProfiles = d.profiles;
      }
      if (successText) Alertify.log.success(successText);
    }).error(function(d) {
      Alertify.log.error('Clock profiles save failed: ' + ((d && d.error) || 'request failed'));
    });
  };

  $scope.bsClockProfileSelectedChanged = function() {
    var profile = $scope.bsSelectedClockProfile();
    $scope.bsClockProfileName = profile ? profile.name : '';
  };

  $scope.bsNewClockProfile = function() {
    $scope.bsSelectedClockProfileId = '';
    $scope.bsClockProfileName = '';
  };

  $scope.bsCurrentClockProfile = function() {
    var prepared = $scope.bsPreparedClockSave();
    var fanCurve = $scope.bsNormalizeFanCurveValues({
      min: $scope.settings.userAutoFanMin,
      target: $scope.settings.userAutoFanTarget,
      midTemp: $scope.settings.userAutoFanMidTemp,
      midFan: $scope.settings.userAutoFanMidFan,
      full: $scope.settings.userAutoFanFull
    });
    return $scope.bsNormalizeClockProfile({
      mapping: $scope.bsClockMapping,
      experimental: !!$scope.bsExperimentalClockIndex.enabled,
      clock_indices: $scope.bsClockIndexValues(),
      name: $scope.bsClockProfileName,
      mode: $scope.bsClockRuntimeMode(),
      scope: $scope.bsClockMode,
      clock: prepared.clock,
      board_clocks: $scope.bsBoardClockArray($scope.bsBoardClocks, prepared.clock),
      recover: prepared.recover,
      cutoff: prepared.cutoff,
      fan_min: fanCurve.min,
      fan_target: fanCurve.target,
      fan_mid_temp: fanCurve.midTemp,
      fan_mid_fan: fanCurve.midFan,
      fan_full: fanCurve.full,
      updated: Math.floor(Date.now() / 1000)
    });
  };

  $scope.bsSaveClockProfile = function() {
    if ($scope.bsExperimentalClockIndex.enabled && !$scope.bsClockIndexValues()) {
      Alertify.log.error('Enter a clock index from 8 to 32 for each board');
      return;
    }
    var profile = $scope.bsCurrentClockProfile();
    var selectedIdx = $scope.bsFindClockProfileIndex($scope.bsSelectedClockProfileId);
    if (selectedIdx >= 0) {
      profile.id = $scope.bsClockProfiles[selectedIdx].id;
      $scope.bsClockProfiles[selectedIdx] = profile;
    } else {
      profile.id = $scope.bsCreateClockProfileId();
      $scope.bsClockProfiles.push(profile);
      if ($scope.bsClockProfiles.length > $scope.bsClockProfileLimit) {
        $scope.bsClockProfiles.shift();
      }
    }
    $scope.bsSelectedClockProfileId = profile.id;
    $scope.bsClockProfileName = profile.name;
    $scope.bsPersistClockProfiles('Clock profile saved: ' + profile.name);
  };

  $scope.bsDeleteClockProfile = function() {
    var profile = $scope.bsSelectedClockProfile();
    if (!profile) return;
    $scope.bsConfirm(
      'Delete clock profile <b>' + $scope.bsHtmlEscape(profile.name) + '</b>?',
      function() {
        var idx = $scope.bsFindClockProfileIndex(profile.id);
        if (idx >= 0) $scope.bsClockProfiles.splice(idx, 1);
        $scope.bsSelectedClockProfileId = '';
        $scope.bsClockProfileName = '';
        $scope.bsPersistClockProfiles('Clock profile deleted');
      },
      {title: 'Delete Clock Profile', yesLabel: 'Delete', noLabel: 'Cancel'}
    );
  };

  $scope.bsApplyClockProfile = function() {
    var profile = $scope.bsSelectedClockProfile();
    if (!profile) return;
    if ((profile.version !== undefined && profile.version !== 2) ||
        (profile.mapping !== undefined && profile.mapping !== $scope.bsClockMapping) ||
        (profile.scope !== undefined && profile.scope !== 'global' && profile.scope !== 'individual') ||
        (profile.experimental !== undefined && typeof profile.experimental !== 'boolean') ||
        (profile.experimental && (profile.version !== 2 || profile.mapping !== $scope.bsClockMapping ||
          profile.mode !== 'individual' || !angular.isArray(profile.clock_indices) ||
          profile.clock_indices.length !== 3 || !profile.clock_indices.every($scope.bsValidClockIndex) ||
          (profile.scope === 'global' && !profile.clock_indices.every(function(value) {
            return Number(value) === Number(profile.clock_indices[0]);
          }))))) {
      Alertify.log.error('This profile has an unsupported mapping or invalid clock indices');
      return;
    }
    profile = $scope.bsNormalizeClockProfile(profile);
    profile.clock_indices = profile.clock_indices.map(Number);
    var prepared = $scope.bsPrepareClockValues(profile.clock, profile.recover, profile.cutoff);
    var profileBoardClocks = $scope.bsBoardClockObject(profile.board_clocks, prepared.clock);
    var profileBoardValues = $scope.bsBoardClockArray(profileBoardClocks, prepared.clock);
    var applyProfile = function() {
      if (!$scope.bsMinerOptionsReady()) {
        Alertify.log.error('Miner options are still loading');
        return;
      }
      $scope.bsClock = {
        mhz: prepared.clock,
        recover: prepared.recover,
        cutoff: prepared.cutoff
      };
      $scope.bsClockMode = profile.scope;
      $scope.bsBoardClocks = profileBoardClocks;
      $scope.bsExperimentalClockIndex.enabled = profile.experimental;
      $scope.bsBoardClockIndex = {board0: profile.clock_indices[0], board1: profile.clock_indices[1], board2: profile.clock_indices[2]};
      $scope.bsGlobalClockIndex.index = profile.clock_indices.every(function(value) { return value === profile.clock_indices[0]; })
        ? profile.clock_indices[0] : 30;
      $scope.bsRefreshNominalClocks();
      $scope.settings.userAutoFanMin = profile.fan_min;
      $scope.settings.userAutoFanTarget = profile.fan_target;
      $scope.settings.userAutoFanMidTemp = profile.fan_mid_temp;
      $scope.settings.userAutoFanMidFan = profile.fan_mid_fan;
      $scope.settings.userAutoFanFull = profile.fan_full;
      $scope.bsEnsureClockModeOption(profile.mode);
      $scope.bsEnsureClockScopeOption(profile.scope);
      $scope.bsEnsureBaikalOption(prepared.value);
      $scope.bsEnsureBoardClockOption(profileBoardValues.join(':'));
      $scope.bsEnsureExperimentalOptions(profile.experimental, profile.clock_indices);
      $scope.sync('settings', $scope.settings, 1);
      $scope.sync('options',$scope.options,1);
      if ($scope.optionForm) $scope.optionForm.$setPristine();
      if ($scope.clockForm) $scope.clockForm.$setPristine();
      if ($scope.fanCurveForm) $scope.fanCurveForm.$setPristine();
    };
    $scope.bsConfirm($scope.bsClockPreview(profile.clock_indices, profile.experimental) +
      '<p>Apply saved temperature limits and fan curve with this profile?</p>' +
      (profile.experimental ? '<p>MHz estimates are performance-equivalent, not measured frequency.<br>Instability or hardware damage is possible.</p>' +
        (profile.scope === 'global' ? '<p>All boards receive the same setting through individual commands.</p>' : '') : ''),
      applyProfile, {title: 'Apply Clock Profile', yesLabel: 'Apply profile', noLabel: 'Cancel', lockBackdrop: true});
  };

  $scope.bsClampFanPercent = function(value, fallback) {
    var percent = $scope.bsParseInt(value, fallback);
    if (percent < $scope.bsFanCurveLimits.minFan) percent = $scope.bsFanCurveLimits.minFan;
    if (percent > $scope.bsFanCurveLimits.maxFan) percent = $scope.bsFanCurveLimits.maxFan;
    return percent;
  };

  $scope.bsClampFanTemp = function(value, fallback) {
    var temp = $scope.bsParseInt(value, fallback);
    if (temp < $scope.bsFanCurveLimits.minTemp) temp = $scope.bsFanCurveLimits.minTemp;
    if (temp > $scope.bsFanCurveLimits.maxTemp) temp = $scope.bsFanCurveLimits.maxTemp;
    return temp;
  };

  $scope.bsNormalizeFanCurveValues = function(values) {
    values = values || {};
    var fanMin = $scope.bsClampFanPercent(values.min, 40);
    var fanTarget = $scope.bsClampFanTemp(values.target, 36);
    var fanFull = $scope.bsClampFanTemp(values.full, 50);
    if (fanFull <= fanTarget + 1) {
      fanFull = Math.min($scope.bsFanCurveLimits.maxTemp, fanTarget + 2);
      if (fanFull <= fanTarget + 1) {
        fanTarget = Math.max($scope.bsFanCurveLimits.minTemp, fanFull - 2);
      }
    }
    var defaultMidTemp = Math.round(fanTarget + ((fanFull - fanTarget) / 2));
    var defaultMidFan = Math.max(fanMin, 75);
    var fanMidTemp = $scope.bsClampFanTemp(values.midTemp, defaultMidTemp);
    var fanMidFan = $scope.bsClampFanPercent(values.midFan, defaultMidFan);
    if (fanMidTemp <= fanTarget) fanMidTemp = fanTarget + 1;
    if (fanMidTemp >= fanFull) fanMidTemp = fanFull - 1;
    if (fanMidFan < fanMin) fanMidFan = fanMin;
    return {
      min: fanMin,
      target: fanTarget,
      midTemp: fanMidTemp,
      midFan: fanMidFan,
      full: fanFull
    };
  };

  $scope.bsNormalizeFanCurve = function() {
    if (!$scope.settings) return;
    var curve = $scope.bsNormalizeFanCurveValues({
      min: $scope.settings.userAutoFanMin,
      target: $scope.settings.userAutoFanTarget,
      midTemp: $scope.settings.userAutoFanMidTemp,
      midFan: $scope.settings.userAutoFanMidFan,
      full: $scope.settings.userAutoFanFull
    });
    $scope.settings.userAutoFanMin = curve.min;
    $scope.settings.userAutoFanTarget = curve.target;
    $scope.settings.userAutoFanMidTemp = curve.midTemp;
    $scope.settings.userAutoFanMidFan = curve.midFan;
    $scope.settings.userAutoFanFull = curve.full;
  };

  $scope.bsStageFanCurve = function() {
    if (!$scope.settings) return;
    if ($scope.settings.userAutoFanMin === undefined ||
        $scope.settings.userAutoFanTarget === undefined ||
        $scope.settings.userAutoFanMidTemp === undefined ||
        $scope.settings.userAutoFanMidFan === undefined ||
        $scope.settings.userAutoFanFull === undefined) return;
    $scope.bsNormalizeFanCurve();
  };

  $scope.bsSaveFanCurve = function() {
    if ($scope.fanCurveForm && $scope.fanCurveForm.$invalid) return;
    $scope.bsNormalizeFanCurve();
    $scope.sync('settings', $scope.settings, 1);
    if ($scope.fanCurveForm) $scope.fanCurveForm.$setPristine();
  };

  $scope.$on('bsOptionsLoaded', function() {
    $scope.bsRefreshClockFromOptions();
  });

  // Auto-load on controller init
  $scope.sync('options');
  $scope.bsLoadPools();
  $scope.bsLoadAssignments();
  $scope.bsLoadClockProfiles();

  $scope.optionAdd = function(a) {
    a = a || {};
    if (!angular.isArray($scope.options)) $scope.options = [];
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


.controller('CtrlSettings', function($scope,$window,$location) {
  $scope.status.extra=true;
  $scope.bsSettingsTab = $scope.bsViewTab('settings');
  $scope.$on('bsViewTabsReset', function() { $scope.bsSettingsTab = $scope.bsViewTab('settings'); });
  $scope.bsSelectSettingsTab = function(tab) {
    if (['scripta', 'mining', 'network', 'wifi'].indexOf(tab) < 0) return;
    $scope.bsSettingsTab = tab;
    $scope.bsRememberViewTab('settings', tab);
  };
  $scope.bsSelectSettingsTab($location.search().section);
  $scope.bsReloadSettingsTab = function() {
    if ($scope.bsSettingsTab === 'network') $scope.$broadcast('bsNetworkReload');
    else if ($scope.bsSettingsTab === 'wifi') $scope.$broadcast('bsWifiReload');
    else $scope.sync('settings', 0, 1);
  };
})


.controller('CtrlPoolHistory', function($scope, $http) {
  // Per-pool aggregate stats over a configurable window. Data is recorded
  // every 5 minutes by /opt/scripta/bin/pool-history-recorder.py. Storage
  // follows Settings > Persist History > Pool. Loaded via f_blakestream.php.
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

  $scope.bsClearPoolHistory = function() {
    var doClear = function() {
      $http.post('f_blakestream.php?action=clear_pool_history').success(function(d) {
        if (d && d.ok) {
          Alertify.log.success('Pool history cleared');
          $scope.bsLoadPoolHistory($scope.bsHistoryWindow);
        } else {
          Alertify.log.error('Clear failed: ' + ((d && d.error) || 'unknown'));
        }
      }).error(function() {
        Alertify.log.error('Clear failed');
      });
    };
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm('Clear all pool history? This cannot be undone.', doClear);
    } else {
      doClear();
    }
  };

  $scope.bsLoadPoolHistory(24);
})




.controller('CtrlFailoverHistory', function($scope, $http, $filter) {
  // Per-board failover events over a configurable window. Backing data is
  // written every minute by /opt/scripta/bin/failover-history-recorder.py.
  $scope.bsFailoverEvents = [];
  $scope.bsFailoverOpen = [];
  $scope.bsFailoverWindow = 24;
  $scope.bsFailoverUpdated = 0;

  $scope.bsLoadFailoverHistory = function(hours) {
    var h = hours || $scope.bsFailoverWindow || 24;
    $http.get('f_blakestream.php?action=load_failover_history&hours=' + h).success(function(d) {
      if (!d) return;
      $scope.bsFailoverEvents = d.events || [];
      $scope.bsFailoverOpen = d.open || [];
      $scope.bsFailoverWindow = d.window_hours || h;
      $scope.bsFailoverUpdated = d.updated || 0;
    }).error(function() {
      Alertify.log.error('Failed to load failover history');
    });
  };

  $scope.bsClearFailoverHistory = function() {
    var doClear = function() {
      $http.post('f_blakestream.php?action=clear_failover_history').success(function(d) {
        if (d && d.ok) {
          Alertify.log.success('Failover history cleared');
          $scope.bsLoadFailoverHistory($scope.bsFailoverWindow);
        } else {
          Alertify.log.error('Clear failed: ' + ((d && d.error) || 'unknown'));
        }
      }).error(function() {
        Alertify.log.error('Clear failed');
      });
    };
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm('Clear all failover history? This cannot be undone.', doClear);
    } else {
      doClear();
    }
  };

  // Currently-active (in-progress) duration in seconds for an open event.
  $scope.bsFailoverOpenDuration = function(evt) {
    if (!evt || !evt.start_ts) return 0;
    return Math.max(0, Math.floor(Date.now() / 1000) - evt.start_ts);
  };

  // Build CSV from the in-memory event list and trigger a browser download.
  // Pulls from the same arrays the table renders, so it works regardless of
  // RAM-vs-SD storage location for the source file.
  $scope.bsExportFailoverHistory = function() {
    var fmtTs = function(ts) {
      if (!ts) return '';
      if (typeof BlakestreamTZ !== 'undefined') {
        return BlakestreamTZ.format(1000 * ts, 'yyyy-MM-dd HH:mm:ss');
      }
      return new Date(1000 * ts).toISOString();
    };
    var fmtDur = $filter('duration');
    var esc = function(v) {
      var s = (v == null) ? '' : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    var rows = [['Board','Failed Over From','Failed Over To','Started','Ended','Duration']];
    angular.forEach($scope.bsFailoverOpen || [], function(e) {
      rows.push([e.board_id,
                 e.primary_pool_name || e.primary_pool_url || '',
                 e.effective_pool_name || e.effective_pool_url || '',
                 fmtTs(e.start_ts),
                 'in progress',
                 fmtDur($scope.bsFailoverOpenDuration(e))]);
    });
    angular.forEach($scope.bsFailoverEvents || [], function(e) {
      rows.push([e.board_id,
                 e.primary_pool_name || e.primary_pool_url || '',
                 e.effective_pool_name || e.effective_pool_url || '',
                 fmtTs(e.start_ts),
                 fmtTs(e.end_ts),
                 fmtDur(e.duration_seconds)]);
    });
    var csv = rows.map(function(r) { return r.map(esc).join(','); }).join('\n');
    var blob = new Blob([csv], {type: 'text/csv;charset=utf-8'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'failover-history-' + new Date().toISOString().slice(0,10) + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  $scope.bsLoadFailoverHistory(24);
})


.controller('CtrlClockHistory', function($scope, $http, $filter, $timeout) {
  // Per-board clock diagnostics written every minute by clock-history-recorder.py
  // while Settings -> Adjustable Clocks is enabled.
  $scope.bsClockRuns = [];
  $scope.bsClockWindow = 24;
  $scope.bsClockUpdated = 0;
  $scope.bsClockStepSeconds = 60;
  var bsClockHistoryTimer = null;

  var bsScheduleClockHistoryRefresh = function() {
    if (bsClockHistoryTimer) {
      $timeout.cancel(bsClockHistoryTimer);
    }
    bsClockHistoryTimer = $timeout(function() {
      bsClockHistoryTimer = null;
      $scope.bsLoadClockHistory($scope.bsClockWindow || 24);
    }, 60000);
  };

  $scope.$on('$destroy', function() {
    if (bsClockHistoryTimer) {
      $timeout.cancel(bsClockHistoryTimer);
      bsClockHistoryTimer = null;
    }
  });

  $scope.bsLoadClockHistory = function(hours) {
    var h = hours || $scope.bsClockWindow || 24;
    $http.get('f_blakestream.php?action=load_clock_history&hours=' + h).success(function(d) {
      if (!d) {
        bsScheduleClockHistoryRefresh();
        return;
      }
      $scope.bsClockRuns = d.runs || [];
      $scope.bsClockWindow = d.window_hours || h;
      $scope.bsClockUpdated = d.updated || 0;
      $scope.bsClockStepSeconds = d.step_seconds || 60;
      bsScheduleClockHistoryRefresh();
    }).error(function() {
      Alertify.log.error('Failed to load clock history');
      bsScheduleClockHistoryRefresh();
    });
  };

  $scope.bsClearClockHistory = function() {
    var doClear = function() {
      $http.post('f_blakestream.php?action=clear_clock_history').success(function(d) {
        if (d && d.ok) {
          Alertify.log.success('Clock history cleared');
          $scope.bsLoadClockHistory($scope.bsClockWindow);
        } else {
          Alertify.log.error('Clear failed: ' + ((d && d.error) || 'unknown'));
        }
      }).error(function() {
        Alertify.log.error('Clear failed');
      });
    };
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm('Clear all clock history? This cannot be undone.', doClear);
    } else {
      doClear();
    }
  };

  $scope.bsClearClockRun = function(run) {
    if (!run) return;
    var doClear = function() {
      $http.post('f_blakestream.php?action=clear_clock_run', {
        start_ts: run.start_ts || 0,
        end_ts: run.end_ts || run.start_ts || 0,
        profile_key: run.profile_key || '',
        run_key: run.run_key || ''
      }).success(function(d) {
        if (d && d.ok) {
          Alertify.log.success('Clock run cleared');
          $scope.bsLoadClockHistory($scope.bsClockWindow);
        } else {
          Alertify.log.error('Clear failed: ' + ((d && d.error) || 'unknown'));
        }
      }).error(function() {
        Alertify.log.error('Clear failed');
      });
    };
    var label = (run.active ? 'current' : 'previous') + ' ' +
      (run.experimental ? 'experimental Index' : (run.clock || '') + ' MHz') + ' clock run';
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm('Clear this ' + $scope.bsHtmlEscape(label) + '? This cannot be undone.', doClear, {
        yesLabel: 'Clear'
      });
    } else {
      doClear();
    }
  };

  $scope.bsClockSampleRows = function() {
    var rows = [];
    angular.forEach($scope.bsClockRuns || [], function(run) {
      angular.forEach(run.boards || [], function(board) {
        angular.forEach(board.samples || [], function(s) {
          rows.push({
            run_id: run.id,
            run_active: run.active ? 'yes' : 'no',
            run_start: run.start_ts || 0,
            run_end: run.end_ts || 0,
            run_clock: run.clock || '',
            run_recover: run.recover || '',
            run_cutoff: run.cutoff || '',
            run_fan: run.fan || '',
            run_fan_mode: run.fan_mode || '',
            board_id: board.id,
            t: s.t || 0,
            reported_clock: s.c || '',
            requested_clock: s.xp ? '' : (s.rc || ''),
            configured_clock: s.xp ? '' : (s.cc || ''),
            experimental: s.xp ? 'yes' : 'no',
            requested_clock_index: s.xp ? s.pi : '',
            reported_clock_code: s.fcc || '',
            sent_clock_index: s.scc || '',
            command_verified: s.cv ? 'yes' : 'no',
            clock_mode: s.cm || 'global',
            recover: s.rec || run.recover || '',
            cutoff: s.cut || run.cutoff || '',
            fan: s.fan || '',
            fan_mode: s.fm || board.fan_mode || run.fan_mode || '',
            temp: s.tmp || '',
            mhs5s: s.xp ? '' : (s.m5 || ''),
            mhsav: s.xp ? '' : (s.ma || ''),
            accepted: s.a || 0,
            rejected: s.r || 0,
            hw: s.hw || 0,
            utility: s.u || '',
            primary_pool_no: s.pp,
            effective_pool_no: s.ep,
            failover_active: s.fo ? 'yes' : 'no',
            status: s.st || '',
            last_share_time: s.ls || 0,
            pool_url: s.pool || ''
          });
        });
      });
    });
    rows.sort(function(a, b) {
      if (a.t === b.t) return a.board_id - b.board_id;
      return a.t - b.t;
    });
    return rows;
  };

  $scope.bsExportClockHistory = function() {
    var fmtTs = function(ts) {
      if (!ts) return '';
      if (typeof BlakestreamTZ !== 'undefined') {
        return BlakestreamTZ.format(1000 * ts, 'yyyy-MM-dd HH:mm:ss');
      }
      return new Date(1000 * ts).toISOString();
    };
    var esc = function(v) {
      var s = (v == null) ? '' : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    var includeDevFields = !!($scope.settings && $scope.settings.userDev);
    var headers = [
      'Time','Run','Run Active','Run Start','Run End','Board',
      'Clock Mode','Requested MHz','Configured MHz','Experimental Index','Requested clock index','Command Verified'
    ];
    if (includeDevFields) {
      headers.push('Bridge Status Echo MHz', 'Bridge Status Echo Code', 'Sent Clock Index');
    }
    headers = headers.concat([
      'Recover Temp','Cutoff Temp','Fan %','Fan Mode','Temp C',
      'Hashrate 5s MH/s','Hashrate Avg MH/s','Accepted','Rejected',
      'HW Errors','Utility','Primary Pool','Effective Pool','Failover',
      'Status','Last Share Time','Pool URL'
    ]);
    var rows = [headers];
    angular.forEach($scope.bsClockSampleRows(), function(r) {
      var row = [
        fmtTs(r.t), r.run_id, r.run_active, fmtTs(r.run_start), fmtTs(r.run_end), r.board_id,
        r.clock_mode, r.requested_clock, r.configured_clock, r.experimental, r.requested_clock_index, r.command_verified
      ];
      if (includeDevFields) {
        row.push(r.reported_clock, r.reported_clock_code, r.sent_clock_index);
      }
      row = row.concat([
        r.recover, r.cutoff, r.fan, r.fan_mode, r.temp,
        r.mhs5s, r.mhsav, r.accepted, r.rejected, r.hw, r.utility,
        r.primary_pool_no, r.effective_pool_no, r.failover_active,
        r.status, fmtTs(r.last_share_time), r.pool_url
      ]);
      rows.push(row);
    });
    var csv = rows.map(function(r) { return r.map(esc).join(','); }).join('\n');
    var blob = new Blob([csv], {type: 'text/csv;charset=utf-8'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'clock-history-' + new Date().toISOString().slice(0,10) + '.csv';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  $scope.bsLoadClockHistory(24);
})


.controller('CtrlDevTools', function($scope, $http, $timeout) {
  var bsDevTabs = {boards: true, logs: true, network: true, api: true, firmware: true};
  function bsDevTabLoad() {
    return $scope.bsViewTab('dev');
  }
  function bsDevTabSave(tab) {
    if (!bsDevTabs[tab]) return;
    $scope.bsRememberViewTab('dev', tab);
  }
  function bsDevTabFirmware() {
    $scope.devTab = 'firmware';
    bsDevTabSave('firmware');
  }
  $scope.devTab = bsDevTabLoad();
  $scope.$on('bsViewTabsReset', function() { $scope.devTab = bsDevTabLoad(); });

  // ---------- Logs panel ----------
  $scope.devLogSources = [
    {id: 'sgminer',         label: 'sgminer'},
    {id: 'network_events',  label: 'Network / Wi-Fi'},
    {id: 'system_events',   label: 'System events'},
    {id: 'cooling_events',  label: 'Fan / temperature'},
    {id: 'recorder',        label: 'recorder syslog (bs-*)'},
    {id: 'syslog',          label: 'syslog (full)'},
    {id: 'kern',            label: 'kern.log'},
    {id: 'daemon',          label: 'daemon.log'},
    {id: 'auth',            label: 'auth.log'},
    {id: 'ssh',             label: 'ssh.log'},
    {id: 'lighttpd',        label: 'Web server errors'},
    {id: 'lighttpd_access', label: 'lighttpd access'},
    {id: 'temp_state',      label: 'temp-state.json'}
  ];
  // Dropdown list = base sources + a transient "viewed file" entry (storage view).
  $scope.devLogSourceList = $scope.devLogSources.slice();
  $scope.devLogTargetOptions = ['SD', 'RAM'];
  $scope.devLogRamCap = '4 MiB';

  // ---------- Logs -> Storage panel (per-file size + RAM/SD headroom) ----------
  $scope.devStorage = {ram: [], sd: [], unified: [], ram_total: 0, sd_total: 0, ram_df: null, sd_df: null, updated: 0, loading: false, error: ''};
  $scope.devStoragePrev = {};   // path -> previous {size} for growth markers
  $scope.devMeasured = {contexts: [], boards: {}};
  $scope.devMeasuredCsrf = '';
  $scope.devMeasuredBusy = false;
  $scope.devMeasuredVisible = false;
  $scope.devMeasuredFilter = {board: 'all'};
  var devStorageRequest = 0;

  $scope.devMeasuredRows = function() {
    var board = $scope.devMeasuredFilter.board;
    return ($scope.devMeasured.contexts || []).filter(function(c) {
      return board === 'all' || (c.context && String(c.context.board) === board);
    });
  };
  $scope.devMeasuredDownload = function(format) {
    return 'f_dev.php?action=measured_rates_download&format=' + encodeURIComponent(format) +
      '&board=' + encodeURIComponent($scope.devMeasuredFilter.board);
  };
  $scope.devMeasuredUpdate = function(op, argument) {
    if ($scope.devMeasuredBusy || devToolsDestroyed) return;
    $scope.devMeasuredBusy = true;
    devStorageRequest++;
    $http.post('f_dev.php?action=measured_rates_update', {
      op: op, argument: argument || '', csrf: $scope.devMeasuredCsrf || ($scope.devActivity && $scope.devActivity.csrf) || ''
    }).success(function(d) {
      if (devToolsDestroyed) return;
      $scope.devMeasuredBusy = false;
      if (!d || !d.ok) Alertify.log.error((d && d.error) || 'Measured rates update failed');
      else if (op === 'reset-board') Alertify.log.success('Board ' + argument + ' measured history reset');
      $scope.devStorageLoad();
    }).error(function() {
      if (devToolsDestroyed) return;
      $scope.devMeasuredBusy = false;
      Alertify.log.error('Measured rates request failed');
      $scope.devStorageLoad();
    });
  };
  $scope.devMeasuredClear = function() {
    $scope.bsConfirm('<p>Clear all personal measured-rate history and buffered measurements? Current rate windows will restart. Miner counters, clocks and lab references stay unchanged.</p>',
      function() { $scope.devMeasuredUpdate('clear'); },
      {title: 'Clear Measured Rates', yesLabel: 'Clear data', noLabel: 'Cancel'});
  };
  $scope.devMeasuredResetBoard = function(board) {
    if ([0, 1, 2].indexOf(board) < 0 || $scope.devMeasuredBusy) return;
    $scope.bsConfirm('<p>Register a replacement for Board ' + board + '? This clears its personal measured-rate history and current rate windows. This cannot be undone.</p>' +
      '<p>Other boards stay unchanged. Mining, miner counters, clock settings and lab references are not reset.</p>',
      function() { $scope.devMeasuredUpdate('reset-board', String(board)); },
      {title: 'Board ' + board + ' Replaced', yesLabel: 'Reset board history', noLabel: 'Cancel'});
  };

  $scope.bsHumanBytes = function (n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    var u = ['KiB', 'MiB', 'GiB', 'TiB'], i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return (n >= 100 ? Math.round(n) : n.toFixed(1)) + ' ' + u[i];
  };
  $scope.devCapPct = function (f) {
    if (!f || !f.cap_bytes) return 0;
    return Math.min(100, Math.round(f.size / f.cap_bytes * 100));
  };
  $scope.devDfClass = function (df) {
    if (!df) return '';
    if (df.pct >= 92) return 'danger';
    if (df.pct >= 80) return 'warning';
    return '';
  };
  $scope.devStorageDisplayName = function (name) {
    var names = {'sgminer.log': 'sgminer', 'network-events.log': 'Network / Wi-Fi',
      'system-events.log': 'System events', 'cooling-events.log': 'Fan / temperature',
      'lighttpd-error.log': 'Web server errors'};
    return names[name] || name;
  };
  $scope.devLogsRefresh = function () {
    if (devToolsDestroyed) return;
    $scope.devStorageLoad();
    $scope.devRecordersRefresh();
  };
  $scope.devStorageLoad = function () {
    var request = ++devStorageRequest;
    $scope.devStorage.loading = true;
    $http.get('f_dev.php?action=storage').success(function (d) {
      if (devToolsDestroyed || request !== devStorageRequest) return;
      $scope.devStorage.loading = false;
      if (!d || !d.ok) { $scope.devStorage.error = (d && d.error) || 'storage read failed'; return; }
      if (devToolsDestroyed) return;
      $scope.devMeasured = d.measured || {contexts: [], boards: {}};
      $scope.devMeasuredCsrf = d.csrf || '';
      $scope.devStorage.error = '';
      var prev = $scope.devStoragePrev, next = {};
      angular.forEach([].concat(d.ram, d.sd), function (f) {
        next[f.path] = f.size;
        f.grew = (prev[f.path] !== undefined && f.size > prev[f.path]) ? (f.size - prev[f.path]) : 0;
      });
      $scope.devStoragePrev = next;
      // merge into one row per logical file: RAM + SD side by side, a single
      // Save-to selector. lighttpd's SD name has a slash; normalize to match RAM.
      var byName = {}, order = [];
      var addSide = function (side, f) {
        var key = (f.name || '').replace('lighttpd/', 'lighttpd-');
        if (!byName[key]) { byName[key] = {name: key, ram: null, sd: null, target_key: null, target: 'RAM'}; order.push(key); }
        byName[key][side] = f;
        if (f.recorder_id) byName[key].recorder_id = f.recorder_id;
        if (f.target_key) { byName[key].target_key = f.target_key; byName[key].target = f.target; }
      };
      angular.forEach(d.ram, function (f) { addSide('ram', f); });
      angular.forEach(d.sd, function (f) { addSide('sd', f); });
      $scope.devStorage.unified = order.map(function (k) { return byName[k]; });
      $scope.devStorage.ram = d.ram;
      $scope.devStorage.sd = d.sd;
      $scope.devStorage.ram_total = d.ram_total_bytes;
      $scope.devStorage.sd_total = d.sd_total_bytes;
      $scope.devStorage.ram_df = d.ram_df;
      $scope.devStorage.sd_df = d.sd_df;
      $scope.devStorage.updated = d.generated_at;
    }).error(function () {
      if (devToolsDestroyed || request !== devStorageRequest) return;
      $scope.devStorage.loading = false;
      $scope.devStorage.error = 'storage request failed';
    });
  };
  // per-row RAM/SD selector: writes the file's location setting; the recorder
  // moves it (and the old-location cleanup deletes the stale copy) next run.
  $scope.devStorageSetTarget = function (f) {
    if (!f || !f.target_key || f.saving) return;
    if (f.target_key === 'measured-rates') {
      var target = f.target;
      f.target = $scope.devMeasured.target || 'RAM';
      if (target === f.target) return;
      $scope.bsConfirm(target === 'RAM'
        ? '<p>Move measured-rate history to RAM and retire its SD copy? This history will be lost on reboot.</p>'
        : '<p>Move current measured-rate history to the SD card?</p>',
        function() { $scope.devMeasuredUpdate('target', target); },
        {title: 'Move Measured Rates', yesLabel: 'Move history', noLabel: 'Cancel'});
      return;
    }
    f.saving = true;
    $http.post('f_dev.php?action=storage_set_target', {file: f.target_key, target: f.target, csrf: $scope.devMeasuredCsrf}).success(function (d) {
      f.saving = false;
      if (devToolsDestroyed) return;
      if (d && !d.ok && typeof Alertify !== 'undefined' && Alertify.log) Alertify.log.error(d.error || 'target change failed');
      if (d && d.ok && /^user(Dev|Network|System|Cooling|WebError)LogTarget$/.test(d.key || '')) {
        if ($scope.settings) $scope.settings[d.key] = d.target;
        if ($scope.bsSaveUserSettingsBaselineField) $scope.bsSaveUserSettingsBaselineField(d.key);
        var source = $scope.devLog.source || '';
        if ($scope.devLog.mode === 'log' && (source === 'path:/run/blakestream/' + f.target_key || source === 'path:/opt/scripta/var/' + f.target_key)) {
          $scope.devStorageView({name: f.target_key, present: true, clearable: true,
            path: (d.target === 'SD' ? '/opt/scripta/var/' : '/run/blakestream/') + f.target_key});
        } else if ($scope.devLog.mode === 'log' && ((f.ram && source === f.ram.view) || (f.sd && source === f.sd.view))) {
          $scope.devLogReset();
        }
      }
      $timeout($scope.devStorageLoad, 1500);
    }).error(function () {
      f.saving = false;
      if (devToolsDestroyed) return;
      if (typeof Alertify !== 'undefined' && Alertify.log) Alertify.log.error('Log destination update failed');
      $scope.devStorageLoad();
    });
  };
  $scope.devStorageClearAll = function (side) {
    var sd = (side === 'SD');
    var msg = sd
      ? '<p>Clear all clearable files on the <b>SD card</b> (logs + history) and prune <code>security.log</code> to the last 7 days?</p>'
      : '<p>Clear all clearable files in <b>RAM</b> (logs + history)?</p>';
    if ($scope.devMeasured.target === side) {
      msg += '<p>Measured Rates and its pending buffer will also be cleared. Current rate windows will restart.</p>';
    } else if (!sd && $scope.devMeasured.target === 'SD') {
      msg += '<p>Pending measured rates will be saved to SD first; SD measurement history stays intact. Current rate windows will restart.</p>';
    }
    var go = function () {
      $http.post('f_dev.php?action=storage_clear_all', {side: side, csrf: $scope.devMeasuredCsrf}).success(function (d) {
        if (typeof Alertify !== 'undefined' && Alertify.log) {
          if (d && d.ok) {
            var m = 'Cleared ' + d.cleared + ' ' + side + ' file(s)';
            if (d.pruned) m += '; pruned security.log (kept ' + d.pruned.kept + ', removed ' + d.pruned.removed + ')';
            Alertify.log.success(m);
          } else { Alertify.log.error('Clear all had errors'); }
        }
        $scope.devStorageLoad();
      });
    };
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm(msg, go, {title: 'Clear all ' + side + ' files', yesLabel: 'Clear All', noLabel: 'Cancel'});
    } else if (window.confirm('Clear all ' + side + ' files?')) { go(); }
  };
  // "view" selects the file in the EXISTING log viewer below (no separate pane).
  $scope.devStorageView = function (f) {
    if (!f || !f.present) return;
    $scope.devLog.paused = false;
    $scope.devMeasuredVisible = f.view === 'measured-rates';
    $scope.devLogReset();
    if ($scope.devMeasuredVisible) return;
    if (f.view === 'sgminer') {
      $scope.devLogSourceList = $scope.devLogSources.slice();
      $scope.devLog.source = 'sgminer';
      $scope.devLogReset();
      return;
    }
    var where = (f.path.indexOf('/run/') === 0) ? 'RAM' : 'SD';
    var id = 'path:' + f.path;
    $scope.devLogSourceList = $scope.devLogSources.slice();
    $scope.devLogSourceList.push({
      id: id,
      label: $scope.devStorageDisplayName(f.name) + ' (' + where + ')',
      clearable: !!f.clearable,
      history: /(?:hashrate-history|pool-history|failover-events|clock-history).*\.json$/.test(f.name)
    });
    $scope.devLog.source = id;
    $scope.devLogReset();
  };
  $scope.devStorageClear = function (f) {
    if (!f || !f.clearable) return;
    if (f.view === 'measured-rates') { $scope.devMeasuredClear(); return; }
    var go = function () {
      $http.post('f_dev.php?action=storage_clear', {path: f.path}).success(function (d) {
        if (d && d.ok) { $scope.devStorageLoad(); }
        else if (typeof Alertify !== 'undefined' && Alertify.log) Alertify.log.error((d && d.error) || 'clear failed');
      });
    };
    var onSd = (f.path.indexOf('/run/') !== 0);
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm(
        '<p>Truncate <code>' + $scope.bsHtmlEscape(f.path) + '</code> to 0 bytes?</p>' +
        (onSd ? '<p>This file is on the <b>SD card</b>.</p>' : ''),
        go, {title: 'Clear log', yesLabel: 'Clear', noLabel: 'Cancel'});
    } else if (window.confirm('Truncate ' + f.path + ' to 0 bytes?' + (onSd ? ' (on the SD card)' : ''))) {
      go();
    }
  };
  // Storage keeps its own cadence. Board health is read on the activity poller
  // so the unified Boards tab does not run a second timer.
  $scope.devStorageGen = 0;

  $scope.devActivityUnits = [0, 1, 2, 3];
  $scope.devActivityChips = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15];
  $scope.devActivityBoardIds = [0, 1, 2];
  $scope.devActivity = {
    board: 'all',
    mode: 'totals',
    boards: [],
    deltas: {},
    baseline: null,
    trace: {},
    support: '',
    traceApi: false,
    csrf: '',
    state: 'loading',
    error: '',
    loading: false,
    busy: false,
    updated: 0
  };
  $scope.devBoards = {rows: [], updated: 0, error: '', loading: false};
  var devHealthGen = 0;
  var devToolsDestroyed = false;
  function devHealthDrop() {
    devHealthGen++;
    if (devToolsDestroyed) return;
    $scope.devBoards.loading = false;
  }
  function devBoardsLoad() {
    if (devToolsDestroyed || $scope.devTab !== 'boards') return;
    var gen = ++devHealthGen;
    $scope.devBoards.loading = true;
    $http.get('f_dev.php?action=board_health').success(function (d) {
      // A newer read, a tab leave, or controller destroy owns the screen.
      if (devToolsDestroyed || gen !== devHealthGen || $scope.devTab !== 'boards') return;
      $scope.devBoards.loading = false;
      if (d && d.ok) {
        $scope.devBoards.rows = d.boards;
        $scope.devBoards.updated = d.generated_at;
        $scope.devBoards.error = '';
      } else {
        $scope.devBoards.error = (d && d.error) || 'no board data';
      }
    }).error(function () {
      if (devToolsDestroyed || gen !== devHealthGen || $scope.devTab !== 'boards') return;
      $scope.devBoards.loading = false;
      $scope.devBoards.error = 'request failed';
    });
  }
  $scope.devBoardsLoad = devBoardsLoad;
  function devActivityApply(d, err) {
    if (err || !d) {
      $scope.devActivity.state = 'failed';
      $scope.devActivity.error = err || 'activity request failed';
      return;
    }
    if (d.csrf) $scope.devActivity.csrf = d.csrf;
    if (d.support) $scope.devActivity.support = d.support;
    if (d.trace_api !== undefined) $scope.devActivity.traceApi = !!d.trace_api;
    if (d.ok !== false && d.support !== 'unreachable' && angular.isArray(d.boards) && window.bsDevActivity) {
      var win = window.bsDevActivity.deltaWindow($scope.devActivity.baseline, d.boards);
      $scope.devActivity.boards = d.boards;
      $scope.devActivity.trace = d.trace || {};
      $scope.devActivity.updated = d.generated_at || 0;
      $scope.devActivity.baseline = win.baseline;
      $scope.devActivity.deltas = win.deltas;
      $scope.devActivity.state = window.bsDevActivity.viewState(d);
      $scope.devActivity.error = '';
      return;
    }
    $scope.devActivity.state = 'failed';
    $scope.devActivity.error = d.error || err || 'activity request failed';
  }
  var devActivityPoller = (window.bsDevActivity && window.bsDevActivity.createPoll) ? window.bsDevActivity.createPoll({
    schedule: function (fn, ms) { return $timeout(fn, ms); },
    cancel: function (timer) { $timeout.cancel(timer); },
    request: function (done) {
      $scope.devActivity.loading = true;
      devBoardsLoad();
      $http.get('f_dev.php?action=activity')
        .success(function (d) { done(d, null); })
        .error(function () { done(null, 'activity request failed'); });
    },
    apply: function (d, err) {
      if (devToolsDestroyed || $scope.devTab !== 'boards') return;
      $scope.devActivity.loading = false;
      devActivityApply(d, err);
    },
    onStart: function () {
      $scope.devActivity.baseline = null;
      $scope.devActivity.deltas = {};
      $scope.devActivity.state = 'loading';
      $scope.devActivity.error = '';
    }
  }) : null;
  function devActivityPoll() {
    if (!devActivityPoller) {
      $scope.devActivity.state = 'failed';
      $scope.devActivity.error = 'activity helper missing';
      devBoardsLoad();
      return;
    }
    devActivityPoller.poll();
  }
  function devActivityStart() {
    if (!devActivityPoller) {
      $scope.devActivity.state = 'failed';
      $scope.devActivity.error = 'activity helper missing';
      devBoardsLoad();
      return;
    }
    devActivityPoller.start();
  }
  function devActivityStop() {
    devHealthDrop();
    if (devActivityPoller) devActivityPoller.leave();
  }
  function devActivityMutate(op, done) {
    if ($scope.devActivityLocked()) return;
    $scope.devActivity.busy = true;
    $http.post('f_dev.php?action=activity_trace', {csrf: $scope.devActivity.csrf, op: op})
      .success(function (d) {
        if (!d || d.ok === false) {
          $scope.devActivity.busy = false;
          $scope.devActivity.state = 'failed';
          $scope.devActivity.error = (d && d.error) || 'trace request failed';
          return;
        }
        if (d.csrf) $scope.devActivity.csrf = d.csrf;
        if (done) done(d);
        else {
          $scope.devActivity.busy = false;
          $scope.devActivity.error = '';
          devActivityPoll();
        }
      })
      .error(function () {
        $scope.devActivity.busy = false;
        $scope.devActivity.state = 'failed';
        $scope.devActivity.error = 'trace request failed';
      });
  }
  function devActivityDownload() {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', 'f_dev.php?action=activity_trace', true);
    xhr.responseType = 'blob';
    xhr.setRequestHeader('Content-Type', 'application/json');
    if (typeof window.BKB_CSRF_TOKEN === 'string') xhr.setRequestHeader('X-BKB-CSRF', window.BKB_CSRF_TOKEN);
    xhr.onload = function () {
      var type = xhr.getResponseHeader('Content-Type') || '';
      $scope.$apply(function () {
        $scope.devActivity.busy = false;
        if (xhr.status !== 200 || type.indexOf('application/json') !== -1) {
          $scope.devActivity.state = 'failed';
          $scope.devActivity.error = 'trace download failed';
          return;
        }
        var url = (window.URL || window.webkitURL).createObjectURL(xhr.response);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'bkb-diag-trace.bin';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        (window.URL || window.webkitURL).revokeObjectURL(url);
        devActivityPoll();
      });
    };
    xhr.onerror = function () {
      $scope.$apply(function () {
        $scope.devActivity.busy = false;
        $scope.devActivity.state = 'failed';
        $scope.devActivity.error = 'trace download failed';
      });
    };
    xhr.send(JSON.stringify({csrf: $scope.devActivity.csrf, op: 'download'}));
  }
  $scope.devActivityRefresh = function () { devActivityPoll(); };
  $scope.devActivityShowBoard = function (b) {
    if (!b) return false;
    if ($scope.devActivity.board === 'all') return true;
    return $scope.devActivity.board === b.id || String($scope.devActivity.board) === String(b.id);
  };
  $scope.devActivityNum = function (v) {
    if (!window.bsDevActivity) return '\u2014';
    var s = window.bsDevActivity.subCount(v, '0');
    return s === null ? '\u2014' : s;
  };
  $scope.devActivityYn = function (v) {
    if (v === true || v === 1 || v === '1') return 'Yes';
    if (v === false || v === 0 || v === '0') return 'No';
    return '\u2014';
  };
  $scope.devActivityCell = function (b, kind, u, c) {
    var grid;
    var row;
    if ($scope.devActivity.mode === 'deltas') {
      row = $scope.devActivity.deltas[b.id];
      grid = row ? row[kind] : null;
    } else {
      grid = b ? b[kind] : null;
    }
    if (!grid || !grid[u]) return '\u2014';
    return $scope.devActivityNum(grid[u][c]);
  };
  $scope.devActivityTiming = function (b, field) {
    if (!window.bsDevActivity || !b) return '\u2014';
    return window.bsDevActivity.timingText($scope.devActivity.mode, b, $scope.devActivity.deltas[b.id], field);
  };
  $scope.devActivityCounter = function (b, group, field) {
    var src;
    if ($scope.devActivity.mode === 'deltas') {
      if (group === 'timing' && field === 'poll_gap_us_last') return '\u2014';
      src = $scope.devActivity.deltas[b.id];
      if (!src || !src[group]) return '\u2014';
      return $scope.devActivityNum(src[group][field]);
    }
    if (!b || !b[group]) return '\u2014';
    return $scope.devActivityNum(b[group][field]);
  };
  $scope.devActivityLocked = function () {
    if (!window.bsDevActivity || !window.bsDevActivity.traceLocked) return true;
    return window.bsDevActivity.traceLocked($scope.devActivity);
  };
  $scope.devActivityTraceToggle = function (ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    var on = $scope.devActivity.trace && $scope.devActivity.trace.enabled === true;
    if (ev && ev.target) ev.target.checked = on;
    if ($scope.devActivityLocked()) return;
    devActivityMutate(on ? 'disable' : 'enable');
  };
  $scope.devActivityTrace = function (op) {
    if (op !== 'enable' && op !== 'disable') return;
    devActivityMutate(op);
  };
  $scope.devActivityClear = function () {
    if ($scope.devActivityLocked()) return;
    var go = function () { devActivityMutate('clear'); };
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm('<p>Clear the trace?</p>', go, {title: 'Clear trace', yesLabel: 'Clear', noLabel: 'Cancel'});
    } else if (window.confirm('Clear the trace?')) {
      go();
    }
  };
  $scope.devActivityExport = function () {
    devActivityMutate('export', function () { devActivityDownload(); });
  };

  $scope.$watch('devTab', function (tab) {
    if (tab === 'activity') {
      devActivityStop();
      $scope.devTab = 'boards';
      return;
    }
    if (!bsDevTabs[tab]) {
      $scope.devTab = 'boards';
      return;
    }
    bsDevTabSave(tab);
    $scope.devStorageGen++;
    if (tab === 'boards') devActivityStart();
    else devActivityStop();
    if (tab === 'logs') {
      var gen = $scope.devStorageGen;
      var tick = function () {
        if (gen !== $scope.devStorageGen) return;
        $scope.devLogsRefresh();
        $timeout(tick, 60000);
      };
      tick();
    }
    if (tab === 'firmware') $scope.devFirmwareStatus();
  });

  // ---------- Firmware tab (bridge-chip identity + reversible revert) ----------
  $scope.devFirmware = $scope.devFirmware || {};
  angular.extend($scope.devFirmware, angular.extend({status: null, verifying: false, reverting: false, dryOk: false, dryMsg: '', confirmText: '', output: '', error: '',
    flashingCustom: false, dryCustomOk: false, dryCustomMsg: '', enableText: '', enableOutput: '', enableError: '',
    flashRuntimeLabel: '~164s'}, $scope.devFirmware));
  // While a DFU op runs, poll the on-device progress log + tick an elapsed timer
  // so the terminal overlay shows live stages and never looks frozen.
  $scope.devFwProgressPoll = function () {
    $scope.bsFirmwarePollJob();
  };
  $scope.devFwProgressStart = function (initialText) {
    $scope.devFwProgress = {text: initialText || '', startMs: Date.now(), elapsed: 0, cancelable: false};
  };
  $scope.devFirmwareStatus = function () {
    $http.get('f_dev.php?action=firmware_status').success(function (d) {
      if (d && d.ok) {
        $scope.devFirmware.status = d;
        $scope.devFirmware.flashRuntimeLabel = d.flash_runtime_label || '~164s';
      }
    });
  };
  $scope.devFirmwareVerify = function () {
    bsDevTabFirmware();
    var go = function () {
      $scope.devFirmware.error = '';
      $scope.bsFirmwareStartJob('verify', {}, 'Reading bridge-chip firmware...', function () {
        $scope.bsSettingsFirmwareOverlayClose();
        $scope.devFirmwareStatus();
      }, function (d) {
        $scope.bsSettingsFirmwareOverlayClose();
        var result = d && d.result ? d.result : {};
        $scope.devFirmware.error = result.message || 'verify failed';
      });
    };
    var pause = $scope.devFirmwareRuntimeLabel('verify');
    var msg = '<p>Reading the chip enters DFU mode and <b>pauses mining for ' + pause + '</b> (it restarts automatically). Nothing is written.</p>';
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm(msg, go, {title: 'Verify chip', yesLabel: 'Read chip', noLabel: 'Cancel'});
    } else if (window.confirm('Reading the chip pauses mining ' + pause + '. Continue?')) { go(); }
  };
  $scope.devFirmwareDryRun = function () {
    bsDevTabFirmware();
    $scope.devFirmware.error = '';
    $http.post('f_dev.php?action=firmware_revert', {dry_run: true}).success(function (d) {
      $scope.devFirmware.dryOk = !!(d && d.ok);
      $scope.devFirmware.dryMsg = (d && d.output) || '';
      if (!$scope.devFirmware.dryOk) $scope.devFirmware.error = (d && d.error) || 'dry run failed';
    });
  };
  $scope.devFirmwareRevert = function () {
    bsDevTabFirmware();
    if (!$scope.devFirmware.dryOk || $scope.devFirmware.confirmText !== 'REVERT') return;
    var go = function () {
      $scope.devFirmware.error = '';
      $scope.devFirmware.output = '';
      $scope.bsFirmwareRequestReloadAfterResult();
      $scope.bsFirmwareStartJob('revert', {confirm: 'REVERT'}, 'Flashing stock firmware...', function (d) {
        $scope.bsSettingsFirmwareOverlayClose();
        var result = d && d.result ? d.result : {};
        $scope.devFirmware.output = (d && d.progress) || '';
        $scope.devFirmware.confirmText = ''; $scope.devFirmware.dryOk = false;
        if (result.elapsed) $scope.devFirmware.flashRuntimeLabel = '~' + result.elapsed + 's';
        if ($scope.settings) $scope.settings.userAdjustableClocks = false;
        $scope.bsSaveUserSettingsBaselineField('userAdjustableClocks');
        $scope.devFirmwareStatus();
      }, function (d) {
        $scope.bsSettingsFirmwareOverlayClose();
        $scope.bsFirmwareClearReloadAfterResult();
        var result = d && d.result ? d.result : {};
        $scope.devFirmware.output = (d && d.progress) || '';
        $scope.devFirmware.error = result.message || 'revert reported no success';
        if (result.finished_at) {
          if ($scope.settings) $scope.settings.userAdjustableClocks = false;
          $scope.bsSaveUserSettingsBaselineField('userAdjustableClocks');
        }
        $scope.devFirmwareStatus();
      });
    };
    var pause = $scope.devFirmwareRuntimeLabel('revert');
    var msg = '<p style="text-align:center">This flashes the <b>stock</b> firmware back onto the STM32F407 bridge chip.</p>' +
              '<p>Take a validated pre-flash backup, write the stock image, and read-back-verifies (reversible).</p>' +
              '<p style="text-align:center">Estimated total: ' + pause + ', including backup, write, readback and restart.<br>Do not power off while the operation is running.</p>';
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm(msg, go, {title: 'Revert chip to stock', boxClass: 'bs-modal-warn', lockBackdrop: true, yesLabel: 'Flash stock now', noLabel: 'Cancel'});
    } else if (window.confirm('Flash stock firmware now? Mining pauses ' + pause + '.')) { go(); }
  };
  $scope.devFirmwareDryRunCustom = function () {
    bsDevTabFirmware();
    $scope.devFirmware.enableError = '';
    $http.post('f_dev.php?action=firmware_flash_custom', {dry_run: true}).success(function (d) {
      $scope.devFirmware.dryCustomOk = !!(d && d.ok);
      $scope.devFirmware.dryCustomMsg = (d && d.output) || '';
      if (!$scope.devFirmware.dryCustomOk) $scope.devFirmware.enableError = (d && d.error) || 'dry run failed';
    });
  };
  $scope.devFirmwareFlashCustom = function () {
    bsDevTabFirmware();
    if (!$scope.devFirmware.dryCustomOk || $scope.devFirmware.enableText !== 'ENABLE') return;
    var go = function () {
      $scope.devFirmware.enableError = '';
      $scope.devFirmware.enableOutput = '';
      $scope.bsFirmwareRequestReloadAfterResult();
      $scope.bsFirmwareStartJob('flash-custom', {confirm: 'ENABLE'}, 'Flashing adjustable-clock firmware...', function (d) {
        $scope.bsSettingsFirmwareOverlayClose();
        var result = d && d.result ? d.result : {};
        $scope.devFirmware.enableOutput = (d && d.progress) || '';
        $scope.devFirmware.enableText = ''; $scope.devFirmware.dryCustomOk = false;
        if (result.elapsed) $scope.devFirmware.flashRuntimeLabel = '~' + result.elapsed + 's';
        if ($scope.settings) $scope.settings.userAdjustableClocks = true;
        $scope.bsSaveUserSettingsBaselineField('userAdjustableClocks');
        $scope.devFirmwareStatus();
      }, function (d) {
        $scope.bsSettingsFirmwareOverlayClose();
        $scope.bsFirmwareClearReloadAfterResult();
        var result = d && d.result ? d.result : {};
        $scope.devFirmware.enableOutput = (d && d.progress) || '';
        $scope.devFirmware.enableError = result.message || 'flash reported no success';
        if (result.finished_at) {
          if ($scope.settings) $scope.settings.userAdjustableClocks = false;
          $scope.bsSaveUserSettingsBaselineField('userAdjustableClocks');
        }
        $scope.devFirmwareStatus();
      });
    };
    var pause = $scope.devFirmwareRuntimeLabel('flash-custom');
    var msg = '<p style="text-align:center">This flashes the <b>adjustable-clock (V1)</b> firmware onto the STM32F407 bridge chip</p>' +
              '<p>Take a validated pre-flash backup, write the image, and read-back-verifies (reversible).</p>' +
              '<p style="text-align:center">Estimated total: ' + pause + ', including backup, write, readback and restart.<br>Do not power off while the operation is running.</p>';
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm(msg, go, {title: 'Enable adjustable clocks', boxClass: 'bs-modal-warn', lockBackdrop: true, yesLabel: 'Flash V1 now', noLabel: 'Cancel'});
    } else if (window.confirm('Flash adjustable-clock firmware now? Mining pauses ' + pause + '.')) { go(); }
  };
  $scope.devLogJournalSources = {
    recorder: true,
    syslog: true,
    kern: true,
    daemon: true,
    auth: true,
    ssh: true
  };
  $scope.devLog = {
    mode: 'log',
    source: 'sgminer',
    text: '',
    offset: 0,
    paused: false,
    status: '',
    filter: '',
    seen: {},
    seenOrder: []
  };
  $scope.devLog.format = 'readable';
  var devLogGeneration = 0;
  var devLogFilePending = null;
  $scope.devLogIsClock = function () {
    return /^path:.*\/clock-history(?:-sd-buffer)?\.json$/.test($scope.devLog.source || '');
  };
  $scope.devLogSetFormat = function (format) {
    if (format !== 'raw' && format !== 'readable') return;
    if ($scope.devLog.format === format) return;
    $scope.devLog.format = format;
    $scope.devLogReset();
  };
  $scope.devLogFileDownload = function () {
    return 'f_dev.php?action=file_download&path=' + encodeURIComponent(($scope.devLog.source || '').slice(5));
  };

  $scope.devLogReset = function () {
    devLogGeneration++;
    $scope.devLog.mode = 'log';
    $scope.devLog.text = '';
    $scope.devLog.offset = 0;
    $scope.devLog.status = '';
    $scope.devLog.seen = {};
    $scope.devLog.seenOrder = [];
  };
  $scope.devLogIsSecurity = function () { return $scope.devLog.source === 'path:/opt/scripta/var/security.log'; };
  $scope.devLogIsSsh = function () { return $scope.devLog.source === 'ssh'; };
  $scope.devLogIsPruneOnly = function () { return $scope.devLogIsSecurity() || $scope.devLogIsSsh(); };
  $scope.devLogCurrentOption = function () {
    var current = null;
    angular.forEach($scope.devLogSourceList, function (s) {
      if (s.id === $scope.devLog.source) current = s;
    });
    return current;
  };
  $scope.devLogCanClear = function () {
    if ($scope.devLogIsPruneOnly()) return false;
    if ($scope.devLog.source.indexOf('path:') === 0) {
      var opt = $scope.devLogCurrentOption();
      return !!(opt && opt.clearable);
    }
    return true;
  };
  $scope.devLogClearLabel = function () {
    var opt = $scope.devLogCurrentOption();
    return (opt && opt.history) ? 'Clear History' : 'Clear Logs';
  };
  $scope.devLogPrune = function () {
    var ssh = $scope.devLogIsSsh();
    var go = function () {
      var request = ssh ? {source: 'ssh', days: 7} : {path: '/opt/scripta/var/security.log', days: 7};
      $http.post('f_dev.php?action=log_prune', request).success(function (d) {
        if (d && d.ok) {
          if (typeof Alertify !== 'undefined' && Alertify.log) {
            if (ssh) Alertify.log.success(d.note || 'SSH view retains the latest 7 days');
            else Alertify.log.success('Pruned security.log: kept ' + d.kept + ', removed ' + d.removed + ' older than ' + d.days + ' days');
          }
          $scope.devLogReset();
        } else if (typeof Alertify !== 'undefined' && Alertify.log) {
          Alertify.log.error('Prune failed: ' + ((d && d.error) || 'unknown'));
        }
      });
    };
    var sourceLabel = ssh ? '<code>ssh.log</code>' : '<code>security.log</code>';
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm('<p>Prune ' + sourceLabel + ' to retain the latest <b>7 days</b>?</p>' +
        (ssh ? '<p>The shared system journal is not erased.</p>' : ''), go,
        {title: ssh ? 'Prune SSH log view' : 'Prune security log', yesLabel: 'Prune (keep 7 days)', noLabel: 'Cancel'});
    } else if (window.confirm('Prune ' + (ssh ? 'ssh.log' : 'security.log') + ' to the last 7 days?')) { go(); }
  };
  $scope.devLogClear = function () {
    $scope.devLog.text = '';
    $scope.devLog.status = '';
  };
  $scope.devLogClearSource = function () {
    var src = $scope.devLog.source || 'sgminer';
    var target = $scope.devLogTargetForSource();
    var label = src;
    angular.forEach($scope.devLogSourceList, function (s) {
      if (s.id === src) label = s.label;
    });
    var doClear = function () {
      $http.post('f_dev.php?action=log_clear', {source: src, target: target})
        .success(function (d) {
          if (d && d.ok) {
            $scope.devLogReset();
            $scope.devStorageLoad();
            Alertify.log.success((d.note || 'Cleared log') + ': ' + (d.path || label));
          } else {
            Alertify.log.error('Clear failed: ' + ((d && d.error) || 'unknown'));
          }
        })
        .error(function (d) {
          Alertify.log.error('Clear failed: ' + ((d && d.error) || 'request failed'));
        });
    };
    var message = ($scope.devLogClearLabel() === 'Clear History' ? 'Clear history for ' : 'Clear logs for ') +
      $scope.bsHtmlEscape(label) + '?';
    if ($scope.devLogJournalSources[src]) {
      message = 'Clear selected source view for ' + $scope.bsHtmlEscape(label) + '? Other log sources are unchanged.';
    }
    if (typeof $scope.bsConfirm === 'function') {
      $scope.bsConfirm(message, doClear);
    } else {
      doClear();
    }
  };
  $scope.devLogTargetChanged = function () {
    $scope.devLogReset();
    $scope.sync('settings', $scope.settings, 1);
  };
  $scope.devLogTargetForSource = function () {
    return ($scope.devLog.source === 'sgminer') ? ($scope.settings.userDevLogTarget || 'RAM') : '';
  };

  var devLogPoll = function () {
    if ($scope.devTab !== 'logs' || $scope.devLog.mode !== 'log' || $scope.devLog.paused || $scope.devMeasuredVisible) return;
    var src = $scope.devLog.source || 'sgminer';
    if (src.indexOf('path:') === 0) {
      var vp = src.slice(5);
      var format = $scope.devLogIsClock() ? $scope.devLog.format : 'raw';
      var generation = devLogGeneration;
      var filter = $scope.devLog.filter;
      var key = JSON.stringify([generation, src, format, filter]);
      if (devLogFilePending === key) return;
      devLogFilePending = key;
      var current = function () {
        return !devLogDestroyed && $scope.devLog.mode === 'log' && !$scope.devMeasuredVisible && $scope.devTab === 'logs' && !$scope.devLog.paused &&
          generation === devLogGeneration && $scope.devLog.source === src &&
          (!$scope.devLogIsClock() || $scope.devLog.format === format) && $scope.devLog.filter === filter;
      };
      $http.get('f_dev.php?action=file_view', {params: {path: vp, format: format}}).success(function (d) {
        if (devLogFilePending === key) devLogFilePending = null;
        if (!current()) return;
        if (!d || !d.ok) { $scope.devLog.text = ''; $scope.devLog.status = (d && d.error) || 'view failed'; return; }
        var t = d.text || '(empty)';
        if ($scope.devLog.filter) {
          var ff = $scope.devLog.filter.toLowerCase();
          t = t.split('\n').filter(function (l) { return l.toLowerCase().indexOf(ff) >= 0; }).join('\n');
        }
        $scope.devLog.text = t;
        $scope.devLog.status = d.format === 'readable'
          ? 'UTC / ' + (d.rows || 0) + ' records' + (d.truncated ? ' / recent records only' : '')
          : 'viewing ' + d.path + (d.truncated ? ' / raw tail: last ' + Math.round(d.shown / 1024) + ' KiB of ' + Math.round(d.size / 1024) + ' KiB' : '');
      }).error(function () {
        if (devLogFilePending === key) devLogFilePending = null;
        if (current()) { $scope.devLog.text = ''; $scope.devLog.status = 'view request failed'; }
      });
      return;
    }
    var target = (src === 'sgminer') ? ($scope.settings.userDevLogTarget || 'RAM') : '';
    var tailGeneration = devLogGeneration;
    $http.get('f_dev.php?action=log_tail&source=' + encodeURIComponent(src)
              + '&target=' + encodeURIComponent(target)
              + '&offset=' + ($scope.devLog.offset || 0))
      .success(function (d) {
        var currentSrc = $scope.devLog.source || 'sgminer';
        var currentTarget = (currentSrc === 'sgminer') ? ($scope.settings.userDevLogTarget || 'RAM') : '';
        if (devLogDestroyed || $scope.devLog.mode !== 'log' || $scope.devMeasuredVisible || $scope.devLog.paused ||
            tailGeneration !== devLogGeneration || src !== currentSrc || target !== currentTarget) return;
        if (!d || !d.ok) {
          $scope.devLog.status = (d && d.error) ? d.error : 'tail failed';
          return;
        }
        if (typeof d.next_offset === 'number') $scope.devLog.offset = d.next_offset;
        if (d.lines && d.lines.length) {
          var incoming = d.lines;
          if (src === 'sgminer') {
            incoming = d.lines.filter(function (line) {
              if ($scope.devLog.seen[line]) return false;
              $scope.devLog.seen[line] = true;
              $scope.devLog.seenOrder.push(line);
              if ($scope.devLog.seenOrder.length > 2000) {
                delete $scope.devLog.seen[$scope.devLog.seenOrder.shift()];
              }
              return true;
            });
          }
          var add = incoming.join('\n');
          if ($scope.devLog.filter) {
            var f = $scope.devLog.filter.toLowerCase();
            add = incoming.filter(function (l) { return l.toLowerCase().indexOf(f) >= 0; }).join('\n');
          }
          if (add) $scope.devLog.text += (($scope.devLog.text && !$scope.devLog.text.match(/\n$/)) ? '\n' : '') + add + '\n';
          var max = 200000;
          if ($scope.devLog.text.length > max) {
            $scope.devLog.text = $scope.devLog.text.slice($scope.devLog.text.length - max);
          }
        }
        $scope.devLog.status = 'reading ' + d.path + ' @ ' + d.next_offset + (d.offset_unit === 'lines' ? ' lines' : 'B');
      })
      .error(function () {
        var currentSrc = $scope.devLog.source || 'sgminer';
        var currentTarget = (currentSrc === 'sgminer') ? ($scope.settings.userDevLogTarget || 'RAM') : '';
        if (devLogDestroyed || $scope.devLog.mode !== 'log' || $scope.devMeasuredVisible || $scope.devLog.paused ||
            tailGeneration !== devLogGeneration || src !== currentSrc || target !== currentTarget) return;
        $scope.devLog.status = 'tail request failed';
      });
  };
  var devLogTimer = null;
  var devLogDestroyed = false;
  var devLogSchedule = function () {
    if (devLogDestroyed) return;
    devLogTimer = $timeout(function () {
      devLogPoll();
      devLogSchedule();
    }, 2000);
  };
  $scope.$on('$destroy', function () {
    devLogDestroyed = true;
    devToolsDestroyed = true;
    if (devLogTimer) $timeout.cancel(devLogTimer);
    devHealthDrop();
    if (devActivityPoller) devActivityPoller.destroy();
  });
  devLogPoll();
  devLogSchedule();

  // ---------- Network probe ----------
  // ---------- Diagnostics: paste-safe state snapshot ----------
  $scope.devSnap = {text: '', loading: false};
  $scope.devSnapshot = function () {
    if (devToolsDestroyed || $scope.devSnap.loading) return;
    $scope.devMeasuredVisible = false;
    $scope.devLogReset();
    $scope.devLog.mode = 'snapshot';
    $scope.devLog.text = 'Collecting...';
    var generation = devLogGeneration;
    $scope.devSnap.loading = true; $scope.devSnap.text = '';
    $http.get('f_dev.php?action=state_snapshot').success(function (d) {
      if (devToolsDestroyed) return;
      $scope.devSnap.loading = false;
      $scope.devSnap.text = (d && d.ok) ? d.text : ('snapshot failed: ' + ((d && d.error) || 'unknown'));
      if ($scope.devLog.mode === 'snapshot' && generation === devLogGeneration) $scope.devLog.text = $scope.devSnap.text;
    }).error(function () {
      if (devToolsDestroyed) return;
      $scope.devSnap.loading = false; $scope.devSnap.text = 'snapshot request failed';
      if ($scope.devLog.mode === 'snapshot' && generation === devLogGeneration) $scope.devLog.text = $scope.devSnap.text;
    });
  };
  $scope.devSnapshotCopy = function () {
    var ta = document.createElement('textarea');
    ta.value = $scope.devSnap.text;
    ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.focus(); ta.select();
    try { document.execCommand('copy'); if (typeof Alertify !== 'undefined' && Alertify.log) Alertify.log.success('Snapshot copied to clipboard'); }
    catch (e) { if (typeof Alertify !== 'undefined' && Alertify.log) Alertify.log.error('Copy failed — select the text and copy manually'); }
    document.body.removeChild(ta);
  };

  $scope.devProbeRunning = false;
  $scope.devProbeResults = [];
  $scope.devProbeUpdated = 0;
  var devProbeGeneration = 0;
  $scope.$on('$destroy', function () { devProbeGeneration++; });
  $scope.devProbeRun = function () {
    if ($scope.devProbeRunning || devToolsDestroyed) return;
    var generation = ++devProbeGeneration;
    $scope.devProbeRunning = true;
    $http.get('f_dev.php?action=network_pools').success(function (d) {
      if (devToolsDestroyed || generation !== devProbeGeneration) return;
      if (!d || !d.ok || !angular.isArray(d.pools)) {
        $scope.devProbeRunning = false;
        Alertify.log.error('Network probe failed: ' + ((d && d.error) || 'unknown'));
        return;
      }
      // render the pool list immediately, then fill each row as its probe returns
      $scope.devProbeResults = d.pools.map(function (p) {
        return {name: p.name, names: p.names || [p.name], key: p.key, host: p.host, port: p.port, probing: true};
      });
      $scope.devProbeUpdated = Math.floor(Date.now() / 1000);
      var pending = $scope.devProbeResults.length;
      if (!pending) { $scope.devProbeRunning = false; return; }
      var done = function (i, result) {
        if (devToolsDestroyed || generation !== devProbeGeneration) return;
        var row = $scope.devProbeResults[i];
        if (result && result.key === row.key) { angular.extend(row, result, {probing: false}); }
        else { row.probing = false; if (!row.tcp) row.tcp = {ok: false, error: 'probe failed'}; }
        $scope.devProbeUpdated = Math.floor(Date.now() / 1000);
        if (--pending <= 0) $scope.devProbeRunning = false;
      };
      angular.forEach($scope.devProbeResults, function (row, i) {
        $http.get('f_dev.php?action=network_probe&endpoint=' + encodeURIComponent(row.key))
          .success(function (pr) { done(i, (pr && pr.ok) ? pr.result : null); })
          .error(function () { done(i, null); });
      });
    }).error(function () {
      if (devToolsDestroyed || generation !== devProbeGeneration) return;
      $scope.devProbeRunning = false;
      Alertify.log.error('Network probe request failed');
    });
  };

  // ---------- API console ----------
  $scope.devApiCommonCommands = [
    'summary', 'stats', 'coin', 'pools', 'devs', 'devdetails', 'config',
    'asccount', 'notify', 'version'
  ];
  $scope.devApi = {
    command: 'summary',
    response: '',
    updated: 0,
    history: []
  };
  $scope.devApiRun = function () {
    var cmd = ($scope.devApi.command || '').trim();
    if (!cmd) return;
    $http.post('f_dev.php?action=sgminer_api', {command: cmd})
        .success(function (d) {
          if (d && d.ok) {
            $scope.devApi.response = (typeof d.response === 'string') ? d.response : JSON.stringify(d.response, null, 2);
            $scope.devApi.updated = Math.floor(Date.now() / 1000);
            $scope.devApi.history.unshift({command: cmd});
            if ($scope.devApi.history.length > 10) $scope.devApi.history.pop();
          } else {
            $scope.devApi.response = 'ERROR: ' + ((d && d.error) || 'unknown');
          }
        })
        .error(function () {
          $scope.devApi.response = 'ERROR: request failed';
        });
  };
  $scope.devApiCopy = function () {
    var t = $scope.devApi.response || '';
    if (!t) return;
    var ta = document.createElement('textarea');
    ta.value = t;
    ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.focus(); ta.select();
    try { document.execCommand('copy'); if (typeof Alertify !== 'undefined' && Alertify.log) Alertify.log.success('API response copied'); }
    catch (e) { if (typeof Alertify !== 'undefined' && Alertify.log) Alertify.log.error('Copy failed — select the text and copy manually'); }
    document.body.removeChild(ta);
  };

  // ---------- Recorders ----------
  $scope.devRecorders = [];
  $scope.devRecordersById = {};
  $scope.devRecordersUpdated = 0;
  $scope.devRecorderRunOutput = '';
  $scope.devRecordersCron = true;
  $scope.devRecordersAllOk = true;
  $scope.devRecordersLoading = false;
  $scope.devRecordersError = '';
  $scope.devRecorderRunning = false;
  $scope.devRecorderWarning = function () {
    if ($scope.devRecordersError) return 'Collector status unavailable.';
    if (!$scope.devRecordersUpdated) return '';
    if (!$scope.devRecordersCron) return 'History collection stopped: scheduler is not running.';
    var warnings = [];
    angular.forEach($scope.devRecorders, function (recorder) {
      if (recorder.health === 'error') warnings.push(recorder.name + ': failed');
      else if (recorder.health === 'stale') warnings.push(recorder.name + ': not updating');
      else if (recorder.health === 'waiting') warnings.push(recorder.name + ': waiting for update');
    });
    return warnings.join('; ');
  };
  $scope.devRecorderWaitingOnly = function () {
    if ($scope.devRecordersError || !$scope.devRecordersUpdated || !$scope.devRecordersCron) return false;
    var waiting = false, failed = false;
    angular.forEach($scope.devRecorders, function (recorder) {
      if (recorder.health === 'waiting') waiting = true;
      if (recorder.health === 'error' || recorder.health === 'stale') failed = true;
    });
    return waiting && !failed;
  };
  $scope.devRecordersRefresh = function () {
    if (devToolsDestroyed || $scope.devRecordersLoading || $scope.devRecorderRunning) return;
    $scope.devRecordersLoading = true;
    $http.get('f_dev.php?action=recorder_health').success(function (d) {
      if (devToolsDestroyed) return;
      $scope.devRecordersLoading = false;
      if (d && d.ok && angular.isArray(d.recorders)) {
        $scope.devRecordersError = '';
        $scope.devRecorders = d.recorders;
        $scope.devRecordersById = {};
        angular.forEach(d.recorders, function (r) { $scope.devRecordersById[r.id] = r; });
        $scope.devRecordersCron = d.cron_up;
        $scope.devRecordersAllOk = d.all_ok;
        $scope.devRecordersUpdated = Math.floor(Date.now() / 1000);
      } else {
        $scope.devRecordersError = (d && d.error) || 'Recorder health unavailable';
      }
    }).error(function () {
      if (devToolsDestroyed) return;
      $scope.devRecordersLoading = false;
      $scope.devRecordersError = 'Recorder health request failed';
    });
  };
  $scope.devRecorderRun = function (r) {
    if (devToolsDestroyed || !r || r.running || $scope.devRecorderRunning || $scope.devRecordersLoading) return;
    $scope.devRecorderRunning = true;
    r.running = true;
    $scope.devMeasuredVisible = false;
    $scope.devLogReset();
    $scope.devLog.mode = 'recorder';
    $scope.devLog.status = r.name;
    var generation = devLogGeneration;
    $scope.devRecorderRunOutput = 'Running ' + r.name + '…\n';
    $scope.devLog.text = $scope.devRecorderRunOutput;
    $http.post('f_dev.php?action=recorder_run', {which: r.id}).success(function (d) {
      if (devToolsDestroyed) return;
      $scope.devRecorderRunning = false;
      r.running = false;
      if (d && d.ok) {
        $scope.devRecorderRunOutput += (d.output || '(no output)') + '\n[exit ' + d.exit + ']';
        $scope.devLogsRefresh();
      } else {
        $scope.devRecorderRunOutput += 'ERROR: ' + ((d && d.error) || 'unknown');
      }
      if ($scope.devLog.mode === 'recorder' && generation === devLogGeneration) $scope.devLog.text = $scope.devRecorderRunOutput;
    }).error(function () {
      if (devToolsDestroyed) return;
      $scope.devRecorderRunning = false;
      r.running = false;
      $scope.devRecorderRunOutput += 'ERROR: request failed';
      if ($scope.devLog.mode === 'recorder' && generation === devLogGeneration) $scope.devLog.text = $scope.devRecorderRunOutput;
    });
  };
})


.controller('CtrlBackup', function($scope,$http,$timeout) {
  $scope.backupName = GetDateTime();
  $scope.backups = [];
  $scope.restoring = 0;
  $scope.backupBusy = false;
  $scope.backupCsrf = '';
  function backupResult(d) {
    if (d && d.csrf) $scope.backupCsrf = d.csrf;
    if (d && d.info) angular.forEach(d.info, function(v) { Alertify.log.create(v.type, v.text); });
    if (!d || d.ok !== true) {
      if (!d || !d.info) Alertify.log.create('danger', (d && d.error) || 'Backup operation failed');
      return false;
    }
    return true;
  }
  function backupMutation(fields) {
    var parts = [];
    angular.forEach(fields, function(value, key) {
      parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(value));
    });
    return $http.post('f_backup.php', parts.join('&'), {
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}
    });
  }
  $scope.bsBackupNow = function() {
    if (!$scope.backupName) $scope.backupName = GetDateTime();
    return $scope.backupLocal();
  };
  $scope.bsBackupExportNow = function() {
    if (!$scope.backupName) $scope.backupName = GetDateTime();
    return $scope.backupExport();
  };

  $scope.backupLocal = function() {
    if ($scope.backupBusy || !$scope.backupCsrf) return null;
    $scope.backupBusy = true;
    return backupMutation({name:$scope.backupName, backup:'[]', csrf:$scope.backupCsrf}).then(function(response) {
      var ok = backupResult(response.data);
      if (ok) $scope.reload();
      return ok;
    }, function(response) {
      backupResult(response.data); return false;
    }).finally(function() {
      $scope.backupBusy = false;
    });
  };

  $scope.backupExport = function() {
    var name = $scope.backupName, request = $scope.backupLocal();
    if (request) return request.then(function(ok) { if (ok) $scope.exportZip(name); });
  };

  $scope.exportZip = function(name) {
    var selected = $scope.backups[$scope.restoring];
    name = name || (selected && selected.dir);
    if (name && !$scope.backupBusy) window.location.href='f_backup.php?export='+encodeURIComponent(name);
  };

  $scope.backupImport = function(file) {
    if (!file || $scope.backupBusy || !$scope.backupCsrf) return;
    if (file.size > 16 * 1024 * 1024) {
      Alertify.log.create('danger', 'Backup archive exceeds 16 MiB'); return;
    }
    var form = new FormData();
    form.append('file', file);
    form.append('csrf', $scope.backupCsrf);
    $scope.backupBusy = true;
    $http.post('f_backup.php', form, {transformRequest:angular.identity, headers:{'Content-Type':undefined}}).then(function(response) {
      if (backupResult(response.data)) $scope.reload();
    }, function(response) { backupResult(response.data); }).finally(function() { $scope.backupBusy = false; });
  };
  $scope.bsChooseBackupFile = function() {
    if (!$scope.backupBusy && $scope.backupCsrf) document.getElementById('bsBackupFile').click();
  };

  $scope.choose = function(i) {
    $scope.restoring=i;
  };

  $scope.restore = function() {
    var selected = $scope.backups[$scope.restoring];
    if (!selected || $scope.backupBusy || !$scope.backupCsrf) return;
    $scope.bsConfirm('Replace application settings and history, then restart the miner? Restored network settings require a separate review and apply.', function() {
      if ($scope.backupBusy) return;
      $scope.backupBusy = true;
      backupMutation({restore:selected.dir, csrf:$scope.backupCsrf}).then(function(response) {
        if (!backupResult(response.data)) return;
        $scope.syncDelay(300,'settings');
        $scope.syncDelay(600,'pools');
        $scope.syncDelay(900,'options');
        $timeout(function() {
          $http.post('f_miner.php', {command:'restart'}).success(function(d) {
            if (d && d.info) angular.forEach(d.info, function(v) { Alertify.log.create(v.type, v.text); });
          }).error(function() { Alertify.log.create('danger', 'Settings restored, but miner restart failed'); });
        }, 900);
      }, function(response) { backupResult(response.data); }).finally(function() { $scope.backupBusy = false; });
    }, {title:'Restore backup', yesLabel:'Restore and Restart', noLabel:'Cancel'});
  };

  $scope.reload = function(wait) {
    wait=wait||0;
    var syncNow = function(){
      $http.get('f_backup.php').success(function(d){
        if (d.csrf) $scope.backupCsrf = d.csrf;
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
      }).error(function(d) { backupResult(d); });
    }
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
    if (!sel || $scope.backupBusy || !$scope.backupCsrf) return;
    $scope.bsConfirm(
      'This cannot be undone.',
      function() {
        if ($scope.backupBusy) return;
        $scope.backupBusy = true;
        backupMutation({delete:sel.dir, csrf:$scope.backupCsrf}).then(function(response) {
          if (backupResult(response.data)) { $scope.restoring = 0; $scope.reload(); }
        }, function(response) { backupResult(response.data); }).finally(function() { $scope.backupBusy = false; });
      },
      {title: 'Delete backup "' + sel.dir + '"', yesLabel: 'Delete', noLabel: 'Cancel'}
    );
  };

  $scope.bsRenameBackup = function() {
    var sel = $scope.backups[$scope.restoring];
    if (!sel || $scope.backupBusy || !$scope.backupCsrf) return;
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
    $scope.backupBusy = true;
    backupMutation({rename:sel.dir, to:newName, csrf:$scope.backupCsrf}).then(function(response) {
      if (backupResult(response.data)) $scope.reload();
    }, function(response) { backupResult(response.data); }).finally(function() { $scope.backupBusy = false; });
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
