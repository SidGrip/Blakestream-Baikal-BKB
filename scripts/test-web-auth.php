<?php
require_once __DIR__ . '/../overlay/var/www/inc/web-auth.php';
date_default_timezone_set('UTC');

function auth_expect($condition, $message) {
    if (!$condition) { fwrite(STDERR, 'FAIL: ' . $message . "\n"); exit(1); }
}

function auth_remove_tree($path) {
    if (is_dir($path) && !is_link($path)) {
        foreach (scandir($path) as $name) {
            if ($name !== '.' && $name !== '..') auth_remove_tree($path . '/' . $name);
        }
        rmdir($path);
    } else {
        unlink($path);
    }
}

$tmp = sys_get_temp_dir() . '/bkb-web-auth-' . getmypid() . '-' . mt_rand(1000, 9999);
auth_expect(mkdir($tmp, 0700), 'temporary fixture directory');
register_shutdown_function(function () use ($tmp) { if (is_dir($tmp)) auth_remove_tree($tmp); });
$helper = realpath(__DIR__ . '/../overlay/var/www/inc/web-auth.php');
$credential = $tmp . '/credential';
$log = $tmp . '/security.log';
$custom = 'Synthetic-custom:7!';
$hash = bs_web_auth_hash($custom);
auth_expect(is_string($hash) && strpos($hash, '$2y$10$') === 0, 'bcrypt cost 10');
$record = 'scripta:' . $hash;
auth_expect(bs_web_auth_verify($custom, $record), 'custom bcrypt success');
auth_expect(!bs_web_auth_verify('wrong', $record), 'custom bcrypt failure');
auth_expect(bs_web_auth_verify($custom, $record . "\n"), 'credential newline accepted');
auth_expect(bs_web_auth_verify($custom, $record . "\r\n"), 'credential CRLF accepted');
auth_expect(!bs_web_auth_verify($custom, $record . "\nextra"), 'extra credential records rejected');
auth_expect(!bs_web_auth_verify($custom, 'scripta:' . md5($custom)), 'MD5 rejected');
auth_expect(!bs_web_auth_verify($custom, $hash), 'credential envelope required');
auth_expect(!bs_web_auth_verify($custom, false), 'failed credential read rejected');
auth_expect(!bs_web_auth_verify($custom, 'scripta:$2y$10$broken'), 'malformed bcrypt rejected');

$invalid = array('', null, false, true, 123, array('CANARY-array'), new stdClass(), str_repeat('x', 73), "CANARY\0nul");
foreach ($invalid as $password) {
    auth_expect(!bs_web_auth_password_valid($password), 'invalid password shape rejected');
    auth_expect(bs_web_auth_hash($password) === false, 'invalid input never hashed');
    auth_expect(!bs_web_auth_verify($password, $record), 'invalid input never verified');
}
$maxPassword = str_repeat('A', 72);
$maxRecord = 'scripta:' . bs_web_auth_hash($maxPassword);
auth_expect(bs_web_auth_verify($maxPassword, $maxRecord), '72 bytes accepted');
auth_expect(!bs_web_auth_verify($maxPassword . 'B', $maxRecord), 'bcrypt prefix-equivalent long input rejected');
auth_expect(!bs_web_auth_password_valid(str_repeat("\xc3\xa9", 37)), 'length measured in bytes');
auth_expect(bs_web_auth_password_valid('0'), 'nonempty zero password accepted');

$default = __DIR__ . '/../overlay/opt/scripta/etc/uipasswd';
auth_expect(is_file($default), 'packaged default fixture required');
$defaultRecord = file_get_contents($default);
auth_expect(strpos($defaultRecord, 'scripta:$2y$10$') === 0, 'packaged default is bcrypt cost 10');
auth_expect(bs_web_auth_verify('baikal', $defaultRecord), 'packaged default verifies');
auth_expect(!bs_web_auth_verify('not-baikal', $defaultRecord), 'packaged default rejects wrong password');

