<?php
// f_blakestream.php — Blakestream-GaintB backend AJAX endpoint
//
// Handles the new saved-pools / categories / board-assignments data model.
// Lives alongside the existing f_settings.php / f_status.php / f_miner.php
// (we don't modify those — additive only, lower risk).
//
// Read actions use GET ?action=<verb>. Mutations use POST ?action=<verb> with
// bounded JSON in the request body and the shared per-session request token.
//
// Verbs:
//   load_pools                 - returns saved-pools.json contents
//   save_pools                 - writes saved-pools.json only
//   load_assignments           - returns board-assignments.json
//   save_assignments           - writes board-assignments.json then triggers restart
//   load_clock_profiles        - returns saved adjustable clock profiles
//   save_clock_profiles        - writes saved adjustable clock profiles
//   load_runtime               - returns the active runtime config (debug)
//   restart                    - re-runs miner-restart.sh
//   status                     - returns a brief device summary

require_once __DIR__ . '/inc/web-security.php';
bs_secure_session_start();
if (!isset($_SESSION['_logged_']) || $_SESSION['_logged_'] !== true) {
    http_response_code(401);
    echo json_encode(array('error' => 'not logged in'));
    exit;
}

header('Content-Type: application/json');
include_once('inc/accepted-work.php');
require_once __DIR__ . '/inc/experimental-clock.php';
require_once __DIR__ . '/inc/connection-status.php';

$ETC = '/opt/scripta/etc';
$VAR = '/opt/scripta/var';
$RUNTIME_DIR = '/run/blakestream';
$SAVED_POOLS = $ETC . '/saved-pools.json';
$ASSIGNMENTS = $ETC . '/board-assignments.json';
$TEMP_STATE = '/run/blakestream-control/temp-state.json';
$TEMP_CONFIG = $ETC . '/temp-config.json';
$FAILOVER_CONFIG = $ETC . '/failover-config.json';
$CLOCK_PROFILES = $ETC . '/clock-profiles.json';
$MINER_OPTIONS = $ETC . '/miner.options.json';
// userChartsSaveTo controls hashrate-history.json storage. The Charts toggle
// is pure show/hide and doesn't affect recording.
// pool-history.json, failover-events.json, and clock-history.json have their
// own Persist History sub-toggles in Settings: ON → SD, OFF → RAM tmpfs.
// Clock History uses a RAM buffer while SD is selected, then flushes that
// buffer to SD periodically to reduce flash writes without losing live detail.
$SCRIPTA_CONF = $ETC . '/scripta.conf';
$_persist_cfg = @json_decode(@file_get_contents($SCRIPTA_CONF), true);
function _bs_runtime_dir($cfg, $key, $default = false) {
    $on = $default;
    if (is_array($cfg) && array_key_exists($key, $cfg)) {
        $on = !empty($cfg[$key]);
    }
    return $on ? '/opt/scripta/var' : '/run/blakestream';
}
function _bs_newest_existing_path($paths) {
    $best_path = '';
    $best_mtime = -1;
    foreach ($paths as $path) {
        if (!file_exists($path)) continue;
        $mtime = @filemtime($path);
        if ($mtime !== false && $mtime > $best_mtime) {
            $best_mtime = $mtime;
            $best_path = $path;
        }
    }
    return $best_path;
}
$POOL_HISTORY = _bs_runtime_dir($_persist_cfg, 'userPersistPoolHistory') . '/pool-history.json';
$_charts_saveto = (is_array($_persist_cfg) && isset($_persist_cfg['userChartsSaveTo']))
    ? $_persist_cfg['userChartsSaveTo']
    : 'RAM';
$HASHRATE_HISTORY = (($_charts_saveto === 'RAM') ? '/run/blakestream' : '/opt/scripta/var') . '/hashrate-history.json';
$CLOCK_HISTORY_RAM = $RUNTIME_DIR . '/clock-history.json';
$CLOCK_HISTORY_SD = $VAR . '/clock-history.json';
$CLOCK_HISTORY_SD_BUFFER = $RUNTIME_DIR . '/clock-history-sd-buffer.json';
$CLOCK_HISTORY_PERSIST_SD = (is_array($_persist_cfg) && !empty($_persist_cfg['userPersistClockHistory']));
$CLOCK_HISTORY = $CLOCK_HISTORY_PERSIST_SD ? $CLOCK_HISTORY_SD : $CLOCK_HISTORY_RAM;
$CLOCK_HISTORY_READ = $CLOCK_HISTORY;
if ($CLOCK_HISTORY_PERSIST_SD) {
    $newest_clock_history = _bs_newest_existing_path(array($CLOCK_HISTORY_SD_BUFFER, $CLOCK_HISTORY_SD));
    if ($newest_clock_history) $CLOCK_HISTORY_READ = $newest_clock_history;
}
$FAILOVER_EVENTS = _bs_runtime_dir($_persist_cfg, 'userPersistFailoverHistory') . '/failover-events.json';
$RUNTIME = '/run/blakestream-miner/scripta-runtime-miner.conf';
// Use the explicit-restart helper, NOT miner-start.sh which is the
// cron-driven idempotent watchdog (it does nothing if sgminer is running).
$LAUNCHER = '/opt/scripta/startup/miner-restart.sh';

function read_json_file($path, $default) {
    if (!file_exists($path)) return $default;
    $raw = @file_get_contents($path);
    if ($raw === false) return $default;
    $d = @json_decode($raw, true);
    return ($d === null) ? $default : $d;
}

function write_json_file($path, $data, $force_object = false) {
    $dir = dirname($path);
    if ($dir && !is_dir($dir)) {
        @mkdir($dir, 0775, true);
    }
    $tmp = $path . '.tmp';
    $flags = 0;
    if (defined('JSON_PRETTY_PRINT')) $flags |= JSON_PRETTY_PRINT;
    if ($force_object && defined('JSON_FORCE_OBJECT')) $flags |= JSON_FORCE_OBJECT;
    $json = json_encode($data, $flags);
    if (@file_put_contents($tmp, $json) === false) return false;
    @chmod($tmp, 0664);
    return @rename($tmp, $path);
}

function write_json_files($paths, $data, $force_object = false) {
    $ok = true;
    $seen = array();
    foreach ($paths as $path) {
        if (!$path || isset($seen[$path])) continue;
        $seen[$path] = true;
        if (!write_json_file($path, $data, $force_object)) {
            $ok = false;
        }
    }
    return $ok;
}

