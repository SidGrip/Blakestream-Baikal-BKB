<?php

require_once __DIR__ . '/../overlay/var/www/inc/clock-log.php';

function fail_test($message) {
    fwrite(STDERR, "FAIL: " . $message . "\n");
    exit(1);
}

function expect($cond, $msg) {
    if (!$cond) fail_test($msg);
}

$tz_prev = date_default_timezone_get();
$tmp = sys_get_temp_dir() . '/bs-clock-log-' . getmypid() . '-' . mt_rand(1000, 9999);
expect(mkdir($tmp, 0700), 'temp dir');
register_shutdown_function(function () use ($tmp, $tz_prev) {
    date_default_timezone_set($tz_prev);
    if (!is_dir($tmp)) return;
    $items = scandir($tmp);
    if (is_array($items)) {
        foreach ($items as $item) {
            if ($item === '.' || $item === '..') continue;
            $path = $tmp . '/' . $item;
            if (is_file($path)) @unlink($path);
        }
    }
    @rmdir($tmp);
});

function write_json($dir, $name, $data) {
    $path = $dir . '/' . $name;
    $json = json_encode($data);
    if ($json === false) fail_test('fixture encode failed for ' . $name);
    if (file_put_contents($path, $json) === false) fail_test('fixture write failed for ' . $name);
    return $path;
}

function record_lines($text) {
    $out = array();
    foreach (explode("\n", (string) $text) as $line) {
        if ($line !== '' && $line[0] !== '#') $out[] = $line;
    }
    return $out;
}

function assert_contract($view, $ok, $label) {
    expect(is_array($view), $label . ' returns an array');
    foreach (array('ok', 'text', 'error', 'format', 'size', 'shown', 'truncated', 'rows') as $key) {
        expect(array_key_exists($key, $view), $label . ' missing ' . $key);
    }
    expect(is_bool($view['ok']) && $view['ok'] === $ok, $label . ' ok');
    expect(is_string($view['text']), $label . ' text type');
    expect(is_string($view['error']), $label . ' error type');
    expect($view['format'] === 'readable', $label . ' format');
    expect(is_int($view['size']) && $view['size'] >= 0, $label . ' size');
    expect(is_int($view['shown']) && $view['shown'] === strlen($view['text']), $label . ' shown');
    expect(is_bool($view['truncated']), $label . ' truncated type');
    expect(is_int($view['rows']) && $view['rows'] >= 0, $label . ' rows');
    expect(count(record_lines($view['text'])) === $view['rows'], $label . ' row lines');
    if ($ok) {
        expect($view['error'] === '', $label . ' error empty');
    } else {
        expect($view['error'] !== '', $label . ' error set');
        expect($view['text'] === '', $label . ' failed text empty');
        expect($view['rows'] === 0 && $view['shown'] === 0, $label . ' failed shows nothing');
    }
}

function assert_no_leak($view, $marker, $label) {
    $blob = $view['text'] . "\n" . $view['error'];
    expect(strpos($blob, $marker) === false, $label . ' leaked file bytes');
}

$helper = file_get_contents(__DIR__ . '/../overlay/var/www/inc/clock-log.php');
expect(is_string($helper) && $helper !== '', 'helper source');
expect(strpos($helper, 'json_decode') !== false, 'uses json_decode');
expect(strpos($helper, 'fseek') === false, 'does not tail with fseek');
expect(strpos($helper, 'inet_pton') === false && strpos($helper, 'inet_ntop') === false, 'no IP parser');
expect(strpos($helper, '},{') === false, 'does not rewrite a JSON tail');
$view_at = strpos($helper, 'function bs_clock_log_view');
$size_at = strpos($helper, 'filesize', $view_at);
$read_at = strpos($helper, 'file_get_contents', $view_at);
expect($view_at !== false && $size_at !== false && $read_at > $size_at, 'size is checked before read');
expect(strpos(substr($helper, $size_at, $read_at - $size_at), 'byte read limit') !== false, 'over-limit refusal precedes read');
expect(bs_clock_log_max_bytes() === 2097152, '2 MiB byte cap');
expect(bs_clock_log_max_rows() === 300, '300 record cap');
expect(gmdate('Y-m-d\\TH:i:s\\Z', 1609459200) === '2021-01-01T00:00:00Z', 'UTC fixture instant');

date_default_timezone_set('Pacific/Auckland');

$missing_path = bs_clock_log_view($tmp . '/absent.json');
assert_contract($missing_path, false, 'absent');
expect(strpos($missing_path['error'], 'not readable') !== false, 'absent error');

