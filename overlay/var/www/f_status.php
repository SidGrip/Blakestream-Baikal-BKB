<?php
require_once __DIR__ . '/inc/web-security.php';

// cgminer() strips spaces before json_decode, so "Chip Count" arrives as
// ChipCount. StartupSuccessCount has no spaces. A missing or invalid startup
// count stays null; 0 is a reported value and is not replaced with ChipCount.
function bs_status_startup_count($value) {
  if (is_int($value)) {
    return ($value < 0) ? null : $value;
  }
  if (is_string($value) && preg_match('/^(0|[1-9][0-9]*)$/', $value) === 1) {
    $number = (int)$value;
    if ((string)$number !== $value) {
      return null;
    }
    return $number;
  }
  return null;
}

function bs_status_chip_fields($stat) {
  if (!is_array($stat)) {
    $stat = array();
  }
  $startup = null;
  if (array_key_exists('StartupSuccessCount', $stat)) {
    $startup = bs_status_startup_count($stat['StartupSuccessCount']);
  }
  return array(
    'StartupSuccessCount' => $startup,
    'Chips' => isset($stat['ChipCount']) ? $stat['ChipCount'] : 0,
  );
}

function bs_status_estimate_fields($stat, $dev, $baseline, $personal) {
  $custom = !empty($stat['ExperimentalClock']);
  $fields = array(
    'EstimatedClockMHz' => $baseline === null ? null : $baseline['estimated_mhz'],
    'EstimatedHashrate' => $custom ? null : (isset($dev['MHS5s']) ? (float)$dev['MHS5s'] : 0),
    'EstimateSource' => $custom ? 'Unavailable' : 'Model',
    'EstimateSeconds' => $personal === null ? 0 : $personal['seconds'],
  );
  if ($custom && $baseline !== null) {
    $fields['EstimatedHashrate'] = $baseline['ghs'] * 1000.0;
    $fields['EstimateSource'] = 'Lab baseline';
  }
  if ($personal !== null) {
    $fields['EstimatedHashrate'] = $personal['ghs'] * 1000.0;
    $fields['EstimateSource'] = 'Your history';
  }
  if ((isset($dev['Enabled']) && $dev['Enabled'] === 'N') ||
      (isset($dev['EffectivePool']) && (int)$dev['EffectivePool'] < 0)) {
    $fields['EstimatedHashrate'] = 0;
  }
  return $fields;
}

if (defined('BS_STATUS_LIB_ONLY')) {
  return;
}

bs_secure_session_start();

if ( !isset($_SESSION['_logged_']) || $_SESSION['_logged_'] !== true ) {
    die();
}
session_write_close();

/*
f_status gets values that people want to see in realtime
returns success, status data and errors

Blakestream-GaintB modification: include disabled (ascdisable'd) boards in
the devs list so the Status page can still show them with a Pool dropdown.
The factory version filtered them out, which made idle boards disappear
entirely from the UI.
*/
header('Content-type: application/json');

include('inc/cgminer.inc.php');
include_once('inc/accepted-work.php');
include_once('inc/measured-rates.php');
include_once('inc/clock-baseline.php');

