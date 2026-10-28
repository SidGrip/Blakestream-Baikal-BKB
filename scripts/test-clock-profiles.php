<?php
require dirname(__DIR__) . '/overlay/var/www/inc/experimental-clock.php';

// Load only the pure normalizer; no session, API requests or filesystem writes.
$source = file_get_contents(dirname(__DIR__) . '/overlay/var/www/f_blakestream.php');
$start = strpos($source, 'function clamp_int(');
$end = strpos($source, '// Actions are selected only by the query key', $start);
if ($start === false || $end === false) exit(1);
eval(substr($source, $start, $end - $start));
$labelStart = strpos($source, 'function saved_label_valid(');
$labelEnd = strpos($source, 'function bk_sgminer_api(', $labelStart);
if ($labelStart === false || $labelEnd === false) exit(1);
eval(substr($source, $labelStart, $labelEnd - $labelStart));
function profile_expect($ok, $message) {
    if (!$ok) { fwrite(STDERR, 'FAIL: ' . $message . "\n"); exit(1); }
}
$profile = array('id' => 'trial', 'version' => 2, 'mapping' => 'bkb-v1-clock-index-1',
    'name' => 'Trial', 'experimental' => true, 'mode' => 'individual', 'clock_indices' => array(8, 30, 32));
foreach (array('<img src=x>', '{{7*7}}', "bad\nname") as $badName) {
    $badProfile = $profile; $badProfile['name'] = $badName;
    profile_expect(bkb_clock_profile_error($badProfile) !== null, 'active markup profile name rejected');
}
profile_expect(saved_pools_error(array('categories' => array(array(
    'id' => 'cat-1', 'name' => 'SAFE & SOUND', 'pools' => array())))) === null, 'normal category accepted');
foreach (array('<img src=x>', '{{7*7}}', "bad\nname") as $badName) {
    profile_expect(saved_pools_error(array('categories' => array(array(
        'id' => 'cat-1', 'name' => $badName, 'pools' => array())))) !== null,
        'active markup category name rejected');
}
profile_expect(strpos($source, '$saved_pools_error = saved_pools_error($saved_pools);') !== false,
    'restored saved pools are revalidated on load');
profile_expect(strpos($source, "'categories' => array(),") !== false,
    'invalid restored pools fail closed without labels');
$clean = normalize_clock_profiles(array('profiles' => array($profile)));
profile_expect(!isset($clean['error']), 'valid profile');
profile_expect($clean['profiles'][0]['clock_indices'] === array(8, 30, 32), 'raw indices retained');
profile_expect($clean['profiles'][0]['experimental'] === true, 'mode retained');
profile_expect($clean['profiles'][0]['mode'] === 'individual', 'experimental actual mode stays individual');
profile_expect($clean['profiles'][0]['scope'] === 'individual', 'legacy experimental scope follows actual mode');
profile_expect($clean['profiles'][0]['version'] === 2, 'version stays 2');
profile_expect($clean['profiles'][0]['mapping'] === 'bkb-v1-clock-index-1', 'mapping stays bkb-v1-clock-index-1');
profile_expect(normalize_clock_profiles($clean) === $clean, 'round trip idempotent');
foreach (array(null, false, 8.5, '08', '30x', "30\n", 7, 33, array()) as $bad) {
    $p = $profile;
    $p['clock_indices'][0] = $bad;
    profile_expect(isset(normalize_clock_profiles(array('profiles' => array($p)))['error']), 'bad index rejected');
}
foreach (array('mapping' => 'future', 'version' => 3, 'experimental' => 'true', 'mode' => 'global',
              'clock_indices' => array(30, 30)) as $key => $bad) {
    $p = $profile; $p[$key] = $bad;
    profile_expect(bkb_clock_profile_error($p) !== null, 'bad ' . $key);
}
$p = $profile; unset($p['mapping']);
profile_expect(bkb_clock_profile_error($p) !== null, 'experimental mapping required');
$p = $profile; $p['experimental'] = false;
profile_expect(bkb_clock_profile_error($p) !== null, 'normal cannot carry non-30 indices');
$legacy = normalize_clock_profiles(array('profiles' => array(array('id' => 'old', 'clock' => 400))));
foreach (array(0 => 20, 20 => 20, 30 => 30, 40 => 40, 101 => 100) as $minimum => $expected) {
    $auto = normalize_clock_profiles(array('profiles' => array(array('id'=>'fan', 'fan_min'=>$minimum))));
    profile_expect($auto['profiles'][0]['fan_min'] === $expected, 'auto profile floor independent of manual fan');
}
profile_expect($legacy['profiles'][0]['fan_min'] === 40, 'auto profile default stays 40');
foreach (array('fan_target'=>36, 'fan_mid_temp'=>43, 'fan_mid_fan'=>75, 'fan_full'=>50,
    'recover'=>40, 'cutoff'=>55) as $key=>$value) {
    profile_expect($legacy['profiles'][0][$key] === $value, 'default profile '.$key);
}
$cold = normalize_clock_profiles(array('profiles'=>array(array('id'=>'cold', 'fan_target'=>0,
    'fan_mid_temp'=>0, 'recover'=>40, 'cutoff'=>120))));
