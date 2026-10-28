<?php
require dirname(__DIR__) . '/overlay/var/www/inc/sgminer-api-policy.php';

function api_expect($condition, $message) {
    if (!$condition) { fwrite(STDERR, 'FAIL: ' . $message . "\n"); exit(1); }
}

$allowed = array('summary', 'stats', 'coin', 'pools', 'devs', 'devdetails',
                 'config', 'asccount', 'notify', 'version');
foreach ($allowed as $command) {
    $result = bs_dev_sgminer_api_request(array('command' => $command));
    api_expect($result['ok'] === true && $result['command'] === $command &&
               $result['parameter'] === '', 'allow read command ' . $command);
    api_expect(bs_dev_sgminer_api_request(array('command' => $command,
        'parameter' => ''))['ok'] === true, 'empty compatibility parameter ' . $command);
}

foreach (array('save', 'addpool', 'removepool', 'restart', 'quit', 'ascenable',
               'ascdisable', 'ascidentify', 'enablepool', 'disablepool',
               'switchpool', 'poolpriority', 'quota', 'check', 'lockstats',
               'SUMMARY') as $command) {
    api_expect(bs_dev_sgminer_api_request(array('command' => $command))['ok'] === false,
               'reject non-read command ' . $command);
}

foreach (array('/etc/cron.d/pwn', '../miner-start.sh', '0', '0,2,1', array(), 1) as $parameter) {
    api_expect(bs_dev_sgminer_api_request(array('command' => 'summary',
        'parameter' => $parameter))['ok'] === false,
        'reject parameter ' . json_encode($parameter));
}

api_expect(bs_dev_sgminer_api_request(array('command' => 'summary', 'extra' => true))['ok'] === false,
           'reject extra fields');
api_expect(bs_dev_sgminer_api_request(array('summary'))['ok'] === false, 'reject list body');
api_expect(bs_dev_sgminer_api_request('summary')['ok'] === false, 'reject scalar body');

$source = file_get_contents(dirname(__DIR__) . '/overlay/var/www/f_dev.php');
$start = strpos($source, "case 'sgminer_api':");
$end = strpos($source, "case 'recorder_status':", $start);
$case = substr($source, $start, $end - $start);
api_expect($start !== false && $end > $start, 'sgminer dispatch exists');
api_expect(strpos($case, 'bs_dev_sgminer_api_request') !== false, 'dispatch applies policy before socket');
api_expect(strpos($case, 'firmware_audit') === false, 'dispatch has no mutating command branch');
api_expect(strpos($case, "['parameter']") === false, 'dispatch never forwards a parameter');

$controller = file_get_contents(dirname(__DIR__) . '/overlay/var/www/ng/controllers.js');
$template = file_get_contents(dirname(__DIR__) . '/overlay/var/www/partials/dev-tools.html');
foreach (array('(other)', 'customCommand', 'devApiDisruptive', "command: 'restart'",
               "'ascenable'", "'ascdisable'") as $unsafe) {
    api_expect(strpos($controller, $unsafe) === false, 'controller omits ' . $unsafe);
}
api_expect(strpos($template, 'custom commands, parameters, pool changes') !== false,
           'template explains read-only policy');
api_expect(strpos($template, 'devBoardSet(') === false && strpos($template, 'devMinerRestart()') === false,
           'template has no mutating board controls');

echo "PASS read-only sgminer API policy and UI\n";
?>