function bs_read_fan_control_state($stats = null, $path = '/run/blakestream-control/fan-control-state.json',
    $settings_path = '/opt/scripta/etc/scripta.conf', $now = null) {
  if ($now === null) $now = time();
  $state = array(
    'enabled' => false,
    'mode' => 'manual',
    'status' => '',
    'fan' => 0,
    'min_fan' => 0,
    'target_temp' => 0,
    'mid_temp' => 0,
    'mid_fan' => 0,
    'full_temp' => 0,
    'emergency_temp' => 0,
    'cutoff_temp' => 0,
    'updated' => 0,
    'max_temp' => 0,
    'reason' => '',
    'pending' => false,
    'wait_seconds' => 0,
    'request_revision' => '',
  );
  $json = json_decode((string)@file_get_contents($path), true);
  if (!is_array($json)) $json = array();
  $settings = json_decode((string)@file_get_contents($settings_path), true);
  if (!is_array($settings)) $settings = array();

  $config = array();
  if (isset($json['config']) && is_array($json['config'])) {
    $config = $json['config'];
  }

  $state['enabled'] = !empty($json['enabled']);
  $state['mode'] = isset($json['mode']) ? (string)$json['mode'] : $state['mode'];
  $state['status'] = isset($json['status']) ? (string)$json['status'] : '';
  $state['fan'] = isset($json['fan']) ? (int)$json['fan'] : 0;
  $state['min_fan'] = isset($config['min_fan']) ? (int)$config['min_fan'] : 0;
  $state['target_temp'] = isset($config['target_temp']) ? (float)$config['target_temp'] : 0;
  $state['mid_temp'] = isset($config['mid_temp']) ? (float)$config['mid_temp'] : 0;
  $state['mid_fan'] = isset($config['mid_fan']) ? (int)$config['mid_fan'] : 0;
  $state['full_temp'] = isset($config['full_temp']) ? (float)$config['full_temp'] : 0;
  $state['emergency_temp'] = isset($config['emergency_temp']) ? (float)$config['emergency_temp'] : 0;
  $state['cutoff_temp'] = isset($config['cutoff_temp']) ? (float)$config['cutoff_temp'] : 0;
  $state['updated'] = isset($json['updated']) ? (int)$json['updated'] : 0;
  $state['max_temp'] = isset($json['max_temp']) ? (float)$json['max_temp'] : 0;
  $state['reason'] = isset($json['reason']) ? (string)$json['reason'] : '';
  $state['request_revision'] = isset($settings['userAutoFanRevision']) ? (string)$settings['userAutoFanRevision'] : '';
  $waiting_tick = !empty($settings['userAutoFan']) && $state['request_revision'] !== '' &&
    (!isset($json['request_revision']) || $json['request_revision'] !== $state['request_revision']);
  if ($waiting_tick) {
    $state['enabled'] = true;
    $state['mode'] = 'auto';
    $state['status'] = 'queued';
    $state['reason'] = 'Waiting for fan controller';
    $state['min_fan'] = isset($settings['userAutoFanMin']) ? (int)$settings['userAutoFanMin'] : 40;
  }
  $state['pending'] = $state['status'] === 'queued';
  $since = $waiting_tick ? (isset($settings['userAutoFanRequestedAt']) ? (int)$settings['userAutoFanRequestedAt'] : 0)
    : (isset($json['pending_since']) ? (int)$json['pending_since'] : $state['updated']);

  if (is_array($stats) && !empty($stats['data']['STATS'])) {
    $saw_baikal = false;
    $confirmed = true;
    $actual_fan = null;
    $startup_grace = 120; // Startup plus one 60-second controller interval.
    $recent_start = true;
    $fan_reported = true;

    foreach ($stats['data']['STATS'] as $stat) {
      if (!is_array($stat) || !isset($stat['ID'])) {
        continue;
      }
      $id = (string)$stat['ID'];
      if (strpos($id, 'BKLU') !== 0) {
        continue;
      }
      $saw_baikal = true;
      $elapsed = isset($stat['Elapsed']) ? $stat['Elapsed'] : null;
      if (!is_numeric($elapsed) || $elapsed < 0 || $elapsed > $startup_grace) $recent_start = false;
      if (!isset($stat['FanPercent']) || !is_numeric($stat['FanPercent']) ||
          $stat['FanPercent'] <= 0 || $stat['FanPercent'] > 100) $fan_reported = false;
      $pending = isset($stat['FanPending']) ? $stat['FanPending'] : null;
      $not_pending = $pending === false || $pending === 0 || $pending === '0' || $pending === 'false';
      if (!$not_pending || !isset($stat['FanPercent']) || (int)$stat['FanPercent'] !== $state['fan']) $confirmed = false;
      if (isset($stat['FanPercent'])) {
        $actual_fan = (int)$stat['FanPercent'];
      }
    }

    if ($saw_baikal && $confirmed && !$waiting_tick && $state['pending']) {
      $state['status'] = 'ok';
      $state['pending'] = false;
    }
    if ($actual_fan !== null) $state['fan'] = $actual_fan;
    // A controller tick during restart can leave an unavailable-temperature
    // snapshot until the next minute. Live startup data makes that a bounded
    // recovery state; command failures and persistent errors remain errors.
    $error_age = $now - $state['updated'];
    if ($state['status'] === 'error' && $saw_baikal && $recent_start && $fan_reported &&
        $error_age >= 0 && $error_age <= $startup_grace &&
        strpos($state['reason'], 'temperature unavailable:') === 0) {
      $state['status'] = 'starting';
      $state['reason'] = 'Waiting for auto fan control after miner restart; ' . $state['reason'];
    }
  }
  if ($state['pending']) $state['wait_seconds'] = max(0, min(60, 60 - ($now - $since)));
  return $state;
}

