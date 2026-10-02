<?php
require_once __DIR__ . '/inc/web-auth.php';
session_start();
if (!isset($_SESSION['_logged_'])) $_SESSION['_logged_'] = false;

$configUipwd = '/opt/scripta/etc/uipasswd';
$securityLog = '/opt/scripta/var/security.log';

if (!array_key_exists('userPassword', $_POST)) {
    echo 0;
    return;
}

$password = $_POST['userPassword'];
$success = bs_web_auth_check($configUipwd, $password);
if ($success) $_SESSION['_logged_'] = true;
if (!bs_web_auth_log($securityLog, $success, $_SERVER, $password)) {
    error_log('Web authentication audit update failed');
}
echo $success ? 1 : 0;
