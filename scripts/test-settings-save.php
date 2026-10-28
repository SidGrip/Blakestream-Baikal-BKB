<?php
define('BKB_MINER_OPTION_POLICY', __DIR__ . '/../overlay/opt/scripta/policy/miner-options.json');
define('BKB_SETTINGS_LIB_ONLY', true);
require __DIR__ . '/../overlay/var/www/f_settings.php';
function write_config_file($path, $contents) { return $GLOBALS['save_ok']; }
function test_clamp_clock_to_stock() { return false; }
function check_save($condition, $message) {
    if (!$condition) throw new Exception($message);
}
$source = file_get_contents(__DIR__ . '/../overlay/var/www/f_settings.php');
$start = strpos($source, '  // Sync current with new settings');
$end = strpos($source, '  // Load current settings', $start);
$branch = substr($source, $start, $end - $start);
$branch = str_replace('clampBaikalClockToStock()', 'test_clamp_clock_to_stock()', $branch);
$old = array('userDev' => false, 'userAutoFan' => false, 'userAdjustableClocks' => false);
$newdata = array('userDev' => true, 'userAutoFan' => true);
$configScripta = '/unused-test-config';
foreach (array(false, true) as $save_ok) {
    $r = array('data' => $old, 'info' => array());
    eval($branch);
    check_save($r['settingsSaved'] === $save_ok, 'explicit save outcome');
    if (!$save_ok) {
        check_save($r['data'] === $old, 'failed write returns original settings');
        check_save(!isset($r['fanRequest']), 'failed write must not request fan change');
    } else {
        check_save($r['data']['userDev'] && $r['data']['userAutoFan'], 'successful write returns saved settings');
        check_save(isset($r['fanRequest']), 'successful fan change reports revision');
    }
}
$save_ok = true;
foreach (array(false, true) as $enabled) {
    foreach (array('RAM', 'SD') as $target) {
        $r = array('data' => array('userDev' => !$enabled, 'userDevLogTarget' => $target,
            'userNetworkLogTarget' => 'SD', 'userSystemLogTarget' => 'RAM',
            'userAutoFan' => false, 'userAdjustableClocks' => false), 'info' => array());
        $newdata = array('userDev' => $enabled, 'userDevLogTarget' => $target === 'RAM' ? 'SD' : 'RAM');
        eval($branch);
        check_save($r['data']['userDevLogTarget'] === $target, 'Dev Tools toggle ignores a stale log target draft');
        check_save($r['data']['userNetworkLogTarget'] === 'SD', 'Dev Tools toggle preserves diagnostic destinations');
    }
}
foreach (array(null, 'invalid', array(), 'sd') as $target) {
    $r = array('data' => array('userDevLogTarget' => $target), 'info' => array());
    $newdata = array('userDev' => false);
    eval($branch);
    check_save($r['data']['userDevLogTarget'] === ($target === 'sd' ? 'SD' : 'RAM'), 'target normalization is safe');
}
echo "PASS settings persistence, save failure, and log destinations independent of Dev Tools\n";
$defaults = bs_normalize_fan_settings(array('userAutoFanMin' => 40));
foreach (array('userAutoFanMin'=>40, 'userAutoFanTarget'=>36, 'userAutoFanMidTemp'=>43,
    'userAutoFanMidFan'=>75, 'userAutoFanFull'=>50) as $key=>$value) {
    check_save($defaults[$key] === $value, 'default curve '.$key);
}
$saved = array('userAutoFanMin'=>30, 'userAutoFanTarget'=>35, 'userAutoFanMidTemp'=>42,
    'userAutoFanMidFan'=>65, 'userAutoFanFull'=>49, 'userDev'=>false);
check_save(bs_normalize_fan_settings($saved) === $saved, 'preserve valid saved curve');
foreach (array(-1, 0, 19, 20, 120, 999) as $value) {
    $clean = bs_normalize_fan_settings(array('userAutoFanTarget'=>$value, 'userAutoFanMidTemp'=>0));
    check_save($clean['userAutoFanTarget'] >= 20, 'target floor');
    check_save($clean['userAutoFanMidTemp'] > $clean['userAutoFanTarget'], 'midpoint ordered');
    check_save($clean['userAutoFanMidTemp'] < $clean['userAutoFanFull'], 'full temperature ordered');
    check_save(bs_normalize_fan_settings($clean) === $clean, 'curve normalization idempotent');
}
check_save(normalizeBaikalOptionsValue('') === '400:40:55', 'stock recover and cutoff defaults');
check_save(normalizeBaikalOptionsValue('400:40:120') === '400:40:99', 'two-digit temperature maximum');
echo "PASS fan defaults, saved curves, temperature floors and two-digit protection settings\n";