auth_expect(bs_web_auth_save($credential, $custom), 'custom password saved');
$saved = file_get_contents($credential);
auth_expect(bs_web_auth_check($credential, $custom), 'saved password verifies');
auth_expect(file_get_contents($credential) === $saved, 'verification never rewrites credential');
auth_expect((fileperms($credential) & 0777) === 0600, 'credential file mode 0600');
foreach ($invalid as $password) {
    auth_expect(!bs_web_auth_save($credential, $password), 'invalid password not saved');
    auth_expect(file_get_contents($credential) === $saved, 'rejected save preserves credential');
}
auth_expect(!bs_web_auth_check($tmp . '/missing', $custom), 'missing file fails closed');
auth_expect(!bs_web_auth_save($tmp . '/missing/credential', $custom), 'missing parent save rejected');
auth_expect(!bs_web_auth_save($tmp, $custom), 'directory save rejected');
auth_expect(symlink($credential, $tmp . '/credential-link'), 'credential symlink fixture');
auth_expect(!bs_web_auth_save($tmp . '/credential-link', $custom), 'symlink save rejected');
auth_expect(!bs_web_auth_check($tmp . '/credential-link', $custom), 'symlink read rejected');
auth_expect(count(glob($tmp . '/.web-auth-*')) === 0, 'no credential temporary files remain');

$stamp = '2026-09-30 12:00:00';
$normal = $stamp . ' web login-OK ip=198.51.100.4 xff=192.0.2.1 ua="Browser/1.0"' . "\n"
    . $stamp . ' devtools firmware-verify start ip=198.51.100.4' . "\n"
    . 'ssh 2026-09-30T12:00:00+0000 miner sshd[42]: Accepted password for user from 198.51.100.4' . "\r\n"
    . "... trimmed ...\n";
auth_expect(bs_web_auth_redact_log($normal) === $normal, 'normal mixed audit events preserved exactly');
$legacy = $stamp . ' web login-FAIL ip=198.51.100.4 xff=192.0.2.1 pw="CANARY-decoy" pw="CANARY-legacy\\" ua="Browser/1.0"' . "\n"
    . $stamp . ' web login-FAIL ip=198.51.100.4 pw="CANARY-unclosed' . "\n"
    . 'CANARY-orphan" ua="Browser/1.0"' . "\n"
    . $stamp . ' web login-FAIL ip=198.51.100.4 pw="CANARY-' . "\x00\xff" . '" ua="Browser/1.0"';
$redacted = bs_web_auth_redact_log($legacy);
auth_expect(strpos($redacted, 'CANARY') === false, 'legacy values and orphan fragments removed');
auth_expect(substr_count($redacted, 'web login-FAIL') === 3, 'legacy failure events retained');
auth_expect(strpos($redacted, 'ua="Browser/1.0"') !== false, 'legacy browser retained');
auth_expect(strpos($redacted, 'xff=192.0.2.1') !== false, 'legacy forwarded IP retained');
auth_expect(bs_web_auth_redact_log($redacted) === $redacted, 'redaction idempotent');
auth_expect(bs_web_auth_redact_log(array()) === false, 'redactor rejects malformed input');
foreach (array('pw="prefix ', "pw='prefix ", 'pw="one pw="two ') as $nested) {
    $crafted = $stamp . ' web login-FAIL ip=198.51.100.4 xff=' . $nested
        . 'pw="CANARY-nested-secret" ua="Browser/1.0"';
    $clean = bs_web_auth_redact_log($crafted);
    auth_expect(strpos($clean, 'CANARY') === false, 'nested legacy fields fail closed');
    auth_expect(strpos($clean, 'web login-FAIL ip=198.51.100.4') !== false, 'ambiguous legacy event retained');
    auth_expect(bs_web_auth_redact_log($clean) === $clean, 'nested redaction idempotent');
    file_put_contents($tmp . '/nested.log', $crafted);
    auth_expect(bs_web_auth_scrub_log($tmp . '/nested.log'), 'nested durable scrub succeeds');
    auth_expect(strpos(file_get_contents($tmp . '/nested.log'), 'CANARY') === false, 'nested durable scrub removes password');
}

