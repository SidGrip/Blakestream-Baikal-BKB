<?php
/**
 * Dev Tools activity policy. Pure helpers: no session, no sockets, no USB.
 * Missing values stay null. A published 0 stays "0". Sentinels (-1 attempt,
 * unset clock index 0, info_valid 0) are unavailable, not a measured zero.
 */

function dev_activity_trace_api() {
    return true;
}

function dev_activity_read_command() {
    return 'stats';
}

function dev_activity_export_paths() {
    return array(
        '/run/blakestream/bkb-diag-trace.bin',
        '/tmp/bkb-diag-trace.bin',
    );
}

function dev_activity_export_max_bytes() {
    return (2 * 1024 * 1024) + (64 * 1024);
}

function dev_activity_collector_max_bytes() {
    return 256 * 1024;
}

function dev_activity_collector_path() {
    return '/run/blakestream/bkb-diagnostics-latest.json';
}

function dev_activity_random_bytes($n) {
    $n = (int) $n;
    if ($n < 1) return '';
    if (function_exists('random_bytes')) {
        try {
            $b = random_bytes($n);
            if (is_string($b) && strlen($b) === $n) return $b;
        } catch (Exception $e) {
        }
    }
    if (function_exists('openssl_random_pseudo_bytes')) {
        $strong = false;
        $b = openssl_random_pseudo_bytes($n, $strong);
        if ($strong && is_string($b) && strlen($b) === $n) return $b;
    }
    $fh = @fopen('/dev/urandom', 'rb');
    if ($fh) {
        $b = fread($fh, $n);
        fclose($fh);
        if (is_string($b) && strlen($b) === $n) return $b;
    }
    return '';
}

function dev_activity_csrf_mint() {
    $b = dev_activity_random_bytes(32);
    if (!is_string($b) || strlen($b) !== 32) return '';
    return bin2hex($b);
}

function dev_activity_csrf_well_formed($token) {
    return is_string($token) && preg_match('/^[0-9a-f]{64}$/', $token) === 1;
}

function dev_activity_hash_equals($a, $b) {
    if (!is_string($a) || !is_string($b)) return false;
    if (function_exists('hash_equals')) return hash_equals($a, $b);
    if (strlen($a) !== strlen($b)) return false;
    $r = 0;
    $n = strlen($a);
    for ($i = 0; $i < $n; $i++) $r |= ord($a[$i]) ^ ord($b[$i]);
    return $r === 0;
}

function dev_activity_csrf_accept($session_token, $given) {
    if (!dev_activity_csrf_well_formed($session_token)) return false;
    if (!dev_activity_csrf_well_formed($given)) return false;
    return dev_activity_hash_equals($session_token, $given);
}

function dev_activity_nonneg($v) {
    if (is_bool($v) || is_float($v) || is_array($v) || is_object($v) || $v === null) return null;
    if (is_int($v)) {
        if ($v < 0) return null;
        return (string) $v;
    }
    if (is_string($v)) {
        if (preg_match('/^(0|[1-9][0-9]{0,19})$/', $v) !== 1) return null;
        return $v;
    }
    return null;
}

function dev_activity_positive_code($v) {
    $s = dev_activity_nonneg($v);
    if ($s === null || $s === '0') return null;
    return $s;
}

function dev_activity_bool($v) {
    if ($v === true) return true;
    if ($v === false) return false;
    if ($v === 'true') return true;
    if ($v === 'false') return false;
    return null;
}

function dev_activity_zero_one($v) {
    if ($v === true || $v === 1 || $v === '1') return 1;
    if ($v === false || $v === 0 || $v === '0') return 0;
    return null;
}

function dev_activity_zero_one_bool($v) {
    $n = dev_activity_zero_one($v);
    if ($n === 1) return true;
    if ($n === 0) return false;
    return null;
}

function dev_activity_decode_json($raw, $depth) {
    if (!defined('JSON_BIGINT_AS_STRING')) return null;
    if (!is_string($raw)) return null;
    $raw = trim($raw, "\x00 \t\r\n");
    if ($raw === '') return null;
    $depth = (int) $depth;
    if ($depth < 1) $depth = 512;
    $decoded = json_decode($raw, true, $depth, JSON_BIGINT_AS_STRING);
    if (!is_array($decoded)) return null;
    return $decoded;
}

function dev_activity_cmp_dec($a, $b) {
    if (!is_string($a) || !is_string($b)) return 0;
    $as = ltrim($a, '0');
    $bs = ltrim($b, '0');
    if ($as === '') $as = '0';
    if ($bs === '') $bs = '0';
    if (strlen($as) !== strlen($bs)) return (strlen($as) < strlen($bs)) ? -1 : 1;
    if ($as === $bs) return 0;
    return ($as < $bs) ? -1 : 1;
}

function dev_activity_bit_bool($v) {
    if ($v === true || $v === 1 || $v === '1') return true;
    if ($v === false || $v === 0 || $v === '0') return false;
    return null;
}

function dev_activity_info_value($v) {
    if ($v === 0 || $v === '0') return 0;
    if ($v === 1 || $v === '1') return 1;
    return null;
}

function dev_activity_pick($row, $keys, $norm) {
    $saw = false;
    $val = null;
    $conflict = false;
    if (!is_array($row)) return array(false, null, false);
    foreach ($keys as $k) {
        if (!array_key_exists($k, $row) || $row[$k] === null) continue;
        $n = call_user_func($norm, $row[$k]);
        if (!$saw) {
            $saw = true;
            $val = $n;
        } elseif ($val !== $n) {
            $conflict = true;
        }
    }
    if ($conflict) return array(true, null, true);
    return array($saw, $val, false);
}

