<?php
require_once __DIR__ . '/dev-activity.php';

function bs_backup_name($name) {
    return is_string($name) && preg_match('/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/D', $name);
}

function bs_backup_request($method, $query, $post, $files, $token) {
    $verbs = array('backup' => 'snapshot', 'restore' => 'restore', 'export' => 'export', 'rename' => 'rename', 'delete' => 'delete');
    $actions = array_intersect(array_keys($query), array_keys($verbs));
    if (count($actions) > 1) return array('error' => 'Choose one backup operation', 'code' => 400);
    if ($files) {
        if ($method !== 'POST' || $actions || !isset($files['file'])) return array('error' => 'Invalid upload', 'code' => 400);
        $verb = 'import';
        $name = isset($post['name']) ? $post['name'] : 'import-' . gmdate('Ymd-His') . '-' . substr(sha1(uniqid('', true)), 0, 6);
    } else {
        if ($method !== 'GET') return array('error' => 'GET or multipart upload required', 'code' => 405);
        $key = count($actions) ? reset($actions) : null;
        $verb = $key === null ? 'list' : $verbs[$key];
        $name = $key === 'backup' ? (isset($query['name']) ? $query['name'] : '') : ($key === null ? '' : $query[$key]);
    }
    if (!in_array($verb, array('list', 'export'), true)) {
        $given = $verb === 'import' ? (isset($post['csrf']) ? $post['csrf'] : null) : (isset($query['csrf']) ? $query['csrf'] : null);
        if (!dev_activity_csrf_accept($token, $given)) return array('error' => 'Session token expired; reload Backup', 'code' => 403);
    }
    if ($verb !== 'list' && !bs_backup_name($name)) return array('error' => 'Invalid backup name', 'code' => 400);
    $payload = $verb === 'list' ? array() : array('name' => $name);
    if ($verb === 'snapshot') {
        if (!is_string($query['backup']) || strlen($query['backup']) > 12000) return array('error' => 'Invalid backup selection', 'code' => 400);
        $items = json_decode($query['backup'], true);
        if (!is_array($items) || count($items) > 64) return array('error' => 'Invalid backup selection', 'code' => 400);
        $payload['items'] = $items;
    }
    if ($verb === 'rename') {
        if (!isset($query['to']) || !bs_backup_name($query['to'])) return array('error' => 'Invalid new name', 'code' => 400);
        $payload['to'] = $query['to'];
    }
    return array('verb' => $verb, 'payload' => $payload);
}

function bs_backup_message($code) {
    $messages = array(
        'exists' => 'A backup with that name already exists', 'not-found' => 'Backup not found',
        'limit' => 'Backup exceeds its file or archive size limit', 'missing' => 'A selected file is missing; no backup was saved',
        'checksum' => 'Backup checksum verification failed', 'unsafe-archive' => 'Unsupported or invalid backup archive',
        'symlink' => 'Backup refused a symbolic link', 'network' => 'Cannot read current network settings; no backup was saved',
        'measured' => 'Measured history could not be safely read; no backup was saved',
        'busy' => 'Backup or measured history is busy; retry shortly',
        'rollback' => 'Restore failed; previous files were restored',
        'rollback-failed' => 'Restore recovery is incomplete; do not restart the miner',
        'invalid-name' => 'Invalid backup name', 'invalid-request' => 'Invalid backup request',
        'io' => 'Backup storage operation failed'
    );
    return isset($messages[$code]) ? $messages[$code] : 'Backup operation failed';
}

function bs_backup_helper($verb, $payload) {
    if (!in_array($verb, array('snapshot', 'restore', 'list', 'export', 'import', 'finish', 'rename', 'delete'), true)) return array('ok' => false, 'error' => 'invalid-request');
    $pipes = array();
    $proc = @proc_open('exec /usr/bin/sudo -n /opt/scripta/bin/backup-settings.py ' . $verb,
        array(0 => array('pipe', 'r'), 1 => array('pipe', 'w'), 2 => array('pipe', 'w')), $pipes);
    if (!is_resource($proc)) return array('ok' => false, 'error' => 'io');
    fwrite($pipes[0], json_encode((object)$payload));
    fclose($pipes[0]);
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    $out = ''; $bytes = 0; $failed = false; $deadline = microtime(true) + 55;
    do {
        $chunk = stream_get_contents($pipes[1], 65536);
        $err = stream_get_contents($pipes[2], 65536);
        $out .= $chunk; $bytes += strlen($chunk) + strlen($err);
        $state = proc_get_status($proc);
        if ($bytes > 1048576 || microtime(true) > $deadline) {
            $failed = true; proc_terminate($proc, 9); break;
        }
        if (!$state['running'] && feof($pipes[1]) && feof($pipes[2])) break;
        usleep(20000);
    } while (true);
    fclose($pipes[1]); fclose($pipes[2]); proc_close($proc);
    $result = $failed ? null : json_decode($out, true);
    return is_array($result) && isset($result['ok']) ? $result : array('ok' => false, 'error' => $failed ? 'busy' : 'io');
}
