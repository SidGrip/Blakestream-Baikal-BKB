<?php

require_once(__DIR__ . '/../overlay/var/www/inc/dev-activity.php');

function fail_test($message) {
    fwrite(STDERR, "FAIL: " . $message . "\n");
    exit(1);
}

function expect($cond, $msg) {
    if (!$cond) fail_test($msg);
}

function grid_csv($base) {
    $cells = array();
    for ($i = 0; $i < 64; $i++) $cells[] = (string) ($base + $i);
    return implode(',', $cells);
}

function matrix_grid($base) {
    $grid = array();
    for ($u = 0; $u < 4; $u++) {
        $grid[$u] = array();
        for ($c = 0; $c < 16; $c++) $grid[$u][$c] = $base + ($u * 16) + $c;
    }
    return $grid;
}

function wire_row($board) {
    return array(
        'ID' => 'BKLU' . $board,
        'DiagBoard' => $board,
        'Chip Count' => 64,
        'StartupSuccessCount' => 56 + $board,
        'AsicVersion' => 1,
        'DiagInfoValid' => 1,
        'DiagLastInfoOk' => 1,
        'DiagLastResultOk' => 0,
        'LocalNoncesRaw' => grid_csv($board),
        'LocalErrorsRaw' => implode(',', array_fill(0, 64, '0')),
        'DiagInvalidId' => 7,
        'DiagInvalidWorkIdx' => 8,
        'DiagReplyMismatch' => 4,
        'DiagUsbTxErrors' => 9,
        'DiagUsbRxErrors' => 10,
        'DiagUptimeUs' => 11,
        'DiagPid' => 123,
        'DiagStartUnix' => 1790640000,
        'DiagSnapMonoUs' => 60000000 + $board,
        'DiagStatEpoch' => 2,
        'DiagRouteEpoch' => 3,
        'DiagEnableEpoch' => 4,
        'RequestedClock' => 400,
        'CommandVerified' => false,
        'AttemptedClockCode' => 30,
        'SentClockIndex' => 30,
        'BridgeStatusEcho' => 400,
        'BridgeStatusEchoCode' => 30,
        'DiagTraceEnabled' => 1,
        'DiagTraceLive' => 100 + $board,
        'DiagTraceUsed' => 68000 + (680 * $board),
        'DiagTraceCapacity' => 2097120,
        'DiagTraceOverwritten' => 0,
        'DiagTraceDropped' => 0,
        'DiagTraceRecorded' => 100 + $board,
        'Password' => 'SENTINEL-PASSWORD-bkb',
        'User' => 'SENTINEL-USER-bkb',
        'Worker' => 'SENTINEL-WORKER-bkb',
    );
}

expect(dev_activity_nonneg(0) === '0', 'explicit int 0 must stay 0');
expect(dev_activity_nonneg('0') === '0', 'explicit string 0 must stay 0');
expect(dev_activity_nonneg(null) === null, 'null counter stays null');
expect(dev_activity_nonneg(1.5) === null, 'float rejected');
expect(dev_activity_nonneg(-1) === null, 'negative rejected');
expect(dev_activity_nonneg('01') === null, 'leading zero rejected');
expect(dev_activity_nonneg(true) === null, 'bool rejected by nonneg');

$decoded = dev_activity_decode_json('{"big":18446744073709551615,"mid":2147483648,"frac":1.5}', 8);
expect(is_array($decoded), 'bigint json decoded');
expect(is_string($decoded['big']) && $decoded['big'] === '18446744073709551615', 'uint64 max stays a decimal string');
expect(!is_float($decoded['big']) && !is_float($decoded['mid']), 'bigint decode must not produce floats');
expect(dev_activity_nonneg($decoded['big']) === '18446744073709551615', 'uint64 max accepted');
expect(dev_activity_nonneg($decoded['mid']) === '2147483648', 'above int32 accepted');
expect(dev_activity_nonneg($decoded['frac']) === null, 'decoded fraction rejected');

$deep = array('v' => 1);
$node = &$deep;
for ($i = 0; $i < 20; $i++) {
    $node['n'] = array();
    $node = &$node['n'];
}
expect(dev_activity_decode_json(json_encode($deep), 16) === null, 'over-deep json rejected');
unset($node);

