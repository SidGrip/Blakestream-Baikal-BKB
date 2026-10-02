<?php
require_once __DIR__ . '/../overlay/var/www/inc/clock-baseline.php';
function check_baseline($condition, $label) {
    if (!$condition) throw new Exception($label);
}
$path = tempnam(sys_get_temp_dir(), 'baseline-');
$identity_path = tempnam(sys_get_temp_dir(), 'identity-');
try {
    $data = array('schema' => 1, 'source' => 'lab-example', 'algorithm' => 'blake256r8',
        'host_sha256' => str_repeat('a', 64),
        'compatible_host_sha256' => array(str_repeat('c', 64)),
        'bridge_artifact_sha256' => 'caf95856393914cf01436d07724e067f41dab9d1fa98057e7b3a272c16ab4a54',
        'bridge_chip_md5' => '0e8467eddabf589ad94549a8d572c759', 'qualified' => false,
        'frequency_status' => 'unverified', 'physical_mhz' => null, 'power_measured' => false,
        'private_path' => '/root/private', 'rows' => array(array('clock_index' => 30, 'ghs' => 60,
            'estimated_mhz' => 400, 'board_windows' => 3, 'board_seconds' => 5401,
            'accepted_shares' => 2500, 'hw_errors' => 1, 'measured_boards' => array(0, 1, 2))));
    file_put_contents($path, json_encode($data));
    $read = bs_clock_baseline_read($path);
    check_baseline(is_array($read) && $read['rows'][0]['ghs'] === 60, 'valid baseline');
    check_baseline(!isset($read['private_path']), 'private fields removed');
    $stat = array('DiagHostSha256' => str_repeat('a', 64), 'ChipCount' => 64, 'Algo' => 'Blakecoin');
    $identity = array('identity' => 'CUSTOM V1', 'chip_md5' => $data['bridge_chip_md5']);
    check_baseline(bs_clock_baseline_matches($read, $stat, $identity), 'driver alias matches');
    $release = array('identity' => 'CUSTOM V1', 'chip_md5' => '18babe0c4c67d9146e230f9dc423f2f2');
    check_baseline(!bs_clock_baseline_matches($read, $stat, $release), 'compatibility must be explicit');
    $data['compatible_bridge_chip_md5'] = array($release['chip_md5']);
    file_put_contents($path, json_encode($data));
    $read = bs_clock_baseline_read($path);
    check_baseline(bs_clock_baseline_matches($read, $stat, $release), 'reviewed release shares lab estimates');
    check_baseline($read['bridge_chip_md5'] === $identity['chip_md5'], 'measured firmware provenance preserved');
    check_baseline(!bs_clock_baseline_matches($read, $stat, array('identity' => 'CUSTOM V1', 'chip_md5' => str_repeat('f', 32))), 'unknown bridge excluded');
    $verified = $stat + array('CommandVerified' => true, 'SentClockIndex' => 30);
    check_baseline(bs_clock_baseline_estimate($read, $verified, $release)['ghs'] === 60, 'release uses same Index estimate');
    $verified['CommandVerified'] = false;
    check_baseline(bs_clock_baseline_estimate($read, $verified, $release) === null, 'unconfirmed clock cannot use Index estimate');
    $stat['Algo'] = 'blake256r8';
    check_baseline(bs_clock_baseline_matches($read, $stat, $identity), 'canonical driver algorithm matches');
    $stat['Algo'] = 'X11';
    check_baseline(!bs_clock_baseline_matches($read, $stat, $identity), 'other algorithm excluded');
    $stat['Algo'] = 'Blakecoin';
    $stat['DiagHostSha256'] = str_repeat('b', 64);
    check_baseline(!bs_clock_baseline_matches($read, $stat, $identity), 'other host excluded');
    $stat['DiagHostSha256'] = str_repeat('c', 64);
    check_baseline(bs_clock_baseline_matches($read, $stat, $identity), 'explicitly reviewed compatible host accepted');
    check_baseline($read['host_sha256'] === str_repeat('a', 64), 'original measured host provenance preserved');
    $stat['DiagHostSha256'] = str_repeat('a', 64);
    check_baseline(!bs_clock_baseline_matches($read, $stat, array('identity' => 'STOCK', 'chip_md5' => $data['bridge_chip_md5'])), 'stock excluded');
    file_put_contents($identity_path, '{broken');
    check_baseline(bs_clock_baseline_identity(array($identity_path, $path)) === null, 'invalid current record cannot use fallback');
    foreach (array('schema' => 2, 'qualified' => true, 'physical_mhz' => 500, 'algorithm' => 'x11') as $key => $bad) {
        $copy = $data; $copy[$key] = $bad;
        file_put_contents($path, json_encode($copy));
        check_baseline(bs_clock_baseline_read($path) === null, 'bad metadata ' . $key);
    }
    foreach (array('clock_index' => 33, 'ghs' => -1, 'board_seconds' => 10, 'estimated_mhz' => '400', 'hw_errors' => -1) as $key => $bad) {
        $copy = $data; $copy['rows'][0][$key] = $bad;
        file_put_contents($path, json_encode($copy));
        check_baseline(bs_clock_baseline_read($path) === null, 'bad row ' . $key);
    }
    foreach (array('any', null, array('bad'), array(str_repeat('C', 64)),
        array_fill(0, 9, str_repeat('c', 64)), array(str_repeat('c', 64), str_repeat('c', 64)),
        array('host' => str_repeat('c', 64))) as $bad) {
        $copy = $data; $copy['compatible_host_sha256'] = $bad;
        file_put_contents($path, json_encode($copy));
        check_baseline(bs_clock_baseline_read($path) === null, 'invalid compatible-host list rejected');
    }
    foreach (array('any', null, array('bad'), array(str_repeat('f', 32)),
        array($release['chip_md5'], $release['chip_md5']), array('bridge' => $release['chip_md5'])) as $bad) {
        $copy = $data; $copy['compatible_bridge_chip_md5'] = $bad;
        file_put_contents($path, json_encode($copy));
        check_baseline(bs_clock_baseline_read($path) === null, 'invalid bridge compatibility rejected');
    }
    $copy = $data;
    $copy['bridge_artifact_sha256'] = 'd2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a';
    file_put_contents($path, json_encode($copy));
    check_baseline(bs_clock_baseline_read($path) === null, 'mismatched measured SHA/MD5 rejected');
    $copy['bridge_chip_md5'] = $release['chip_md5'];
    $copy['compatible_bridge_chip_md5'] = array();
    file_put_contents($path, json_encode($copy));
    check_baseline(bs_clock_baseline_matches(bs_clock_baseline_read($path), $stat, $release), 'new observations retain release provenance');
    $copy = $data; $copy['rows'][] = $copy['rows'][0];
    file_put_contents($path, json_encode($copy));
    check_baseline(bs_clock_baseline_read($path) === null, 'duplicate Index excluded');
    file_put_contents($path, str_repeat(' ', 65537));
    check_baseline(bs_clock_baseline_read($path) === null, 'oversized file excluded');
    echo "PASS bounded lab baseline, provenance, algorithm, host and firmware compatibility\n";
} finally {
    unlink($path);
    unlink($identity_path);
}