$normal_path = write_json($tmp, 'normal.json', array(
    'version' => 2,
    'boards' => array(
        array(
            'id' => 0,
            'samples' => array(array(
                't' => 1609459200,
                'cm' => 'global',
                'xp' => 0,
                'pi' => 0,
                'scc' => 30,
                'c' => 480,
                'rc' => 400,
                'cv' => 1,
                'tmp' => 45.5,
                'fan' => 80,
                'a' => 10,
                'da' => 1234.5,
                'r' => 2,
                'hw' => 1,
                'en' => 1,
                'st' => 'Alive',
                'm5' => 99999,
                'ma' => 88888,
            )),
        ),
        array(
            'id' => 1,
            'samples' => array(array(
                't' => 1609459200,
                'cm' => 'individual',
                'xp' => 1,
                'pi' => 22,
                'scc' => 22,
                'c' => 400,
                'rc' => 400,
                'cv' => true,
                'tmp' => 50,
                'fan' => 90,
                'a' => 4,
                'da' => 10,
                'r' => 0,
                'hw' => 0,
            )),
        ),
    ),
));
$normal = bs_clock_log_view($normal_path);
assert_contract($normal, true, 'normal');
expect($normal['size'] === filesize($normal_path), 'normal size');
expect($normal['truncated'] === false, 'normal not truncated');
expect($normal['rows'] === 2, 'normal rows');
$normal_lines = record_lines($normal['text']);
expect(strpos($normal_lines[0], '2021-01-01T00:00:00Z board=0 ') === 0, 'normal board order');
expect(strpos($normal_lines[1], '2021-01-01T00:00:00Z board=1 ') === 0, 'experimental follows same timestamp');
expect(strpos($normal_lines[0], 'mode=global') !== false, 'normal mode');
expect(strpos($normal_lines[0], 'experimental=0') !== false, 'normal experimental flag is a real zero');
expect(strpos($normal_lines[0], 'requested_clock_index=none(normal)') !== false, 'normal sentinel is not a requested clock index');
expect(strpos($normal_lines[0], 'sent_clock_index=30') !== false, 'normal sent Index');
expect(strpos($normal_lines[0], 'command=verified') !== false, 'normal command verified');
expect(strpos($normal_lines[0], 'requested_label=400') !== false, 'normal requested label');
expect(strpos($normal_lines[0], 'echo=480 (bridge echo, not physical MHz)') !== false, 'normal echo is not MHz');
expect(strpos($normal_lines[0], 'temp=45.5') !== false, 'normal temp');
expect(strpos($normal_lines[0], 'fan=80') !== false, 'normal fan');
expect(strpos($normal_lines[0], 'accepted=10') !== false, 'normal accepted');
expect(strpos($normal_lines[0], 'difficulty=1234.5') !== false, 'normal difficulty');
expect(strpos($normal_lines[0], 'rejected=2') !== false, 'normal rejected');
expect(strpos($normal_lines[0], 'hw=1') !== false, 'normal hw');
expect(strpos($normal_lines[0], 'Alive') === false, 'status text is not dumped');
expect(strpos($normal_lines[0], '99999') === false && strpos($normal_lines[0], '88888') === false, 'no synthetic hashrate');
expect(stripos($normal['text'], 'hashrate') === false || strpos($normal['text'], 'No hashrate is derived.') !== false, 'hashrate is not calculated');
expect(strpos($normal['text'], 'MH/s') === false, 'no hashrate unit');
expect(substr_count(strtolower($normal['text']), 'hashrate') === 1, 'hashrate appears only in the disclaimer');
expect(strpos($normal_lines[1], 'mode=individual') !== false, 'experimental mode');
expect(strpos($normal_lines[1], 'experimental=1') !== false, 'experimental flag');
expect(strpos($normal_lines[1], 'requested_clock_index=22') !== false, 'experimental requested Index');
expect(strpos($normal_lines[1], 'sent_clock_index=22') !== false, 'experimental sent Index');
expect(strpos($normal_lines[1], 'command=verified') !== false, 'bool cv true is verified');
expect(strpos($normal_lines[1], 'echo=400 (bridge echo, not physical MHz)') !== false, 'experimental echo label');
expect(strpos($normal_lines[1], 'rejected=0') !== false && strpos($normal_lines[1], 'hw=0') !== false, 'experimental zeros stay zeros');
expect(substr_count($normal['text'], 'MHz') === 3, 'disclaimer once in the header and once per row');