function trigger_restart($launcher) {
    if (!is_file($launcher) || !is_executable($launcher)) return false;

    $preflight = 'sudo -n ' . escapeshellarg($launcher) . ' --preflight 2>&1';
    @exec($preflight, $preflightOutput, $preflightRc);
    if ($preflightRc !== 0) return false;

    // Run miner-restart.sh in the background, detached, so we don't block the
    // HTTP response while sgminer stops, rebuilds runtime config, and starts.
    $cmd = 'sudo -n ' . escapeshellarg($launcher) . ' > /tmp/blakestream-launcher.log 2>&1 &';
    @exec($cmd, $output, $rc);
    return ($rc === 0);
}

function restart_payload($launcher, $extra = array()) {
    $started = trigger_restart($launcher);
    $payload = array_merge(array('ok' => true, 'restarted' => $started), $extra);
    if (!$started) {
        $payload['restart_pending'] = true;
        $payload['message'] = 'Miner restart pending; automatic helper repair will retry shortly';
    }
    return $payload;
}

// Pool catalog edits do not restart sgminer. Runtime config is rebuilt when
// board assignments are saved or the user explicitly restarts/reloads.

function summary_assignment($a) {
    if (!$a) return array();
    $out = array();
    foreach ($a as $bid => $pid) {
        $out[(string)$bid] = $pid;
    }
    return $out;
}

function assignment_primary_pool($v) {
    if (is_array($v)) {
        return (isset($v['primary']) && is_string($v['primary']) && $v['primary'] !== '') ? $v['primary'] : null;
    }
    if (is_object($v)) {
        return (isset($v->primary) && is_string($v->primary) && $v->primary !== '') ? $v->primary : null;
    }
    return (is_string($v) && $v !== '') ? $v : null;
}

function clean_clock_pool_url($url) {
    if ($url === null || $url === '') return '';
    $s = (string)$url;
    $s = preg_replace('|^https?://|i', '', $s);
    $s = preg_replace('|^quota:\d+;|i', '', $s);
    $s = preg_replace('~^stratum\+(tcp|ssl|tls)://~i', '', $s);
    return $s;
}

function pool_endpoint_key($url) {
    $s = trim(clean_clock_pool_url($url));
    if ($s === '') return '';
    $parts = explode('/', $s, 2);
    $s = $parts[0];
    if (strpos($s, '@') !== false) {
        $user_split = explode('@', $s);
        $s = end($user_split);
    }
    return strtolower(rtrim($s, "/ \t\r\n"));
}

function saved_pool_meta_by_id($saved_pools) {
    $out = array();
    if (!is_array($saved_pools) || !isset($saved_pools['categories']) || !is_array($saved_pools['categories'])) {
        return $out;
    }
    foreach ($saved_pools['categories'] as $cat) {
        $pools = (isset($cat['pools']) && is_array($cat['pools'])) ? $cat['pools'] : array();
        foreach ($pools as $pool) {
            if (!is_array($pool) || empty($pool['id'])) continue;
            $out[(string)$pool['id']] = array(
                'algo' => isset($pool['algo']) ? (string)$pool['algo'] : '',
                'endpoint' => pool_endpoint_key(isset($pool['url']) ? $pool['url'] : ''),
            );
        }
    }
    return $out;
}

function sanitize_same_endpoint_failovers(&$assignments, $saved_pools) {
    $pool_meta = saved_pool_meta_by_id($saved_pools);
    $cleared = 0;
    foreach ($assignments as $bid => $slot) {
        if (!is_array($slot)) continue;
        $primary = isset($slot['primary']) ? $slot['primary'] : null;
        $failover = isset($slot['failover']) ? $slot['failover'] : null;
        if (!$primary || !$failover) continue;
        $same_endpoint = false;
        if ($primary === $failover) {
            $same_endpoint = true;
        } elseif (isset($pool_meta[$primary]) && isset($pool_meta[$failover])) {
            $pe = $pool_meta[$primary]['endpoint'];
            $fe = $pool_meta[$failover]['endpoint'];
            $same_endpoint = ($pe !== '' && $fe !== '' && $pe === $fe);
        }
        if ($same_endpoint) {
            $assignments[$bid]['failover'] = null;
            $cleared++;
        }
    }
    return $cleared;
}

function clamp_int($value, $default, $min, $max) {
    if (!is_numeric($value)) $value = $default;
    $value = (int)$value;
    if ($value < $min) $value = $min;
    if ($value > $max) $value = $max;
    return $value;
}

function normalize_board_clock($value, $default) {
    return 400;
}

function clean_profile_name($name, $fallback) {
    $name = trim((string)$name);
    $name = preg_replace('/[[:cntrl:]]+/', '', $name);
    $name = preg_replace('/\s+/', ' ', $name);
    if ($name === '') $name = $fallback;
    if (strlen($name) > 48) $name = substr($name, 0, 48);
    return $name;
}

