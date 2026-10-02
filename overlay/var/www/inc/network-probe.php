<?php
// Endpoint identity does not depend on pool credentials or DNS answers.
function bs_probe_canonical_host($host) {
    if (!is_string($host) || $host === '') return null;
    $host = strtolower($host);
    if (substr($host, -1) === '.') $host = substr($host, 0, -1);
    $packed = @inet_pton($host);
    if ($packed !== false) return inet_ntop($packed);
    // Reject ambiguous numeric IP forms rather than changing their destination.
    if (preg_match('/^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*$/D', $host) ||
        strpos($host, ':') !== false || strlen($host) > 253) return null;
    foreach (explode('.', $host) as $label) {
        if (!preg_match('/^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$/D', $label)) return null;
    }
    return $host;
}

function bs_probe_parse_endpoint($url) {
    if (!is_string($url)) return null;
    $url = trim($url);
    if ($url === '' || preg_match('/[\s\x00-\x1f\x7f]/', $url)) return null;
    $url = preg_replace('/^quota:[0-9]+;/i', '', $url);
    if (strpos($url, '://') === false) $url = 'tcp://' . $url;
    // parse_url accepts a numeric prefix in a malformed port on some PHP versions.
    if (!preg_match('~\A[a-z][a-z0-9+.-]*://(?:\[[0-9a-f:.]+\]|[a-z0-9_.-]+):[0-9]{1,5}(?:[/?#]|\z)~i', $url)) return null;
    $parts = @parse_url($url);
    if (!is_array($parts) || !isset($parts['host'], $parts['port'], $parts['scheme']) ||
        isset($parts['user']) || isset($parts['pass']) ||
        !in_array(strtolower($parts['scheme']), array('tcp', 'http', 'https', 'stratum+tcp', 'stratum+ssl', 'stratum+tls'), true) ||
        $parts['port'] < 1 || $parts['port'] > 65535) return null;
    $host = $parts['host'];
    if (substr($host, 0, 1) === '[' && substr($host, -1) === ']') $host = substr($host, 1, -1);
    $host = bs_probe_canonical_host($host);
    if ($host === null) return null;
    return array('host' => $host, 'port' => (int)$parts['port']);
}

function bs_probe_endpoint_key($host, $port) {
    return 'v1|' . $host . '|' . (int)$port;
}

function bs_probe_connect_host($host) {
    return strpos($host, ':') !== false ? 'tcp://[' . $host . ']' : $host;
}

function bs_probe_endpoints_from_saved($saved) {
    $out = array();
    $index_by_key = array();
    if (!is_array($saved) || !isset($saved['categories']) || !is_array($saved['categories'])) return $out;
    foreach ($saved['categories'] as $cat) {
        if (!is_array($cat) || !isset($cat['pools']) || !is_array($cat['pools'])) continue;
        foreach ($cat['pools'] as $pool) {
            if (!is_array($pool)) continue;
            $hp = bs_probe_parse_endpoint(isset($pool['url']) ? $pool['url'] : '');
            if ($hp === null) continue;
            $name = isset($pool['name']) && is_string($pool['name']) ? trim($pool['name']) : '';
            if ($name === '') $name = $hp['host'];
            $key = bs_probe_endpoint_key($hp['host'], $hp['port']);
            if (isset($index_by_key[$key])) {
                $i = $index_by_key[$key];
                if (!in_array($name, $out[$i]['names'], true)) $out[$i]['names'][] = $name;
                $out[$i]['name'] = implode(', ', $out[$i]['names']);
                continue;
            }
            $index_by_key[$key] = count($out);
            $out[] = array('index' => count($out), 'key' => $key, 'name' => $name,
                'names' => array($name), 'host' => $hp['host'], 'port' => $hp['port']);
        }
    }
    return $out;
}

function bs_probe_select($endpoints, $selector) {
    if (!is_string($selector) && !is_int($selector)) return null;
    $selector = (string)$selector;
    foreach ($endpoints as $endpoint) {
        if ($selector === $endpoint['key'] || $selector === (string)$endpoint['index']) return $endpoint;
    }
    return null;
}
