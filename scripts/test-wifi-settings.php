<?php
require_once __DIR__ . '/../overlay/var/www/inc/wifi-settings.php';
function check_wifi($condition, $message) {
    if (!$condition) { fwrite(STDERR, "FAIL: $message\n"); exit(1); }
}
$token = str_repeat('a', 64);
foreach (array('scan', 'connect', 'disconnect', 'forget', 'radio') as $action) {
    check_wifi(bs_wifi_request('GET', $action, '', $token)['code'] === 405, "$action requires POST");
    check_wifi(bs_wifi_request('POST', $action, '{}', $token)['code'] === 403, "$action requires CSRF");
    check_wifi(bs_wifi_request('POST', $action, '{', $token)['code'] === 400, "$action JSON validation");
    check_wifi(bs_wifi_request('POST', $action, str_repeat('x', 4097), $token)['code'] === 413, "$action bounded body");
}
check_wifi(isset(bs_wifi_request('GET', 'status', '', $token)['payload']), 'read-only status');
check_wifi(bs_wifi_request('POST', 'status', '', $token)['code'] === 405, 'status requires GET');
check_wifi(isset(bs_wifi_request('GET', 'pending', '', $token)['payload']), 'pending is read-only');
check_wifi(bs_wifi_request('POST', 'activate', '{}', $token)['code'] === 400, 'internal activate is not exposed');
check_wifi(bs_wifi_request('POST', 'tick', '{}', $token)['code'] === 400, 'internal tick is not exposed');
check_wifi(bs_wifi_request('POST', 'provision', '{}', $token)['code'] === 400, 'boot provision is not exposed');
check_wifi(bs_wifi_request('POST', 'sync-radio', '{}', $token)['code'] === 400, 'sync-radio is not exposed');
check_wifi(bs_wifi_request('POST', 'migrate-secrets', '{}', $token)['code'] === 400, 'key migration is not exposed');
$scan = bs_wifi_request('POST', 'scan', json_encode(array('csrf' => $token)), $token);
check_wifi(isset($scan['payload']) && $scan['payload'] === array(), 'scan CSRF POST has an empty helper payload');
$body = array('csrf' => $token, 'ssid' => 'LabNet', 'security' => 'wpa-psk', 'psk' => 'secretpass', 'hidden' => false, 'command' => 'bad', 'interface' => 'eth0');
$parsed = bs_wifi_request('POST', 'connect', json_encode($body), $token);
check_wifi($parsed['payload'] === array('ssid' => 'LabNet', 'security' => 'wpa-psk', 'psk' => 'secretpass', 'hidden' => false), 'only Wi-Fi fields reach helper');
$uuid = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
$reconnect = bs_wifi_request('POST', 'connect', json_encode(array('csrf' => $token, 'uuid' => $uuid)), $token);
check_wifi($reconnect['payload'] === array('uuid' => $uuid), 'saved network reconnect sends only its UUID');
$mixed = bs_wifi_request('POST', 'connect', json_encode(array('csrf' => $token, 'uuid' => $uuid, 'psk' => 'secretpass')), $token);
check_wifi(isset($mixed['payload']['psk']), 'mixed connect modes reach strict helper validation rather than silently ignoring fields');
$radio = bs_wifi_request('POST', 'radio', json_encode(array('csrf' => $token, 'enabled' => true, 'interface' => 'wlan0')), $token);
check_wifi($radio['payload'] === array('enabled' => true), 'radio payload is only enabled');
check_wifi(bs_wifi_request('POST', 'radio', json_encode(array('csrf' => $token, 'enabled' => 'true')), $token)['code'] === 400, 'radio rejects string enabled');
check_wifi(bs_wifi_request('POST', 'radio', json_encode(array('csrf' => $token, 'enabled' => 1)), $token)['code'] === 400, 'radio rejects integer enabled');
$leaked = array('ok' => true, 'psk' => 'secretpass', 'nested' => array('password' => 'secretpass', 'ssid' => 'LabNet'));
$redacted = bs_wifi_redact($leaked);
check_wifi(!isset($redacted['psk']) && !isset($redacted['nested']['password']), 'credentials redacted from helper output');
check_wifi($redacted['nested']['ssid'] === 'LabNet', 'non-secret fields retained');
check_wifi(bs_wifi_helper('activate', array())['ok'] === false, 'worker inaccessible via wrapper');
$endpoint = file_get_contents(__DIR__ . '/../overlay/var/www/f_wifi.php');
check_wifi(strpos($endpoint, "\$_SESSION['_logged_']") < strpos($endpoint, 'bs_wifi_helper('), 'auth before helper');
check_wifi(strpos($endpoint, 'userDev') === false, 'Wi-Fi settings independent of Dev Tools');
$sudoers = file_get_contents(__DIR__ . '/../overlay/etc/sudoers.d/blakestream-wifi');
check_wifi(strpos($sudoers, 'NOPASSWD: ALL') === false, 'no blanket sudo');
foreach (array('activate', 'tick', 'provision', 'sync-radio', 'migrate-secrets') as $verb) {
    check_wifi(strpos($sudoers, 'wifi-settings.py ' . $verb) === false, "sudoers omits $verb");
}
foreach (array('status', 'pending', 'scan', 'connect', 'disconnect', 'forget', 'radio') as $verb) {
    check_wifi(strpos($sudoers, 'wifi-settings.py ' . $verb) !== false, "sudoers allows $verb");
}
echo "PASS Wi-Fi endpoint method, CSRF, radio bool, redaction and sudoers boundaries\n";
