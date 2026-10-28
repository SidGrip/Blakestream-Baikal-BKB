<?php
require_once __DIR__ . '/inc/firmware-identity.php';
require_once __DIR__ . '/inc/experimental-clock.php';
require_once __DIR__ . '/inc/web-auth.php';
require_once __DIR__ . '/inc/web-security.php';

function bs_miner_option_policy_path() {
  return defined('BKB_MINER_OPTION_POLICY')
    ? BKB_MINER_OPTION_POLICY : '/opt/scripta/policy/miner-options.json';
}

function bs_miner_option_policy() {
  $raw = @file_get_contents(bs_miner_option_policy_path());
  $policy = is_string($raw) ? json_decode($raw, true) : null;
  if (!is_array($policy) || !isset($policy['version']) || $policy['version'] !== 1 ||
      !isset($policy['allowed_keys']) || !is_array($policy['allowed_keys']) ||
      !isset($policy['fixed_values']) || !is_array($policy['fixed_values'])) return null;
  $allowed = array();
  foreach ($policy['allowed_keys'] as $key) {
    if (!is_string($key) || $key === '' || isset($allowed[$key])) return null;
    $allowed[$key] = true;
  }
  foreach ($policy['fixed_values'] as $key => $value) {
    if (!isset($allowed[$key]) || !is_string($value)) return null;
  }
  return array('allowed' => $allowed, 'fixed' => $policy['fixed_values']);
}

function bs_miner_option_security_error($options) {
  $policy = bs_miner_option_policy();
  if ($policy === null) return 'Miner option security policy is unavailable';
  if (!is_array($options)) return 'Miner options must be a list';
  if ($options !== array_values($options)) return 'Miner options must be a sequential list';
  if (count($options) > 32) return 'Too many miner options';
  $seen = array();
  foreach ($options as $option) {
    if (!is_array($option) || !array_key_exists('key', $option) || !array_key_exists('value', $option) ||
        !is_string($option['key'])) return 'Each miner option must contain a string key and value';
    $key = $option['key'];
    if (!isset($policy['allowed'][$key])) return 'Unsupported miner option: ' . $key;
    if (isset($seen[$key])) return 'Duplicate miner option: ' . $key;
    $seen[$key] = true;
    $value = $option['value'];
    if (isset($policy['fixed'][$key]) && (!is_string($value) || $value !== $policy['fixed'][$key]))
      return $key . ' is fixed by security policy';
    if ($key === 'baikal-options' && (!is_string($value) ||
        preg_match('/^[0-9]{1,4}(?::[0-9]{1,3}){0,2}$/D', $value) !== 1))
      return 'baikal-options must contain one to three colon-separated integers';
    if ($key === 'baikal-clock-mode' && $value !== 'global' && $value !== 'individual')
      return 'baikal-clock-mode must be global or individual';
    if ($key === 'baikal-board-clocks' && (!is_string($value) ||
        preg_match('/^[0-9]{1,4}:[0-9]{1,4}:[0-9]{1,4}$/D', $value) !== 1))
      return 'baikal-board-clocks must contain three colon-separated integers';
    if ($key === 'baikal-experimental-clock-index' &&
        !($value === true || $value === false || $value === 'true' || $value === 'false'))
      return 'baikal-experimental-clock-index must be true or false';
    if ($key === 'baikal-board-clock-index' && (!is_string($value) ||
        preg_match('/^(?:[8-9]|[1-2][0-9]|3[0-2])(?::(?:[8-9]|[1-2][0-9]|3[0-2])){2}$/D', $value) !== 1))
      return 'baikal-board-clock-index must contain three integers from 8 to 32';
    if ($key === 'blakestream-clock-scope' && $value !== 'global' && $value !== 'individual')
      return 'blakestream-clock-scope must be global or individual';
    if ($key === 'scan-time' && (!is_string($value) || preg_match('/^(?:0|[1-9][0-9]{0,2})$/D', $value) !== 1))
      return 'scan-time must be an integer from 0 to 999';
    if ($key === 'no-submit-stale' && !($value === true || $value === false || $value === 'true' || $value === 'false'))
      return 'no-submit-stale must be true or false';
    if ($key === 'baikal-fan' && (!is_string($value) || preg_match('/^(?:4[0-9]|[5-9][0-9]|100)$/D', $value) !== 1))
      return 'baikal-fan must be an integer from 40 to 100';
  }
  return null;
}

