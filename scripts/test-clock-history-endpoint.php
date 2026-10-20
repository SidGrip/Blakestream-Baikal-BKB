<?php
// Run with PHP CLI, including PHP 5.6; no miner access.
date_default_timezone_set('UTC');
if (!defined('STDERR')) define('STDERR', fopen('php://stderr', 'w'));
if (getenv('BS_CLOCK_TEST_ARGS')) $argv = json_decode(getenv('BS_CLOCK_TEST_ARGS'), true);

function clock_expect($ok, $message) {
    if (!$ok) { fwrite(STDERR, 'FAIL: ' . $message . "\n"); exit(1); }
}

// Frozen full-array transformation; aggregation uses the endpoint's unchanged
// array branch so equal-time usort behavior matches the running PHP version.
function clock_legacy_emit($path, $hours, $cutoff, $storage) {
    $hist = read_json_file($path, array('boards' => array(), 'updated' => 0));
    $rows = array();
    foreach (isset($hist['boards']) && is_array($hist['boards']) ? $hist['boards'] : array() as $b) {
        foreach (isset($b['samples']) && is_array($b['samples']) ? $b['samples'] : array() as $s) {
            if (is_array($s) && isset($s['t']) && (int)$s['t'] >= $cutoff) {
                $rows[] = array('board_id' => isset($b['id']) ? (int)$b['id'] : 0, 'sample' => $s);
            }
        }
    }
    usort($rows, function($a, $b) {
        $ta = (int)$a['sample']['t']; $tb = (int)$b['sample']['t'];
        return $ta === $tb ? $a['board_id'] - $b['board_id'] : $ta - $tb;
    });
    $by_time = array();
    foreach ($rows as $row) {
        $t = (int)$row['sample']['t'];
        if ($t) $by_time[$t][] = $row;
    }
    ksort($by_time, SORT_NUMERIC);
    $runs = array(); $map = array(); $current = null; $latest = null; $latest_end = 0;
    foreach ($by_time as $t => $items) {
        $p = clock_time_profile_from_rows($items);
        if ($p === null) continue;
        if ($current === null || $runs[$current]['run_key'] !== $p['run_key']) {
            $current = 'run-' . (count($runs) + 1);
            $runs[$current] = array('id' => $current, 'profile_key' => $p['key'], 'run_key' => $p['run_key'],
                'clock' => $p['clock'], 'experimental' => $p['experimental'], 'recover' => $p['recover'],
                'cutoff' => $p['cutoff'], 'fan' => $p['fan'], 'fan_mode' => $p['fan_mode'],
                'start_ts' => (int)$t, 'end_ts' => (int)$t, 'active' => false,
                'boards_by_id' => array(), 'sample_count' => 0);
        }
        foreach (array('recover', 'cutoff', 'fan', 'fan_mode') as $key) $runs[$current][$key] = $p[$key];
        $runs[$current]['end_ts'] = (int)$t;
        $map[$t] = $current;
        if ($t >= $latest_end) { $latest_end = $t; $latest = $current; }
    }
    foreach ($rows as $row) {
        $t = (int)$row['sample']['t'];
        if (!$t || !isset($map[$t])) continue;
        $runs[$map[$t]]['boards_by_id'][$row['board_id']][] = $row['sample'];
        $runs[$map[$t]]['sample_count']++;
    }
    $out = array();
    foreach ($runs as $run) {
        $boards = array();
        foreach ($run['boards_by_id'] as $bid => $samples) $boards[] = clock_aggregate_board($bid, $samples);
        usort($boards, function($a, $b) { return $a['id'] - $b['id']; });
        unset($run['boards_by_id']);
        $run['boards'] = $boards;
        $run['duration_seconds'] = max(0, $run['end_ts'] - $run['start_ts']);
        $run['active'] = $latest !== null && $run['id'] === $latest;
        $out[] = $run;
    }
    usort($out, function($a, $b) {
        if (!empty($a['active']) && empty($b['active'])) return -1;
        if (empty($a['active']) && !empty($b['active'])) return 1;
        return (int)$b['end_ts'] - (int)$a['end_ts'];
    });
    echo json_encode(array('runs' => $out, 'window_hours' => $hours,
        'step_seconds' => isset($hist['step_seconds']) ? max(1, (int)$hist['step_seconds']) : 60,
        'updated' => isset($hist['updated']) ? (int)$hist['updated'] : 0,
        'storage' => $storage, 'read_path' => $path));
}

