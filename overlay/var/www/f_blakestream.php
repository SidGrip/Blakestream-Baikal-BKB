<?php
// f_blakestream.php — Blakestream-GaintB backend AJAX endpoint
//
// Handles the new saved-pools / categories / board-assignments data model.
// Lives alongside the existing f_settings.php / f_status.php / f_miner.php
// (we don't modify those — additive only, lower risk).
//
// Requests are GET ?action=<verb>[&data=<json>] for parity with the rest of
// Scripta's AJAX style. POST is also accepted for save actions when the JSON
// blob is too large for a query string.
//
// Verbs:
//   load_pools                 - returns saved-pools.json contents
//   save_pools (data=<json>)   - writes saved-pools.json only
//   load_assignments           - returns board-assignments.json
//   save_assignments (data=)   - writes board-assignments.json then triggers restart
//   load_clock_profiles        - returns saved adjustable clock profiles
//   save_clock_profiles        - writes saved adjustable clock profiles
//   load_runtime               - returns the active runtime config (debug)
//   restart                    - re-runs miner-restart.sh
//   status                     - returns a brief device summary

session_start();
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
$TEMP_STATE = $RUNTIME_DIR . '/temp-state.json';
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
$RUNTIME = '/tmp/scripta-runtime-miner.conf';
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

// Determine action — query string takes precedence, then POST body
$action = isset($_REQUEST['action']) ? $_REQUEST['action'] : '';

