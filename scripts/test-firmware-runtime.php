<?php
$source = file_get_contents(__DIR__ . '/../overlay/var/www/f_dev.php');
$start = strpos($source, 'function firmware_runtime_path()');
$end = strpos($source, 'function firmware_job_paths()', $start);
if ($start === false || $end === false) throw new Exception('timing helpers missing');
eval(substr($source, $start, $end - $start));
function read_json($path, $default = array()) {
    $data = json_decode(@file_get_contents($path), true);
    return $data === null ? $default : $data;
}
function expect_runtime($condition, $message) {
    if (!$condition) throw new Exception($message);
}
$path = tempnam(sys_get_temp_dir(), 'bkb-firmware-runtime-');
try {
    foreach (array('', '{broken', 'true', '"invalid"', '{"actions":{"stock":"invalid"}}') as $invalid) {
        file_put_contents($path, $invalid);
        expect_runtime(firmware_flash_runtime('revert', $path)['seconds'] === 164, 'stock lab baseline fallback');
        expect_runtime(firmware_flash_runtime('flash-custom', $path)['label'] === '~164s', 'V1 lab baseline fallback');
        expect_runtime(firmware_flash_runtime('verify', $path)['seconds'] === 45, 'read-only fallback');
    }
    expect_runtime(firmware_flash_runtime('revert', $path . '.missing')['label'] === '~164s', 'first flash is never blank');
    file_put_contents($path, json_encode(array('seconds' => 164, 'action' => 'custom-v1', 'at' => 123)));
    expect_runtime(firmware_flash_runtime('flash-custom', $path)['seconds'] === 164, 'legacy custom sample');
    expect_runtime(!firmware_flash_runtime('revert', $path)['measured'], 'custom is not stock evidence');
    expect_runtime(firmware_record_flash_runtime('revert', 171.2, $path), 'record stock');
    expect_runtime(firmware_flash_runtime('revert', $path)['seconds'] === 171, 'stock updated');
    expect_runtime(firmware_flash_runtime('flash-custom', $path)['seconds'] === 164, 'custom history retained');
    expect_runtime(firmware_record_flash_runtime('verify', 43, $path), 'record successful read');
    expect_runtime(firmware_flash_runtime('verify', $path)['seconds'] === 43, 'read learned separately');
    expect_runtime(firmware_flash_runtime('', $path)['seconds'] === 171, 'read cannot replace last flash');
    expect_runtime(firmware_flash_runtime('revert', $path)['label'] === '~171s', 'next duration stays an estimate');
    $before = file_get_contents($path);
    foreach (array(0, -1, 3601, 'not-time', true, null, INF, NAN) as $bad) {
        expect_runtime(!firmware_record_flash_runtime('stock', $bad, $path), 'invalid elapsed rejected');
    }
    expect_runtime(!firmware_record_flash_runtime('unknown', 170, $path), 'unknown operation rejected');
    expect_runtime(file_get_contents($path) === $before, 'invalid samples leave history unchanged');
    expect_runtime(strpos($source, "firmware_record_flash_runtime('verify'") !== false, 'successful read records timing');
    echo "PASS firmware timing defaults, per-operation learning, legacy migration and invalid samples\n";
} finally {
    unlink($path);
}