function bs_normalize_fan_settings($settings) {
  $fields = array('userAutoFanMin' => 40, 'userAutoFanTarget' => 36,
    'userAutoFanMidTemp' => 43, 'userAutoFanMidFan' => 75, 'userAutoFanFull' => 50);
  if (!array_intersect_key($settings, $fields)) return $settings;
  $curve = array();
  foreach ($fields as $key => $fallback) {
    $value = isset($settings[$key]) && is_numeric($settings[$key]) ? (int)$settings[$key] : $fallback;
    $percent = $key === 'userAutoFanMin' || $key === 'userAutoFanMidFan';
    $curve[$key] = max(20, min($percent ? 100 : 120, $value));
  }
  $target = min(118, $curve['userAutoFanTarget']);
  $full = max($target + 2, $curve['userAutoFanFull']);
  $mid = isset($settings['userAutoFanMidTemp']) && is_numeric($settings['userAutoFanMidTemp'])
    ? $curve['userAutoFanMidTemp'] : (int)round(($target + $full) / 2);
  $curve['userAutoFanTarget'] = $target;
  $curve['userAutoFanFull'] = $full;
  $curve['userAutoFanMidTemp'] = max($target + 1, min($full - 1, $mid));
  $curve['userAutoFanMidFan'] = max($curve['userAutoFanMin'], $curve['userAutoFanMidFan']);
  return array_merge($settings, $curve);
}

function bs_fan_request_settings($old, $new, $now = null) {
  $keys = array('userAutoFan', 'userAutoFanMin', 'userAutoFanTarget', 'userAutoFanMidTemp',
    'userAutoFanMidFan', 'userAutoFanFull', 'userAutoFanEmergency', 'userAutoFanStep', 'userAutoFanMaxDownStep');
  $changed = false;
  foreach ($keys as $key) {
    $before = isset($old[$key]) ? $old[$key] : null;
    $after = isset($new[$key]) ? $new[$key] : null;
    if ($before != $after) $changed = true;
  }
  foreach (array('userAutoFanRevision', 'userAutoFanRequestedAt') as $key) {
    unset($new[$key]);
    if (isset($old[$key])) $new[$key] = $old[$key];
  }
  if ($changed) {
    $new['userAutoFanRevision'] = bin2hex(openssl_random_pseudo_bytes(12));
    $new['userAutoFanRequestedAt'] = $now === null ? time() : $now;
  }
  return $new;
}