function normalize_clock_profiles($payload) {
    $raw = array();
    if (is_array($payload) && isset($payload['profiles']) && is_array($payload['profiles'])) {
        $raw = $payload['profiles'];
    } elseif (is_array($payload)) {
        $raw = $payload;
    }

    $profiles = array();
    $seen = array();
    $i = 0;
    foreach ($raw as $profile) {
        $error = bkb_clock_profile_error($profile);
        if ($error !== null) return array('error' => $error, 'profiles' => array());
        $i++;
        if ($i > 24) break;

        $clock = normalize_board_clock(isset($profile['clock']) ? $profile['clock'] : 400, 400);
        $has_board_clocks = isset($profile['board_clocks']) && is_array($profile['board_clocks']);
        $mode = isset($profile['mode']) ? (string)$profile['mode'] : '';
        if ($mode !== 'global' && $mode !== 'individual') {
            $mode = $has_board_clocks ? 'individual' : 'global';
        }
        /* Legacy profiles have no scope. Derive it from the actual mode.
           An explicit scope is presentation and is not rewritten from mode. */
        if (array_key_exists('scope', $profile)) {
            $scope = $profile['scope'];
        } else {
            $scope = $mode;
        }
        $board_clocks = array($clock, $clock, $clock);
        if ($has_board_clocks) {
            for ($board = 0; $board < 3; $board++) {
                $key = 'board' . $board;
                if (array_key_exists($board, $profile['board_clocks'])) {
                    $board_clocks[$board] = normalize_board_clock($profile['board_clocks'][$board], $clock);
                } elseif (array_key_exists($key, $profile['board_clocks'])) {
                    $board_clocks[$board] = normalize_board_clock($profile['board_clocks'][$key], $clock);
                }
            }
        }
        $recover = clamp_int(isset($profile['recover']) ? $profile['recover'] : 40, 40, 1, 99);
        $cutoff = clamp_int(isset($profile['cutoff']) ? $profile['cutoff'] : 55, 55, 1, 99);
        if ($recover >= $cutoff) {
            $recover = 40;
            $cutoff = 55;
        }

        $fan_min = clamp_int(isset($profile['fan_min']) ? $profile['fan_min'] : 40, 40, 20, 100);
        $fan_target = clamp_int(isset($profile['fan_target']) ? $profile['fan_target'] : 36, 36, 20, 120);
        $fan_full = clamp_int(isset($profile['fan_full']) ? $profile['fan_full'] : 50, 50, 20, 120);
        if ($fan_full <= $fan_target + 1) {
            $fan_full = min(120, $fan_target + 2);
            if ($fan_full <= $fan_target + 1) $fan_target = max(20, $fan_full - 2);
        }
        $fan_mid_temp_default = (int)round($fan_target + (($fan_full - $fan_target) / 2));
        $fan_mid_fan_default = max($fan_min, 75);
        $fan_mid_temp = clamp_int(isset($profile['fan_mid_temp']) ? $profile['fan_mid_temp'] : $fan_mid_temp_default,
                                  $fan_mid_temp_default, 20, 120);
        $fan_mid_fan = clamp_int(isset($profile['fan_mid_fan']) ? $profile['fan_mid_fan'] : $fan_mid_fan_default,
                                 $fan_mid_fan_default, $fan_min, 100);
        if ($fan_mid_temp <= $fan_target) $fan_mid_temp = min(120, $fan_target + 1);
        if ($fan_mid_temp >= $fan_full) $fan_mid_temp = max(20, $fan_full - 1);
        if ($fan_mid_temp <= $fan_target || $fan_mid_temp >= $fan_full) {
            $fan_mid_temp = $fan_mid_temp_default;
        }

        $id = isset($profile['id']) ? (string)$profile['id'] : '';
        if (!preg_match('/^[A-Za-z0-9._-]{1,64}$/', $id) || isset($seen[$id])) {
            $id = 'cp-' . time() . '-' . $i;
        }
        $seen[$id] = true;

        $profiles[] = array(
            'version' => 2,
            'mapping' => 'bkb-v1-clock-index-1',
            'experimental' => !empty($profile['experimental']),
            'clock_indices' => !empty($profile['experimental'])
                ? array_map('intval', $profile['clock_indices']) : array(30, 30, 30),
            'id' => $id,
            'name' => clean_profile_name(isset($profile['name']) ? $profile['name'] : '',
                !empty($profile['experimental']) ? 'Experimental Index' : 'Normal 400'),
            'mode' => $mode,
            'scope' => $scope,
            'clock' => $clock,
            'board_clocks' => $board_clocks,
            'recover' => $recover,
            'cutoff' => $cutoff,
            'fan_min' => $fan_min,
            'fan_target' => $fan_target,
            'fan_mid_temp' => $fan_mid_temp,
            'fan_mid_fan' => $fan_mid_fan,
            'fan_full' => $fan_full,
            'updated' => isset($profile['updated']) ? max(0, (int)$profile['updated']) : time(),
        );
    }

    return array('profiles' => $profiles);
}

// Actions are selected only by the query key; cookies and request bodies may
// never override routing. Every action is explicitly classified below.
$action = isset($_GET['action']) && is_string($_GET['action']) ? $_GET['action'] : '';
$readActions = array(
    'load_clock_profiles', 'load_pools', 'load_assignments', 'load_runtime', 'status',
    'load_temp_state', 'load_failover_config', 'load_pool_history', 'load_hashrate_history',
    'load_failover_history', 'load_clock_history', 'sysstats'
);
$mutationActions = array(
    'save_clock_profiles', 'set_manual_fan', 'save_pools', 'save_assignments', 'restart',
    'save_failover_config', 'clear_pool_history', 'clear_failover_history',
    'clear_clock_history', 'clear_clock_run'
);
bs_enforce_action($action, $readActions, $mutationActions,
    isset($_SESSION['web_csrf']) ? $_SESSION['web_csrf'] : '');

// Mutation payloads are accepted only as bounded raw POST JSON.
function get_payload() {
    if (isset($_SERVER['CONTENT_LENGTH']) && (int)$_SERVER['CONTENT_LENGTH'] > 1048576) return null;
    $raw = @file_get_contents('php://input', false, null, 0, 1048577);
    if (is_string($raw) && $raw !== '' && strlen($raw) <= 1048576) return json_decode($raw, true);
    return null;
}

function saved_label_valid($value, $max_bytes) {
    return is_string($value) && trim($value) !== '' && strlen($value) <= $max_bytes &&
        preg_match('/[<>{}\x00-\x1f\x7f]/', $value) !== 1;
}

function saved_id_valid($value) {
    return is_string($value) && preg_match('/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/D', $value) === 1;
}

function saved_pools_error($payload) {
    if (!is_array($payload) || !isset($payload['categories']) || !is_array($payload['categories']))
        return 'invalid payload: expected categories';
    if (count($payload['categories']) > 64) return 'too many categories';
    $pool_count = 0;
    foreach ($payload['categories'] as $category) {
        if (!is_array($category) || !isset($category['id'], $category['name']) ||
            !saved_id_valid($category['id']) || !saved_label_valid($category['name'], 64))
            return 'category has an invalid id or name';
        $pools = isset($category['pools']) ? $category['pools'] : array();
        if (!is_array($pools)) return 'category pools must be a list';
        foreach ($pools as $pool) {
            $pool_count++;
            if ($pool_count > 512) return 'too many saved pools';
            if (!is_array($pool) || !isset($pool['id'], $pool['url']) ||
                !saved_id_valid($pool['id']) || !is_string($pool['url']) || trim($pool['url']) === '')
                return 'pool has an invalid id or URL';
            if (isset($pool['name']) && !saved_label_valid($pool['name'], 96))
                return 'pool has an invalid name';
        }
    }
    return null;
}