function dev_activity_parse_grid($v) {
    $flat = null;
    if (is_string($v)) {
        if ($v === '') return null;
        $parts = explode(',', $v);
        if (count($parts) !== 64) return null;
        $flat = array();
        foreach ($parts as $part) $flat[] = trim($part);
    } elseif (is_array($v)) {
        $matrix = (count($v) === 4);
        $flat_try = array();
        if ($matrix) {
            for ($u = 0; $u < 4; $u++) {
                if (!isset($v[$u]) || !is_array($v[$u]) || count($v[$u]) !== 16) {
                    $matrix = false;
                    break;
                }
                for ($c = 0; $c < 16; $c++) {
                    if (!array_key_exists($c, $v[$u])) {
                        $matrix = false;
                        break;
                    }
                    $flat_try[] = $v[$u][$c];
                }
                if (!$matrix) break;
            }
        }
        if ($matrix && count($flat_try) === 64) {
            $flat = $flat_try;
        } elseif (count($v) === 64) {
            $flat = array_values($v);
        } else {
            return null;
        }
    } else {
        return null;
    }
    if (!is_array($flat) || count($flat) !== 64) return null;
    $grid = array();
    for ($u = 0; $u < 4; $u++) {
        $grid[$u] = array();
        for ($c = 0; $c < 16; $c++) {
            $cell = dev_activity_nonneg($flat[($u * 16) + $c]);
            if ($cell === null) return null;
            $grid[$u][$c] = $cell;
        }
    }
    return $grid;
}

function dev_activity_extension_keys() {
    return array(
        'StartupSuccessCount', 'AsicCountR', 'AsicCountSuccess', 'asic_count_r',
        'AsicVersion', 'ASICVersion', 'AsicVer', 'asic_ver',
        'LocalNoncesRaw', 'NonceGrid', 'LocalNonceGrid', 'DiagNonceGrid', 'nonce_grid',
        'LocalErrorsRaw', 'ErrorGrid', 'LocalErrorGrid', 'DiagErrorGrid', 'error_grid',
        'DiagBoard', 'DiagReplyMismatch', 'reply_mismatch',
        'DiagLastInfoOk', 'DiagLastResultOk', 'DiagSnapMonoUs', 'DiagUptimeUs',
        'DiagPid', 'ProcessPid', 'diag_pid',
        'DiagStartUnix', 'ProcessStartUnix', 'diag_start_unix',
        'DiagStatEpoch', 'stat_epoch',
        'RouteEpoch', 'DiagRouteEpoch', 'route_epoch',
        'EnableEpoch', 'EnablementEpoch', 'DiagEnableEpoch', 'enable_epoch',
        'DiagInfoValid', 'info_valid',
        'DiagAttemptedClockCode', 'AttemptedClockCode', 'attempted_clock_code',
        'AckedClockCode', 'acked_clock_code',
        'DiagInvalidId', 'invalid_id',
        'DiagInvalidWorkIdx', 'DiagInvalidWorkidx', 'invalid_workidx',
        'DiagMissingWork', 'missing_work',
        'DiagAlgoMismatch', 'algo_mismatch',
        'DiagShortReply', 'short_reply',
        'DiagWorkRequests', 'work_requests',
        'DiagWorkSends', 'work_sends',
        'DiagSkipOverheat', 'skip_overheat',
        'DiagSkipNoTarget', 'skip_no_target',
        'DiagSkipDisabled', 'skip_disabled',
        'DiagSkipEmpty', 'skip_empty',
        'DiagUsbTxErrors', 'DiagUsbTxError', 'usb_tx_error',
        'DiagUsbRxErrors', 'DiagUsbRxError', 'usb_rx_error',
        'DiagWorkWaitUsSum', 'work_wait_us_sum',
        'DiagWorkWaitUsMax', 'work_wait_us_max',
        'DiagWorkWaitCount', 'work_wait_count',
        'DiagAckLatencyUsSum', 'ack_latency_us_sum',
        'DiagAckLatencyUsMax', 'ack_latency_us_max',
        'DiagAckLatencyCount', 'ack_latency_count',
        'DiagRequestToAckUsSum', 'request_to_ack_us_sum',
        'DiagRequestToAckUsMax', 'request_to_ack_us_max',
        'DiagRequestToAckCount', 'request_to_ack_count',
        'DiagPollGapUsMax', 'poll_gap_us_max',
        'DiagPollGapUsLast', 'poll_gap_us_last',
        'DiagTraceEnabled', 'TraceEnabled', 'trace_enabled',
        'DiagTraceLive', 'TraceLive', 'trace_live',
        'DiagTraceUsed',
        'DiagTraceCapacity', 'TraceCapacity', 'trace_capacity',
        'DiagTraceOverwritten', 'TraceOverwritten', 'trace_overwritten',
        'DiagTraceDropped', 'TraceDropped', 'trace_dropped',
        'DiagTraceRecorded', 'TraceRecorded', 'trace_recorded',
        'DiagTraceExport',
    );
}

function dev_activity_row_has_extension($row) {
    if (!is_array($row)) return false;
    foreach (dev_activity_extension_keys() as $k) {
        if (array_key_exists($k, $row)) return true;
    }
    return false;
}

