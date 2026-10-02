<?php
function bkb_firmware_clock_images() {
    return array(
        '0e8467eddabf589ad94549a8d572c759' => 'CUSTOM V1',
        '18babe0c4c67d9146e230f9dc423f2f2' => 'CUSTOM V1',
    );
}

// A release label alone never enables clock controls: require the paired hash.
function bkb_firmware_supports_clocks($record) {
    if (!is_array($record) || !isset($record['identity'], $record['chip_md5']) ||
        !is_string($record['identity']) || !is_string($record['chip_md5'])) return false;
    $images = bkb_firmware_clock_images();
    $hash = strtolower($record['chip_md5']);
    return isset($images[$hash]) && $images[$hash] === $record['identity'];
}

function bkb_firmware_is_current($record) {
    return bkb_firmware_supports_clocks($record) &&
        strtolower($record['chip_md5']) === '18babe0c4c67d9146e230f9dc423f2f2';
}

function bkb_firmware_display_record($record) {
    // The retired development image used the same label; never show it as current.
    if (bkb_firmware_supports_clocks($record) && !bkb_firmware_is_current($record)) {
        $record['identity'] = 'CUSTOM V1 (development)';
    }
    return $record;
}
