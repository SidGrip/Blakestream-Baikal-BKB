<?php
require dirname(__DIR__) . '/overlay/var/www/inc/network-probe.php';
function probe_expect($condition, $message) {
    if (!$condition) { fwrite(STDERR, 'FAIL: ' . $message . "\n"); exit(1); }
}
function pool_fixture($name, $url) { return array('name' => $name, 'url' => $url); }
$saved = array('categories' => array(array('pools' => array(
    pool_fixture('AT1', 'stratum+tcp://WWW.AT1.BLAKECOIN.COM:3334'),
    pool_fixture('AT1 copy', 'www.at1.blakecoin.com.:3334'),
    pool_fixture('AT1 copy 2', 'http://www.at1.blakecoin.com:03334/path'),
    pool_fixture('Other port', 'www.at1.blakecoin.com:3335'),
    pool_fixture('IPv4', '192.0.2.1:3334'),
    pool_fixture('IPv4 again', 'quota:2;192.0.2.1:3334'),
    pool_fixture('IPv6', '[2001:0DB8:0000:0000:0000:0000:0000:0001]:3334'),
    pool_fixture('IPv6 again', 'stratum+tcp://[2001:db8::1]:3334'),
    pool_fixture('Invalid', 'host:bad')
)), array('pools' => array(pool_fixture('AT1', 'www.at1.blakecoin.com:3334')))));
$endpoints = bs_probe_endpoints_from_saved($saved);
probe_expect(count($endpoints) === 4, 'one endpoint per canonical host/port across categories');
probe_expect($endpoints[0]['names'] === array('AT1', 'AT1 copy', 'AT1 copy 2'), 'all distinct names grouped');
probe_expect($endpoints[1]['port'] === 3335, 'different port remains separate');
probe_expect($endpoints[3]['host'] === '2001:db8::1', 'native IPv6 normalization');
probe_expect(bs_probe_select($endpoints, '0') === $endpoints[0], 'numeric selector supported');
probe_expect(bs_probe_select(array_reverse($endpoints), $endpoints[0]['key']) === $endpoints[0], 'key survives reorder');
probe_expect(bs_probe_select($endpoints, '0bad') === null, 'invalid selector is not coerced to zero');
probe_expect(bs_probe_select($endpoints, array()) === null, 'array selector rejected');
probe_expect(bs_probe_select($endpoints, 'v1|gone.example|3334') === null, 'deleted endpoint fails closed');
probe_expect(bs_probe_connect_host('2001:db8::1') === 'tcp://[2001:db8::1]', 'IPv6 socket form');
foreach (array('', 'x', 'host:0', 'host:65536', 'host:12x', 'host:-1', "host:3334\nmore",
    'user:password@host:3334', 'stratum+tcp://host:3334\x', 'host..:3334', '192.168.010.001:3334',
    '127.1:3334', '0x7f000001:3334', '0x7f.0.0.1:3334', '256.0.0.1:3334',
    '2001:db8::1:3334', '[2001:::1]:3334', '[192.0.2.1::]:3334',
    '[fe80::1%eth0]:3334', '[::1]:3334x', 'unix:///tmp/socket', array(), null) as $invalid) {
    probe_expect(bs_probe_parse_endpoint($invalid) === null, 'invalid endpoint ' . json_encode($invalid));
}
probe_expect(bs_probe_parse_endpoint('stratum+ssl://pool.example:443')['port'] === 443, 'TLS endpoint still has TCP reachability');
// Exercise both dispatch paths with stubbed socket work, without a miner/network.
$source = file_get_contents(dirname(__DIR__) . '/overlay/var/www/f_dev.php');
$start = strpos($source, "case 'network_probe':");
$end = strpos($source, "case 'sgminer_api':", $start);
probe_expect($start !== false && $end > $start, 'dispatch extraction');
function read_json($path) { return $GLOBALS['saved']; }
function pool_list_for_probe($etc) { return bs_probe_endpoints_from_saved(read_json('')); }
function probe_one_pool($p) { $GLOBALS['probed'][] = $p['key']; $p['probe_count'] = count($GLOBALS['probed']); return $p; }
$ETC = '';
$GLOBALS['probed'] = array();
$_GET = array();
if (isset($argv[1]) && $argv[1] === '--single') $_GET['endpoint'] = $argv[2];
if (isset($argv[1]) && $argv[1] === '--numeric') $_GET['pool'] = $argv[2];
ob_start();
eval("switch ('network_probe') {" . substr($source, $start, $end - $start) . '}');
$response = json_decode(ob_get_clean(), true);
probe_expect($response['ok'] === true && count($GLOBALS['probed']) === 4, 'all path probes each endpoint once');
probe_expect(count(array_unique($GLOBALS['probed'])) === 4, 'no duplicate all-path calls');
$command = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__);
foreach (array('--single' => $endpoints[0]['key'], '--numeric' => '0') as $flag => $selector) {
    $one = json_decode(shell_exec($command . ' ' . $flag . ' ' . escapeshellarg($selector)), true);
    probe_expect($one['ok'] && $one['result']['key'] === $endpoints[0]['key'] && $one['result']['probe_count'] === 1,
        'single dispatch performs one probe');
}
$bad = json_decode(shell_exec($command . ' --single ' . escapeshellarg('v1|gone.example|3334')), true);
probe_expect(!$bad['ok'], 'removed endpoint does not probe a different row');
echo "PASS network endpoint grouping, parsing, stable keys and all-probe dispatch\n";
