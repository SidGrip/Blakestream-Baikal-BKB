<?php
require_once __DIR__ . '/dev-activity.php';

function bs_network_restored_draft($path = '/opt/scripta/etc/network-restore.json') {
    if (is_link($path) || !is_file($path) || filesize($path) > 16384) return null;
    $raw = @file_get_contents($path, false, null, 0, 16385);
    $data = is_string($raw) ? json_decode($raw, true) : null;
    if (!is_array($data) || !isset($data['v'], $data['staged'], $data['applied'], $data['portable']) ||
        $data['v'] !== 1 || $data['staged'] !== true || $data['applied'] !== false ||
        !is_array($data['portable']) || !isset($data['portable']['interfaces']) ||
        !is_array($data['portable']['interfaces']) || count($data['portable']['interfaces']) > 8) return null;
    $interfaces = array();
    foreach ($data['portable']['interfaces'] as $item) {
        if (!is_array($item) || !isset($item['interface'], $item['mode']) ||
            !is_string($item['interface']) || !preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/D', $item['interface']) ||
            !in_array($item['mode'], array('dhcp', 'static'), true)) continue;
        $row = array('interface' => $item['interface'], 'mode' => $item['mode'], 'address' => '',
            'prefix' => 24, 'gateway' => '', 'dns' => array());
        if ($item['mode'] === 'static') {
            if (!isset($item['address'], $item['prefix'], $item['gateway'], $item['dns']) ||
                !filter_var($item['address'], FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) ||
                !filter_var($item['gateway'], FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) ||
                !is_int($item['prefix']) || $item['prefix'] < 1 || $item['prefix'] > 30 ||
                !is_array($item['dns']) || count($item['dns']) < 1 || count($item['dns']) > 3) continue;
            $valid = true;
            foreach ($item['dns'] as $dns) if (!filter_var($dns, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) $valid = false;
            if (!$valid) continue;
            foreach (array('address', 'prefix', 'gateway', 'dns') as $key) $row[$key] = $item[$key];
        }
        $interfaces[] = $row;
    }
    return count($interfaces) ? array('interfaces' => $interfaces) : null;
}

function bs_network_request($method, $action, $raw, $token, $server_address) {
    if (!in_array($action, array('status', 'pending', 'apply', 'confirm', 'rollback'), true)) {
        return array('error' => 'Unknown action', 'code' => 400);
    }
    if ($action === 'status' || $action === 'pending') {
        return $method === 'GET' ? array('payload' => array()) : array('error' => 'GET required', 'code' => 405);
    }
    if ($method !== 'POST') return array('error' => 'POST required', 'code' => 405);
    if (!is_string($raw) || strlen($raw) > 4096) return array('error' => 'Request too large', 'code' => 413);
    $body = json_decode($raw, true);
    if (!is_array($body)) return array('error' => 'Invalid JSON', 'code' => 400);
    if (!dev_activity_csrf_accept($token, isset($body['csrf']) ? $body['csrf'] : null)) {
        return array('error' => 'Session token expired; reload Network settings', 'code' => 403);
    }
    $payload = array();
    if ($action === 'apply') {
        foreach (array('interface', 'mode', 'address', 'prefix', 'gateway', 'dns') as $key) {
            if (isset($body[$key])) $payload[$key] = $body[$key];
        }
    } else {
        $payload['id'] = isset($body['id']) ? $body['id'] : '';
        if ($action === 'confirm') $payload['server_address'] = $server_address;
    }
    return array('payload' => $payload);
}

function bs_network_helper($action, $payload) {
    if (!in_array($action, array('status', 'pending', 'apply', 'confirm', 'rollback'), true)) {
        return array('ok' => false, 'error' => 'Unknown action');
    }
    $command = 'exec /usr/bin/sudo -n /opt/scripta/bin/network-settings.py ' . $action;
    $pipes = array();
    $proc = @proc_open($command, array(0 => array('pipe', 'r'), 1 => array('pipe', 'w'), 2 => array('pipe', 'w')), $pipes);
    if (!is_resource($proc)) return array('ok' => false, 'error' => 'Network service unavailable');
    fwrite($pipes[0], json_encode($payload));
    fclose($pipes[0]);
    stream_set_blocking($pipes[1], false);
    stream_set_blocking($pipes[2], false);
    $out = '';
    $size = 0;
    $deadline = microtime(true) + 15;
    $failed = false;
    do {
        $chunk = stream_get_contents($pipes[1], 65536);
        $err = stream_get_contents($pipes[2], 65536);
        $out .= $chunk;
        $size += strlen($chunk) + strlen($err);
        $state = proc_get_status($proc);
        if ($size > 262144 || microtime(true) > $deadline) {
            $failed = true;
            proc_terminate($proc, 9);
            break;
        }
        if (!$state['running'] && feof($pipes[1]) && feof($pipes[2])) break;
        usleep(20000);
    } while (true);
    fclose($pipes[1]);
    fclose($pipes[2]);
    proc_close($proc);
    $data = $failed ? null : json_decode($out, true);
    if (!is_array($data) || !isset($data['ok'])) {
        return array('ok' => false, 'error' => $failed ? 'Network service timed out; reload to check pending changes' : 'Network service unavailable');
    }
    return $data;
}