$server = array('REMOTE_ADDR' => '198.51.100.4', 'HTTP_X_FORWARDED_FOR' => '192.0.2.1, 2001:db8::1', 'HTTP_USER_AGENT' => 'Browser/1.0');
$line = bs_web_auth_audit_line(false, $server, $custom);
auth_expect(strpos($line, 'web login-FAIL ip=198.51.100.4 xff=192.0.2.1,2001:db8::1 ua="Browser/1.0"') !== false, 'normal failure metadata');
auth_expect(strpos(bs_web_auth_audit_line(true, $server, $custom), 'web login-OK') !== false, 'normal success event');
auth_expect(strpos($line, $custom) === false && strpos($line, 'pw=') === false, 'no password field in new events');
$injected = $server;
$injected['HTTP_X_FORWARDED_FOR'] = '192.0.2.1 pw="CANARY-xff"' . "\nforged";
$injected['HTTP_USER_AGENT'] = 'Browser " pw="CANARY-ua" ' . $custom . "\r\n\t\0\x1b\\";
$line = bs_web_auth_audit_line(false, $injected, $custom);
auth_expect(strpos($line, 'CANARY') === false && strpos($line, $custom) === false, 'header password smuggling removed');
auth_expect(strpos($line, 'xff=') === false, 'invalid forwarded header omitted');
auth_expect(substr_count($line, "\n") === 1 && !preg_match('/[\x00-\x1f\x7f]/', rtrim($line, "\n")), 'metadata controls removed');
auth_expect(strpos($line, '\\"') !== false && strpos($line, '\\\\') !== false, 'metadata quotes and backslashes escaped');
foreach ($invalid as $password) {
    $injected['HTTP_USER_AGENT'] = 'CANARY-array CANARY-nul ' . str_repeat('x', 73);
    $line = bs_web_auth_audit_line(false, $injected, $password);
    auth_expect(strpos($line, 'CANARY') === false && strpos($line, str_repeat('x', 73)) === false, 'malformed input suppresses untrusted headers');
}
$arrayServer = array('REMOTE_ADDR' => array('CANARY'), 'HTTP_USER_AGENT' => array('CANARY'), 'HTTP_X_FORWARDED_FOR' => array('CANARY'));
auth_expect(strpos(bs_web_auth_audit_line(false, $arrayServer, $custom), 'CANARY') === false, 'non-string metadata rejected');
$server['HTTP_USER_AGENT'] = str_repeat('U', 5000);
auth_expect(strpos(bs_web_auth_audit_line(false, $server, $custom), 'ua="-"') !== false, 'oversized UA rejected');
$server['HTTP_USER_AGENT'] = 'Browser/1.0';

file_put_contents($log, $normal . $legacy);
auth_expect(bs_web_auth_log($log, false, $server, $custom), 'scrub and append succeed');
$audit = file_get_contents($log);
auth_expect(strpos($audit, 'CANARY') === false && strpos($audit, $custom) === false, 'durable audit has no supplied passwords');
auth_expect(strpos($audit, $normal) === 0, 'existing normal records preserved');
auth_expect(substr_count($audit, 'web login-FAIL') === 4, 'failure event appended once');
auth_expect(bs_web_auth_scrub_log($log), 'explicit scrub succeeds');
auth_expect(file_get_contents($log) === $audit, 'clean scrub leaves bytes unchanged');
$snapshot = null;
auth_expect(bs_security_log_update($log, null, function ($text) use (&$snapshot) { $snapshot = $text; return $text; }), 'locked snapshot succeeds');
auth_expect($snapshot === $audit, 'snapshot is sanitized full log');
auth_expect(!bs_security_log_update($log, null, function ($text) { return false; }), 'transform can abort');
auth_expect(!bs_security_log_update($log, null, function ($text) { throw new Exception('fixture'); }), 'transform exception reported');
auth_expect(file_get_contents($log) === $audit, 'aborted transform preserves log');
auth_expect(bs_security_log_update($log, null, function ($text) { return ''; }), 'transform can clear log');
auth_expect(file_get_contents($log) === '', 'transform result persisted');
file_put_contents($log, $audit);
auth_expect(bs_web_auth_scrub_log($tmp . '/absent.log') && !file_exists($tmp . '/absent.log'), 'absent scrub does not create log');
file_put_contents($tmp . '/empty.log', '');
auth_expect(bs_web_auth_scrub_log($tmp . '/empty.log'), 'empty scrub succeeds');
auth_expect(!bs_web_auth_log($tmp . '/missing/log', false, $server, $custom), 'log open failure reported');
auth_expect(!bs_web_auth_scrub_log($tmp), 'directory log rejected');
symlink($log, $tmp . '/log-link');
auth_expect(!bs_web_auth_scrub_log($tmp . '/log-link'), 'symlink log rejected');
mkdir($tmp . '/bad-lock.log.lock');
auth_expect(!bs_web_auth_log($tmp . '/bad-lock.log', false, $server, $custom), 'lock failure reported');
auth_expect(!file_exists($tmp . '/bad-lock.log'), 'lock failure does not create log');
$readOnly = fopen($log, 'rb');
auth_expect(!bs_web_auth_audit_locked($readOnly, bs_web_auth_audit_line(false, $server, $custom)), 'failed append reported');
fclose($readOnly);
auth_expect(file_get_contents($log) === $audit, 'failed append preserves existing bytes');
$writeOnly = fopen($tmp . '/write-only', 'wb');
auth_expect(!bs_web_auth_audit_locked($writeOnly, ''), 'failed read reported');
fclose($writeOnly);
file_put_contents($tmp . '/large.log', str_repeat('X', 4194305));
auth_expect(!bs_web_auth_scrub_log($tmp . '/large.log'), 'oversized migration fails explicitly');
file_put_contents($tmp . '/trim.log', str_repeat($normal . $legacy . "\n", 500));
auth_expect(bs_web_auth_log($tmp . '/trim.log', true, $server, $custom), 'large log scrub and trim');
$trimmed = file_get_contents($tmp . '/trim.log');
auth_expect(strpos($trimmed, 'CANARY') === false && strlen($trimmed) < 262144, 'trim bounded and password-free');
auth_expect(substr($trimmed, 0, 16) === "... trimmed ...\n", 'trim starts on record boundary');

