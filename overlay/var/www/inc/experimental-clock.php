<?php
function bkb_clock_profile_error($profile) {
    if (!is_array($profile)) return 'Invalid clock profile';
    if (array_key_exists('version', $profile) && $profile['version'] !== 2)
        return 'Unsupported clock profile version';
    if (array_key_exists('mapping', $profile) && $profile['mapping'] !== 'bkb-v1-clock-index-1')
        return 'Unsupported clock mapping';
    if (array_key_exists('experimental', $profile) && !is_bool($profile['experimental']))
        return 'Experimental must be a boolean';
    if (array_key_exists('scope', $profile) &&
        (!is_string($profile['scope']) ||
         ($profile['scope'] !== 'global' && $profile['scope'] !== 'individual')))
        return 'Clock scope must be global or individual';
    $experimental = !empty($profile['experimental']);
    if ($experimental && (!isset($profile['version'], $profile['mapping'], $profile['mode']) ||
        $profile['version'] !== 2 || $profile['mapping'] !== 'bkb-v1-clock-index-1' || $profile['mode'] !== 'individual'))
        return 'Experimental profile requires version 2, current mapping and individual mode';
    if ($experimental || array_key_exists('clock_indices', $profile)) {
        if (!isset($profile['clock_indices']) || !is_array($profile['clock_indices']) ||
            array_keys($profile['clock_indices']) !== array(0, 1, 2))
            return 'Expected three clock indices';
        foreach ($profile['clock_indices'] as $index) {
            if ((!is_int($index) && !is_string($index)) ||
                !preg_match('/\A(?:[89]|[12][0-9]|3[0-2])\z/', (string)$index))
                return 'clock indices must be integers from 8 to 32';
            if (!$experimental && (int)$index !== 30)
                return 'Normal 400 uses clock index 30';
        }
        if ($experimental && array_key_exists('scope', $profile) && $profile['scope'] === 'global') {
            $first = (int)$profile['clock_indices'][0];
            if ((int)$profile['clock_indices'][1] !== $first || (int)$profile['clock_indices'][2] !== $first)
                return 'Experimental global scope requires equal clock indices';
        }
    }
    return null;
}

function bkb_strip_experimental_options($options) {
    $out = array();
    if (!is_array($options)) return $out;
    foreach ($options as $option) {
        if (is_array($option) && isset($option['key']) &&
            ($option['key'] === 'baikal-experimental-clock-index' || $option['key'] === 'baikal-board-clock-index' ||
             $option['key'] === 'blakestream-clock-scope'))
            continue;
        $out[] = $option;
    }
    return $out;
}

function bkb_clear_experimental_options($path) {
    $options = @json_decode(@file_get_contents($path), true);
    if (!is_array($options)) return false;
    foreach ($options as $option) {
        if (!is_array($option) || !isset($option['key'])) return false;
    }
    $clean = bkb_strip_experimental_options($options);
    if ($clean === $options) return true;
    $encoded = json_encode($clean, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    if ($encoded === false) return false;
    $tmp = @tempnam(dirname($path), 'miner.options.clock_index.');
    if ($tmp === false) return false;
    $mode = @fileperms($path);
    if (@file_put_contents($tmp, $encoded) !== strlen($encoded) ||
        !@chmod($tmp, $mode === false ? 0664 : ($mode & 0777)) ||
        !@rename($tmp, $path)) {
        @unlink($tmp);
        return false;
    }
    return @json_decode(@file_get_contents($path), true) === $clean;
}
