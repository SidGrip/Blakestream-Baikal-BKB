<?php
require __DIR__ . '/../overlay/var/www/inc/measured-rates.php';
function expect($ok, $message) {
    if (!$ok) { fwrite(STDERR, "FAIL $message\n"); exit(1); }
}
$old = array('context_id' => 'old', 'context' => array('board' => 0, 'index' => 30, 'startup' => 64, 'total' => 64),
    'last' => 1700000000, 'seconds' => 3600, 'difficulty' => 52020, 'accepted' => 2000,
    'rejected' => 0, 'hw' => 6, 'temp_min' => 39, 'temp_max' => 43, 'fixed32' => true);
$new = $old;
$new['context_id'] = 'new'; $new['context']['board'] = 2; $new['context']['index'] = 32;
$new['last'] += 60; $new['seconds'] = 21600; $new['accepted'] = 10000;
$fixture = array('ok' => true, 'target' => 'RAM', 'updated' => 1700000060, 'last_saved' => 1700000000,
    'pending_intervals' => 12, 'contexts' => array($old, $new), 'salt' => 'private-salt',
    'runtime' => array('password' => 'private-password'),
    'boards' => array(0 => array('context_id' => 'old'), 2 => array('context_id' => 'new')),
    'board_ids' => array(0 => 'installation-0', 1 => 'installation-1', 2 => 'installation-2'));
$before = serialize($fixture);
$report = bs_measured_report($fixture, 'America/Chicago');
expect(strpos($report, 'Storage: RAM') !== false, 'RAM target');
expect(strpos($report, 'Board: All') !== false, 'all-board report labeled');
expect(strpos($report, 'Timezone: America/Chicago') !== false, 'configured timezone named');
expect(strpos($report, '2023-11-14 16:14:20') !== false, 'configured timezone applied');
expect(strpos($report, 'Buffered intervals: 12') !== false, 'pending samples disclosed');
expect(strpos($report, '62.06 GH/s') !== false, 'accepted rate matches table calculation');
expect(strpos($report, '39.0 / 43.0 C') !== false, 'temperature range matches table');
expect(strpos($report, '1h 0m 0s') !== false, 'readable duration');
expect(strpos($report, 'Your history') !== false && strpos($report, 'Early history') !== false, 'history labels');
expect(strpos($report, 'private-') === false, 'private fields omitted');
expect(serialize($fixture) === $before, 'report does not mutate measurements');
$lines = explode("\n", $report);
$rows = array_values(array_filter($lines, function ($line) { return preg_match('/^[0-2] +\|/', $line); }));
expect(count($rows) === 2 && $rows[0][0] === '2' && $rows[1][0] === '0', 'newest-first ordering');
foreach (array('Board', 'Index', 'Chips', 'Observed', 'Accepted Rate', 'Shares', 'Rejects', 'HW', 'Temp Min/Max', 'Last Seen') as $label)
    expect(strpos($report, $label) !== false, 'column ' . $label);
$sd = $fixture; $sd['target'] = 'SD';
expect(bs_measured_report($sd, 'America/Chicago') === str_replace('Storage: RAM', 'Storage: SD', $report),
    'RAM and SD use the same report');
$bad = $fixture;
$bad['ok'] = false; $bad['error'] = "Measured rates are stale\nretry";
$bad['contexts'] = array(array('context' => array('board' => 0, 'index' => 30), 'seconds' => 0,
    'difficulty' => 0, 'accepted' => 0, 'rejected' => 0, 'hw' => 0, 'last' => 1700000000));
$text = bs_measured_report($bad, 'not-a-timezone');
expect(strpos($text, 'Timezone: UTC') !== false, 'invalid timezone falls back to UTC');
expect(strpos($text, 'Status: Measured rates are stale retry') !== false, 'stale/error status explicit');
expect(strpos($text, 'n/a') !== false && strpos($text, 'INF') === false, 'zero duration and missing fields safe');
$bad['contexts'][0]['seconds'] = 60;
expect(strpos(bs_measured_report($bad), '0.00 GH/s') !== false, 'zero work remains a real zero');
$bad['contexts'][0]['seconds'] = array();
$bad['contexts'][0]['difficulty'] = true;
$bad['contexts'][0]['last'] = -1;
expect(strpos(bs_measured_report($bad), 'n/a') !== false, 'invalid values do not fabricate data');
expect(strpos(bs_measured_report(array()), 'No measured history') !== false, 'empty report');