function dev_activity_empty_board($id, $present) {
    return array(
        'id' => (int) $id,
        'present' => $present ? true : false,
        'asic_count' => null,
        'asic_count_r' => null,
        'asic_ver' => null,
        'info_valid' => null,
        'requested_clock' => null,
        'attempted_clock_index' => null,
        'last_ack' => null,
        'bridge_status_echo' => null,
        'bridge_status_echo_code' => null,
        'command_verified' => null,
        'last_info_ok' => null,
        'last_result_ok' => null,
        'nonce' => null,
        'error' => null,
        'discard' => array(
            'invalid_id' => null,
            'invalid_workidx' => null,
            'missing_work' => null,
            'algo_mismatch' => null,
            'short_reply' => null,
            'reply_mismatch' => null,
        ),
        'timing' => array(
            'work_wait_us_sum' => null,
            'work_wait_us_max' => null,
            'work_wait_count' => null,
            'request_to_ack_us_sum' => null,
            'request_to_ack_us_max' => null,
            'request_to_ack_count' => null,
            'ack_latency_us_sum' => null,
            'ack_latency_us_max' => null,
            'ack_latency_count' => null,
            'poll_gap_us_max' => null,
            'poll_gap_us_last' => null,
            'uptime_us' => null,
        ),
        'work' => array(
            'requests' => null,
            'sends' => null,
            'skip_overheat' => null,
            'skip_no_target' => null,
            'skip_disabled' => null,
            'skip_empty' => null,
        ),
        'usb' => array(
            'tx_error' => null,
            'rx_error' => null,
        ),
        'epoch' => array(
            'pid' => null,
            'start_unix' => null,
            'stat' => null,
            'route' => null,
            'enable' => null,
        ),
    );
}

function dev_activity_empty_trace() {
    return array(
        'enabled' => null,
        'live' => null,
        'used' => null,
        'capacity' => null,
        'overwritten' => null,
        'dropped' => null,
        'recorded' => null,
        'export' => null,
    );
}

function dev_activity_apply_count_group($row, &$dst, $map) {
    foreach ($map as $field => $keys) {
        list($saw, $val, $conflict) = dev_activity_pick($row, $keys, 'dev_activity_nonneg');
        if ($conflict || $saw) $dst[$field] = $conflict ? null : $val;
    }
}