$zeros_path = write_json($tmp, 'zeros.json', array(
    'version' => '2',
    'boards' => array(array(
        'id' => 0,
        'samples' => array(array(
            't' => 0,
            'cm' => 'global',
            'xp' => 0,
            'pi' => 0,
            'scc' => 0,
            'c' => 0,
            'rc' => 0,
            'cv' => 0,
            'tmp' => 0,
            'fan' => 0,
            'a' => 0,
            'da' => 0,
            'r' => 0,
            'hw' => 0,
        )),
    )),
));
$zeros = bs_clock_log_view($zeros_path);
assert_contract($zeros, true, 'zeros');
expect($zeros['rows'] === 1 && $zeros['truncated'] === false, 'zeros one row');
$zero_line = record_lines($zeros['text']);
expect(count($zero_line) === 1, 'zeros single record');
$zero_line = $zero_line[0];
expect(strpos($zero_line, '1970-01-01T00:00:00Z board=0 ') === 0, 'epoch zero is real');
foreach (array(
    'mode=global',
    'experimental=0',
    'requested_clock_index=none(normal)',
    'sent_clock_index=0',
    'command=unverified',
    'requested_label=0',
    'echo=0 (bridge echo, not physical MHz)',
    'temp=0',
    'fan=0',
    'accepted=0',
    'difficulty=0',
    'rejected=0',
    'hw=0',
) as $token) {
    expect(strpos($zero_line, $token) !== false, 'zeros missing ' . $token);
}
expect(strpos($zero_line, 'n/a') === false, 'explicit zeros are not n/a');

$missing = bs_clock_log_view(write_json($tmp, 'missing.json', array(
    'version' => 2,
    'boards' => array(array(
        'id' => 3,
        'samples' => array(array('t' => 1609459200)),
    )),
)));
assert_contract($missing, true, 'missing');
expect($missing['rows'] === 1, 'missing still shows the sample');
$missing_line = record_lines($missing['text']);
$missing_line = $missing_line[0];
expect(strpos($missing_line, '2021-01-01T00:00:00Z board=3 ') === 0, 'missing keeps time and board');
expect(strpos($missing_line, '=0') === false, 'missing fields are not zero');
foreach (array(
    'mode=n/a',
    'experimental=n/a',
    'requested_clock_index=n/a',
    'sent_clock_index=n/a',
    'command=n/a',
    'requested_label=n/a',
    'echo=n/a (bridge echo, not physical MHz)',
    'temp=n/a',
    'fan=n/a',
    'accepted=n/a',
    'difficulty=n/a',
    'rejected=n/a',
    'hw=n/a',
) as $token) {
    expect(strpos($missing_line, $token) !== false, 'missing token ' . $token);
}

$empty = bs_clock_log_view(write_json($tmp, 'empty.json', array('version' => 2, 'boards' => array())));
assert_contract($empty, true, 'empty');
expect($empty['rows'] === 0 && $empty['truncated'] === false, 'empty rows');
expect(strpos($empty['text'], '# no samples') !== false, 'empty note');

$order_base = 1700000000;
$ordered = bs_clock_log_view(write_json($tmp, 'order.json', array(
    'version' => 2,
    'boards' => array(
        array('id' => 10, 'samples' => array(
            array('t' => $order_base + 100, 'a' => 10),
        )),
        array('id' => 2, 'samples' => array(
            array('t' => $order_base, 'a' => 1),
            array('t' => $order_base + 100, 'a' => 5),
            array('t' => $order_base + 100, 'a' => 6),
        )),
    ),
)));
assert_contract($ordered, true, 'order');
expect($ordered['rows'] === 4 && $ordered['truncated'] === false, 'order count');
$order_lines = record_lines($ordered['text']);
$expect_order = array(
    array($order_base, 2, 1),
    array($order_base + 100, 2, 5),
    array($order_base + 100, 2, 6),
    array($order_base + 100, 10, 10),
);
foreach ($expect_order as $i => $spec) {
    $prefix = gmdate('Y-m-d\\TH:i:s\\Z', $spec[0]) . ' board=' . $spec[1] . ' ';
    expect(strpos($order_lines[$i], $prefix) === 0, 'order line ' . $i);
    expect(strpos($order_lines[$i], 'accepted=' . $spec[2] . ' ') !== false, 'order accepted ' . $spec[2]);
}

