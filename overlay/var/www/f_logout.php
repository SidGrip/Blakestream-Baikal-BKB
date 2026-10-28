<?php
require_once __DIR__ . '/inc/web-security.php';
bs_secure_session_start();
header('Cache-Control: no-store');
header('Content-Type: application/json');

$token = isset($_SESSION['web_csrf']) ? $_SESSION['web_csrf'] : '';
bs_enforce_action('logout', array(), array('logout'), $token);
$_SESSION = array();
if (ini_get('session.use_cookies')) {
    $params = session_get_cookie_params();
    setcookie(session_name(), '', time() - 42000, $params['path'], $params['domain'], $params['secure'], $params['httponly']);
}
session_destroy();
echo json_encode(array('ok' => true, 'redirect' => 'login.php'));
?>