function dev_activity_parse_board_fields($row) {
    $b = dev_activity_empty_board(0, true);
    if (!is_array($row)) return $b;

    list($info_saw, $info_val, $info_conflict) = dev_activity_pick(
        $row,
        array('DiagInfoValid', 'info_valid'),
        'dev_activity_info_value'
    );
    $info_state = 'absent';
    if ($info_conflict || ($info_saw && $info_val !== 0 && $info_val !== 1)) {
        $info_state = 'bad';
    } elseif ($info_saw && $info_val === 0) {
        $info_state = '0';
        $b['info_valid'] = 0;
    } elseif ($info_saw && $info_val === 1) {
        $info_state = '1';
        $b['info_valid'] = 1;
    }

    list(, $asic, $asic_conflict) = dev_activity_pick(
        $row,
        array('Chip Count', 'AsicCount', 'ChipCount', 'asic_count'),
        'dev_activity_nonneg'
    );
    list(, $asic_r, $asic_r_conflict) = dev_activity_pick(
        $row,
        array('StartupSuccessCount', 'AsicCountR', 'AsicCountSuccess', 'asic_count_r'),
        'dev_activity_nonneg'
    );
    list(, $asic_ver, $ver_conflict) = dev_activity_pick(
        $row,
        array('AsicVersion', 'ASICVersion', 'AsicVer', 'asic_ver'),
        'dev_activity_nonneg'
    );
    $b['asic_count'] = $asic_conflict ? null : $asic;
    $b['asic_count_r'] = $asic_r_conflict ? null : $asic_r;
    $b['asic_ver'] = $ver_conflict ? null : $asic_ver;

    list(, $req, $req_conflict) = dev_activity_pick($row, array('RequestedClock'), 'dev_activity_nonneg');
    $b['requested_clock'] = $req_conflict ? null : $req;

    list(, $attempt, $attempt_conflict) = dev_activity_pick(
        $row,
        array('DiagAttemptedClockCode', 'AttemptedClockCode', 'attempted_clock_code'),
        'dev_activity_nonneg'
    );
    $b['attempted_clock_index'] = $attempt_conflict ? null : $attempt;

    list($ack_saw, $ack, $ack_conflict) = dev_activity_pick(
        $row,
        array('AckedClockCode', 'acked_clock_code'),
        'dev_activity_positive_code'
    );
    if ($ack_saw || $ack_conflict) {
        $b['last_ack'] = $ack_conflict ? null : $ack;
    } else {
        list(, $sent, $sent_conflict) = dev_activity_pick(
            $row,
            array('SentClockIndex'),
            'dev_activity_positive_code'
        );
        $b['last_ack'] = $sent_conflict ? null : $sent;
    }

    $echo_norm = ($info_state === '1') ? 'dev_activity_nonneg' : 'dev_activity_positive_code';
    list(, $echo, $echo_conflict) = dev_activity_pick($row, array('BridgeStatusEcho'), $echo_norm);
    list(, $echo_code, $echo_code_conflict) = dev_activity_pick($row, array('BridgeStatusEchoCode'), $echo_norm);
    $b['bridge_status_echo'] = $echo_conflict ? null : $echo;
    $b['bridge_status_echo_code'] = $echo_code_conflict ? null : $echo_code;

    if ($info_state === '0' || $info_state === 'bad') {
        $b['asic_count'] = null;
        $b['asic_count_r'] = null;
        $b['asic_ver'] = null;
        $b['bridge_status_echo'] = null;
        $b['bridge_status_echo_code'] = null;
    }

    list(, $verified, $verified_conflict) = dev_activity_pick(
        $row,
        array('CommandVerified'),
        'dev_activity_bool'
    );
    $b['command_verified'] = $verified_conflict ? null : $verified;

    list(, $last_info, $last_info_conflict) = dev_activity_pick($row, array('DiagLastInfoOk'), 'dev_activity_zero_one');
    list(, $last_result, $last_result_conflict) = dev_activity_pick($row, array('DiagLastResultOk'), 'dev_activity_zero_one');
    $b['last_info_ok'] = $last_info_conflict ? null : $last_info;
    $b['last_result_ok'] = $last_result_conflict ? null : $last_result;

    list(, $nonce, $nonce_conflict) = dev_activity_pick(
        $row,
        array('LocalNoncesRaw', 'NonceGrid', 'LocalNonceGrid', 'DiagNonceGrid', 'nonce_grid'),
        'dev_activity_parse_grid'
    );
    list(, $error, $error_conflict) = dev_activity_pick(
        $row,
        array('LocalErrorsRaw', 'ErrorGrid', 'LocalErrorGrid', 'DiagErrorGrid', 'error_grid'),
        'dev_activity_parse_grid'
    );
    $b['nonce'] = $nonce_conflict ? null : $nonce;
    $b['error'] = $error_conflict ? null : $error;

    dev_activity_apply_count_group($row, $b['discard'], array(
        'invalid_id' => array('DiagInvalidId', 'invalid_id'),
        'invalid_workidx' => array('DiagInvalidWorkIdx', 'DiagInvalidWorkidx', 'invalid_workidx'),
        'missing_work' => array('DiagMissingWork', 'missing_work'),
        'algo_mismatch' => array('DiagAlgoMismatch', 'algo_mismatch'),
        'short_reply' => array('DiagShortReply', 'short_reply'),
        'reply_mismatch' => array('DiagReplyMismatch', 'reply_mismatch'),
    ));
    dev_activity_apply_count_group($row, $b['timing'], array(
        'work_wait_us_sum' => array('DiagWorkWaitUsSum', 'work_wait_us_sum'),
        'work_wait_us_max' => array('DiagWorkWaitUsMax', 'work_wait_us_max'),
        'work_wait_count' => array('DiagWorkWaitCount', 'work_wait_count'),
        'request_to_ack_us_sum' => array('DiagRequestToAckUsSum', 'request_to_ack_us_sum'),
        'request_to_ack_us_max' => array('DiagRequestToAckUsMax', 'request_to_ack_us_max'),
        'request_to_ack_count' => array('DiagRequestToAckCount', 'request_to_ack_count'),
        'ack_latency_us_sum' => array('DiagAckLatencyUsSum', 'ack_latency_us_sum'),
        'ack_latency_us_max' => array('DiagAckLatencyUsMax', 'ack_latency_us_max'),
        'ack_latency_count' => array('DiagAckLatencyCount', 'ack_latency_count'),
        'poll_gap_us_max' => array('DiagPollGapUsMax', 'poll_gap_us_max'),
        'poll_gap_us_last' => array('DiagPollGapUsLast', 'poll_gap_us_last'),
        'uptime_us' => array('DiagUptimeUs'),
    ));
    dev_activity_apply_count_group($row, $b['work'], array(
        'requests' => array('DiagWorkRequests', 'work_requests'),
        'sends' => array('DiagWorkSends', 'work_sends'),
        'skip_overheat' => array('DiagSkipOverheat', 'skip_overheat'),
        'skip_no_target' => array('DiagSkipNoTarget', 'skip_no_target'),
        'skip_disabled' => array('DiagSkipDisabled', 'skip_disabled'),
        'skip_empty' => array('DiagSkipEmpty', 'skip_empty'),
    ));
    dev_activity_apply_count_group($row, $b['usb'], array(
        'tx_error' => array('DiagUsbTxErrors', 'DiagUsbTxError', 'usb_tx_error'),
        'rx_error' => array('DiagUsbRxErrors', 'DiagUsbRxError', 'usb_rx_error'),
    ));

    $epoch_norm = 'dev_activity_nonneg';
    list(, $pid, $pid_conflict) = dev_activity_pick($row, array('DiagPid', 'ProcessPid', 'diag_pid'), $epoch_norm);
    list(, $start, $start_conflict) = dev_activity_pick($row, array('DiagStartUnix', 'ProcessStartUnix', 'diag_start_unix'), $epoch_norm);
    list(, $stat_epoch, $stat_conflict) = dev_activity_pick($row, array('DiagStatEpoch', 'stat_epoch'), $epoch_norm);
    list(, $route, $route_conflict) = dev_activity_pick($row, array('RouteEpoch', 'DiagRouteEpoch', 'route_epoch'), $epoch_norm);
    list(, $enable, $enable_conflict) = dev_activity_pick($row, array('EnableEpoch', 'EnablementEpoch', 'DiagEnableEpoch', 'enable_epoch'), $epoch_norm);
    if ($pid === '0') $pid = null;
    if ($start === '0') $start = null;
    $b['epoch'] = array(
        'pid' => $pid_conflict ? null : $pid,
        'start_unix' => $start_conflict ? null : $start,
        'stat' => $stat_conflict ? null : $stat_epoch,
        'route' => $route_conflict ? null : $route,
        'enable' => $enable_conflict ? null : $enable,
    );
    return $b;
}

function dev_activity_board_index($v) {
    if (is_int($v) && $v >= 0 && $v <= 2) return $v;
    if (is_string($v) && preg_match('/^[0-2]$/', $v) === 1) return (int) $v;
    return null;
}