function auth_start_php($body) {
    $proc = proc_open(escapeshellarg(PHP_BINARY) . ' -d date.timezone=UTC -d display_errors=stderr', array(0 => array('pipe', 'r'), 1 => array('pipe', 'w'), 2 => array('pipe', 'w')), $pipes);
    auth_expect(is_resource($proc), 'PHP child started');
    fwrite($pipes[0], "<?php\n" . $body);
    fclose($pipes[0]);
    return array($proc, $pipes);
}

function auth_finish_php($child) {
    list($proc, $pipes) = $child;
    $out = stream_get_contents($pipes[1]);
    $err = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    auth_expect(proc_close($proc) === 0, 'PHP child exit status');
    return array($out, $err);
}

$concurrent = $tmp . '/concurrent.log';
file_put_contents($concurrent, $normal);
$lock = bs_web_auth_audit_lock($concurrent);
auth_expect(is_resource($lock), 'sidecar lock acquired');
$child = auth_start_php('require ' . var_export($helper, true) . '; echo "ready\\n"; flush(); exit(bs_web_auth_log(' . var_export($concurrent, true) . ', false, array(), "fixture") ? 0 : 1);');
auth_expect(fgets($child[1][1]) === "ready\n", 'waiting writer started');
usleep(100000);
$status = proc_get_status($child[0]);
auth_expect($status['running'], 'writer waits for shared sidecar lock');
file_put_contents($tmp . '/replacement.log', $normal . $normal);
rename($tmp . '/replacement.log', $concurrent);
bs_web_auth_audit_unlock($lock);
$result = auth_finish_php($child);
auth_expect($result === array('', ''), 'waiting writer completed cleanly');
auth_expect(substr_count(file_get_contents($concurrent), 'web login-OK') === 2, 'replacement events preserved');
auth_expect(substr_count(file_get_contents($concurrent), 'web login-FAIL') === 1, 'append targets replacement inode');
$children = array();
for ($i = 0; $i < 4; $i++) {
    $children[] = auth_start_php('require ' . var_export($helper, true) . '; for ($i=0; $i<8; $i++) { if (!bs_web_auth_log(' . var_export($concurrent, true) . ', false, array(), "fixture")) exit(1); }');
}
foreach ($children as $child) auth_expect(auth_finish_php($child) === array('', ''), 'concurrent writer completed');
auth_expect(substr_count(file_get_contents($concurrent), 'web login-FAIL') === 33, 'concurrent appends retain every event');

