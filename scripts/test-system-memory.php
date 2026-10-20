<?php
require_once __DIR__ . '/../overlay/var/www/inc/system-memory.php';

function expect_memory($input, $total, $used, $pct) {
    $actual = system_memory_stats($input);
    if ($actual != array('total_kb' => $total, 'used_kb' => $used, 'used_pct' => $pct)) {
        fwrite(STDERR, 'Unexpected memory stats: ' . json_encode($actual) . "\n");
        exit(1);
    }
}

$legacy = "MemTotal: 200000 kB\nMemFree: 10000 kB\nBuffers: 4000 kB\nCached: 50000 kB\nShmem: 30000 kB\n";
expect_memory($legacy, 200000, 166000, 83);
expect_memory($legacy . "MemAvailable: 40000 kB\n", 200000, 160000, 80);
expect_memory($legacy . "MemAvailable: 0 kB\n", 200000, 200000, 100);
expect_memory("MemTotal: 100 kB\nCached: 10 kB\nShmem: 50 kB\n", 100, 100, 100);
expect_memory("MemTotal: 100 kB\nMemAvailable: 200 kB\n", 100, 0, 0);
expect_memory("MemTotal: 100 kB\nMemFree: 5 kB\nCached: 10 kB\n", 100, 85, 85);
expect_memory(false, 0, 0, 0);

function bs_connection_status() { return array('type' => 'ethernet'); }
$web = realpath(__DIR__ . '/../overlay/var/www');
$source = file_get_contents($web . '/f_blakestream.php');
$start = strpos($source, "    case 'sysstats':");
$end = strpos($source, "    case 'delete_backup':", $start);
$case = substr($source, $start, $end - $start);
$case = str_replace('__DIR__', var_export($web, true), $case);
set_error_handler(function($severity, $message) { throw new Exception($message); });
eval("switch ('sysstats') {\n" . $case . "\n}");
restore_error_handler();
if (!is_numeric($response['mem_available_kb']) ||
    $response['mem_available_kb'] + $response['mem_used_kb'] !== $response['mem_total_kb']) {
    fwrite(STDERR, "sysstats must expose numeric, consistent memory totals\n");
    exit(1);
}
echo "PASS system memory accounting, tmpfs, and zero availability\n";