function dev_activity_row_board_id($row) {
    if (!is_array($row)) return null;
    if (array_key_exists('DiagBoard', $row) && $row['DiagBoard'] !== null) {
        return dev_activity_board_index($row['DiagBoard']);
    }
    $from_id = null;
    if (isset($row['ID']) && is_string($row['ID']) && preg_match('/^BKLU([0-2])$/', $row['ID'], $m)) {
        $from_id = (int) $m[1];
    }
    $from_asc = null;
    if (array_key_exists('ASC', $row)) {
        $from_asc = dev_activity_board_index($row['ASC']);
    }
    if ($from_id !== null && $from_asc !== null && $from_id !== $from_asc) return null;
    if ($from_id !== null) return $from_id;
    return $from_asc;
}

function dev_activity_trace_export_value($v) {
    if (!is_string($v) || $v === '') return null;
    if (!in_array($v, dev_activity_export_paths(), true)) return null;
    return $v;
}

function dev_activity_trace_from_row($row) {
    if (!is_array($row)) return null;
    $fields = array(
        'enabled' => array(array('DiagTraceEnabled', 'TraceEnabled', 'trace_enabled'), 'dev_activity_zero_one_bool'),
        'live' => array(array('DiagTraceLive', 'TraceLive', 'trace_live'), 'dev_activity_nonneg'),
        'used' => array(array('DiagTraceUsed'), 'dev_activity_nonneg'),
        'capacity' => array(array('DiagTraceCapacity', 'TraceCapacity', 'trace_capacity'), 'dev_activity_nonneg'),
        'overwritten' => array(array('DiagTraceOverwritten', 'TraceOverwritten', 'trace_overwritten'), 'dev_activity_nonneg'),
        'dropped' => array(array('DiagTraceDropped', 'TraceDropped', 'trace_dropped'), 'dev_activity_nonneg'),
        'recorded' => array(array('DiagTraceRecorded', 'TraceRecorded', 'trace_recorded'), 'dev_activity_nonneg'),
    );
    $out = dev_activity_empty_trace();
    $any = false;
    foreach ($fields as $name => $spec) {
        list($saw, $val, $conflict) = dev_activity_pick($row, $spec[0], $spec[1]);
        if ($conflict) return null;
        if ($saw) $any = true;
        $out[$name] = $saw ? $val : null;
    }
    if (array_key_exists('DiagTraceExport', $row) && $row['DiagTraceExport'] !== null && $row['DiagTraceExport'] !== '') {
        $any = true;
        $out['export'] = dev_activity_trace_export_value($row['DiagTraceExport']);
    }
    if (!$any) return null;
    list($snap_saw, $snap, $snap_conflict) = dev_activity_pick($row, array('DiagSnapMonoUs'), 'dev_activity_nonneg');
    if ($snap_conflict || !$snap_saw || $snap === null) return null;
    return array('snap' => $snap, 'trace' => $out);
}

function dev_activity_trace_same($a, $b) {
    foreach (array('enabled', 'live', 'used', 'capacity', 'overwritten', 'dropped', 'recorded', 'export') as $k) {
        if (!isset($a[$k]) && !isset($b[$k])) continue;
        $av = isset($a[$k]) ? $a[$k] : null;
        $bv = isset($b[$k]) ? $b[$k] : null;
        if ($av !== $bv) return false;
    }
    return true;
}

function dev_activity_select_trace($rows) {
    $best = null;
    $best_snap = null;
    $conflict = false;
    foreach ($rows as $row) {
        $picked = dev_activity_trace_from_row($row);
        if ($picked === null) continue;
        if ($best === null || dev_activity_cmp_dec($picked['snap'], $best_snap) > 0) {
            $best = $picked['trace'];
            $best_snap = $picked['snap'];
            $conflict = false;
        } elseif (dev_activity_cmp_dec($picked['snap'], $best_snap) === 0) {
            if (!dev_activity_trace_same($picked['trace'], $best)) $conflict = true;
        }
    }
    if ($conflict || $best === null) return dev_activity_empty_trace();
    return $best;
}

function dev_activity_merge_shared($rows, $spec) {
    $out = dev_activity_empty_trace();
    foreach ($spec as $field => $info) {
        $keys = $info[0];
        $norm = $info[1];
        $saw = false;
        $val = null;
        $bad = false;
        foreach ($rows as $row) {
            list($present, $v, $conflict) = dev_activity_pick($row, $keys, $norm);
            if ($conflict) {
                $bad = true;
                continue;
            }
            if (!$present) continue;
            if (!$saw) {
                $saw = true;
                $val = $v;
            } elseif ($val !== $v) {
                $bad = true;
            }
        }
        $out[$field] = $bad ? null : ($saw ? $val : null);
    }
    return $out;
}

function dev_activity_parse_stats($stats) {
    $boards = array(
        0 => dev_activity_empty_board(0, false),
        1 => dev_activity_empty_board(1, false),
        2 => dev_activity_empty_board(2, false),
    );
    $trace_rows = array();
    $seen = array();
    $rows = array();
    $extension = false;
    if (is_array($stats) && isset($stats['STATS']) && is_array($stats['STATS'])) {
        $rows = $stats['STATS'];
    }
    foreach ($rows as $row) {
        if (!is_array($row)) continue;
        if (dev_activity_row_has_extension($row)) $extension = true;
        $id = dev_activity_row_board_id($row);
        if ($id === null) continue;
        $trace_rows[] = $row;
        if (!empty($seen[$id])) {
            $boards[$id] = dev_activity_empty_board($id, true);
            continue;
        }
        $seen[$id] = true;
        $parsed = dev_activity_parse_board_fields($row);
        $parsed['id'] = $id;
        $parsed['present'] = true;
        $boards[$id] = $parsed;
    }
    $trace = dev_activity_select_trace($trace_rows);
    return array(
        'boards' => $boards,
        'trace' => $trace,
        'extension' => $extension,
    );
}