if (!defined('BKB_SETTINGS_LIB_ONLY')) {
bs_secure_session_start();

if ( !isset($_SESSION['_logged_']) || $_SESSION['_logged_'] !== true ) {
	echo json_encode(false);
	die();
}

/*
f_settings syncs settings in different files and always returns new state
returns settings and ['date']
*/
header('Content-type: application/json');
include('inc/cgminer.inc.php');
$configFolder='/opt/scripta/etc/';
$configScripta=$configFolder.'scripta.conf';
$configUipwd=$configFolder.'uipasswd';
$configOptns=$configFolder.'miner.options.json';
$configPools=$configFolder.'miner.pools.json';
$configMiner=$configFolder.'miner.conf';
$minerRestart='/opt/scripta/startup/miner-restart.sh';
$r=array('data'=>array(), 'info'=>array());

$settingsMethod = isset($_SERVER['REQUEST_METHOD']) ? strtoupper($_SERVER['REQUEST_METHOD']) : '';
$settingsSource = $settingsMethod === 'POST' ? $_POST : ($settingsMethod === 'GET' ? $_GET : array());
$settingsActions = array('pass', 'settings', 'pools', 'options', 'timezone');
$settingsFound = array();
foreach ($settingsActions as $candidate) {
  if (array_key_exists($candidate, $settingsSource)) $settingsFound[] = $candidate;
}
if (count($settingsFound) !== 1 || !is_string($settingsSource[$settingsFound[0]])) {
  http_response_code(400);
  echo json_encode(array('ok' => false, 'error' => 'exactly one settings action is required'));
  exit;
}
$settingsAction = $settingsFound[0];
$settingsPayload = $settingsSource[$settingsAction];
$settingsMode = $settingsMethod === 'GET' && in_array($settingsAction, array('settings', 'pools', 'options'), true)
  ? 'read' : 'mutation';
$settingsIntegrity = bs_request_integrity_check($settingsMethod, $settingsMode,
  isset($_SESSION['web_csrf']) ? $_SESSION['web_csrf'] : '', $_SERVER);
if (!$settingsIntegrity['ok']) {
  http_response_code($settingsIntegrity['status']);
  echo json_encode(array('ok' => false, 'error' => $settingsIntegrity['error']));
  exit;
}
if ($settingsMode === 'read' && json_decode($settingsPayload, true) !== 'load') {
  http_response_code(400);
  echo json_encode(array('ok' => false, 'error' => 'GET is load-only'));
  exit;
}

function write_config_file($path, $contents, $mode = 0664) {
  $dir = dirname($path);
  if (!is_dir($dir) || !is_writable($dir)) return false;
  $tmp = @tempnam($dir, basename($path) . '.tmp.');
  if ($tmp === false) return false;
  if (@file_put_contents($tmp, $contents) === false) {
    @unlink($tmp);
    return false;
  }
  @chmod($tmp, $mode);
  if (!@rename($tmp, $path)) {
    @unlink($tmp);
    return false;
  }
  @chmod($path, $mode);
  return true;
}

function trigger_miner_restart($launcher) {
  if (!is_file($launcher) || !is_executable($launcher)) return false;

  $preflight = 'sudo -n ' . escapeshellarg($launcher) . ' --preflight 2>&1';
  @exec($preflight, $preflightOutput, $preflightRc);
  if ($preflightRc !== 0) return false;

  $cmd = 'sudo -n ' . escapeshellarg($launcher) . ' > /tmp/blakestream-launcher.log 2>&1 &';
  @exec($cmd, $output, $rc);
  return ($rc === 0);
}

function add_miner_restart_info(&$r, $started) {
  if ($started) {
    $r['info'][]=array('type' => 'info', 'text' => 'Miner: restart triggered');
  } else {
    $r['info'][]=array('type' => 'warning', 'text' => 'Miner restart pending; automatic helper repair will retry shortly');
  }
}


// Set new web-dashboard password. Linux credentials are managed separately over SSH.
if ($settingsAction === 'pass') {
  $pass = json_decode($settingsPayload);
  if (!bs_web_auth_password_valid($pass)) {
    $r['info'][]=array('type' => 'error', 'text' => 'Web password must contain 1 to 72 bytes and no NUL bytes');
  } elseif (!bs_web_auth_save($configUipwd, $pass)) {
    $r['info'][]=array('type' => 'error', 'text' => 'Web password save failed');
  } else {
    $r['info'][]=array('type' => 'success', 'text' => 'Web password saved');
  }
}

// Manage settings
elseif ($settingsAction === 'settings') {
  $newdata   = json_decode($settingsPayload, true);
  $r['data'] = json_decode(@file_get_contents($configScripta), true);
  if (!is_array($r['data'])) $r['data'] = array();

  // Sync current with new settings
  if(!empty($newdata)&&is_array($newdata)){
    $oldSettings = $r['data'];
    foreach ($newdata as $key => $value) {
      $r['data'][$key]=$value;
    }
    $r['data'] = bs_fan_request_settings($oldSettings, bs_normalize_fan_settings($r['data']));
    $savedLogTarget = isset($oldSettings['userDevLogTarget']) && is_string($oldSettings['userDevLogTarget'])
      ? strtoupper($oldSettings['userDevLogTarget']) : '';
    $logTarget = isset($r['data']['userDevLogTarget']) && is_string($r['data']['userDevLogTarget'])
      ? strtoupper($r['data']['userDevLogTarget']) : 'RAM';
    // Log destinations are changed through storage_set_target, not stale Settings drafts.
    if (in_array($savedLogTarget, array('SD', 'RAM'), true)) $logTarget = $savedLogTarget;
    $r['data']['userDevLogTarget'] = in_array($logTarget, array('SD', 'RAM'), true) ? $logTarget : 'RAM';
	    if (!empty($r['data']['userAdjustableClocks']) && !firmware_custom_active()) {
	      $r['data']['userAdjustableClocks'] = false;
	      $r['info'][]=array('type' => 'warning', 'text' => 'Adjustable Clocks not enabled; bridge firmware is stock or unknown');
	    }
	    if (!write_config_file($configScripta, json_encode($r['data'], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES))) {
	      $r['data'] = $oldSettings;
	      $r['settingsSaved'] = false;
	      $r['info'][]=array('type' => 'error', 'text' => 'Configuration save failed: ' . $configScripta);
	    } else {
      $r['settingsSaved'] = true;
      $r['info'][]=array('type' => 'success', 'text' => 'Configuration saved');
      if (!empty($r['data']['userAutoFan']) && isset($r['data']['userAutoFanRevision']) &&
          (!isset($oldSettings['userAutoFanRevision']) || $oldSettings['userAutoFanRevision'] !== $r['data']['userAutoFanRevision'])) {
        $r['fanRequest'] = $r['data']['userAutoFanRevision'];
      }
      if (empty($r['data']['userAdjustableClocks'])) {
        $clockClamped = clampBaikalClockToStock();
        if ($clockClamped) {
          if (!minerConfigGenerate()) {
            $r['info'][]=array('type' => 'error', 'text' => 'Miner configuration was not rewritten');
          } else {
            $r['info'][]=array('type' => 'info', 'text' => 'Adjustable Clocks disabled; requested clock set to 400 MHz');
            add_miner_restart_info($r, trigger_miner_restart($minerRestart));
          }
        }
      }
    }
  }
  // Load current settings
  else{
  }

  if(isset($r['data']['userTimezone'])){
    date_default_timezone_set($r['data']['userTimezone']);
    $r['data']['date'] = date('Y-m-d H:i:s');
  }
}

// Manage pools
elseif ($settingsAction === 'pools') {
  $newdata   = json_decode($settingsPayload, true);

  $r['data'] = json_decode(@file_get_contents($configPools), true);
  if (is_array($r['data'])) {
    foreach ($r['data'] as $id => $p)
    {
      if (!is_array($p)) $p = array();
      $r['data'][$id]['user'] = str_replace('/ ','/+',isset($p['user']) ? $p['user'] : '');
      $r['data'][$id]['url']  = str_replace('stratum tcp','stratum+tcp',isset($p['url']) ? $p['url'] : '');
      if(isset($r['data'][$id]['extranonce']) && $r['data'][$id]['extranonce'] == "true") {
        $r['data'][$id]['extranonce'] = true;
      }
      if (!isset($r['data'][$id]['priority'])) $r['data'][$id]['priority'] = pool_priority($p);
    }
  } else {
    $r['data'] = array();
  }
  $m=0;
  if (is_array($newdata)) {
    foreach ($newdata as $id => $p)
    {
      if (!is_array($p)) $p = array();
      $newdata[$id]['user'] = str_replace('/ ','/+',isset($p['user']) ? $p['user'] : '');
      $newdata[$id]['url']  = str_replace('stratum tcp','stratum+tcp',isset($p['url']) ? $p['url'] : '');
      if (!isset($newdata[$id]['priority'])) $newdata[$id]['priority'] = pool_priority($p);
      if(isset($p['prio']) && $p['prio'] > $m) $m=$p['prio'];
    }
  }
  
  if(is_array($newdata)){
      //var_dump($newdata);
      usort($newdata,'cmp');
      //var_dump($newdata);
  }else{
      usort($r['data'],'cmp');
  }
  //
  // Overwrite current with new pools
  if(!empty($newdata)&&is_array($newdata)){
    $savedata = $newdata;
    foreach ($savedata as $id => $p)
    {
      if(isset($savedata[$id]['extranonce']) && $savedata[$id]['extranonce'] == true) {
        $savedata[$id]['extranonce']  = "true";
      }
    }

    if (!write_config_file($configPools, json_encode($savedata, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES))) {
      $r['info'][]=array('type' => 'error', 'text' => 'Pools config save failed: ' . $configPools);
    } else {
      minerConfigGenerate();
      $r['data']=$newdata;

      $r['info'][]=array('type' => 'success', 'text' => 'Pools config saved');
      add_miner_restart_info($r, trigger_miner_restart($minerRestart));
    }
  }
  // Load current settings
  elseif(!empty($r['data'])&&is_array($r['data'])){
    $r['info'][]=array('type' => 'info', 'text' => 'Pools config loaded');
  }
  // Load new settings
  else{
    $r['data']=array(array('url'=>'empty'));
    $r['info'][]=array('type' => 'error', 'text' => 'Pools config not found');
  }
}

// Manage miner.conf
elseif ($settingsAction === 'options') {
  $newdata   = json_decode($settingsPayload, true);
  $r['data'] = ensureBaikalOptions(json_decode(@file_get_contents($configOptns), true));

  // Overwrite current with new config
  if(!empty($newdata)&&is_array($newdata)){
    $optionSecurityError = bs_miner_option_security_error($newdata);
    $boardClockError = ($optionSecurityError === null) ? validateBaikalBoardClocks($newdata) : null;
    $experimentalError = ($boardClockError === null) ? baikalExperimentalClockError($newdata) : null;
    $experimentalPolicyError = ($boardClockError === null && $experimentalError === null)
      ? baikalExperimentalPolicyError($newdata) : null;
    if ($optionSecurityError !== null) {
      $r['info'][]=array('type' => 'error', 'text' => $optionSecurityError);
    } elseif ($boardClockError !== null) {
      $r['info'][]=array('type' => 'error', 'text' => $boardClockError);
    } elseif ($experimentalError !== null) {
      $r['info'][]=array('type' => 'error', 'text' => $experimentalError);
    } elseif ($experimentalPolicyError !== null) {
      $r['info'][]=array('type' => 'error', 'text' => $experimentalPolicyError);
    } else {
      $newdata = baikalNormalizeExperimentalOptions(ensureBaikalOptions($newdata));
    }
    if ($optionSecurityError === null && $boardClockError === null && $experimentalError === null && $experimentalPolicyError === null &&
        !write_config_file($configOptns, json_encode($newdata, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES))) {
      $r['info'][]=array('type' => 'error', 'text' => 'Miner options save failed: ' . $configOptns);
    } elseif ($optionSecurityError === null && $boardClockError === null && $experimentalError === null && $experimentalPolicyError === null) {
      if (!minerConfigGenerate()) {
        $r['info'][]=array('type' => 'error', 'text' => 'Miner configuration was not rewritten');
      } else {
        $r['data']=$newdata;
        $r['info'][]=array('type' => 'success', 'text' => 'Miner options saved');
        add_miner_restart_info($r, trigger_miner_restart($minerRestart));
      }
    }
  }
  // Load current settings
  elseif(!empty($r['data'])&&is_array($r['data'])){
    if (!baikalExperimentalLaunchAllowed())
      $r['data'] = baikalStripExperimentalOptions($r['data']);
    $r['info'][]=array('type' => 'info', 'text' => 'Miner options loaded');
  }
  // Load new settings
  else{
    $r['data']=ensureBaikalOptions(array());
    $r['info'][]=array('type' => 'error', 'text' => 'Miner options not found');
  }
}

// Persist user-selected timezone (display-only — system clock stays UTC).
// The dashboard renders timestamps in this zone via PHP
// date_default_timezone_set and Intl.DateTimeFormat on the JS side.
elseif ($settingsAction === 'timezone') {
  $timezone = json_decode($settingsPayload);
  ini_set( 'date.timezone', $timezone );
  putenv('TZ=' . $timezone);
  date_default_timezone_set($timezone);
  $settings = json_decode(@file_get_contents($configScripta), true);
  if (!is_array($settings)) $settings = array();
  $settings['userTimezone'] = $timezone;
  if (!write_config_file($configScripta, json_encode($settings, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES))) {
    $r['info'][]=array('type' => 'error', 'text' => 'Timezone save failed: ' . $configScripta);
  } else {
    $r['data']['date'] = date('Y-m-d H:i:s');
    $r['info'][]=array('type' => 'info', 'text' => 'Timezone is '.$timezone );
  }
}

if (isset($r['data']['userAdjustableClocks']) && !empty($r['data']['userAdjustableClocks']) && !firmware_custom_active()) {
  $r['data']['userAdjustableClocks'] = false;
}

echo json_encode($r);
}

