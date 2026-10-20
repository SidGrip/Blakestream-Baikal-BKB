<?php
// Run with PHP CLI, including PHP 5.6; no miner access.
require_once dirname(__DIR__) . '/overlay/var/www/inc/hashrate-history.php';
date_default_timezone_set('UTC');
if (!defined('STDERR')) define('STDERR', fopen('php://stderr', 'w'));
if (getenv('BS_HISTORY_TEST_ARGS')) $argv = json_decode(getenv('BS_HISTORY_TEST_ARGS'), true);

function history_expect($ok, $message) {
    if (!$ok) {
        fwrite(STDERR, 'FAIL: ' . $message . "\n");
        exit(1);
    }
}

// Frozen pre-streaming transformation, used only as a compatibility oracle.
function history_legacy($raw, $hours, $cutoff, $names) {
    $hist = json_decode($raw, true);
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
        $accepted_window = array();
        foreach (isset($b['accepted_samples']) && is_array($b['accepted_samples']) ? $b['accepted_samples'] : array() as $s) {
            if (is_array($s) && count($s) >= 2 && (int)$s[0] >= $cutoff) {
                $accepted_window[] = array((int)$s[0], $s[1] === null ? null : (float)$s[1]);
            }
        }
        $url = isset($b['pool_url']) ? $b['pool_url'] : '';
        $clean = preg_replace('|^https?://|', '', $url);
        $clean = preg_replace('|^quota:\d+;|', '', $clean);
        $name = isset($names[$clean]) ? $names[$clean] : null;
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
    return json_encode(array('boards' => $boards_out, 'window_hours' => $hours,
        'step_seconds' => isset($hist['step_seconds']) ? (int)$hist['step_seconds'] : 60,
        'updated' => isset($hist['updated']) ? (int)$hist['updated'] : 0));
}

if (isset($argv[1]) && $argv[1] === '--worker') {
    $tmp = $argv[2];
    $hours = $argv[3];
    $action = $argv[4];
    $now = (int)$argv[5];
    $source_path = $argv[6];
    register_shutdown_function(function() {
        $buffered = 0;
        foreach (ob_get_status(true) as $buffer) $buffered += $buffer['buffer_used'];
        fwrite(STDERR, json_encode(array('peak_bytes' => memory_get_peak_usage(true),
            'buffered_bytes' => $buffered,
            'status' => http_response_code() ? http_response_code() : 200)) . "\n");
    });
    if ($source_path === 'legacy') {
        echo history_legacy(file_get_contents($tmp . '/ram/hashrate-history.json'),
            (int)$hours, $now - ((int)$hours * 3600), array());
        exit;
    }
    $www = dirname(__DIR__) . '/overlay/var/www';
    $source = file_get_contents($source_path);
    // Evaluate the real endpoint with only filesystem roots and time redirected.
    $source = str_replace(array("'/opt/scripta/etc'", "'/opt/scripta/var'", "'/run/blakestream'", '__DIR__', 'time()'),
        array(var_export($tmp . '/etc', true), var_export($tmp . '/sd', true),
            var_export($tmp . '/ram', true), var_export($www, true), (string)$now), $source);
    session_save_path($tmp);
    session_id('history-test-' . getmypid());
    session_start();
    $_SESSION['_logged_'] = $argv[7] === 'yes';
    session_write_close();
    $_REQUEST = array('action' => $action);
    if ($hours !== 'default') $_REQUEST['hours'] = $hours;
    chdir($www);
    eval(substr($source, 5));
    exit;
}

$tmp = sys_get_temp_dir() . '/bs-history-' . getmypid() . '-' . mt_rand(1000, 9999);
history_expect(mkdir($tmp, 0700), 'create test directory');
foreach (array('etc', 'ram', 'sd') as $dir) history_expect(mkdir($tmp . '/' . $dir, 0700), 'create fixture root');
register_shutdown_function(function() use ($tmp) {
    $items = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($tmp, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::CHILD_FIRST);
    foreach ($items as $item) {
        if ($item->isDir()) rmdir($item->getPathname());
        else unlink($item->getPathname());
    }
    rmdir($tmp);
});
$endpoint = dirname(__DIR__) . '/overlay/var/www/f_blakestream.php';