function dev_activity_unix($v) {
    if (is_bool($v) || $v === null) return null;
    if (is_int($v) && $v >= 0) return $v;
    if (is_string($v) && preg_match('/^(0|[1-9][0-9]{0,9})$/', $v) === 1) {
        $n = (int) $v;
        if ((string) $n === $v || $v === '0') return $n;
        return null;
    }
    if (is_float($v) && $v >= 0 && $v < 4000000000) return (int) $v;
    return null;
}

function dev_activity_parse_collector($decoded) {
    if (!is_array($decoded)) return null;
    if (!isset($decoded['v']) || $decoded['v'] !== 1) return null;
    if (!isset($decoded['boards']) || !is_array($decoded['boards'])) return null;
    $boards = array(
        0 => null,
        1 => null,
        2 => null,
    );
    $list = array_values($decoded['boards']);
    foreach ($list as $board) {
        if (!is_array($board) || !array_key_exists('id', $board)) continue;
        $id = $board['id'];
        if ($id === '0' || $id === '1' || $id === '2') $id = (int) $id;
        if (!is_int($id) || $id < 0 || $id > 2) continue;
        $sample = (isset($board['sample']) && is_array($board['sample'])) ? $board['sample'] : array();
        $diag = (isset($sample['diag']) && is_array($sample['diag'])) ? $sample['diag'] : array();
        $out = dev_activity_empty_board($id, true);
        if (array_key_exists('ac', $sample)) $out['asic_count'] = dev_activity_nonneg($sample['ac']);
        if (array_key_exists('ss', $sample)) $out['asic_count_r'] = dev_activity_nonneg($sample['ss']);
        if (array_key_exists('av', $sample)) $out['asic_ver'] = dev_activity_nonneg($sample['av']);
        if (array_key_exists('rc', $sample)) $out['requested_clock'] = dev_activity_nonneg($sample['rc']);
        if (array_key_exists('cv', $sample)) $out['command_verified'] = dev_activity_bit_bool($sample['cv']);
        if (array_key_exists('sc', $sample)) $out['last_ack'] = dev_activity_positive_code($sample['sc']);
        if (array_key_exists('attempted_clock_code', $diag)) {
            $out['attempted_clock_index'] = dev_activity_nonneg($diag['attempted_clock_code']);
        }
        $discard = array(
            'invalid_id' => 'invalid_id',
            'invalid_workidx' => 'invalid_workidx',
            'missing_work' => 'missing_work',
            'algo_mismatch' => 'algo_mismatch',
            'short_reply' => 'short_reply',
            'reply_mismatch' => 'reply_mismatch',
        );
        foreach ($discard as $dst => $src) {
            if (array_key_exists($src, $diag)) $out['discard'][$dst] = dev_activity_nonneg($diag[$src]);
        }
        $timing = array(
            'work_wait_us_sum', 'work_wait_us_max', 'work_wait_count',
            'request_to_ack_us_sum', 'request_to_ack_us_max', 'request_to_ack_count',
            'ack_latency_us_sum', 'ack_latency_us_max', 'ack_latency_count',
            'poll_gap_us_max', 'poll_gap_us_last',
        );
        foreach ($timing as $src) {
            if (array_key_exists($src, $diag)) $out['timing'][$src] = dev_activity_nonneg($diag[$src]);
        }
        $work = array(
            'requests' => 'work_requests',
            'sends' => 'work_sends',
            'skip_overheat' => 'skip_overheat',
            'skip_no_target' => 'skip_no_target',
            'skip_disabled' => 'skip_disabled',
            'skip_empty' => 'skip_empty',
        );
        foreach ($work as $dst => $src) {
            if (array_key_exists($src, $diag)) $out['work'][$dst] = dev_activity_nonneg($diag[$src]);
        }
        if (array_key_exists('usb_tx_error', $diag)) $out['usb']['tx_error'] = dev_activity_nonneg($diag['usb_tx_error']);
        if (array_key_exists('usb_rx_error', $diag)) $out['usb']['rx_error'] = dev_activity_nonneg($diag['usb_rx_error']);
        $out = dev_activity_overlay_driver_sample($out, $sample);
        $out['nonce'] = null;
        $out['error'] = null;
        $out['epoch'] = array(
            'pid' => null,
            'start_unix' => null,
            'stat' => null,
            'route' => null,
            'enable' => null,
        );
        $boards[$id] = $out;
    }
    return array(
        'updated' => array_key_exists('updated', $decoded) ? dev_activity_unix($decoded['updated']) : null,
        'boards' => $boards,
    );
}

function dev_activity_overlay_driver_sample($out, $sample) {
    if (!is_array($sample)) return $out;
    $hit = false;
    foreach (dev_activity_extension_keys() as $key) {
        if (array_key_exists($key, $sample)) {
            $hit = true;
            break;
        }
    }
    if (!$hit) return $out;
    $parsed = dev_activity_parse_board_fields($sample);
    $identity = array(
        'asic_count', 'asic_count_r', 'asic_ver', 'info_valid', 'requested_clock',
        'attempted_clock_index', 'last_ack', 'bridge_status_echo', 'bridge_status_echo_code',
        'command_verified', 'last_info_ok', 'last_result_ok',
    );
    foreach ($identity as $key) {
        if ($parsed[$key] !== null) $out[$key] = $parsed[$key];
    }
    if ($parsed['info_valid'] === 0) {
        $out['asic_count'] = null;
        $out['asic_count_r'] = null;
        $out['asic_ver'] = null;
        $out['bridge_status_echo'] = null;
        $out['bridge_status_echo_code'] = null;
    }
    foreach (array('discard', 'timing', 'work', 'usb') as $group) {
        foreach ($parsed[$group] as $key => $val) {
            if ($val !== null && array_key_exists($key, $out[$group])) $out[$group][$key] = $val;
        }
    }
    return $out;
}