function baikalExperimentalFlagValue($value, &$parsed) {
  if ($value === true || $value === false) {
    $parsed = $value;
    return true;
  }
  if (is_string($value) && ($value === 'true' || $value === 'false')) {
    $parsed = ($value === 'true');
    return true;
  }
  return false;
}

function baikalCanonicalBoardPll($value) {
  if (!is_string($value)) return null;
  /* D makes $ match only the end, so a final newline is not an index tuple. */
  if (!preg_match('/^(?:[8-9]|[1-2][0-9]|3[0-2])(?::(?:[8-9]|[1-2][0-9]|3[0-2])){2}$/D', $value))
    return null;
  return $value;
}

function baikalClockScopeValue($value) {
  if (!is_string($value)) return null;
  if ($value === 'global' || $value === 'individual') return $value;
  return null;
}

function baikalBoardPllIndicesEqual($clock_index) {
  if (!is_string($clock_index)) return false;
  $parts = explode(':', $clock_index);
  return count($parts) === 3 && $parts[0] === $parts[1] && $parts[1] === $parts[2];
}

function baikalSavedClockScope($options) {
  if (!is_array($options)) return null;
  $scope = null;
  $seen = 0;
  foreach ($options as $option) {
    if (!is_array($option) || !isset($option['key']) || $option['key'] !== 'blakestream-clock-scope')
      continue;
    $seen++;
    if ($seen !== 1) return null;
    $scope = array_key_exists('value', $option) ? baikalClockScopeValue($option['value']) : null;
    if ($scope === null) return null;
  }
  return $scope;
}

