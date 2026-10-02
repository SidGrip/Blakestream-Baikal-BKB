<?php
/* PHP 5.6-compatible checks for experimental Index option validation.
 * Defines BKB_SETTINGS_LIB_ONLY before loading f_settings.php so the web
 * request path, session, and process launches do not run. */
define('BKB_SETTINGS_LIB_ONLY', true);
require dirname(__DIR__) . '/overlay/var/www/f_settings.php';

function expect_clock_index($condition, $message) {
    if (!$condition) {
        fwrite(STDERR, "FAIL: " . $message . "\n");
        exit(1);
    }
}

function clock_index_option($key, $value) {
    return array('key' => $key, 'value' => $value);
}

function experimental_options($flag, $clock_index, $mode) {
    $options = array();
    if ($mode !== null)
        $options[] = clock_index_option('baikal-clock-mode', $mode);
    if ($flag !== null)
        $options[] = clock_index_option('baikal-experimental-clock-index', $flag);
    if ($clock_index !== null)
        $options[] = clock_index_option('baikal-board-clock-index', $clock_index);
    $options[] = clock_index_option('baikal-options', '400:40:55');
    $options[] = clock_index_option('no-submit-stale', 'false');
    return $options;
}

function has_experimental_key($options) {
    if (!is_array($options)) return false;
    foreach ($options as $option) {
        if (!is_array($option) || !isset($option['key'])) continue;
        if ($option['key'] === 'baikal-experimental-clock-index' || $option['key'] === 'baikal-board-clock-index')
            return true;
    }
    return false;
}

function with_scope($options, $scope) {
    $options[] = clock_index_option('blakestream-clock-scope', $scope);
    return $options;
}

function find_scope($options) {
    $found = null;
    $count = 0;
    if (!is_array($options)) return array(0, null);
    foreach ($options as $option) {
        if (!is_array($option) || !isset($option['key']) || $option['key'] !== 'blakestream-clock-scope')
            continue;
        $count++;
        $found = $option;
    }
    return array($count, $found);
}

function saved_clock_options($options) {
    $boardError = validateBaikalBoardClocks($options);
    if ($boardError !== null) return array('error' => $boardError);
    $experimentalError = baikalExperimentalClockError($options);
    if ($experimentalError !== null) return array('error' => $experimentalError);
    return array('saved' => baikalNormalizeExperimentalOptions(ensureBaikalOptions($options)));
}

expect_clock_index(baikalExperimentalClockError(array(
    clock_index_option('baikal-options', '400:40:55'),
)) === null, 'absent experimental keys are valid');

$off = baikalNormalizeExperimentalOptions(experimental_options(false, null, 'global'));
expect_clock_index(!has_experimental_key($off), 'explicit false omits both keys');

expect_clock_index(baikalExperimentalClockError(experimental_options('false', null, 'individual')) === null,
    'string false is explicit off');
expect_clock_index(baikalExperimentalClockError(experimental_options(false, '29:30:30', 'individual')) !== null,
    'clock_index without an enabled flag conflicts');
expect_clock_index(baikalExperimentalClockError(experimental_options(true, null, 'individual')) !== null,
    'flag requires clock_index');
expect_clock_index(baikalExperimentalClockError(experimental_options(true, '29:30:30', 'global')) !== null,
    'global mode is rejected');
expect_clock_index(baikalExperimentalClockError(experimental_options(true, '29:30:30', null)) !== null,
    'missing mode is not inferred');
expect_clock_index(baikalExperimentalClockError(array(
    clock_index_option('baikal-board-clocks', '400:400:400'),
    clock_index_option('baikal-experimental-clock-index', true),
    clock_index_option('baikal-board-clock-index', '29:30:30'),
)) !== null, 'legacy board clocks do not imply individual mode');

foreach (array(1, 1.0, '', 'TRUE', 'yes', null, array()) as $flag) {
    expect_clock_index(baikalExperimentalClockError(experimental_options($flag, '29:30:30', 'individual')) !== null,
        'rejected flag type');
}
foreach (array(true, 29, 29.0, '', '08:30:30', '29:30:30 ', ' 29:30:30', '+29:30:30',
               '29.0:30:30', '29:30', '29:30:30:31', '7:30:30', '33:30:30', '029:30:30',
               '8:09:32', "29:30:30\n", "29:30:30\r\n", null) as $clock_index) {
    expect_clock_index(baikalExperimentalClockError(experimental_options(true, $clock_index, 'individual')) !== null,
        'rejected clock_index value');
}
expect_clock_index(baikalExperimentalClockError(experimental_options('true', '8:9:32', 'individual')) === null,
    'string true and edge indices are structurally valid');
