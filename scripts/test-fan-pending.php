<?php
define('BKB_MINER_OPTION_POLICY', __DIR__ . '/../overlay/opt/scripta/policy/miner-options.json');
define('BKB_SETTINGS_LIB_ONLY', true);
require __DIR__ . '/../overlay/var/www/f_settings.php';
$source = file_get_contents(__DIR__ . '/../overlay/var/www/f_status.php');
$start = strpos($source, 'function bs_read_fan_control_state(');
eval(substr($source, $start, strpos($source, 'function bs_pool_algo_label(', $start) - $start));
function check_fan($condition, $message) {
  if (!$condition) throw new Exception($message);
}
$old = array('userAutoFan' => true, 'userAutoFanMin' => 40);
$new = bs_fan_request_settings($old, array_merge($old, array('userAutoFanMin' => 20)), 1000);
check_fan($new['userAutoFanRequestedAt'] === 1000, 'request timestamp');
check_fan(!empty($new['userAutoFanRevision']), 'unique revision');
$same = bs_fan_request_settings($new, array_merge($new, array('userCharts' => true,
  'userAutoFanRevision' => 'forged', 'userAutoFanRequestedAt' => 9000)), 1010);
check_fan($same['userAutoFanRevision'] === $new['userAutoFanRevision'], 'unrelated saves preserve revision');
check_fan($same['userAutoFanRequestedAt'] === 1000, 'request metadata is server owned');
$next = bs_fan_request_settings($new, array_merge($new, array('userAutoFanMin' => 30)), 1000);
check_fan($next['userAutoFanRevision'] !== $new['userAutoFanRevision'], 'two saves in same second differ');
$state = tempnam(sys_get_temp_dir(), 'fan-state-');
$settings = tempnam(sys_get_temp_dir(), 'fan-settings-');
try {
  file_put_contents($settings, json_encode($new));
  $stats = array('data' => array('STATS' => array(array('ID' => 'BKLU0', 'FanPercent' => 70, 'FanPending' => false))));
  $r = bs_read_fan_control_state($stats, $state, $settings, 1000);
  check_fan($r['pending'] && $r['wait_seconds'] === 60, 'pending before first controller tick');
  check_fan($r['fan'] === 70, 'actual fan, not requested minimum');
  $json = array('enabled' => true, 'status' => 'queued', 'fan' => 80,
    'request_revision' => $new['userAutoFanRevision'], 'pending_since' => 1000, 'updated' => 1010);
  file_put_contents($state, json_encode($json));
  $r = bs_read_fan_control_state($stats, $state, $settings, 1030);
  check_fan($r['pending'] && $r['wait_seconds'] === 30, 'wrong target must not confirm');
  $r = bs_read_fan_control_state(null, $state, $settings, 1070);
  check_fan($r['pending'] && $r['wait_seconds'] === 0, 'expired timer is not completion');
  $stats['data']['STATS'][0]['FanPercent'] = 80;
  unset($stats['data']['STATS'][0]['FanPending']);
  check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['pending'], 'missing pending field is not an acknowledgement');
  $stats['data']['STATS'][0]['FanPending'] = true;
  check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['pending'], 'queued target is not applied');
  $stats['data']['STATS'][0]['FanPending'] = false;
  $r = bs_read_fan_control_state($stats, $state, $settings, 1040);
  check_fan(!$r['pending'] && $r['status'] === 'ok' && $r['fan'] === 80, 'matching actual target confirms');
  $json['status'] = 'error';
  file_put_contents($state, json_encode($json));
  check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['status'] === 'error', 'errors stay errors');
  $json['reason'] = 'temperature unavailable: [Errno 111] Connection refused';
  file_put_contents($state, json_encode($json));
  $stats['data']['STATS'][0]['Elapsed'] = 20;
  $r = bs_read_fan_control_state($stats, $state, $settings, 1040);
  check_fan($r['status'] === 'starting' && !$r['pending'] && $r['fan'] === 80,
    'recent restart with live fan data waits for auto control');
  check_fan(strpos($r['reason'], 'after miner restart') !== false, 'restart wait explains its cause');
  $stats['data']['STATS'][0]['Elapsed'] = 120;
  check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['status'] === 'starting',
    'startup grace includes the next controller interval');
  foreach (array(121, -1, null, 'invalid') as $elapsed) {
    $stats['data']['STATS'][0]['Elapsed'] = $elapsed;
    check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['status'] === 'error',
      'persistent or unproven startup errors stay errors');
  }
  $stats['data']['STATS'][0]['Elapsed'] = 20;
  $stats['data']['STATS'][0]['FanPercent'] = 0;
  check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['status'] === 'error',
    'zero fan report is not a normal startup indication');
  $stats['data']['STATS'][0]['FanPercent'] = 80;
  $json['updated'] = 900;
  file_put_contents($state, json_encode($json));
  check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['status'] === 'error',
    'stale controller error is not hidden');
  $json['updated'] = 1010;
  $json['reason'] = 'failed to set 80%: rejected';
  file_put_contents($state, json_encode($json));
  check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['status'] === 'error',
    'fan-command failure is not a startup indication');
  $json['status'] = 'emergency';
  file_put_contents($state, json_encode($json));
  check_fan(bs_read_fan_control_state($stats, $state, $settings, 1040)['status'] === 'emergency',
    'emergency state is preserved');
} finally {
  unlink($state);
  unlink($settings);
}
$history = file_get_contents(__DIR__ . '/../overlay/var/www/f_blakestream.php');
$start = strpos($history, '$_charts_saveto =');
$end = strpos($history, '$CLOCK_HISTORY_RAM', $start);
foreach (array(array(), array('userChartsSaveTo' => 'RAM'), array('userChartsSaveTo' => 'SD')) as $_persist_cfg) {
  eval(substr($history, $start, $end - $start));
  $expected = isset($_persist_cfg['userChartsSaveTo']) && $_persist_cfg['userChartsSaveTo'] === 'SD' ? '/opt/scripta/var' : '/run/blakestream';
  check_fan($HASHRATE_HISTORY === $expected . '/hashrate-history.json', 'chart reader defaults to RAM');
}
echo "PASS fan request tracking, 60-second pending state, ACK checks and RAM chart default\n";
