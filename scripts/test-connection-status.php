<?php
require_once __DIR__ . '/../overlay/var/www/inc/connection-status.php';
$dir = sys_get_temp_dir() . '/bkb-connection-' . bin2hex(openssl_random_pseudo_bytes(6));
mkdir($dir, 0700);
mkdir($dir . '/proc');
mkdir($dir . '/sys');
foreach (array('eth0', 'wlan0', 'tun0') as $interface) {
    mkdir($dir . '/sys/' . $interface);
    file_put_contents($dir . '/sys/' . $interface . '/type', $interface === 'tun0' ? '65534' : '1');
    file_put_contents($dir . '/sys/' . $interface . '/operstate', 'up');
    file_put_contents($dir . '/sys/' . $interface . '/carrier', '1');
}
mkdir($dir . '/sys/wlan0/wireless');
function check_route($ipv4, $ipv6, $expected) {
    global $dir;
    file_put_contents($dir . '/proc/route', $ipv4);
    file_put_contents($dir . '/proc/ipv6_route', $ipv6);
    $actual = bs_connection_status($dir . '/proc', $dir . '/sys');
    if ($actual !== $expected) throw new Exception(json_encode($actual));
}
function route4($interface, $metric, $flags = '0003') {
    return $interface . " 00000000 0101A8C0 " . $flags . " 0 0 " . $metric . " 00000000 0 0 0\n";
}
try {
    check_route(route4('wlan0', 600) . route4('eth0', 100), '', array('type'=>'ethernet', 'interface'=>'eth0'));
    check_route(route4('wlan0', 50) . route4('eth0', 100), '', array('type'=>'wifi', 'interface'=>'wlan0'));
    file_put_contents($dir . '/sys/eth0/carrier', '0');
    check_route(route4('eth0', 1) . route4('wlan0', 600), '', array('type'=>'wifi', 'interface'=>'wlan0'));
    check_route(route4('wlan0', 1, '0201'), '', array('type'=>'none', 'interface'=>''));
    check_route(route4('../../x', 1), '', array('type'=>'none', 'interface'=>''));
    check_route(route4('tun0', 1), '', array('type'=>'other', 'interface'=>'tun0'));
    $v6 = str_repeat('0', 32) . ' 00 ' . str_repeat('0', 32) . ' 00 ' . str_repeat('0', 32) . ' 00000258 00000000 00000000 00000003 wlan0';
    check_route('', $v6, array('type'=>'wifi', 'interface'=>'wlan0'));
    rmdir($dir . '/sys/wlan0/wireless');
    mkdir($dir . '/sys/phy0');
    symlink('../phy0', $dir . '/sys/wlan0/phy80211');
    check_route(route4('wlan0', 600), '', array('type'=>'wifi', 'interface'=>'wlan0'));
    check_route('', $v6, array('type'=>'wifi', 'interface'=>'wlan0'));
    check_route('', str_replace('00000003', '00200200', $v6), array('type'=>'none', 'interface'=>''));
    if (bs_connection_status($dir . '/missing', $dir . '/sys')['type'] !== 'unknown') throw new Exception('unreadable route');
    echo "PASS connection icon routing, Ethernet preference, Wi-Fi, missing/down links, IPv6 and unsafe names\n";
} finally {
    $it = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($dir, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST);
    foreach ($it as $file) { if ($file->isDir() && !$file->isLink()) rmdir($file->getPathname()); else unlink($file->getPathname()); }
    rmdir($dir);
}