function history_worker($tmp, $endpoint, $hours, $action, $now, $limit = '128M', $auth = 'yes', $cgi_buffer = null) {
    $worker_args = array(__FILE__, '--worker', $tmp, (string)$hours, $action, (string)$now, $endpoint, $auth);
    $args = array(PHP_BINARY, '-d', 'memory_limit=' . $limit, '-d', 'output_buffering=4096',
        '-d', 'display_errors=stderr', '-d', 'date.timezone=UTC', __FILE__, '--worker', $tmp,
        (string)$hours, $action, (string)$now, $endpoint, $auth);
    $env = null;
    if ($cgi_buffer !== null) {
        $args = array(dirname(PHP_BINARY) . '/php-cgi', '-d', 'memory_limit=' . $limit,
            '-d', 'output_buffering=' . $cgi_buffer, '-d', 'display_errors=stderr', '-d', 'date.timezone=UTC');
        $env = array('SCRIPT_FILENAME' => __FILE__, 'REQUEST_METHOD' => 'GET', 'REDIRECT_STATUS' => '200',
            'SERVER_PROTOCOL' => 'HTTP/1.1', 'BS_HISTORY_TEST_ARGS' => json_encode($worker_args));
    }
    $command = implode(' ', array_map('escapeshellarg', $args));
    $start = microtime(true);
    $process = proc_open($command, array(0 => array('pipe', 'r'),
        1 => array('file', $tmp . '/out', 'w'), 2 => array('file', $tmp . '/err', 'w')), $pipes, null, $env);
    history_expect(is_resource($process), 'start isolated PHP worker');
    fclose($pipes[0]);
    $rc = proc_close($process);
    $errors = trim(file_get_contents($tmp . '/err'));
    $stats = json_decode($errors, true);
    clearstatcache(true, $tmp . '/out');
    $result = array('rc' => $rc, 'stats' => $stats, 'errors' => $errors,
        'sha256' => hash_file('sha256', $tmp . '/out'), 'bytes' => filesize($tmp . '/out'),
        'seconds' => round(microtime(true) - $start, 3));
    if ($cgi_buffer !== null) {
        $parts = explode("\r\n\r\n", file_get_contents($tmp . '/out'), 2);
        history_expect(count($parts) === 2, 'CGI headers present');
        $result['headers'] = $parts[0];
        $result['sha256'] = hash('sha256', $parts[1]);
        $result['bytes'] = strlen($parts[1]);
    }
    return $result;
}

function history_success($result, $label) {
    history_expect($result['rc'] === 0 && is_array($result['stats']), $label . ': ' . $result['errors']);
}

function history_cgi_checks($tmp, $endpoint, $now, $expected) {
    if (!is_executable(dirname(PHP_BINARY) . '/php-cgi')) {
        echo "SKIP CGI checks: php-cgi is not installed beside PHP CLI\n";
        return;
    }
    foreach (array('4096', 'On') as $buffer) {
        $cgi = history_worker($tmp, $endpoint, 168, 'load_hashrate_history', $now, '16M', 'yes', $buffer);
        history_success($cgi, 'CGI buffer ' . $buffer);
        history_expect($cgi['sha256'] === $expected['sha256'], 'CGI body byte identity');
        history_expect(stripos($cgi['headers'], 'Content-Type: application/json') !== false, 'CGI JSON content type');
        history_expect($cgi['stats']['peak_bytes'] < 8 * 1024 * 1024, 'CGI bounded peak');
        history_expect($cgi['stats']['buffered_bytes'] <= 16384, 'CGI does not retain full response');
        echo 'CGI buffer=', $buffer, ' ', json_encode($cgi), "\n";
    }
    $cgi = history_worker($tmp, $endpoint, 168, 'load_hashrate_history', $now, '16M', 'no', 'On');
    history_success($cgi, 'CGI auth');
    history_expect($cgi['stats']['status'] === 401 && strpos($cgi['headers'], 'Status: 401') !== false &&
        $cgi['sha256'] === hash('sha256', '{"error":"not logged in"}'), 'CGI auth rejection unchanged');
}