// Talk to the local sgminer API (same channel the fan-controller uses).
function bk_sgminer_api($command, $parameter = null, $timeout = 4) {
    $payload = array('command' => $command);
    if ($parameter !== null) $payload['parameter'] = $parameter;
    $errno = 0; $errstr = '';
    $sock = @fsockopen('127.0.0.1', 4028, $errno, $errstr, $timeout);
    if (!$sock) return array(null, 'api connect failed: ' . $errstr);
    stream_set_timeout($sock, $timeout);
    @fwrite($sock, json_encode($payload));
    $data = '';
    while (!feof($sock)) {
        $chunk = @fread($sock, 8192);
        if ($chunk === false || $chunk === '') break;
        $data .= $chunk;
    }
    @fclose($sock);
    $text = trim(str_replace("\x00", '', $data));
    $json = json_decode($text, true);
    return array($json !== null ? $json : $text, null);
}

// Persist the manual fan percent into miner.options.json (baikal-fan).
// Live manual fan control is intentionally disabled here. Auto Fan uses the
// narrowly constrained bkbfan API verb from the root-side controller.
function bk_persist_manual_fan($path, $fan) {
    $fan = max(40, min(100, (int)$fan));
    $opts = @json_decode(@file_get_contents($path), true);
    if (!is_array($opts)) return false;
    $found = false;
    foreach ($opts as &$o) {
        if (isset($o['key']) && $o['key'] === 'baikal-fan') { $o['value'] = (string)$fan; $found = true; break; }
    }
    unset($o);
    if (!$found) {
        $opts[] = array('key' => 'baikal-fan', 'value' => (string)$fan,
                        'description' => 'BK-B manual fan percent');
    }
    $tmp = $path . '.tmp';
    if (@file_put_contents($tmp, json_encode($opts, JSON_PRETTY_PRINT)) === false) return false;
    return @rename($tmp, $path);
}

function clock_sample_profile($sample) {
    $clock = isset($sample['rc']) ? (int)$sample['rc'] : (isset($sample['cc']) ? (int)$sample['cc'] : (isset($sample['c']) ? (int)$sample['c'] : 400));
    $recover = isset($sample['rec']) ? (int)$sample['rec'] : 40;
    $cutoff = isset($sample['cut']) ? (int)$sample['cut'] : 55;
    $fan = isset($sample['fan']) ? (int)$sample['fan'] : 0;
    $fan_mode = isset($sample['fm']) ? (string)$sample['fm'] : 'manual';
    $clock_mode = isset($sample['cm']) ? (string)$sample['cm'] : 'incompatible';
    if ($clock_mode !== 'global' && $clock_mode !== 'individual') {
        $clock_mode = 'incompatible';
    }
    $experimental = !empty($sample['xp']);
    $clock_index = isset($sample['pi']) ? (int)$sample['pi'] : 0;
    return array(
        'key' => $clock_mode . ':' . $clock . ':' . $recover . ':' . $cutoff . ':' . $fan . ':' . $fan_mode . ($experimental ? ':clock_index:' . $clock_index : ''),
        'experimental' => $experimental,
        'clock_index' => $clock_index,
        'clock_mode' => $clock_mode,
        'clock' => $clock,
        'recover' => $recover,
        'cutoff' => $cutoff,
        'fan' => $fan,
        'fan_mode' => $fan_mode,
    );
}

function clock_time_profile_from_rows($rows) {
    if (empty($rows) || !isset($rows[0]['sample']) || !is_array($rows[0]['sample'])) {
        return null;
    }
    $profile = clock_sample_profile($rows[0]['sample']);
    $pool_parts = array();
    $clock_parts = array();
    foreach ($rows as $row) {
        $bid = isset($row['board_id']) ? (int)$row['board_id'] : 0;
        $s = isset($row['sample']) && is_array($row['sample']) ? $row['sample'] : array();
        $pool = isset($s['pool']) ? clean_clock_pool_url($s['pool']) : '';
        $pool_parts[] = $bid . '=' . $pool;
        $applied_clock = isset($s['cc']) ? (int)$s['cc'] : (isset($s['rc']) ? (int)$s['rc'] : $profile['clock']);
        $clock_parts[] = $bid . '=' . (!empty($s['xp']) ? 'clock_index:' . (int)$s['pi'] : $applied_clock);
    }
    sort($pool_parts, SORT_STRING);
    sort($clock_parts, SORT_STRING);
    $profile['pool_signature'] = implode('|', $pool_parts);
    $profile['applied_clock_signature'] = implode('|', $clock_parts);
    $profile['run_key'] = $profile['clock_mode'] . '|' . $profile['clock'] . '|' . $profile['applied_clock_signature'] . '|' . $profile['pool_signature'];
    return $profile;
}

function clock_sample_profile_matches($sample, $profile_key) {
    if ($profile_key === '') return true;
    if (is_array($sample) && isset($sample['prof']) && (string)$sample['prof'] === $profile_key) {
        return true;
    }
    $profile = clock_sample_profile($sample);
    return isset($profile['key']) && $profile['key'] === $profile_key;
}

