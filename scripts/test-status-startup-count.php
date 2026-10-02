<?php
/**
 * Devices table: StartupSuccessCount mapping and the status.html contract.
 * Chips stays the reported total. Missing/invalid startup stays null.
 * The plain Chips heading displays startup-success/reported total.
 */
define('BS_STATUS_LIB_ONLY', true);
require dirname(__DIR__) . '/overlay/var/www/f_status.php';

function fail_test($message) {
    fwrite(STDERR, "FAIL: " . $message . "\n");
    exit(1);
}

function expect($cond, $msg) {
    if (!$cond) fail_test($msg);
}

function expect_same($actual, $expected, $msg) {
    if ($actual !== $expected) {
        fail_test($msg . ' got ' . var_export($actual, true));
    }
}

function cgminer_normalize($raw) {
    $response = preg_replace('/[^[:alnum:][:punct:]]/', '', $raw);
    $decoded = json_decode($response, true);
    if (!is_array($decoded)) {
        fail_test('normalized API payload did not decode: ' . $response);
    }
    return $decoded;
}

expect_same(bs_status_startup_count(64), 64, 'int 64');
expect_same(bs_status_startup_count(56), 56, 'int 56');
expect_same(bs_status_startup_count(0), 0, 'int zero');
expect_same(bs_status_startup_count('0'), 0, 'string zero');
expect_same(bs_status_startup_count('64'), 64, 'string 64');
expect_same(bs_status_startup_count('56'), 56, 'string 56');
expect_same(bs_status_startup_count(null), null, 'null');
expect_same(bs_status_startup_count(-1), null, 'negative');
expect_same(bs_status_startup_count(1.5), null, 'float');
expect_same(bs_status_startup_count(56.0), null, 'whole float');
expect_same(bs_status_startup_count(true), null, 'true');
expect_same(bs_status_startup_count(false), null, 'false is not zero');
expect_same(bs_status_startup_count(''), null, 'empty string');
expect_same(bs_status_startup_count('n/a'), null, 'text');
expect_same(bs_status_startup_count('56.0'), null, 'decimal string');
expect_same(bs_status_startup_count('064'), null, 'leading zero');
expect_same(bs_status_startup_count(' 56'), null, 'padded string');
expect_same(bs_status_startup_count(array(56)), null, 'array');

$root = dirname(__DIR__);
$cgminer = file_get_contents($root . '/overlay/var/www/inc/cgminer.inc.php');
expect(is_string($cgminer), 'cgminer.inc.php readable');
expect(strpos($cgminer, "preg_replace('/[^[:alnum:][:punct:]]/','',\$response)") !== false,
    'cgminer still strips spaces before json_decode');

$live = cgminer_normalize("{\n\"STATS\":[\n"
    . "{\"ID\":\"BKLU0\",\"Chip Count\":64,\"StartupSuccessCount\":64},\n"
    . "{\"ID\":\"BKLU1\",\"Chip Count\":64,\"StartupSuccessCount\":64},\n"
    . "{\"ID\":\"BKLU2\",\"Chip Count\":64,\"StartupSuccessCount\":56}\n"
    . "]\n}\x00");
expect(isset($live['STATS'][0]['ChipCount']) && !array_key_exists('Chip Count', $live['STATS'][0]),
    'Chip Count key is ChipCount after the space strip');
$expected_live = array(
    array('StartupSuccessCount' => 64, 'Chips' => 64),
    array('StartupSuccessCount' => 64, 'Chips' => 64),
    array('StartupSuccessCount' => 56, 'Chips' => 64),
);
for ($i = 0; $i < 3; $i++) {
    expect_same(bs_status_chip_fields($live['STATS'][$i]), $expected_live[$i], 'live board ' . $i);
}

$spaced = cgminer_normalize('{"Chip Count":64,"Startup Success Count":56}');
expect_same(bs_status_chip_fields($spaced), array('StartupSuccessCount' => 56, 'Chips' => 64),
    'spaced startup key survives only through the stripper');

$zero = cgminer_normalize('{"Chip Count":64,"StartupSuccessCount":0}');
expect_same(bs_status_chip_fields($zero), array('StartupSuccessCount' => 0, 'Chips' => 64),
    'reported zero stays zero and does not change Chips');

$zero_total = cgminer_normalize('{"Chip Count":0,"StartupSuccessCount":0}');
expect_same(bs_status_chip_fields($zero_total), array('StartupSuccessCount' => 0, 'Chips' => 0),
    'zero total stays the Chips fallback value');

$missing = cgminer_normalize('{"Chip Count":64}');
expect(!array_key_exists('StartupSuccessCount', $missing), 'missing startup key is absent after decode');
expect_same(bs_status_chip_fields($missing), array('StartupSuccessCount' => null, 'Chips' => 64),
    'missing startup is null and is not copied from Chips');

$alias = array('ChipCount' => 64, 'AsicCountR' => 56);
expect_same(bs_status_chip_fields($alias), array('StartupSuccessCount' => null, 'Chips' => 64),
    'alias count is not startup success');