function baikalExplicitIndividualMode($options) {
  $seen = 0;
  $individual = false;
  if (!is_array($options)) return false;
  foreach ($options as $option) {
    if (!is_array($option) || !isset($option['key']) || $option['key'] !== 'baikal-clock-mode') continue;
    $seen++;
    $value = isset($option['value']) ? $option['value'] : null;
    $individual = ($value === 'individual');
  }
  return $seen === 1 && $individual;
}

function baikalExperimentalFlagOn($options) {
  $flag = false;
  $valid = false;
  if (!is_array($options)) return false;
  foreach ($options as $option) {
    if (!is_array($option) || !isset($option['key']) || $option['key'] !== 'baikal-experimental-clock-index') continue;
    if (!array_key_exists('value', $option) || !baikalExperimentalFlagValue($option['value'], $flag))
      return false;
    $valid = true;
  }
  return $valid && $flag === true;
}

function baikalExperimentalClockError($options) {
  if (!is_array($options)) return 'miner options must be a list';
  $flagSeen = false;
  $flag = false;
  $clock_indexSeen = false;
  $clock_index = null;
  $scopeSeen = false;
  $scope = null;
  foreach ($options as $option) {
    if (!is_array($option) || !isset($option['key'])) continue;
    if ($option['key'] === 'baikal-experimental-clock-index') {
      if ($flagSeen) return 'baikal-experimental-clock-index must appear once';
      $flagSeen = true;
      if (!array_key_exists('value', $option) || !baikalExperimentalFlagValue($option['value'], $flag))
        return 'baikal-experimental-clock-index must be a boolean or the string true or false';
    } elseif ($option['key'] === 'baikal-board-clock-index') {
      if ($clock_indexSeen) return 'baikal-board-clock-index must appear once';
      $clock_indexSeen = true;
      $clock_index = array_key_exists('value', $option) ? baikalCanonicalBoardPll($option['value']) : null;
      if ($clock_index === null)
        return 'baikal-board-clock-index must be three integers 8..32 such as 29:30:30';
    } elseif ($option['key'] === 'blakestream-clock-scope') {
      if ($scopeSeen) return 'blakestream-clock-scope must appear once';
      $scopeSeen = true;
      $scope = array_key_exists('value', $option) ? baikalClockScopeValue($option['value']) : null;
      if ($scope === null)
        return 'blakestream-clock-scope must be global or individual';
    }
  }
  if (!$flagSeen && !$clock_indexSeen) return null;
  if ($flagSeen && $flag === false && !$clock_indexSeen) return null;
  if ($flagSeen && $flag === true && $clock_indexSeen && baikalExplicitIndividualMode($options)) {
    /* Global UI still saves individual mode. Equal indices are the batch. */
    if ($scope === 'global' && !baikalBoardPllIndicesEqual($clock_index))
      return 'blakestream-clock-scope global requires three equal clock indices';
    return null;
  }
  if ($clock_indexSeen && (!$flagSeen || $flag === false))
    return 'baikal-board-clock-index requires baikal-experimental-clock-index';
  if ($flagSeen && $flag === true && !$clock_indexSeen)
    return 'baikal-experimental-clock-index requires baikal-board-clock-index';
  return 'baikal-experimental-clock-index requires explicit baikal-clock-mode individual';
}