function clock_legacy_clear($path, $paths, $start, $end, $profile_key, $run_key, $now) {
    $hist = read_json_file($path, array('boards' => array()));
    if (!is_array($hist)) $hist = array();
    if (!isset($hist['boards']) || !is_array($hist['boards'])) $hist['boards'] = array();
    $by_time = array(); $targets = array(); $removed = 0;
    if ($run_key !== '') {
        foreach ($hist['boards'] as $b) {
            foreach (isset($b['samples']) && is_array($b['samples']) ? $b['samples'] : array() as $s) {
                if (!is_array($s) || !isset($s['t'])) continue;
                $t = (int)$s['t'];
                if ($t >= $start && $t <= $end) $by_time[$t][] = array(
                    'board_id' => isset($b['id']) ? (int)$b['id'] : 0, 'sample' => $s);
            }
        }
        foreach ($by_time as $t => $rows) {
            $p = clock_time_profile_from_rows($rows);
            if ($p !== null && $p['run_key'] === $run_key) $targets[$t] = true;
        }
    }
    foreach ($hist['boards'] as &$b) {
        if (!isset($b['samples']) || !is_array($b['samples'])) continue;
        $kept = array();
        foreach ($b['samples'] as $s) {
            $t = is_array($s) && isset($s['t']) ? (int)$s['t'] : 0;
            $remove = is_array($s) && ($run_key !== '' ? isset($targets[$t]) :
                ($t >= $start && $t <= $end && clock_sample_profile_matches($s, $profile_key)));
            if ($remove) $removed++; else $kept[] = $s;
        }
        $b['samples'] = $kept;
    }
    unset($b);
    $hist['updated'] = $now;
    foreach (array('version' => 1, 'step_seconds' => 60, 'max_age_seconds' => 7 * 86400) as $k => $v) {
        if (!isset($hist[$k])) $hist[$k] = $v;
    }
    return write_json_files($paths, $hist) ? array('ok' => true, 'removed_samples' => $removed) : false;
}

if (isset($argv[1]) && $argv[1] === '--worker') {
    $cfg = json_decode($argv[2], true);
    $tmp = $cfg['tmp'];
    $www = dirname(__DIR__) . '/overlay/var/www';
    register_shutdown_function(function() {
        $buffered = 0;
        foreach (ob_get_status(true) as $b) $buffered += $b['buffer_used'];
        fwrite(STDERR, json_encode(array('peak_bytes' => memory_get_peak_usage(true),
            'buffered_bytes' => $buffered, 'session' => session_status(),
            'status' => http_response_code() ? http_response_code() : 200)) . "\n");
    });
    $source = file_get_contents($cfg['endpoint']);
    $source = str_replace("__DIR__ . '/inc/clock-history-stream.php'",
        var_export($tmp . '/clock-helper.php', true), $source);
    if ($cfg['legacy']) $source = str_replace(array('bs_clock_history_emit(', 'bs_clock_history_clear('),
        array('clock_legacy_emit(', 'clock_legacy_clear('), $source);
    // Redirect only filesystem roots and time; exercise the real auth/dispatch.
    $source = str_replace(array("'/opt/scripta/etc'", "'/opt/scripta/var'", "'/run/blakestream'", '__DIR__', 'time()'),
        array(var_export($tmp . '/etc', true), var_export($tmp . '/sd', true),
            var_export($tmp . '/ram', true), var_export($www, true), (string)$cfg['now']), $source);
    session_save_path($tmp);
    session_id('clock-test-' . getmypid());
    session_start(); $_SESSION['_logged_'] = $cfg['auth']; session_write_close();
    $_REQUEST = array('action' => $cfg['action'], 'data' => json_encode($cfg['payload']));
    if ($cfg['hours'] !== 'default') $_REQUEST['hours'] = $cfg['hours'];
    chdir($www);
    eval(substr($source, 5));
    exit;
}