$cap = bs_clock_log_max_rows();
$cap_n = $cap + 5;
$cap_base = 1700000000;
$cap_samples = array();
for ($i = 0; $i < $cap_n; $i++) {
    $cap_samples[] = array('t' => $cap_base + $i, 'a' => 1000 + $i, 'pi' => 0);
}
$capped = bs_clock_log_view(write_json($tmp, 'recent.json', array(
    'version' => 2,
    'boards' => array(array('id' => 0, 'samples' => $cap_samples)),
)));
assert_contract($capped, true, 'recent');
expect($capped['rows'] === $cap && $capped['truncated'] === true, 'recent window');
expect(strpos($capped['text'], '# showing ' . $cap . ' most recent of ' . $cap_n . ' valid records') !== false, 'recent note');
expect(strpos($capped['text'], '# omitted ') === false, 'row cap is not an omission');
$cap_lines = record_lines($capped['text']);
expect(strpos($cap_lines[0], gmdate('Y-m-d\\TH:i:s\\Z', $cap_base + 5) . ' board=0 ') === 0, 'oldest kept');
expect(strpos($cap_lines[0], 'accepted=1005 ') !== false, 'oldest kept counter');
expect(strpos($cap_lines[$cap - 1], gmdate('Y-m-d\\TH:i:s\\Z', $cap_base + $cap_n - 1) . ' board=0 ') === 0, 'newest kept');
expect(strpos($cap_lines[$cap - 1], 'accepted=' . (1000 + $cap_n - 1) . ' ') !== false, 'newest counter');
expect(strpos($capped['text'], 'accepted=1000 ') === false, 'oldest dropped');
expect(strpos($capped['text'], 'accepted=1004 ') === false, 'last dropped counter');

$many = array();
for ($i = 0; $i < 2000; $i++) $many[] = array('t' => $cap_base + $i, 'a' => $i);
$large = bs_clock_log_view(write_json($tmp, 'many.json', array('version' => 2,
    'boards' => array(array('id' => 0, 'samples' => array_reverse($many))))));
assert_contract($large, true, 'bounded collection');
expect($large['rows'] === 300 && $large['truncated'], 'bounded collection size');
expect(strpos($large['text'], '# showing 300 most recent of 2000 valid records') !== false, 'bounded total count');
$large_lines = record_lines($large['text']);
expect(strpos($large_lines[0], 'accepted=1700 ') !== false, 'bounded newest window start');
expect(strpos($large_lines[299], 'accepted=1999 ') !== false, 'bounded newest window end');

$omitted = bs_clock_log_view(write_json($tmp, 'omissions.json', array(
    'version' => 2,
    'boards' => array(
        'nope',
        array('samples' => array(array('t' => 1609459200, 'a' => 8))),
        array('id' => -1, 'samples' => array(array('t' => 1609459200, 'a' => 8))),
        array('id' => 1, 'samples' => array(
            array('a' => 0),
            array('t' => false, 'a' => 7),
            array('t' => 1.5, 'a' => 9),
            array('t' => '01', 'a' => 11),
            array('t' => -5, 'a' => 12),
            array('t' => true, 'a' => 13),
            array('t' => 1609459200, 'a' => 4, 'pi' => 0),
        )),
        array('id' => 2, 'samples' => 'bad'),
    ),
)));
assert_contract($omitted, true, 'omissions');
expect($omitted['rows'] === 1, 'one valid sample kept');
expect(strpos($omitted['text'], '# omitted 10 invalid board or sample records') !== false, 'omission count');
$omit_lines = record_lines($omitted['text']);
expect(strpos($omit_lines[0], '2021-01-01T00:00:00Z board=1 ') === 0, 'valid sample kept');
expect(strpos($omit_lines[0], 'accepted=4 ') !== false, 'valid counter kept');
expect(strpos($omit_lines[0], 'requested_clock_index=0') !== false, 'valid zero Index kept');
expect(substr_count($omitted['text'], '1970-01-01T00:00:00Z') === 0, 'bad timestamps did not become epoch');
foreach (array('accepted=7 ', 'accepted=8 ', 'accepted=9 ', 'accepted=11 ', 'accepted=0 ') as $dropped) {
    expect(strpos($omitted['text'], $dropped) === false, 'did not keep ' . $dropped);
}

