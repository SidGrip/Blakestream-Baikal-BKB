<?php
define('BKB_MINER_OPTION_POLICY', __DIR__ . '/../overlay/opt/scripta/policy/miner-options.json');
define('BKB_SETTINGS_LIB_ONLY', true);
require __DIR__ . '/../overlay/var/www/f_settings.php';
$source = file_get_contents(__DIR__ . '/../overlay/var/www/f_blakestream.php');
$start = strpos($source, 'function bk_persist_manual_fan(');
$end = strpos($source, 'function clock_sample_profile(', $start);
eval(substr($source, $start, $end - $start));
$path = tempnam(sys_get_temp_dir(), 'bkb-fan-');
try {
    foreach (array(0 => 40, 39 => 40, 40 => 40, 70 => 70, 100 => 100, 101 => 100) as $input => $expected) {
        file_put_contents($path, json_encode(array(array('key' => 'baikal-fan', 'value' => '70'))));
        if (!bk_persist_manual_fan($path, $input)) throw new Exception('Fan save failed');
        $saved = json_decode(file_get_contents($path), true);
        if ($saved[0]['value'] !== (string)$expected) throw new Exception('Fan endpoint minimum');
        $options = array(array('key' => 'baikal-fan', 'value' => (string)$input));
        $runtime = baikalBuildMinerConfig($options);
        if ($input >= 40 && $input <= 100) {
            if (!is_array($runtime) || $runtime['baikal-fan'] !== (string)$expected)
                throw new Exception('Miner options valid boundary');
        } elseif ($runtime !== false) {
            throw new Exception('Miner options reject out-of-range saved values');
        }
    }
    if (strpos($source, 'if ($fan < 40) $fan = 40;') === false)
        throw new Exception('Fan response must report the clamped value');
} finally {
    unlink($path);
    if (is_file($path . '.tmp')) unlink($path . '.tmp');
}
echo "PASS manual fan persistence and miner options enforce 40-100%\n";
