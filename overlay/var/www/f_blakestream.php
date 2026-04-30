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
//   save_pools (data=<json>)   - writes saved-pools.json then triggers restart
//   load_assignments           - returns board-assignments.json
//   save_assignments (data=)   - writes board-assignments.json then triggers restart
//   load_runtime               - returns the active runtime config (debug)
//   restart                    - re-runs miner-start.sh
//   status                     - returns a brief device summary

session_start();
if (!isset($_SESSION['_logged_']) || $_SESSION['_logged_'] !== true) {
    http_response_code(401);
    echo json_encode(array('error' => 'not logged in'));
    exit;
}

header('Content-Type: application/json');

$ETC = '/opt/scripta/etc';
$VAR = '/opt/scripta/var';
$SAVED_POOLS = $ETC . '/saved-pools.json';
$ASSIGNMENTS = $ETC . '/board-assignments.json';
$TEMP_STATE = $ETC . '/temp-state.json';
$TEMP_CONFIG = $ETC . '/temp-config.json';
$FAILOVER_CONFIG = $ETC . '/failover-config.json';
$POOL_HISTORY = $VAR . '/pool-history.json';
$HASHRATE_HISTORY = $VAR . '/hashrate-history.json';
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
    $tmp = $path . '.tmp';
    $flags = 0;
    if (defined('JSON_PRETTY_PRINT')) $flags |= JSON_PRETTY_PRINT;
    if ($force_object && defined('JSON_FORCE_OBJECT')) $flags |= JSON_FORCE_OBJECT;
    $json = json_encode($data, $flags);
    if (file_put_contents($tmp, $json) === false) return false;
    return rename($tmp, $path);
}

function trigger_restart($launcher) {
    // Run miner-start.sh in the background, detached, so we don't block the
    // HTTP response. www-data needs sudo / setuid to run miner-start.sh as
    // root. For now we use a sudoers entry (see README) — alternatively the
    // launcher could be setuid root.
    $cmd = "sudo -n " . escapeshellarg($launcher) . " > /tmp/blakestream-launcher.log 2>&1 &";
    @exec($cmd);
}

// (trigger_apply_assignments helper removed: ascdisable is broken in
// sgminer-baikal so apply-board-assignments.py is a no-op now. Pool
// changes always require a full sgminer restart so the runtime config
// gets regenerated with the new pool list.)

function summary_assignment($a) {
    if (!$a) return array();
    $out = array();
    foreach ($a as $bid => $pid) {
        $out[(string)$bid] = $pid;
    }
    return $out;
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

$response = array();

switch ($action) {
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
        // Auto-restart so the new pool list takes effect
        trigger_restart($LAUNCHER);
        $response = array('ok' => true, 'restarted' => true);
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
        if (!write_json_file($ASSIGNMENTS, $clean, true)) {
            http_response_code(500);
            $response = array('error' => 'failed to write ' . $ASSIGNMENTS);
            break;
        }
        // ALWAYS trigger a full restart on assignment change. The runtime
        // config (which pools sgminer mines) is built from board-assignments.json,
        // so any change requires regenerating /tmp/scripta-runtime-miner.conf
        // and restarting sgminer to pick it up.
        trigger_restart($LAUNCHER);
        $response = array('ok' => true, 'restarted' => true, 'assignments' => (object) $clean);
        break;

    case 'load_runtime':
        $response = read_json_file($RUNTIME, array('error' => 'no runtime config (idle?)'));
        break;

    case 'restart':
        trigger_restart($LAUNCHER);
        $response = array('ok' => true, 'restarted' => true);
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
            if ($v) $assigned++;
        }
        $response = array(
            'categories' => $cat_count,
            'pools' => $pool_count,
            'boards_assigned' => $assigned,
            'boards_idle' => 3 - $assigned,
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
        trigger_restart($LAUNCHER);
        $response = array('ok' => true, 'restarted' => true, 'config' => $clean);
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
                                           'delete_backup', 'rename_backup'));
}

echo json_encode($response);