$tmp = sys_get_temp_dir() . '/bs-clock-' . getmypid() . '-' . mt_rand(1000, 9999);
clock_expect(mkdir($tmp, 0700), 'create test directory');
foreach (array('etc', 'ram', 'sd') as $dir) clock_expect(mkdir($tmp . '/' . $dir, 0700), 'create fixture root');
register_shutdown_function(function() use ($tmp) {
    $items = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($tmp, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::CHILD_FIRST);
    foreach ($items as $item) {
        if ($item->isDir()) rmdir($item->getPathname()); else unlink($item->getPathname());
    }
    rmdir($tmp);
});
$endpoint = dirname(__DIR__) . '/overlay/var/www/f_blakestream.php';
$inc = dirname(__DIR__) . '/overlay/var/www/inc';
// Keep the production collector's lock untouched, including on development hosts.
file_put_contents($tmp . '/clock-helper.php', str_replace(
    array('__DIR__', "'/tmp/blakestream-combined-history.lock'"),
    array(var_export($inc, true), var_export($tmp . '/collector.lock', true)),
    file_get_contents($inc . '/clock-history-stream.php')));
$paths = array('ram/clock-history.json', 'ram/clock-history-sd-buffer.json', 'sd/clock-history.json');
$now = 1800000000;

function clock_install($raw, $sd = false) {
    global $tmp, $paths, $now;
    file_put_contents($tmp . '/etc/scripta.conf', json_encode(array('userPersistClockHistory' => $sd)));
    foreach ($paths as $path) { file_put_contents($tmp . '/' . $path, $raw); touch($tmp . '/' . $path, $now); }
}

function clock_saved($decode = false) {
    global $tmp, $paths;
    $saved = array();
    foreach ($paths as $path) {
        $raw = file_get_contents($tmp . '/' . $path);
        $saved[$path] = hash('sha256', $decode ? json_encode(json_decode($raw, true)) : $raw);
    }
    return $saved;
}

function clock_worker($options = array()) {
    global $tmp, $endpoint, $now;
    $cfg = array_merge(array('tmp' => $tmp, 'endpoint' => $endpoint, 'now' => $now, 'legacy' => false,
        'action' => 'load_clock_history', 'payload' => array(), 'hours' => 168, 'auth' => true), $options);
    $args = array(PHP_BINARY, '-d', 'memory_limit=' . ($cfg['legacy'] ? '256M' : '16M'),
        '-d', 'date.timezone=UTC', '-d', 'display_errors=stderr', __FILE__, '--worker', json_encode($cfg));
    $env = null;
    if (isset($cfg['cgi'])) {
        $args = array(dirname(PHP_BINARY) . '/php-cgi', '-d', 'memory_limit=16M',
            '-d', 'date.timezone=UTC', '-d', 'output_buffering=' . $cfg['cgi']);
        $env = array('SCRIPT_FILENAME' => __FILE__, 'REQUEST_METHOD' => 'GET', 'REDIRECT_STATUS' => '200',
            'SERVER_PROTOCOL' => 'HTTP/1.1', 'BS_CLOCK_TEST_ARGS' => json_encode(array(__FILE__, '--worker', json_encode($cfg))));
    }
    $start = microtime(true);
    $process = proc_open(implode(' ', array_map('escapeshellarg', $args)), array(0 => array('pipe', 'r'),
        1 => array('file', $tmp . '/out', 'w'), 2 => array('file', $tmp . '/err', 'w')), $pipes, null, $env);
    clock_expect(is_resource($process), 'start isolated worker'); fclose($pipes[0]);
    $rc = proc_close($process);
    $errors = trim(file_get_contents($tmp . '/err')); $lines = explode("\n", $errors);
    $stats = json_decode(end($lines), true);
    clock_expect($rc === 0 && is_array($stats), 'worker: ' . $errors);
    $body = file_get_contents($tmp . '/out'); $headers = '';
    if (isset($cfg['cgi'])) {
        $parts = explode("\r\n\r\n", $body, 2);
        clock_expect(count($parts) === 2, 'CGI headers'); $headers = $parts[0]; $body = $parts[1];
    }
    return array('stats' => $stats, 'body' => $body, 'headers' => $headers, 'errors' => $errors,
        'seconds' => round(microtime(true) - $start, 3), 'bytes' => strlen($body), 'sha256' => hash('sha256', $body));
}

