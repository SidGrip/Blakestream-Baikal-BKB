'use strict';

angular.module('Scripta.controllers').controller('CtrlWifiSettings', function($scope, $http, $timeout) {
  var timer = null, closed = false, loading = false, token = '', generation = 0;
  var scanTimer = null, scanDeadline = 0, scanCooldownSeconds = 15;
  var radioEnabling = false;
  var connectPosting = false, connectTarget = null, connectedTimer = null;
  $scope.wifi = {adapter:{present:false}, radio:{enabled:false, hardware:true, blocked:false}, connection:null, saved:[], networks:[], pending:null, ethernet_connected:false};
  $scope.wifiDraft = {ssid:'', security:'wpa-psk', psk:'', hidden:false};
  $scope.wifiPasswordVisible = false;
  $scope.wifiBusy = false;
  $scope.wifiReady = false;
  $scope.wifiScanned = false;
  $scope.wifiScanWait = 0;
  $scope.wifiError = '';
  $scope.wifiConnected = false;
  function clearConnectFeedback() {
    if (connectedTimer) $timeout.cancel(connectedTimer);
    connectedTimer = null;
    $scope.wifiConnected = false;
  }
  function updateConnectFeedback() {
    var pending = $scope.wifi.pending, connection = $scope.wifi.connection;
    if (pending) {
      clearConnectFeedback();
      if (!connectTarget) connectTarget = {ssid:pending.ssid, security:pending.security};
      return;
    }
    if (!connection || !$scope.wifi.radio.enabled) clearConnectFeedback();
    if (!connectTarget || connectPosting) return;
    var target = connectTarget, last = $scope.wifi.last_result;
    connectTarget = null;
    var matches = connection && (target.uuid ? connection.uuid === target.uuid :
      connection.ssid === target.ssid && (!target.security || connection.security === target.security));
    if (!matches || (last && last.ok === false) || !$scope.wifi.radio.enabled) return;
    clearConnectFeedback();
    $scope.wifiConnected = true;
    connectedTimer = $timeout(clearConnectFeedback, 5000);
  }
  function cancelTimer() { if (timer) $timeout.cancel(timer); timer = null; }
  function cancelScanTimer() { if (scanTimer) $timeout.cancel(scanTimer); scanTimer = null; }
  function updateScanCountdown() {
    cancelScanTimer();
    if (closed) return;
    $scope.wifiScanWait = Math.max(0, Math.ceil((scanDeadline - Date.now()) / 1000));
    if ($scope.wifiScanWait && $scope.bsSettingsTab === 'wifi') scanTimer = $timeout(updateScanCountdown, 1000);
  }
  function scanCooldown(seconds) {
    if (typeof seconds !== 'number' || !isFinite(seconds) || seconds < 0) return;
    scanDeadline = Math.max(scanDeadline, Date.now() + Math.min(seconds, 60) * 1000);
    updateScanCountdown();
  }
  function schedule() {
    cancelTimer();
    if (!closed && $scope.bsSettingsTab === 'wifi') timer = $timeout(function() { load(false); }, $scope.wifi.pending || $scope.wifiAdapterStarting() ? 5000 : 10000);
  }
  function message(error) {
    if (/Scanning not allowed/i.test(error || '')) return 'Scanning is temporarily unavailable. Try again shortly.';
    if (/NoReply|timed out|timeout/i.test(error || '')) return 'Wi-Fi service is busy. Retrying status...';
    return error || 'Wi-Fi service unavailable';
  }
  function adopt(data, partial) {
    if (closed) return;
    if (data.csrf) token = data.csrf;
    if (Object.prototype.hasOwnProperty.call(data, 'scan_retry_after')) scanCooldown(data.scan_retry_after);
    if (!data.ok) { clearConnectFeedback(); $scope.wifiError = message(data.error); return false; }
    if (data.adapter) $scope.wifi.adapter = data.adapter;
    if (data.radio) $scope.wifi.radio = data.radio;
    if (Object.prototype.hasOwnProperty.call(data, 'connection')) $scope.wifi.connection = data.connection;
    if (Array.isArray(data.saved)) $scope.wifi.saved = data.saved;
    if (Array.isArray(data.networks) && (data.networks.length || !partial)) $scope.wifi.networks = data.networks;
    if (Object.prototype.hasOwnProperty.call(data, 'pending')) $scope.wifi.pending = data.pending;
    if (Object.prototype.hasOwnProperty.call(data, 'last_result')) $scope.wifi.last_result = data.last_result;
    if (Object.prototype.hasOwnProperty.call(data, 'ethernet_connected')) $scope.wifi.ethernet_connected = data.ethernet_connected;
    $scope.wifiReady = true;
    var last = $scope.wifi.last_result;
    if (last && last.ok === false && last.error) $scope.wifiError = message(last.error);
    else $scope.wifiError = '';
    updateConnectFeedback();
    return true;
  }
  function ssidByteLength(value) {
    if (typeof value !== 'string') return 0;
    try { return unescape(encodeURIComponent(value)).length; }
    catch (err) { return 0; }
  }
  function load() {
    if (closed || loading || $scope.wifiBusy) return;
    if ($scope.bsSettingsTab !== 'wifi') return;
    loading = true;
    var current = generation;
    $http.get('f_wifi.php?action=status', {timeout: 18000}).success(function(data) {
      if (current === generation && !adopt(data)) $scope.wifiReady = false;
      loading = false; schedule();
    }).error(function() {
      if (!closed && current === generation) { clearConnectFeedback(); $scope.wifiReady = false; $scope.wifiError = 'Cannot reach Wi-Fi service.'; }
      loading = false; schedule();
    });
  }
  function mutate(action, body) {
    cancelTimer();
    clearConnectFeedback();
    if (action === 'radio' && !body.enabled) $scope.wifiPasswordVisible = false;
    if (action === 'connect') {
      $scope.wifiPasswordVisible = false;
      connectPosting = true;
      connectTarget = {uuid:body.uuid, ssid:body.ssid, security:body.security};
    }
    generation++;
    $scope.wifiBusy = true;
    $scope.wifiError = '';
    var request = angular.copy(body || {});
    request.csrf = token;
    $http.post('f_wifi.php?action=' + action, request, {timeout: 20000}).success(function(data) {
      if (action === 'connect') {
        connectPosting = false;
        if (!data.ok) connectTarget = null;
      }
      if (action === 'connect' && data.ok && data.pending) {
        // A queued connection is an acknowledgement, not an adapter snapshot.
        if (!closed) {
          $scope.wifi.pending = data.pending;
          $scope.wifi.last_result = data.last_result || null;
        }
      } else adopt(data, action !== 'scan');
      if (action === 'radio') {
        radioEnabling = false;
        if (data.ok && body.enabled && typeof data.scan_retry_after !== 'number') scanCooldown(scanCooldownSeconds);
      }
      if (action === 'scan') {
        scanCooldown(typeof data.scan_retry_after === 'number' ? data.scan_retry_after : scanCooldownSeconds);
        if (data.ok && !data.scan_deferred) $scope.wifiScanned = true;
      }
      if (action === 'connect') $scope.wifiDraft.psk = '';
      $scope.wifiBusy = false; schedule();
    }).error(function() {
      if (action === 'connect') connectPosting = false;
      if (action === 'radio') radioEnabling = false;
      if (!closed) { $scope.wifiReady = false; $scope.wifiError = 'Connection interrupted. Reload Wi-Fi settings.'; }
      if (action === 'connect') $scope.wifiDraft.psk = '';
      $scope.wifiBusy = false; schedule();
    });
  }
  $scope.wifiAbsent = function() {
    return $scope.wifiReady && !$scope.wifi.adapter.present;
  };
  $scope.wifiUnsupportedAdapter = function() {
    var a = $scope.wifi.adapter || {};
    return $scope.wifiReady && a.present && (!a.managed || a.firmware_missing);
  };
  $scope.wifiRadioOff = function() {
    return $scope.wifiReady && !($scope.wifi.radio && $scope.wifi.radio.enabled);
  };
  $scope.wifiHardwareBlocked = function() {
    return !!( $scope.wifi.radio && $scope.wifi.radio.blocked );
  };
  $scope.wifiAdapterUnavailable = function() {
    var a = $scope.wifi.adapter;
    return a.present && a.managed && !a.firmware_missing &&
      $scope.wifi.radio.enabled && !$scope.wifiHardwareBlocked() &&
      (typeof a.state !== 'number' || a.state < 30);
  };
  $scope.wifiAdapterStarting = function() {
    return radioEnabling || ($scope.wifiAdapterUnavailable() && $scope.wifiScanWait > 0);
  };
  $scope.wifiCanMutate = function() {
    return $scope.wifiReady && !!token && !$scope.wifiBusy && !$scope.wifi.pending &&
      $scope.wifi.adapter.present && $scope.wifi.adapter.managed && !$scope.wifi.adapter.firmware_missing &&
      $scope.wifi.radio.enabled && !$scope.wifi.radio.blocked && !$scope.wifiAdapterUnavailable();
  };
  $scope.wifiValid = function() {
    var d = $scope.wifiDraft, ssid = d.ssid, bytes = ssidByteLength(ssid);
    if (!$scope.wifiCanMutate() || bytes < 1 || bytes > 32) return false;
    if (d.security === 'open') return true;
    if (d.security !== 'wpa-psk') return false;
    var psk = d.psk || '';
    if (psk.length === 64) return /^[0-9a-fA-F]{64}$/.test(psk);
    return psk.length >= 8 && psk.length <= 63;
  };
  $scope.wifiSelectNetwork = function(network) {
    if (!network || !network.supported || !$scope.wifiCanMutate()) return;
    clearConnectFeedback();
    $scope.wifiPasswordVisible = false;
    for (var i = 0; i < $scope.wifi.saved.length; i++) {
      var saved = $scope.wifi.saved[i];
      if (saved.saved && saved.ssid === network.ssid && saved.security === network.security) {
        $scope.wifiReconnect(saved);
        return;
      }
    }
    $scope.wifiDraft.ssid = network.ssid;
    $scope.wifiDraft.security = network.security;
    $scope.wifiDraft.hidden = false;
    if (network.security === 'open') $scope.wifiDraft.psk = '';
  };
  $scope.wifiReconnect = function(network) {
    if (!network || !network.uuid || !network.saved || !network.supported || !$scope.wifiCanMutate()) return;
    if ($scope.wifi.connection && $scope.wifi.connection.uuid === network.uuid) return;
    $scope.wifiDraft.ssid = network.ssid;
    $scope.wifiDraft.security = network.security;
    $scope.wifiDraft.hidden = !!network.hidden;
    $scope.wifiDraft.psk = '';
    mutate('connect', {uuid:network.uuid});
  };
  $scope.wifiScan = function() {
    if (!$scope.wifiCanScan()) return;
    scanCooldown(scanCooldownSeconds);
    mutate('scan', {});
  };
  $scope.wifiCanScan = function() {
    return $scope.wifiCanMutate() && [30, 100, 120].indexOf($scope.wifi.adapter.state) >= 0 &&
      $scope.wifiScanWait === 0 && Date.now() >= scanDeadline;
  };
  $scope.wifiConnect = function() {
    if (!$scope.wifiValid()) return;
    var d = $scope.wifiDraft, body = {ssid:d.ssid, security:d.security, hidden:!!d.hidden};
    if (d.security === 'wpa-psk') body.psk = d.psk;
    mutate('connect', body);
  };
  $scope.wifiDisconnect = function() {
    if (!$scope.wifiCanMutate() || !$scope.wifi.connection) return;
    if ($scope.bsConfirm) {
      $scope.bsConfirm('Disconnect from this Wi-Fi network?', function() { mutate('disconnect', {}); });
      return;
    }
    mutate('disconnect', {});
  };
  $scope.wifiForget = function(network) {
    if (!network || !network.uuid || $scope.wifiBusy || $scope.wifi.pending || !$scope.wifiReady || !token) return;
    var go = function() { mutate('forget', {uuid:network.uuid}); };
    if ($scope.bsConfirm) $scope.bsConfirm('Forget this saved Wi-Fi network?', go);
    else go();
  };
  $scope.wifiSetRadio = function(enabled) {
    if ($scope.wifiBusy || !token || !$scope.wifiReady) return;
    if (!!enabled === !!($scope.wifi.radio && $scope.wifi.radio.enabled)) return;
    var go = function() {
      radioEnabling = !!enabled;
      if (enabled) scanCooldown(scanCooldownSeconds);
      mutate('radio', {enabled: !!enabled});
    };
    if (!enabled && $scope.wifi.connection && $scope.bsConfirm) {
      $scope.bsConfirm('Turn off the Wi-Fi radio? The current network will disconnect. Ethernet is unchanged.', go);
      return;
    }
    go();
  };
  $scope.wifiPendingLabel = function() {
    var p = $scope.wifi.pending;
    if (!p) return '';
    if (p.state === 'rollback-failed') return 'Restoring the previous Wi-Fi network';
    return p.ssid ? ('Connecting to ' + p.ssid) : 'Connecting';
  };
  $scope.wifiConnecting = function() { return connectPosting || !!$scope.wifi.pending; };
  $scope.wifiConnectLabel = function() {
    if ($scope.wifiConnecting()) {
      return $scope.wifi.pending && $scope.wifi.pending.state === 'rollback-failed' ? 'Restoring...' : 'Connecting...';
    }
    return $scope.wifiConnected ? 'Connected' : 'Connect';
  };
  $scope.wifiClearConnectFeedback = clearConnectFeedback;
  $scope.wifiSecurityChanged = function() {
    clearConnectFeedback();
    $scope.wifiPasswordVisible = false;
  };
  $scope.wifiTogglePassword = function() {
    if ($scope.wifiCanMutate() && $scope.wifiDraft.security === 'wpa-psk') {
      $scope.wifiPasswordVisible = !$scope.wifiPasswordVisible;
    }
  };
  $scope.wifiSecurityLabel = function(security) {
    if (security === 'open') return 'Open';
    if (security === 'wpa-psk') return 'WPA-PSK';
    return 'Unsupported';
  };
  $scope.$watch('bsSettingsTab', function(tab) {
    cancelTimer(); updateScanCountdown();
    if (tab === 'wifi') load();
    else $scope.wifiPasswordVisible = false;
  });
  $scope.$on('bsWifiReload', function() { load(); });
  $scope.$on('$destroy', function() { closed = true; cancelTimer(); cancelScanTimer(); clearConnectFeedback(); });
});
