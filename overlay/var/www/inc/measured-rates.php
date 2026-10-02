<?php

function bs_measured_read($path = '/run/blakestream/measured-rates-status.json', $now = null) {
    $raw = @file_get_contents($path, false, null, 0, 524289);
    $data = is_string($raw) && strlen($raw) <= 524288 ? json_decode($raw, true) : null;
    if (!is_array($data)) return array('ok' => false, 'error' => 'Measured rates unavailable', 'boards' => array(), 'contexts' => array());
    $now = $now === null ? time() : $now;
    $age = $now - (isset($data['updated']) ? $data['updated'] : 0);
    if ($age < 0 || $age > 120) {
        $data['ok'] = false;
        $data['error'] = 'Measured rates are stale';
        $data['boards'] = array();
    }
    unset($data['runtime'], $data['salt']);
    return $data;
}

function bs_measured_filter($data, $board) {
    if (!in_array($board, array('all', '0', '1', '2'), true)) return null;
    if ($board === 'all') return $data;
    $data['board_filter'] = $board;
    $contexts = isset($data['contexts']) && is_array($data['contexts']) ? $data['contexts'] : array();
    $data['contexts'] = array_values(array_filter($contexts, function ($c) use ($board) {
        return isset($c['context']['board']) && in_array($c['context']['board'], array((int)$board, $board), true);
    }));
    foreach (array('boards', 'board_ids') as $key) {
        if (isset($data[$key]) && is_array($data[$key])) $data[$key] = array_intersect_key($data[$key], array($board => true));
    }
    return $data;
}

function bs_measured_report($data, $timezone = 'UTC') {
    try { $zone = new DateTimeZone(is_string($timezone) ? $timezone : 'UTC'); }
    catch (Exception $e) { $zone = new DateTimeZone('UTC'); }
    $number = function ($row, $key) {
        if (!is_array($row) || !isset($row[$key]) || is_bool($row[$key]) || !is_numeric($row[$key])) return null;
        $n = (float)$row[$key];
        return is_finite($n) && abs($n) <= 9007199254740991 ? $n : null;
    };
    $integer = function ($row, $key) use ($number) {
        $n = $number($row, $key);
        return $n !== null && $n >= 0 && floor($n) == $n ? sprintf('%.0f', $n) : 'n/a';
    };
    $stamp = function ($row, $key) use ($number, $zone) {
        $n = $number($row, $key);
        if ($n === null || $n < 0 || $n > PHP_INT_MAX) return 'n/a';
        $date = new DateTime('@' . sprintf('%.0f', floor($n)), $zone);
        $date->setTimezone($zone);
        return $date->format('Y-m-d H:i:s');
    };
    $target = isset($data['target']) && in_array($data['target'], array('RAM', 'SD'), true) ? $data['target'] : 'Unavailable';
    $board = isset($data['board_filter']) && in_array($data['board_filter'], array('0', '1', '2'), true) ? $data['board_filter'] : 'All';
    $lines = array('Blakestream Measured Rates', 'Storage: ' . $target, 'Board: ' . $board,
        'Timezone: ' . $zone->getName(), 'Updated: ' . $stamp($data, 'updated'),
        'Last written: ' . $stamp($data, 'last_saved'),
        'Buffered intervals: ' . $integer($data, 'pending_intervals'));
    if (empty($data['ok']) || !empty($data['error'])) {
        $error = isset($data['error']) && is_string($data['error']) ? $data['error'] : 'Measurements unavailable';
        $lines[] = 'Status: ' . preg_replace('/[\x00-\x1f\x7f]+/', ' ', substr($error, 0, 500));
    }
    $contexts = isset($data['contexts']) && is_array($data['contexts']) ? array_values($data['contexts']) : array();
    usort($contexts, function ($a, $b) use ($number) {
        $a = $number($a, 'last'); $b = $number($b, 'last');
        return $a == $b ? 0 : ($a < $b ? 1 : -1);
    });
    $rows = array(array('Board', 'Index', 'Chips', 'Observed', 'Accepted Rate', 'Shares',
        'Rejects', 'HW', 'Temp Min/Max', 'Last Seen', 'History'));
    foreach ($contexts as $c) {
        if (!is_array($c)) continue;
        $context = isset($c['context']) && is_array($c['context']) ? $c['context'] : array();
        $seconds = $number($c, 'seconds');
        $difficulty = $number($c, 'difficulty');
        $duration = 'n/a';
        if ($seconds !== null && $seconds >= 0 && $seconds <= PHP_INT_MAX) {
            $s = (int)floor($seconds);
            $duration = ($s >= 86400 ? floor($s / 86400) . 'd ' : '')
                . ($s >= 3600 ? floor(($s % 86400) / 3600) . 'h ' : '')
                . ($s >= 60 ? floor(($s % 3600) / 60) . 'm ' : '') . ($s % 60) . 's';
        }
        $ghs = $seconds > 0 && $difficulty !== null && $difficulty >= 0
            ? $difficulty * 4.294967296 / $seconds : null;
        $rate = $ghs !== null && is_finite($ghs) ? number_format($ghs, 2, '.', '') . ' GH/s' : 'n/a';
        $min = $number($c, 'temp_min'); $max = $number($c, 'temp_max');
        $temp = ($min === null ? 'n/a' : number_format($min, 1, '.', '')) . ' / '
            . ($max === null ? 'n/a' : number_format($max, 1, '.', '')) . ' C';
        $qualified = !empty($c['fixed32']) && $seconds >= 21600 && $number($c, 'accepted') >= 10000;
        $rows[] = array($integer($context, 'board'), $integer($context, 'index'),
            $integer($context, 'startup') . '/' . $integer($context, 'total'), $duration, $rate,
            $integer($c, 'accepted'), $integer($c, 'rejected'), $integer($c, 'hw'), $temp,
            $stamp($c, 'last'), $qualified ? 'Your history' : 'Early history');
    }
    $widths = array_fill(0, count($rows[0]), 0);
    foreach ($rows as $row) foreach ($row as $i => $cell) $widths[$i] = max($widths[$i], strlen($cell));
    $lines[] = '';
    foreach ($rows as $index => $row) {
        $cells = array();
        foreach ($row as $i => $cell) $cells[] = str_pad($cell, $widths[$i]);
        $lines[] = rtrim(implode(' | ', $cells));
        if ($index === 0) $lines[] = implode('-+-', array_map(function ($width) { return str_repeat('-', $width); }, $widths));
    }
    if (count($rows) === 1) $lines[] = 'No measured history';
    return implode("\n", $lines) . "\n";
}