$invalids = array(-1, 1.5, true, false, '', 'n/a', '56.0', '064', null);
foreach ($invalids as $bad) {
    $stat = array('ChipCount' => 64, 'StartupSuccessCount' => $bad);
    expect_same(bs_status_chip_fields($stat), array('StartupSuccessCount' => null, 'Chips' => 64),
        'invalid startup ' . var_export($bad, true));
}

expect_same(bs_status_chip_fields(array('StartupSuccessCount' => 56)),
    array('StartupSuccessCount' => 56, 'Chips' => 0),
    'missing Chip Count still uses the Chips fallback and does not borrow startup');
expect_same(bs_status_chip_fields(array()),
    array('StartupSuccessCount' => null, 'Chips' => 0),
    'empty stat matches the offline placeholder contract');

$status_php = file_get_contents($root . '/overlay/var/www/f_status.php');
$html = file_get_contents($root . '/overlay/var/www/partials/status.html');
$controllers = file_get_contents($root . '/overlay/var/www/ng/controllers.js');
$css = file_get_contents($root . '/overlay/var/www/css/blakestream-dark.css');
expect(is_string($status_php) && is_string($html) && is_string($controllers) && is_string($css),
    'status sources readable');

expect(strpos($status_php, "\$chip_fields = bs_status_chip_fields(\$stat);") !== false, 'live row uses the mapper');
expect(strpos($status_php, "['Chips']=\$chip_fields['Chips'];") !== false, 'Chips assignment preserved from mapper');
expect(strpos($status_php, "['StartupSuccessCount']=\$chip_fields['StartupSuccessCount'];") !== false,
    'startup field assigned from mapper');
expect(strpos($status_php, "isset(\$stat['ChipCount']) ? \$stat['ChipCount'] : 0") !== false,
    'Chips still falls back to 0');
expect(strpos($status_php, "'Chips'          => 0,") !== false, 'offline placeholder keeps Chips 0');
expect(strpos($status_php, "'StartupSuccessCount' => null,") !== false, 'offline startup is null');
expect(strpos($status_php, 'EstimatedHashrate') !== false, 'estimate field untouched');
expect(strpos($status_php, "['MHS5s'] = 0;") !== false, 'idle hashrate zeroing untouched');

$chip_header = strpos($html, '<th>Chips</th>');
expect($chip_header !== false, 'plain Chips heading without tooltip or control');
expect(strpos($html, '<th>CHIPS</th>') === false, 'uppercase heading removed');
expect(strpos($html, 'Startup / Total') === false, 'header is not a ratio');
expect(strpos($html, 'data-tip="Bridge startup-success count; not a continuous working-chip measurement."') === false,
    'startup tooltip removed');
expect(strpos($html, '&mdash;') !== false, 'missing value uses the em dash entity');

$cell = strpos($html, 'class="bs-chip-cell"');
$next_row = strpos($html, '{{bsDeviceClockText(d)}}', $cell);
expect($cell !== false && $next_row !== false && $next_row > $cell, 'clock cell still follows chips');
$cell_html = substr($html, $cell, $next_row - $cell);
expect(strpos($cell_html, 'd.StartupSuccessCount!=null') !== false, 'zero stays visible');
expect(strpos($cell_html, 'd.StartupSuccessCount==null') !== false, 'null uses the empty branch');
expect(strpos($cell_html, '{{d.StartupSuccessCount}}') !== false, 'cell renders startup count');
expect(strpos($cell_html, 'd.Chips!=null') !== false, 'total zero stays visible');
expect(strpos($cell_html, 'd.Chips==null') !== false, 'missing total remains unknown');
expect(strpos($cell_html, '{{d.Chips}}') !== false, 'reported total is displayed separately');
expect(strpos($cell_html, '</span>/<span') !== false, 'startup/total separator without added spaces');
expect(strpos($cell_html, '&mdash;') !== false, 'chip cell contains the em dash entity');
expect(strpos($cell_html, 'Startup / Total') === false, 'chip cell is not a ratio header');

expect(strpos($controllers, 'bsChipCountView') === false, 'old saved chip mode is ignored');
expect(strpos($controllers, 'bsToggleChipCountView') === false, 'toggle handler removed');
expect(strpos($controllers, 'bsChipCountTip') === false, 'chip tooltip handler removed');
expect(strpos($html, 'bsChipCount') === false, 'chip mode bindings removed');
expect(strpos($html, 'bsToggleChipCountView') === false, 'toggle control removed');
expect(strpos($css, '.bs-chip-toggle') === false, 'unused toggle styling removed');
expect(preg_match('/#statusDevs\s*>\s*thead\s*>\s*tr\s*>\s*th\s*\{\s*text-align:\s*center;\s*\}/', $css) === 1,
    'Devices headings centered without changing numeric row alignment');

expect(strpos($html, '<th>Estimate</th>') !== false,
    'plain estimate header unchanged');
expect(strpos($html, '{{d.EstimatedHashrate|mhs}}h/s') !== false, 'estimate cell uses source-selected rate');
expect(strpos($html, 'data-tip="Source: {{d.EstimateSource') === false,
    'estimate values have no explanatory tooltip');
expect(strpos($html, 'colspan="14"') !== false, 'disabled row colspan unchanged');
expect(substr_count($html, '<th>Chips</th>') === 1, 'one Chips header');

printf("PASS status startup count 64/64/56, zero, missing, invalid, template\n");