expect_clock_index(baikalExperimentalClockError(experimental_options(true, '29:30:30', 'individual')) === null,
    'boolean true is structurally valid');

$duplicate = experimental_options(true, '29:30:30', 'individual');
$duplicate[] = clock_index_option('baikal-board-clock-index', '8:9:32');
expect_clock_index(baikalExperimentalClockError($duplicate) !== null, 'duplicate clock_index is rejected');
$twoModes = experimental_options(true, '29:30:30', 'individual');
$twoModes[] = clock_index_option('baikal-clock-mode', 'individual');
expect_clock_index(baikalExperimentalClockError($twoModes) !== null, 'duplicate mode is not explicit');

$ensured = ensureBaikalOptions(array(clock_index_option('baikal-options', '400:40:55')));
list($scopeCount, $scopeOption) = find_scope($ensured);
expect_clock_index($scopeCount === 0, 'reading options does not invent scope');

$legacySaved = saved_clock_options(experimental_options(true, '8:30:32', 'individual'));
expect_clock_index(!isset($legacySaved['error']), 'legacy experimental save without scope');
list($scopeCount, $scopeOption) = find_scope($legacySaved['saved']);
expect_clock_index($scopeCount === 0, 'legacy save does not add scope');
foreach ($legacySaved['saved'] as $option) {
    if ($option['key'] === 'baikal-options')
        expect_clock_index($option['value'] === '400:40:55', 'normal 400 label is unchanged');
}

$globalEqual = saved_clock_options(with_scope(experimental_options('true', '29:29:29', 'individual'), 'global'));
expect_clock_index(!isset($globalEqual['error']), 'global scope with equal indices saves');
list($scopeCount, $scopeOption) = find_scope($globalEqual['saved']);
expect_clock_index($scopeCount === 1 && $scopeOption['value'] === 'global', 'global scope preserved');
expect_clock_index($scopeOption['description'] === 'Clock control scope (presentation only)',
    'scope description is presentation metadata');
expect_clock_index(strpos($scopeOption['description'], 'MHz') === false, 'scope metadata is not a frequency');
$sawPll = false;
foreach ($globalEqual['saved'] as $option) {
    if ($option['key'] === 'baikal-board-clock-index') {
        expect_clock_index($option['value'] === '29:29:29', 'global UI stores three identical indices');
        $sawPll = true;
    }
    if ($option['key'] === 'baikal-clock-mode')
        expect_clock_index($option['value'] === 'individual', 'global UI keeps actual individual mode');
    if ($option['key'] === 'baikal-experimental-clock-index')
        expect_clock_index($option['value'] === true, 'string true still canonicalizes beside scope');
}
expect_clock_index($sawPll, 'equal indices retained');

$individualUnequal = saved_clock_options(with_scope(experimental_options(true, '8:30:32', 'individual'), 'individual'));
expect_clock_index(!isset($individualUnequal['error']), 'individual scope allows unequal indices');
list($scopeCount, $scopeOption) = find_scope($individualUnequal['saved']);
expect_clock_index($scopeCount === 1 && $scopeOption['value'] === 'individual', 'individual scope preserved');

$individualEqual = saved_clock_options(with_scope(experimental_options(true, '8:8:8', 'individual'), 'individual'));
expect_clock_index(!isset($individualEqual['error']), 'individual scope allows equal indices');

$normalScope = saved_clock_options(with_scope(experimental_options(false, null, 'global'), 'global'));
expect_clock_index(!isset($normalScope['error']), 'normal global scope is optional metadata');
expect_clock_index(!has_experimental_key($normalScope['saved']), 'explicit false still omits experimental keys');
list($scopeCount, $scopeOption) = find_scope($normalScope['saved']);
expect_clock_index($scopeCount === 1 && $scopeOption['value'] === 'global', 'normal scope is preserved');
foreach ($normalScope['saved'] as $option) {
    if ($option['key'] === 'baikal-options')
        expect_clock_index($option['value'] === '400:40:55', 'normal 400 stays 400 beside scope');
}

