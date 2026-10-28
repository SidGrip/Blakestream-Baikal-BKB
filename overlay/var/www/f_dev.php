<?php
require_once __DIR__ . '/inc/web-security.php';
bs_secure_session_start();

// Auth gate 1: must be logged in
if (!isset($_SESSION['_logged_']) || $_SESSION['_logged_'] !== true) {
    http_response_code(403);
    echo json_encode(array('ok' => false, 'error' => 'forbidden'));
    exit;
}

// Auth gate 2: userDev must be enabled in scripta.conf
$ETC = '/opt/scripta/etc';
$SCRIPTA_CONF = $ETC . '/scripta.conf';
$cfg = @json_decode(@file_get_contents($SCRIPTA_CONF), true);
$action = isset($_GET['action']) && is_string($_GET['action']) ? $_GET['action'] : '';
$settingsFirmwareActions = array(
    'firmware_status' => true,
    'firmware_progress' => true,
    'firmware_verify' => true,
    'firmware_flash_custom' => true,
    'firmware_abort' => true,
);
if ((!is_array($cfg) || empty($cfg['userDev'])) && !isset($settingsFirmwareActions[$action])) {
    http_response_code(403);
    echo json_encode(array('ok' => false, 'error' => 'dev-tools disabled'));
    exit;
}

// Activity operations retain their narrower token as defense in depth. Every
// mutation also passes the shared header-token and origin/method policy below.
require_once __DIR__ . '/inc/dev-activity.php';
require_once __DIR__ . '/inc/firmware-identity.php';
require_once __DIR__ . '/inc/experimental-clock.php';
require_once __DIR__ . '/inc/network-probe.php';
require_once __DIR__ . '/inc/clock-log.php';
require_once __DIR__ . '/inc/measured-rates.php';
require_once __DIR__ . '/inc/web-auth.php';
require_once __DIR__ . '/inc/diagnostic-logs.php';
require_once __DIR__ . '/inc/sgminer-api-policy.php';
if (!isset($_SESSION['dev_csrf']) || !dev_activity_csrf_well_formed($_SESSION['dev_csrf'])) {
    $minted = dev_activity_csrf_mint();
    if ($minted !== '') $_SESSION['dev_csrf'] = $minted;
}
$dev_activity_csrf = (isset($_SESSION['dev_csrf']) && dev_activity_csrf_well_formed($_SESSION['dev_csrf']))
    ? $_SESSION['dev_csrf'] : '';
$web_csrf = bs_csrf_ensure($_SESSION);
$devReadActions = array(
    'log_tail', 'log_download', 'network_pools', 'network_probe', 'recorder_status',
    'recorder_health', 'state_snapshot', 'diagnostics_zip', 'board_health', 'storage',
    'measured_rates', 'measured_rates_download', 'file_download', 'file_view',
    'firmware_status', 'firmware_progress', 'activity'
);
$devMutationActions = array(
    'log_clear', 'sgminer_api', 'recorder_run', 'measured_rates_update', 'storage_set_target',
    'firmware_verify', 'firmware_revert', 'firmware_flash_custom', 'firmware_abort',
    'storage_clear', 'storage_clear_all', 'log_prune', 'activity_trace'
);
bs_enforce_action($action, $devReadActions, $devMutationActions, $web_csrf);

// Both auth gates passed. Release the session lock so a long firmware op
// cannot block concurrent progress polls.
@session_write_close();

$RUNTIME_DIR = '/run/blakestream';
$DEV_LOG_CLEAR_MARKERS = $RUNTIME_DIR . '/dev-log-clear-markers.json';

// ============================================================================
// Helpers
// ============================================================================

function read_json($path, $default = array()) {
    if (!file_exists($path)) return $default;
    $raw = @file_get_contents($path);
    if ($raw === false) return $default;
    $d = @json_decode($raw, true);
    return ($d === null) ? $default : $d;
}

function normalize_log_target($target) {
    return (is_string($target) && strtoupper($target) === 'SD') ? 'SD' : 'RAM';
}

function sgminer_log_target($cfg) {
    $target = (is_array($cfg) && isset($cfg['userDevLogTarget'])) ? $cfg['userDevLogTarget'] : 'RAM';
    return normalize_log_target($target);
}

function sgminer_log_paths_for_target($target) {
    $target = normalize_log_target($target);
    if ($target === 'RAM') {
        // Root writes the RAM log below a sealed parent; the web UI receives
        // file-level access but cannot replace it with a symlink.
        return array('/run/blakestream-miner/sgminer.log');
    }
    return array('/var/log/blakestream-miner/sgminer.log');
}

function first_existing_path($paths) {
    foreach ($paths as $p) {
        if (file_exists($p)) return $p;
    }
    return count($paths) ? $paths[0] : null;
}

function command_exists($cmd) {
    $out = @shell_exec('command -v ' . escapeshellarg($cmd) . ' 2>/dev/null');
    return ($out !== null && trim($out) !== '');
}

class DevZip {
    private $native;
    private $tmp;
    private $stage;

    function __construct($tmp, $native = null, $stage = '') {
        $this->tmp = $tmp;
        $this->native = $native;
        $this->stage = $stage;
    }

    function addFile($abs, $name) {
        if ($this->native) return $this->native->addFile($abs, $name);
        $dest = $this->stage . '/' . $this->safeName($name);
        $dir = dirname($dest);
        if (!is_dir($dir) && !mkdir($dir, 0755, true)) return false;
        return @copy($abs, $dest);
    }

    function addFromString($name, $contents) {
        if ($this->native) return $this->native->addFromString($name, $contents);
        $dest = $this->stage . '/' . $this->safeName($name);
        $dir = dirname($dest);
        if (!is_dir($dir) && !mkdir($dir, 0755, true)) return false;
        return @file_put_contents($dest, $contents) !== false;
    }

    function close() {
        if ($this->native) return $this->native->close();
        $cmd = 'cd ' . escapeshellarg($this->stage)
             . ' && zip -q -r ' . escapeshellarg($this->tmp) . ' . 2>&1';
        @shell_exec($cmd);
        @shell_exec('rm -rf ' . escapeshellarg($this->stage));
        return file_exists($this->tmp) && filesize($this->tmp) > 0;
    }

    private function safeName($name) {
        $name = str_replace('\\', '/', $name);
        $parts = array();
        foreach (explode('/', $name) as $part) {
            if ($part === '' || $part === '.' || $part === '..') continue;
            $parts[] = $part;
        }
        return count($parts) ? implode('/', $parts) : 'file';
    }
}

function dev_zip_create($tmp) {
    if (class_exists('ZipArchive')) {
        $native = new ZipArchive();
        if ($native->open($tmp, ZipArchive::CREATE | ZipArchive::OVERWRITE) !== true) return null;
        return new DevZip($tmp, $native);
    }

    if (!command_exists('zip')) return null;
    $stage = tempnam('/tmp', 'blakestream-diag-stage-');
    if ($stage === false) return null;
    @unlink($stage);
    if (!mkdir($stage, 0700, true)) return null;
    return new DevZip($tmp, null, $stage);
}

function sgminer_log_path($cfg, $target = '') {
    $target = ($target === '') ? sgminer_log_target($cfg) : normalize_log_target($target);
    return first_existing_path(sgminer_log_paths_for_target($target));
}

function strip_terminal_control_sequences($text) {
    // OSC commands, CSI cursor/style commands, character-set selectors, and
    // remaining C0 controls emitted by sgminer's ncurses display.
    $text = preg_replace('/\x1B\][^\x07]*(?:\x07|\x1B\\\\)/s', '', $text);
    $text = preg_replace('/\x1B\[[0-?]*[ -\/]*[@-~]/', '', $text);
    $text = preg_replace('/\x1B[()][0-2A-Z]/', '', $text);
    $text = preg_replace('/\x1B[=>]/', '', $text);
    return preg_replace('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/', '', $text);
}

function normalize_sgminer_log_text($text) {
    $text = str_replace(array("\r\n", "\r"), "\n", $text);
    $timestamp = '\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]';
    $lines = array();
    $seen = array();

    foreach (explode("\n", $text) as $raw_line) {
        $records = array();
        preg_match_all('/' . $timestamp . '.*?(?=' . $timestamp . '|$)/s', $raw_line, $records);

        if (!empty($records[0])) {
            foreach ($records[0] as $record) {
                // Ncurses redraws duplicate recent messages using cursor moves.
                // Keep sgminer's original plain-text record and discard the
                // redraw copy instead of trying to reconstruct terminal state.
                if (preg_match('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/', $record)) {
                    continue;
                }
                $record = trim(strip_terminal_control_sequences($record));
                $record = preg_replace('/[ \t]+/', ' ', $record);
                if ($record !== '' && !isset($seen[$record])) {
                    $seen[$record] = true;
                    $lines[] = $record;
                }
            }
            continue;
        }

        // Preserve ordinary text-only/startup output. Lines containing terminal
        // controls but no timestamp are ncurses dashboard redraw fragments.
        if (!preg_match('/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/', $raw_line)) {
            $line = rtrim($raw_line);
            if ($line !== '' && !isset($seen[$line])) {
                $seen[$line] = true;
                $lines[] = $line;
            }
        }
    }

    return implode("\n", $lines) . (count($lines) ? "\n" : '');
}

function normalize_log_text($source, $text) {
    if ($source === 'sgminer') {
        return normalize_sgminer_log_text($text);
    }
    return $text;
}

function lighttpd_log_path($kind) {
    if ($kind === 'access') {
        return first_existing_path(array('/run/blakestream-web/access.log', '/var/log/lighttpd/access.log'));
    }
    $cfg = read_json('/opt/scripta/etc/scripta.conf');
    return bs_diagnostic_source_path('lighttpd', $cfg);
}

function temp_state_path() {
    return first_existing_path(array('/run/blakestream-control/temp-state.json', '/opt/scripta/etc/temp-state.json'));
}

function dev_recorder_specs() {
    return array(
        'diagnostics' => array(
            'id' => 'diagnostics', 'name' => 'Diagnostic logs', 'cron_sec' => 30,
            'script' => '/opt/scripta/bin/diagnostic-log-recorder.py',
            'paths' => array('/run/blakestream/diagnostic-log-state.json'),
            'storage' => false,
        ),
        'hashrate' => array(
            'id' => 'hashrate',
            'name' => 'Hashrate History',
            'cron_sec' => 60,
            'script' => '/opt/scripta/bin/hashrate-history-recorder.py',
            'paths' => array('/opt/scripta/var/hashrate-history.json', '/run/blakestream/hashrate-history.json'),
        ),
        'pool' => array(
            'id' => 'pool',
            'name' => 'Pool History',
            'cron_sec' => 300,
            'script' => '/opt/scripta/bin/pool-history-recorder.py',
            'paths' => array('/opt/scripta/var/pool-history.json', '/run/blakestream/pool-history.json'),
        ),
        'failover' => array(
            'id' => 'failover',
            'name' => 'Failover History',
            'cron_sec' => 60,
            'script' => '/opt/scripta/bin/failover-history-recorder.py',
            'paths' => array('/opt/scripta/var/failover-events.json', '/run/blakestream/failover-events.json'),
        ),
        'clock' => array(
            'id' => 'clock',
            'name' => 'Clock History',
            'cron_sec' => 60,
            'script' => '/opt/scripta/bin/clock-history-recorder.py',
            'paths' => array(
                '/opt/scripta/var/clock-history.json',
                '/run/blakestream/clock-history.json',
                '/run/blakestream/clock-history-sd-buffer.json',
            ),
        ),
    );
}

function recorder_file_status($paths) {
    clearstatcache();
    $best_path = '';
    $best_mtime = 0;
    $best_size = 0;
    foreach ($paths as $p) {
        if (file_exists($p)) {
            $m = @filemtime($p);
            if ($m && $m > $best_mtime) {
                $best_mtime = $m;
                $best_path = $p;
                $best_size = (int) @filesize($p);
            }
        }
    }
    return array(
        'path' => $best_path ?: $paths[0],
        'mtime' => $best_mtime,
        'size' => $best_size,
        'age_sec' => $best_mtime ? max(0, time() - $best_mtime) : -1,
    );
}

function recorder_journal_tag($id) {
    // Per-recorder logger tag (the cron lines pipe to logger -t bs-*).
    $map = array('hashrate' => 'bs-hashrate', 'pool' => 'bs-history', 'failover' => 'bs-failover', 'clock' => 'bs-clock');
    return isset($map[$id]) ? $map[$id] : '';
}

function recorder_pool_waiting($age_sec, $cron_sec, $cron_up, $boot_seconds, $miner_seconds) {
    if (!$cron_up || $age_sec >= 0) return false;
    // A first attempt can precede mining; allow its cadence plus one scheduler tick.
    $grace = $cron_sec + 60;
    foreach (array($boot_seconds, $miner_seconds) as $elapsed) {
        if (is_numeric($elapsed) && $elapsed >= 0 && $elapsed <= $grace) return true;
    }
    return false;
}

function recorder_status_rows() {
    $out = array();
    foreach (dev_recorder_specs() as $spec) {
        $st = recorder_file_status($spec['paths']);
        $out[] = array(
            'id' => $spec['id'],
            'name' => $spec['name'],
            'cron_sec' => $spec['cron_sec'],
            'path' => $st['path'],
            'mtime' => $st['mtime'],
            'age_sec' => $st['age_sec'],
            'size' => $st['size'],
            'running' => false,
        );
    }
    return $out;
}