// For save_* actions, accept the JSON payload either as ?data=... (URL-encoded)
// or as the raw POST body (preferred for large payloads).
function get_payload() {
    if (isset($_REQUEST['data'])) {
        return json_decode($_REQUEST['data'], true);
    }
    $raw = @file_get_contents('php://input');
    if ($raw) return json_decode($raw, true);
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
// Live ascset fan control is intentionally disabled for now: on this BK-B
// sgminer build, ascset fan can wedge the API thread until sgminer restarts.
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
        'first_seen' => isset($samples[0]['t']) ? (int)$samples[0]['t'] : 0,
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
        elseif (isset($_REQUEST['fan'])) $fan = (int)$_REQUEST['fan'];
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
        $response = read_json_file($SAVED_POOLS, array('categories' => array()));
        break;

    case 'save_pools':
        $payload = get_payload();
        if (!is_array($payload) || !isset($payload['categories'])) {
            http_response_code(400);
            $response = array('error' => 'invalid payload: expected {categories: [...]}');
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
        // so any change requires regenerating /tmp/scripta-runtime-miner.conf
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
        $hours = isset($_REQUEST['hours']) ? max(1, (int)$_REQUEST['hours']) : 24;
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
        // Per-board samples in the requested window. ?hours=N (default 1).
        $hours = isset($_REQUEST['hours']) ? max(1, (int)$_REQUEST['hours']) : 1;
        $cutoff = time() - ($hours * 3600);
        $hist = read_json_file($HASHRATE_HISTORY, array('boards' => array(), 'updated' => 0));
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
        $boards_out = array();
        $boards_in = isset($hist['boards']) && is_array($hist['boards']) ? $hist['boards'] : array();
        foreach ($boards_in as $b) {
            $samples = isset($b['samples']) && is_array($b['samples']) ? $b['samples'] : array();
            $window = array();
            foreach ($samples as $s) {
                if (is_array($s) && count($s) >= 2 && (int)$s[0] >= $cutoff) {
                    $window[] = array((int)$s[0], (int)$s[1]);
                }
            }
            // Resolve pool name (strip the "http://quota:N;" sgminer prefix).
            $accepted_window = array();
            foreach (isset($b['accepted_samples']) && is_array($b['accepted_samples']) ? $b['accepted_samples'] : array() as $s) {
                if (is_array($s) && count($s) >= 2 && (int)$s[0] >= $cutoff) {
                    $accepted_window[] = array((int)$s[0], $s[1] === null ? null : (float)$s[1]);
                }
            }
            $url = isset($b['pool_url']) ? $b['pool_url'] : '';
            $clean = preg_replace('|^https?://|', '', $url);
            $clean = preg_replace('|^quota:\d+;|', '', $clean);
            $name = isset($pool_name_by_url[$clean]) ? $pool_name_by_url[$clean] : null;
            $boards_out[] = array(
                'id' => isset($b['id']) ? (int)$b['id'] : 0,
                'enabled' => isset($b['enabled']) ? (bool)$b['enabled'] : true,
                'primary_pool_no' => isset($b['primary_pool_no']) ? (int)$b['primary_pool_no'] : -1,
                'effective_pool_no' => isset($b['effective_pool_no']) ? (int)$b['effective_pool_no'] : -1,
                'failover_active' => isset($b['failover_active']) ? (bool)$b['failover_active'] : false,
                'pool_url' => $clean,
                'pool_name' => $name,
                'samples' => $window,
                'accepted_samples' => $accepted_window,
            );
        }
        usort($boards_out, function($a, $b) { return $a['id'] - $b['id']; });
        $response = array(
            'boards' => $boards_out,
            'window_hours' => $hours,
            'step_seconds' => isset($hist['step_seconds']) ? (int)$hist['step_seconds'] : 60,
            'updated' => isset($hist['updated']) ? (int)$hist['updated'] : 0,
        );
        break;

    case 'load_failover_history':
        // Failover events filtered to ?hours=N (default 24). Returns closed
        // events sorted by start_ts desc plus the current in-progress `open`
        // map so the dashboard can show "still failing over" rows at the top.
        $hours = isset($_REQUEST['hours']) ? max(1, (int)$_REQUEST['hours']) : 24;
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
        // Clock diagnostics grouped into contiguous setting runs. This keeps
        // old 400 MHz data visible when the user switches to 480 MHz, while
        // still applying the selected window and the 7-day recorder cutoff.
        $hours = isset($_REQUEST['hours']) ? max(1, (int)$_REQUEST['hours']) : 24;
        $cutoff = time() - ($hours * 3600);
        $hist = read_json_file($CLOCK_HISTORY_READ, array('boards' => array(), 'updated' => 0));
        $boards_in = isset($hist['boards']) && is_array($hist['boards']) ? $hist['boards'] : array();
        $step = isset($hist['step_seconds']) ? max(1, (int)$hist['step_seconds']) : 60;
        $all_samples = array();

        foreach ($boards_in as $b) {
            $board_id = isset($b['id']) ? (int)$b['id'] : 0;
            $samples = isset($b['samples']) && is_array($b['samples']) ? $b['samples'] : array();
            foreach ($samples as $s) {
                if (is_array($s) && isset($s['t']) && (int)$s['t'] >= $cutoff) {
                    $all_samples[] = array('board_id' => $board_id, 'sample' => $s);
                }
            }
        }

        usort($all_samples, function($a, $b) {
            $ta = isset($a['sample']['t']) ? (int)$a['sample']['t'] : 0;
            $tb = isset($b['sample']['t']) ? (int)$b['sample']['t'] : 0;
            if ($ta === $tb) return ((int)$a['board_id']) - ((int)$b['board_id']);
            return $ta - $tb;
        });

        $samples_by_time = array();
        foreach ($all_samples as $row) {
            $s = $row['sample'];
            $t = isset($s['t']) ? (int)$s['t'] : 0;
            if (!$t) continue;
            if (!isset($samples_by_time[$t])) $samples_by_time[$t] = array();
            $samples_by_time[$t][] = $row;
        }
        ksort($samples_by_time, SORT_NUMERIC);

        $time_profiles = array();
        foreach ($samples_by_time as $t => $rows) {
            $profile = clock_time_profile_from_rows($rows);
            if ($profile === null) continue;
            $time_profiles[$t] = $profile;
        }

        $runs = array();
        $time_to_run = array();
        $latest_run_id = null;
        $latest_run_end = 0;
        $run_index = 0;
        $current_run_id = null;
        $current_run_key = null;
        foreach ($time_profiles as $t => $profile) {
            // Merge only adjacent samples with the same clock and routing.
            // A -> B -> A is three runs; merging both A periods would include
            // B's elapsed time in accepted-work deltas and distort hashrate.
            if ($current_run_id === null || $current_run_key !== $profile['run_key']) {
                $run_index++;
                $current_run_id = 'run-' . $run_index;
                $current_run_key = $profile['run_key'];
                $runs[$current_run_id] = array(
                    'id' => $current_run_id,
                    'profile_key' => $profile['key'],
                    'run_key' => $profile['run_key'],
                    'clock' => $profile['clock'],
                    'experimental' => $profile['experimental'],
                    'recover' => $profile['recover'],
                    'cutoff' => $profile['cutoff'],
                    'fan' => $profile['fan'],
                    'fan_mode' => $profile['fan_mode'],
                    'start_ts' => (int)$t,
                    'end_ts' => (int)$t,
                    'active' => false,
                    'boards_by_id' => array(),
                    'sample_count' => 0,
                );
            }
            // Keep the run's headline fan/recover/cutoff at the latest values seen
            // within the clock segment (the clock defines the run; these may vary).
            $runs[$current_run_id]['recover'] = $profile['recover'];
            $runs[$current_run_id]['cutoff'] = $profile['cutoff'];
            $runs[$current_run_id]['fan'] = $profile['fan'];
            $runs[$current_run_id]['fan_mode'] = $profile['fan_mode'];
            $runs[$current_run_id]['end_ts'] = (int)$t;
            $time_to_run[$t] = $current_run_id;
            if ((int)$t >= $latest_run_end) {
                $latest_run_end = (int)$t;
                $latest_run_id = $current_run_id;
            }
        }

        foreach ($all_samples as $row) {
            $s = $row['sample'];
            $t = isset($s['t']) ? (int)$s['t'] : 0;
            if (!$t || !isset($time_to_run[$t])) continue;
            $run_id = $time_to_run[$t];
            $board_id = (int)$row['board_id'];
            if (!isset($runs[$run_id]['boards_by_id'][$board_id])) {
                $runs[$run_id]['boards_by_id'][$board_id] = array();
            }
            $runs[$run_id]['boards_by_id'][$board_id][] = $s;
            $runs[$run_id]['sample_count']++;
        }

        $runs_out = array();
        foreach ($runs as $run_id => $run) {
            $boards = array();
            foreach ($run['boards_by_id'] as $board_id => $samples) {
                $boards[] = clock_aggregate_board($board_id, $samples);
            }
            usort($boards, function($a, $b) { return $a['id'] - $b['id']; });
            unset($run['boards_by_id']);
            $run['boards'] = $boards;
            $run['duration_seconds'] = max(0, $run['end_ts'] - $run['start_ts']);
            $runs_out[] = $run;
        }
        if (!empty($runs_out)) {
            foreach ($runs_out as &$run_out) {
                $run_out['active'] = ($latest_run_id !== null && $run_out['id'] === $latest_run_id);
            }
            unset($run_out);
        }
        usort($runs_out, function($a, $b) {
            if (!empty($a['active']) && empty($b['active'])) return -1;
            if (empty($a['active']) && !empty($b['active'])) return 1;
            return ((int)$b['end_ts']) - ((int)$a['end_ts']);
        });

        $response = array(
            'runs' => $runs_out,
            'window_hours' => $hours,
            'step_seconds' => $step,
            'updated' => isset($hist['updated']) ? (int)$hist['updated'] : 0,
            'storage' => $CLOCK_HISTORY_PERSIST_SD ? 'SD' : 'RAM',
            'read_path' => $CLOCK_HISTORY_READ,
        );
        break;

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

        $hist = read_json_file($CLOCK_HISTORY_READ, array('boards' => array(), 'updated' => 0));
        $boards = isset($hist['boards']) && is_array($hist['boards']) ? $hist['boards'] : array();
        $target_times = array();
        if ($run_key !== '') {
            $samples_by_time = array();
            foreach ($boards as $board) {
                if (!is_array($board) || !isset($board['samples']) || !is_array($board['samples'])) {
                    continue;
                }
                $board_id = isset($board['id']) ? (int)$board['id'] : 0;
                foreach ($board['samples'] as $sample) {
                    if (!is_array($sample)) continue;
                    $t = isset($sample['t']) ? (int)$sample['t'] : 0;
                    if ($t < $start || $t > $end) continue;
                    if (!isset($samples_by_time[$t])) $samples_by_time[$t] = array();
                    $samples_by_time[$t][] = array('board_id' => $board_id, 'sample' => $sample);
                }
            }
            foreach ($samples_by_time as $t => $rows) {
                $profile = clock_time_profile_from_rows($rows);
                if ($profile !== null && isset($profile['run_key']) && $profile['run_key'] === $run_key) {
                    $target_times[$t] = true;
                }
            }
        }
        $removed = 0;
        foreach ($boards as &$board) {
            if (!is_array($board) || !isset($board['samples']) || !is_array($board['samples'])) {
                continue;
            }
            $kept = array();
            foreach ($board['samples'] as $sample) {
                if (!is_array($sample)) {
                    $kept[] = $sample;
                    continue;
                }
                $t = isset($sample['t']) ? (int)$sample['t'] : 0;
                $in_range = ($t >= $start && $t <= $end);
                $remove_sample = false;
                if ($run_key !== '') {
                    $remove_sample = isset($target_times[$t]);
                } else {
                    $remove_sample = ($in_range && clock_sample_profile_matches($sample, $profile_key));
                }
                if ($remove_sample) {
                    $removed++;
                    continue;
                }
                $kept[] = $sample;
            }
            $board['samples'] = $kept;
        }
        unset($board);

        $hist['boards'] = $boards;
        $hist['updated'] = time();
        if (!isset($hist['version'])) $hist['version'] = 1;
        if (!isset($hist['step_seconds'])) $hist['step_seconds'] = 60;
        if (!isset($hist['max_age_seconds'])) $hist['max_age_seconds'] = 7 * 86400;
        $write_paths = $CLOCK_HISTORY_PERSIST_SD
            ? array($CLOCK_HISTORY_SD_BUFFER, $CLOCK_HISTORY_SD)
            : array($CLOCK_HISTORY_RAM);
        if (!write_json_files($write_paths, $hist)) {
            http_response_code(500);
            $response = array('error' => 'failed to write clock history');
            break;
        }
        $response = array('ok' => true, 'removed_samples' => $removed);
        break;

    case 'sysstats':
        // Read /proc/meminfo and /proc/loadavg for the dashboard footer.
        $mem_total = 0;
        $mem_available = 0;
        $mem_free = 0;
        $buffers = 0;
        $cached = 0;
        $meminfo = @file_get_contents('/proc/meminfo');
        if ($meminfo !== false) {
            foreach (explode("\n", $meminfo) as $line) {
                if (preg_match('/^(\w+):\s+(\d+)/', $line, $m)) {
                    if ($m[1] === 'MemTotal')     $mem_total = (int)$m[2];
                    if ($m[1] === 'MemAvailable') $mem_available = (int)$m[2];
                    if ($m[1] === 'MemFree')      $mem_free = (int)$m[2];
                    if ($m[1] === 'Buffers')      $buffers = (int)$m[2];
                    if ($m[1] === 'Cached')       $cached = (int)$m[2];
                }
            }
        }
        // Older kernels (3.4 sun8i) may not have MemAvailable; fall back.
        if ($mem_available === 0) {
            $mem_available = $mem_free + $buffers + $cached;
        }
        $mem_used = $mem_total - $mem_available;
        $mem_pct = ($mem_total > 0) ? round(($mem_used / $mem_total) * 100) : 0;

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

    case 'delete_backup':
    case 'rename_backup':
        // Both share the same path-validation + backup-folder constants.
        $BACKUP_FOLDER = '/opt/scripta/etc/backup/';

        // Validate the source name. Only [A-Za-z0-9._-] allowed; no slashes,
        // no leading dot, no '..'. This is the directory name passed in by
        // the dropdown which originated from filesystem listing, but we
        // re-validate defensively.
        $name = isset($_REQUEST['name']) ? $_REQUEST['name'] : '';
        if ($name === '' || !preg_match('/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/', $name)) {
            http_response_code(400);
            $response = array('error' => 'invalid backup name: ' . $name);
            break;
        }
        $src = $BACKUP_FOLDER . $name;
        if (!is_dir($src)) {
            http_response_code(404);
            $response = array('error' => 'backup not found: ' . $name);
            break;
        }
        // Defensive: realpath must still be inside the backup folder.
        $real = realpath($src);
        if ($real === false || strpos($real, realpath($BACKUP_FOLDER)) !== 0) {
            http_response_code(400);
            $response = array('error' => 'backup name escapes backup folder');
            break;
        }

        if ($action === 'delete_backup') {
            // Recursive remove. Only deletes files + dirs inside the
            // validated backup directory.
            $delete_recursive = function($path) use (&$delete_recursive) {
                if (is_dir($path)) {
                    $children = @scandir($path);
                    if ($children !== false) {
                        foreach ($children as $c) {
                            if ($c === '.' || $c === '..') continue;
                            $delete_recursive($path . '/' . $c);
                        }
                    }
                    return @rmdir($path);
                }
                return @unlink($path);
            };
            if ($delete_recursive($real)) {
                $response = array('ok' => true,
                                  'info' => array(array('type' => 'success',
                                                        'text' => 'Backup "' . $name . '" deleted')));
            } else {
                http_response_code(500);
                $response = array('error' => 'failed to delete backup ' . $name,
                                  'info' => array(array('type' => 'danger',
                                                        'text' => 'Could not delete backup "' . $name . '"')));
            }
        } else {
            // rename_backup: validate the destination name with the same rule
            $newname = isset($_REQUEST['to']) ? $_REQUEST['to'] : '';
            if ($newname === '' || !preg_match('/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/', $newname)) {
                http_response_code(400);
                $response = array('error' => 'invalid new backup name: ' . $newname);
                break;
            }
            if ($newname === $name) {
                $response = array('ok' => true,
                                  'info' => array(array('type' => 'info',
                                                        'text' => 'Name unchanged')));
                break;
            }
            $dst = $BACKUP_FOLDER . $newname;
            if (file_exists($dst)) {
                http_response_code(409);
                $response = array('error' => 'a backup with that name already exists',
                                  'info' => array(array('type' => 'danger',
                                                        'text' => 'Backup "' . $newname . '" already exists')));
                break;
            }
            if (@rename($real, $dst)) {
                $response = array('ok' => true,
                                  'info' => array(array('type' => 'success',
                                                        'text' => 'Renamed "' . $name . '" → "' . $newname . '"')));
            } else {
                http_response_code(500);
                $response = array('error' => 'failed to rename backup',
                                  'info' => array(array('type' => 'danger',
                                                        'text' => 'Could not rename "' . $name . '"')));
            }
        }
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
                                           'load_failover_history', 'clear_failover_history',
                                           'delete_backup', 'rename_backup'));
}

echo json_encode($response);
