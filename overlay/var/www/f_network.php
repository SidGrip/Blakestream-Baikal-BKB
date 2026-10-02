<?php
session_start();
header('Content-Type: application/json');
header('Cache-Control: no-store');
if (!isset($_SESSION['_logged_']) || $_SESSION['_logged_'] === false) {
    http_response_code(403);
    echo json_encode(array('ok' => false, 'error' => 'forbidden'));
    exit;
}
require_once __DIR__ . '/inc/network-settings.php';
if (!isset($_SESSION['network_csrf']) || !dev_activity_csrf_well_formed($_SESSION['network_csrf'])) {
    $_SESSION['network_csrf'] = dev_activity_csrf_mint();
}
$token = $_SESSION['network_csrf'];
session_write_close();
$action = isset($_GET['action']) && is_string($_GET['action']) ? $_GET['action'] : 'status';
$request = bs_network_request(
    isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '',
    $action, file_get_contents('php://input', false, null, 0, 4097), $token,
    isset($_SERVER['SERVER_ADDR']) ? $_SERVER['SERVER_ADDR'] : ''
);
if (isset($request['error'])) {
    http_response_code($request['code']);
    echo json_encode(array('ok' => false, 'error' => $request['error']));
    exit;
}
$result = bs_network_helper($action, $request['payload']);
if ($action === 'status') $result['restored_draft'] = bs_network_restored_draft();
$result['csrf'] = $token;
echo json_encode($result);