$scopeOnly = saved_clock_options(array(
    clock_index_option('baikal-options', '400:40:55'),
    clock_index_option('baikal-clock-mode', 'individual'),
    clock_index_option('blakestream-clock-scope', 'individual'),
));
expect_clock_index(!isset($scopeOnly['error']), 'scope without experimental keys is valid');
list($scopeCount, $scopeOption) = find_scope($scopeOnly['saved']);
expect_clock_index($scopeCount === 1 && $scopeOption['value'] === 'individual', 'scope-only save preserved');

$conflict = with_scope(experimental_options(true, '8:30:32', 'individual'), 'global');
expect_clock_index(baikalExperimentalClockError($conflict) ===
    'blakestream-clock-scope global requires three equal clock indices',
    'global scope rejects unequal indices');
expect_clock_index(isset(saved_clock_options($conflict)['error']), 'conflicting scope is not saved');

$bypass = with_scope(experimental_options(true, '30:30:30', 'global'), 'global');
expect_clock_index(baikalExperimentalClockError($bypass) ===
    'baikal-experimental-clock-index requires explicit baikal-clock-mode individual',
    'scope does not authorize global actual mode');
expect_clock_index(baikalBuildMinerConfig($bypass) === false, 'global actual mode does not build a runtime config');

$duplicateScope = with_scope(experimental_options(false, null, 'global'), 'global');
$duplicateScope[] = clock_index_option('blakestream-clock-scope', 'individual');
expect_clock_index(baikalExperimentalClockError($duplicateScope) === 'blakestream-clock-scope must appear once',
    'duplicate scope is rejected');
expect_clock_index(baikalExperimentalClockError(array(array('key' => 'blakestream-clock-scope'))) ===
    'blakestream-clock-scope must be global or individual', 'scope without a value is rejected');
foreach (array(true, false, 0, 1, 30, 400, 480, null, '', 'Global', 'GLOBAL', 'global ',
               ' global', "individual\n", 'both', '400', '480', '30', array()) as $badScope) {
    expect_clock_index(baikalExperimentalClockError(with_scope(
        experimental_options(false, null, 'individual'), $badScope)) ===
        'blakestream-clock-scope must be global or individual', 'rejected scope type or value');
    expect_clock_index(baikalExperimentalClockError(with_scope(
        experimental_options(true, '29:29:29', 'individual'), $badScope)) !== null,
        'rejected scope cannot enable experimental Index');
}

$saved = baikalNormalizeExperimentalOptions(experimental_options('true', '8:30:32', 'individual'));
$sawFlag = false;
$sawPll = false;
foreach ($saved as $option) {
    if ($option['key'] === 'baikal-experimental-clock-index') {
        expect_clock_index($option['value'] === true, 'saved flag is boolean true');
        $sawFlag = true;
    }
    if ($option['key'] === 'baikal-board-clock-index') {
        expect_clock_index($option['value'] === '8:30:32', 'saved clock_index is canonical');
        $sawPll = true;
    }
}
expect_clock_index($sawFlag && $sawPll, 'enabled options are retained canonically');

$blocked = baikalBuildMinerConfig(experimental_options(true, '29:30:30', 'individual'));
expect_clock_index(is_array($blocked), 'unverified enable still builds a normal config');
expect_clock_index(!array_key_exists('baikal-experimental-clock-index', $blocked), 'unverified flag omitted');
expect_clock_index(!array_key_exists('baikal-board-clock-index', $blocked), 'unverified clock_index omitted');
expect_clock_index($blocked['baikal-options'] === '400:40:55', 'public clock label preserved');
expect_clock_index($blocked['no-submit-stale'] === 'false', 'string false is not coerced');
expect_clock_index(baikalExperimentalPolicyError(experimental_options(true, '29:30:30', 'individual')) !== null,
    'web enable is rejected without verified firmware');

$invalid = baikalBuildMinerConfig(experimental_options(true, '7:30:30', 'individual'));
expect_clock_index($invalid === false, 'malformed config does not produce miner.conf entries');