// ---- Storage / disk-usage view (Logs tab) -------------------------------
// Read-only. Reports the size + location (RAM tmpfs vs SD card) of every log,
// history, and runtime file, plus df headroom, so the operator can see exactly
// what is wearing the SD card.
function security_line_ts($line) {
    // web line:  "2026-06-12 03:09:42 web login-FAIL ..."
    if (preg_match('/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/', $line, $m)) {
        $t = strtotime($m[1]);
        return ($t !== false) ? $t : null;
    }
    // ssh line:  "ssh 2026-06-12T03:08:22+0800 host sshd[..]: ..."
    if (preg_match('/^ssh\s+(\S+)/', $line, $m)) {
        $t = strtotime($m[1]);
        return ($t !== false) ? $t : null;
    }
    return null;   // unparseable (e.g. trim marker) -> keep
}

function storage_clearable_paths() {
    // Files www-data can truncate from the UI. History JSON are recorder-owned
    // and simply rebuild after a truncate. NOT security.log (intrusion evidence
    // — that one is pruned to 7 days instead, never wiped).
    $paths = array(
        '/run/blakestream-miner/sgminer.log',
        '/var/log/blakestream-miner/sgminer.log',
        '/run/blakestream-web/access.log',
        '/var/log/lighttpd/access.log',
        '/opt/scripta/etc/temp-state.json',
        '/run/blakestream/hashrate-history.json', '/opt/scripta/var/hashrate-history.json',
        '/run/blakestream/pool-history.json', '/opt/scripta/var/pool-history.json',
        '/run/blakestream/failover-events.json', '/opt/scripta/var/failover-events.json',
        '/run/blakestream/clock-history.json', '/opt/scripta/var/clock-history.json',
        '/run/blakestream/clock-history-sd-buffer.json',
    );
    foreach (bs_diagnostic_logs() as $name => $spec) {
        foreach (array('RAM', 'SD') as $side) $paths[] = bs_diagnostic_path($name, $side);
    }
    return $paths;
}

function storage_view_source($path) {
    foreach (bs_diagnostic_logs() as $name => $spec) {
        if ($path === bs_diagnostic_path($name, 'RAM') || $path === bs_diagnostic_path($name, 'SD')) return $spec['source'];
    }
    $map = array(
        '/run/blakestream-miner/sgminer.log' => 'sgminer',
        '/var/log/blakestream-miner/sgminer.log' => 'sgminer',
        '/run/blakestream/lighttpd-error.log' => 'lighttpd',
        '/var/log/lighttpd/error.log' => 'lighttpd',
        '/run/blakestream-web/access.log' => 'lighttpd_access',
        '/var/log/lighttpd/access.log' => 'lighttpd_access',
        '/run/blakestream-control/temp-state.json' => 'temp_state',
        '/var/log/syslog' => 'syslog',
        '/var/log/kern.log' => 'kern',
        '/var/log/daemon.log' => 'daemon',
        '/var/log/auth.log' => 'auth',
        '/var/log/blakestream-security/ssh.log' => 'ssh',
    );
    return isset($map[$path]) ? $map[$path] : '';
}

function storage_stat($name, $path, $cap = 0) {
    clearstatcache(false, $path);
    $present = file_exists($path);
    $size = $present ? (int) @filesize($path) : 0;
    $mtime = $present ? (int) @filemtime($path) : 0;
    $row = array(
        'name' => $name,
        'path' => $path,
        'present' => $present ? true : false,
        'size' => $size,
        'mtime' => $mtime,
        'age_sec' => $mtime ? max(0, time() - $mtime) : -1,
        'clearable' => in_array($path, storage_clearable_paths(), true),
        'view' => storage_view_source($path),
    );
    if ($cap > 0) $row['cap_bytes'] = $cap;
    return $row;
}

function storage_df($path) {
    $total = @disk_total_space($path);
    $free = @disk_free_space($path);
    if (!$total || $free === false) return null;
    $used = $total - $free;
    return array(
        'total' => (float) $total,
        'used' => (float) $used,
        'avail' => (float) $free,
        'pct' => (int) round($used / $total * 100),
    );
}

function storage_recorder_files($files, $prefix) {
    foreach (dev_recorder_specs() as $spec) {
        if (isset($spec['storage']) && !$spec['storage']) continue;
        foreach ($spec['paths'] as $path) {
            if (strpos($path, $prefix) !== 0) continue;
            if (basename($path) === 'clock-history-sd-buffer.json' && !is_file($path)) continue;
            $found = false;
            foreach ($files as $i => $file) {
                if ($file['path'] !== $path) continue;
                $files[$i]['recorder_id'] = $spec['id'];
                $found = true;
                break;
            }
            if (!$found) {
                $file = storage_stat(basename($path), $path);
                $file['recorder_id'] = $spec['id'];
                $files[] = $file;
            }
        }
    }
    return $files;
}

function storage_report() {
    global $dev_activity_csrf;
    $SGMINER_CAP = 4194304; // mirrors capped-log-writer.py DEFAULT_MAX_BYTES (4 MiB)
    $ram = array(
        storage_stat('sgminer.log', '/run/blakestream-miner/sgminer.log', $SGMINER_CAP),
        storage_stat('hashrate-history.json', '/run/blakestream/hashrate-history.json'),
        storage_stat('pool-history.json', '/run/blakestream/pool-history.json'),
        storage_stat('failover-events.json', '/run/blakestream/failover-events.json'),
        storage_stat('clock-history.json', '/run/blakestream/clock-history.json'),
        storage_stat('temp-state.json', '/run/blakestream-control/temp-state.json'),
        storage_stat('lighttpd-access.log', '/run/blakestream-web/access.log'),
    );
    $sd = array(
        storage_stat('sgminer.log', '/var/log/blakestream-miner/sgminer.log', $SGMINER_CAP),
        storage_stat('security.log', '/opt/scripta/var/security.log'),
        storage_stat('ssh-auth.log', '/var/log/blakestream-security/ssh.log'),
        storage_stat('hashrate-history.json', '/opt/scripta/var/hashrate-history.json'),
        storage_stat('pool-history.json', '/opt/scripta/var/pool-history.json'),
        storage_stat('failover-events.json', '/opt/scripta/var/failover-events.json'),
        storage_stat('clock-history.json', '/opt/scripta/var/clock-history.json'),
        storage_stat('lighttpd/access.log', '/var/log/lighttpd/access.log'),
    );
    $ram = storage_recorder_files($ram, '/run/blakestream/');
    $sd = storage_recorder_files($sd, '/opt/scripta/var/');
    // Mark files whose RAM/SD location is user-configurable + their current target,
    // so the storage view can show a per-row RAM/SD selector.
    $cfg = @json_decode(@file_get_contents('/opt/scripta/etc/scripta.conf'), true);
    if (!is_array($cfg)) $cfg = array();
    foreach (bs_diagnostic_logs() as $name => $spec) {
        foreach (array('RAM', 'SD') as $side) {
            $row = storage_stat($name, bs_diagnostic_path($name, $side), 1048576);
            $row['target'] = bs_diagnostic_target($spec, $cfg);
            $row['target_key'] = $name;
            if ($side === 'RAM') $ram[] = $row; else $sd[] = $row;
        }
    }
    $tgt = function ($name) use ($cfg) {
        switch ($name) {
            case 'hashrate-history.json': return (strtoupper(isset($cfg['userChartsSaveTo']) ? $cfg['userChartsSaveTo'] : 'RAM') === 'SD') ? 'SD' : 'RAM';
            case 'sgminer.log':           return (strtoupper(isset($cfg['userDevLogTarget']) ? $cfg['userDevLogTarget'] : 'RAM') === 'SD') ? 'SD' : 'RAM';
            case 'pool-history.json':     return !empty($cfg['userPersistPoolHistory']) ? 'SD' : 'RAM';
            case 'failover-events.json':  return !empty($cfg['userPersistFailoverHistory']) ? 'SD' : 'RAM';
            case 'clock-history.json':    return !empty($cfg['userPersistClockHistory']) ? 'SD' : 'RAM';
            default: return '';
        }
    };
    foreach ($ram as $i => $r) { $t = $tgt($r['name']); if ($t !== '') { $ram[$i]['target'] = $t; $ram[$i]['target_key'] = $r['name']; } }
    foreach ($sd as $i => $r) { $t = $tgt($r['name']); if ($t !== '') { $sd[$i]['target'] = $t; $sd[$i]['target_key'] = $r['name']; } }

    $measured = bs_measured_read();
    $target = isset($measured['target']) ? $measured['target'] : 'RAM';
    foreach (array('RAM', 'SD') as $side) {
        $row = array('name' => 'Measured Rates', 'path' => 'dataset:measured-rates:' . $side,
            'target_key' => 'measured-rates', 'target' => $target, 'present' => $side === $target,
            'size' => $side === $target && isset($measured['bytes']) ? $measured['bytes'] : 0,
            'age_sec' => isset($measured['last_saved']) ? max(0, time() - $measured['last_saved']) : 0,
            'clearable' => true, 'view' => 'measured-rates');
        if ($side === 'RAM') $ram[] = $row; else $sd[] = $row;
    }

    $ram_total = 0; foreach ($ram as $r) { $ram_total += $r['size']; }
    $sd_total = 0; foreach ($sd as $r) { $sd_total += $r['size']; }
    return array(
        'ok' => true,
        'measured' => $measured,
        'csrf' => $dev_activity_csrf,
        'generated_at' => time(),
        'ram' => $ram,
        'sd' => $sd,
        'ram_total_bytes' => $ram_total,
        'sd_total_bytes' => $sd_total,
        'ram_df' => storage_df('/run/blakestream'),
        'sd_df' => storage_df('/'),
    );
}