function clock_pair($raw, $options = array(), $sd = false, $prior = null) {
    global $endpoint;
    $mutating = isset($options['action']) && $options['action'] === 'clear_clock_run';
    clock_install($raw, $sd); $before = clock_saved();
    $old_options = array_merge($options, array('legacy' => true));
    if ($prior !== null) $old_options['endpoint'] = $prior;
    $old = clock_worker($old_options); $old_saved = clock_saved($mutating);
    clock_install($raw, $sd);
    $new = clock_worker($options); $new_saved = clock_saved($mutating);
    clock_expect($old['body'] === $new['body'], 'old/new response byte identity');
    clock_expect($old_saved === $new_saved, 'old/new retained data identity');
    if (!isset($options['action'])) {
        clock_expect(clock_saved() === $before, 'load does not modify stored bytes');
        clock_expect($new['stats']['session'] === PHP_SESSION_NONE, 'read-only clock releases session');
    }
    return array($old, $new);
}

function clock_row($t, $clock, $n) {
    return array('t' => $t, 'rc' => $clock, 'cc' => $clock, 'c' => $clock, 'cm' => 'global',
        'rec' => 40, 'cut' => 55, 'fan' => 60 + $n, 'fm' => 'manual', 'pool' => 'http://quota:2;pool.example',
        'a' => $n, 'r' => $n % 3, 'hw' => $n % 2, 'tmp' => 30.125 + $n,
        'unknown' => array('quoted' => '[},' . chr(34) . chr(92), 'nested' => array(0, null, false)));
}

if (isset($argv[1]) && $argv[1] === '--compare') {
    clock_expect(isset($argv[3]), '--compare CLOCK_JSON PRIOR_ENDPOINT_PHP');
    $raw = file_get_contents($argv[2]); $hist = json_decode($raw, true);
    $now = (int)$hist['updated']; unset($hist);
    $pair = clock_pair($raw, array(), true, $argv[3]);
    $view = json_decode($pair[0]['body'], true); $key = $view['runs'][0]['run_key']; unset($view);
    foreach ($pair as &$result) { unset($result['body'], $result['errors']); } unset($result);
    echo 'REAL load ', json_encode($pair), "\n";
    foreach (array('', $key) as $key) {
        $pair = clock_pair($raw, array('action' => 'clear_clock_run',
            'payload' => array('start_ts' => $now - 3600, 'end_ts' => $now, 'run_key' => $key)), true, $argv[3]);
        foreach ($pair as &$result) { unset($result['errors']); } unset($result);
        echo 'REAL clear ', json_encode($pair), "\n";
    }
    exit;
}

$rows = array(clock_row($now - 120, 400, 1), clock_row($now - 60, 480, 2), clock_row($now, 400, 3));
$cases = array();
$cases['A-B-A'] = array('updated' => $now, 'boards' => array(array('id' => 2, 'samples' => $rows), array('id' => 0, 'samples' => $rows)));
$cases['ties-unsorted-duplicate-boards'] = array('updated' => $now, 'boards' => array(
    array('id' => 2, 'samples' => array($rows[2], $rows[0], $rows[1], $rows[0])),
    array('id' => 0, 'samples' => array($rows[1], $rows[0], $rows[2], $rows[0])),
    array('id' => 2, 'samples' => array($rows[0], $rows[0], $rows[1], $rows[2]))));
$cases['keyed-and-nonarray'] = array('updated' => $now, 'extra' => array('keep' => true), 'boards' => array(
    'two' => array('extra' => false, 'samples' => array('last' => $rows[2], 'first' => $rows[0], 'middle' => $rows[1]), 'id' => 2),
    'zero' => array('id' => 0, 'samples' => array(null, false, 'keep', array('unknown' => true), $rows[0], $rows[1], $rows[2]))));
$cases['null-defaults'] = array('updated' => null, 'step_seconds' => 0, 'version' => null, 'boards' => array(null, false,
    array('id' => 0, 'samples' => array(array('t' => $now), array('t' => $now - 60, 'pool' => null)))));
$large = clock_row($now, 400, 4); $large['unknown']['long'] = str_repeat('x', 60000);
$cases['60KiB-sample'] = array('updated' => $now, 'boards' => array(array('id' => 0, 'samples' => array($rows[0], $large))));
$cases['cutoff'] = array('updated' => $now, 'boards' => array(array('id' => 0, 'samples' => array(
    clock_row($now - 3601, 400, 1), clock_row($now - 3600, 400, 2), clock_row($now, 400, 3)))));
