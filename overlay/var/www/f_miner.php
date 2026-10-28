<?php
require_once __DIR__ . '/inc/web-security.php';
bs_secure_session_start();
header('Content-Type: application/json');
header('Cache-Control: no-store');
if (!isset($_SESSION['_logged_']) || $_SESSION['_logged_'] !== true) {
    http_response_code(403);
    echo json_encode(array('ok' => false, 'error' => 'forbidden'));
    exit;
}

$method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '';
$body = array();
if ($method === 'POST') {
    $raw = file_get_contents('php://input', false, null, 0, 4097);
    if (!is_string($raw) || strlen($raw) > 4096 || !is_array($body = json_decode($raw, true))) {
        http_response_code(400);
        echo json_encode(array('ok' => false, 'error' => 'invalid JSON body'));
        exit;
    }
}
$command = $method === 'POST'
    ? (isset($body['command']) && is_string($body['command']) ? $body['command'] : '')
    : (isset($_GET['command']) && is_string($_GET['command']) ? $_GET['command'] : 'summary');
$parameter = $method === 'POST'
    ? (isset($body['parameter']) && is_string($body['parameter']) ? $body['parameter'] : '')
    : (isset($_GET['parameter']) && is_string($_GET['parameter']) ? $_GET['parameter'] : '');

$readCommands = array('summary', 'devs', 'pools', 'stats', 'version', 'config', 'coin', 'check');
$mutationCommands = array('restart', 'ascidentify');
$mode = in_array($command, $readCommands, true) ? 'read'
    : (in_array($command, $mutationCommands, true) ? 'mutation' : null);
$result = bs_request_integrity_check($method, $mode,
    isset($_SESSION['web_csrf']) ? $_SESSION['web_csrf'] : '', $_SERVER);
if (!$result['ok']) {
    http_response_code($result['status']);
    echo json_encode(array('ok' => false, 'error' => $result['error']));
    exit;
}
if ($command === 'restart' && $parameter !== '') {
    http_response_code(400);
    echo json_encode(array('ok' => false, 'error' => 'restart takes no parameter'));
    exit;
}
if ($command === 'ascidentify' && preg_match('/^0[0-2]$/D', $parameter) !== 1) {
    http_response_code(400);
    echo json_encode(array('ok' => false, 'error' => 'invalid board identifier'));
    exit;
}
if ($mode === 'read' && $parameter !== '') {
    http_response_code(400);
    echo json_encode(array('ok' => false, 'error' => 'read command takes no parameter'));
    exit;
}

include_once __DIR__ . '/inc/cgminer.inc.php';
echo json_encode($parameter === '' ? cgminer($command) : cgminer($command, $parameter));
?>
