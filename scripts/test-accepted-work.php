<?php

require_once(__DIR__ . '/../overlay/var/www/inc/accepted-work.php');

function fail_test($message) {
    fwrite(STDERR, "FAIL: " . $message . "\n");
    exit(1);
}

function sample_at($t, $accepted, $difficulty, $overrides = array()) {
    return array_merge(array(
        't' => $t,
        'a' => $accepted,
        'da' => $difficulty,
        'de' => $t,
        'se' => 7,
        'cg' => 12,
        'ep' => 1,
        'pool' => 'pool.example:3334',
        'en' => 1,
    ), $overrides);
}

$samples = array();
for ($i = 0; $i <= 15; $i++) {
    $samples[] = sample_at(1000 + ($i * 60), 100 + ($i * 10), 5000 + ($i * 640));
}
$rate = bs_accepted_work_rate($samples, 15 * 60);
$expected = (9600.0 * 4294967296.0) / 900.0 / 1000000000.0;
if (empty($rate['valid']) || abs($rate['ghs'] - $expected) > 0.000001) {
    fail_test('valid fixed-difficulty rate does not match the formula');
}

$generation_change = $samples;
$generation_change[14]['cg'] = 13;
$generation_change[15]['cg'] = 13;
if (!empty(bs_accepted_work_rate($generation_change, 15 * 60)['valid'])) {
    fail_test('clock-generation boundary was accepted');
}

$route_change = $samples;
$route_change[14]['ep'] = 2;
$route_change[15]['ep'] = 2;
if (!empty(bs_accepted_work_rate($route_change, 15 * 60)['valid'])) {
    fail_test('pool-route boundary was accepted');
}

$disabled = $samples;
$disabled[14]['en'] = 0;
if (!empty(bs_accepted_work_rate($disabled, 15 * 60)['valid'])) {
    fail_test('disabled-board boundary was accepted');
}

$reset = $samples;
$reset[15]['se'] = 8;
$reset[15]['a'] = 1;
$reset[15]['da'] = 32;
if (!empty(bs_accepted_work_rate($reset, 15 * 60)['valid'])) {
    fail_test('counter-reset boundary was accepted');
}

foreach (array('xp', 'pi', 'scc', 'pid', 'hs', 'ds', 're', 'ee') as $key) {
    $changed = $samples;
    $changed[14][$key] = 99;
    $changed[15][$key] = 99;
    if (!empty(bs_accepted_work_rate($changed, 900)['valid'])) {
        fail_test($key . ' identity boundary was accepted');
    }
}
$context = array('pid' => 123, 'hs' => 'abc', 'ds' => 0, 'cg' => 2,
    're' => 0, 'ee' => 0, 'scc' => 29, 'xp' => 1, 'pi' => 29, 'ep' => 1);
$stat = array('DiagPid' => 123, 'DiagHostSha256' => 'abc', 'DiagStatEpoch' => 0,
    'ClockGeneration' => 2, 'DiagRouteEpoch' => 0, 'DiagEnableEpoch' => 0,
    'SentClockIndex' => 29, 'ExperimentalClock' => true, 'RequestedClockIndex' => 29);
$dev = array('EffectivePool' => 1);
if (!bs_accepted_context_matches(array('context' => $context), $stat, $dev)) {
    fail_test('matching live context was rejected');
}
foreach (array_keys($context) as $key) {
    $changed = $context;
    $changed[$key] = $key === 'xp' ? 0 : 'changed';
    if (bs_accepted_context_matches(array('context' => $changed), $stat, $dev)) {
        fail_test('stale ' . $key . ' context was accepted');
    }
    unset($changed[$key]);
    if (bs_accepted_context_matches(array('context' => $changed), $stat, $dev)) {
        fail_test('missing ' . $key . ' context was accepted');
    }
}
if (bs_accepted_context_matches(array(), $stat, $dev)) fail_test('missing context accepted');
$reconnected = $stat;
$reconnected['DiagRouteEpoch'] = 2;
if (!bs_accepted_context_matches(array('context' => $context), $reconnected, $dev)) {
    fail_test('same-pool reconnect hid still-current accepted rates');
}
if (bs_accepted_context_matches(array('context' => $context), $reconnected, array('EffectivePool' => 2))) {
    fail_test('a different active pool reused accepted rates');
}
$regressed = $context;
$regressed['re'] = 3;
if (bs_accepted_context_matches(array('context' => $regressed), $reconnected, $dev)) {
    fail_test('a regressed route epoch reused accepted rates');
}

$tmp = tempnam(sys_get_temp_dir(), 'bkb-accepted-');
$summary = array(
    'version' => 1,
    'updated' => 1900,
    'boards' => array(
        '0' => array(
            '15m' => $rate,
            '1h' => array('valid' => false, 'ghs' => null, 'reason' => 'measuring'),
        ),
    ),
);
file_put_contents($tmp, json_encode($summary));
$compact = bs_accepted_rates_by_board($tmp, 1900, 180);
if (!isset($compact[0]['15m']) || empty($compact[0]['15m']['valid']) ||
    abs($compact[0]['15m']['ghs'] - $expected) > 0.000001) {
    @unlink($tmp);
    fail_test('compact recorder summary was not loaded correctly');
}
if (count(bs_accepted_rates_by_board($tmp, 2081, 180)) !== 0) {
    @unlink($tmp);
    fail_test('stale compact recorder summary was accepted');
}
@unlink($tmp);

printf("PASS accepted-work %.6f GH/s and boundary guards\n", $rate['ghs']);