$cases['run-key-original-board-order'] = $cases['A-B-A'];
$cases['run-key-original-board-order']['boards'][0]['samples'][2]['cm'] = 'individual';
foreach ($cases as $name => $data) {
    $raw = json_encode($data); $pair = clock_pair($raw);
    $view = json_decode($pair[0]['body'], true);
    if ($name === 'A-B-A') clock_expect(count($view['runs']) === 3, 'A-B-A remains three runs');
    foreach (array(array(), array('profile_key' => 'global:400:40:55:63:manual'),
            array('run_key' => $view['runs'][0]['run_key'])) as $selector) {
        $payload = array_merge(array('start_ts' => $now - 120, 'end_ts' => $now), $selector);
        clock_pair($raw, array('action' => 'clear_clock_run', 'payload' => $payload), true);
        clock_pair($raw, array('action' => 'clear_clock_run', 'payload' => $payload));
    }
    echo 'PASS ', $name, " load/interval/profile/run-key clear\n";
}
foreach (array('default', 0, 1, 24, 168) as $hours) clock_pair(json_encode($cases['cutoff']), array('hours' => $hours));

$raw = json_encode($cases['A-B-A']);
foreach (array(false, true) as $sd) {
    clock_install($raw, $sd);
    $before = clock_saved();
    foreach (array('load_clock_history', 'clear_clock_run') as $action) {
        $result = clock_worker(array('action' => $action, 'auth' => false));
        clock_expect($result['stats']['status'] === 401 && $result['body'] === '{"error":"not logged in"}', 'auth unchanged');
    }
    foreach (array(null, array(), array('start_ts' => 2, 'end_ts' => 1)) as $payload) {
        $result = clock_worker(array('action' => 'clear_clock_run', 'payload' => $payload));
        clock_expect($result['stats']['status'] === 400, 'invalid clear rejected');
    }
    clock_expect(clock_saved() === $before, 'rejected requests never write');
    $result = clock_worker(); $view = json_decode($result['body'], true);
    clock_expect($view['read_path'] === $tmp . '/' . ($sd ? $paths[1] : $paths[0]), 'RAM/SD tie selection');
}
touch($tmp . '/' . $paths[2], $now + 1);
$result = clock_worker(); $view = json_decode($result['body'], true);
clock_expect($view['read_path'] === $tmp . '/' . $paths[2], 'newer SD wins');
echo "PASS hours, auth, validation, read-only session release, RAM/SD selection\n";

// Cross multiple batch boundaries and verify CGI never retains a full response.
$samples = array();
for ($i = 0; $i < 2048; $i++) $samples[] = clock_row($now - 2048 + $i, 400, $i);
$batch_raw = json_encode(array('updated' => $now, 'boards' => array(array('id' => 0, 'samples' => $samples))));
unset($samples);
$pair = clock_pair($batch_raw);
clock_expect($pair[1]['stats']['peak_bytes'] < 12 * 1024 * 1024, 'bounded multi-batch peak');
if (is_executable(dirname(PHP_BINARY) . '/php-cgi')) {
    foreach (array('4096', 'On') as $buffer) {
        $result = clock_worker(array('cgi' => $buffer));
        clock_expect($result['body'] === $pair[1]['body'], 'CGI body byte identity');
        clock_expect(stripos($result['headers'], 'Content-Type: application/json') !== false, 'CGI content type');
        clock_expect($result['stats']['buffered_bytes'] <= 16384, 'bounded CGI output buffer');
        clock_expect($result['stats']['session'] === PHP_SESSION_NONE, 'CGI session released');
    }
    $result = clock_worker(array('cgi' => 'On', 'auth' => false));
    clock_expect(strpos($result['headers'], 'Status: 401') !== false, 'CGI auth status');
    echo "PASS CGI headers, auth, session and bounded output buffering\n";
} else echo "SKIP CGI: php-cgi not installed beside PHP CLI\n";

$week_samples = array();
for ($i = 0; $i < 10080; $i++) {
    $row = clock_row($now - 604740 + $i * 60, 400, $i % 5);
    unset($row['unknown']);
    $row['a'] = $i;
    $week_samples[] = $row;
}
$week = array('updated' => $now, 'boards' => array());
foreach (array(2, 0, 1) as $bid) $week['boards'][] = array('id' => $bid, 'samples' => $week_samples);
$week_raw = json_encode($week);
unset($week, $week_samples, $row);
$pair = clock_pair($week_raw);
clock_expect($pair[1]['stats']['peak_bytes'] <= 16 * 1024 * 1024, 'bounded full-week peak');
$week_view = json_decode($pair[1]['body'], true);
clock_expect(count($week_view['runs']) === 1 && $week_view['runs'][0]['sample_count'] === 30240,
    'all three boards retain a full week');