function user_adjustable_clocks_enabled() {
  global $configScripta;
  $path = isset($GLOBALS['BKB_SCRIPTA_CONF_PATH']) ? $GLOBALS['BKB_SCRIPTA_CONF_PATH'] : $configScripta;
  if (!$path) return false;
  $settings = json_decode(@file_get_contents($path), true);
  return is_array($settings) && isset($settings['userAdjustableClocks']) &&
    $settings['userAdjustableClocks'] === true;
}

function baikalExperimentalLaunchAllowed() {
  return firmware_custom_active() && user_adjustable_clocks_enabled();
}

function baikalExperimentalPolicyError($options) {
  if (baikalExperimentalClockError($options) !== null || !baikalExperimentalFlagOn($options))
    return null;
  if (!firmware_custom_active())
    return 'Experimental Index requires verified custom bridge firmware';
  if (!user_adjustable_clocks_enabled())
    return 'Experimental Index requires Adjustable Clocks';
  return null;
}

function baikalStripExperimentalOptions($options) {
  return bkb_strip_experimental_options($options);
}

function baikalNormalizeExperimentalOptions($options) {
  $valid = baikalExperimentalClockError($options) === null;
  $enabled = $valid && baikalExperimentalFlagOn($options);
  $scope = $valid ? baikalSavedClockScope($options) : null;
  $clock_index = null;
  if ($enabled) {
    foreach ($options as $option) {
      if (is_array($option) && isset($option['key']) && $option['key'] === 'baikal-board-clock-index')
        $clock_index = baikalCanonicalBoardPll(isset($option['value']) ? $option['value'] : null);
    }
  }
  $out = baikalStripExperimentalOptions($options);
  if ($enabled && $clock_index !== null) {
    $out[] = array(
      'key' => 'baikal-experimental-clock-index',
      'value' => true,
      'description' => 'Experimental per-board clock indices (not MHz)'
    );
    $out[] = array(
      'key' => 'baikal-board-clock-index',
      'value' => $clock_index,
      'description' => 'Experimental clock indices board0:board1:board2 (8-32, not MHz)'
    );
  }
  if ($scope !== null) {
    $out[] = array(
      'key' => 'blakestream-clock-scope',
      'value' => $scope,
      'description' => 'Clock control scope (presentation only)'
    );
  }
  return $out;
}

function baikalAppendExperimentalMinerKeys(&$miner, $rawOptions) {
  if (baikalExperimentalClockError($rawOptions) !== null) return false;
  if (!baikalExperimentalFlagOn($rawOptions)) return true;
  if (!baikalExperimentalLaunchAllowed()) return true;
  $clock_index = null;
  foreach ($rawOptions as $option) {
    if (is_array($option) && isset($option['key']) && $option['key'] === 'baikal-board-clock-index')
      $clock_index = baikalCanonicalBoardPll(isset($option['value']) ? $option['value'] : null);
  }
  if ($clock_index === null) return false;
  $miner['baikal-experimental-clock-index'] = true;
  $miner['baikal-board-clock-index'] = $clock_index;
  return true;
}