// Optional real-fixture comparison uses the actual prior endpoint, not the
// oracle above. Freeze time at fixture updated so the retained week is covered.
if (isset($argv[1]) && $argv[1] === '--compare') {
    history_expect(isset($argv[3]), '--compare HISTORY_JSON PRIOR_ENDPOINT_PHP [CLOCK_JSON]');
    copy($argv[2], $tmp . '/ram/hashrate-history.json');
    $handle = fopen($argv[2], 'rb');
    $index = bs_hashrate_history_index(new BSHistoryJsonReader($handle));
    fclose($handle);
    $now = isset($index['updated']) ? (int)$index['updated'] : time();
    unset($index);
    file_put_contents($tmp . '/etc/scripta.conf', '{"userChartsSaveTo":"RAM"}');
    $old = history_worker($tmp, $argv[3], 168, 'load_hashrate_history', $now);
    history_success($old, 'prior real hashrate endpoint');
    $new = history_worker($tmp, $endpoint, 168, 'load_hashrate_history', $now, '16M');
    history_success($new, 'streamed real hashrate endpoint');
    history_expect($old['sha256'] === $new['sha256'], 'real hashrate payload byte identity');
    echo 'REAL hashrate: ', json_encode(array('prior' => $old, 'streamed' => $new)), "\n";
    history_cgi_checks($tmp, $endpoint, $now, $new);
    if (isset($argv[4])) {
        copy($argv[4], $tmp . '/ram/clock-history-sd-buffer.json');
        file_put_contents($tmp . '/etc/scripta.conf', '{"userPersistClockHistory":true}');
        $old = history_worker($tmp, $argv[3], 168, 'load_clock_history', $now, '256M');
        history_success($old, 'prior real clock endpoint');
        $new = history_worker($tmp, $endpoint, 168, 'load_clock_history', $now, '256M');
        history_success($new, 'current real clock endpoint');
        history_expect($old['sha256'] === $new['sha256'], 'real clock payload byte identity');
        echo 'REAL clock compatibility: ', json_encode(array('prior' => $old, 'current' => $new)), "\n";
    }
    exit;
}

$now = 1800000000;
$cutoff = $now - 3600;
$names = array('pool.example:3333' => 'Friendly pool', 'stratum+tcp://pool.example:3333' => 'Stratum pool');
$fixture = array('updated' => $now, 'step_seconds' => 30, 'boards' => array(
    array('id' => 2, 'pool_url' => 'https://quota:3;pool.example:3333', 'enabled' => false,
        'primary_pool_no' => 0, 'effective_pool_no' => 2, 'failover_active' => true,
        'samples' => array(array($cutoff - 1, 9), array($cutoff, 10.9), array($now, '42'),
            array($cutoff + 1, null), array($now, 0), array($now), null),
        'accepted_samples' => array(array($cutoff - 1, 5), array($cutoff, null), array($now, 42.125),
            array($cutoff + 1, '2.5'), array($now, 0), array($now, null, 'extra'))),
    array('id' => 0, 'pool_url' => 'stratum+tcp://pool.example:3333', 'samples' => array()),
    array('id' => '1', 'pool_url' => 'HTTP://quota:3;pool.example:3333', 'enabled' => null,
        'samples' => null, 'accepted_samples' => false),
));

$cases = array('normal' => json_encode($fixture), 'pretty' => json_encode($fixture, JSON_PRETTY_PRINT),
    'empty' => '{}', 'null' => 'null', 'array' => '[]', 'scalar' => 'true', 'number' => '42',
    'missing-fields' => '{"boards":[{},null,false,[],{"id":5}],"updated":null,"step_seconds":null}',
    'object-collections' => '{"boards":{"z":{"id":2,"samples":{"x":[1800000000,1],"y":[1800000000,2],"x":[1800000000,3]},"accepted_samples":{"0":{"0":1800000000,"1":null}}},"a":{"id":0}}}',
    'duplicate-fields' => '{"boards":[{"id":9}],"updated":1,"boards":[{"samples":[],"id":8,"id":2,"samples":[[1800000000,8]]}],"updated":2}',
    'duplicate-board-key' => '{"boards":{"x":{"id":9},"y":{"id":0},"x":{"id":1}}}',
    'escapes' => json_encode(array('ignored' => array('samples' => '[},' . chr(34) . chr(92),
        'unicode' => json_decode('"\uD834\uDD1E"')), 'boards' => array(array('id' => 0,
        'pool_url' => 'http://quota:1;pool.example:3333')))),
    'ties' => '{"boards":[{"id":1,"pool_url":"a"},{"id":1,"pool_url":"b"},{"id":0},{"id":1,"pool_url":"c"}]}');