$wire_raw = json_encode(array('STATS' => array(wire_row(0), wire_row(1), wire_row(2))));
$wire = dev_activity_parse_stats(dev_activity_decode_json($wire_raw, 512));
expect($wire['boards'][2]['nonce'][3][15] === '65', 'LocalNoncesRaw board 2 last cell');
expect($wire['boards'][2]['error'][0][0] === '0', 'explicit grid zero stays 0');
expect($wire['boards'][2]['usb']['tx_error'] === '9', 'DiagUsbTxErrors');
expect($wire['boards'][2]['usb']['rx_error'] === '10', 'DiagUsbRxErrors');
expect($wire['boards'][2]['discard']['reply_mismatch'] === '4', 'DiagReplyMismatch');
expect($wire['boards'][2]['timing']['uptime_us'] === '11', 'DiagUptimeUs');
expect($wire['boards'][2]['last_info_ok'] === 1, 'DiagLastInfoOk 1');
expect($wire['boards'][0]['last_result_ok'] === 0, 'DiagLastResultOk 0 stays 0');
expect($wire['trace']['enabled'] === true, 'DiagTraceEnabled 1 is on');
expect($wire['trace']['recorded'] === '102', 'latest trace row wins when counts increase');
expect($wire['trace']['live'] === '102', 'latest live count kept');
expect($wire['boards'][2]['asic_count'] === '64', 'chip count');
expect($wire['boards'][2]['asic_count_r'] === '58', 'startup success');
expect($wire['boards'][2]['attempted_clock_index'] === '30', 'attempted clock_index');
expect($wire['boards'][2]['last_ack'] === '30', 'last ack from SentClockIndex');
expect($wire['boards'][2]['command_verified'] === false, 'CommandVerified false');
$wire_json = json_encode($wire);
expect(strpos($wire_json, 'SENTINEL-PASSWORD-bkb') === false, 'password absent');
expect(strpos($wire_json, 'SENTINEL-USER-bkb') === false, 'user absent');
expect(strpos($wire_json, 'SENTINEL-WORKER-bkb') === false, 'worker absent');
expect(!array_key_exists('ready', $wire['trace']), 'trace has no ready field');

$empty_grid = wire_row(0);
$empty_grid['LocalNoncesRaw'] = '';
$empty_parsed = dev_activity_parse_board_fields($empty_grid);
expect($empty_parsed['nonce'] === null, 'empty LocalNoncesRaw is unavailable');

$short = wire_row(0);
$short['LocalNoncesRaw'] = '1,2,3';
expect(dev_activity_parse_board_fields($short)['nonce'] === null, 'short grid is null');

$bad_cell = implode(',', array_fill(0, 63, '0')) . ',x';
$bad = wire_row(0);
$bad['LocalNoncesRaw'] = $bad_cell;
expect(dev_activity_parse_board_fields($bad)['nonce'] === null, 'one bad cell nulls the grid');

$matrix = wire_row(0);
$matrix['LocalNoncesRaw'] = matrix_grid(2);
$matrix_parsed = dev_activity_parse_board_fields($matrix);
expect($matrix_parsed['nonce'][3][15] === '65', '4x16 grid parses');

$conflict = wire_row(0);
$conflict['NonceGrid'] = grid_csv(10);
expect(dev_activity_parse_board_fields($conflict)['nonce'] === null, 'LocalNoncesRaw conflict has no fallback');

$alias = wire_row(0);
$alias['AsicCountR'] = 3;
$alias['asic_count_r'] = 4;
expect(dev_activity_parse_board_fields($alias)['asic_count_r'] === null, 'alias conflict is null');

$invalid_info = wire_row(0);
$invalid_info['DiagInfoValid'] = 0;
$invalid_info['Chip Count'] = 64;
$invalid_info['StartupSuccessCount'] = 64;
$invalid_info['AsicVersion'] = 3;
$invalid_info['BridgeStatusEcho'] = 400;
$invalid_info['BridgeStatusEchoCode'] = 20;
$invalid_parsed = dev_activity_parse_board_fields($invalid_info);
expect($invalid_parsed['asic_count'] === null, 'info_valid 0 nulls chip count');
expect($invalid_parsed['asic_count_r'] === null, 'info_valid 0 nulls startup success');
expect($invalid_parsed['asic_ver'] === null, 'info_valid 0 nulls asic version');
expect($invalid_parsed['bridge_status_echo'] === null, 'info_valid 0 nulls echo');
expect($invalid_parsed['bridge_status_echo_code'] === null, 'info_valid 0 nulls echo code');
expect($invalid_parsed['requested_clock'] === '400', 'requested clock is not gated by info_valid');