function dev_activity_run_dir_is_real($path) {
    if (!is_string($path) || $path === '') return false;
    if (is_link($path)) return false;
    $st = @lstat($path);
    if (!is_array($st) || !isset($st['mode'])) return false;
    $type = $st['mode'] & 0170000;
    if ($type === 0120000) return false;
    return $type === 0040000;
}

function dev_activity_export_path_for_run_dir($is_real_dir) {
    if ($is_real_dir) return '/run/blakestream/bkb-diag-trace.bin';
    return '/tmp/bkb-diag-trace.bin';
}

function dev_activity_selected_export_path() {
    return dev_activity_export_path_for_run_dir(dev_activity_run_dir_is_real('/run/blakestream'));
}

function dev_activity_collector_candidates_for($run_is_real) {
    if (!$run_is_real) return array();
    return array(dev_activity_collector_path());
}

function dev_activity_collector_candidates() {
    return dev_activity_collector_candidates_for(dev_activity_run_dir_is_real('/run/blakestream'));
}

function dev_activity_path_allowed($path, $allowed) {
    if (!is_string($path) || $path === '' || $path[0] !== '/') return false;
    if (strpos($path, "\0") !== false) return false;
    if (strpos($path, '..') !== false) return false;
    if (strpos($path, '//') !== false) return false;
    if (!is_array($allowed) || !in_array($path, $allowed, true)) return false;
    return true;
}

function dev_activity_open_export($path, $allowed, $max_bytes) {
    $fail = function ($err) {
        return array('ok' => false, 'error' => $err, 'fp' => null, 'size' => 0);
    };
    if (!dev_activity_path_allowed($path, $allowed)) return $fail('rejected');
    if (is_link($path)) return $fail('rejected');
    clearstatcache(true, $path);
    $st = @lstat($path);
    if (!is_array($st) || !isset($st['mode'])) return $fail('missing');
    $type = $st['mode'] & 0170000;
    if ($type === 0120000) return $fail('rejected');
    if ($type !== 0100000) return $fail('rejected');
    $size = isset($st['size']) ? (int) $st['size'] : -1;
    if ($size < 10 || $size > (int) $max_bytes) return $fail('rejected');
    $real = realpath($path);
    if ($real === false || $real !== $path) return $fail('rejected');
    $fp = @fopen($path, 'rb');
    if (!$fp) return $fail('rejected');
    $fst = fstat($fp);
    if (!is_array($fst) || !isset($fst['ino']) || !isset($st['ino']) ||
        $fst['ino'] !== $st['ino'] || $fst['dev'] !== $st['dev'] || (int) $fst['size'] !== $size) {
        fclose($fp);
        return $fail('rejected');
    }
    $magic = fread($fp, 10);
    if ($magic !== "BKBTRACE1\n") {
        fclose($fp);
        return $fail('rejected');
    }
    if (fseek($fp, 0, SEEK_SET) !== 0) {
        fclose($fp);
        return $fail('rejected');
    }
    return array('ok' => true, 'error' => '', 'fp' => $fp, 'size' => $size);
}

function dev_activity_open_selected_export() {
    $path = dev_activity_selected_export_path();
    $known = dev_activity_export_paths();
    if (!in_array($path, $known, true)) {
        return array('ok' => false, 'error' => 'rejected', 'fp' => null, 'size' => 0);
    }
    return dev_activity_open_export($path, array($path), dev_activity_export_max_bytes());
}

function dev_activity_stream_fp($fp, $size, $max_bytes) {
    $left = (int) $size;
    if ($left < 0) $left = 0;
    $max_bytes = (int) $max_bytes;
    if ($left > $max_bytes) $left = $max_bytes;
    while ($left > 0) {
        $chunk = fread($fp, $left > 8192 ? 8192 : $left);
        if ($chunk === false || $chunk === '') break;
        $n = strlen($chunk);
        if ($n > $left) {
            echo substr($chunk, 0, $left);
            break;
        }
        echo $chunk;
        $left -= $n;
    }
}

function dev_activity_read_collector_file($path, $allowed_path, $max_bytes) {
    if (!is_string($allowed_path) || $path !== $allowed_path) return null;
    if (!dev_activity_path_allowed($path, array($allowed_path))) return null;
    if (is_link($path)) return null;
    clearstatcache(true, $path);
    $st = @lstat($path);
    if (!is_array($st) || !isset($st['mode'])) return null;
    $type = $st['mode'] & 0170000;
    if ($type !== 0100000) return null;
    $size = isset($st['size']) ? (int) $st['size'] : -1;
    if ($size < 2 || $size > (int) $max_bytes) return null;
    $real = realpath($path);
    if ($real === false || $real !== $path) return null;
    $fp = @fopen($path, 'rb');
    if (!$fp) return null;
    $fst = fstat($fp);
    if (!is_array($fst) || $fst['ino'] !== $st['ino'] || $fst['dev'] !== $st['dev'] || (int) $fst['size'] !== $size) {
        fclose($fp);
        return null;
    }
    $raw = fread($fp, $size);
    fclose($fp);
    if (!is_string($raw) || strlen($raw) !== $size) return null;
    $decoded = dev_activity_decode_json($raw, 16);
    if (!is_array($decoded)) return null;
    return dev_activity_parse_collector($decoded);
}

