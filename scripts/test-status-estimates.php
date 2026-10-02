<?php
define('BS_STATUS_LIB_ONLY', true);
require __DIR__ . '/../overlay/var/www/f_status.php';
require __DIR__ . '/../overlay/var/www/inc/clock-baseline.php';

function expect_estimate($condition, $message) {
    if (!$condition) throw new Exception($message);
}

$data = bs_clock_baseline_read(__DIR__ . '/../overlay/opt/scripta/etc/clock-baseline.json');
expect_estimate(count($data['rows']) === 25, 'all supported clock indices are bundled');
$current_host = hash_file('sha256', __DIR__ . '/../overlay/opt/scripta/bin/sgminer');
expect_estimate($current_host === hash_file('sha256', __DIR__ . '/../sgminer-build/sgminer.patched'), 'release host copies match');
expect_estimate(in_array($current_host, $data['compatible_host_sha256'], true), 'renamed host has explicit baseline compatibility');
$identity = array('identity' => 'CUSTOM V1', 'chip_md5' => $data['bridge_chip_md5']);
$stat = array('DiagHostSha256' => $data['host_sha256'], 'ChipCount' => 64,
    'Algo' => 'blake256r8', 'ExperimentalClock' => true, 'CommandVerified' => true,
    'SentClockIndex' => 8, 'RequestedClockIndex' => 32);
$dev = array('Enabled' => 'Y', 'EffectivePool' => 0, 'MHS5s' => 61321);
foreach ($data['rows'] as $row) {
    $stat['DiagHostSha256'] = $current_host;
    $stat['SentClockIndex'] = $row['clock_index'];
    $baseline = bs_clock_baseline_estimate($data, $stat, $identity);
    $fields = bs_status_estimate_fields($stat, $dev, $baseline, null);
    expect_estimate($fields['EstimatedHashrate'] === $row['ghs'] * 1000.0, 'baseline GH/s converted to MH/s');
    expect_estimate($fields['EstimatedClockMHz'] === $row['estimated_mhz'], 'performance-equivalent MHz');
    expect_estimate($fields['EstimateSource'] === 'Lab baseline', 'source metadata retained');
    expect_estimate($fields['EstimateSeconds'] === 0, 'baseline does not invent personal history');
    expect_estimate(!isset($fields['Accepted15m'], $fields['Clock']), 'raw telemetry and accepted work untouched');
}
$stat['SentClockIndex'] = 8;
$baseline = bs_clock_baseline_estimate($data, $stat, $identity);
expect_estimate($baseline['clock_index'] === 8, 'sent index wins over requested index');
$personal = array('ghs' => 31.75, 'seconds' => 22000);
$fields = bs_status_estimate_fields($stat, $dev, $baseline, $personal);
expect_estimate($fields['EstimatedHashrate'] === 31750.0 && $fields['EstimateSource'] === 'Your history', 'qualified personal history takes priority');
expect_estimate($fields['EstimateSeconds'] === 22000, 'personal duration retained');
expect_estimate($fields['EstimatedClockMHz'] === $baseline['estimated_mhz'], 'personal hashrate does not relabel MHz');
foreach (array('CommandVerified' => false, 'Algo' => 'x11', 'ChipCount' => 32,
    'DiagHostSha256' => str_repeat('0', 64), 'SentClockIndex' => 33) as $key => $bad) {
    $copy = $stat; $copy[$key] = $bad;
    expect_estimate(bs_clock_baseline_estimate($data, $copy, $identity) === null, 'incompatible context: ' . $key);
}
foreach (array(null, true, 8.5, '8', -1, 0) as $bad) {
    $copy = $stat; $copy['SentClockIndex'] = $bad;
    expect_estimate(bs_clock_baseline_estimate($data, $copy, $identity) === null, 'invalid sent index');
}
expect_estimate(bs_clock_baseline_estimate($data, $stat, array('identity' => 'STOCK')) === null, 'stock has no custom baseline');
expect_estimate(bs_clock_baseline_estimate(null, $stat, $identity) === null, 'missing file stays unavailable');
$fields = bs_status_estimate_fields($stat, $dev, null, null);
expect_estimate($fields['EstimatedHashrate'] === null && $fields['EstimatedClockMHz'] === null, 'no invented custom estimate');
$normal = $stat; $normal['ExperimentalClock'] = false;
$fields = bs_status_estimate_fields($normal, $dev, $baseline, null);
expect_estimate($fields['EstimatedHashrate'] === 61321.0 && $fields['EstimateSource'] === 'Model', 'normal model unchanged');
foreach (array('Enabled' => 'N', 'EffectivePool' => -1) as $key => $value) {
    $idle = $dev; $idle[$key] = $value;
    $fields = bs_status_estimate_fields($stat, $idle, $baseline, $personal);
    expect_estimate($fields['EstimatedHashrate'] === 0, 'idle boards cannot display an active hashrate');
}
$source = file_get_contents(__DIR__ . '/../overlay/var/www/f_status.php');
$start = strpos($source, '$devices = 0;');
$end = strpos($source, '$r[\'status\'][\'fanControl\']', $start);
expect_estimate($start !== false && $end !== false, 'status totals block found');
$totalsCode = substr($source, $start, $end - $start);
function header_totals($boards, $code) {
    $r = array('status' => array('devs' => $boards));
    eval($code);
    return $r['status']['dtot'];
}
$ready = array('Enabled'=>'Y', 'EffectivePool'=>0, 'MHS5s'=>60000, 'MHSav'=>60000,
    'Accepted'=>50, 'Rejected'=>0, 'HardwareErrors'=>0, 'Utility'=>1,
    'EstimatedHashrate'=>60000, 'EstimateSource'=>'Model', 'Accepted15m'=>59000,
    'Accepted1h'=>58000, 'Accepted15mSeconds'=>840, 'Accepted1hSeconds'=>3540,
    'Accepted15mMeasuring'=>false, 'Accepted1hMeasuring'=>false, 'Accepted1hFullWindow'=>true);
$totals = header_totals(array($ready, $ready), $totalsCode);
expect_estimate($totals['Accepted1hFullWindow'] && $totals['Accepted1h'] === 116000.0,
    'full hour uses every active board despite rolling interval jitter');
$partial = $ready; $partial['Accepted1hFullWindow'] = false;
expect_estimate(!header_totals(array($ready, $partial), $totalsCode)['Accepted1hFullWindow'], 'one immature board holds header at 15m');
$partial['Enabled'] = 'N';
expect_estimate(header_totals(array($ready, $partial), $totalsCode)['Accepted1hFullWindow'], 'disabled board does not hold active total');
$partial['Enabled'] = 'Y'; $partial['EffectivePool'] = -1;
expect_estimate(header_totals(array($ready, $partial), $totalsCode)['Accepted1hFullWindow'], 'route-idle board excluded');
$invalid = $ready; $invalid['Accepted1h'] = null;
expect_estimate(!header_totals(array($ready, $invalid), $totalsCode)['Accepted1hFullWindow'], 'missing measurement invalidates maturity');
expect_estimate(!header_totals(array(), $totalsCode)['Accepted1hFullWindow'], 'empty miner has no full hour');
echo "PASS dashboard baseline, personal priority, compatibility and full-hour total guards\n";