$zero_info = array(
    'DiagInfoValid' => 1,
    'StartupSuccessCount' => 0,
    'BridgeStatusEchoCode' => 0,
    'Chip Count' => 0,
);
$zero_parsed = dev_activity_parse_board_fields($zero_info);
expect($zero_parsed['asic_count_r'] === '0', 'info_valid 1 keeps startup success 0');
expect($zero_parsed['bridge_status_echo_code'] === '0', 'info_valid 1 keeps echo code 0');
expect($zero_parsed['asic_count'] === '0', 'info_valid 1 keeps chip count 0');

$clocks = array(
    'AttemptedClockCode' => 31,
    'SentClockIndex' => 30,
    'BridgeStatusEchoCode' => 22,
    'DiagInfoValid' => 1,
);
$clock_parsed = dev_activity_parse_board_fields($clocks);
expect($clock_parsed['attempted_clock_index'] === '31', 'attempted clock_index is not the ack');
expect($clock_parsed['last_ack'] === '30', 'last ack is SentClockIndex');
expect($clock_parsed['bridge_status_echo_code'] === '22', 'echo is not last ack');

$unset_ack = array('SentClockIndex' => 0, 'BridgeStatusEchoCode' => 22, 'DiagInfoValid' => 1);
$unset_parsed = dev_activity_parse_board_fields($unset_ack);
expect($unset_parsed['last_ack'] === null, 'SentClockIndex 0 is not an ack');
expect($unset_parsed['bridge_status_echo_code'] === '22', 'echo is not copied into last ack');

$acked_blocks = array('AckedClockCode' => 0, 'SentClockIndex' => 30);
expect(dev_activity_parse_board_fields($acked_blocks)['last_ack'] === null, 'present unusable AckedClockCode does not fall back');

$attempt_unset = array('AttemptedClockCode' => -1, 'SentClockIndex' => 30);
expect(dev_activity_parse_board_fields($attempt_unset)['attempted_clock_index'] === null, 'attempted -1 is unavailable');
expect(dev_activity_parse_board_fields($attempt_unset)['last_ack'] === '30', 'ack still comes from SentClockIndex');

expect(dev_activity_parse_board_fields(array('CommandVerified' => false))['command_verified'] === false, 'false stays false');
expect(dev_activity_parse_board_fields(array('CommandVerified' => true))['command_verified'] === true, 'true stays true');
expect(dev_activity_parse_board_fields(array('StartupSuccessCount' => 1))['command_verified'] === null, 'missing CommandVerified stays null');
expect(dev_activity_parse_board_fields(array('CommandVerified' => 0))['command_verified'] === null, '0 is not a bool');
expect(dev_activity_parse_board_fields(array('CommandVerified' => 1))['command_verified'] === null, '1 is not a bool');
expect(dev_activity_parse_board_fields(array('AppliedClockValid' => true, 'StartupSuccessCount' => 1))['command_verified'] === null, 'AppliedClockValid is not CommandVerified');
expect(dev_activity_parse_board_fields(array('Requested Clock' => 250, 'StartupSuccessCount' => 1))['requested_clock'] === null, 'prose clock name is not RequestedClock');

expect(dev_activity_parse_board_fields(array('DiagTraceEnabled' => 0, 'DiagSnapMonoUs' => 1))['last_info_ok'] === null, 'missing last info stays null');
$trace_off = dev_activity_parse_stats(array('STATS' => array(array(
    'ID' => 'BKLU0', 'DiagBoard' => 0, 'DiagTraceEnabled' => 0, 'DiagSnapMonoUs' => 5,
    'DiagTraceLive' => 0, 'DiagTraceRecorded' => 0,
))));
expect($trace_off['trace']['enabled'] === false, 'DiagTraceEnabled 0 is off');
expect($trace_off['trace']['recorded'] === '0', 'trace recorded 0 stays 0');
$trace_absent = dev_activity_parse_stats(array('STATS' => array(array('ID' => 'BKLU0', 'StartupSuccessCount' => 1))));
expect($trace_absent['trace']['enabled'] === null, 'absent trace enabled stays null');

$same_snap = dev_activity_parse_stats(array('STATS' => array(
    array('ID' => 'BKLU0', 'DiagBoard' => 0, 'DiagSnapMonoUs' => 5, 'DiagTraceEnabled' => 1, 'DiagTraceRecorded' => 1, 'DiagTraceLive' => 1),
    array('ID' => 'BKLU1', 'DiagBoard' => 1, 'DiagSnapMonoUs' => 5, 'DiagTraceEnabled' => 1, 'DiagTraceRecorded' => 2, 'DiagTraceLive' => 1),
)));
expect($same_snap['trace']['recorded'] === null, 'same snap disagreement is unavailable');
expect($same_snap['trace']['enabled'] === null, 'same snap disagreement nulls the whole tuple');
expect($same_snap['trace']['live'] === null, 'same snap disagreement does not keep a partial');

