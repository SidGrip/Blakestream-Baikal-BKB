'use strict';

angular.module('Scripta.controllers').controller('CtrlNetworkSettings', function($scope, $http, $timeout, $window) {
  var timer = null, closed = false, loading = false, token = '', initialized = false, generation = 0, reconnectAddress = '';
  $scope.network = {interfaces: [], pending: null};
  $scope.networkDraft = {interface: '', mode: 'dhcp', address: '', prefix: 24, gateway: '', dnsText: ''};
  $scope.networkBusy = false;
  $scope.networkReady = false;
  $scope.networkError = '';
  function cancelTimer() { if (timer) $timeout.cancel(timer); timer = null; }
  function schedule() {
    cancelTimer();
    if (!closed && $scope.bsSettingsTab === 'network') timer = $timeout(function() { load(false); }, $scope.network.pending ? 5000 : 10000);
  }
  function message(error) {
    if (/NoReply|timed out|timeout/i.test(error || '')) return 'Network service is busy. Retrying status...';
    return error || 'Network service unavailable';
  }
  function adopt(data, partial) {
    if (closed) return;
    if (data.csrf) token = data.csrf;
    if (Array.isArray(data.interfaces) && (data.interfaces.length || (data.ok && !partial && !data.pending))) $scope.network.interfaces = data.interfaces;
    if (Object.prototype.hasOwnProperty.call(data, 'pending')) $scope.network.pending = data.pending;
    if (Object.prototype.hasOwnProperty.call(data, 'last_result')) $scope.network.last_result = data.last_result;
    if (Object.prototype.hasOwnProperty.call(data, 'restored_draft')) $scope.network.restored_draft = data.restored_draft;
    if (!data.ok) { $scope.networkError = message(data.error); $scope.networkReady = false; return false; }
    reconnectAddress = '';
    $scope.networkReady = true;
    $scope.networkError = '';
    return true;
  }
  $scope.networkCurrent = function() {
    return $scope.network.interfaces.filter(function(i) { return i.interface === $scope.networkDraft.interface; })[0] || {};
  };
  $scope.networkSelectInterface = function() {
    var selected = $scope.networkCurrent();
    if (!selected.interface) return;
    $scope.networkDraft = {
      interface: selected.interface, mode: selected.saved && selected.mode === 'static' ? 'static' : 'dhcp',
      address: selected.address || '', prefix: selected.prefix || 24,
      gateway: selected.gateway || '', dnsText: (selected.dns || []).join(', ')
    };
  };
  $scope.networkRestoredInterface = function() {
    var saved = $scope.network.restored_draft;
    return saved && Array.isArray(saved.interfaces) ? saved.interfaces.filter(function(i) {
      return i.interface === $scope.networkDraft.interface && (i.mode === 'static' || i.mode === 'dhcp');
    })[0] : null;
  };
  $scope.networkReviewRestored = function() {
    var saved = $scope.networkRestoredInterface();
    if (!saved || !$scope.networkReady || $scope.networkBusy || $scope.network.pending) return;
    $scope.networkDraft = {interface: saved.interface, mode: saved.mode, address: saved.address || '',
      prefix: saved.prefix || 24, gateway: saved.gateway || '', dnsText: (saved.dns || []).join(', ')};
    if ($scope.networkForm) $scope.networkForm.$setDirty();
  };
  function resetDraft() {
    var list = $scope.network.interfaces || [];
    if (!list.some(function(i) { return i.interface === $scope.networkDraft.interface; }) && list.length) {
      $scope.networkDraft.interface = list[0].interface;
    }
    $scope.networkSelectInterface();
    if ($scope.networkForm) $scope.networkForm.$setPristine();
  }
  function load(reset) {
    if (closed || loading || $scope.networkBusy) return;
    loading = true;
    var current = generation;
    $http.get('f_network.php?action=status', {timeout: 18000}).success(function(data) {
      if (current === generation && adopt(data) && (!initialized || reset)) { resetDraft(); initialized = true; }
      loading = false; schedule();
    }).error(function() {
      if (!closed && current === generation) { $scope.networkReady = false; $scope.networkError = 'Cannot reach network service. Unconfirmed changes will be rolled back.'; }
      loading = false; schedule();
    });
  }
  function ipv4(value) {
    if (typeof value !== 'string' || !/^(\d{1,3}\.){3}\d{1,3}$/.test(value)) return false;
    return value.split('.').every(function(s) { return String(Number(s)) === s && Number(s) <= 255; });
  }
  function payload() {
    var d = $scope.networkDraft, dns = d.dnsText.trim() ? d.dnsText.trim().split(/[\s,]+/) : [];
    return {interface:d.interface, mode:d.mode, address:d.mode === 'static' ? d.address.trim() : '',
      prefix:d.mode === 'static' ? d.prefix : 24, gateway:d.mode === 'static' ? d.gateway.trim() : '', dns:d.mode === 'static' ? dns : []};
  }
  $scope.networkValid = function() {
    var p = payload();
    if (!$scope.networkReady || !token || !$scope.network.interfaces.some(function(i) { return i.interface === p.interface; })) return false;
    if (p.mode === 'dhcp') return true;
    return p.mode === 'static' && ipv4(p.address) && p.prefix >= 1 && p.prefix <= 30 && p.prefix % 1 === 0 &&
      ipv4(p.gateway) && p.dns.length > 0 && p.dns.length <= 3 && p.dns.every(ipv4);
  };
  function mutate(action, body) {
    cancelTimer();
    generation++;
    $scope.networkBusy = true;
    $scope.networkError = '';
    var request = angular.copy(body);
    if (action === 'apply') reconnectAddress = body.mode === 'static' ? body.address : '';
    request.csrf = token;
    $http.post('f_network.php?action=' + action, request, {timeout: 20000}).success(function(data) {
      if (adopt(data, true) && action !== 'apply') resetDraft();
      $scope.networkBusy = false; schedule();
    }).error(function() {
      if (!closed) { $scope.networkReady = false; $scope.networkError = 'Connection interrupted. Reconnect to the miner to confirm, or wait for automatic rollback.'; }
      $scope.networkBusy = false; schedule();
    });
  }
  $scope.networkApply = function() {
    if (!$scope.networkValid() || $scope.networkBusy || $scope.network.pending) return;
    mutate('apply', payload());
  };
  $scope.networkConfirm = function() {
    if (!$scope.networkBusy && $scope.networkReady && $scope.network.pending && $scope.network.pending.state === 'awaiting-confirmation') mutate('confirm', {id:$scope.network.pending.id});
  };
  $scope.networkRollback = function() {
    if (!$scope.networkBusy && $scope.network.pending) mutate('rollback', {id:$scope.network.pending.id});
  };
  $scope.networkReconnect = function() {
    var pending = $scope.network.pending, address = (pending && pending.address) || reconnectAddress;
    if (!ipv4(address)) return '';
    return $window.location.protocol + '//' + address + ($window.location.port ? ':' + $window.location.port : '') + '/index.php?network=1#/settings?section=network';
  };
  $scope.networkPendingLabel = function() {
    var p = $scope.network.pending;
    if (!p) return '';
    if (p.state === 'rollback-failed') return 'Restoring previous settings';
    return p.state === 'awaiting-confirmation' ? 'Keep this connection?' : 'Applying network settings';
  };
  $scope.networkCountdown = function() {
    var p = $scope.network.pending, seconds = Math.max(0, Number(p && p.seconds_remaining) || 0);
    return Math.floor(seconds / 60) + ':' + ('0' + Math.floor(seconds % 60)).slice(-2);
  };
  $scope.$watch('bsSettingsTab', function(tab) { cancelTimer(); if (tab === 'network') load(false); });
  $scope.$on('bsNetworkReload', function() { load(true); });
  $scope.$on('$destroy', function() { closed = true; cancelTimer(); });
});