profile_expect($cold['profiles'][0]['fan_target'] === 20, 'profile target floor');
profile_expect($cold['profiles'][0]['fan_mid_temp'] === 21, 'profile midpoint floor and ordering');
profile_expect($cold['profiles'][0]['cutoff'] === 99, 'profile two-digit protection limit');
profile_expect($legacy['profiles'][0]['experimental'] === false, 'legacy remains normal');
profile_expect($legacy['profiles'][0]['clock_indices'] === array(30, 30, 30), 'normal 400 means 30, not 22');
profile_expect($legacy['profiles'][0]['mode'] === 'global', 'legacy normal mode stays global');
profile_expect($legacy['profiles'][0]['scope'] === 'global', 'legacy normal scope follows global mode');
profile_expect($legacy['profiles'][0]['version'] === 2 && $legacy['profiles'][0]['mapping'] === 'bkb-v1-clock-index-1',
    'legacy migration keeps version 2 mapping');
profile_expect(normalize_clock_profiles($legacy) === $legacy, 'legacy scope round trip');
$legacyIndividual = normalize_clock_profiles(array('profiles' => array(
    array('id' => 'old-i', 'clock' => 400, 'mode' => 'individual'))));
profile_expect($legacyIndividual['profiles'][0]['scope'] === 'individual', 'legacy individual scope follows mode');
profile_expect($legacyIndividual['profiles'][0]['clock_indices'] === array(30, 30, 30), 'individual normal stays Index 30');

$global = $profile;
$global['clock_indices'] = array(29, '29', 29);
$global['scope'] = 'global';
$globalClean = normalize_clock_profiles(array('profiles' => array($global)));
profile_expect(!isset($globalClean['error']), 'experimental global scope with equal indices');
profile_expect($globalClean['profiles'][0]['scope'] === 'global', 'explicit global scope preserved');
profile_expect($globalClean['profiles'][0]['mode'] === 'individual', 'global scope does not change actual mode');
profile_expect($globalClean['profiles'][0]['clock_indices'] === array(29, 29, 29), 'equal indices canonicalized');
profile_expect(normalize_clock_profiles($globalClean) === $globalClean, 'global scope round trip');

$individual = $profile;
$individual['scope'] = 'individual';
$individualClean = normalize_clock_profiles(array('profiles' => array($individual)));
profile_expect($individualClean['profiles'][0]['scope'] === 'individual', 'explicit individual scope preserved');
profile_expect($individualClean['profiles'][0]['clock_indices'] === array(8, 30, 32), 'unequal indices preserved');
profile_expect(normalize_clock_profiles($individualClean) === $individualClean, 'individual scope round trip');

$mixed = array('id' => 'mix', 'clock' => 400, 'mode' => 'global', 'scope' => 'individual', 'experimental' => false);
$mixedClean = normalize_clock_profiles(array('profiles' => array($mixed)));
profile_expect($mixedClean['profiles'][0]['scope'] === 'individual', 'explicit normal scope is not rewritten');
profile_expect($mixedClean['profiles'][0]['mode'] === 'global', 'explicit normal mode is not rewritten');
profile_expect($mixedClean['profiles'][0]['clock_indices'] === array(30, 30, 30), 'normal scope migration stays Index 30');
profile_expect(normalize_clock_profiles($mixedClean) === $mixedClean, 'mixed normal scope round trip');

$unequal = $profile;
$unequal['scope'] = 'global';
profile_expect(bkb_clock_profile_error($unequal) === 'Experimental global scope requires equal clock indices',
    'experimental global scope rejects unequal indices');
profile_expect(isset(normalize_clock_profiles(array('profiles' => array($unequal)))['error']),
    'unequal global scope is not saved');

$actualGlobal = $profile;
$actualGlobal['mode'] = 'global';
$actualGlobal['scope'] = 'global';
$actualGlobal['clock_indices'] = array(30, 30, 30);
profile_expect(bkb_clock_profile_error($actualGlobal) ===
    'Experimental profile requires version 2, current mapping and individual mode',
    'global actual mode experimental still rejected');

foreach (array('linked', 'GLOBAL', 'global ', true, 1, null, 400, '480', '') as $badScope) {
    $p = $profile;
    $p['scope'] = $badScope;
    profile_expect(bkb_clock_profile_error($p) === 'Clock scope must be global or individual',
        'unknown profile scope rejected');
}
echo "PASS versioned clock profiles, legacy migration and strict indices\n";