$skip_snap = dev_activity_parse_stats(array('STATS' => array(
    array('ID' => 'BKLU0', 'DiagBoard' => 0, 'DiagTraceEnabled' => 1, 'DiagTraceRecorded' => 1),
    array('ID' => 'BKLU1', 'DiagBoard' => 1, 'DiagSnapMonoUs' => 50, 'DiagTraceEnabled' => 1, 'DiagTraceRecorded' => 7, 'DiagTraceLive' => 7),
)));
expect($skip_snap['trace']['recorded'] === '7', 'row without snap is ignored');

$mapped = dev_activity_parse_stats(array('STATS' => array(
    array('ID' => 'BKLU0', 'DiagBoard' => 2, 'StartupSuccessCount' => 9),
)));
expect($mapped['boards'][2]['present'] === true && $mapped['boards'][2]['asic_count_r'] === '9', 'DiagBoard wins over BKLU id');
expect($mapped['boards'][0]['present'] === false, 'BKLU0 is not used when DiagBoard disagrees');
expect(count($mapped['boards']) === 3, 'three boards');

$bad_board = dev_activity_parse_stats(array('STATS' => array(
    array('ID' => 'BKLU0', 'DiagBoard' => 5, 'StartupSuccessCount' => 9),
)));
expect($bad_board['boards'][0]['present'] === false, 'out of range DiagBoard does not fall back');
expect($bad_board['extension'] === true, 'DiagBoard still marks the extension');

$export_bad = dev_activity_parse_stats(array('STATS' => array(array(
    'ID' => 'BKLU0', 'DiagSnapMonoUs' => 9, 'DiagTraceEnabled' => 1, 'DiagTraceRecorded' => 1,
    'DiagTraceExport' => '/etc/passwd',
))));
expect($export_bad['trace']['export'] === null, 'unexpected export path is not surfaced');
$export_ok = dev_activity_parse_stats(array('STATS' => array(array(
    'ID' => 'BKLU0', 'DiagSnapMonoUs' => 9, 'DiagTraceEnabled' => 1, 'DiagTraceRecorded' => 1,
    'DiagTraceExport' => '/tmp/bkb-diag-trace.bin',
))));
expect($export_ok['trace']['export'] === '/tmp/bkb-diag-trace.bin', 'allowlisted export path is kept');

$legacy = dev_activity_build(array('STATS' => array(array(
    'ID' => 'BKLU0', 'Chip Count' => 64, 'RequestedClock' => 400, 'CommandVerified' => true,
))), null, 'csrf', null);
expect($legacy['support'] === 'legacy' && $legacy['ok'] === true && $legacy['error'] === null, 'old clock keys are legacy');
expect(count($legacy['boards']) === 3, 'legacy still emits 3 boards');

$live_only = dev_activity_build(array('STATS' => array(array(
    'ID' => 'BKLU0', 'DiagBoard' => 0, 'LocalNoncesRaw' => grid_csv(0), 'DiagInvalidId' => 9,
))), null, 'csrf', null);
expect($live_only['support'] === 'ok', 'LocalNoncesRaw marks support ok');
expect($live_only['trace_api'] === true, 'trace api is available');

$collector = dev_activity_parse_collector(array(
    'v' => 1,
    'updated' => 50,
    'boards' => array(array(
        'id' => 0,
        'sample' => array(
            'a' => 'POOL-ACCEPTED-SENTINEL',
            'pool' => 'POOL-URL-SENTINEL',
            'wk' => 'WORKER-SENTINEL',
            'ac' => 64,
            'sc' => 0,
            'cv' => 0,
            'diag' => array('invalid_id' => 4),
            'LocalNoncesRaw' => grid_csv(3),
        ),
    )),
));
expect(is_array($collector), 'collector v1 parses');
expect($collector['boards'][0]['discard']['invalid_id'] === '4', 'collector discard parsed');
expect($collector['boards'][0]['nonce'] === null, 'collector grid is not activity data');
expect($collector['boards'][0]['epoch']['pid'] === null, 'collector epoch is not activity data');
expect($collector['boards'][0]['last_ack'] === null, 'collector sent code 0 does not fill last ack');
expect($collector['boards'][0]['command_verified'] === false, 'collector cv 0 is a real false');
expect(dev_activity_parse_collector(array('v' => '1', 'boards' => array())) === null, 'string schema version ignored');