// ---- Firmware / bridge-chip identity + reversible revert-to-stock ----------
function firmware_known_images() {
    return array(
        'd5f844be2952fd713717a71a67e2be61' => 'STOCK',
    ) + bkb_firmware_clock_images();
}
function firmware_identity_for_md5($md5) {
    $k = firmware_known_images();
    return isset($k[$md5]) ? $k[$md5] : 'UNKNOWN';
}
function firmware_identity_paths() {
    return array('/opt/scripta/etc/bkb-firmware-identity.json');
}
function firmware_last_identity() {
    foreach (firmware_identity_paths() as $path) {
        $d = @json_decode(@file_get_contents($path), true);
        if (is_array($d)) return bkb_firmware_display_record($d);
    }
    return null;
}
function firmware_write_identity($rec) {
    foreach (firmware_identity_paths() as $path) {
        $dir = dirname($path);
        if ($dir && !is_dir($dir)) @mkdir($dir, 0775, true);
        $tmp = @tempnam($dir, 'bkb-firmware-identity.tmp.');
        if ($tmp === false) continue;
        if (@file_put_contents($tmp, json_encode($rec, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)) === false) {
            @unlink($tmp);
            continue;
        }
        @chmod($tmp, 0664);
        if (!@rename($tmp, $path)) {
            @unlink($tmp);
            continue;
        }
        @chmod($path, 0664);
    }
}
function firmware_clear_identity() {
    foreach (firmware_identity_paths() as $path) {
        @unlink($path);
    }
}
function firmware_miner_running() {
    $fp = @fsockopen('127.0.0.1', 4028, $e, $es, 1);
    if ($fp) { fclose($fp); return true; }
    return false;
}
function firmware_audit($msg) {
    // Append-only, size-capped, persisted to SD (mirrors f_login.php bs_log_auth)
    // so every firmware action leaves an intrusion-reviewable trail.
    $path = '/opt/scripta/var/security.log';
    $ip = isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '-';
    $line = date('Y-m-d H:i:s') . ' devtools ' . $msg . ' ip=' . $ip . "\n";
    if (!bs_web_auth_update_audit($path, $line)) error_log('Firmware audit update failed');
}
function security_log_prune($path, $days) {
    $lock = bs_web_auth_audit_lock($path);
    if ($lock === false) return false;
    try {
        if (is_link($path) || !is_file($path) || filesize($path) > 4194304) return false;
        $text = @file_get_contents($path);
        $text = bs_web_auth_redact_log($text);
        if ($text === false) return false;
        $lines = $text === '' ? array() : explode("\n", rtrim($text, "\n"));
        $cutoff = time() - ($days * 86400);
        $kept = array(); $removed = 0;
        foreach ($lines as $line) {
            $ts = security_line_ts($line);
            if ($ts === null || $ts >= $cutoff) $kept[] = $line;
            else $removed++;
        }
        $out = $kept ? implode("\n", $kept) . "\n" : '';
        if (@file_put_contents($path, $out, LOCK_EX) !== strlen($out)) return false;
        return array('kept' => count($kept), 'removed' => $removed);
    } finally {
        bs_web_auth_audit_unlock($lock);
    }
}
function firmware_runtime_path() {
    return '/opt/scripta/etc/bkb-firmware-runtime.json';
}
function firmware_runtime_label($seconds, $measured) {
    $seconds = (int) $seconds;
    if ($seconds < 1) $seconds = 164;
    return '~' . $seconds . 's';
}
function firmware_runtime_action($action) {
    if ($action === 'revert') return 'stock';
    if ($action === 'flash-custom') return 'custom-v1';
    return in_array($action, array('stock', 'custom-v1', 'verify'), true) ? $action : '';
}
function firmware_runtime_seconds($value) {
    if (!is_numeric($value) || !is_finite((float) $value)) return 0;
    $seconds = (int) round($value);
    return ($seconds >= 1 && $seconds <= 3600) ? $seconds : 0;
}
function firmware_flash_runtime($action = '', $path = null) {
    $action = firmware_runtime_action($action);
    $d = read_json($path === null ? firmware_runtime_path() : $path, array());
    if (!is_array($d)) $d = array();
    $sample = $d;
    if ($action !== '') {
        $sample = isset($d['actions'][$action]) && is_array($d['actions'][$action]) ? $d['actions'][$action] : array();
        if (!count($sample) && isset($d['action']) && $d['action'] === $action) $sample = $d;
    }
    $seconds = isset($sample['seconds']) ? firmware_runtime_seconds($sample['seconds']) : 0;
    if ($seconds) {
        return array(
            'seconds' => $seconds,
            'label' => firmware_runtime_label($seconds, true),
            'measured' => true,
            'action' => $action !== '' ? $action : (isset($sample['action']) ? $sample['action'] : ''),
            'at' => isset($sample['at']) ? (int) $sample['at'] : 0,
        );
    }
    $seconds = $action === 'verify' ? 45 : 164;
    return array('seconds' => $seconds, 'label' => firmware_runtime_label($seconds, false),
        'measured' => false, 'action' => $action, 'at' => 0);
}
function firmware_record_flash_runtime($action, $seconds, $path = null) {
    $action = firmware_runtime_action($action);
    $seconds = firmware_runtime_seconds($seconds);
    if ($action === '' || !$seconds) return false;
    if ($path === null) $path = firmware_runtime_path();
    $data = read_json($path, array());
    if (!is_array($data)) $data = array();
    if (!isset($data['actions']) || !is_array($data['actions'])) $data['actions'] = array();
    // Retain the previous single-sample format when migrating local history.
    if (isset($data['action'], $data['seconds']) && firmware_runtime_action($data['action']) &&
        firmware_runtime_seconds($data['seconds']) && !isset($data['actions'][$data['action']])) {
        $data['actions'][$data['action']] = array('seconds' => (int) $data['seconds'],
            'at' => isset($data['at']) ? (int) $data['at'] : 0);
    }
    $at = time();
    $data['actions'][$action] = array('seconds' => $seconds, 'at' => $at);
    $data['version'] = 2;
    if ($action !== 'verify') {
        $data['seconds'] = $seconds;
        $data['action'] = $action;
        $data['at'] = $at;
    }
    $dir = dirname($path);
    $tmp = @tempnam($dir, 'bkb-firmware-runtime.tmp.');
    if ($tmp === false) return false;
    if (@file_put_contents($tmp, json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)) === false) {
        @unlink($tmp);
        return false;
    }
    @chmod($tmp, 0664);
    if (!@rename($tmp, $path)) {
        @unlink($tmp);
        return false;
    }
    @chmod($path, 0664);
    return true;
}
function firmware_job_paths() {
    $base = '/run/blakestream-firmware';
    return array(
        'lock' => $base . '/bkb-firmware.lock',
        'pid' => $base . '/bkb-firmware.pid',
        'meta' => $base . '/bkb-firmware-meta.env',
        'result' => $base . '/bkb-firmware-result.env',
        'phase' => $base . '/bkb-firmware-phase',
        'progress' => $base . '/bkb-firmware-progress.log',
        'abort' => $base . '/bkb-firmware-abort',
        'applied' => $base . '/bkb-firmware-result-applied',
        'recovery' => '/var/lib/blakestream-firstboot/firmware-recovery-required',
    );
}
function firmware_env_read($path) {
    $out = array();
    $raw = @file($path, FILE_IGNORE_NEW_LINES);
    if (!is_array($raw)) return $out;
    foreach ($raw as $line) {
        $pos = strpos($line, '=');
        if ($pos === false) continue;
        $key = trim(substr($line, 0, $pos));
        if ($key === '') continue;
        $out[$key] = substr($line, $pos + 1);
    }
    return $out;
}
function firmware_pid_running($pid) {
    $pid = (int) $pid;
    return ($pid > 1 && is_dir('/proc/' . $pid));
}
function firmware_job_cleanup_stale() {
    $p = firmware_job_paths();
    $pid = 0;
    if (file_exists($p['pid'])) $pid = (int) trim((string) @file_get_contents($p['pid']));
    if (firmware_pid_running($pid)) return;
    if (file_exists($p['lock'])) {
        $age = time() - (int) @filemtime($p['lock']);
        if ($age > 1200) {
            @unlink($p['lock']);
            @unlink($p['pid']);
            @unlink($p['meta']);
            @unlink($p['phase']);
            @unlink($p['abort']);
        }
    }
}
function firmware_progress_text($limit = 8192) {
    $p = firmware_job_paths();
    $txt = @file_get_contents($p['progress']);
    if ($txt === false) $txt = '';
    if (strlen($txt) > $limit) $txt = "…\n" . substr($txt, -$limit);
    return $txt;
}
function firmware_job_result() {
    $p = firmware_job_paths();
    $res = firmware_env_read($p['result']);
    if (!count($res)) return null;
    $res['ok'] = (isset($res['ok']) && (string) $res['ok'] === '1');
    $res['exit_code'] = isset($res['exit_code']) ? (int) $res['exit_code'] : 0;
    $res['elapsed'] = isset($res['elapsed']) ? (int) $res['elapsed'] : 0;
    $res['started_at'] = isset($res['started_at']) ? (int) $res['started_at'] : 0;
    $res['finished_at'] = isset($res['finished_at']) ? (int) $res['finished_at'] : 0;
    if (isset($res['chip_md5']) && $res['chip_md5'] !== '') {
        $res['identity'] = firmware_identity_for_md5($res['chip_md5']);
    }
    return $res;
}
function firmware_job_apply_result($SCRIPTA_CONF) {
    $p = firmware_job_paths();
    $res = firmware_job_result();
    $applied = @file_get_contents($p['applied']);
    if (!is_array($res) || (is_string($applied) && trim($applied) !== '')) return $res;
    $op = isset($res['op']) ? $res['op'] : '';
    if (empty($res['ok'])) {
        if ($op !== 'revert' && $op !== 'flash-custom') return $res;
        // A verified write can be followed by a failed restart. Fail closed
        // without treating the failed job as a new timing or identity sample.
        $disabled = scripta_conf_set_bool($SCRIPTA_CONF, 'userAdjustableClocks', false);
        $cleared = bkb_clear_experimental_options('/opt/scripta/etc/miner.options.json');
        firmware_audit('firmware-' . $op . ' failed; adjustable clocks OFF; experimental reset ' . ($cleared ? 'OK' : 'failed'));
        if ($disabled && $cleared) {
            @file_put_contents($p['applied'], date('c') . ' ' . $op . " failed\n");
            @chmod($p['applied'], 0660);
        }
        return $res;
    }
    if ($op === 'verify' && !empty($res['chip_md5'])) {
        firmware_record_flash_runtime('verify', isset($res['elapsed']) ? $res['elapsed'] : 0);
        $identity = firmware_identity_for_md5($res['chip_md5']);
        firmware_write_identity(array('chip_md5' => $res['chip_md5'], 'identity' => $identity, 'at' => time()));
        if (!bkb_firmware_supports_clocks(array('identity' => $identity, 'chip_md5' => $res['chip_md5']))) {
            scripta_conf_set_bool($SCRIPTA_CONF, 'userAdjustableClocks', false);
            if (!bkb_clear_experimental_options('/opt/scripta/etc/miner.options.json'))
                firmware_audit('experimental Index option reset failed; adjustable clocks OFF');
        }
        firmware_audit('firmware-verify result ' . $identity . ' ' . $res['chip_md5']);
    } elseif ($op === 'flash-custom') {
        firmware_record_flash_runtime('custom-v1', isset($res['elapsed']) ? $res['elapsed'] : 0);
        firmware_write_identity(array('chip_md5' => '18babe0c4c67d9146e230f9dc423f2f2', 'identity' => 'CUSTOM V1', 'at' => time()));
        $clocks_cleared = bkb_clear_experimental_options('/opt/scripta/etc/miner.options.json');
        scripta_conf_set_bool($SCRIPTA_CONF, 'userAdjustableClocks', $clocks_cleared);
        firmware_audit('firmware-flash-custom SUCCESS');
        firmware_audit($clocks_cleared ? 'adjustable-clocks auto-ON after custom flash; experimental Index cleared'
            : 'experimental Index option reset failed; adjustable clocks OFF');
    } elseif ($op === 'revert') {
        firmware_record_flash_runtime('stock', isset($res['elapsed']) ? $res['elapsed'] : 0);
        firmware_write_identity(array('chip_md5' => 'd5f844be2952fd713717a71a67e2be61', 'identity' => 'STOCK', 'at' => time()));
        scripta_conf_set_bool($SCRIPTA_CONF, 'userAdjustableClocks', false);
        if (!bkb_clear_experimental_options('/opt/scripta/etc/miner.options.json'))
            firmware_audit('experimental Index option reset failed; adjustable clocks OFF');
        firmware_audit('firmware-revert-to-stock SUCCESS');
        firmware_audit('adjustable-clocks auto-OFF after stock revert');
    }
    @file_put_contents($p['applied'], date('c') . ' ' . $op . "\n");
    @chmod($p['applied'], 0660);
    return firmware_job_result();
}
function firmware_job_state($SCRIPTA_CONF) {
    firmware_job_cleanup_stale();
    $p = firmware_job_paths();
    $meta = firmware_env_read($p['meta']);
    $pid = file_exists($p['pid']) ? (int) trim((string) @file_get_contents($p['pid'])) : 0;
    $running = firmware_pid_running($pid);
    $phase = trim((string) @file_get_contents($p['phase']));
    $op = isset($meta['op']) ? $meta['op'] : '';
    if ($phase === '') $phase = $running ? 'starting' : 'done';
    $result = null;
    if (!$running) $result = firmware_job_apply_result($SCRIPTA_CONF);
    $abort_requested = file_exists($p['abort']);
    $cancelable = ($running && $op === 'verify' && !$abort_requested);
    return array(
        'ok' => true,
        'running' => $running,
        'op' => $op,
        'phase' => $phase,
        'pid' => $pid,
        'started_at' => isset($meta['started_at']) ? (int) $meta['started_at'] : 0,
        'elapsed' => isset($meta['started_at']) ? max(0, time() - (int) $meta['started_at']) : 0,
        'runtime' => firmware_flash_runtime($op),
        'cancelable' => $cancelable,
        'abort_requested' => $abort_requested,
        'recovery_required' => file_exists($p['recovery']),
        'result' => $result,
        'progress' => firmware_progress_text(),
    );
}
function firmware_wrapper_response($raw, $error) {
    $text = trim((string) $raw);
    $res = json_decode($text, true);
    if (!is_array($res)) {
        // sudo can prepend a non-fatal hostname warning before the wrapper's
        // JSON. Recover the final valid JSON line for start and abort responses.
        $lines = preg_split('/\r?\n/', $text);
        for ($i = count($lines) - 1; $i >= 0; $i--) {
            $candidate = json_decode(trim($lines[$i]), true);
            if (is_array($candidate)) {
                $res = $candidate;
                break;
            }
        }
    }
    if (!is_array($res)) {
        return array('ok' => false, 'error' => $error, 'raw' => $raw);
    }
    return $res;
}
function firmware_job_start($op) {
    $wrapper = '/opt/scripta/bin/bkb-firmware.sh';
    if (!file_exists($wrapper)) return array('ok' => false, 'error' => 'wrapper missing');
    $cmd = '/usr/bin/sudo -n ' . escapeshellarg($wrapper) . ' start-job ' . escapeshellarg($op) . ' 2>&1';
    return firmware_wrapper_response(shell_exec($cmd), 'firmware job failed to start');
}
function scripta_conf_set_bool($path, $key, $bool) {
    // Atomically flip one boolean key in scripta.conf, reusing storage_set_target's
    // www-data-writable tempnam -> rename -> chmod 0664 pattern. Returns true on a
    // verified write. Used to keep the Adjustable Clocks setting in step with the
    // firmware actually flashed (revert -> OFF, flash-custom -> ON).
    $cfg = @json_decode(@file_get_contents($path), true);
    if (!is_array($cfg)) $cfg = array();
    $cfg[$key] = (bool) $bool;
    $dir = dirname($path);
    $tmp = @tempnam($dir, 'scripta.conf.tmp.');
    if ($tmp === false) return false;
    if (@file_put_contents($tmp, json_encode($cfg, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)) === false) {
        @unlink($tmp); return false;
    }
    @chmod($tmp, 0664);
    if (!@rename($tmp, $path)) { @unlink($tmp); return false; }
    @chmod($path, 0664);
    return true;
}

// ---- Boards: live per-board health (sgminer devs + live fan) ----------------
function sgminer_api_exchange($command, $parameter = '') {
    $payload = array('command' => $command);
    if ($parameter !== '') $payload['parameter'] = $parameter;
    $errno = 0; $errstr = '';
    $sock = @fsockopen('127.0.0.1', 4028, $errno, $errstr, 3);
    if (!$sock) return null;
    stream_set_timeout($sock, 4);
    fwrite($sock, json_encode($payload));
    @stream_socket_shutdown($sock, STREAM_SHUT_WR);
    $resp = '';
    while (!feof($sock)) {
        $chunk = fread($sock, 8192);
        if ($chunk === '' || $chunk === false) break;
        $resp .= $chunk;
    }
    fclose($sock);
    return $resp;
}

function sgminer_api_call($command, $parameter = '') {
    $resp = sgminer_api_exchange($command, $parameter);
    if ($resp === null) return null;
    return json_decode(trim($resp, "\x00"), true);
}