$loginSource = file_get_contents(__DIR__ . '/../overlay/var/www/f_login.php');
$loginSource = str_replace(array("__DIR__ . '/inc/web-auth.php'", "'/opt/scripta/etc/uipasswd'", "'/opt/scripta/var/security.log'"), array(var_export($helper, true), var_export($credential, true), var_export($tmp . '/endpoint.log', true)), $loginSource);
auth_expect(strpos($loginSource, '/opt/scripta/') === false, 'endpoint fixture cannot access system paths');
$loginSource = substr($loginSource, strlen('<?php'));
$endpointPasswords = array($custom, 'CANARY-wrong', array('CANARY-array'), str_repeat('Z', 73), "CANARY\0nul", '', null);
foreach ($endpointPasswords as $password) {
    $code = 'ini_set("session.save_path", ' . var_export($tmp, true) . '); $_SERVER = ' . var_export($server, true) . '; $_POST = ' . var_export(array('userPassword' => $password), true) . '; ' . $loginSource . '; echo "|" . (!empty($_SESSION["_logged_"]) ? "1" : "0");';
    $result = auth_finish_php(auth_start_php($code));
    auth_expect($result === array($password === $custom ? '1|1' : '0|0', ''), 'actual login response and session contract');
    auth_expect(file_get_contents($credential) === $saved, 'actual login never changes credential');
}
auth_expect(strpos(file_get_contents($tmp . '/endpoint.log'), 'CANARY') === false, 'endpoint malformed-input audit does not leak');
file_put_contents($credential, 'scripta:' . md5($custom));
$code = 'ini_set("session.save_path", ' . var_export($tmp, true) . '); $_SERVER = array(); $_POST = ' . var_export(array('userPassword' => $custom), true) . '; ' . $loginSource;
auth_expect(auth_finish_php(auth_start_php($code)) === array('0', ''), 'actual endpoint rejects MD5');
auth_expect(file_get_contents($credential) === 'scripta:' . md5($custom), 'MD5 record never migrated');
file_put_contents($credential, $saved);

function hostHardCtl($op, $payload) {
    $GLOBALS['auth_host_calls'][] = array($op, $payload);
    return array('info' => array(array('type' => 'info', 'text' => 'host fixture')));
}

$settings = file_get_contents(__DIR__ . '/../overlay/var/www/f_settings.php');
$start = strpos($settings, '// Set new password');
$end = strpos($settings, '// Manage settings', $start);
auth_expect($start !== false && $end !== false, 'settings password block located');
$settingsBlock = substr($settings, $start, $end - $start);
function auth_settings_fixture($block, $path, $request, $method = 'POST', $post = null, $get = array(), $cookies = array()) {
    $configUipwd = $path;
    $_SERVER['REQUEST_METHOD'] = $method;
    $_POST = $post === null ? array('pass' => $request) : $post;
    $_GET = $get;
    $_COOKIE = $cookies;
    $_REQUEST = array_merge($_GET, $_POST, $_COOKIE);
    $r = array('data' => array(), 'info' => array());
    $GLOBALS['auth_host_calls'] = array();
    eval($block);
    return $r;
}

$newPassword = 'New-custom:9!';
$payload = json_encode($newPassword);
$result = auth_settings_fixture($settingsBlock, $credential, $payload);
auth_expect(bs_web_auth_check($credential, $newPassword), 'settings writes bcrypt password');
auth_expect($GLOBALS['auth_host_calls'] === array(array(2, $payload)), 'successful save preserves hostHardCtl call');
auth_expect(strpos(json_encode($result), 'Web password saved') !== false, 'successful settings save reported');
$saved = file_get_contents($credential);
foreach (array(
    array('GET', array(), array('pass' => json_encode($custom)), array()),
    array('GET', array('pass' => json_encode($custom)), array(), array()),
    array('POST', array(), array('pass' => json_encode($custom)), array()),
    array('POST', array(), array(), array('pass' => json_encode($custom))),
    array('', array('pass' => json_encode($custom)), array(), array())
) as $input) {
    $result = auth_settings_fixture($settingsBlock, $credential, null, $input[0], $input[1], $input[2], $input[3]);
    auth_expect(count($GLOBALS['auth_host_calls']) === 0, 'non-POST or non-form password never changes Linux password');
    auth_expect(strpos(json_encode($result), '"type":"error"') !== false, 'non-POST or non-form password reports error');
    auth_expect(strpos(json_encode($result), '"type":"success"') === false, 'rejected transport never reports success');
    auth_expect(file_get_contents($credential) === $saved, 'rejected transport preserves web password');
}
$postedPayload = json_encode($newPassword);
$result = auth_settings_fixture($settingsBlock, $credential, $postedPayload, 'POST', null,
    array('pass' => json_encode('query-password')), array('pass' => json_encode('cookie-password')));
