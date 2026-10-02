<?php
require_once __DIR__ . '/dev-activity.php';

function bs_wifi_reads() {
    return array('status', 'pending');
}

function bs_wifi_mutations() {
    return array('scan', 'connect', 'disconnect', 'forget', 'radio');
}

function bs_wifi_actions() {
    return array_merge(bs_wifi_reads(), bs_wifi_mutations());
}

function bs_wifi_redact($data) {
    if (!is_array($data)) return $data;
    $out = array();
    foreach ($data as $key => $value) {
        $name = is_string($key) ? strtolower($key) : $key;
        if (is_string($name) && (
            $name === 'psk' || $name === 'password' || $name === 'secret' || $name === 'pin' ||
            substr($name, -4) === '-psk' || substr($name, -9) === '-password' ||
            substr($name, -7) === '-secret' || substr($name, -4) === '-pin' ||
            strpos($name, 'wep-key') === 0 || strpos($name, 'private-key') !== false
        )) continue;
        $out[$key] = is_array($value) ? bs_wifi_redact($value) : $value;
    }
    return $out;
}

function bs_wifi_request($method, $action, $raw, $token) {
    if (!in_array($action, bs_wifi_actions(), true)) {
        return array('error' => 'Unknown action', 'code' => 400);
    }
    if (in_array($action, bs_wifi_reads(), true)) {
        return $method === 'GET' ? array('payload' => array()) : array('error' => 'GET required', 'code' => 405);
    }
    if ($method !== 'POST') return array('error' => 'POST required', 'code' => 405);
    if (!is_string($raw) || strlen($raw) > 4096) return array('error' => 'Request too large', 'code' => 413);
    $body = json_decode($raw, true);
    if (!is_array($body)) return array('error' => 'Invalid JSON', 'code' => 400);
    if (!dev_activity_csrf_accept($token, isset($body['csrf']) ? $body['csrf'] : null)) {
        return array('error' => 'Session token expired; reload Wi-Fi settings', 'code' => 403);
    }
    $payload = array();
    if ($action === 'connect') {
        foreach (array('ssid', 'security', 'psk', 'hidden', 'uuid') as $key) {
            if (array_key_exists($key, $body)) $payload[$key] = $body[$key];
        }
    } else if ($action === 'forget') {
        $payload['uuid'] = isset($body['uuid']) ? $body['uuid'] : '';
    } else if ($action === 'disconnect') {
        if (isset($body['uuid'])) $payload['uuid'] = $body['uuid'];
    } else if ($action === 'radio') {
        if (!array_key_exists('enabled', $body) || ($body['enabled'] !== true && $body['enabled'] !== false)) {
            return array('error' => 'invalid radio state', 'code' => 400);
        }
        $payload['enabled'] = $body['enabled'];
    }
    return array('payload' => $payload);
}

function bs_wifi_helper($action, $payload) {
    if (!in_array($action, bs_wifi_actions(), true)) {
        return array('ok' => false, 'error' => 'Unknown action');
    }
    $command = 'exec /usr/bin/sudo -n /opt/scripta/bin/wifi-settings.py ' . $action;
    $pipes = array();
    $proc = @proc_open($command, array(0 => array('pipe', 'r'), 1 => array('pipe', 'w'), 2 => array('pipe', 'w')), $pipes);
    if (!is_resource($proc)) return array('ok' => false, 'error' => 'Wi-Fi service unavailable');
    $body = json_encode($payload);
    if ($body === '[]' || $body === false) $body = '{}';
    fwrite($pipes[0], $body);
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
        return array('ok' => false, 'error' => $failed ? 'Wi-Fi service timed out; reload to check status' : 'Wi-Fi service unavailable');
    }
    return bs_wifi_redact($data);
}