$bad = array('', '{', '{"boards":[{}]', '{"boards":[]} trailing', '{"boards":[],}',
    '{"boards":[{},]}', '{"boards":[{} {}]}', '{"boards":[],"ignored":01}',
    '{"boards":[],"ignored":NaN}', '{"boards":[],"ignored":"\q"}',
    '{"boards":[],"ignored":"\uD800"}', "{\"boards\":[],\"ignored\":\"\xff\"}",
    '{"boards":[],"ignored":"unterminated}', '{"boards":[],"ignored":true false}');
foreach ($bad as $i => $raw) {
    $cases['invalid-' . $i] = str_replace('"boards":[]', '"updated":123,"boards":[{"id":6}]', $raw);
}
foreach (array(127, 128, 129, 255, 256, 257) as $size) {
    $pairs = array();
    for ($i = 0; $i < $size; $i++) $pairs[] = array($now - $i, $i % 3 ? $i * 1.125 : null);
    $cases['batch-' . $size] = json_encode(array('boards' => array(array(
        'id' => 0, 'samples' => $pairs, 'accepted_samples' => $pairs))));
}
$cases['invalid-late-batch'] = '{"boards":[{"id":0,"samples":[' .
    str_repeat('[1800000000,1],', 300) . '[1800000000,01]]}]}';
foreach (array(509, 510, 511, 512) as $depth) {
    $cases['depth-' . $depth] = '{"updated":7,"ignored":' . str_repeat('[', $depth) . '0' . str_repeat(']', $depth) . '}';
}
foreach (range(8170, 8198) as $length) {
    $cases['chunk-' . $length] = json_encode(array('ignored' => str_repeat('x', $length) . '"\\end', 'boards' => $fixture['boards']));
}
$path = $tmp . '/ram/hashrate-history.json';
foreach ($cases as $label => $raw) {
    if (strpos($label, 'invalid-') !== 0 && strpos($label, 'depth-') !== 0) {
        json_decode($raw, true);
        history_expect(json_last_error() === JSON_ERROR_NONE, 'valid fixture: ' . $label);
    }
    file_put_contents($path, $raw);
    ob_start();
    bs_hashrate_history_emit($path, 1, $cutoff, $names);
    $actual = ob_get_clean();
    history_expect($actual === history_legacy($raw, 1, $cutoff, $names), 'byte identity: ' . $label);
}
foreach (array($tmp . '/missing', $tmp . '/ram') as $missing) {
    ob_start();
    bs_hashrate_history_emit($missing, 1, $cutoff, $names);
    history_expect(ob_get_clean() === history_legacy('{}', 1, $cutoff, $names), 'unreadable history fallback');
}

$error_log = $tmp . '/history-errors.log';
ini_set('error_log', $error_log);
foreach (array('"' . str_repeat('private', 10000) . '"',
               '[' . str_repeat('0,', 35000) . '0]') as $large) {
    file_put_contents($path, '{"boards":[{"id":0,"pool_url":' . $large . '}]}');
    ob_start();
    bs_hashrate_history_emit($path, 1, $cutoff, $names);
    $oversized_output = ob_get_clean();
    history_expect($oversized_output === history_legacy('{}', 1, $cutoff, $names),
        'oversized metadata fallback (' . strlen($large) . ' input bytes, ' . strlen($oversized_output) . ' output bytes)');
}
$errors = file_get_contents($error_log);
history_expect(strpos($errors, 'History value too large') !== false &&
    strpos($errors, 'private') === false, 'bounded metadata logs reason without contents');
ini_restore('error_log');

// A collector rename after indexing must not change the descriptor's samples.
file_put_contents($path, json_encode($fixture));
$handle = fopen($path, 'rb');
$reader = new BSHistoryJsonReader($handle);
$index = bs_hashrate_history_index($reader);
file_put_contents($tmp . '/replacement', '{}');
rename($tmp . '/replacement', $path);
ob_start();
bs_hashrate_history_samples($reader, $index['boards'][0]['accepted_samples'], $cutoff, true);
$samples = ob_get_clean();
fclose($handle);
$expected = json_decode(history_legacy(json_encode($fixture), 1, $cutoff, $names), true);
history_expect('[' . $samples . ']' === json_encode($expected['boards'][2]['accepted_samples']), 'atomic replacement snapshot');