function board_health_rows() {
    $devs = sgminer_api_call('devs');
    $rows = array();
    if (!is_array($devs) || !isset($devs['DEVS']) || !is_array($devs['DEVS'])) return $rows;
    $fanState = @json_decode(@file_get_contents('/run/blakestream-control/fan-control-state.json'), true);
    $fan = (is_array($fanState) && isset($fanState['fan'])) ? (int) $fanState['fan'] : null;
    $now = time();
    foreach ($devs['DEVS'] as $d) {
        if (!isset($d['ASC'])) continue;
        $ls = isset($d['Last Share Time']) ? (int) $d['Last Share Time'] : 0;
        $rows[] = array(
            'id' => (int) $d['ASC'],
            'enabled' => (isset($d['Enabled']) && $d['Enabled'] === 'Y'),
            'status' => isset($d['Status']) ? $d['Status'] : '',
            'temp' => isset($d['Temperature']) ? (float) $d['Temperature'] : 0,
            'mhs5s' => isset($d['MHS 5s']) ? (float) $d['MHS 5s'] : 0,
            'mhsav' => isset($d['MHS av']) ? (float) $d['MHS av'] : 0,
            'accepted' => isset($d['Accepted']) ? (int) $d['Accepted'] : 0,
            'rejected' => isset($d['Rejected']) ? (int) $d['Rejected'] : 0,
            'hw' => isset($d['Hardware Errors']) ? (int) $d['Hardware Errors'] : 0,
            'last_share_age' => $ls ? max(0, $now - $ls) : -1,
            'fan' => $fan,
        );
    }
    return $rows;
}

