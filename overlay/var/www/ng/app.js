'use strict';

// Declare app level module which depends on filters, and services
angular.module('Scripta', ['Scripta.filters', 'Scripta.services', 'Scripta.directives', 'Scripta.controllers'])
.config(['$httpProvider', function($httpProvider) {
  var token = window.BKB_CSRF_TOKEN;
  if (typeof token === 'string' && /^[0-9a-f]{64}$/.test(token)) {
    $httpProvider.defaults.headers.common['X-BKB-CSRF'] = token;
  }
}])
.config(['$routeProvider', function($routeProvider) {
  var v = '?v=20260930-ui-backup';
  $routeProvider.when('/status',       {templateUrl: 'partials/status.html?v=fan-header-1', controller: 'CtrlStatus'});
  $routeProvider.when('/miner',        {templateUrl: 'partials/miner.html?v=fan-inputs-1', controller: 'CtrlMiner'});
  $routeProvider.when('/settings',     {templateUrl: 'partials/settings.html?v=collector-wait-1', controller: 'CtrlSettings'});
  $routeProvider.when('/backup',       {templateUrl: 'partials/backup.html' + v,       controller: 'CtrlBackup'});
  $routeProvider.when('/pool-history',     {templateUrl: 'partials/pool-history.html' + v,     controller: 'CtrlPoolHistory'});
  $routeProvider.when('/failover-history', {templateUrl: 'partials/failover-history.html' + v, controller: 'CtrlFailoverHistory'});
  $routeProvider.when('/clock-history',    {templateUrl: 'partials/clock-history.html?v=clock-index-1',    controller: 'CtrlClockHistory'});
  $routeProvider.when('/terminal',         {templateUrl: 'partials/terminal.html?v=https-terminal-1'});
  $routeProvider.when('/dev-tools',        {templateUrl: 'partials/dev-tools.html?v=sgminer-api-readonly-1', controller: 'CtrlDevTools'});
  $routeProvider.otherwise({redirectTo: '/status'});
}]);