function clock_aggregate_board($board_id, $samples) {
    if ($samples instanceof BSClockHistorySamples) {
        $first = $samples->at(0);
        $latest = $samples->at(count($samples) - 1);
    } else {
        usort($samples, function($a, $b) {
            return ((int)$a['t']) - ((int)$b['t']);
        });
        foreach ($samples as &$sample) {
            if (is_array($sample) && isset($sample['pool'])) {
                $sample['pool'] = clean_clock_pool_url($sample['pool']);
            }
        }
        unset($sample);
        $latest = end($samples);
        $first = isset($samples[0]) ? $samples[0] : array();
    }
    $sample_count = count($samples);
    $sum_mhs5s = 0.0;
    $sum_clock = 0.0;
    $sum_requested_clock = 0.0;
    $sum_temp = 0.0;
    $min_temp = null;
    $max_temp = null;
    $delta_a = 0;
    $delta_r = 0;
    $delta_hw = 0;
    $prev = null;

    foreach ($samples as $s) {
        $temp = isset($s['tmp']) ? (float)$s['tmp'] : 0.0;
        $sum_temp += $temp;
        if ($min_temp === null || $temp < $min_temp) $min_temp = $temp;
        if ($max_temp === null || $temp > $max_temp) $max_temp = $temp;
        $sum_mhs5s += isset($s['m5']) ? (float)$s['m5'] : 0.0;
        $sum_clock += isset($s['c']) ? (float)$s['c'] : 0.0;
        $sum_requested_clock += isset($s['rc']) ? (float)$s['rc'] : 0.0;

        if ($prev !== null) {
            $da = (isset($s['a']) ? (int)$s['a'] : 0) - (isset($prev['a']) ? (int)$prev['a'] : 0);
            $dr = (isset($s['r']) ? (int)$s['r'] : 0) - (isset($prev['r']) ? (int)$prev['r'] : 0);
            $dh = (isset($s['hw']) ? (int)$s['hw'] : 0) - (isset($prev['hw']) ? (int)$prev['hw'] : 0);
            if ($da > 0) $delta_a += $da;
            if ($dr > 0) $delta_r += $dr;
            if ($dh > 0) $delta_hw += $dh;
        }
        $prev = $s;
    }

    $shares = $delta_a + $delta_r;
    // Legacy raw logs are not synchronized measurement evidence. Only display
    // the current reducer windows when this history view is on that context.
    $current_rates = bs_accepted_rates_by_board();
    $current = isset($current_rates[$board_id]) ? $current_rates[$board_id] : array();
    $matching = isset($latest['t']) && abs(time() - $latest['t']) <= 120;
    foreach (array('pid', 'hs', 'ds', 'cg', 're', 'ee', 'xp', 'pi', 'scc', 'ep') as $key) {
        if (!isset($latest[$key], $current['context'][$key]) || (string)$latest[$key] !== (string)$current['context'][$key]) $matching = false;
    }
    $accepted_15m = $matching ? $current['15m'] : array('valid' => false);
    $accepted_1h = $matching ? $current['1h'] : array('valid' => false);
    return array(
        'id' => (int)$board_id,
        'status' => isset($latest['st']) ? $latest['st'] : '',
        'sample_count' => $sample_count,
        'first_seen' => isset($first['t']) ? (int)$first['t'] : 0,
        'last_seen' => isset($latest['t']) ? (int)$latest['t'] : 0,
        'clock' => isset($latest['c']) ? (int)$latest['c'] : 0,
        'reported_clock' => isset($latest['c']) ? (int)$latest['c'] : 0,
        'reported_clock_code' => isset($latest['fcc']) ? (int)$latest['fcc'] : 0,
        'sent_clock_index' => isset($latest['scc']) ? (int)$latest['scc'] : 0,
        'command_verified' => !empty($latest['cv']),
        'clock_mode' => (isset($latest['cm']) &&
            ($latest['cm'] === 'global' || $latest['cm'] === 'individual'))
            ? $latest['cm'] : 'incompatible',
        'requested_clock' => isset($latest['rc']) ? (int)$latest['rc'] : 0,
        'experimental' => !empty($latest['xp']),
        'requested_clock_index' => isset($latest['pi']) ? (int)$latest['pi'] : 0,
        'configured_clock' => isset($latest['cc']) ? (int)$latest['cc'] : (isset($latest['rc']) ? (int)$latest['rc'] : 0),
        'avg_clock' => $sample_count ? round($sum_clock / $sample_count, 1) : 0,
        'avg_requested_clock' => $sample_count ? round($sum_requested_clock / $sample_count, 1) : 0,
        'fan' => isset($latest['fan']) ? (int)$latest['fan'] : 0,
        'fan_mode' => isset($latest['fm']) ? (string)$latest['fm'] : 'manual',
        'recover' => isset($latest['rec']) ? (int)$latest['rec'] : 40,
        'cutoff' => isset($latest['cut']) ? (int)$latest['cut'] : 55,
        'temp' => isset($latest['tmp']) ? (float)$latest['tmp'] : 0,
        'min_temp' => $min_temp === null ? 0 : $min_temp,
        'avg_temp' => $sample_count ? round($sum_temp / $sample_count, 1) : 0,
        'max_temp' => $max_temp === null ? 0 : $max_temp,
        'mhs5s' => isset($latest['m5']) ? (float)$latest['m5'] : 0,
        'mhsav' => isset($latest['ma']) ? (float)$latest['ma'] : 0,
        'avg_mhs5s' => $sample_count ? round($sum_mhs5s / $sample_count, 1) : 0,
        'accepted_15m' => !empty($accepted_15m['valid']) ? $accepted_15m['ghs'] * 1000.0 : null,
        'accepted_1h' => !empty($accepted_1h['valid']) ? $accepted_1h['ghs'] * 1000.0 : null,
        'accepted_15m_measuring' => empty($accepted_15m['valid']),
        'accepted_1h_measuring' => empty($accepted_1h['valid']),
        'accepted_delta' => $delta_a,
        'rejected_delta' => $delta_r,
        'reject_pct' => $shares > 0 ? round(($delta_r / $shares) * 100, 3) : 0,
        'hw_delta' => $delta_hw,
        'utility' => isset($latest['u']) ? (float)$latest['u'] : 0,
        'primary_pool_no' => isset($latest['pp']) ? (int)$latest['pp'] : -1,
        'effective_pool_no' => isset($latest['ep']) ? (int)$latest['ep'] : -1,
        'failover_active' => !empty($latest['fo']),
        'pool_url' => isset($latest['pool']) ? clean_clock_pool_url($latest['pool']) : '',
        'last_share_time' => isset($latest['ls']) ? (int)$latest['ls'] : 0,
        'samples' => $samples,
    );
}

$response = array();

