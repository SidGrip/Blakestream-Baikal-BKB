<?php
// Read the active default route without changing or querying network profiles.
function bs_connection_status($proc = '/proc/net', $sys = '/sys/class/net') {
    $routes = array();
    $readable = false;
    foreach (array('route', 'ipv6_route') as $file) {
        $raw = @file_get_contents($proc . '/' . $file, false, null, 0, 65536);
        if ($raw === false) continue;
        $readable = true;
        foreach (explode("\n", $raw) as $line) {
            $f = preg_split('/\s+/', trim($line));
            if ($file === 'route') {
                if (count($f) < 8 || $f[1] !== '00000000' || $f[7] !== '00000000' ||
                    !ctype_xdigit($f[3]) || !ctype_digit($f[6])) continue;
                $interface = $f[0]; $flags = hexdec($f[3]); $metric = (int)$f[6]; $family = 4;
            } else {
                if (count($f) !== 10 || $f[0] !== str_repeat('0', 32) || $f[1] !== '00' ||
                    !ctype_xdigit($f[8]) || !ctype_xdigit($f[5])) continue;
                $interface = $f[9]; $flags = hexdec($f[8]); $metric = hexdec($f[5]); $family = 6;
            }
            if (!($flags & 1) || ($flags & 512) || $interface === 'lo' ||
                !preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/D', $interface)) continue;
            $dir = $sys . '/' . $interface;
            $state = trim((string)@file_get_contents($dir . '/operstate'));
            $carrier = trim((string)@file_get_contents($dir . '/carrier'));
            if ($state === 'down' || $state === 'notpresent' || $state === 'lowerlayerdown' || $carrier === '0') continue;
            if (!is_dir($dir)) continue;
            $type = (is_dir($dir . '/wireless') || is_dir($dir . '/phy80211')) ? 'wifi' :
                (trim((string)@file_get_contents($dir . '/type')) === '1' ? 'ethernet' : 'other');
            $routes[] = array('type' => $type, 'interface' => $interface, 'metric' => $metric, 'family' => $family);
        }
    }
    usort($routes, function($a, $b) {
        if ($a['family'] !== $b['family']) return $a['family'] - $b['family'];
        if ($a['metric'] === $b['metric']) return strcmp($a['interface'], $b['interface']);
        return $a['metric'] < $b['metric'] ? -1 : 1;
    });
    return count($routes) ? array('type' => $routes[0]['type'], 'interface' => $routes[0]['interface']) :
        array('type' => $readable ? 'none' : 'unknown', 'interface' => '');
}
