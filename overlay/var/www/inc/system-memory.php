<?php

function system_memory_stats($meminfo) {
    $values = array();
    foreach (explode("\n", (string)$meminfo) as $line) {
        if (preg_match('/^(\w+):\s+(\d+)/', $line, $match)) {
            $values[$match[1]] = (int)$match[2];
        }
    }
    $total = isset($values['MemTotal']) ? $values['MemTotal'] : 0;
    if (isset($values['MemAvailable'])) {
        $available = $values['MemAvailable'];
    } else {
        // Legacy kernels count tmpfs in Cached, but it cannot be reclaimed
        // without swap. Treat it as used, not as available file cache.
        $free = isset($values['MemFree']) ? $values['MemFree'] : 0;
        $buffers = isset($values['Buffers']) ? $values['Buffers'] : 0;
        $cached = isset($values['Cached']) ? $values['Cached'] : 0;
        $shmem = isset($values['Shmem']) ? $values['Shmem'] : 0;
        $available = $free + $buffers + max(0, $cached - $shmem);
    }
    $available = max(0, min($total, $available));
    return array(
        'total_kb' => $total,
        'used_kb' => $total - $available,
        'used_pct' => $total > 0 ? round(100 * ($total - $available) / $total) : 0,
    );
}
