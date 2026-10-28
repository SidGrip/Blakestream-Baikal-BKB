<?php
require_once __DIR__ . '/inc/web-security.php';
bs_secure_session_start();
header('Cache-Control: no-store');
header('Content-Type: application/json');
if (!isset($_SESSION['_logged_']) || $_SESSION['_logged_'] !== true) {
    http_response_code(403); echo json_encode(array('ok' => false, 'error' => 'Forbidden')); exit;
}
require_once __DIR__ . '/inc/backup-v2.php';
if (!isset($_SESSION['backup_csrf']) || !dev_activity_csrf_well_formed($_SESSION['backup_csrf'])) $_SESSION['backup_csrf'] = dev_activity_csrf_mint();
$token = $_SESSION['backup_csrf'];
$webToken = bs_csrf_ensure($_SESSION);
$method = isset($_SERVER['REQUEST_METHOD']) ? strtoupper($_SERVER['REQUEST_METHOD']) : '';
$integrity = bs_request_integrity_check($method, $method === 'GET' ? 'read' : 'mutation', $webToken, $_SERVER);
if (!$integrity['ok']) {
    http_response_code($integrity['status']);
    echo json_encode(array('ok' => false, 'error' => $integrity['error'], 'csrf' => $token));
    exit;
}
session_write_close();
if (isset($_SERVER['CONTENT_LENGTH']) && (float)$_SERVER['CONTENT_LENGTH'] > 17 * 1024 * 1024) {
    http_response_code(413); echo json_encode(array('ok' => false, 'error' => 'Backup upload exceeds 16 MiB', 'csrf' => $token)); exit;
}
$request = bs_backup_request($method, $_GET, $_POST, $_FILES, $token);
if (isset($request['error'])) {
    http_response_code($request['code']); echo json_encode(array('ok' => false, 'error' => $request['error'], 'csrf' => $token)); exit;
}
$lockPath = '/run/blakestream/backup-web.lock';
$lock = is_link($lockPath) ? false : @fopen($lockPath, 'c');
if (!$lock || !flock($lock, LOCK_EX | LOCK_NB)) {
    http_response_code(409); echo json_encode(array('ok' => false, 'error' => 'Backup is busy; retry shortly', 'csrf' => $token)); exit;
}
@chmod($lockPath, 0600);
$verb = $request['verb'];
$result = null;
$upload = '/run/blakestream/backup-upload.bin';
$export = '/run/blakestream-backup/backup-export.zip';
try {
    if ($verb === 'import') {
        $file = $_FILES['file'];
        if (!isset($file['error'], $file['size'], $file['tmp_name']) || $file['error'] !== UPLOAD_ERR_OK ||
            $file['size'] > 16 * 1024 * 1024 || !is_uploaded_file($file['tmp_name'])) {
            $result = array('ok' => false, 'error' => 'unsafe-archive');
        } else {
            $cleanup = bs_backup_helper('finish', array());
            if (!$cleanup['ok'] || file_exists($upload) || is_link($upload) || !move_uploaded_file($file['tmp_name'], $upload)) {
                $result = array('ok' => false, 'error' => 'io');
            } else {
                chmod($upload, 0600);
            }
        }
    }
    if ($result === null) $result = bs_backup_helper($verb, $request['payload']);
    if ($result['ok'] && $verb === 'export') {
        if (is_link($export) || !is_file($export) || !isset($result['bytes'], $result['sha256']) ||
            filesize($export) !== $result['bytes'] || hash_file('sha256', $export) !== $result['sha256']) {
            $result = array('ok' => false, 'error' => 'checksum');
        } else {
            header('Content-Type: application/zip');
            header('Content-Disposition: attachment; filename="' . $request['payload']['name'] . '.zip"');
            header('Content-Length: ' . filesize($export));
            readfile($export);
            $result = null;
        }
    }
    if ($result !== null) {
        $result['csrf'] = $token;
        if (!$result['ok']) {
            http_response_code(400);
            $result['error'] = bs_backup_message(isset($result['error']) ? $result['error'] : 'io');
        } elseif ($verb === 'list') {
            $result['data'] = $result['backups']; unset($result['backups']);
        } else {
            $texts = array('snapshot' => 'Application backup saved', 'import' => 'Backup imported',
                'restore' => 'Application backup restored. Review restored network settings separately.',
                'rename' => 'Backup renamed', 'delete' => 'Backup deleted');
            $result['info'] = array(array('type' => 'success', 'text' => isset($texts[$verb]) ? $texts[$verb] : 'Backup complete'));
        }
        echo json_encode($result);
    }
} finally {
    if ($verb === 'import' || $verb === 'export') bs_backup_helper('finish', array());
    flock($lock, LOCK_UN); fclose($lock);
}
