<?php
require_once __DIR__ . '/../overlay/var/www/inc/backup-v2.php';
function backup_expect($ok, $message) {
    if (!$ok) { fwrite(STDERR, "FAIL $message\n"); exit(1); }
}
$token = str_repeat('b', 64);
foreach (array('backup', 'restore', 'rename', 'delete') as $action) {
    $body = array($action => $action === 'backup' ? '[]' : 'sample', 'name' => 'sample', 'to' => 'renamed');
    backup_expect(bs_backup_request('GET', $body, array(), array(), $token)['code'] === 405, 'GET mutation rejected');
    backup_expect(bs_backup_request('POST', array(), $body, array(), $token)['code'] === 403, 'mutation token required');
    $body['csrf'] = $token;
    $request = bs_backup_request('POST', array(), $body, array(), $token);
    backup_expect(isset($request['verb']) && $request['payload']['name'] === 'sample', 'POST named mutation');
}
backup_expect(bs_backup_request('GET', array(), array(), array(), $token)['verb'] === 'list', 'authenticated list');
backup_expect(bs_backup_request('GET', array('export' => 'sample'), array(), array(), $token)['verb'] === 'export', 'authenticated download');
foreach (array('../sample', 'a/b', "sample\n", array('sample'), '') as $name) {
    backup_expect(!bs_backup_name($name), 'invalid name');
}
backup_expect(isset(bs_backup_request('GET', array('export' => 'a', 'delete' => 'a'), array(), array(), $token)['error']), 'one action only');
backup_expect(bs_backup_request('POST', array(), array('export' => 'sample'), array(), $token)['code'] === 405, 'export stays GET-only');
$files = array('file' => array('tmp_name' => '/tmp/fixture'));
backup_expect(bs_backup_request('POST', array(), array(), $files, $token)['code'] === 403, 'upload token required');
backup_expect(bs_backup_request('POST', array(), array('csrf' => $token), $files, $token)['verb'] === 'import', 'multipart import');
backup_expect(bs_backup_helper('arbitrary-command', array())['ok'] === false, 'fixed helper verbs only');
$helperSource = file_get_contents(__DIR__ . '/../overlay/var/www/inc/backup-v2.php');
backup_expect(strpos($helperSource, 'json_encode((object)$payload)') !== false, 'empty list/finish payload is a JSON object');
$term = strpos($helperSource, '@proc_terminate($proc, 15)');
$kill = strpos($helperSource, '@proc_terminate($proc, 9)');
backup_expect($term !== false && $kill !== false && $term < $kill, 'timeout sends TERM before KILL');
backup_expect(strpos($helperSource, '$termDeadline = $now + 45.0') !== false, 'TERM grace covers a full legacy-SD rollback');
backup_expect(strpos($helperSource, '$killDeadline = $now + 1.0') !== false, 'KILL drain is bounded');
backup_expect(strpos($helperSource, '$reapDeadline = microtime(true) + 1.0') !== false, 'final reap is bounded');
backup_expect(strpos($helperSource, 'proc_terminate($proc, 9); break;') === false, 'no immediate KILL-and-break timeout');
$endpoint = file_get_contents(__DIR__ . '/../overlay/var/www/f_backup.php');
backup_expect(strpos($endpoint, "\$_SESSION['_logged_']") < strpos($endpoint, 'bs_backup_helper('), 'auth before privileged helper');
backup_expect(strpos($endpoint, 'hash_file(\'sha256\'') !== false, 'verify exported bytes');
backup_expect(strpos($endpoint, 'is_uploaded_file') !== false, 'only PHP uploads accepted');
echo "PASS backup endpoint names, POST/CSRF guards, multipart import and authenticated export\n";