function baikalBuildMinerConfig($rawOptions) {
  if (bs_miner_option_security_error($rawOptions) !== null) return false;
  if (baikalExperimentalClockError($rawOptions) !== null) return false;
  $options = ensureBaikalOptions(is_array($rawOptions) ? $rawOptions : array());
  $miner = array();
  $clockMode = 'global';

  foreach ($options as $o) {
    if (is_array($o) && isset($o['key']) && $o['key'] === 'baikal-clock-mode') {
      $clockMode = normalizeBaikalClockMode(isset($o['value']) ? $o['value'] : '', false);
      break;
    }
  }

  if (!is_array($options)) $options = array();
  foreach ($options as $o) {
    if (!is_array($o) || !isset($o['key'])) continue;
    if ($o['key'] === 'baikal-experimental-clock-index' || $o['key'] === 'baikal-board-clock-index' ||
        $o['key'] === 'blakestream-clock-scope') continue;
    if ($o['key'] === 'baikal-board-clocks' && $clockMode !== 'individual') continue;
    if (!array_key_exists('value', $o)) $o['value'] = '';
    if ($o['value'] == "true") {
      $o['value'] = true;
    }
    $miner[$o['key']] = $o['value'];
  }
  if (!baikalAppendExperimentalMinerKeys($miner, is_array($rawOptions) ? $rawOptions : array()))
    return false;
  $miner['api-port'] = '4028';
  $miner['api-groups'] = 'B:*:restart:ascidentify:ascenable:ascdisable:bkbtrace:bkbfan:ascpool:ascfailover';
  $miner['api-allow'] = 'B:127.0.0.1';
  return $miner;
}

