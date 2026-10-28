<?php
require __DIR__ . '/../overlay/var/www/inc/web-security.php';

function security_expect($condition, $message) {
    if (!$condition) throw new Exception($message);
}

$token = str_repeat('a', 64);
$base = array('HTTP_HOST' => 'miner.local', 'HTTPS' => 'on', 'SERVER_PORT' => '443');
$read = bs_request_integrity_check('GET', 'read', '', $base);
security_expect($read['ok'], 'GET read allowed');
security_expect(bs_request_integrity_check('POST', 'read', '', $base)['status'] === 405, 'read is GET-only');
security_expect(bs_request_integrity_check('GET', 'mutation', $token, $base)['status'] === 405, 'mutation is POST-only');
security_expect(bs_request_integrity_check('POST', 'mutation', $token, $base)['status'] === 403, 'missing token rejected');

$valid = $base;
$valid['HTTP_X_BKB_CSRF'] = $token;
security_expect(bs_request_integrity_check('POST', 'mutation', $token, $valid)['ok'], 'token-only CLI/old browser allowed');
$valid['HTTP_ORIGIN'] = 'https://miner.local';
$valid['HTTP_REFERER'] = 'https://miner.local/settings';
security_expect(bs_request_integrity_check('POST', 'mutation', $token, $valid)['ok'], 'same origin accepted');

$port = array('HTTP_HOST' => '192.0.2.4:8443', 'HTTPS' => 'on', 'SERVER_PORT' => '8443',
    'HTTP_X_BKB_CSRF' => $token, 'HTTP_ORIGIN' => 'https://192.0.2.4:8443');
security_expect(bs_request_integrity_check('POST', 'mutation', $token, $port)['ok'], 'host port matched');

foreach (array('https://evil.example', 'null', "https://miner.local\n.example") as $origin) {
    $foreign = $valid;
    $foreign['HTTP_ORIGIN'] = $origin;
    security_expect(bs_request_integrity_check('POST', 'mutation', $token, $foreign)['status'] === 403,
        'foreign or malformed origin rejected');
}
$cross = $valid;
$cross['HTTP_SEC_FETCH_SITE'] = 'cross-site';
security_expect(bs_request_integrity_check('POST', 'mutation', $token, $cross)['status'] === 403, 'cross-site fetch rejected');
$wrong = $valid;
$wrong['HTTP_X_BKB_CSRF'] = str_repeat('b', 64);
security_expect(bs_request_integrity_check('POST', 'mutation', $token, $wrong)['status'] === 403, 'wrong token rejected');
security_expect(bs_request_integrity_check('POST', 'mutation', $token, array_merge($valid,
    array('HTTP_HOST' => "bad host")))['status'] === 403, 'malformed host cannot authorize origin');
security_expect(bs_action_mode('restart', array('status'), array('restart')) === 'mutation', 'action classification');
security_expect(bs_action_mode('unknown', array('status'), array('restart')) === null, 'unknown action fails closed');

foreach (array('index.php', 'f_backup.php', 'f_blakestream.php', 'f_dev.php', 'f_miner.php',
               'f_network.php', 'f_settings.php', 'f_status.php', 'f_wifi.php') as $endpoint) {
    $source = file_get_contents(__DIR__ . '/../overlay/var/www/' . $endpoint);
    security_expect(strpos($source, "\$_SESSION['_logged_'] !== true") !== false,
        $endpoint . ' requires the exact authenticated session state');
}

echo "PASS shared session CSRF, method and origin policy\n";
?>
