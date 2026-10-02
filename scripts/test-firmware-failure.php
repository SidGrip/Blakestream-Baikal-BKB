<?php
$source = file_get_contents(__DIR__ . '/../overlay/var/www/f_dev.php');
$start = strpos($source, 'function firmware_job_apply_result(');
$end = strpos($source, 'function firmware_job_state(', $start);
if ($start === false || $end === false) throw new Exception('result handler missing');
eval(substr($source, $start, $end - $start));
$start = strpos($source, 'function firmware_wrapper_response(');
$end = strpos($source, 'function firmware_job_start(', $start);
if ($start === false || $end === false) throw new Exception('wrapper response helper missing');
eval(substr($source, $start, $end - $start));
$applied = tempnam(sys_get_temp_dir(), 'bkb-firmware-failure-');
unlink($applied);
$result = array();
$calls = array();
$clear_ok = true;
function firmware_job_paths() { global $applied; return array('applied' => $applied); }
function firmware_job_result() { global $result; return $result; }
function scripta_conf_set_bool($path, $key, $value) {
    global $calls;
    $calls[] = array('setting', $key, $value);
    return true;
}
function bkb_clear_experimental_options($path) {
    global $calls, $clear_ok;
    $calls[] = array('clear');
    return $clear_ok;
}
function firmware_audit($text) { global $calls; $calls[] = array('audit'); }
function firmware_record_flash_runtime($action, $seconds) { throw new Exception('failure must not train duration'); }
function firmware_write_identity($identity) { throw new Exception('failure must not invent a successful identity'); }
function expect_failure($condition, $message) { if (!$condition) throw new Exception($message); }
try {
    foreach (array('', "sudo: unable to resolve host fixture: Connection refused\n") as $warning) {
        foreach (array(array('ok' => true, 'message' => 'abort requested'),
                       array('ok' => false, 'error' => 'firmware writes cannot be aborted')) as $reply) {
            expect_failure(firmware_wrapper_response($warning . json_encode($reply) . "\n", 'fallback') === $reply,
                           'wrapper result preserved despite sudo warnings');
        }
    }
    foreach (array('', 'null', 'true', '"invalid"', 'sudo: a password is required', '{broken') as $raw) {
        expect_failure(firmware_wrapper_response($raw, 'abort request failed') ===
                       array('ok' => false, 'error' => 'abort request failed', 'raw' => $raw),
                       'missing wrapper JSON must remain a failure');
    }
    foreach (array('revert', 'flash-custom') as $op) {
        $calls = array();
        $result = array('ok' => false, 'op' => $op, 'elapsed' => 180, 'message' => 'miner restart failed');
        expect_failure(firmware_job_apply_result('fixture') === $result, 'keep failure visible');
        expect_failure(in_array(array('setting', 'userAdjustableClocks', false), $calls, true), 'clocks disabled');
        expect_failure(in_array(array('clear'), $calls, true), 'raw Index options cleared');
        expect_failure(file_exists($applied), 'successful cleanup marked applied');
        $before = $calls;
        firmware_job_apply_result('fixture');
        expect_failure($calls === $before, 'cleanup idempotent');
        unlink($applied);
    }
    $clear_ok = false;
    firmware_job_apply_result('fixture');
    expect_failure(!file_exists($applied), 'incomplete cleanup remains retryable');
    $calls = array();
    $result = array('ok' => false, 'op' => 'verify');
    firmware_job_apply_result('fixture');
    expect_failure(!$calls, 'read-only failure does not change clocks');
    echo "PASS firmware response warnings, failed-flash clock reconciliation, no timing learning and retryable cleanup\n";
} finally {
    if (file_exists($applied)) unlink($applied);
}