// Full dispatch verifies defaults, selected RAM/SD source, labels, and auth.
file_put_contents($path, json_encode($fixture));
file_put_contents($tmp . '/sd/hashrate-history.json', '{"boards":[{"id":7}],"updated":7}');
file_put_contents($tmp . '/etc/saved-pools.json', json_encode(array('categories' => array(array('pools' => array(
    array('url' => 'pool.example:3333', 'name' => 'old label'),
    array('url' => 'pool.example:3333', 'name' => 'Friendly pool'),
    array('url' => 'stratum+tcp://pool.example:3333', 'name' => 'Stratum pool')))))));
foreach (array('default', '0', '-9', 'invalid', '1', '24', '168') as $hours) {
    $result = history_worker($tmp, $endpoint, $hours, 'load_hashrate_history', $now, '16M');
    history_success($result, 'RAM dispatch ' . $hours);
    $h = $hours === 'default' ? 1 : max(1, (int)$hours);
    history_expect(file_get_contents($tmp . '/out') === history_legacy(json_encode($fixture), $h, $now - ($h * 3600), $names),
        'RAM hours and labels ' . $hours);
}
foreach (array('SD', 'anything-except-RAM') as $storage) {
    file_put_contents($tmp . '/etc/scripta.conf', json_encode(array('userChartsSaveTo' => $storage)));
    $result = history_worker($tmp, $endpoint, 168, 'load_hashrate_history', $now, '16M');
    history_success($result, 'SD dispatch');
    history_expect(file_get_contents($tmp . '/out') === history_legacy(file_get_contents($tmp . '/sd/hashrate-history.json'), 168, $now - 604800, $names), 'selected SD file only');
}
$result = history_worker($tmp, $endpoint, 168, 'load_hashrate_history', $now, '16M', 'no');
history_success($result, 'auth dispatch');
history_expect($result['stats']['status'] === 401 && file_get_contents($tmp . '/out') === '{"error":"not logged in"}', 'unchanged auth rejection');

// Seven days for three boards: all 60,480 samples must survive. Build the
// fixture incrementally so the generator itself does not inflate peak memory.
file_put_contents($tmp . '/etc/scripta.conf', '{"userChartsSaveTo":"RAM"}');
unlink($tmp . '/etc/saved-pools.json');
$handle = fopen($path, 'wb');
fwrite($handle, '{"boards":[');
foreach (array(2, 0, 1) as $i => $board) {
    fwrite($handle, ($i ? ',' : '') . '{"id":' . $board . ',"samples":[');
    foreach (array(false, true) as $accepted) {
        if ($accepted) fwrite($handle, '],"accepted_samples":[');
        for ($n = 0; $n < 10080; $n++) {
            fwrite($handle, ($n ? ',' : '') . json_encode(array($now - 604800 + $n * 60,
                $accepted ? ($n % 7 ? 12345.125 : null) : 23456)));
        }
    }
    fwrite($handle, ']}');
}
fwrite($handle, '],"updated":' . $now . ',"step_seconds":60}');
fclose($handle);
$old = history_worker($tmp, 'legacy', 168, 'load_hashrate_history', $now);
history_success($old, 'seven-day legacy oracle');
$new = history_worker($tmp, $endpoint, 168, 'load_hashrate_history', $now, '16M');
history_success($new, 'seven-day endpoint at 16 MiB');
history_expect($old['sha256'] === $new['sha256'], 'seven-day payload byte identity');
history_expect($new['stats']['peak_bytes'] < 8 * 1024 * 1024, 'streaming peak below 8 MiB');
history_expect($old['stats']['peak_bytes'] > $new['stats']['peak_bytes'] * 3, 'material memory reduction');
echo 'PASS ', count($cases), ' JSON fixtures, atomic rename, auth, RAM/SD dispatch, seven-day byte identity', "\n";
echo 'MEMORY ', json_encode(array('fixture_bytes' => filesize($path), 'legacy' => $old, 'streamed' => $new)), "\n";
history_cgi_checks($tmp, $endpoint, $now, $new);