function bs_pool_algo_label($pool) {
  if (!is_array($pool)) {
    return '';
  }

  $type = '';
  $raw = '';
  if (isset($pool['AlgorithmType'])) {
    $type = trim((string)$pool['AlgorithmType']);
  } elseif (isset($pool['Algorithm Type'])) {
    $type = trim((string)$pool['Algorithm Type']);
  }
  if (isset($pool['Algorithm'])) {
    $raw = trim((string)$pool['Algorithm']);
  }

  if ($raw !== '') {
    return $raw;
  }
  return $type;
}

// Miner data
$devs=cgminer('devs');
$pools=array('data' => array());
$stats=array('data' => array());
$miner_api_ok = !empty($devs['data']['DEVS']);
if($miner_api_ok){
  $pools=cgminer('pools');
  $stats=cgminer('stats');
}
$r['status']['minerSession'] = null;
foreach (isset($stats['data']['STATS']) ? $stats['data']['STATS'] : array() as $row) {
  if (!empty($row['DiagPid']) && !empty($row['DiagStartUnix'])) {
    $r['status']['minerSession'] = trim((string)@file_get_contents('/proc/sys/kernel/random/boot_id')) .
      ':' . (int)$row['DiagPid'] . ':' . (int)$row['DiagStartUnix'];
    break;
  }
}

$pool_by_no = array();
$accepted_rates = bs_accepted_rates_by_board();
$measured_rates = bs_measured_read();
$clock_baseline = bs_clock_baseline_read();
$clock_baseline_identity = bs_clock_baseline_identity();
$r['status']['clockBaseline'] = $clock_baseline;
$r['status']['measuredRates'] = array_intersect_key($measured_rates,
    array_flip(array('ok', 'target', 'last_saved', 'unsaved', 'pending_intervals', 'error', 'updated')));
if(!empty($pools['data']['POOLS'])){
  foreach ($pools['data']['POOLS'] as $pool) {
    if (isset($pool['POOL'])) {
      $pool_by_no[(int)$pool['POOL']] = $pool;
    }
  }
}