foreach ($week_view['runs'][0]['boards'] as $board) clock_expect(count($board['samples']) === 10080, 'full board week');
echo 'PASS full week: legacy_peak=', $pair[0]['stats']['peak_bytes'],
    ' streamed_peak=', $pair[1]['stats']['peak_bytes'], ' response_bytes=', $pair[1]['bytes'], "\n";
unset($week_raw, $week_view, $pair);

foreach (array('{"private":"PRIVATE_MARKER","boards":[', '{"boards":[{"samples":[{"t":1},]}]}',
        '{"boards":[{"samples":[{"t":1,"large":"' . str_repeat('x', 140000) . '"}]}]}') as $bad) {
    clock_install($bad); $before = clock_saved();
    $result = clock_worker(); $view = json_decode($result['body'], true);
    clock_expect(is_array($view) && $view['runs'] === array(), 'invalid/oversize load fallback');
    $result = clock_worker(array('action' => 'clear_clock_run', 'payload' => array('start_ts' => 1, 'end_ts' => 2)));
    clock_expect($result['stats']['status'] === 500 && clock_saved() === $before, 'corrupt clear fails without overwriting');
    clock_expect(strpos($result['errors'], 'PRIVATE_MARKER') === false &&
        strpos($result['errors'], 'clock-history:') !== false, 'static corruption reason without payload');
}
echo "PASS malformed and oversized input guards preserve evidence\n";

require_once $tmp . '/clock-helper.php';
ini_set('error_log', $tmp . '/transaction-errors');
$a = $tmp . '/target-a'; $b = $tmp . '/target-b';
foreach (array(false, true) as $missing) {
    if ($missing) unlink($a); else file_put_contents($a, 'original-a');
    file_put_contents($b, 'original-b'); $calls = 0;
    $ok = bs_clock_history_replace(array($a, $b), function($out) { bs_clock_history_write($out, 'new'); },
        function() { return true; }, function($from, $to) use (&$calls) { return ++$calls === 2 ? false : rename($from, $to); });
    clock_expect(!$ok && ($missing ? !file_exists($a) : file_get_contents($a) === 'original-a'), 'first target rollback');
    clock_expect(file_get_contents($b) === 'original-b', 'failed second target unchanged');
    clock_expect(count(glob($tmp . '/.clock-history-*')) === 0, 'rollback cleanup');
}
file_put_contents($a, 'original-a');
$ok = bs_clock_history_replace(array($a, $b), function($out) {
    bs_clock_history_write($out, 'partial'); throw new RuntimeException('injected staging failure');
}, function() { return true; });
clock_expect(!$ok && file_get_contents($a) === 'original-a' && file_get_contents($b) === 'original-b', 'partial stage never installed');
$ok = bs_clock_history_replace(array($a, $b), function($out) { bs_clock_history_write($out, '{"updated":9}'); },
    function() use ($a) { file_put_contents($a . '.next', 'collector-new'); rename($a . '.next', $a); return false; });
clock_expect(!$ok && file_get_contents($a) === 'collector-new' && file_get_contents($b) === 'original-b', 'new collector generation retained');
$calls = 0;
$ok = bs_clock_history_replace(array($a, $b), function($out) { bs_clock_history_write($out, '{"boards":[]}'); },
    function() { return true; }, function($from, $to) use (&$calls) {
        clock_expect(json_decode(file_get_contents($from), true) === array('boards' => array()), 'complete file before rename');
        $calls++; return rename($from, $to);
    });
clock_expect($ok && $calls === 2 && file_get_contents($a) === file_get_contents($b), 'complete two-target commit');
clock_expect(count(glob($tmp . '/.clock-history-*')) === 0, 'transaction stages cleaned');
$before = hash_file('sha256', $a);
$lock = fopen($tmp . '/collector.lock', 'c');
clock_expect(is_resource($lock) && flock($lock, LOCK_EX | LOCK_NB), 'acquire collector lock');
clock_expect(bs_clock_history_clear($a, array($a, $b), 1, 2, '', '', $now) === false, 'collector/second-clear exclusion');
flock($lock, LOCK_UN); fclose($lock);
clock_expect(hash_file('sha256', $a) === $before, 'busy lock never writes');
echo "PASS staging, rollback, generation guards, atomic-file commits, recorder/second-clear lock\n";
echo "PASS clock history endpoint regressions\n";