$corrupt_marker = 'SENTINEL-CORRUPT-CLOCK';
$corrupt_path = $tmp . '/corrupt.json';
$corrupt_body = '{"version":2,"boards":[{"id":0,"samples":[{"t":1609459200,"a":3,"c":480}]} ' . $corrupt_marker;
expect(file_put_contents($corrupt_path, $corrupt_body) !== false, 'write corrupt');
$corrupt = bs_clock_log_view($corrupt_path);
assert_contract($corrupt, false, 'corrupt');
expect($corrupt['truncated'] === false, 'corrupt is not a byte truncation');
expect($corrupt['size'] === strlen($corrupt_body), 'corrupt size');
expect(strpos($corrupt['error'], 'could not be decoded') !== false, 'corrupt decode error');
expect(strpos($corrupt['error'], 'raw view') !== false && strpos($corrupt['error'], 'download') !== false, 'corrupt points at raw or download');
expect(strpos($corrupt['error'], 'over the') === false, 'corrupt is not the size error');
assert_no_leak($corrupt, $corrupt_marker, 'corrupt');
assert_no_leak($corrupt, '2021-01-01', 'corrupt prefix');
assert_no_leak($corrupt, 'accepted=3', 'corrupt row');

$deep_marker = 'SENTINEL-DEEP-CLOCK';
$node = array('leak' => $deep_marker);
for ($i = 0; $i < bs_clock_log_max_depth() + 8; $i++) $node = array($node);
$deep = bs_clock_log_view(write_json($tmp, 'deep.json', array(
    'version' => 2,
    'boards' => array(array(
        'id' => 0,
        'samples' => array(array('t' => 1609459200, 'a' => 6)),
    )),
    'trap' => $node,
)));
assert_contract($deep, false, 'deep');
expect($deep['truncated'] === false, 'deep is a decode failure');
expect(strpos($deep['error'], 'depth') !== false, 'deep error names depth');
expect(strpos($deep['error'], 'raw view') !== false, 'deep points at raw');
assert_no_leak($deep, $deep_marker, 'deep');
assert_no_leak($deep, 'accepted=6', 'deep sample');

$limit = bs_clock_log_max_bytes();
$exact_path = $tmp . '/exact.json';
$exact_fh = fopen($exact_path, 'wb');
expect($exact_fh !== false, 'exact open');
expect(fwrite($exact_fh, str_repeat('x', $limit)) === $limit, 'exact write');
fclose($exact_fh);
$exact = bs_clock_log_view($exact_path);
assert_contract($exact, false, 'exact-limit garbage');
expect($exact['size'] === $limit && $exact['truncated'] === false, 'limit itself is still decoded');
expect(strpos($exact['error'], 'could not be decoded') !== false, 'exact-limit decode error');
expect(strpos($exact['error'], 'over the') === false, 'exact-limit is not over');
@unlink($exact_path);

$over_marker = 'SENTINEL-OVERSIZE-CLOCK';
$over_path = $tmp . '/over.json';
$over_fh = fopen($over_path, 'wb');
expect($over_fh !== false, 'over open');
expect(fwrite($over_fh, $over_marker) === strlen($over_marker), 'over marker');
$pad = $limit + 1 - strlen($over_marker);
expect(fwrite($over_fh, str_repeat('y', $pad)) === $pad, 'over pad');
fclose($over_fh);
$over = bs_clock_log_view($over_path);
assert_contract($over, false, 'oversize');
expect($over['size'] === $limit + 1 && $over['truncated'] === true, 'oversize bounded');
expect(strpos($over['error'], 'over the ' . $limit . ' byte read limit') !== false, 'oversize limit text');
expect(strpos($over['error'], 'truncated slice was refused') !== false, 'oversize refuses a slice');
expect(strpos($over['error'], 'raw view') !== false && strpos($over['error'], 'download') !== false, 'oversize points at raw or download');
assert_no_leak($over, $over_marker, 'oversize');
@unlink($over_path);

$bad_version = bs_clock_log_view(write_json($tmp, 'version.json', array(
    'version' => 1,
    'boards' => array(array('id' => 0, 'samples' => array(array('t' => 1609459200, 'a' => 1)))),
)));
assert_contract($bad_version, false, 'version');
expect(strpos($bad_version['error'], 'version') !== false, 'version error');
assert_no_leak($bad_version, 'accepted=1', 'version row');

$list = bs_clock_log_view(write_json($tmp, 'list.json', array(array('id' => 0))));
assert_contract($list, false, 'top-level list');
expect(strpos($list['error'], 'not an object') !== false, 'list rejected');

$no_boards = bs_clock_log_view(write_json($tmp, 'noboards.json', array('version' => 2)));
assert_contract($no_boards, false, 'no boards');
expect(strpos($no_boards['error'], 'boards') !== false, 'boards error');

echo "PASS test-clock-log.php\n";
