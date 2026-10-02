<?php
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
