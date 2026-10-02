<?php
session_start();
header('Content-Type: application/json');
header('Cache-Control: no-store');
if (!isset($_SESSION['_logged_']) || $_SESSION['_logged_'] === false) {
    http_response_code(403);
    echo json_encode(array('ok' => false, 'error' => 'forbidden'));
    exit;
}
require_once __DIR__ . '/inc/wifi-settings.php';
if (!isset($_SESSION['wifi_csrf']) || !dev_activity_csrf_well_formed($_SESSION['wifi_csrf'])) {
    $_SESSION['wifi_csrf'] = dev_activity_csrf_mint();
}
$token = $_SESSION['wifi_csrf'];
session_write_close();
$action = isset($_GET['action']) && is_string($_GET['action']) ? $_GET['action'] : 'status';
$request = bs_wifi_request(
    isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '',
    $action, file_get_contents('php://input', false, null, 0, 4097), $token
);
if (isset($request['error'])) {
    http_response_code($request['code']);
    echo json_encode(array('ok' => false, 'error' => $request['error']));
    exit;
}
$result = bs_wifi_helper($action, $request['payload']);
$result = bs_wifi_redact($result);
$result['csrf'] = $token;
echo json_encode($result);