function dev_activity_load_production_collector() {
    $paths = dev_activity_collector_candidates();
    if (!count($paths)) return null;
    return dev_activity_read_collector_file($paths[0], $paths[0], dev_activity_collector_max_bytes());
}

function dev_activity_build($stats, $collector, $csrf, $error) {
    $support = 'unreachable';
    $parsed = array(
        'boards' => array(
            0 => dev_activity_empty_board(0, false),
            1 => dev_activity_empty_board(1, false),
            2 => dev_activity_empty_board(2, false),
        ),
        'trace' => dev_activity_empty_trace(),
        'extension' => false,
    );
    $live = false;
    if ($error === null) {
        if (!is_array($stats) || !isset($stats['STATS']) || !is_array($stats['STATS'])) {
            $error = 'stats unavailable';
        } else {
            $live = true;
            $parsed = dev_activity_parse_stats($stats);
            $support = $parsed['extension'] ? 'ok' : 'legacy';
        }
    }
    $used_collector = false;
    $collector_at = null;
    if ($error === null && is_array($collector) && isset($collector['boards']) && is_array($collector['boards'])) {
        $used_collector = true;
        $collector_at = array_key_exists('updated', $collector) ? $collector['updated'] : null;
    }
    $boards = array($parsed['boards'][0], $parsed['boards'][1], $parsed['boards'][2]);
    return array(
        'ok' => ($error === null),
        'error' => $error,
        'csrf' => $csrf,
        'generated_at' => time(),
        'support' => $support,
        'trace_api' => dev_activity_trace_api() ? true : false,
        'sources' => array(
            'live' => $live,
            'collector' => $used_collector,
            'collector_at' => $used_collector ? $collector_at : null,
        ),
        'boards' => $boards,
        'trace' => ($error === null) ? $parsed['trace'] : dev_activity_empty_trace(),
    );
}

function dev_activity_trace_spec($op) {
    $map = array(
        'enable' => '0,diag-trace,on',
        'disable' => '0,diag-trace,off',
        'clear' => '0,diag-trace,clear',
        'export' => '0,diag-trace,export',
    );
    if (!is_string($op) || !isset($map[$op])) return null;
    return array('command' => 'ascset', 'parameter' => $map[$op]);
}

function dev_activity_mutation_kind($op) {
    if ($op === 'download') return 'file';
    if (dev_activity_trace_spec($op) !== null) return 'api';
    return null;
}

function dev_activity_sgminer_allowed($command, $parameter) {
    if (!is_string($command) || !is_string($parameter)) return false;
    if ($command === 'stats' && $parameter === '') return true;
    if ($command !== 'ascset') return false;
    $allowed = array(
        '0,diag-trace,on',
        '0,diag-trace,off',
        '0,diag-trace,clear',
        '0,diag-trace,export',
    );
    return in_array($parameter, $allowed, true);
}

function dev_activity_read_stream($fp, $max) {
    if (!is_resource($fp)) return '';
    $max = (int) $max;
    if ($max < 1) return '';
    $buf = '';
    while (strlen($buf) < $max) {
        $need = $max - strlen($buf);
        $chunk = fread($fp, $need);
        if (!is_string($chunk) || $chunk === '') break;
        if (strlen($chunk) > $need) $chunk = substr($chunk, 0, $need);
        $buf .= $chunk;
    }
    return $buf;
}

function dev_activity_parse_mutation($method, $raw, $content_length, $session_token) {
    if ($method !== 'POST') {
        return array('ok' => false, 'status' => 405, 'error' => 'method');
    }
    if ($content_length !== null && (int) $content_length > 4096) {
        return array('ok' => false, 'status' => 413, 'error' => 'body');
    }
    if (!is_string($raw) || strlen($raw) > 4096) {
        return array('ok' => false, 'status' => 413, 'error' => 'body');
    }
    $body = json_decode($raw, true);
    if (!is_array($body)) {
        return array('ok' => false, 'status' => 400, 'error' => 'bad json');
    }
    $keys = array_keys($body);
    sort($keys);
    if ($keys !== array('csrf', 'op')) {
        return array('ok' => false, 'status' => 400, 'error' => 'rejected');
    }
    if (!is_string($body['op']) || dev_activity_mutation_kind($body['op']) === null) {
        return array('ok' => false, 'status' => 400, 'error' => 'bad op');
    }
    if (!dev_activity_csrf_accept($session_token, isset($body['csrf']) ? $body['csrf'] : '')) {
        return array('ok' => false, 'status' => 403, 'error' => 'forbidden');
    }
    return array('ok' => true, 'status' => 200, 'error' => null, 'op' => $body['op']);
}

function dev_activity_ascset_ok($decoded) {
    if (!is_array($decoded) || !isset($decoded['STATUS']) || !is_array($decoded['STATUS'])) return false;
    if (!count($decoded['STATUS'])) return false;
    foreach ($decoded['STATUS'] as $st) {
        if (!is_array($st) || !isset($st['STATUS']) || $st['STATUS'] !== 'S') return false;
    }
    return true;
}

function dev_activity_status_message($decoded) {
    $msg = 'trace request failed';
    if (is_array($decoded) && isset($decoded['STATUS'][0]) && is_array($decoded['STATUS'][0]) &&
        isset($decoded['STATUS'][0]['Msg']) && is_string($decoded['STATUS'][0]['Msg'])) {
        $raw = preg_replace('/[^\x20-\x7E]/', '', $decoded['STATUS'][0]['Msg']);
        $raw = trim($raw);
        if ($raw !== '') {
            if (strlen($raw) > 180) $raw = substr($raw, 0, 180);
            $msg = $raw;
        }
    }
    return $msg;
}
