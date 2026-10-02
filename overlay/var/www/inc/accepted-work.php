<?php

/*
 * Accepted-work hashrate from clock-history recorder samples.
 *
 * A window is valid only while the board stays enabled and its counters,
 * process epoch, clock generation, and effective pool route remain unchanged.
 */
function bs_accepted_work_rate($samples, $window_seconds) {
    $result = array(
        'valid' => false,
        'ghs' => null,
        'seconds' => 0,
        'accepted_delta' => 0,
        'difficulty_delta' => 0,
        'reason' => 'measuring',
    );
    if (!is_array($samples) || count($samples) < 2) return $result;

    usort($samples, function($a, $b) {
        return (isset($a['t']) ? (int)$a['t'] : 0) -
               (isset($b['t']) ? (int)$b['t'] : 0);
    });
    $latest = end($samples);
    if (!is_array($latest) || empty($latest['en'])) {
        $result['reason'] = 'board-disabled';
        return $result;
    }

    $latest_t = isset($latest['t']) ? (int)$latest['t'] : 0;
    $cutoff = $latest_t - max(1, (int)$window_seconds);
    $candidate = null;
    for ($i = count($samples) - 2; $i >= 0; $i--) {
        $sample = $samples[$i];
        if (!is_array($sample) || !isset($sample['t'])) continue;
        if ((int)$sample['t'] < $cutoff) break;
        if (empty($sample['en'])) break;
        if ((int)(isset($sample['se']) ? $sample['se'] : -1) !==
            (int)(isset($latest['se']) ? $latest['se'] : -2)) break;
        if ((int)(isset($sample['cg']) ? $sample['cg'] : -1) !==
            (int)(isset($latest['cg']) ? $latest['cg'] : -2)) break;
        $same_identity = true;
        foreach (array('xp', 'pi', 'scc', 'pid', 'hs', 'ds', 're', 'ee') as $key) {
            if ((isset($sample[$key]) ? $sample[$key] : null) !==
                (isset($latest[$key]) ? $latest[$key] : null)) $same_identity = false;
        }
        if (!$same_identity) break;
        if ((int)(isset($sample['ep']) ? $sample['ep'] : -1) !==
            (int)(isset($latest['ep']) ? $latest['ep'] : -2)) break;
        if ((string)(isset($sample['pool']) ? $sample['pool'] : '') !==
            (string)(isset($latest['pool']) ? $latest['pool'] : '')) break;
        if ((float)(isset($sample['da']) ? $sample['da'] : -1) >
            (float)(isset($latest['da']) ? $latest['da'] : -2)) break;
        if ((int)(isset($sample['de']) ? $sample['de'] : -1) >
            (int)(isset($latest['de']) ? $latest['de'] : -2)) break;
        $candidate = $sample;
    }

    if ($candidate === null) return $result;
    $seconds = $latest_t - (int)$candidate['t'];
    $accepted_delta = (int)(isset($latest['a']) ? $latest['a'] : 0) -
                      (int)(isset($candidate['a']) ? $candidate['a'] : 0);
    $difficulty_delta = (float)(isset($latest['da']) ? $latest['da'] : 0) -
                        (float)(isset($candidate['da']) ? $candidate['da'] : 0);
    $result['seconds'] = max(0, $seconds);
    $result['accepted_delta'] = max(0, $accepted_delta);
    $result['difficulty_delta'] = max(0, $difficulty_delta);

    if ($seconds < 300 || $accepted_delta < 20 || $difficulty_delta <= 0) {
        return $result;
    }
    $result['valid'] = true;
    $result['ghs'] = ($difficulty_delta * 4294967296.0) / $seconds / 1000000000.0;
    $result['reason'] = '';
    return $result;
}

function bs_accepted_rates_by_board(
    $path = '/run/blakestream/accepted-work-rates.json',
    $now = null,
    $max_age_seconds = 180
) {
    $out = array();
    $raw = @file_get_contents($path);
    if ($raw === false || $raw === '') return $out;
    $summary = @json_decode($raw, true);
    if (!is_array($summary) || !isset($summary['boards']) ||
        !is_array($summary['boards'])) {
        return $out;
    }

    $current_time = $now === null ? time() : (int)$now;
    $updated = isset($summary['updated']) ? (int)$summary['updated'] : 0;
    $age = $current_time - $updated;
    if (isset($summary['version']) && (int)$summary['version'] >= 2) {
        $max_age_seconds = min(120, $max_age_seconds);
        if ($age < 0) return $out;
    }
    if ($updated <= 0 || $age < -300 || $age > max(1, (int)$max_age_seconds)) {
        return $out;
    }

    foreach ($summary['boards'] as $board_id => $rates) {
        if (!is_array($rates)) continue;
        $bid = (int)$board_id;
        if ($bid < 0 || $bid > 2) continue;
        $out[$bid] = array(
            'context' => isset($rates['context']) && is_array($rates['context']) ? $rates['context'] : array(),
            '15m' => isset($rates['15m']) && is_array($rates['15m'])
                ? $rates['15m'] : array('valid' => false),
            '1h' => isset($rates['1h']) && is_array($rates['1h'])
                ? $rates['1h'] : array('valid' => false),
        );
    }
    return $out;
}

function bs_accepted_context_matches($rates, $stat, $dev) {
    if (!isset($rates['context']) || !is_array($rates['context'])) return false;
    $context = $rates['context'];
    if (isset($context['start'])) {
        foreach (array('start' => 'DiagStartUnix', 'startup' => 'StartupSuccessCount',
                       'chips' => 'ChipCount', 'algo' => 'Algo', 'mode' => 'ClockMode') as $key => $field) {
            if (!isset($context[$key], $stat[$field]) || (string)$context[$key] !== (string)$stat[$field]) return false;
        }
        if (empty($stat['DiagShareSnapValid']) || empty($stat['DiagDeviceElapsedValid'])) return false;
        if (!isset($context['cv'], $stat['CommandVerified']) ||
            (int)$context['cv'] !== (empty($stat['CommandVerified']) ? 0 : 1)) return false;
        foreach (array('GlobalClockPending', 'GlobalClockInflight', 'ClockRetryPending', 'ClockReapplyPending', 'PendingClockReapply') as $key) {
            if (!empty($stat[$key])) return false;
        }
    }
    $keys = array('pid' => 'DiagPid', 'hs' => 'DiagHostSha256', 'ds' => 'DiagStatEpoch',
                  'cg' => 'ClockGeneration', 'ee' => 'DiagEnableEpoch',
                  'scc' => 'SentClockIndex');
    foreach ($keys as $key => $field) {
        if (!isset($context[$key]) || !isset($stat[$field]) ||
            (string)$context[$key] !== (string)$stat[$field]) return false;
    }
    // Live rates can survive a reconnect; the active pool and clock must still match.
    if (!isset($context['re'], $stat['DiagRouteEpoch']) ||
        !ctype_digit((string)$context['re']) || !ctype_digit((string)$stat['DiagRouteEpoch']) ||
        (float)$context['re'] > (float)$stat['DiagRouteEpoch']) return false;
    if (empty($context['pid']) || empty($context['hs'])) return false;
    if (!isset($context['xp']) || (bool)$context['xp'] !== !empty($stat['ExperimentalClock'])) return false;
    $clock_index = isset($stat['RequestedClockIndex']) ? (int)$stat['RequestedClockIndex'] : 0;
    if (!isset($context['pi']) || (int)$context['pi'] !== $clock_index) return false;
    if (!isset($context['ep']) || !isset($dev['EffectivePool']) ||
        (int)$context['ep'] !== (int)$dev['EffectivePool']) return false;
    return true;
}