$gap = dev_activity_build(array('STATS' => array(array(
    'ID' => 'BKLU0', 'DiagBoard' => 0, 'StartupSuccessCount' => 1,
))), $collector, 'csrf', null);
expect($gap['boards'][0]['discard']['invalid_id'] === null, 'collector does not backfill results');
expect($gap['boards'][0]['asic_count'] === null, 'collector does not backfill chip count');
expect($gap['boards'][0]['command_verified'] === null, 'collector does not backfill command verified');
expect($gap['boards'][0]['requested_clock'] === null, 'collector does not backfill clock');
expect($gap['sources']['collector'] === true && $gap['sources']['collector_at'] === 50, 'collector is freshness metadata only');
$gap_json = json_encode($gap);
expect(strpos($gap_json, 'POOL-ACCEPTED-SENTINEL') === false, 'pool accepted sentinel absent');
expect(strpos($gap_json, 'POOL-URL-SENTINEL') === false, 'pool url sentinel absent');

$kept = dev_activity_build(array('STATS' => array(array(
    'ID' => 'BKLU0', 'DiagBoard' => 0, 'StartupSuccessCount' => 1, 'DiagInvalidId' => 9,
    'LocalNoncesRaw' => grid_csv(1),
    'CommandVerified' => true,
    'DiagPid' => 5, 'DiagStartUnix' => 6, 'DiagStatEpoch' => 1, 'DiagRouteEpoch' => 1, 'DiagEnableEpoch' => 1,
))), $collector, 'csrf', null);
expect($kept['boards'][0]['discard']['invalid_id'] === '9', 'live discard beats collector');
expect($kept['boards'][0]['nonce'][0][0] === '1', 'collector grid does not override live');
expect($kept['boards'][0]['command_verified'] === true, 'live CommandVerified beats collector bit');
expect($kept['boards'][0]['epoch']['pid'] === '5', 'collector does not supply epoch');

$blocked = dev_activity_build(array('STATS' => array(array(
    'ID' => 'BKLU0', 'DiagBoard' => 0, 'DiagInfoValid' => 0, 'Chip Count' => 0, 'StartupSuccessCount' => 1,
))), $collector, 'csrf', null);
expect($blocked['boards'][0]['asic_count'] === null, 'info_valid 0 stays null without collector backfill');

$down = dev_activity_build(null, $collector, 'csrf', 'sgminer api unreachable');
expect($down['ok'] === false && $down['support'] === 'unreachable', 'unreachable support');
expect($down['sources']['collector'] === false, 'unreachable does not apply collector');
expect($down['boards'][0]['discard']['invalid_id'] === null, 'unreachable does not show stale discard');
expect($down['boards'][0]['present'] === false, 'unreachable boards are absent');

$big_raw = '{"STATS":[{"ID":"BKLU1","DiagBoard":1,"DiagSnapMonoUs":10,"DiagTraceEnabled":1,"DiagTraceRecorded":18446744073709551615,"DiagTraceLive":2147483648,"DiagUsbTxErrors":2147483648}]}';
$big = dev_activity_parse_stats(dev_activity_decode_json($big_raw, 512));
expect($big['trace']['recorded'] === '18446744073709551615', 'live uint64 trace counter stays decimal');
expect($big['trace']['live'] === '2147483648', 'live counter above int32 stays decimal');
expect($big['boards'][1]['usb']['tx_error'] === '2147483648', 'usb counter above int32 stays decimal');

$tok = dev_activity_csrf_mint();
expect(is_string($tok) && preg_match('/^[0-9a-f]{64}$/', $tok) === 1, 'csrf token is 64 hex chars');
expect(dev_activity_csrf_accept($tok, $tok) === true, 'matching csrf accepted');
expect(dev_activity_csrf_accept($tok, str_repeat('ab', 32)) === false, 'wrong csrf rejected');
$get = dev_activity_parse_mutation('GET', '', 0, $tok);
expect($get['ok'] === false && $get['status'] === 405, 'GET mutation rejected');
$extra = dev_activity_parse_mutation('POST', json_encode(array('csrf' => $tok, 'op' => 'enable', 'path' => '/tmp/x')), 80, $tok);
expect($extra['ok'] === false && $extra['status'] === 400, 'extra path key rejected');
$bad_op = dev_activity_parse_mutation('POST', json_encode(array('csrf' => $tok, 'op' => 'enable-now')), 80, $tok);
expect($bad_op['ok'] === false && $bad_op['status'] === 400, 'bad op rejected');
$bad_tok = dev_activity_parse_mutation('POST', json_encode(array('csrf' => str_repeat('a', 64), 'op' => 'enable')), 90, $tok);
expect($bad_tok['ok'] === false && $bad_tok['status'] === 403, 'bad token rejected');
$good = dev_activity_parse_mutation('POST', json_encode(array('op' => 'clear', 'csrf' => $tok)), 90, $tok);
expect($good['ok'] === true && $good['op'] === 'clear', 'strict csrf and op accepted');