function bs_measured_command($operation, $argument = '') {
    $allowed = array('status', 'target', 'clear', 'reset-board', 'clear-side', 'flush');
    if (!is_string($operation) || !in_array($operation, $allowed, true) || !is_string($argument))
        return array('ok' => false, 'error' => 'Invalid measured-rate command');
    if (($operation === 'target' || $operation === 'clear-side') && !in_array($argument, array('SD', 'RAM'), true))
        return array('ok' => false, 'error' => 'Invalid storage target');
    if ($operation === 'reset-board' && !in_array($argument, array('0', '1', '2'), true))
        return array('ok' => false, 'error' => 'Invalid board');
    $cmd = '/usr/bin/timeout 15 /usr/bin/python /opt/scripta/bin/measured-rate-history.py ' . escapeshellarg($operation);
    if ($argument !== '') $cmd .= ' ' . escapeshellarg($argument);
    $raw = shell_exec($cmd . ' 2>/dev/null');
    $data = json_decode((string)$raw, true);
    return is_array($data) ? $data : array('ok' => false, 'error' => 'Measured-rate operation failed or timed out');
}

function bs_measured_authorized($method, $body, $token) {
    return $method === 'POST' && is_array($body) && isset($body['csrf']) &&
        dev_activity_csrf_accept($token, $body['csrf']);
}

function bs_measured_personal($summary, $board, $stat, $dev, $stats) {
    if (empty($stat['CommandVerified'])) return null;
    if (empty($summary['ok']) || !empty($summary['error']) || !isset($summary['boards'][$board])) return null;
    $row = $summary['boards'][$board];
    if (!bs_accepted_context_matches($row, $stat, $dev) || empty($row['personal']['qualified'])) return null;
    $p = $row['personal'];
    if (!isset($p['context']['indices']) || count($p['context']['indices']) !== 3) return null;
    $indices = array();
    foreach ($stats as $s) {
        if (!isset($s['ID']) || !preg_match('/^BKLU([0-2])$/', $s['ID'], $m)) continue;
        $indices[(int)$m[1]] = isset($s['SentClockIndex']) ? (int)$s['SentClockIndex'] : 0;
    }
    for ($i = 0; $i < 3; $i++) {
        if (!isset($indices[$i]) || $indices[$i] < 8 || $indices[$i] > 32 ||
            $indices[$i] !== (int)$p['context']['indices'][$i]) return null;
    }
    return $p;
}
