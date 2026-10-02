<?php
function bs_diagnostic_logs() {
    return array(
        'network-events.log' => array('key' => 'userNetworkLogTarget', 'source' => 'network_events'),
        'system-events.log' => array('key' => 'userSystemLogTarget', 'source' => 'system_events'),
        'cooling-events.log' => array('key' => 'userCoolingLogTarget', 'source' => 'cooling_events'),
        'lighttpd-error.log' => array('key' => 'userWebErrorLogTarget', 'source' => 'lighttpd'),
    );
}

function bs_diagnostic_path($name, $target) {
    return ($target === 'SD' ? '/opt/scripta/var/' : '/run/blakestream/') . $name;
}

function bs_diagnostic_target($spec, $cfg) {
    return isset($cfg[$spec['key']]) && $cfg[$spec['key']] === 'SD' ? 'SD' : 'RAM';
}

function bs_diagnostic_source_path($source, $cfg, $target = '') {
    foreach (bs_diagnostic_logs() as $name => $spec) {
        if ($source === $spec['source']) {
            return bs_diagnostic_path($name, $target === '' ? bs_diagnostic_target($spec, $cfg) : $target);
        }
    }
    return null;
}

function bs_diagnostic_managed_path($path) {
    foreach (bs_diagnostic_logs() as $name => $spec) {
        foreach (array('RAM', 'SD') as $target) {
            if ($path === bs_diagnostic_path($name, $target)) return true;
        }
    }
    return false;
}

function bs_diagnostic_lock() {
    $path = '/run/blakestream/diagnostic-logs.lock';
    if (is_link($path)) return false;
    $lock = @fopen($path, 'c');
    if (!$lock) return false;
    if (!@flock($lock, LOCK_EX | LOCK_NB)) { fclose($lock); return false; }
    return $lock;
}

function bs_diagnostic_unlock($lock) {
    flock($lock, LOCK_UN);
    fclose($lock);
}

function bs_diagnostic_clear($path) {
    if (!bs_diagnostic_managed_path($path)) return @file_put_contents($path, '', LOCK_EX);
    $lock = bs_diagnostic_lock();
    if (!$lock) return false;
    try {
        return is_link($path) ? false : @file_put_contents($path, '', LOCK_EX);
    } finally {
        bs_diagnostic_unlock($lock);
    }
}

function bs_diagnostic_set_target($name, $target, $config_path) {
    $specs = bs_diagnostic_logs();
    if (!isset($specs[$name]) || !in_array($target, array('RAM', 'SD'), true)) {
        return array('ok' => false, 'error' => 'Invalid log destination');
    }
    $lock = bs_diagnostic_lock();
    if (!$lock) return array('ok' => false, 'error' => 'Log collector is busy. Try again shortly.');
    $tmp = false;
    try {
        $cfg = @json_decode(@file_get_contents($config_path), true);
        if (!is_array($cfg)) return array('ok' => false, 'error' => 'Settings unavailable');
        $old = bs_diagnostic_target($specs[$name], $cfg);
        if ($old === $target) return array('ok' => true, 'key' => $specs[$name]['key'], 'target' => $target);
        $src = bs_diagnostic_path($name, $old);
        $dst = bs_diagnostic_path($name, $target);
        if (is_link($src) || is_link($dst)) return array('ok' => false, 'error' => 'Invalid log path');
        // Keep the source until both the copy and the setting are safely written.
        if (file_exists($src)) {
            if (!@copy($src, $dst)) return array('ok' => false, 'error' => 'Could not move log; original retained');
            @chmod($dst, 0664);
        } elseif (@file_put_contents($dst, '') === false) {
            return array('ok' => false, 'error' => 'Destination is not writable');
        }
        $cfg[$specs[$name]['key']] = $target;
        $tmp = @tempnam(dirname($config_path), 'scripta.conf.tmp.');
        $encoded = json_encode($cfg, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
        if ($tmp === false || @file_put_contents($tmp, $encoded) !== strlen($encoded)) {
            return array('ok' => false, 'error' => 'Settings write failed; original retained');
        }
        @chmod($tmp, 0664);
        if (!@rename($tmp, $config_path)) return array('ok' => false, 'error' => 'Settings write failed; original retained');
        if (file_exists($src)) @unlink($src);
        return array('ok' => true, 'key' => $specs[$name]['key'], 'target' => $target, 'note' => 'Log moved to ' . $target);
    } finally {
        if ($tmp !== false && file_exists($tmp)) @unlink($tmp);
        bs_diagnostic_unlock($lock);
    }
}