// Exercise the endpoint branch with a fixed snapshot, without controller paths.
$source = file_get_contents(__DIR__ . '/../overlay/var/www/f_dev.php');
$begin = strpos($source, "case 'measured_rates_download':");
$end = strpos($source, "case 'measured_rates_update':", $begin);
$branch = str_replace('bs_measured_read()', '$fixture', substr($source, $begin, $end - $begin));
$cfg = array('userTimezone' => 'America/Chicago');
$run = function ($format, $board = null) use ($branch, &$cfg, $fixture) {
    $_GET = $format === null ? array() : array('format' => $format);
    if ($board !== null) $_GET['board'] = $board;
    http_response_code(200);
    ob_start(); eval('switch ("measured_rates_download") {' . $branch . '}');
    return ob_get_clean();
};
expect($run('readable') === $report, 'readable endpoint returns the report');
$cfg['userShowSystemTime'] = true;
expect(strpos($run('readable'), 'Timezone: UTC') !== false, 'system-time preference uses UTC');
expect(json_decode($run(null), true) === $fixture, 'existing JSON endpoint stays compatible');
foreach (array('html', array('readable')) as $invalid) {
    $result = json_decode($run($invalid), true);
    expect(http_response_code() === 400 && !$result['ok'], 'invalid format rejected');
}
expect(strpos($branch, 'filename="measured-rates') !== false && strpos($branch, 'text/plain; charset=utf-8') !== false,
    'readable download has a text attachment and content type');
expect(strpos($branch, 'bs_measured_command') === false, 'download never flushes or clears');
expect(json_decode($run('json', 'all'), true) === $fixture, 'explicit All preserves JSON compatibility');
foreach (array('0', '1', '2') as $board) {
    $filtered = json_decode($run('json', $board), true);
    expect($filtered['board_filter'] === $board, 'JSON names selected board');
    expect(count($filtered['contexts']) === ($board === '1' ? 0 : 1), 'JSON history follows selection');
    foreach ($filtered['contexts'] as $c) expect((string)$c['context']['board'] === $board, 'no other-board rows');
    foreach (array('boards', 'board_ids') as $key)
        foreach (array_keys($filtered[$key]) as $id) expect((string)$id === $board, 'other-board metadata excluded');
    $text = $run('readable', $board);
    expect(strpos($text, 'Board: ' . $board . "\n") !== false, 'text names selected board');
    $selected = array_values(array_filter(explode("\n", $text), function ($line) { return preg_match('/^[0-2] +\|/', $line); }));
    expect(count($selected) === count($filtered['contexts']), 'text and JSON select same rows');
    foreach ($selected as $row) expect($row[0] === $board, 'text excludes other boards');
    if ($board === '1') expect(strpos($text, 'No measured history') !== false, 'empty board report is explicit');
}
foreach (array('', '3', '-1', '00', '0.0', 'ALL', array('0'), false, 0) as $invalid) {
    $response = json_decode($run('readable', $invalid), true);
    expect(http_response_code() === 400 && !$response['ok'], 'invalid board selection rejected');
}
expect(serialize($fixture) === $before, 'filtering never alters source history');
$template = file_get_contents(__DIR__ . '/../overlay/var/www/partials/dev-tools.html');
expect(preg_match('/<span title="Save buffered measurements to the selected RAM or SD storage now\.">\s*(<button[^>]+>.*?Write buffer<\/button>)\s*<\/span>/s', $template, $writeBuffer) === 1,
    'write-buffer explanation wraps the button so it is available while disabled');
expect(strpos($writeBuffer[1], 'aria-label="Write buffer: save buffered measurements') !== false,
    'write-buffer explanation is available to assistive technology');
expect(strpos($writeBuffer[1], 'ng-disabled="devMeasuredBusy || !devMeasured.unsaved"') !== false &&
    strpos($writeBuffer[1], 'ng-click="devMeasuredUpdate(\'flush\')"') !== false,
    'write-buffer tooltip preserves the flush action and disabled guards');
echo "PASS measured-rate readable report, ordering, units, timezone, privacy and download formats\n";