auth_expect(bs_web_auth_check($credential, $newPassword), 'POST body wins over query and cookie values');
auth_expect($GLOBALS['auth_host_calls'] === array(array(2, $postedPayload)), 'Linux password receives only POST payload');
$saved = file_get_contents($credential);
foreach (array(array('CANARY-array'), '{broken', 'null', '123', json_encode(''), json_encode(str_repeat('Q', 73)), json_encode("CANARY\0nul")) as $payload) {
    $result = auth_settings_fixture($settingsBlock, $credential, $payload);
    auth_expect(count($GLOBALS['auth_host_calls']) === 0, 'invalid settings input never changes Linux password');
    auth_expect(strpos(json_encode($result), '"type":"error"') !== false, 'invalid settings input reports error');
    auth_expect(strpos(json_encode($result), 'CANARY') === false, 'settings error contains no submitted input');
    auth_expect(file_get_contents($credential) === $saved, 'invalid settings input preserves password');
}
$result = auth_settings_fixture($settingsBlock, $tmp . '/missing/credential', json_encode($custom));
auth_expect(count($GLOBALS['auth_host_calls']) === 0, 'failed web save does not change Linux password');
auth_expect(strpos(json_encode($result), '"type":"success"') === false, 'failed web save never reports success');
auth_expect(count(glob($tmp . '/.web-auth-*')) === 0, 'no secret backup or temporary credential files');

$devPath = __DIR__ . '/../overlay/var/www/f_dev.php';
if (is_file($devPath)) {
    $dev = file_get_contents($devPath);
    auth_expect(strpos($dev, "'/inc/web-auth.php'") !== false, 'Dev Tools loads shared auth helper');
    auth_expect(strpos($dev, 'bs_web_auth_update_audit($path, $line)') !== false, 'firmware audit uses shared writer');
    $start = strpos($dev, 'function security_line_ts(');
    $end = strpos($dev, 'function storage_clearable_paths(', $start);
    auth_expect($start !== false && $end !== false, 'timestamp parser located');
    eval(substr($dev, $start, $end - $start));
    $start = strpos($dev, 'function security_log_prune(');
    $end = strpos($dev, 'function firmware_runtime_path(', $start);
    auth_expect($start !== false && $end !== false, 'security retention helper located');
    eval(substr($dev, $start, $end - $start));
    $recent = date('Y-m-d H:i:s') . ' web login-FAIL ip=198.51.100.4 pw="CANARY-prune" ua="Browser/1.0"' . "\n";
    $old = '2001-01-01 00:00:00 web login-OK ip=198.51.100.4 ua="Browser/1.0"' . "\n";
    file_put_contents($tmp . '/prune.log', $old . $recent);
    $pruned = security_log_prune($tmp . '/prune.log', 7);
    auth_expect(is_array($pruned) && $pruned['kept'] === 1 && $pruned['removed'] === 1, 'retention counts retained');
    $retained = file_get_contents($tmp . '/prune.log');
    auth_expect(strpos($retained, 'CANARY') === false && strpos($retained, 'ua="Browser/1.0"') !== false, 'retention redacts password and preserves browser');
    $lock = bs_web_auth_audit_lock($tmp . '/prune.log');
    auth_expect(is_resource($lock), 'retention releases shared lock');
    bs_web_auth_audit_unlock($lock);
}
$sshPath = __DIR__ . '/../overlay/opt/scripta/bin/ssh-auth-logger.sh';
if (is_file($sshPath)) {
    $ssh = file_get_contents($sshPath);
    auth_expect(strpos($ssh, '$SEC.lock') !== false, 'SSH shares stable lock path');
    auth_expect(strpos($ssh, 'flock -x 9') < strpos($ssh, 'tail -c'), 'SSH trim happens under lock');
    auth_expect(strpos($ssh, 'flock -u 9') > strpos($ssh, '>> "$SEC"'), 'SSH append happens under lock');
}

echo "PASS bcrypt-only login/save, input boundaries, audit redaction, errors and concurrent writers\n";