$root = sys_get_temp_dir() . '/bkb-exp-clock-index-php-' . getmypid();
expect_clock_index(mkdir($root), 'temp dir');
$identity = $root . '/identity.json';
$clocks = $root . '/scripta.conf';
$GLOBALS['BKB_FIRMWARE_IDENTITY_PATHS'] = array($identity);
$GLOBALS['BKB_SCRIPTA_CONF_PATH'] = $clocks;

file_put_contents($identity, json_encode(array(
    'identity' => 'STOCK',
    'chip_md5' => 'd5f844be2952fd713717a71a67e2be61',
)));
file_put_contents($clocks, json_encode(array('userAdjustableClocks' => true)));
$stockError = baikalExperimentalPolicyError(experimental_options(true, '29:30:30', 'individual'));
expect_clock_index(is_string($stockError) && strpos($stockError, 'verified custom') !== false,
    'stock firmware error names V1');

file_put_contents($identity, json_encode(array(
    'identity' => 'CUSTOM V1',
    'chip_md5' => '0E8467EDDABF589AD94549A8D572C759',
)));
file_put_contents($clocks, json_encode(array('userAdjustableClocks' => false)));
$clockError = baikalExperimentalPolicyError(experimental_options(true, '29:30:30', 'individual'));
expect_clock_index(is_string($clockError) && strpos($clockError, 'Adjustable Clocks') !== false,
    'disabled clocks are a separate error');

file_put_contents($clocks, json_encode(array('userAdjustableClocks' => true)));
expect_clock_index(baikalExperimentalPolicyError(experimental_options(true, '29:30:30', 'individual')) === null,
    'verified V1 and adjustable clocks allow enable');
$built = baikalBuildMinerConfig(experimental_options('true', '29:30:30', 'individual'));
expect_clock_index(is_array($built), 'allowed config builds');
expect_clock_index($built['baikal-experimental-clock-index'] === true, 'miner.conf flag is JSON true');
expect_clock_index($built['baikal-board-clock-index'] === '29:30:30', 'miner.conf carries the indices');
expect_clock_index($built['baikal-clock-mode'] === 'individual', 'individual mode preserved');

$globalBuilt = baikalBuildMinerConfig(with_scope(
    experimental_options('true', '29:29:29', 'individual'), 'global'));
expect_clock_index(is_array($globalBuilt), 'global presentation scope still builds');
expect_clock_index(!array_key_exists('blakestream-clock-scope', $globalBuilt), 'PHP runtime strips global scope');
expect_clock_index($globalBuilt['baikal-clock-mode'] === 'individual', 'runtime mode stays individual');
expect_clock_index($globalBuilt['baikal-board-clock-index'] === '29:29:29', 'runtime keeps equal indices');
expect_clock_index($globalBuilt['baikal-experimental-clock-index'] === true, 'runtime flag stays boolean true');
expect_clock_index($globalBuilt['baikal-options'] === '400:40:55', 'runtime keeps the 400 label');

$individualBuilt = baikalBuildMinerConfig(with_scope(
    experimental_options(true, '8:30:32', 'individual'), 'individual'));
expect_clock_index(is_array($individualBuilt), 'individual scope builds unequal indices');
expect_clock_index(!array_key_exists('blakestream-clock-scope', $individualBuilt), 'PHP runtime strips individual scope');
expect_clock_index($individualBuilt['baikal-board-clock-index'] === '8:30:32', 'unequal indices reach runtime');
expect_clock_index($individualBuilt['no-submit-stale'] === 'false', 'scope does not coerce unrelated strings');

$normalBuilt = baikalBuildMinerConfig(with_scope(
    experimental_options(false, null, 'global'), 'global'));
expect_clock_index(is_array($normalBuilt), 'normal scope still builds');
expect_clock_index(!array_key_exists('blakestream-clock-scope', $normalBuilt), 'normal scope does not reach runtime');
expect_clock_index(!array_key_exists('baikal-experimental-clock-index', $normalBuilt), 'normal build has no experimental flag');
expect_clock_index($normalBuilt['baikal-clock-mode'] === 'global', 'normal global mode preserved');
expect_clock_index($normalBuilt['baikal-options'] === '400:40:55', 'normal 400 preserved in runtime');
$newerIdentity = $root . '/newer-identity.json';
file_put_contents($newerIdentity, json_encode(array('identity' => 'STOCK', 'chip_md5' => 'd5f844be2952fd713717a71a67e2be61')));
$GLOBALS['BKB_FIRMWARE_IDENTITY_PATHS'] = array($newerIdentity, $identity);
expect_clock_index(!firmware_custom_active(), 'current stock identity beats stale custom fallback');
unlink($newerIdentity);
expect_clock_index(firmware_custom_active(), 'missing RAM identity allows valid SD fallback');
$GLOBALS['BKB_FIRMWARE_IDENTITY_PATHS'] = array($identity);