if($miner_api_ok){
  // Blakestream-GaintB: include ALL devices, not just Enabled=Y. Disabled
  // boards still appear in the UI so the user can re-enable them by
  // assigning a pool.
  foreach ($devs['data']['DEVS'] as $id => $vdev) {
    $r['status']['devs'][$id] = $vdev;
  }

  foreach ($r['status']['devs'] as $id => $dev) {
      $stat = isset($stats['data']['STATS'][$id]) ? $stats['data']['STATS'][$id] : array();
      $r['status']['devs'][$id]['ClockBaselineCompatible'] = bs_clock_baseline_matches($clock_baseline, $stat, $clock_baseline_identity);
      $algo = isset($stat["Algo"]) ? $stat["Algo"] : '-';
      $pool_algo_no = null;
      if (isset($dev['EffectivePool']) && (int)$dev['EffectivePool'] >= 0) {
        $pool_algo_no = (int)$dev['EffectivePool'];
      } elseif (isset($dev['PrimaryPool']) && (int)$dev['PrimaryPool'] >= 0) {
        $pool_algo_no = (int)$dev['PrimaryPool'];
      }
      if ($pool_algo_no !== null && isset($pool_by_no[$pool_algo_no])) {
        $pool_algo = bs_pool_algo_label($pool_by_no[$pool_algo_no]);
        if ($pool_algo !== '') {
          $algo = $pool_algo;
        }
      }

      $chip_fields = bs_status_chip_fields($stat);
      $r['status']['devs'][$id]['Chips']=$chip_fields['Chips'];
      $r['status']['devs'][$id]['StartupSuccessCount']=$chip_fields['StartupSuccessCount'];
      $r['status']['devs'][$id]['Clock']=isset($stat["Clock"]) ? $stat["Clock"] : 0;
      $r['status']['devs'][$id]['RequestedClock']=isset($stat["RequestedClock"]) ? (int)$stat["RequestedClock"] : $r['status']['devs'][$id]['Clock'];
      $r['status']['devs'][$id]['SentClockIndex']=isset($stat["SentClockIndex"]) ? (int)$stat["SentClockIndex"] : 0;
      $r['status']['devs'][$id]['ExperimentalClock']=!empty($stat['ExperimentalClock']);
      $r['status']['devs'][$id]['RequestedClockIndex']=isset($stat['RequestedClockIndex']) ? (int)$stat['RequestedClockIndex'] : 0;
      $r['status']['devs'][$id]['CommandVerified']=!empty($stat["CommandVerified"]);
      $r['status']['devs'][$id]['BridgeStatusEcho']=isset($stat["BridgeStatusEcho"]) ? (int)$stat["BridgeStatusEcho"] : 0;
      $board_id = isset($dev['ASC']) ? (int)$dev['ASC'] : (isset($dev['ID']) ? (int)$dev['ID'] : (int)$id);
      $all_stats = isset($stats['data']['STATS']) ? $stats['data']['STATS'] : array();
      $personal = bs_measured_personal($measured_rates, $board_id, $stat, $dev, $all_stats);
      $baseline = bs_clock_baseline_estimate($clock_baseline, $stat, $clock_baseline_identity);
      foreach (bs_status_estimate_fields($stat, $dev, $baseline, $personal) as $key => $value) {
          $r['status']['devs'][$id][$key] = $value;
      }
      if (isset($accepted_rates[$board_id]) &&
          !bs_accepted_context_matches($accepted_rates[$board_id], $stat, $dev)) {
          unset($accepted_rates[$board_id]);
      }
      $rate15 = isset($accepted_rates[$board_id]['15m']) ? $accepted_rates[$board_id]['15m'] : array('valid' => false);
      $rate1h = isset($accepted_rates[$board_id]['1h']) ? $accepted_rates[$board_id]['1h'] : array('valid' => false);
      $r['status']['devs'][$id]['Accepted15m']=!empty($rate15['valid']) ? $rate15['ghs'] * 1000.0 : null;
      $r['status']['devs'][$id]['Accepted1h']=!empty($rate1h['valid']) ? $rate1h['ghs'] * 1000.0 : null;
      $r['status']['devs'][$id]['Accepted15mSeconds']=isset($rate15['seconds']) ? $rate15['seconds'] : 0;
      $r['status']['devs'][$id]['Accepted1hSeconds']=isset($rate1h['seconds']) ? $rate1h['seconds'] : 0;
      $r['status']['devs'][$id]['Accepted1hFullWindow']=!empty($rate1h['valid']) && !empty($rate1h['full_window']);
      $r['status']['devs'][$id]['AcceptedWorkHashrate']=!empty($rate15['valid']) ? $rate15['ghs'] * 1000.0 : null;
      $r['status']['devs'][$id]['AcceptedWorkHashrate15m']=!empty($rate15['valid']) ? $rate15['ghs'] * 1000.0 : null;
      $r['status']['devs'][$id]['AcceptedWorkHashrate1h']=!empty($rate1h['valid']) ? $rate1h['ghs'] * 1000.0 : null;
      $r['status']['devs'][$id]['Accepted15mMeasuring']=empty($rate15['valid']);
      $r['status']['devs'][$id]['Accepted1hMeasuring']=empty($rate1h['valid']);
      $r['status']['devs'][$id]['Algo']=$algo;
  }
}
else{
  // Blakestream-GaintB: when sgminer isn't running we still need to render
  // 3 placeholder rows in the Status page Devices table so the user can
  // pick a pool for each board and click Apply & Restart. Without these,
  // status.html hides the entire table (chicken-and-egg: no devices means
  // no assignment dropdowns means no way to bootstrap a fresh miner from
  // the UI). The BK-B always has 3 ASCs (IDs 0, 1, 2).
  $r['status']['devs'] = array();
  for ($id = 0; $id < 3; $id++) {
    $r['status']['devs'][$id] = array(
      'Name'           => 'BKLU',
      'ID'             => $id,
      'Enabled'        => 'N',
      'Status'         => 'Idle',
      'Temperature'    => 0,
      'MHS5s'          => 0,
      'MHSav'          => 0,
      'Accepted'       => 0,
      'Rejected'       => 0,
      'HardwareErrors' => 0,
      'Utility'        => 0,
      'LastShareTime'  => 0,
      'Chips'          => 0,
      'StartupSuccessCount' => null,
      'Clock'          => 0,
      'Algo'           => '-',
    );
  }
}

