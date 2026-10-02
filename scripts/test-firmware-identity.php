<?php
require dirname(__DIR__) . '/overlay/var/www/inc/firmware-identity.php';

function expect_identity($condition, $message) {
    if (!$condition) {
        fwrite(STDERR, 'FAIL: ' . $message . "\n");
        exit(1);
    }
}

$md5 = '0e8467eddabf589ad94549a8d572c759';
foreach (bkb_firmware_clock_images() as $md5 => $label) {
    foreach (array($md5, strtoupper($md5)) as $hash) {
        $record = array('identity' => $label, 'chip_md5' => $hash, 'at' => 1234);
        $expected = $record;
        $current = $md5 === '18babe0c4c67d9146e230f9dc423f2f2';
        if (!$current) $expected['identity'] = 'CUSTOM V1 (development)';
        expect_identity(bkb_firmware_supports_clocks($record), 'supported image accepted: ' . $label);
        expect_identity(bkb_firmware_is_current($record) === $current, 'release distinguished by hash, not reused label');
        expect_identity(bkb_firmware_display_record($record) === $expected,
            'label normalized without changing verification time/hash');
        expect_identity($record['identity'] === $label, 'original record left unchanged');
    }
}
foreach (array(
    null, false, '', array(),
    array('identity' => 'CUSTOM V1'),
    array('chip_md5' => $md5),
    array('identity' => 'CUSTOM V1', 'chip_md5' => 'wrong'),
    array('identity' => 'CUSTOM V2', 'chip_md5' => '18babe0c4c67d9146e230f9dc423f2f2'),
    array('identity' => 'CUSTOM V2', 'chip_md5' => '0e8467eddabf589ad94549a8d572c759'),
    array('identity' => 'CUSTOM v1', 'chip_md5' => 'wrong'),
    array('identity' => 'CUSTOM v1', 'chip_md5' => $md5),
    array('identity' => 'CUSTOM V1', 'chip_md5' => array($md5)),
    array('identity' => array('CUSTOM V1'), 'chip_md5' => $md5),
    array('identity' => 'STOCK', 'chip_md5' => $md5),
    array('identity' => 'CUSTOM v11', 'chip_md5' => $md5),
    array('identity' => 'CUSTOM v11', 'chip_md5' => 'c6d7b77d7bd2b3ca92ee289f848aab9a'),
    array('identity' => 'STOCK', 'chip_md5' => 'd5f844be2952fd713717a71a67e2be61'),
) as $record) {
    expect_identity(!bkb_firmware_is_current($record), 'invalid or older record cannot enable current firmware');
    expect_identity(!bkb_firmware_supports_clocks($record), 'invalid label/hash pair cannot enable clocks');
    expect_identity(bkb_firmware_display_record($record) === $record, 'older/unknown record preserved');
}

$root = dirname(__DIR__);
foreach (array('f_dev.php', 'f_settings.php') as $file) {
    $source = file_get_contents($root . '/overlay/var/www/' . $file);
    expect_identity(strpos($source, "require_once __DIR__ . '/inc/firmware-identity.php';") !== false,
        $file . ' loads shared identity validation');
    expect_identity(strpos($source, 'bkb_firmware_supports_clocks(') !== false,
        $file . ' uses compatible identity validation');
}
expect_identity(md5_file($root . '/overlay/opt/scripta/firmware/custom.bin') === '18babe0c4c67d9146e230f9dc423f2f2',
    'staged V1 release image pinned');
expect_identity(hash_file('sha256', $root . '/overlay/opt/scripta/firmware/custom.bin') ===
    'd2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a', 'V1 release SHA256');
expect_identity(hash_file('sha256', $root . '/firmware-custom/build/bkb-stm32f407-flash-clockmod-v1.bin') ===
    'd2a1a66db73e8fb95cc482962b64011226ded70f107eb6301808aef65e076c8a', 'reference matches V1 release');
echo "Firmware identity: V1 release pinned, development image distinguished, strict label/hash checks PASS\n";