$spec = dev_activity_trace_spec('enable');
expect(is_array($spec) && $spec['command'] === 'bkbtrace' && $spec['parameter'] === 'on', 'enable maps to constrained trace on');
expect(dev_activity_trace_spec('disable')['parameter'] === 'off', 'disable maps to constrained trace off');
expect(dev_activity_trace_spec('clear')['parameter'] === 'clear', 'clear maps to constrained trace clear');
expect(dev_activity_trace_spec('export')['parameter'] === 'export', 'export maps to constrained trace export');
expect(dev_activity_trace_spec('download') === null, 'download is not an API mutation');
expect(dev_activity_mutation_kind('download') === 'file', 'download is a file op');
expect(dev_activity_read_command() === 'stats', 'read command is stats');
expect(dev_activity_sgminer_allowed('stats', '') === true, 'stats allowed');
foreach (array('on', 'off', 'clear', 'export') as $trace_op) {
    expect(dev_activity_sgminer_allowed('bkbtrace', $trace_op) === true, 'exact trace op allowed: ' . $trace_op);
}
foreach (array('', 'enable', 'on,extra', 'on ', "on\n", '0,diag-trace,on') as $trace_op) {
    expect(dev_activity_sgminer_allowed('bkbtrace', $trace_op) === false, 'non-canonical trace op rejected');
}
expect(dev_activity_sgminer_allowed('ascset', '0,diag-trace,on') === false, 'generic trace ascset rejected');
expect(dev_activity_sgminer_allowed('ascset', '0,fan,80') === false, 'fan rejected');
expect(dev_activity_sgminer_allowed('ascset', '0,reapply-clock,400') === false, 'reapply-clock rejected');
expect(dev_activity_sgminer_allowed('ascidentify', '0') === false, 'ascidentify rejected');

expect(dev_activity_export_paths() === array('/run/blakestream/bkb-diag-trace.bin', '/tmp/bkb-diag-trace.bin'), 'only two export paths');
expect(dev_activity_export_path_for_run_dir(true) === '/run/blakestream/bkb-diag-trace.bin', 'real run dir selects /run');
expect(dev_activity_export_path_for_run_dir(false) === '/tmp/bkb-diag-trace.bin', 'missing run dir selects /tmp');
expect(dev_activity_collector_candidates_for(true) === array('/run/blakestream/bkb-diagnostics-latest.json'), 'collector path');
expect(dev_activity_collector_candidates_for(false) === array(), 'no collector outside a real run dir');
expect(dev_activity_path_allowed('/tmp/../tmp/bkb-diag-trace.bin', array('/tmp/../tmp/bkb-diag-trace.bin')) === false, 'traversal rejected');
expect(dev_activity_open_export('/tmp/not-the-trace.bin', dev_activity_export_paths(), 100)['error'] === 'rejected', 'non-allowlisted path rejected');

$tmp = sys_get_temp_dir() . '/bkb-ui-export-' . getmypid() . '-' . mt_rand();
expect(mkdir($tmp), 'temp dir');
function dev_activity_test_rm($dir) {
    $list = @scandir($dir);
    if (!is_array($list)) return;
    foreach ($list as $name) {
        if ($name === '.' || $name === '..') continue;
        $path = $dir . '/' . $name;
        if (is_link($path) || is_file($path)) @unlink($path);
        elseif (is_dir($path)) dev_activity_test_rm($path);
    }
    @rmdir($dir);
}
register_shutdown_function(function () use (&$tmp) {
    if (is_string($tmp) && is_dir($tmp)) dev_activity_test_rm($tmp);
});

$real_dir = $tmp . '/run';
$link_dir = $tmp . '/run-link';
expect(mkdir($real_dir), 'real dir');
expect(symlink($real_dir, $link_dir), 'dir symlink');
expect(dev_activity_run_dir_is_real($real_dir) === true, 'real directory accepted');
expect(dev_activity_run_dir_is_real($link_dir) === false, 'directory symlink rejected');

$good_path = $tmp . '/trace.bin';
$payload = "BKBTRACE1\n{\"enabled\":0}\n";
expect(file_put_contents($good_path, $payload) === strlen($payload), 'write trace');
$good_real = realpath($good_path);
$opened = dev_activity_open_export($good_real, array($good_real), dev_activity_export_max_bytes());
expect($opened['ok'] === true && $opened['size'] === strlen($payload), 'good trace opens');
if ($opened['fp']) fclose($opened['fp']);