switch ($action) {
    case 'load_clock_profiles':
        $response = normalize_clock_profiles(read_json_file($CLOCK_PROFILES, array('profiles' => array())));
        break;

    case 'save_clock_profiles':
        $payload = get_payload();
        if (!is_array($payload) || !isset($payload['profiles']) || !is_array($payload['profiles'])) {
            http_response_code(400);
            $response = array('error' => 'invalid payload: expected {profiles: [...]}');
            break;
        }
        $clean = normalize_clock_profiles($payload);
        if (isset($clean['error'])) {
            http_response_code(400);
            $response = $clean;
            break;
        }
        if (!write_json_file($CLOCK_PROFILES, $clean)) {
            http_response_code(500);
            $response = array('error' => 'failed to write ' . $CLOCK_PROFILES);
            break;
        }
        $response = array('ok' => true, 'profiles' => $clean['profiles']);
        break;

    case 'set_manual_fan':
        $payload = get_payload();
        $fan = null;
        if (is_array($payload) && isset($payload['fan'])) $fan = (int)$payload['fan'];
        if ($fan === null) {
            http_response_code(400);
            $response = array('error' => 'missing fan percent');
            break;
        }
        if ($fan < 40) $fan = 40;
        if ($fan > 100) $fan = 100;
        $persisted = bk_persist_manual_fan($MINER_OPTIONS, $fan);
        $response = array(
            'ok' => $persisted,
            'fan' => $fan,
            'applied_boards' => array(),
            'persisted' => $persisted,
            'deferred' => true,
            'errors' => $persisted ? array() : array('failed to write ' . $MINER_OPTIONS),
            'info' => array(array(
                'type' => $persisted ? 'success' : 'danger',
                'text' => $persisted
                    ? ('Fan saved at ' . $fan . '%. It applies on the next miner restart.')
                    : ('Fan save failed: failed to write ' . $MINER_OPTIONS),
            )),
        );
        break;

    case 'load_pools':
        $saved_pools = read_json_file($SAVED_POOLS, array('categories' => array()));
        $saved_pools_error = saved_pools_error($saved_pools);
        if ($saved_pools_error !== null) {
            // Imported backups are checksummed but not trusted. Validate the
            // restored catalog again before exposing labels to the browser.
            http_response_code(422);
            $response = array(
                'error' => 'saved pool catalog rejected: ' . $saved_pools_error,
                'categories' => array(),
            );
            break;
        }
        $response = $saved_pools;
        break;

    case 'save_pools':
        $payload = get_payload();
        $saved_pools_error = saved_pools_error($payload);
        if ($saved_pools_error !== null) {
            http_response_code(400);
            $response = array('error' => $saved_pools_error);
            break;
        }
        // Light validation: each category needs id+name, each pool needs id+url+algo
        foreach ($payload['categories'] as $cat) {
            if (empty($cat['id']) || empty($cat['name'])) {
                http_response_code(400);
                $response = array('error' => 'category missing id or name');
                break 2;
            }
            if (isset($cat['pools'])) {
                foreach ($cat['pools'] as $p) {
                    if (empty($p['id']) || empty($p['url'])) {
                        http_response_code(400);
                        $response = array('error' => 'pool in category "' . $cat['name'] . '" missing id or url');
                        break 3;
                    }
                }
            }
        }
        if (!write_json_file($SAVED_POOLS, $payload)) {
            http_response_code(500);
            $response = array('error' => 'failed to write ' . $SAVED_POOLS);
            break;
        }
        $saved_assignments = read_json_file($ASSIGNMENTS, array());
        $assignment_clears = 0;
        if (is_array($saved_assignments)) {
            $assignment_clears = sanitize_same_endpoint_failovers($saved_assignments, $payload);
            if ($assignment_clears > 0) {
                write_json_file($ASSIGNMENTS, $saved_assignments, true);
            }
        }
        $response = array(
            'ok' => true,
            'catalog_only' => true,
            'sanitized_failovers' => $assignment_clears,
            'message' => 'Pools saved',
        );
        break;

    case 'load_assignments':
        // Per-board shape: {primary: pool-id|null, failover: pool-id|null}.
        // Coerce legacy string values to {primary, failover:null}.
        $raw = read_json_file($ASSIGNMENTS, array('0' => null, '1' => null, '2' => null));
        $a = array();
        foreach (array('0','1','2') as $bid) {
            $v = isset($raw[$bid]) ? $raw[$bid] : null;
            if (is_array($v)) {
                $a[$bid] = (object) array(
                    'primary'  => (isset($v['primary'])  && is_string($v['primary'])  && $v['primary']  !== '') ? $v['primary']  : null,
                    'failover' => (isset($v['failover']) && is_string($v['failover']) && $v['failover'] !== '') ? $v['failover'] : null,
                );
            } elseif (is_string($v) && $v !== '') {
                $a[$bid] = (object) array('primary' => $v, 'failover' => null);
            } else {
                $a[$bid] = (object) array('primary' => null, 'failover' => null);
            }
        }
        $response = (object) $a;
        break;

    case 'save_assignments':
        $payload = get_payload();
        if (!is_array($payload)) {
            http_response_code(400);
            $response = array('error' => 'invalid payload: expected {0:{primary,failover},...}');
            break;
        }
        // Normalize: each board gets a {primary, failover} object. Accept the
        // legacy string-shape too (auto-migrate on first save).
        $clean = array();
        foreach ($payload as $bid => $v) {
            $bid = (string)$bid;
            if (is_array($v)) {
                $clean[$bid] = array(
                    'primary'  => (isset($v['primary'])  && is_string($v['primary'])  && $v['primary']  !== '') ? $v['primary']  : null,
                    'failover' => (isset($v['failover']) && is_string($v['failover']) && $v['failover'] !== '') ? $v['failover'] : null,
                );
            } elseif (is_string($v) && $v !== '') {
                $clean[$bid] = array('primary' => $v, 'failover' => null);
            } else {
                $clean[$bid] = array('primary' => null, 'failover' => null);
            }
        }
        $sanitized_failovers = sanitize_same_endpoint_failovers(
            $clean,
            read_json_file($SAVED_POOLS, array('categories' => array()))
        );
        if (!write_json_file($ASSIGNMENTS, $clean, true)) {
            http_response_code(500);
            $response = array('error' => 'failed to write ' . $ASSIGNMENTS);
            break;
        }
        // ALWAYS trigger a full restart on assignment change. The runtime
        // config (which pools sgminer mines) is built from board-assignments.json,
        // so any change requires regenerating the root-owned runtime config
        // and restarting sgminer to pick it up.
        $response = restart_payload($LAUNCHER, array(
            'assignments' => (object) $clean,
            'sanitized_failovers' => $sanitized_failovers,
        ));
        break;

    case 'load_runtime':
        $response = read_json_file($RUNTIME, array('error' => 'no runtime config (idle?)'));
        break;

    case 'restart':
        $response = restart_payload($LAUNCHER);
        break;

    case 'status':
        $pools = read_json_file($SAVED_POOLS, array('categories' => array()));
        $assignments = read_json_file($ASSIGNMENTS, array());
        $runtime_exists = file_exists($RUNTIME);
        $cat_count = count($pools['categories']);
        $pool_count = 0;
        foreach ($pools['categories'] as $c) {
            if (isset($c['pools'])) $pool_count += count($c['pools']);
        }
        $assigned = 0;
        foreach ($assignments as $v) {
            if (assignment_primary_pool($v)) $assigned++;
        }
        $response = array(
            'categories' => $cat_count,
            'pools' => $pool_count,
            'boards_assigned' => $assigned,
            'boards_idle' => max(0, 3 - $assigned),
            'runtime_loaded' => $runtime_exists,
        );
        break;

    case 'load_temp_state':
        $ts = read_json_file($TEMP_STATE, array());
        $tc = read_json_file($TEMP_CONFIG, array('enabled' => true, 'disable_at' => 80, 'recover_at' => 70));
        $response = array('state' => (object) $ts, 'config' => $tc);
        break;

    case 'load_failover_config':
        $fc = read_json_file($FAILOVER_CONFIG, array('enabled' => true, 'primary_quota_multiplier' => 10));
        $response = $fc;
        break;

    case 'save_failover_config':
        $payload = get_payload();
        if (!is_array($payload)) {
            http_response_code(400);
            $response = array('error' => 'invalid payload');
            break;
        }
        $clean = array(
            'enabled' => !empty($payload['enabled']),
            'primary_quota_multiplier' => max(1, (int) (isset($payload['primary_quota_multiplier']) ? $payload['primary_quota_multiplier'] : 10)),
        );
        if (!write_json_file($FAILOVER_CONFIG, $clean)) {
            http_response_code(500);
            $response = array('error' => 'failed to write ' . $FAILOVER_CONFIG);
            break;
        }
        $response = restart_payload($LAUNCHER, array('config' => $clean));
        break;

    case 'load_pool_history':
        // Returns aggregated stats per pool over the configured window
        // (defaults: last 24 hours). Use ?hours=N to override.
        $hours = isset($_GET['hours']) ? max(1, (int)$_GET['hours']) : 24;
        $cutoff = time() - ($hours * 3600);
        $hist = read_json_file($POOL_HISTORY, array('pools' => array(), 'updated' => 0));
        $out = array();
        if (isset($hist['pools']) && is_array($hist['pools'])) {
            foreach ($hist['pools'] as $key => $pool) {
                $samples = isset($pool['samples']) ? $pool['samples'] : array();
                // Filter to the time window
                $window = array();
                foreach ($samples as $s) {
                    if (isset($s['t']) && $s['t'] >= $cutoff) {
                        $window[] = $s;
                    }
                }
                if (empty($window)) continue;
                $first = $window[0];
                $last = end($window);
                // Sum positive segment-deltas. sgminer's counters reset to 0
                // on restart, so a single (last - first) goes negative if a
                // restart happened mid-window. Walk pairwise and only count
                // forward progress; skip the negative jump at each reset.
                $delta_a = 0; $delta_r = 0; $delta_da = 0; $delta_dr = 0;
                $prev = null;
                foreach ($window as $s) {
                    if ($prev !== null) {
                        $da = $s['a']  - $prev['a'];   if ($da > 0) $delta_a  += $da;
                        $dr = $s['r']  - $prev['r'];   if ($dr > 0) $delta_r  += $dr;
                        $dda = $s['da'] - $prev['da']; if ($dda > 0) $delta_da += $dda;
                        $ddr = $s['dr'] - $prev['dr']; if ($ddr > 0) $delta_dr += $ddr;
                    }
                    $prev = $s;
                }
                $out[] = array(
                    'id' => $key,
                    'name' => isset($pool['name']) ? $pool['name'] : $key,
                    'category' => isset($pool['category']) ? $pool['category'] : '',
                    'algo' => isset($pool['algo']) ? $pool['algo'] : '',
                    'url' => isset($pool['url']) ? $pool['url'] : '',
                    'window_hours' => $hours,
                    'sample_count' => count($window),
                    'accepted' => $delta_a,
                    'rejected' => $delta_r,
                    'diff_accepted' => $delta_da,
                    'diff_rejected' => $delta_dr,
                    'last_status' => isset($last['st']) ? $last['st'] : '',
                    'last_best' => isset($last['best']) ? $last['best'] : 0,
                    'first_seen' => $first['t'],
                    'last_seen' => $last['t'],
                );
            }
        }
        // Sort by accepted shares descending
        usort($out, function($a, $b) {
            return $b['accepted'] - $a['accepted'];
        });
        $response = array('pools' => $out, 'window_hours' => $hours, 'updated' => isset($hist['updated']) ? $hist['updated'] : 0);
        break;

    case 'clear_pool_history':
        $empty = array('pools' => (object) array(), 'updated' => time());
        if (!write_json_file($POOL_HISTORY, $empty)) {
            http_response_code(500);
            $response = array('error' => 'failed to write ' . $POOL_HISTORY);
            break;
        }
        $response = array('ok' => true);
        break;

    case 'load_hashrate_history':
        session_write_close();
        // Per-board samples in the requested window. ?hours=N (default 1).
        $hours = isset($_GET['hours']) ? max(1, (int)$_GET['hours']) : 1;
        $cutoff = time() - ($hours * 3600);
        require_once __DIR__ . '/inc/hashrate-history.php';
        $saved = read_json_file($SAVED_POOLS, array('categories' => array()));
        // Map cleaned-url -> saved pool name for friendly labels.
        $pool_name_by_url = array();
        if (isset($saved['categories']) && is_array($saved['categories'])) {
            foreach ($saved['categories'] as $cat) {
                if (!isset($cat['pools']) || !is_array($cat['pools'])) continue;
                foreach ($cat['pools'] as $p) {
                    if (isset($p['url']) && isset($p['name'])) {
                        $pool_name_by_url[$p['url']] = $p['name'];
                    }
                }
            }
        }
        // php.ini output_buffering=On must not retain the complete response.
        // Leave bounded buffers and custom output handlers unchanged.
        while ($buffer = ob_get_status()) {
            if ($buffer['name'] !== 'default output handler' ||
                    ($buffer['chunk_size'] > 0 && $buffer['chunk_size'] <= 8192)) break;
            if (!@ob_end_flush()) break;
        }
        ob_start(null, 8192);
        bs_hashrate_history_emit($HASHRATE_HISTORY, $hours, $cutoff, $pool_name_by_url);
        exit;

    case 'load_failover_history':
        // Failover events filtered to ?hours=N (default 24). Returns closed
        // events sorted by start_ts desc plus the current in-progress `open`
        // map so the dashboard can show "still failing over" rows at the top.
        $hours = isset($_GET['hours']) ? max(1, (int)$_GET['hours']) : 24;
        $cutoff = time() - ($hours * 3600);
        $state = read_json_file($FAILOVER_EVENTS, array(
            'version' => 1, 'updated' => 0, 'open' => array(), 'events' => array()
        ));
        $events = isset($state['events']) && is_array($state['events']) ? $state['events'] : array();
        $window = array();
        foreach ($events as $e) {
            $ts = isset($e['end_ts']) ? (int)$e['end_ts'] : 0;
            if ($ts >= $cutoff) $window[] = $e;
        }
        usort($window, function($a, $b) {
            return ((int)$b['start_ts']) - ((int)$a['start_ts']);
        });
        $response = array(
            'events' => $window,
            'open' => isset($state['open']) && is_array($state['open']) ? array_values($state['open']) : array(),
            'window_hours' => $hours,
            'updated' => isset($state['updated']) ? (int)$state['updated'] : 0,
        );
        break;

    case 'clear_failover_history':
        // Wipes failover-events.json. Open events are cleared too — a still-
        // active failover will reappear in `open` on the next recorder tick.
        $empty = array(
            'version' => 1,
            'updated' => time(),
            'max_age_seconds' => 7 * 86400,
            'open' => (object) array(),
            'events' => array(),
        );
        if (!write_json_file($FAILOVER_EVENTS, $empty)) {
            http_response_code(500);
            $response = array('error' => 'failed to write ' . $FAILOVER_EVENTS);
            break;
        }
        $response = array('ok' => true);
        break;

    case 'load_clock_history':
        session_write_close();
        $hours = isset($_GET['hours']) ? max(1, (int)$_GET['hours']) : 24;
        $cutoff = time() - ($hours * 3600);
        require_once __DIR__ . '/inc/clock-history-stream.php';
        bs_clock_history_emit($CLOCK_HISTORY_READ, $hours, $cutoff,
            $CLOCK_HISTORY_PERSIST_SD ? 'SD' : 'RAM');
        exit;

    case 'clear_clock_history':
        $empty = array(
            'version' => 1,
            'updated' => time(),
            'step_seconds' => 60,
            'max_age_seconds' => 7 * 86400,
            'boards' => array(),
        );
        $write_paths = $CLOCK_HISTORY_PERSIST_SD
            ? array($CLOCK_HISTORY_SD_BUFFER, $CLOCK_HISTORY_SD)
            : array($CLOCK_HISTORY_RAM);
        if (!write_json_files($write_paths, $empty)) {
            http_response_code(500);
            $response = array('error' => 'failed to write clock history');
            break;
        }
        $response = array('ok' => true);
        break;

    case 'clear_clock_run':
        $payload = get_payload();
        if (!is_array($payload)) {
            http_response_code(400);
            $response = array('error' => 'invalid payload');
            break;
        }
        $start = isset($payload['start_ts']) ? (int)$payload['start_ts'] : 0;
        $end = isset($payload['end_ts']) ? (int)$payload['end_ts'] : 0;
        $profile_key = isset($payload['profile_key']) ? (string)$payload['profile_key'] : '';
        $run_key = isset($payload['run_key']) ? (string)$payload['run_key'] : '';
        if ($start <= 0 || $end < $start) {
            http_response_code(400);
            $response = array('error' => 'invalid run time range');
            break;
        }

        require_once __DIR__ . '/inc/clock-history-stream.php';
        $write_paths = $CLOCK_HISTORY_PERSIST_SD
            ? array($CLOCK_HISTORY_SD_BUFFER, $CLOCK_HISTORY_SD)
            : array($CLOCK_HISTORY_RAM);
        $response = bs_clock_history_clear($CLOCK_HISTORY_READ, $write_paths, $start, $end,
            $profile_key, $run_key, time());
        if ($response === false) {
            http_response_code(500);
            $response = array('error' => 'failed to write clock history');
        }
        break;

    case 'sysstats':
        // Read /proc/meminfo and /proc/loadavg for the dashboard footer.
        require_once __DIR__ . '/inc/system-memory.php';
        $memory = system_memory_stats(@file_get_contents('/proc/meminfo'));
        $mem_total = $memory['total_kb'];
        $mem_used = $memory['used_kb'];
        $mem_available = $mem_total - $mem_used;
        $mem_pct = $memory['used_pct'];

        $load1 = 0; $load5 = 0; $load15 = 0;
        $loadavg = @file_get_contents('/proc/loadavg');
        if ($loadavg !== false) {
            $parts = preg_split('/\s+/', trim($loadavg));
            if (count($parts) >= 3) {
                $load1 = (float)$parts[0];
                $load5 = (float)$parts[1];
                $load15 = (float)$parts[2];
            }
        }

        // CPU core count (read once and cache via static would be nice but
        // PHP request lifecycle is short so just read it)
        $cores = 0;
        $cpuinfo = @file_get_contents('/proc/cpuinfo');
        if ($cpuinfo !== false) {
            $cores = preg_match_all('/^processor\s*:/m', $cpuinfo);
        }
        if ($cores < 1) $cores = 1;

        // Convert load average to CPU%: load/cores*100, capped at 100%.
        // Load > cores means queue is building up - we still cap at 100% for
        // display, but the raw load is exposed for tooltip.
        $cpu_pct_1 = min(100, max(0, round(($load1 / $cores) * 100)));
        $cpu_pct_5 = min(100, max(0, round(($load5 / $cores) * 100)));
        $cpu_pct_15 = min(100, max(0, round(($load15 / $cores) * 100)));

        // Process count
        $proc_count = 0;
        $procs = @scandir('/proc');
        if ($procs !== false) {
            foreach ($procs as $p) {
                if (ctype_digit($p)) $proc_count++;
            }
        }

        $response = array(
            'mem_total_kb' => $mem_total,
            'mem_used_kb' => $mem_used,
            'mem_available_kb' => $mem_available,
            'mem_pct' => $mem_pct,
            'mem_total_mb' => round($mem_total / 1024),
            'mem_used_mb' => round($mem_used / 1024),
            'load_1' => $load1,
            'load_5' => $load5,
            'load_15' => $load15,
            'cores' => $cores,
            'cpu_pct' => $cpu_pct_1,
            'cpu_pct_5' => $cpu_pct_5,
            'cpu_pct_15' => $cpu_pct_15,
            'procs' => $proc_count,
            'connection' => bs_connection_status(),
        );
        break;

    default:
        http_response_code(400);
        $response = array('error' => 'unknown action: ' . $action,
                          'verbs' => array('load_pools', 'save_pools', 'load_assignments',
                                           'save_assignments', 'load_runtime', 'restart', 'status',
                                           'load_clock_profiles', 'save_clock_profiles',
                                           'set_manual_fan',
                                           'load_pool_history', 'clear_pool_history',
                                           'load_hashrate_history',
                                           'load_failover_history', 'clear_failover_history'));
}

echo json_encode($response);