function minerConfigGenerate(){
  global $configOptns,$configPools,$configMiner;
  $raw = json_decode(@file_get_contents($configOptns), true);
  $miner = baikalBuildMinerConfig(is_array($raw) ? $raw : array());
  if ($miner === false) return false;
  $miner['pools'] = json_decode(@file_get_contents($configPools), true);
  if (!is_array($miner['pools'])) $miner['pools'] = array();
  return write_config_file($configMiner, json_encode($miner, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
}

function pool_priority($p) {
    if (is_array($p)) {
      if (isset($p['priority'])) return (int)$p['priority'];
      if (isset($p['prio'])) return (int)$p['prio'];
    }
    return 0;
}

function cmp($a, $b) {
    return pool_priority($a) - pool_priority($b);
}

function baikalIntOrDefault($value, $default) {
    return is_numeric($value) ? intval($value) : $default;
}

function normalizeBaikalOptionsValue($value) {
    $clock = 400;
    $recover = 40;
    $cutoff = 55;
    $text = trim((string)$value);

    if ($text !== '') {
        $parts = explode(':', $text);
        if (count($parts) >= 3) {
            $clock = baikalIntOrDefault($parts[0], $clock);
            $recover = baikalIntOrDefault($parts[1], $recover);
            $cutoff = baikalIntOrDefault($parts[2], $cutoff);
        } elseif (count($parts) >= 2) {
            // Backward-compatible old format: recover:cutoff.
            $recover = baikalIntOrDefault($parts[0], $recover);
            $cutoff = baikalIntOrDefault($parts[1], $cutoff);
        } elseif (count($parts) == 1) {
            $clock = baikalIntOrDefault($parts[0], $clock);
        }
    }

    if ($clock !== 400) $clock = 400;
    if ($recover < 1) $recover = 1;
    if ($recover > 99) $recover = 99;
    if ($cutoff < 1) $cutoff = 1;
    if ($cutoff > 99) $cutoff = 99;
    if ($recover >= $cutoff) {
        $recover = 40;
        $cutoff = 55;
    }

    return $clock . ':' . $recover . ':' . $cutoff;
}

function normalizeBaikalClockMode($value, $hasLegacyBoardClocks) {
    if ($value === 'global' || $value === 'individual') return $value;
    return $hasLegacyBoardClocks ? 'individual' : 'global';
}

function ensureBaikalOptions($options) {
    if (!is_array($options)) $options = array();
    $foundBaikal = false;
    $foundMode = false;
    $foundBoardClocks = false;

    foreach ($options as $option) {
        if (!is_array($option) || !isset($option['key'])) continue;
        if ($option['key'] === 'baikal-clock-mode') $foundMode = true;
        if ($option['key'] === 'baikal-board-clocks') $foundBoardClocks = true;
    }
    $migratedMode = $foundMode ? 'global' : normalizeBaikalClockMode('', $foundBoardClocks);

    foreach ($options as $idx => $option) {
        if (!is_array($option) || !isset($option['key'])) continue;
        if ($option['key'] === 'baikal-options') {
            $options[$idx]['value'] = normalizeBaikalOptionsValue(isset($option['value']) ? $option['value'] : '');
            if (empty($options[$idx]['description'])) {
                $options[$idx]['description'] = 'BK-B ASIC clock MHz:recover temp:cutoff temp';
            }
            $foundBaikal = true;
        } elseif ($option['key'] === 'baikal-fan') {
            $options[$idx]['value'] = (string)max(40, min(100,
                baikalIntOrDefault(isset($option['value']) ? $option['value'] : null, 70)));
        } elseif ($option['key'] === 'baikal-clock-mode') {
            $options[$idx]['value'] = normalizeBaikalClockMode(
                isset($option['value']) ? $option['value'] : '',
                false
            );
            if (empty($options[$idx]['description'])) {
                $options[$idx]['description'] = 'BK-B clock application mode (global or individual)';
            }
            $migratedMode = $options[$idx]['value'];
        } elseif ($option['key'] === 'baikal-board-clocks' && empty($options[$idx]['description'])) {
            $options[$idx]['description'] = 'Advanced per-board applied clocks MHz (board0:board1:board2)';
        }
    }

    if (!$foundBaikal) {
        $options[] = array(
            'key' => 'baikal-options',
            'value' => normalizeBaikalOptionsValue(''),
            'description' => 'BK-B ASIC clock MHz:recover temp:cutoff temp'
        );
    }
    if (!$foundMode) {
        $options[] = array(
            'key' => 'baikal-clock-mode',
            'value' => $migratedMode,
            'description' => 'BK-B clock application mode (global or individual)'
        );
    }
    if (!$foundBoardClocks) {
        $options[] = array(
            'key' => 'baikal-board-clocks',
            'value' => '400:400:400',
            'description' => 'Advanced per-board applied clocks MHz (board0:board1:board2)'
        );
    }

    return $options;
}

function validateBaikalBoardClocks($options) {
    if (!is_array($options)) return null;
    foreach ($options as $option) {
        if (!is_array($option) || !isset($option['key'])) continue;
        if ($option['key'] === 'baikal-clock-mode') {
            $mode = isset($option['value']) ? (string)$option['value'] : '';
            if ($mode !== 'global' && $mode !== 'individual') {
                return 'baikal-clock-mode must be global or individual';
            }
            continue;
        }
        if ($option['key'] !== 'baikal-board-clocks') continue;
        $value = isset($option['value']) ? trim((string)$option['value']) : '';
        if (!preg_match('/^(\d+):(\d+):(\d+)$/', $value, $matches)) {
            return 'baikal-board-clocks must be board0:board1:board2';
        }
        for ($i = 1; $i <= 3; $i++) {
            $clock = (int)$matches[$i];
            if ($clock !== 400) {
                return 'baikal-board-clocks values must use a validated clock (currently 400 MHz)';
            }
        }
    }
    return null;
}

function firmware_identity_paths() {
    if (isset($GLOBALS['BKB_FIRMWARE_IDENTITY_PATHS']) && is_array($GLOBALS['BKB_FIRMWARE_IDENTITY_PATHS']))
        return $GLOBALS['BKB_FIRMWARE_IDENTITY_PATHS'];
    return array('/opt/scripta/etc/bkb-firmware-identity.json');
}

function firmware_custom_active() {
    foreach (firmware_identity_paths() as $path) {
        $d = @json_decode(@file_get_contents($path), true);
        if (is_array($d)) return bkb_firmware_supports_clocks($d);
    }
    return false;
}

function clampBaikalClockToStock() {
    global $configOptns;
    $rawOptions = json_decode(@file_get_contents($configOptns), true);
    $options = ensureBaikalOptions($rawOptions);
    $changed = false;
    $optionsNormalized = (json_encode($options) !== json_encode(is_array($rawOptions) ? $rawOptions : array()));

    foreach ($options as $idx => $option) {
        if (!is_array($option) || !isset($option['key']) || $option['key'] !== 'baikal-options') continue;
        $normalized = normalizeBaikalOptionsValue(isset($option['value']) ? $option['value'] : '');
        $parts = explode(':', $normalized);
        $clock = isset($parts[0]) ? baikalIntOrDefault($parts[0], 400) : 400;
        if ($clock !== 400) {
            $parts[0] = '400';
            $options[$idx]['value'] = implode(':', $parts);
            $changed = true;
        } else {
            $options[$idx]['value'] = $normalized;
        }
        break;
    }

    foreach ($options as $idx => $option) {
        if (!is_array($option) || !isset($option['key']) || $option['key'] !== 'baikal-clock-mode') continue;
        if (!isset($option['value']) || $option['value'] !== 'global') {
            $options[$idx]['value'] = 'global';
            $changed = true;
        }
        break;
    }

    $stripped = baikalStripExperimentalOptions($options);
    if (count($stripped) !== count($options)) {
        $options = $stripped;
        $changed = true;
    }

    if (!$changed && !$optionsNormalized) return false;
    if (!write_config_file($configOptns, json_encode($options, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES))) {
        return false;
    }
    return true;
}

?>