$link_file = $tmp . '/trace-link.bin';
expect(symlink($good_path, $link_file), 'file symlink');
$linked = dev_activity_open_export($link_file, array($link_file), dev_activity_export_max_bytes());
expect($linked['ok'] === false && $linked['error'] === 'rejected', 'symlink export rejected');

$big_path = $tmp . '/big.bin';
$big_body = "BKBTRACE1\n" . str_repeat('x', 32);
expect(file_put_contents($big_path, $big_body) !== false, 'write oversize');
$big_real = realpath($big_path);
$over = dev_activity_open_export($big_real, array($big_real), 20);
expect($over['ok'] === false && $over['error'] === 'rejected', 'oversize export rejected');

$bad_magic_path = $tmp . '/bad.bin';
expect(file_put_contents($bad_magic_path, "NOTTRACE1\nhello") !== false, 'write bad magic');
$bad_real = realpath($bad_magic_path);
$bad_open = dev_activity_open_export($bad_real, array($bad_real), dev_activity_export_max_bytes());
expect($bad_open['ok'] === false && $bad_open['error'] === 'rejected', 'bad magic rejected');

$col_path = $tmp . '/latest.json';
$col_body = '{"v":1,"updated":60,"boards":[{"id":0,"sample":{"DiagInvalidId":18446744073709551615}}]}';
expect(file_put_contents($col_path, $col_body) !== false, 'write collector');
$col_real = realpath($col_path);
$col_read = dev_activity_read_collector_file($col_real, $col_real, dev_activity_collector_max_bytes());
expect(is_array($col_read) && $col_read['boards'][0]['discard']['invalid_id'] === '18446744073709551615', 'collector file keeps uint64 decimal');
expect(dev_activity_read_collector_file($col_real, $col_real . '-other', 1000) === null, 'collector allowlist mismatch ignored');

$root = dirname(__DIR__);
$fdev = file_get_contents($root . '/overlay/var/www/f_dev.php');
$ctrl = file_get_contents($root . '/overlay/var/www/ng/controllers.js');
$html = file_get_contents($root . '/overlay/var/www/partials/dev-tools.html');
$index = file_get_contents($root . '/overlay/var/www/index.php');
$app = file_get_contents($root . '/overlay/var/www/ng/app.js');
expect(is_string($fdev) && is_string($ctrl) && is_string($html) && is_string($index) && is_string($app), 'source files readable');

$fw = strpos($fdev, '$settingsFirmwareActions = array(');
$fw_end = strpos($fdev, ');', $fw);
$fw_block = substr($fdev, $fw, $fw_end - $fw);
expect(strpos($fw_block, 'activity') === false, 'activity is not a firmware action');

$act = strpos($fdev, "case 'activity':");
$act_end = strpos($fdev, "case 'activity_trace':");
$act_slice = substr($fdev, $act, $act_end - $act);
$act_code = preg_replace('/\/\*.*?\*\//s', '', $act_slice);
$act_code = preg_replace('/\/\/.*$/m', '', $act_code);
expect(strpos($act_slice, 'dev_activity_read_command') !== false, 'activity uses the read command');
expect(strpos($act_slice, 'dev_activity_decode_json') !== false, 'activity decodes with bigint strings');
expect(strpos($act_code, 'bkbtrace') === false && strpos($act_code, 'ascset') === false && strpos($act_code, 'ascidentify') === false, 'activity case does not call a mutation verb');
expect(strpos($act_code, 'json_decode') === false, 'activity case does not raw-decode json');

$tr = strpos($fdev, "case 'activity_trace':");
$tr_end = strpos($fdev, 'default:', $tr);
$tr_slice = substr($fdev, $tr, $tr_end - $tr);
$open_at = strpos($tr_slice, 'dev_activity_open_selected_export');
$api_at = strpos($tr_slice, 'sgminer_api_exchange');
expect($open_at !== false && $api_at !== false && $open_at < $api_at, 'download opens the fixed file before any mutation API call');
expect(strpos($tr_slice, 'dev_activity_decode_json') !== false, 'trace response uses bigint decode');
expect(strpos($tr_slice, 'dev_activity_read_stream') !== false && strpos($tr_slice, '4097') !== false, 'trace body read is capped');
expect(strpos($tr_slice, 'file_get_contents') === false, 'trace body is not an unbounded read');
expect(strpos($act_slice, 'dev_activity_read_stream') === false, 'stats GET does not read a request body');

