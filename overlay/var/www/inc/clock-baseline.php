<?php
require_once __DIR__ . '/firmware-identity.php';

function bs_clock_baseline_read($path = '/opt/scripta/etc/clock-baseline.json') {
    $bridge_images = array(
        'caf95856393914cf01436d07724e067f41dab9d1fa98057e7b3a272c16ab4a54' => '0e8467eddabf589ad94549a8d572c759',
        'd2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a' => '18babe0c4c67d9146e230f9dc423f2f2',
    );
    $raw = @file_get_contents($path, false, null, 0, 65537);
    $data = is_string($raw) && strlen($raw) <= 65536 ? json_decode($raw, true) : null;
    if (!is_array($data) || !isset($data['schema'], $data['source'], $data['algorithm'],
        $data['host_sha256'], $data['bridge_artifact_sha256'], $data['bridge_chip_md5'], $data['rows']) ||
        $data['schema'] !== 1 || $data['source'] !== 'lab-example' || $data['algorithm'] !== 'blake256r8' ||
        !is_string($data['host_sha256']) || !preg_match('/\A[a-f0-9]{64}\z/', $data['host_sha256']) ||
        !is_string($data['bridge_artifact_sha256']) ||
        !isset($bridge_images[$data['bridge_artifact_sha256']]) ||
        $data['bridge_chip_md5'] !== $bridge_images[$data['bridge_artifact_sha256']] ||
        !isset($data['qualified'], $data['frequency_status'], $data['power_measured']) ||
        $data['qualified'] !== false || $data['frequency_status'] !== 'unverified' || $data['power_measured'] !== false ||
        !array_key_exists('physical_mhz', $data) || $data['physical_mhz'] !== null ||
        !is_array($data['rows']) || count($data['rows']) > 25) return null;
    // Compatibility is explicit per reviewed binary; measured provenance stays unchanged.
    $compatible_hosts = array_key_exists('compatible_host_sha256', $data) ? $data['compatible_host_sha256'] : array();
    if (!is_array($compatible_hosts) || count($compatible_hosts) > 8 ||
        array_values($compatible_hosts) !== $compatible_hosts) return null;
    foreach ($compatible_hosts as $sha) {
        if (!is_string($sha) || !preg_match('/\A[a-f0-9]{64}\z/', $sha)) return null;
    }
    if (count(array_unique($compatible_hosts)) !== count($compatible_hosts)) return null;
    $compatible_bridges = array_key_exists('compatible_bridge_chip_md5', $data) ? $data['compatible_bridge_chip_md5'] : array();
    if (!is_array($compatible_bridges) || array_values($compatible_bridges) !== $compatible_bridges ||
        count($compatible_bridges) > 1) return null;
    foreach ($compatible_bridges as $md5) {
        if ($md5 !== '18babe0c4c67d9146e230f9dc423f2f2') return null;
    }
    $rows = array();
    foreach ($data['rows'] as $row) {
        if (!is_array($row) || !isset($row['clock_index'], $row['ghs'], $row['board_windows'],
            $row['board_seconds'], $row['accepted_shares'], $row['hw_errors'], $row['measured_boards']) ||
            !is_int($row['clock_index']) || $row['clock_index'] < 8 || $row['clock_index'] > 32 || isset($rows[$row['clock_index']]) ||
            !bs_clock_baseline_positive($row['ghs']) || !bs_clock_baseline_positive($row['board_seconds']) ||
            !is_int($row['board_windows']) || $row['board_windows'] < 1 ||
            $row['board_seconds'] < 1800 * $row['board_windows'] ||
            !is_int($row['accepted_shares']) || $row['accepted_shares'] < 1 ||
            !is_int($row['hw_errors']) || $row['hw_errors'] < 0 ||
            !is_array($row['measured_boards']) || count($row['measured_boards']) < 1 || count($row['measured_boards']) > 3 ||
            !array_key_exists('estimated_mhz', $row) ||
            ($row['estimated_mhz'] !== null && !bs_clock_baseline_positive($row['estimated_mhz']))) return null;
        foreach ($row['measured_boards'] as $board) {
            if (!is_int($board) || $board < 0 || $board > 2) return null;
        }
        if ($row['clock_index'] === 30 && $row['estimated_mhz'] !== 400) return null;
        $rows[$row['clock_index']] = array_intersect_key($row, array_flip(array(
            'clock_index', 'ghs', 'estimated_mhz', 'board_windows', 'board_seconds', 'accepted_shares', 'hw_errors', 'measured_boards')));
    }
    $out = array_intersect_key($data, array_flip(array('schema', 'source', 'algorithm', 'host_sha256',
        'bridge_artifact_sha256', 'bridge_chip_md5', 'qualified', 'frequency_status', 'physical_mhz', 'power_measured')));
    $out['rows'] = array_values($rows);
    $out['compatible_host_sha256'] = $compatible_hosts;
    $out['compatible_bridge_chip_md5'] = $compatible_bridges;
    return $out;
}

function bs_clock_baseline_positive($value) {
    return (is_int($value) || is_float($value)) && is_finite($value) && $value > 0;
}

function bs_clock_baseline_identity($paths = null) {
    if ($paths === null) $paths = array('/run/blakestream/bkb-firmware-identity.json', '/opt/scripta/etc/bkb-firmware-identity.json');
    foreach ($paths as $path) {
        if (file_exists($path)) return json_decode((string)@file_get_contents($path, false, null, 0, 8192), true);
    }
    return null;
}

function bs_clock_baseline_matches($data, $stat, $identity) {
    return is_array($data) && is_array($stat) && bkb_firmware_supports_clocks($identity) &&
        isset($data['bridge_chip_md5']) &&
        (strtolower($identity['chip_md5']) === $data['bridge_chip_md5'] ||
         (isset($data['compatible_bridge_chip_md5']) && is_array($data['compatible_bridge_chip_md5']) &&
          in_array(strtolower($identity['chip_md5']), $data['compatible_bridge_chip_md5'], true))) &&
        isset($stat['DiagHostSha256'], $stat['Algo'], $stat['ChipCount']) &&
        ($stat['DiagHostSha256'] === $data['host_sha256'] ||
         (isset($data['compatible_host_sha256']) && is_array($data['compatible_host_sha256']) &&
          in_array($stat['DiagHostSha256'], $data['compatible_host_sha256'], true))) &&
        (int)$stat['ChipCount'] === 64 &&
        is_string($stat['Algo']) && in_array(strtolower(trim($stat['Algo'])), array('blakecoin', 'blake256r8'), true);
}

function bs_clock_baseline_estimate($data, $stat, $identity) {
    if (!bs_clock_baseline_matches($data, $stat, $identity) || empty($stat['CommandVerified']) ||
        !isset($stat['SentClockIndex']) || !is_int($stat['SentClockIndex'])) return null;
    foreach ($data['rows'] as $row) {
        if ($row['clock_index'] === $stat['SentClockIndex']) return $row;
    }
    return null;
}