if(!empty($pools['data']['POOLS'])){
  $r['status']['pools'] = $pools['data']['POOLS'];
  $r['status']['minerUp'] = true;
  $r['status']['minerDown'] = false;
}
else{
  $r['status']['pools'] = array();
  $r['status']['minerUp'] = false;
  $r['status']['minerDown'] = true;
}

// Debug miner data
if(!empty($_REQUEST['dev']) && $r['status']['minerUp']){
  $r['status']['devs'][]=array('Name'=>'Hoeba','ID'=>0,'Temperature'=>rand(20,35),'MHS5s'=>rand(80000,100000),'MHSav'=>rand(90000,100000),'LongPoll'=>'N','Getworks'=>200,'Accepted'=>rand(70,200),'Rejected'=>rand(1,10),'HardwareErrors'=>rand(0,50),'Utility'=>1.2,'LastShareTime'=>time()-rand(0,10));
  $r['status']['devs'][]=array('Name'=>'Debug','ID'=>1,'Temperature'=>rand(20,35),'MHS5s'=>rand(40000,50000),'MHSav'=>rand(45000,50000),'LongPoll'=>'N','Getworks'=>1076,'Accepted'=>1324,'Rejected'=>1,'HardwareErrors'=>46,'Utility'=>1.2,'LastShareTime'=>time()-rand(0,40));
}

$devices = 0;
$MHSav = 0;
$MHS5s = 0;
$Accepted = 0;
$Rejected = 0;
$HardwareErrors = 0;
$Utility = 0;
$experimental_clock = false;
$accepted_rate_boards = 0;
$accepted_hour_full = true;
$accepted_rate_totals = array('Accepted15m' => 0.0, 'Accepted1h' => 0.0);
$accepted_rate_measuring = array('Accepted15m' => false, 'Accepted1h' => false);
$accepted_rate_seconds = array('Accepted15m' => 900, 'Accepted1h' => 3600);
$estimate_sources = array();
$estimate_total = 0;
$estimate_missing = false;