// Mask pool worker logins/passwords before a config goes into a shared bundle.
function redact_creds_recursive($data) {
    if (!is_array($data)) return $data;
    foreach ($data as $k => $v) {
        if (is_string($k) && is_string($v) && preg_match('/^(pass|password|pwd|user|username|worker)$/i', $k)) {
            $data[$k] = '[REDACTED]';
        } elseif (is_string($v)) {
            $data[$k] = preg_replace('#://[^/@\s]+:[^/@\s]+@#', '://[REDACTED]@', $v);
        } else {
            $data[$k] = redact_creds_recursive($v);
        }
    }
    return $data;
}
function redact_json_string($path) {
    $raw = @file_get_contents($path);
    if ($raw === false) return null;
    $d = json_decode($raw, true);
    if (!is_array($d)) {
        return preg_replace('#://[^/@\s]+:[^/@\s]+@#', '://[REDACTED]@', (string) $raw);
    }
    return json_encode(redact_creds_recursive($d), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
}

function recorder_mtime_text($mtime) {
    return $mtime ? date('Y-m-d H:i:s', $mtime) : 'missing';
}

function recorder_clean_pool_url($url) {
    $url = (string) $url;
    $url = preg_replace('#^https?://#', '', $url);
    return preg_replace('/^quota:\d+;/', '', $url);
}

function recorder_latest_sample($samples) {
    if (!is_array($samples) || !count($samples)) return null;
    return $samples[count($samples) - 1];
}

function recorder_preview_hashrate($data) {
    $boards = (isset($data['boards']) && is_array($data['boards'])) ? $data['boards'] : array();
    $lines = array(
        'Updated: ' . recorder_mtime_text(isset($data['updated']) ? (int) $data['updated'] : 0),
        'Boards: ' . count($boards),
    );
    $shown = 0;
    foreach ($boards as $b) {
        if ($shown >= 6) break;
        $sample = recorder_latest_sample(isset($b['samples']) ? $b['samples'] : array());
        if (!$sample) continue;
        $ts = isset($sample[0]) ? (int) $sample[0] : 0;
        $mhs = isset($sample[1]) ? (float) $sample[1] : 0.0;
        $ghs = $mhs / 1000.0;
        $enabled = !empty($b['enabled']) ? 'enabled' : 'disabled';
        $pool = recorder_clean_pool_url(isset($b['pool_url']) ? $b['pool_url'] : '');
        $lines[] = sprintf(
            'Board %s: %.2f GH/s, %s, pool %s, sample %s',
            isset($b['id']) ? $b['id'] : '?',
            $ghs,
            $enabled,
            $pool ?: '-',
            recorder_mtime_text($ts)
        );
        $shown++;
    }
    if (!$shown) $lines[] = 'No board samples present.';
    return $lines;
}

function recorder_preview_pool($data) {
    $pools = (isset($data['pools']) && is_array($data['pools'])) ? $data['pools'] : array();
    $lines = array(
        'Updated: ' . recorder_mtime_text(isset($data['updated']) ? (int) $data['updated'] : 0),
        'Pools: ' . count($pools),
    );
    $shown = 0;
    foreach ($pools as $pool_id => $p) {
        if ($shown >= 6) break;
        $sample = recorder_latest_sample(isset($p['samples']) ? $p['samples'] : array());
        if (!$sample) continue;
        $lines[] = sprintf(
            '%s: %s, Acc %s, Rej %s, DiffAcc %s, DiffRej %s, Best %s, sample %s',
            isset($p['name']) ? $p['name'] : $pool_id,
            isset($sample['st']) ? $sample['st'] : '-',
            isset($sample['a']) ? $sample['a'] : 0,
            isset($sample['r']) ? $sample['r'] : 0,
            isset($sample['da']) ? $sample['da'] : 0,
            isset($sample['dr']) ? $sample['dr'] : 0,
            isset($sample['best']) ? $sample['best'] : 0,
            recorder_mtime_text(isset($sample['t']) ? (int) $sample['t'] : 0)
        );
        $shown++;
    }
    if (!$shown) $lines[] = 'No pool samples present.';
    return $lines;
}

function recorder_preview_failover($data) {
    $open = (isset($data['open']) && is_array($data['open'])) ? $data['open'] : array();
    $events = (isset($data['events']) && is_array($data['events'])) ? $data['events'] : array();
    $lines = array(
        'Updated: ' . recorder_mtime_text(isset($data['updated']) ? (int) $data['updated'] : 0),
        'Open failovers: ' . count($open),
        'Completed events: ' . count($events),
    );
    $shown = 0;
    foreach ($open as $evt) {
        if ($shown >= 3) break;
        $start = isset($evt['start_ts']) ? (int) $evt['start_ts'] : 0;
        $lines[] = sprintf(
            'OPEN board %s: %s -> %s, age %ss',
            isset($evt['board_id']) ? $evt['board_id'] : '?',
            isset($evt['primary_pool_name']) && $evt['primary_pool_name'] ? $evt['primary_pool_name'] : recorder_clean_pool_url(isset($evt['primary_pool_url']) ? $evt['primary_pool_url'] : ''),
            isset($evt['effective_pool_name']) && $evt['effective_pool_name'] ? $evt['effective_pool_name'] : recorder_clean_pool_url(isset($evt['effective_pool_url']) ? $evt['effective_pool_url'] : ''),
            $start ? max(0, time() - $start) : 0
        );
        $shown++;
    }
    if (!$shown && count($events)) {
        $slice = array_slice($events, -3);
        foreach ($slice as $evt) {
            $lines[] = sprintf(
                'RECENT board %s: %s -> %s, duration %ss, ended %s',
                isset($evt['board_id']) ? $evt['board_id'] : '?',
                isset($evt['primary_pool_name']) && $evt['primary_pool_name'] ? $evt['primary_pool_name'] : recorder_clean_pool_url(isset($evt['primary_pool_url']) ? $evt['primary_pool_url'] : ''),
                isset($evt['effective_pool_name']) && $evt['effective_pool_name'] ? $evt['effective_pool_name'] : recorder_clean_pool_url(isset($evt['effective_pool_url']) ? $evt['effective_pool_url'] : ''),
                isset($evt['duration_seconds']) ? $evt['duration_seconds'] : 0,
                recorder_mtime_text(isset($evt['end_ts']) ? (int) $evt['end_ts'] : 0)
            );
        }
    } elseif (!$shown) {
        $lines[] = 'No failover events recorded.';
    }
    return $lines;
}

function recorder_preview_clock($data) {
    $boards = (isset($data['boards']) && is_array($data['boards'])) ? $data['boards'] : array();
    $lines = array(
        'Updated: ' . recorder_mtime_text(isset($data['updated']) ? (int) $data['updated'] : 0),
        'Boards: ' . count($boards),
    );
    $shown = 0;
    foreach ($boards as $b) {
        if ($shown >= 6) break;
        $sample = recorder_latest_sample(isset($b['samples']) ? $b['samples'] : array());
        if (!$sample) continue;
        $requested = isset($sample['rc']) ? $sample['rc'] : (isset($sample['cc']) ? $sample['cc'] : '?');
        $reported = isset($sample['c']) ? $sample['c'] : '?';
        $reported_code = isset($sample['fcc']) ? $sample['fcc'] : '?';
        $lines[] = sprintf(
            'Board %s: requested %s MHz, reported %s MHz (code %s), temp %.1f C, fan %s%%, 5s %.2f GH/s, rejected %s, HW %s, sample %s',
            isset($b['id']) ? $b['id'] : '?',
            $requested,
            $reported,
            $reported_code,
            isset($sample['tmp']) ? (float) $sample['tmp'] : 0.0,
            isset($sample['fan']) ? $sample['fan'] : 0,
            isset($sample['m5']) ? ((float) $sample['m5'] / 1000.0) : 0.0,
            isset($sample['r']) ? $sample['r'] : 0,
            isset($sample['hw']) ? $sample['hw'] : 0,
            recorder_mtime_text(isset($sample['t']) ? (int) $sample['t'] : 0)
        );
        $shown++;
    }
    if (!$shown) $lines[] = 'No clock samples present.';
    return $lines;
}

function recorder_data_preview($recorder_id, $path) {
    if (!file_exists($path)) return array('Latest data written:', 'Output file is missing.');
    $data = read_json($path, null);
    if (!is_array($data)) return array('Latest data written:', 'Output file is not valid JSON.');

    if ($recorder_id === 'hashrate') {
        $preview = recorder_preview_hashrate($data);
    } elseif ($recorder_id === 'pool') {
        $preview = recorder_preview_pool($data);
    } elseif ($recorder_id === 'failover') {
        $preview = recorder_preview_failover($data);
    } elseif ($recorder_id === 'clock') {
        $preview = recorder_preview_clock($data);
    } else {
        $preview = array('No preview available for recorder ' . $recorder_id . '.');
    }

    array_unshift($preview, 'Latest data written:');
    return $preview;
}

function recorder_run_summary($spec, $before, $after, $exit, $raw_output) {
    $changed = ($after['mtime'] != $before['mtime']) || ($after['size'] != $before['size']);
    $lines = array();
    $lines[] = 'Recorder: ' . $spec['name'];
    $lines[] = 'Script: ' . $spec['script'];
    $lines[] = 'Output file: ' . $after['path'];
    $lines[] = 'Before: ' . recorder_mtime_text($before['mtime']) . ' (' . $before['size'] . ' bytes)';
    $lines[] = 'After: ' . recorder_mtime_text($after['mtime']) . ' (' . $after['size'] . ' bytes)';
    $lines[] = 'Change: ' . ($changed ? 'file updated' : 'no file change');
    $lines[] = '';
    $lines = array_merge($lines, recorder_data_preview($spec['id'], $after['path']));
    if (trim($raw_output) !== '') {
        $lines[] = '';
        $lines[] = trim($raw_output);
    } elseif (!$changed && $exit === 0) {
        $lines[] = '';
        $lines[] = 'No stdout/stderr. Exit 0 means the recorder ran cleanly; no file change usually means there was no new source data to append.';
    }
    return implode("\n", $lines);
}

function source_log_path($source, $cfg, $target = '') {
    $diagnostic = bs_diagnostic_source_path($source, $cfg, $target);
    if ($diagnostic !== null) return $diagnostic;
    switch ($source) {
        case 'sgminer':         return sgminer_log_path($cfg, $target);
        case 'lighttpd':        return lighttpd_log_path('error');
        case 'lighttpd_access': return lighttpd_log_path('access');
        case 'temp_state':      return temp_state_path();
        default: return null;   // journal-based sources resolve via source_journal_spec
    }
}

function source_journal_spec($source) {
    switch ($source) {
        case 'syslog':   return array('label' => 'systemd journal (all)',     'args' => '');
        case 'kern':     return array('label' => 'kernel ring buffer (-k)',    'args' => '-k');
        case 'daemon':   return array('label' => 'SYSLOG_FACILITY=3 (daemon)', 'args' => 'SYSLOG_FACILITY=3');
        case 'auth':     return array('label' => 'SYSLOG_FACILITY=4,10 (auth)','args' => 'SYSLOG_FACILITY=4 + SYSLOG_FACILITY=10');
        case 'ssh':      return array('label' => 'sshd auth/session log',      'args' => '-t sshd');
        case 'recorder': return array('label' => 'bs-* recorders',
                                      'args' => '-t bs-hashrate -t bs-pool -t bs-failover -t bs-temp -t bs-miner -t bs-history');
        default:         return null;
    }
}

function safe_source_filename($source) {
    $name = preg_replace('/[^A-Za-z0-9_.-]+/', '-', (string)$source);
    return $name ? $name : 'log';
}

function journalctl_command($args, $lines) {
    $lines = max(1, (int)$lines);
    return journalctl_command_since($args, $lines, 0);
}

function journalctl_command_since($args, $lines, $since) {
    $lines = max(1, (int)$lines);
    $since_arg = ((int)$since > 0) ? '--since ' . escapeshellarg('@' . (int)$since) . ' ' : '';
    return '/bin/journalctl --no-pager -q --output=short ' . $since_arg . $args . ' -n ' . $lines . ' 2>&1';
}

function request_value($key, $default = '') {
    static $body = null;
    if ($body === null) {
        $raw = @file_get_contents('php://input', false, null, 0, 4097);
        $body = @json_decode($raw, true);
        if (!is_array($body)) $body = array();
    }
    return isset($body[$key]) ? $body[$key] : $default;
}

function clearable_log_path($source, $cfg, $target = '') {
    $diagnostic = bs_diagnostic_source_path($source, $cfg, $target);
    if ($diagnostic !== null) return $diagnostic;
    if (strpos($source, 'path:') === 0) {
        $path = substr($source, 5);
        return in_array($path, storage_clearable_paths(), true) ? $path : null;
    }
    switch ($source) {
        case 'sgminer':         return sgminer_log_path($cfg, $target);
        case 'lighttpd':        return lighttpd_log_path('error');
        case 'lighttpd_access': return lighttpd_log_path('access');
        case 'temp_state':
            $path = temp_state_path();
            return in_array($path, storage_clearable_paths(), true) ? $path : null;
        default: return null;
    }
}

function dev_runtime_dir() {
    global $RUNTIME_DIR;
    if (!is_dir($RUNTIME_DIR)) {
        @mkdir($RUNTIME_DIR, 0775, true);
    }
    @chmod($RUNTIME_DIR, 0775);
    return $RUNTIME_DIR;
}

function log_clear_marker_key($source) {
    return preg_replace('/[^A-Za-z0-9_.-]+/', '-', (string)$source);
}

function read_log_clear_markers() {
    global $DEV_LOG_CLEAR_MARKERS;
    $d = read_json($DEV_LOG_CLEAR_MARKERS, array());
    return is_array($d) ? $d : array();
}

function write_log_clear_markers($markers) {
    global $DEV_LOG_CLEAR_MARKERS;
    dev_runtime_dir();
    $ok = @file_put_contents($DEV_LOG_CLEAR_MARKERS, json_encode($markers));
    if ($ok !== false) @chmod($DEV_LOG_CLEAR_MARKERS, 0664);
    return $ok !== false;
}

function journal_clear_marker($source) {
    $markers = read_log_clear_markers();
    $key = log_clear_marker_key($source);
    return isset($markers[$key]) ? max(0, (int)$markers[$key]) : 0;
}

function set_journal_clear_marker($source, $since = null) {
    $markers = read_log_clear_markers();
    $markers[log_clear_marker_key($source)] = ($since === null) ? time() + 1 : max(0, (int)$since);
    return write_log_clear_markers($markers);
}

function zip_add_file_if_readable($zip, $abs, $name) {
    if (file_exists($abs) && is_readable($abs)) {
        $zip->addFile($abs, $name);
        return true;
    }
    return false;
}

function zip_add_sgminer_target_logs($zip, $target, $base_name) {
    $paths = sgminer_log_paths_for_target($target);
    foreach ($paths as $path) {
        if (zip_add_file_if_readable($zip, $path, $base_name . '.log')) {
            if (file_exists($path . '.1')) zip_add_file_if_readable($zip, $path . '.1', $base_name . '.log.1');
            if (file_exists($path . '.1.gz')) zip_add_file_if_readable($zip, $path . '.1.gz', $base_name . '.log.1.gz');
            return $path;
        }
    }
    return '';
}

function hashrate_history_target($cfg) {
    $target = (is_array($cfg) && isset($cfg['userChartsSaveTo'])) ? $cfg['userChartsSaveTo'] : 'SD';
    return normalize_log_target($target);
}

function persist_history_target($cfg, $key) {
    $persist = (is_array($cfg) && array_key_exists($key, $cfg)) ? !empty($cfg[$key]) : false;
    return $persist ? 'SD' : 'RAM';
}

function history_path_for_target($filename, $target) {
    $base = (normalize_log_target($target) === 'RAM') ? '/run/blakestream' : '/opt/scripta/var';
    return $base . '/' . $filename;
}

function zip_add_history_target_file($zip, $filename, $target, $base_name) {
    $path = history_path_for_target($filename, $target);
    return zip_add_file_if_readable($zip, $path, $base_name . '.json') ? $path : '';
}

function zip_add_history_copies($zip, $filename, $current_target, $base_name) {
    $current_target = normalize_log_target($current_target);
    $current = zip_add_history_target_file(
        $zip,
        $filename,
        $current_target,
        $base_name . '-current-' . strtolower($current_target)
    );
    if ($current) {
        zip_add_file_if_readable($zip, $current, $base_name . '.json');
    }
    zip_add_history_target_file($zip, $filename, 'SD', $base_name . '-sd');
    zip_add_history_target_file($zip, $filename, 'RAM', $base_name . '-ram');
}

function clean_pool_url_parts($url) {
    return bs_probe_parse_endpoint($url);
}

function pool_list_for_probe($etc) {
    return bs_probe_endpoints_from_saved(read_json($etc . '/saved-pools.json'));
}

function probe_one_pool($p) {
    $host = $p['host'];
    $port = (int) $p['port'];
    // TCP-connect latency (privilege-free, reliable; the RTT that matters).
    $samples = array();
    $reachable = false;
    $errno = 0; $errstr = '';
    for ($i = 0; $i < 5; $i++) {
        $t0 = microtime(true);
        $sock = @fsockopen(bs_probe_connect_host($host), $port, $errno, $errstr, 3);
        if ($sock) { $samples[] = (microtime(true) - $t0) * 1000.0; $reachable = true; fclose($sock); }
        usleep(150000);
    }
    $latency = array('min' => 0, 'avg' => 0, 'max' => 0, 'jitter' => 0, 'count' => count($samples));
    if ($samples) {
        $latency['min'] = round(min($samples), 1);
        $latency['max'] = round(max($samples), 1);
        $avg = array_sum($samples) / count($samples);
        $latency['avg'] = round($avg, 1);
        $mad = 0.0; foreach ($samples as $s) { $mad += abs($s - $avg); }
        $latency['jitter'] = round($mad / count($samples), 1);
    }
    $tcp = $reachable ? array('ok' => true, 'error' => '')
                      : array('ok' => false, 'error' => $errstr ?: ('errno ' . $errno));
    // ICMP ping (best-effort; works now that /bin/ping is setuid).
    $icmp = array('ok' => false, 'blocked' => false, 'loss' => 100, 'avg' => 0);
    $ping = strpos($host, ':') !== false ? 'ping6' : 'ping';
    $ping_out = @shell_exec($ping . ' -c 4 -w 4 ' . escapeshellarg($host) . ' 2>/dev/null');
    if ($ping_out) {
        if (preg_match('/(\d+(?:\.\d+)?)% packet loss/', $ping_out, $m)) $icmp['loss'] = (float) $m[1];
        if (preg_match('#=\s*([\d\.]+)/([\d\.]+)/([\d\.]+)#', $ping_out, $m)) { $icmp['avg'] = (float) $m[2]; $icmp['ok'] = true; }
    }
    if (!$icmp['ok'] && $reachable) $icmp['blocked'] = true;
    return array('name' => $p['name'], 'names' => $p['names'], 'key' => $p['key'], 'host' => $host, 'port' => $port,
                 'tcp' => $tcp, 'latency' => $latency, 'icmp' => $icmp);
}

// ============================================================================
// Actions
// ============================================================================

header('Content-Type: application/json');

switch ($action) {

case 'log_tail':
    $source = isset($_GET['source']) ? $_GET['source'] : 'sgminer';
    $offset = isset($_GET['offset']) ? max(0, (int)$_GET['offset']) : 0;
    $target = isset($_GET['target']) ? $_GET['target'] : '';

    // Journal-backed sources: shell out to journalctl (BK-B has no /var/log/syslog
    // file — systemd-journald is the system log sink). www-data must be in the
    // systemd-journal group to read it; the overlay's etc/group seeds that.
    $jspec = source_journal_spec($source);
    if ($jspec) {
        $since = journal_clear_marker($source);
        $cmd = journalctl_command_since($jspec['args'], 500, $since);
        $out = @shell_exec($cmd);
        $lines = ($out === null) ? array() : array_values(array_filter(explode("\n", rtrim($out, "\n")), 'strlen'));
        $total = count($lines);
        if ($offset > 0) {
            $lines = ($offset < $total) ? array_slice($lines, $offset) : array();
        }
        echo json_encode(array(
            'ok' => true,
            'path' => 'journalctl ' . trim($jspec['args']) . '  (' . $jspec['label'] . ')',
            'lines' => $lines,
            'next_offset' => $total,
            'offset_unit' => 'lines',
            'cleared_since' => $since,
        ));
        exit;
    }

    $path = source_log_path($source, $cfg, $target);
    if (!$path) {
        echo json_encode(array('ok' => false, 'error' => 'unknown source'));
        exit;
    }

    if (!file_exists($path)) {
        echo json_encode(array(
            'ok' => true,
            'path' => $path,
            'lines' => array(),
            'next_offset' => 0,
            'note' => 'file not present yet',
        ));
        exit;
    }
    clearstatcache(true, $path);
    $size = filesize($path);
    if ($offset > $size) $offset = 0;   // file was rotated, restart
    $lines = array();
    $next = $offset;
    if ($offset === 0) {
        // First read: serve the last ~32 KiB so the user has context.
        $head = max(0, $size - 32 * 1024);
        $offset = $head;
    }
    $fp = @fopen($path, 'rb');
    if ($fp) {
        if ($offset > 0) fseek($fp, $offset);
        $chunk = stream_get_contents($fp);
        fclose($fp);
        if ($chunk !== false && $chunk !== '') {
            $raw_len = strlen($chunk);
            $chunk = normalize_log_text($source, $chunk);
            $lines = explode("\n", rtrim($chunk, "\n"));
            $next = $offset + $raw_len;
            if (count($lines) > 500) {
                $lines = array_slice($lines, count($lines) - 500);
            }
        } else {
            $next = $size;
        }
    }
    echo json_encode(array(
        'ok' => true,
        'path' => $path,
        'lines' => $lines,
        'next_offset' => $next,
    ));
    break;

case 'log_download':
    $source = isset($_GET['source']) ? $_GET['source'] : 'sgminer';
    $target = isset($_GET['target']) ? $_GET['target'] : '';

    $jspec = source_journal_spec($source);
    if ($jspec) {
        $out = @shell_exec(journalctl_command_since($jspec['args'], 5000, journal_clear_marker($source)));
        if ($out === null) $out = '';
        header('Content-Type: text/plain; charset=utf-8');
        header('Content-Disposition: attachment; filename="' . safe_source_filename($source) . '-' . date('Ymd-Hi') . '.log"');
        header('Content-Length: ' . strlen($out));
        echo $out;
        exit;
    }

    $path = source_log_path($source, $cfg, $target);
    if (!$path) {
        http_response_code(404);
        echo json_encode(array('ok' => false, 'error' => 'unknown source'));
        exit;
    }
    if (!file_exists($path)) {
        $out = "Log source is not present yet: " . $path . "\n";
        header('Content-Type: text/plain; charset=utf-8');
        header('Content-Disposition: attachment; filename="' . safe_source_filename($source) . '-' . date('Ymd-Hi') . '.log"');
        header('Content-Length: ' . strlen($out));
        echo $out;
        exit;
    }
    $suffix = ($source === 'sgminer') ? '-' . normalize_log_target($target ?: sgminer_log_target($cfg)) : '';
    header('Content-Type: text/plain; charset=utf-8');
    header('Content-Disposition: attachment; filename="' . $source . $suffix . '-' . date('Ymd-Hi') . '.log"');
    if ($source === 'sgminer') {
        $out = @file_get_contents($path);
        if ($out === false) $out = '';
        $out = normalize_log_text($source, $out);
        header('Content-Length: ' . strlen($out));
        echo $out;
    } else {
        header('Content-Length: ' . filesize($path));
        readfile($path);
    }
    exit;

case 'log_clear':
    $source = request_value('source', 'sgminer');
    $target = request_value('target', '');

    if (source_journal_spec($source)) {
        $jspec = source_journal_spec($source);
        if (!set_journal_clear_marker($source)) {
            http_response_code(500);
            echo json_encode(array(
                'ok' => false,
                'error' => 'failed to write journal clear marker',
            ));
            exit;
        }
        echo json_encode(array(
            'ok' => true,
            'path' => 'journalctl ' . trim($jspec['args']) . '  (' . $jspec['label'] . ')',
            'note' => 'Cleared selected source view',
            'cleared' => true,
        ));
        exit;
    }

    $path = clearable_log_path($source, $cfg, $target);
    if (!$path) {
        http_response_code(400);
        echo json_encode(array(
            'ok' => false,
            'error' => 'source is not a clearable log',
        ));
        exit;
    }

    if (!file_exists($path)) {
        echo json_encode(array(
            'ok' => true,
            'path' => $path,
            'note' => 'file not present yet',
        ));
        exit;
    }

    if (bs_diagnostic_clear($path) === false) {
        http_response_code(500);
        echo json_encode(array(
            'ok' => false,
            'path' => $path,
            'error' => 'failed to clear log',
        ));
        exit;
    }
    @chmod($path, 0664);
    echo json_encode(array(
        'ok' => true,
        'path' => $path,
        'cleared' => true,
    ));
    exit;

case 'network_pools':
    // Fast: just the pool list, so the UI can render rows before probing.
    echo json_encode(array('ok' => true, 'pools' => pool_list_for_probe($ETC)));
    break;

case 'network_probe':
    $pools = pool_list_for_probe($ETC);
    // A stable endpoint key cannot change meaning if saved profiles are reordered.
    $selector = isset($_GET['endpoint']) ? $_GET['endpoint'] : (isset($_GET['pool']) ? $_GET['pool'] : '');
    if ($selector !== '') {
        $pool = bs_probe_select($pools, $selector);
        if ($pool === null) { echo json_encode(array('ok' => false, 'error' => 'endpoint no longer available')); exit; }
        echo json_encode(array('ok' => true, 'index' => $pool['index'], 'result' => probe_one_pool($pool)));
        exit;
    }
    $results = array();
    foreach ($pools as $p) { $results[] = probe_one_pool($p); }
    echo json_encode(array('ok' => true, 'results' => $results));
    break;

case 'sgminer_api':
    $body = json_decode(file_get_contents('php://input'), true);
    $request = bs_dev_sgminer_api_request($body);
    if (!$request['ok']) {
        http_response_code(400);
        echo json_encode($request);
        exit;
    }
    $command = $request['command'];
    error_log('DevTools sgminer_api read: command=' . $command . ' remote=' . (isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : 'unknown'));
    $payload = array('command' => $command);
    $errno = 0; $errstr = '';
    $sock = @fsockopen('127.0.0.1', 4028, $errno, $errstr, 3);
    if (!$sock) {
        echo json_encode(array('ok' => false, 'error' => 'sgminer api: ' . $errstr));
        exit;
    }
    stream_set_timeout($sock, 4);
    fwrite($sock, json_encode($payload));
    @stream_socket_shutdown($sock, STREAM_SHUT_WR);
    $resp = '';
    while (!feof($sock)) {
        $chunk = fread($sock, 8192);
        if ($chunk === '' || $chunk === false) break;
        $resp .= $chunk;
    }
    fclose($sock);
    $resp = trim($resp, "\x00");
    $parsed = json_decode($resp, true);
    if ($resp === '' || $parsed === null) {
        echo json_encode(array(
            'ok' => false,
            'error' => ($resp === '') ? 'sgminer api timeout' : 'invalid sgminer api response',
            'response' => $resp,
        ));
        break;
    }
    echo json_encode(array(
        'ok' => true,
        'response' => $parsed,
    ));
    break;

case 'recorder_status':
    echo json_encode(array('ok' => true, 'recorders' => recorder_status_rows()));
    break;

case 'recorder_health':
    // Augment each recorder with health from its bs-* journal + a cron check, so
    // a silently-dead recorder (mtime stale, or last run errored) is obvious.
    $cron_up = (trim((string) @shell_exec('pgrep -x cron 2>/dev/null')) !== '');
    $hcfg = @json_decode(@file_get_contents($SCRIPTA_CONF), true);
    $adj_on = is_array($hcfg) && !empty($hcfg['userAdjustableClocks']);
    $rows = recorder_status_rows();
    $all_ok = true;
    foreach ($rows as $i => $r) {
        if ($r['id'] === 'diagnostics') {
            $state = read_json('/run/blakestream/diagnostic-log-state.json');
            $status = $r['age_sec'] < 0 || $r['age_sec'] > 90 ? 'stale' : (empty($state['ok']) ? 'error' : 'ok');
            $rows[$i]['health'] = $status;
            $rows[$i]['last_log'] = '';
            if ($status !== 'ok') $all_ok = false;
            continue;
        }
        $tag = recorder_journal_tag($r['id']);
        $last = ''; $status = 'unknown';
        if ($tag) {
            $out = (string) @shell_exec('/bin/journalctl --no-pager -q -t ' . escapeshellarg($tag) . ' -n 4 2>&1');
            $lines = array_values(array_filter(array_map('trim', explode("\n", $out))));
            $last = $lines ? $lines[count($lines) - 1] : '';
            $err = false;
            foreach ($lines as $l) { if (preg_match('/traceback|error|exception|failed/i', $l)) { $err = true; break; } }
            if ($err) $status = 'error';
            elseif ($r['id'] === 'clock' && !$adj_on) $status = 'idle';
            elseif ($r['age_sec'] >= 0 && $r['age_sec'] <= $r['cron_sec'] * 3) $status = 'ok';
            elseif ($r['id'] === 'pool' && $r['age_sec'] < 0) {
                $uptime = explode(' ', trim((string) @file_get_contents('/proc/uptime')));
                $miner_age = trim((string) @shell_exec('ps -C sgminer -o etimes= 2>/dev/null'));
                $status = recorder_pool_waiting($r['age_sec'], $r['cron_sec'], $cron_up,
                    $uptime[0], $miner_age) ? 'waiting' : 'stale';
            }
            else $status = 'stale';
        }
        $rows[$i]['health'] = $status;
        $rows[$i]['last_log'] = $last;
        if ($status === 'error' || $status === 'stale') $all_ok = false;
    }
    echo json_encode(array('ok' => true, 'cron_up' => $cron_up, 'all_ok' => $all_ok, 'recorders' => $rows));
    break;

case 'recorder_run':
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) $body = array();
    $which = isset($body['which']) ? $body['which'] : '';
    $specs = dev_recorder_specs();
    if (!isset($specs[$which])) {
        echo json_encode(array('ok' => false, 'error' => 'unknown recorder'));
        exit;
    }
    $spec = $specs[$which];
    $script = $spec['script'];
    if (!file_exists($script)) {
        echo json_encode(array('ok' => false, 'error' => 'script missing: ' . $script));
        exit;
    }
    $before = recorder_file_status($spec['paths']);
    $prepare = '/opt/scripta/bin/prepare-runtime-dir.sh';
    $prefix = file_exists($prepare) ? '/usr/bin/sudo -n ' . escapeshellarg($prepare) . ' 2>&1 && ' : '';
    $extra_arg = ($spec['id'] === 'clock') ? ' --force' : '';
    $cmd = $prefix . 'PYTHONDONTWRITEBYTECODE=1 /usr/bin/python ' . escapeshellarg($script) . $extra_arg . ' 2>&1';
    $output = '';
    $exit = -1;
    $proc = @proc_open($cmd, array(1 => array('pipe', 'w'), 2 => array('pipe', 'w')), $pipes);
    if (is_resource($proc)) {
        $output = stream_get_contents($pipes[1]);
        $err = stream_get_contents($pipes[2]);
        if ($err) $output .= $err;
        fclose($pipes[1]);
        fclose($pipes[2]);
        $exit = proc_close($proc);
    }
    $after = recorder_file_status($spec['paths']);
    $summary = recorder_run_summary($spec, $before, $after, $exit, $output);
    echo json_encode(array(
        'ok' => true,
        'output' => $summary,
        'raw_output' => $output,
        'exit' => $exit,
        'before' => $before,
        'after' => $after,
    ));
    break;

case 'state_snapshot':
    // One-shot, paste-safe text snapshot (pool URLs reduced to host:port — no creds).
    $L = array();
    $L[] = '=== BK-B state snapshot ' . date('Y-m-d H:i:s') . ' ===';
    $L[] = trim((string) @shell_exec('uname -a'));
    $L[] = 'uptime:' . rtrim((string) @shell_exec('uptime'));
    $L[] = 'mem: ' . trim((string) @shell_exec("free -m | awk 'NR==2{print \$3\"/\"\$2\" MiB used\"}'"));
    $L[] = 'disk /: ' . trim((string) @shell_exec("df -h / | awk 'NR==2{print \$3\"/\"\$2\" (\"\$5\")\"}'"));
    $fid = firmware_last_identity();
    $L[] = '';
    $L[] = 'bridge firmware: ' . ($fid ? ($fid['identity'] . ' (md5 ' . substr($fid['chip_md5'], 0, 12) . '…)') : 'unknown — not verified');
    $L[] = '';
    $L[] = 'boards:';
    foreach (board_health_rows() as $b) {
        $L[] = sprintf('  ASC%d %-3s temp=%d C fan=%s MHS5s=%s HW=%d acc=%d rej=%d',
            $b['id'], $b['enabled'] ? 'on' : 'OFF', $b['temp'], $b['fan'] === null ? '-' : $b['fan'] . '%',
            round($b['mhs5s']), $b['hw'], $b['accepted'], $b['rejected']);
    }
    if (!board_health_rows()) $L[] = '  (sgminer API not reachable)';
    $L[] = '';
    $L[] = 'recorders:';
    foreach (recorder_status_rows() as $r) {
        $L[] = sprintf('  %-9s %-26s %8d B  age=%ds', $r['id'], basename($r['path']), $r['size'], $r['age_sec']);
    }
    $L[] = '';
    $L[] = 'pools (host:port):';
    foreach (pool_list_for_probe($ETC) as $p) { $L[] = '  ' . $p['name'] . '  ' . $p['host'] . ':' . $p['port']; }
    $rep = storage_report();
    $L[] = '';
    if ($rep['ram_df']) $L[] = 'RAM tmpfs: ' . round($rep['ram_df']['used'] / 1048576) . '/' . round($rep['ram_df']['total'] / 1048576) . ' MiB (' . $rep['ram_df']['pct'] . '%), our files ' . round($rep['ram_total_bytes'] / 1024) . ' KiB';
    if ($rep['sd_df']) $L[] = 'SD card: ' . round($rep['sd_df']['used'] / 1073741824, 1) . '/' . round($rep['sd_df']['total'] / 1073741824, 1) . ' GiB (' . $rep['sd_df']['pct'] . '%), our files ' . round($rep['sd_total_bytes'] / 1024) . ' KiB';
    $L[] = '';
    $L[] = 'recent journal (restart/OOM/error):';
    $jt = trim((string) @shell_exec("/bin/journalctl --no-pager -q -n 500 2>&1 | grep -iE 'oom|killed|restart|panic|reset|i/o error' | tail -12"));
    $L[] = ($jt !== '') ? $jt : '  (none)';
    echo json_encode(array('ok' => true, 'text' => implode("\n", $L)));
    break;

case 'diagnostics_zip':
    $tmp = tempnam('/tmp', 'blakestream-diag-') . '.zip';
    $zip = dev_zip_create($tmp);
    if (!$zip) {
        http_response_code(500);
        echo json_encode(array('ok' => false, 'error' => 'zip support not available'));
        exit;
    }

    $add_file = function ($zip, $abs, $name) {
        zip_add_file_if_readable($zip, $abs, $name);
    };

    // Logs
    $current_target = sgminer_log_target($cfg);
    $current = zip_add_sgminer_target_logs($zip, $current_target, 'logs/sgminer-current-' . strtolower($current_target));
    if ($current) {
        $add_file($zip, $current, 'logs/sgminer.log');
    }
    zip_add_sgminer_target_logs($zip, 'SD', 'logs/sgminer-sd');
    zip_add_sgminer_target_logs($zip, 'RAM', 'logs/sgminer-ram');
    foreach (bs_diagnostic_logs() as $name => $spec) {
        foreach (array('RAM', 'SD') as $side) {
            $add_file($zip, bs_diagnostic_path($name, $side), 'logs/' . strtolower($side) . '/' . $name);
        }
    }
    $add_file($zip, lighttpd_log_path('access'), 'logs/lighttpd-access.log');

    // Configs. Pool-bearing files are credential-redacted before bundling (this
    // zip is shared for support) — host/port/name are kept for triage.
    $sp = redact_json_string($ETC . '/saved-pools.json');
    if ($sp !== null) $zip->addFromString('config/saved-pools.json', $sp);
    $rm = redact_json_string('/run/blakestream-miner/scripta-runtime-miner.conf');
    if ($rm !== null) $zip->addFromString('config/runtime-miner.conf', $rm);
    $pm = redact_json_string('/run/blakestream-miner/scripta-runtime-poolmap.json');
    if ($pm !== null) $zip->addFromString('config/runtime-poolmap.json', $pm);
    $add_file($zip, $ETC . '/board-assignments.json', 'config/board-assignments.json');
    $add_file($zip, $ETC . '/failover-config.json', 'config/failover-config.json');
    $add_file($zip, $ETC . '/miner.options.json', 'config/miner.options.json');
    $add_file($zip, $ETC . '/clock-profiles.json', 'config/clock-profiles.json');

    // Redacted scripta.conf (strip alertSmtpPwd, uipasswd)
    $sc_redacted = $cfg;
    if (is_array($sc_redacted)) {
        if (isset($sc_redacted['alertSmtpPwd'])) $sc_redacted['alertSmtpPwd'] = '[REDACTED]';
    }
    $zip->addFromString('config/scripta.conf', json_encode($sc_redacted, JSON_PRETTY_PRINT));

    // Live data. Include the active target under the familiar file name, plus
    // explicit SD/RAM copies so RAM history is exported even when stale SD
    // history from a prior setting also exists.
    zip_add_history_copies($zip, 'hashrate-history.json', hashrate_history_target($cfg), 'data/hashrate-history');
    zip_add_history_copies($zip, 'clock-history.json', persist_history_target($cfg, 'userPersistClockHistory'), 'data/clock-history');
    zip_add_file_if_readable($zip, '/run/blakestream/clock-history-sd-buffer.json', 'data/clock-history-sd-buffer.json');
    $add_file($zip, '/run/blakestream/accepted-work-rates.json', 'data/accepted-work-rates.json');
    zip_add_history_copies($zip, 'pool-history.json', persist_history_target($cfg, 'userPersistPoolHistory'), 'data/pool-history');
    zip_add_history_copies($zip, 'failover-events.json', persist_history_target($cfg, 'userPersistFailoverHistory'), 'data/failover-events');

    // Syslog bs-* tail. BK-B uses journald, not /var/log/syslog.
    $rec_spec = source_journal_spec('recorder');
    $bs_log = @shell_exec(journalctl_command_since($rec_spec['args'], 2000, journal_clear_marker('recorder')));
    $zip->addFromString('logs/syslog-bs.txt', $bs_log === null ? '' : $bs_log);

    // System info
    $sysinfo = "uname -a:\n" . shell_exec('uname -a') . "\n"
             . "uptime:\n" . shell_exec('uptime') . "\n"
             . "free -h:\n" . shell_exec('free -h') . "\n"
             . "df -h:\n" . shell_exec('df -h /') . "\n"
             . "ip a:\n" . shell_exec('ip a') . "\n"
             . "ip route:\n" . shell_exec('ip route');
    $zip->addFromString('system/sysinfo.txt', $sysinfo);

    $versions = "sgminer:\n" . shell_exec('/opt/scripta/bin/sgminer --version 2>&1 | head -3') . "\n"
              . "screen:\n" . shell_exec('screen --version 2>&1') . "\n"
              . "lighttpd:\n" . shell_exec('lighttpd -v 2>&1') . "\n"
              . "php:\n" . shell_exec('php -v 2>&1');
    $zip->addFromString('system/versions.txt', $versions);

    // Failure-mode logs + live state (what you actually need when the box is sick:
    // SD I/O errors, OOM on 192 MB, DFU/USB resets, intrusions).
    if (!bs_web_auth_scrub_log('/opt/scripta/var/security.log')) {
        $zip->close();
        @unlink($tmp);
        http_response_code(500);
        echo json_encode(array('ok' => false, 'error' => 'security log redaction failed'));
        exit;
    }
    $add_file($zip, '/opt/scripta/var/security.log', 'logs/security.log');
    $add_file($zip, '/var/log/blakestream-security/ssh.log', 'logs/ssh-auth.log');
    $zip->addFromString('logs/kernel-ring.txt', (string) @shell_exec(journalctl_command('-k', 1500)));
    $zip->addFromString('logs/journal-tail.txt', (string) @shell_exec(journalctl_command('', 2000)));
    $zip->addFromString('system/firmware-identity.json', json_encode(firmware_last_identity(), JSON_PRETTY_PRINT));
    $zip->addFromString('system/board-health.json', json_encode(board_health_rows(), JSON_PRETTY_PRINT));
    $add_file($zip, temp_state_path(), 'data/temp-state.json');

    if (!$zip->close()) {
        @unlink($tmp);
        http_response_code(500);
        echo json_encode(array('ok' => false, 'error' => 'zip close failed'));
        exit;
    }

    $fname = 'blakestream-diag-' . date('Ymd-Hi') . '.zip';
    header('Content-Type: application/zip');
    header('Content-Disposition: attachment; filename="' . $fname . '"');
    header('Content-Length: ' . filesize($tmp));
    readfile($tmp);
    @unlink($tmp);
    exit;

case 'board_health':
    $rows = board_health_rows();
    echo json_encode(array('ok' => (count($rows) > 0), 'boards' => $rows, 'generated_at' => time()));
    break;

case 'storage':
    echo json_encode(storage_report());
    break;

case 'measured_rates':
    $data = bs_measured_read();
    $data['csrf'] = $dev_activity_csrf;
    echo json_encode($data);
    break;

case 'measured_rates_download':
    $format = isset($_GET['format']) ? $_GET['format'] : 'json';
    if (!in_array($format, array('json', 'readable'), true)) {
        http_response_code(400);
        echo json_encode(array('ok' => false, 'error' => 'Invalid report format'));
        break;
    }
    $board = isset($_GET['board']) ? $_GET['board'] : 'all';
    $data = bs_measured_filter(bs_measured_read(), $board);
    if ($data === null) {
        http_response_code(400);
        echo json_encode(array('ok' => false, 'error' => 'Invalid board filter'));
        break;
    }
    $suffix = $board === 'all' ? '' : '-board-' . $board;
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    if ($format === 'readable') {
        header('Content-Type: text/plain; charset=utf-8');
        header('Content-Disposition: attachment; filename="measured-rates' . $suffix . '.txt"');
        $timezone = empty($cfg['userShowSystemTime']) && isset($cfg['userTimezone']) ? $cfg['userTimezone'] : 'UTC';
        echo bs_measured_report($data, $timezone);
    } else {
        header('Content-Type: application/json');
        header('Content-Disposition: attachment; filename="measured-rates' . $suffix . '.json"');
        echo json_encode($data);
    }
    break;

case 'measured_rates_update':
    $body = json_decode(file_get_contents('php://input'), true);
    if (!bs_measured_authorized(isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '', $body, $dev_activity_csrf)) {
        http_response_code(403);
        echo json_encode(array('ok' => false, 'error' => 'Invalid request token'));
        break;
    }
    $op = isset($body['op']) ? $body['op'] : '';
    if (!in_array($op, array('target', 'clear', 'reset-board', 'flush'), true)) {
        echo json_encode(array('ok' => false, 'error' => 'Invalid measured-rate operation'));
        break;
    }
    echo json_encode(bs_measured_command($op, isset($body['argument']) ? $body['argument'] : ''));
    break;

case 'storage_set_target':
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) $body = array();
    $file = isset($body['file']) ? $body['file'] : '';
    $diagnostic_specs = bs_diagnostic_logs();
    if (is_string($file) && isset($diagnostic_specs[$file])) {
        if (!bs_measured_authorized(isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '', $body, $dev_activity_csrf)) {
            http_response_code(403);
            echo json_encode(array('ok' => false, 'error' => 'Invalid request token'));
        } else {
            echo json_encode(bs_diagnostic_set_target($file, isset($body['target']) ? $body['target'] : '', $SCRIPTA_CONF));
        }
        break;
    }
    if ($file === 'measured-rates') {
        if (!bs_measured_authorized(isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '', $body, $dev_activity_csrf)) {
            http_response_code(403);
            echo json_encode(array('ok' => false, 'error' => 'Invalid request token'));
        } else {
            echo json_encode(bs_measured_command('target', isset($body['target']) ? $body['target'] : ''));
        }
        break;
    }
    $target = (strtoupper(isset($body['target']) ? $body['target'] : '') === 'SD') ? 'SD' : 'RAM';
    $map = array(
        'hashrate-history.json' => array('userChartsSaveTo', 'enum'),
        'sgminer.log'           => array('userDevLogTarget', 'enum'),
        'pool-history.json'     => array('userPersistPoolHistory', 'bool'),
        'failover-events.json'  => array('userPersistFailoverHistory', 'bool'),
        'clock-history.json'    => array('userPersistClockHistory', 'bool'),
    );
    if (!isset($map[$file])) { echo json_encode(array('ok' => false, 'error' => 'file not configurable')); exit; }
    list($key, $type) = $map[$file];
    $cfg = @json_decode(@file_get_contents($SCRIPTA_CONF), true);
    if (!is_array($cfg)) $cfg = array();
    $cfg[$key] = ($type === 'enum') ? $target : ($target === 'SD');
    $dir = dirname($SCRIPTA_CONF);
    $tmp = @tempnam($dir, 'scripta.conf.tmp.');
    if ($tmp !== false && @file_put_contents($tmp, json_encode($cfg, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES)) !== false) {
        @chmod($tmp, 0664);
        if (@rename($tmp, $SCRIPTA_CONF)) {
            @chmod($SCRIPTA_CONF, 0664);
            // Migrate existing data so history survives the switch: copy the newest
            // existing copy to the new target, then clear the old location(s). The
            // copy happens first and the old is cleared only on success, so a failed
            // copy never loses data. (sgminer.log is a live log owned by the
            // capped-log-writer; it moves on the next miner restart instead.)
            $migrated = false;
            if ($file !== 'sgminer.log') {
                $to_sd = ($target === 'SD');
                $ram = '/run/blakestream/' . $file;
                $sd  = '/opt/scripta/var/' . $file;
                $dst = $to_sd ? $sd : $ram;
                $candidates = array($ram, $sd);
                if ($file === 'clock-history.json') $candidates[] = '/run/blakestream/clock-history-sd-buffer.json';
                $src = ''; $best = -1;
                foreach ($candidates as $c) {
                    if ($c === $dst || !@is_file($c)) continue;
                    $m = @filemtime($c);
                    if ($m !== false && $m > $best) { $best = $m; $src = $c; }
                }
                if ($src !== '') {
                    $ddir = dirname($dst);
                    if (!is_dir($ddir)) @mkdir($ddir, 0775, true);
                    if (@copy($src, $dst)) { @chmod($dst, 0664); $migrated = true; }
                }
                if ($migrated || $src === '') {
                    foreach ($candidates as $c) { if ($c !== $dst) @unlink($c); }
                }
            }
            $note = ($file === 'sgminer.log') ? 'takes effect on next miner restart'
                  : ($migrated ? ('existing data moved to ' . $target) : ('recorder writes to ' . $target . ' on the next run'));
            echo json_encode(array('ok' => true, 'key' => $key, 'target' => $target, 'migrated' => $migrated, 'note' => $note));
        } else { @unlink($tmp); echo json_encode(array('ok' => false, 'error' => 'rename failed')); }
    } else {
        if ($tmp !== false) @unlink($tmp);
        echo json_encode(array('ok' => false, 'error' => 'config write failed'));
    }
    break;

