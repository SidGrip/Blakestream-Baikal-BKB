<?php
/* Shared PHP 5.6-compatible session and request-integrity helpers. */

function bs_request_is_https($server = null) {
    if ($server === null) $server = $_SERVER;
    if (isset($server['HTTPS']) && $server['HTTPS'] !== '' && strtolower((string)$server['HTTPS']) !== 'off') return true;
    return isset($server['SERVER_PORT']) && (string)$server['SERVER_PORT'] === '443';
}

function bs_secure_session_start() {
    if (session_status() === PHP_SESSION_ACTIVE) return true;
    @ini_set('session.use_strict_mode', '1');
    @ini_set('session.use_only_cookies', '1');
    @ini_set('session.use_trans_sid', '0');
    @ini_set('session.cookie_httponly', '1');

    $secure = bs_request_is_https() || (string)ini_get('session.cookie_secure') === '1';
    /* PHP 5.6 has no SameSite argument. The path suffix is its standard fallback. */
    session_set_cookie_params(0, '/; SameSite=Strict', '', $secure, true);
    return session_start();
}

function bs_random_bytes($length) {
    $length = (int)$length;
    if ($length < 1) return '';
    if (function_exists('random_bytes')) {
        try {
            $bytes = random_bytes($length);
            if (is_string($bytes) && strlen($bytes) === $length) return $bytes;
        } catch (Exception $e) {
        }
    }
    if (function_exists('openssl_random_pseudo_bytes')) {
        $strong = false;
        $bytes = openssl_random_pseudo_bytes($length, $strong);
        if ($strong && is_string($bytes) && strlen($bytes) === $length) return $bytes;
    }
    $handle = @fopen('/dev/urandom', 'rb');
    if ($handle) {
        $bytes = fread($handle, $length);
        fclose($handle);
        if (is_string($bytes) && strlen($bytes) === $length) return $bytes;
    }
    return '';
}

function bs_csrf_well_formed($token) {
    return is_string($token) && preg_match('/^[0-9a-f]{64}$/D', $token) === 1;
}

function bs_hash_equals($expected, $given) {
    if (!is_string($expected) || !is_string($given)) return false;
    if (function_exists('hash_equals')) return hash_equals($expected, $given);
    if (strlen($expected) !== strlen($given)) return false;
    $diff = 0;
    for ($i = 0, $n = strlen($expected); $i < $n; $i++) $diff |= ord($expected[$i]) ^ ord($given[$i]);
    return $diff === 0;
}

function bs_csrf_ensure(&$session) {
    if (isset($session['web_csrf']) && bs_csrf_well_formed($session['web_csrf'])) return $session['web_csrf'];
    $bytes = bs_random_bytes(32);
    if (!is_string($bytes) || strlen($bytes) !== 32) return '';
    $session['web_csrf'] = bin2hex($bytes);
    return $session['web_csrf'];
}

function bs_request_header_token($server) {
    return isset($server['HTTP_X_BKB_CSRF']) && is_string($server['HTTP_X_BKB_CSRF'])
        ? $server['HTTP_X_BKB_CSRF'] : '';
}

function bs_request_origin($server, $header) {
    if (!isset($server[$header]) || !is_string($server[$header]) || $server[$header] === '') return null;
    $value = trim($server[$header]);
    if ($value === '' || strtolower($value) === 'null' || preg_match('/[\x00-\x20\x7f]/', $value)) return false;
    $parts = @parse_url($value);
    if (!is_array($parts) || empty($parts['scheme']) || empty($parts['host'])) return false;
    $scheme = strtolower($parts['scheme']);
    if ($scheme !== 'http' && $scheme !== 'https') return false;
    $origin = $scheme . '://' . strtolower($parts['host']);
    if (isset($parts['port'])) $origin .= ':' . (int)$parts['port'];
    return $origin;
}

function bs_expected_origin($server) {
    if (!isset($server['HTTP_HOST']) || !is_string($server['HTTP_HOST'])) return null;
    $host = trim($server['HTTP_HOST']);
    if ($host === '' || preg_match('/[\x00-\x20\x7f\/@]/', $host)) return null;
    if (preg_match('/^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$/D', $host) !== 1 &&
        preg_match('/^\[[0-9A-Fa-f:]+\](?::[0-9]{1,5})?$/D', $host) !== 1) return null;
    return (bs_request_is_https($server) ? 'https://' : 'http://') . strtolower($host);
}

function bs_request_integrity_check($method, $mode, $session_token, $server) {
    $method = strtoupper((string)$method);
    if ($mode === 'read') {
        return $method === 'GET'
            ? array('ok' => true, 'status' => 200, 'error' => '')
            : array('ok' => false, 'status' => 405, 'error' => 'method not allowed');
    }
    if ($mode !== 'mutation') return array('ok' => false, 'status' => 400, 'error' => 'unknown action');
    if ($method !== 'POST') return array('ok' => false, 'status' => 405, 'error' => 'method not allowed');

    $fetch_site = isset($server['HTTP_SEC_FETCH_SITE']) ? strtolower(trim((string)$server['HTTP_SEC_FETCH_SITE'])) : '';
    if ($fetch_site === 'cross-site') return array('ok' => false, 'status' => 403, 'error' => 'foreign origin');

    $expected = bs_expected_origin($server);
    $origin = bs_request_origin($server, 'HTTP_ORIGIN');
    if ($origin === false || ($origin !== null && ($expected === null || $origin !== $expected)))
        return array('ok' => false, 'status' => 403, 'error' => 'foreign origin');
    $referer = bs_request_origin($server, 'HTTP_REFERER');
    if ($referer === false || ($referer !== null && ($expected === null || $referer !== $expected)))
        return array('ok' => false, 'status' => 403, 'error' => 'foreign referer');

    $given = bs_request_header_token($server);
    if (!bs_csrf_well_formed($session_token) || !bs_csrf_well_formed($given) || !bs_hash_equals($session_token, $given))
        return array('ok' => false, 'status' => 403, 'error' => 'invalid request token');
    return array('ok' => true, 'status' => 200, 'error' => '');
}

function bs_action_mode($action, $reads, $mutations) {
    if (in_array($action, $reads, true)) return 'read';
    if (in_array($action, $mutations, true)) return 'mutation';
    return null;
}

function bs_enforce_action($action, $reads, $mutations, $session_token) {
    $mode = bs_action_mode($action, $reads, $mutations);
    $result = bs_request_integrity_check(isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '', $mode, $session_token, $_SERVER);
    if ($result['ok']) return $mode;
    http_response_code($result['status']);
    header('Content-Type: application/json');
    if ($result['status'] === 405) header('Allow: ' . ($mode === 'read' ? 'GET' : 'POST'));
    echo json_encode(array('ok' => false, 'error' => $result['error']));
    exit;
}
?>
