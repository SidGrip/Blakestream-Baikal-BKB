<?php
require_once __DIR__ . '/../overlay/var/www/inc/network-settings.php';
function check_network($condition, $message) {
    if (!$condition) { fwrite(STDERR, "FAIL: $message\n"); exit(1); }
}
$token = str_repeat('a', 64);
foreach (array('apply', 'confirm', 'rollback') as $action) {
    check_network(bs_network_request('GET', $action, '', $token, '')['code'] === 405, 'mutation requires POST');
    check_network(bs_network_request('POST', $action, '{}', $token, '')['code'] === 403, 'mutation requires CSRF');
    check_network(bs_network_request('POST', $action, '{', $token, '')['code'] === 400, 'JSON validation');
    check_network(bs_network_request('POST', $action, str_repeat('x', 4097), $token, '')['code'] === 413, 'bounded body');
}
check_network(isset(bs_network_request('GET', 'status', '', $token, '')['payload']), 'read-only status');
check_network(bs_network_request('POST', 'status', '', $token, '')['code'] === 405, 'status requires GET');
check_network(isset(bs_network_request('GET', 'pending', '', $token, '')['payload']), 'pending is a read-only check');
check_network(bs_network_request('POST', 'pending', '', $token, '')['code'] === 405, 'pending requires GET');
check_network(bs_network_request('POST', 'activate', '{}', $token, '')['code'] === 400, 'internal worker not exposed');
$body = array('csrf' => $token, 'id' => 'test', 'server_address' => 'untrusted');
$parsed = bs_network_request('POST', 'confirm', json_encode($body), $token, '192.0.2.10');
check_network($parsed['payload'] === array('id' => 'test', 'server_address' => '192.0.2.10'), 'server address cannot be spoofed by JSON');
$body = array('csrf' => $token, 'interface' => 'eth0', 'mode' => 'dhcp', 'command' => 'bad', 'state_path' => '/tmp/bad');
$parsed = bs_network_request('POST', 'apply', json_encode($body), $token, '');
check_network($parsed['payload'] === array('interface' => 'eth0', 'mode' => 'dhcp'), 'only network fields reach helper');
check_network(bs_network_helper('activate', array())['ok'] === false, 'worker inaccessible via wrapper');
$endpoint = file_get_contents(__DIR__ . '/../overlay/var/www/f_network.php');
check_network(strpos($endpoint, "\$_SESSION['_logged_']") < strpos($endpoint, 'bs_network_helper('), 'auth before helper');
check_network(strpos($endpoint, 'userDev') === false, 'network settings independent of Dev Tools');
$login = file_get_contents(__DIR__ . '/../overlay/var/www/login.php');
check_network(strpos($login, "\$_GET['network'] === '1'") !== false, 'login return is an allowlisted flag');
check_network(strpos($login, 'index.php#/settings?section=network') !== false, 'login returns to Network');
check_network(strpos($login, 'networkConfirm') === false, 'login cannot confirm network changes');
$draftPath = tempnam(sys_get_temp_dir(), 'bkb-network-draft-');
$draft = array('v' => 1, 'staged' => true, 'applied' => false, 'portable' => array('interfaces' => array(
    array('interface' => 'eth0', 'mode' => 'dhcp', 'address' => '192.0.2.99', 'dns' => array('192.0.2.1')),
    array('interface' => 'eth1', 'mode' => 'static', 'address' => '192.0.2.50', 'prefix' => 24, 'gateway' => '192.0.2.1', 'dns' => array('192.0.2.53'))
)));
file_put_contents($draftPath, json_encode($draft));
$restored = bs_network_restored_draft($draftPath);
check_network(count($restored['interfaces']) === 2, 'restored portable interfaces available for review');
check_network($restored['interfaces'][0]['address'] === '' && $restored['interfaces'][0]['dns'] === array(), 'DHCP review never pins archived lease or DNS');
check_network($restored['interfaces'][1]['address'] === '192.0.2.50', 'static review retains explicit address');
$draft['portable']['interfaces'][1]['dns'] = array('not-an-address');
file_put_contents($draftPath, json_encode($draft));
check_network(count(bs_network_restored_draft($draftPath)['interfaces']) === 1, 'invalid static draft excluded');
$draft['applied'] = true;
file_put_contents($draftPath, json_encode($draft));
check_network(bs_network_restored_draft($draftPath) === null, 'only unapplied staged data is offered');
file_put_contents($draftPath, str_repeat('x', 16385));
check_network(bs_network_restored_draft($draftPath) === null, 'bounded staged document');
unlink($draftPath);
echo "PASS network endpoint method, CSRF, trusted-address and input boundaries\n";