$validOptions = array(
    array('key' => 'api-port', 'value' => '4028'),
    array('key' => 'api-allow', 'value' => 'B:127.0.0.1'),
    array('key' => 'api-groups', 'value' => 'B:*:restart:ascidentify:ascenable:ascdisable:bkbtrace:bkbfan:ascpool:ascfailover'),
    array('key' => 'baikal-fan', 'value' => '40'),
    array('key' => 'baikal-options', 'value' => '400:40:55'),
    array('key' => 'scan-time', 'value' => '999'),
    array('key' => 'no-submit-stale', 'value' => false),
);
check_save(bs_miner_option_security_error($validOptions) === null, 'supported option list accepted');
$built = baikalBuildMinerConfig($validOptions);
check_save(is_array($built), 'supported options build a miner config');
check_save($built['api-port'] === '4028' && $built['api-allow'] === 'B:127.0.0.1',
    'runtime API settings stay pinned to restricted loopback group');
$apiGroup = explode(':', $built['api-groups']);
check_save(array_shift($apiGroup) === 'B' && in_array('*', $apiGroup, true),
    'runtime API group includes read-only commands');
foreach (array('restart', 'ascidentify', 'ascenable', 'ascdisable', 'bkbtrace', 'bkbfan', 'ascpool', 'ascfailover') as $verb) {
    check_save(in_array($verb, $apiGroup, true), 'required runtime API verb granted: ' . $verb);
}
foreach (array('save', 'addpool', 'removepool', 'quit', 'setconfig', 'debug', 'lockstats', 'ascset') as $verb) {
    check_save(!in_array($verb, $apiGroup, true), 'unrelated write API verb omitted: ' . $verb);
}
foreach (array('monitor', 'event-runcmd', 'events', 'include', 'includes', 'Include', 'INCLUDES',
               'api-listen', 'api-network', "monitor\0safe") as $key) {
    check_save(bs_miner_option_security_error(array(array('key' => $key, 'value' => '/bin/false'))) !== null,
        'unsupported option rejected: ' . $key);
}
foreach (array('W:0/0', 'R:0/0', 'W:127.0.0.1', 'B:0/0', 'B:192.0.2.1') as $value) {
    check_save(bs_miner_option_security_error(array(array('key' => 'api-allow', 'value' => $value))) !== null,
        'API allow widening rejected');
}
foreach (array('B:*:save', 'B:*:addpool', 'B:*:quit', 'W:*', 'B:*:ascset', 'B:*:bkbtrace:ascset') as $value) {
    check_save(bs_miner_option_security_error(array(array('key' => 'api-groups', 'value' => $value))) !== null,
        'API group widening rejected');
}
check_save(bs_miner_option_security_error(array(
    array('key' => 'baikal-fan', 'value' => '70'),
    array('key' => 'baikal-fan', 'value' => '80'),
)) !== null, 'duplicate option rejected');
check_save(bs_miner_option_security_error(array(1 => array('key' => 'baikal-fan', 'value' => '70'))) !== null,
    'sparse option object rejected');
foreach (array(0, 1, 0.0, 1.0, null, array()) as $value) {
    check_save(bs_miner_option_security_error(array(array('key' => 'no-submit-stale', 'value' => $value))) !== null,
        'non-boolean stale option rejected');
}
foreach (array('400:40:55 --monitor /tmp/pwn', "400:40:55\nevent-runcmd=x", '400:40:55:1') as $value) {
    $unsafe = array(array('key' => 'baikal-options', 'value' => $value));
    check_save(bs_miner_option_security_error($unsafe) !== null && baikalBuildMinerConfig($unsafe) === false,
        'nested command-looking option rejected');
}
echo "PASS miner option allowlist, exact values, list shape and runtime API pinning\n";
