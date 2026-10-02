<?php
require __DIR__ . '/../overlay/var/www/inc/accepted-work.php';
require __DIR__ . '/../overlay/var/www/inc/dev-activity.php';
require __DIR__ . '/../overlay/var/www/inc/measured-rates.php';
function expect($ok, $message) { if (!$ok) { fwrite(STDERR, "FAIL $message\n"); exit(1); } }
$devSource = file_get_contents(__DIR__ . '/../overlay/var/www/f_dev.php');
expect(strpos($devSource, "\$target = isset(\$measured['target']) ? \$measured['target'] : 'RAM';") !== false,
    'storage row defaults to RAM while preserving a reported target');
$token = str_repeat('a', 64);
expect(bs_measured_authorized('POST', array('csrf' => $token), $token), 'valid mutation token');
foreach (array(null, array(), array('csrf' => 'bad'), array('csrf' => array())) as $body) {
    expect(!bs_measured_authorized('POST', $body, $token), 'bad mutation token rejected');
}
expect(!bs_measured_authorized('GET', array('csrf' => $token), $token), 'GET mutation rejected');
expect(!bs_measured_command('target', 'sd')['ok'], 'target strict');
expect(!bs_measured_command('reset-board', '3')['ok'], 'board bounded');
expect(!bs_measured_command('unrecognized')['ok'], 'no arbitrary command');
$path = tempnam(sys_get_temp_dir(), 'bkb-measured-');
register_shutdown_function(function() use ($path) { @unlink($path); });
file_put_contents($path, json_encode(array('ok' => true, 'updated' => 1000, 'boards' => array(0 => array()), 'salt' => 'private', 'runtime' => array())));
$r = bs_measured_read($path, 1100);
expect($r['ok'] && !isset($r['salt']) && !isset($r['runtime']), 'private control data omitted');
expect(!bs_measured_read($path, 1121)['ok'], 'stale summary');
expect(!bs_measured_read($path, 999)['ok'], 'backward clock');
file_put_contents($path, '{broken');
expect(!bs_measured_read($path, 1000)['ok'], 'corrupt summary');
$context = array('pid' => 3, 'start' => 100, 'hs' => 'hash', 'ds' => 0, 'cg' => 1,
    're' => 0, 'ee' => 0, 'scc' => 30, 'xp' => 0, 'pi' => 0, 'ep' => 0,
    'startup' => 64, 'chips' => 64, 'algo' => 'Blakecoin', 'mode' => 'individual', 'cv' => 1);
$stat = array('DiagPid' => 3, 'DiagStartUnix' => 100, 'DiagHostSha256' => 'hash',
    'DiagStatEpoch' => 0, 'ClockGeneration' => 1, 'DiagRouteEpoch' => 0, 'DiagEnableEpoch' => 0,
    'SentClockIndex' => 30, 'ExperimentalClock' => false, 'RequestedClockIndex' => 0,
    'StartupSuccessCount' => 64, 'ChipCount' => 64, 'Algo' => 'Blakecoin', 'ClockMode' => 'individual',
    'DiagShareSnapValid' => 1, 'DiagDeviceElapsedValid' => 1, 'CommandVerified' => true);
$dev = array('EffectivePool' => 0);
$s = array('ok' => true, 'boards' => array(0 => array('context' => $context,
    'personal' => array('qualified' => true, 'ghs' => 60, 'context' => array('indices' => array(30,30,30))))));
$stats = array();
for ($i = 0; $i < 3; $i++) $stats[] = array('ID' => 'BKLU'.$i, 'SentClockIndex' => 30);
expect(bs_measured_personal($s, 0, $stat, $dev, $stats)['ghs'] === 60, 'qualified current personal estimate');
$stats[1]['SentClockIndex'] = 22;
expect(bs_measured_personal($s, 0, $stat, $dev, $stats) === null, 'all-board tuple boundary');
$stats[1]['SentClockIndex'] = 30;
$unconfirmed = $stat;
$unconfirmed['CommandVerified'] = false;
$unconfirmed['SentClockIndex'] = 0;
$live = $s;
$live['boards'][0]['context']['cv'] = 0;
$live['boards'][0]['context']['scc'] = 0;
expect(bs_accepted_context_matches($live['boards'][0], $unconfirmed, $dev), 'live rates survive an unconfirmed clock');
expect(bs_measured_personal($live, 0, $unconfirmed, $dev, $stats) === null, 'unconfirmed clock cannot promote verified history');
expect(!bs_accepted_context_matches($s['boards'][0], $unconfirmed, $dev), 'clock confirmation boundary rejects stale rates');
foreach (array('GlobalClockPending', 'GlobalClockInflight', 'ClockRetryPending', 'ClockReapplyPending', 'PendingClockReapply') as $field) {
    $pending = $unconfirmed; $pending[$field] = true;
    expect(!bs_accepted_context_matches($live['boards'][0], $pending, $dev), 'unconfirmed pending clock rejected '.$field);
}
foreach (array('DiagShareSnapValid', 'DiagDeviceElapsedValid') as $field) {
    $invalid = $unconfirmed; $invalid[$field] = 0;
    expect(!bs_accepted_context_matches($live['boards'][0], $invalid, $dev), 'invalid share snapshot rejected '.$field);
}
$unknown = $s;
$unknown['boards'][0]['context']['scc'] = 0;
$unknown['boards'][0]['personal']['context']['indices'][0] = 0;
$unknownStat = $stat; $unknownStat['SentClockIndex'] = 0;
$unknownStats = $stats; $unknownStats[0]['SentClockIndex'] = 0;
expect(bs_measured_personal($unknown, 0, $unknownStat, $dev, $unknownStats) === null, 'unknown index cannot promote history');
foreach (array('DiagStartUnix', 'StartupSuccessCount', 'ChipCount', 'Algo', 'ClockMode') as $field) {
    $changed = $stat; $changed[$field] = 'other';
    expect(bs_measured_personal($s, 0, $changed, $dev, $stats) === null, 'live context boundary '.$field);
}
$s['error'] = 'SD full';
expect(bs_measured_personal($s, 0, $stat, $dev, $stats) === null, 'save error cannot promote history');
echo "PASS measured-rate read, freshness, context and mutation guards\n";