case 'file_download':
case 'file_view':
    // Only files already listed in the storage view are readable here.
    $path = isset($_GET['path']) ? $_GET['path'] : '';
    $rep = storage_report();
    $allowed = array();
    foreach (array_merge($rep['ram'], $rep['sd']) as $r) { $allowed[$r['path']] = true; }
    if (!is_string($path) || !isset($allowed[$path])) { echo json_encode(array('ok' => false, 'error' => 'not viewable')); exit; }
    if (!file_exists($path)) { echo json_encode(array('ok' => false, 'error' => 'absent')); exit; }
    if ($path === '/opt/scripta/var/security.log' && !bs_web_auth_scrub_log($path)) {
        echo json_encode(array('ok' => false, 'error' => 'security log redaction failed')); exit;
    }
    if ($action === 'file_download') {
        $fp = @fopen($path, 'rb');
        if (!$fp) { echo json_encode(array('ok' => false, 'error' => 'unreadable')); exit; }
        header('Content-Type: application/octet-stream');
        header('Content-Disposition: attachment; filename="' . basename($path) . '"');
        header('X-Content-Type-Options: nosniff');
        fpassthru($fp);
        fclose($fp);
        exit;
    }
    $clock_log = in_array(basename($path), array('clock-history.json', 'clock-history-sd-buffer.json'), true);
    $format = isset($_GET['format']) ? $_GET['format'] : ($clock_log ? 'readable' : 'raw');
    if ($clock_log && $format !== 'raw') {
        $view = bs_clock_log_view($path);
        $view['path'] = $path;
        echo json_encode($view);
        exit;
    }
    $size = (int) @filesize($path);
    $max = 262144;   // 256 KiB — enough to show most history files in full
    $text = '';
    $fp = @fopen($path, 'rb');
    if ($fp) {
        if ($size > $max) @fseek($fp, $size - $max);
        $text = stream_get_contents($fp, $max);
        fclose($fp);
    }
    $truncated = ($size > $max);
    // Human-readable: normalize sgminer's CR-rewritten status line, and for JSON
    // pretty-print the whole file when it fits, else put one record per line.
    $text = str_replace(array("\r\n", "\r"), "\n", (string) $text);
    if (!$clock_log && substr($path, -5) === '.json') {
        if (!$truncated) {
            $decoded = json_decode($text, true);
            if ($decoded !== null) {
                $pp = json_encode($decoded, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
                if ($pp !== false) $text = $pp;
            }
        } else {
            $text = str_replace('},{', "},\n{", $text);
        }
    }
    echo json_encode(array(
        'ok' => true, 'path' => $path, 'size' => $size,
        'shown' => strlen($text), 'truncated' => $truncated, 'text' => $text, 'format' => 'raw',
    ));
    break;

case 'firmware_status':
    firmware_job_apply_result($SCRIPTA_CONF);
    $FW = '/opt/scripta/firmware';
    $dfu = $FW . '/dfu_flash.py';
    $stock = $FW . '/stock.bin';
    $wrapper = '/opt/scripta/bin/bkb-firmware.sh';
    $stock_expected = 'd5f844be2952fd713717a71a67e2be61';
    $stock_present = file_exists($stock);
    $stock_md5 = $stock_present ? md5_file($stock) : '';
    $recovery_required = file_exists('/var/lib/blakestream-firstboot/firmware-recovery-required');
    $mining = firmware_miner_running();
    $flash_runtime = firmware_flash_runtime();
    $checks = array(
        array('name' => 'Stock image staged + md5 matches',
              'ok' => ($stock_present && $stock_md5 === $stock_expected),
              'detail' => $stock_present ? ($stock_md5 === $stock_expected ? 'OK' : 'md5 mismatch (' . $stock_md5 . ')') : 'missing'),
        array('name' => 'Flasher present', 'ok' => file_exists($dfu), 'detail' => file_exists($dfu) ? 'dfu_flash.py' : 'missing'),
        array('name' => 'Wrapper present', 'ok' => file_exists($wrapper), 'detail' => file_exists($wrapper) ? 'bkb-firmware.sh' : 'missing'),
        array('name' => 'Bridge verified for normal boot', 'ok' => !$recovery_required,
              'detail' => $recovery_required ? 'RECOVERY REQUIRED — bridge held in DFU' : 'OK'),
        array('name' => 'Miner reachable (API)', 'ok' => $mining,
              'detail' => $mining ? 'sgminer API up (a flash pauses mining ' . $flash_runtime['label'] . ')' : 'not reachable'),
    );
    $ready = true; foreach ($checks as $c) { if (!$c['ok']) { $ready = false; break; } }
    // Enable-path readiness is tracked separately so it never gates the
    // always-available revert: the custom image must be staged AND the corrected
    // sgminer clock encoder must be deployed (its sentinel present) before
    // flashing the adjustable-clock firmware is allowed.
    $li = firmware_last_identity();
    $custom_img = $FW . '/custom.bin';
    $custom_md5 = '18babe0c4c67d9146e230f9dc423f2f2';
    $custom_present = file_exists($custom_img) && md5_file($custom_img) === $custom_md5;
    $encoder_ready = file_exists($FW . '/ENCODER_READY');
    $custom_identity = ($li && isset($li['identity']) && strpos($li['identity'], 'CUSTOM') !== false);
    $custom_active = bkb_firmware_is_current($li);
    echo json_encode(array(
        'ok' => true,
        'known' => firmware_known_images(),
        'last_identity' => $li,
        'checks' => $checks,
        'ready' => $ready,
        'recovery_required' => $recovery_required,
        'custom_present' => $custom_present,
        'encoder_ready' => $encoder_ready,
        'custom_active' => $custom_active,
        'custom_legacy' => ($custom_identity && !$custom_active),
        'flash_runtime' => $flash_runtime,
        'flash_runtime_label' => $flash_runtime['label'],
        'flash_runtimes' => array(
            'revert' => firmware_flash_runtime('revert'),
            'flash-custom' => firmware_flash_runtime('flash-custom'),
            'verify' => firmware_flash_runtime('verify'),
        ),
    ));
    break;

case 'firmware_progress':
    // Live DFU progress for the Dev Tools/settings terminal overlay. Real
    // reads/writes run as a detached root job, so navigation or refresh cannot
    // kill the firmware operation; the browser only reconnects to this state.
    echo json_encode(firmware_job_state($SCRIPTA_CONF));
    break;

case 'firmware_verify':
    $wrapper = '/opt/scripta/bin/bkb-firmware.sh';
    if (!file_exists($wrapper)) { echo json_encode(array('ok' => false, 'error' => 'wrapper missing')); exit; }
    firmware_audit('firmware-verify start');
    $res = firmware_job_start('verify');
    echo json_encode($res);
    break;

case 'firmware_revert':
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) $body = array();
    $token = isset($body['confirm']) ? $body['confirm'] : '';
    $dry = !empty($body['dry_run']);
    $wrapper = '/opt/scripta/bin/bkb-firmware.sh';
    if (!file_exists($wrapper)) { echo json_encode(array('ok' => false, 'error' => 'wrapper missing')); exit; }
    if (!$dry && $token !== 'REVERT') {
        echo json_encode(array('ok' => false, 'error' => 'type REVERT to confirm'));
        exit;
    }
    if ($dry) {
        // Dry run: confirm the staged stock image is valid; write nothing.
        $stock = '/opt/scripta/firmware/stock.bin';
        $ok = file_exists($stock) && md5_file($stock) === 'd5f844be2952fd713717a71a67e2be61';
        echo json_encode(array('ok' => $ok, 'dry_run' => true,
            'output' => $ok ? 'Dry run OK: stock image staged + md5 verified. Nothing written.' : 'Dry run FAILED: stock image missing or md5 mismatch.'));
        exit;
    }
    firmware_audit('firmware-revert-to-stock start');
    echo json_encode(firmware_job_start('revert'));
    break;