$stream = fopen('php://temp', 'rb+');
expect(is_resource($stream), 'temp stream');
fwrite($stream, str_repeat('Z', 5000));
rewind($stream);
$capped = dev_activity_read_stream($stream, 4097);
expect(strlen($capped) === 4097, 'read stops at 4097 bytes');
$rest = stream_get_contents($stream);
expect(strlen($rest) === 5000 - 4097, 'bytes past the cap stay unread');
fclose($stream);
$short = fopen('php://temp', 'rb+');
fwrite($short, 'abc');
rewind($short);
expect(dev_activity_read_stream($short, 4097) === 'abc', 'short body is unchanged');
fclose($short);
$too_big = dev_activity_parse_mutation('POST', str_repeat('x', 4097), 0, $tok);
expect($too_big['ok'] === false && $too_big['status'] === 413, '4097 byte body is rejected');

$csrf_at = strpos($fdev, '$_SESSION[\'dev_csrf\']');
$close_at = strpos($fdev, 'session_write_close');
expect($csrf_at !== false && $close_at !== false && $csrf_at < $close_at, 'csrf is minted before the session lock drops');

expect(strpos($ctrl, 'activity: true') === false, 'activity is not a separate dev tab');
expect(strpos($ctrl, 'createPoll') !== false, 'activity poll uses the cancellable poller');
expect(strpos($ctrl, '$timeout.cancel') !== false, 'activity timer is cancelled');
expect(strpos($ctrl, "if (tab === 'boards') devActivityStart();") !== false, 'boards tab starts activity polling');
expect(strpos($ctrl, "else devActivityStop();") !== false, 'leaving boards stops activity polling');
expect(strpos($ctrl, "return \$scope.bsViewTab('dev');") !== false, 'Dev Tools uses login-scoped tab memory');
expect(strpos($ctrl, 'devActivityPoller.leave') !== false, 'leave stops the poller');
expect(strpos($ctrl, 'devActivityPoller.destroy') !== false, 'destroy tears the poller down');
expect(strpos($ctrl, 'devBoardsGen') === false, 'board health does not keep a second poller');
expect(strpos($ctrl, 'pendingTrace') === false, 'trace commands are not queued behind GET');
expect(strpos($ctrl, "get('f_dev.php?action=activity')") !== false, 'poll GET stays on the read endpoint');
expect(strpos($ctrl, "get('f_dev.php?action=board_health')") !== false, 'health read stays on board_health');
expect(strpos($html, 'ng-switch-when="activity"') === false, 'activity is not a separate panel');
expect(strpos($html, 'ng-switch-when="boards"') !== false, 'unified boards panel exists');
expect(strpos($html, 'devMinerRestart()') === false, 'restart is not exposed through the raw miner API');
expect(strpos($html, 'devBoardSet(') === false, 'board mutations are not exposed through the raw miner API');
expect(strpos($html, 'Locally valid nonces / local errors · not pool-accepted') !== false, 'local contribution legend');
expect(strpos($html, 'bs-activity-k">Results') !== false, 'results group is not labeled discard');
expect(strpos($html, 'Unattributed IDs') !== false, 'unattributed id label');
expect(strpos($html, 'bs-activity-k">Discard') === false, 'activity group is not Discard');
expect(strpos($html, 'Work wait max (lifetime)') !== false, 'lifetime max is labeled');
$css = file_get_contents($root . '/overlay/var/www/css/blakestream-dark.css');
expect(strpos($css, 'width: 7.5rem;') !== false && strpos($css, 'width: 127.5rem;') !== false, 'grid cells fit a uint32');
expect(strpos($css, '4.75rem') === false, 'narrow activity tracks are gone');
$helper = file_get_contents($root . '/overlay/var/www/inc/dev-activity.php');
expect(strpos($helper, 'dev_activity_fill_board') === false, 'collector backfill helper is gone');
$js_at = strpos($index, 'ng/dev-activity.js');
$ctrl_at = strpos($index, 'ng/controllers.js');
expect($js_at !== false && $ctrl_at !== false && $js_at < $ctrl_at, 'activity helper loads before the controller');
expect(strpos($app, '20260930-ui-backup') !== false, 'partial cache tag bumped');
expect(strpos($index, 'ng/controllers.js?v=pool-catalog-status-1') !== false, 'controller cache tag bumped');
expect(strpos($index, 'blakestream-dark.css?v=collector-wait-1') !== false, 'css cache tag bumped');

echo "ok\n";