if(!empty($r['status']['devs'])){
  foreach ($r['status']['devs'] as $id => $dev) {
    // Compute TotalShares for ALL devices so the UI can render them.
    // Only count enabled+hashing devices in the totals.
    $r['status']['devs'][$id]['TotalShares']=$dev['Accepted']+$dev['Rejected']+$dev['HardwareErrors'];

    // Blakestream-GaintB: sgminer can keep reporting a decaying/synthetic
    // speed for boards that are no longer receiving effective work. For UI
    // totals, treat disabled boards and boards with EffectivePool=-1 as idle.
    // The underlying accepted/rejected/HW counters are left intact.
    $effective_pool = isset($dev['EffectivePool']) ? (int)$dev['EffectivePool'] : null;
    $route_idle = ($dev['Enabled'] == 'Y' && $effective_pool !== null && $effective_pool < 0);
    if ($route_idle) {
      $r['status']['devs'][$id]['MHS5s'] = 0;
      $r['status']['devs'][$id]['MHSav'] = 0;
      $r['status']['devs'][$id]['Status'] = 'Idle';
      $dev['MHS5s'] = 0;
      $dev['MHSav'] = 0;
      $dev['Status'] = 'Idle';
    } elseif ($dev['Enabled'] == 'N' && $dev['MHS5s'] == 0) {
      $r['status']['devs'][$id]['MHSav'] = 0;
      $dev['MHSav'] = 0;
    }

    if(($dev['Enabled'] == 'Y')) {
        if (!empty($dev['ExperimentalClock'])) $experimental_clock = true;
        if (!$route_idle) {
            $accepted_rate_boards++;
            $accepted_hour_full = $accepted_hour_full && !empty($dev['Accepted1hFullWindow']);
            $estimate_sources[isset($dev['EstimateSource']) ? $dev['EstimateSource'] : 'No history'] = true;
            if (!isset($dev['EstimatedHashrate'])) $estimate_missing = true;
            else $estimate_total += $dev['EstimatedHashrate'];
            foreach ($accepted_rate_totals as $field => $total) {
                $accepted_rate_seconds[$field] = min($accepted_rate_seconds[$field],
                    isset($dev[$field . 'Seconds']) ? $dev[$field . 'Seconds'] : 0);
                if (!isset($dev[$field]) || !is_numeric($dev[$field]) ||
                    !empty($dev[$field . 'Measuring'])) {
                    $accepted_rate_measuring[$field] = true;
                } else {
                    $accepted_rate_totals[$field] += (float)$dev[$field];
                }
            }
        }
        $devices += $dev['MHS5s']>0?1:0; // Only count hashing devices
        $MHS5s += $dev['MHS5s'];
        $MHSav += $dev['MHSav'];
        $Accepted += $dev['Accepted'];
        $Rejected += $dev['Rejected'];
        $HardwareErrors += $dev['HardwareErrors'];
        $Utility += $dev['Utility'];
    }
  }
}


$ret = explode(' ',$MHS5s);
$KHS5s = ($ret[0]/1024). " Kh/s";
$ret = explode(' ',$MHSav);
$KHSav = ($ret[0]/1024). " Kh/s";

$r['status']['dtot']=array(
  'ExperimentalClock'=>$experimental_clock,
  'EstimatedHashrate'=>(!$estimate_missing && count($estimate_sources) === 1) ? $estimate_total : null,
  'EstimateSource'=>count($estimate_sources) === 1 ? key($estimate_sources) : 'Mixed sources',
  'devices'=>$devices,
  'MHS5s'=>$MHS5s,
  'MHSav'=>$MHSav,
  'KHS5s'=>$KHS5s,
  'KHSav'=>$KHSav,
  'Accepted'=>$Accepted,
  'Rejected'=>$Rejected,
  'HardwareErrors'=>$HardwareErrors,
  'Utility'=>$Utility,
  'TotalShares'=>$Accepted+$Rejected+$HardwareErrors);

foreach ($accepted_rate_totals as $field => $total) {
    $measuring = !$accepted_rate_boards || $accepted_rate_measuring[$field];
    $r['status']['dtot'][$field] = $measuring ? null : $total;
    $r['status']['dtot'][$field . 'Measuring'] = $measuring;
    $r['status']['dtot'][$field . 'Seconds'] = $accepted_rate_boards ? $accepted_rate_seconds[$field] : 0;
}
$r['status']['dtot']['Accepted1hFullWindow'] = $accepted_rate_boards > 0 &&
    !$accepted_rate_measuring['Accepted1h'] && $accepted_hour_full;

$r['status']['fanControl'] = bs_read_fan_control_state($stats);

// CPU intensive stuff
if(!empty($_REQUEST['all'])){
  $ret = sys_getloadavg();
  $r['status']['pi']['load'] = $ret[2];
  $ret = explode(' ', exec('cat /proc/uptime'));
  $r['status']['pi']['uptime'] = $ret[0];
  $r['status']['pi']['temp'] = exec('cat /sys/class/thermal/thermal_zone0/temp');

  if($miner_api_ok){
    // What other interesting stuff is in summary?
    $summary=cgminer('summary');
    if(!empty($summary['data']['SUMMARY'][0]['Elapsed'])){
      $r['status']['uptime'] = $summary['data']['SUMMARY'][0]['Elapsed'];
    }
    else{
      $r['status']['uptime'] = 0;
    }
  }
  else{
    $r['status']['uptime'] = 0;
  }
}

$r['status']['time'] = time();

echo json_encode($r);
?>