case 'firmware_flash_custom':
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) $body = array();
    $token = isset($body['confirm']) ? $body['confirm'] : '';
    $dry = !empty($body['dry_run']);
    $wrapper = '/opt/scripta/bin/bkb-firmware.sh';
    $custom = '/opt/scripta/firmware/custom.bin';
    $custom_md5 = '18babe0c4c67d9146e230f9dc423f2f2';
    if (!file_exists($wrapper)) { echo json_encode(array('ok' => false, 'error' => 'wrapper missing')); exit; }
    if (!$dry && $token !== 'ENABLE') {
        echo json_encode(array('ok' => false, 'error' => 'type ENABLE to confirm'));
        exit;
    }
    if ($dry) {
        // Dry run: confirm the staged adjustable-clock image is valid; write nothing.
        $ok = file_exists($custom) && md5_file($custom) === $custom_md5;
        echo json_encode(array('ok' => $ok, 'dry_run' => true,
            'output' => $ok ? 'Dry run OK: adjustable-clock (V1) image staged + md5 verified. Nothing written.' : 'Dry run FAILED: custom image missing or md5 mismatch.'));
        exit;
    }
    // Hard gate: v1 reads validated clock indices and dest=1 requests that only the
    // corrected sgminer encoder/bridge-global coordinator supplies. Until that
    // coordinator is deployed (its sentinel present),
    // flashing v1 would silently mis-drive the clocks, so refuse the write.
    if (!file_exists('/opt/scripta/firmware/ENCODER_READY')) {
        echo json_encode(array('ok' => false,
            'error' => 'corrected sgminer bridge-global clock coordinator not deployed — flashing the adjustable-clock firmware now would mis-drive the clocks. Deploy the coordinator first.'));
        exit;
    }
    firmware_audit('firmware-flash-custom start');
    echo json_encode(firmware_job_start('flash-custom'));
    break;

