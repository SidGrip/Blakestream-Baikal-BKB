<?php
require_once __DIR__ . '/../overlay/var/www/inc/diagnostic-logs.php';
function expect($ok, $message) { if (!$ok) { fwrite(STDERR, "FAIL $message\n"); exit(1); } }
$source = file_get_contents(__DIR__ . '/../overlay/var/www/f_dev.php');
function load_section($start, $end) {
    global $source;
    $a = strpos($source, $start); $b = strpos($source, $end, $a);
    expect($a !== false && $b > $a, 'function section exists');
    return substr($source, $a, $b - $a);
}
eval(load_section('function dev_recorder_specs()', 'function recorder_file_status('));
eval(load_section('function normalize_log_target(', 'function sgminer_log_paths_for_target('));
expect(sgminer_log_target(array()) === 'RAM', 'missing sgminer destination defaults to RAM');
foreach (array(false, true) as $dev) {
    expect(sgminer_log_target(array('userDev' => $dev, 'userDevLogTarget' => 'SD')) === 'SD', 'SD choice independent of visibility');
}
foreach (array(null, '', 'invalid', array()) as $target) {
    expect(normalize_log_target($target) === 'RAM', 'invalid log target cannot select SD');
}
eval(load_section('function recorder_pool_waiting(', 'function recorder_status_rows('));
expect(recorder_pool_waiting(-1, 300, true, 10, ''), 'first boot waits before miner starts');
expect(recorder_pool_waiting(-1, 300, true, 7200, '20'), 'first miner start waits even after a delayed pool setup');
expect(recorder_pool_waiting(-1, 300, true, 7200, '360'), 'wait includes one scheduler tick');
expect(!recorder_pool_waiting(-1, 300, true, 7200, '361'), 'missing history becomes overdue after startup grace');
expect(!recorder_pool_waiting(901, 300, true, 20, '20'), 'existing stale history is not hidden by startup grace');
expect(!recorder_pool_waiting(0, 300, true, 20, '20'), 'first sample ends waiting');
expect(!recorder_pool_waiting(-1, 300, false, 20, '20'), 'stopped scheduler is not a normal wait');
expect(!recorder_pool_waiting(-1, 300, true, '', ''), 'missing uptime cannot grant unlimited grace');
expect(!recorder_pool_waiting(-1, 300, true, -1, 'unavailable'), 'invalid age cannot grant grace');
$health = load_section("case 'recorder_health':", "case 'recorder_run':");
expect(strpos($health, "if ($" . "err)") < strpos($health, 'recorder_pool_waiting('), 'reported errors take priority over waiting');
$present = array();
function fixture_is_file($path) { global $present; return in_array($path, $present, true); }
function storage_stat($name, $path, $cap = 0) {
    return array('name'=>$name, 'path'=>$path, 'size'=>fixture_is_file($path) ? 50 : 0, 'present'=>fixture_is_file($path), 'cap'=>$cap);
}
function storage_df($path) { return array(); }
function bs_measured_read() { return array('target'=>'RAM', 'bytes'=>0); }
$dev_activity_csrf = 'fixture';
eval(str_replace('is_file($path)', 'fixture_is_file($path)', load_section('function storage_recorder_files(', '// ---- Firmware /')));
$report = storage_report();
foreach (array('ram','sd') as $side) {
    $paths = array(); $ids = array();
    foreach ($report[$side] as $row) {
        if ($row['name'] === 'sgminer.log') expect($row['cap'] === 4194304, 'sgminer cap matches on both destinations');
        expect(!in_array($row['path'], $paths, true), 'no duplicate file path');
        $paths[] = $row['path'];
        if (isset($row['recorder_id'])) $ids[] = $row['recorder_id'];
    }
    sort($ids); expect($ids === array('clock','failover','hashrate','pool'), 'every recorder mapped on '.$side);
    foreach (bs_diagnostic_logs() as $name => $spec) {
        $found = array_values(array_filter($report[$side], function($row) use ($name) { return $row['name'] === $name; }));
        expect(count($found) === 1, 'one diagnostic row on '.$side.': '.$name);
        expect($found[0]['target'] === 'RAM' && $found[0]['target_key'] === $name, 'diagnostics default to RAM');
    }
}
$present[] = '/run/blakestream/clock-history-sd-buffer.json';
$report = storage_report();
$buffers = array_values(array_filter($report['ram'], function($row) {return $row['name']==='clock-history-sd-buffer.json';}));
expect(count($buffers) === 1 && $buffers[0]['recorder_id']==='clock', 'clock buffer included when present');
expect($buffers[0]['present'] && $report['ram_total_bytes']===50, 'buffer storage accounted for');
expect(!isset($buffers[0]['target_key']), 'buffer is not a separate configurable destination');
$added = storage_recorder_files(array(), '/opt/scripta/var/');
expect(count($added) === 4, 'missing recorder histories automatically added');
foreach ($added as $row) expect(isset($row['recorder_id']), 'new history maps back to recorder');
echo "PASS all recorder histories mapped to storage rows, existing clock buffer included, no duplicate files\n";