$stripped = baikalStripExperimentalOptions($saved);
expect_clock_index(!has_experimental_key($stripped), 'strip removes both keys');
$optionsPath = $root . '/miner.options.json';
file_put_contents($optionsPath, json_encode($saved));
chmod($optionsPath, 0640);
expect_clock_index(bkb_clear_experimental_options($optionsPath), 'firmware transition clears saved override');
expect_clock_index(json_decode(file_get_contents($optionsPath), true) === $stripped,
    'firmware transition preserves every unrelated option');
expect_clock_index((fileperms($optionsPath) & 0777) === 0640, 'option reset preserves mode');
expect_clock_index(bkb_clear_experimental_options($optionsPath), 'reset is idempotent');
file_put_contents($optionsPath, 'invalid-json');
expect_clock_index(!bkb_clear_experimental_options($optionsPath), 'malformed options fail closed');
expect_clock_index(file_get_contents($optionsPath) === 'invalid-json', 'malformed options not overwritten');
$scoped = saved_clock_options(with_scope(experimental_options(true, '8:8:8', 'individual'), 'global'));
$scoped['saved'][] = clock_index_option('fixture-unrelated', 'preserved');
file_put_contents($optionsPath, json_encode($scoped['saved']));
chmod($optionsPath, 0640);
expect_clock_index(bkb_clear_experimental_options($optionsPath), 'firmware reset clears scope with experimental keys');
$cleared = json_decode(file_get_contents($optionsPath), true);
list($scopeCount, $scopeOption) = find_scope($cleared);
expect_clock_index($scopeCount === 0 && !has_experimental_key($cleared), 'reset removes scope and experimental keys');
$sawUnrelated = false;
$sawMode = false;
$sawLabel = false;
foreach ($cleared as $option) {
    if ($option['key'] === 'fixture-unrelated') {
        expect_clock_index($option['value'] === 'preserved', 'reset keeps unrelated options');
        $sawUnrelated = true;
    }
    if ($option['key'] === 'baikal-clock-mode') {
        expect_clock_index($option['value'] === 'individual', 'reset keeps the actual clock mode');
        $sawMode = true;
    }
    if ($option['key'] === 'baikal-options') {
        expect_clock_index($option['value'] === '400:40:55', 'reset keeps the 400 label');
        $sawLabel = true;
    }
}
expect_clock_index($sawUnrelated && $sawMode && $sawLabel, 'reset preserves non-experimental options');
expect_clock_index((fileperms($optionsPath) & 0777) === 0640, 'scope reset preserves mode');
expect_clock_index(bkb_clear_experimental_options($optionsPath), 'scope reset is idempotent');
$staleScope = array(
    clock_index_option('baikal-options', '400:40:55'),
    clock_index_option('baikal-clock-mode', 'global'),
    clock_index_option('blakestream-clock-scope', 'global'),
);
expect_clock_index(baikalStripExperimentalOptions($staleScope) === array(
    clock_index_option('baikal-options', '400:40:55'),
    clock_index_option('baikal-clock-mode', 'global'),
), 'strip removes a stale scope without rewriting other values');
unlink($optionsPath);
expect_clock_index(!bkb_clear_experimental_options($optionsPath), 'missing options fail closed');
$devSource = file_get_contents(dirname(__DIR__) . '/overlay/var/www/f_dev.php');
expect_clock_index(substr_count($devSource, "bkb_clear_experimental_options('/opt/scripta/etc/miner.options.json')") === 4,
    'verify, successful flashes and failed flash jobs clear saved experimental options');
expect_clock_index(baikalExperimentalPolicyError(experimental_options(false, null, 'global')) === null,
    'explicit off is not an enable attempt');

unlink($identity);
unlink($clocks);
rmdir($root);
echo "Experimental Index settings validation PASS\n";
