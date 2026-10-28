<?php
require_once __DIR__ . '/dev-activity.php';

function bs_backup_name($name) {
    return is_string($name) && preg_match('/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/D', $name);
}

function bs_backup_request($method, $query, $post, $files, $token) {
    $verbs = array('backup' => 'snapshot', 'restore' => 'restore', 'export' => 'export', 'rename' => 'rename', 'delete' => 'delete');
    $query_actions = array_values(array_intersect(array_keys($query), array_keys($verbs)));
    $post_actions = array_values(array_intersect(array_keys($post), array_keys($verbs)));
    if (count($query_actions) + count($post_actions) > 1)
        return array('error' => 'Choose one backup operation', 'code' => 400);
    if ($files) {
        if ($method !== 'POST' || $query_actions || $post_actions || !isset($files['file']))
            return array('error' => 'Invalid upload', 'code' => 400);
        $verb = 'import';
        $name = isset($post['name']) ? $post['name'] : 'import-' . gmdate('Ymd-His') . '-' . substr(sha1(uniqid('', true)), 0, 6);
    } elseif ($method === 'GET') {
        if ($post_actions) return array('error' => 'Invalid backup request', 'code' => 400);
        $key = count($query_actions) ? reset($query_actions) : null;
        if ($key !== null && $key !== 'export')
            return array('error' => 'POST required', 'code' => 405);
        $verb = $key === null ? 'list' : 'export';
        $name = $key === null ? '' : $query[$key];
    } elseif ($method === 'POST') {
        if ($query_actions || count($post_actions) !== 1)
            return array('error' => 'Choose one backup operation', 'code' => 400);
        $key = reset($post_actions);
        if ($key === 'export') return array('error' => 'GET required', 'code' => 405);
        $verb = $verbs[$key];
        $name = $key === 'backup' ? (isset($post['name']) ? $post['name'] : '') : $post[$key];
    } else {
        return array('error' => 'GET or POST required', 'code' => 405);
    }
    if (!in_array($verb, array('list', 'export'), true)) {
        $given = isset($post['csrf']) ? $post['csrf'] : null;
        if (!dev_activity_csrf_accept($token, $given)) return array('error' => 'Session token expired; reload Backup', 'code' => 403);
    }
    if ($verb !== 'list' && !bs_backup_name($name)) return array('error' => 'Invalid backup name', 'code' => 400);
    $payload = $verb === 'list' ? array() : array('name' => $name);
    if ($verb === 'snapshot') {
        if (!is_string($post['backup']) || strlen($post['backup']) > 12000) return array('error' => 'Invalid backup selection', 'code' => 400);
        $items = json_decode($post['backup'], true);
        if (!is_array($items) || count($items) > 64) return array('error' => 'Invalid backup selection', 'code' => 400);
        $payload['items'] = $items;
    }
    if ($verb === 'rename') {
        if (!isset($post['to']) || !bs_backup_name($post['to'])) return array('error' => 'Invalid new name', 'code' => 400);
        $payload['to'] = $post['to'];
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
    $termDeadline = null; $killDeadline = null; $sentKill = false;
    do {
        $chunk = stream_get_contents($pipes[1], 65536);
        $err = stream_get_contents($pipes[2], 65536);
        if (!is_string($chunk)) $chunk = '';
        if (!is_string($err)) $err = '';
        if (!$failed) $out .= $chunk;
        $bytes += strlen($chunk) + strlen($err);
        $now = microtime(true);
        $state = proc_get_status($proc);
        if (!$failed && ($bytes > 1048576 || $now > $deadline)) {
            $failed = true;
            // Give Python enough time to replay a full 32 MiB journal on a
            // conservative ~1 MiB/s legacy SD card, plus fsync overhead.
            @proc_terminate($proc, 15);
            $termDeadline = $now + 45.0;
        } elseif ($failed && !$sentKill && $state['running'] && $now >= $termDeadline) {
            // A wedged helper must not hold a PHP worker indefinitely.
            @proc_terminate($proc, 9);
            $sentKill = true;
            $killDeadline = $now + 1.0;
        }
        if (!$state['running'] && feof($pipes[1]) && feof($pipes[2])) break;
        if ($failed && !$state['running'] && $now >= $termDeadline) break;
        if ($sentKill && $now >= $killDeadline) break;
        usleep(20000);
    } while (true);
    fclose($pipes[1]); fclose($pipes[2]);
    // Reap the direct child. If TERM/KILL did not stop it, make one final
    // bounded KILL attempt before proc_close rather than abandoning it.
    $state = proc_get_status($proc);
    if ($state['running']) {
        if (!$sentKill) {
            @proc_terminate($proc, 9);
            $sentKill = true;
        }
        $reapDeadline = microtime(true) + 1.0;
        do {
            usleep(20000);
            $state = proc_get_status($proc);
        } while ($state['running'] && microtime(true) < $reapDeadline);
    }
    proc_close($proc);
    $result = $failed ? null : json_decode($out, true);
    return is_array($result) && isset($result['ok']) ? $result : array('ok' => false, 'error' => $failed ? 'busy' : 'io');
}