case 'firmware_abort':
    $state = firmware_job_state($SCRIPTA_CONF);
    if (empty($state['running']) || empty($state['cancelable'])) {
        echo json_encode(array('ok' => false, 'error' => 'firmware operation is not abortable'));
        exit;
    }
    $wrapper = '/opt/scripta/bin/bkb-firmware.sh';
    $raw = shell_exec('/usr/bin/sudo -n ' . escapeshellarg($wrapper) . ' abort-job 2>&1');
    echo json_encode(firmware_wrapper_response($raw, 'abort request failed'));
    break;

case 'storage_clear':
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) $body = array();
    $path = isset($body['path']) ? $body['path'] : '';
    if (!in_array($path, storage_clearable_paths(), true)) {
        echo json_encode(array('ok' => false, 'error' => 'not a clearable path'));
        exit;
    }
    if (!file_exists($path)) {
        echo json_encode(array('ok' => true, 'cleared' => false, 'size' => 0, 'note' => 'already absent'));
        exit;
    }
    $before = (int) @filesize($path);
    $ok = bs_diagnostic_clear($path);
    @touch($path);   // reset mtime so the row's "age" clears to just-now
    clearstatcache(false, $path);
    echo json_encode(array(
        'ok' => ($ok !== false),
        'cleared' => ($ok !== false),
        'before' => $before,
        'size' => (int) @filesize($path),
        'error' => ($ok === false) ? 'truncate failed (permission?)' : '',
    ));
    break;

case 'storage_clear_all':
    // Clear every clearable file on one side (RAM or SD). The SD side also
    // prunes security.log to the last 7 days instead of wiping it.
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) $body = array();
    if (!bs_measured_authorized(isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '', $body, $dev_activity_csrf)) {
        http_response_code(403);
        echo json_encode(array('ok' => false, 'error' => 'Invalid request token'));
        break;
    }
    $side = isset($body['side']) ? $body['side'] : '';
    if (!in_array($side, array('RAM', 'SD'), true)) {
        http_response_code(400);
        echo json_encode(array('ok' => false, 'error' => 'Invalid storage side'));
        break;
    }
    $measured_clear = bs_measured_command('clear-side', $side);
    if (empty($measured_clear['ok'])) { echo json_encode($measured_clear); break; }
    $prefixes = ($side === 'SD') ? array('/opt/scripta/var/', '/var/log/')
        : array('/run/blakestream/', '/run/blakestream-miner/', '/run/blakestream-web/');
    $cleared = 0; $errors = array();
    foreach (storage_clearable_paths() as $path) {
        $on_side = false;
        foreach ($prefixes as $pre) { if (strpos($path, $pre) === 0) { $on_side = true; break; } }
        if (!$on_side || !file_exists($path)) continue;
        if (bs_diagnostic_clear($path) !== false) { @touch($path); $cleared++; }
        else { $errors[] = $path; }
    }
    $pruned = null;
    if ($side === 'SD') {
        $sec = '/opt/scripta/var/security.log';
        if (file_exists($sec)) {
            $pruned = security_log_prune($sec, 7);
            if ($pruned === false) $errors[] = 'security log prune failed';
        }
    }
    clearstatcache();
    echo json_encode(array('ok' => empty($errors), 'side' => $side, 'cleared' => $cleared, 'errors' => $errors, 'pruned' => $pruned));
    break;

case 'log_prune':
    // Keep the last N days of security evidence. security.log is physically
    // pruned; SSH is a shared-journal view, so only its earliest visible time
    // changes and the underlying journal remains untouched.
    $body = json_decode(file_get_contents('php://input'), true);
    if (!is_array($body)) $body = array();
    $path = isset($body['path']) ? $body['path'] : '';
    $source = isset($body['source']) ? $body['source'] : '';
    $days = isset($body['days']) ? (int) $body['days'] : 7;
    if ($days < 1) $days = 7;
    if ($source === 'ssh') {
        $cutoff = time() - ($days * 86400);
        if (!set_journal_clear_marker('ssh', $cutoff)) {
            echo json_encode(array('ok' => false, 'error' => 'failed to set SSH retention marker'));
            exit;
        }
        firmware_audit('dev-log-prune ssh keep=' . $days . 'd');
        echo json_encode(array(
            'ok' => true,
            'source' => 'ssh',
            'days' => $days,
            'since' => $cutoff,
            'note' => 'SSH view retains the latest ' . $days . ' days; system journal unchanged',
        ));
        exit;
    }
    $prunable = array('/opt/scripta/var/security.log');
    if (!in_array($path, $prunable, true)) { echo json_encode(array('ok' => false, 'error' => 'not a prunable log')); exit; }
    if (!file_exists($path)) { echo json_encode(array('ok' => true, 'kept' => 0, 'removed' => 0, 'days' => $days, 'size' => 0)); exit; }
    $pruned = security_log_prune($path, $days);
    if ($pruned !== false) firmware_audit('dev-log-prune security.log keep=' . $days . 'd');
    clearstatcache(false, $path);
    echo json_encode(array(
        'ok' => ($pruned !== false), 'kept' => $pruned === false ? 0 : $pruned['kept'],
        'removed' => $pruned === false ? 0 : $pruned['removed'],
        'days' => $days, 'size' => (int) @filesize($path),
        'error' => ($pruned === false) ? 'prune failed' : '',
    ));
    break;

case 'activity':
    // Read-only. dev_activity_read_command() is stats; this case must not call a mutation verb.
    $method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET';
    header('Cache-Control: no-store');
    if ($method !== 'GET' && $method !== 'HEAD') {
        http_response_code(405);
        echo json_encode(array('ok' => false, 'error' => 'method', 'csrf' => $dev_activity_csrf));
        exit;
    }
    $cmd = dev_activity_read_command();
    if ($cmd !== 'stats' || !dev_activity_sgminer_allowed($cmd, '')) {
        http_response_code(500);
        echo json_encode(array('ok' => false, 'error' => 'rejected', 'csrf' => $dev_activity_csrf));
        exit;
    }
    $raw = sgminer_api_exchange($cmd, '');
    $stats = null;
    $act_error = null;
    if (!is_string($raw)) {
        $act_error = 'sgminer api unreachable';
    } else {
        $stats = dev_activity_decode_json($raw, 512);
        if (!is_array($stats)) $act_error = 'stats decode failed';
    }
    $collector = ($act_error === null) ? dev_activity_load_production_collector() : null;
    echo json_encode(dev_activity_build($stats, $collector, $dev_activity_csrf, $act_error));
    exit;

case 'activity_trace':
    $method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '';
    $len = isset($_SERVER['CONTENT_LENGTH']) ? (int) $_SERVER['CONTENT_LENGTH'] : 0;
    $raw_body = '';
    if ($method === 'POST' && $len <= 4096) {
        $in = fopen('php://input', 'rb');
        if ($in) {
            $raw_body = dev_activity_read_stream($in, 4097);
            fclose($in);
        }
    }
    $parsed = dev_activity_parse_mutation($method, $raw_body, $len, $dev_activity_csrf);
    if (!$parsed['ok']) {
        http_response_code($parsed['status']);
        header('Cache-Control: no-store');
        echo json_encode(array('ok' => false, 'error' => $parsed['error'], 'csrf' => $dev_activity_csrf));
        exit;
    }
    header('Cache-Control: no-store');
    if ($parsed['op'] === 'download') {
        $opened = dev_activity_open_selected_export();
        if (!$opened['ok']) {
            http_response_code($opened['error'] === 'missing' ? 404 : 400);
            echo json_encode(array('ok' => false, 'error' => $opened['error'], 'csrf' => $dev_activity_csrf));
            exit;
        }
        header('Content-Type: application/octet-stream');
        header('Content-Disposition: attachment; filename="bkb-diag-trace.bin"');
        header('Content-Length: ' . $opened['size']);
        header('X-Content-Type-Options: nosniff');
        dev_activity_stream_fp($opened['fp'], $opened['size'], dev_activity_export_max_bytes());
        fclose($opened['fp']);
        exit;
    }
    $spec = dev_activity_trace_spec($parsed['op']);
    if ($spec === null || !dev_activity_sgminer_allowed($spec['command'], $spec['parameter'])) {
        http_response_code(501);
        echo json_encode(array('ok' => false, 'error' => 'unsupported', 'csrf' => $dev_activity_csrf));
        exit;
    }
    $resp = sgminer_api_exchange($spec['command'], $spec['parameter']);
    $decoded = null;
    if (is_string($resp)) {
        $decoded = dev_activity_decode_json($resp, 512);
    }
    if (!dev_activity_mutation_ok($decoded)) {
        http_response_code(502);
        echo json_encode(array(
            'ok' => false,
            'op' => $parsed['op'],
            'error' => dev_activity_status_message($decoded),
            'csrf' => $dev_activity_csrf,
        ));
        exit;
    }
    echo json_encode(array(
        'ok' => true,
        'op' => $parsed['op'],
        'error' => null,
        'csrf' => $dev_activity_csrf,
    ));
    exit;

default:
    http_response_code(400);
    echo json_encode(array('ok' => false, 'error' => 'unknown action: ' . $action));
    break;
}
