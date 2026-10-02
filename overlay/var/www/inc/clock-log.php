<?php

/*
 * Readable view of a version-2 clock-history document.
 *
 * bs_clock_log_view($path) returns:
 *   ok        bool
 *   text      string   formatted records, or '' when ok is false
 *   error     string   empty on success; why the view failed otherwise
 *   format    'readable'
 *   size      int      source file bytes (0 when the file was not sized)
 *   shown     int      strlen(text)
 *   truncated bool     true when the file or the rendered window was bounded
 *   rows      int      record lines included in text
 *
 * The file is sized first and decoded with json_decode only when it is within
 * the byte limit. A chopped tail is never treated as JSON. Raw JSON is not
 * produced here. PHP 5.6, no scalar type hints.
 *
 * Compact sample keys match clock-history-recorder.py: t, cm, xp, pi, scc, c,
 * rc, cv, tmp, fan, a, da, r, hw. Echo values are labels, not physical MHz.
 * A stored 0 is shown as 0. Missing and non-numeric values are n/a. Invalid
 * timestamps and boards/samples are counted omissions, not synthetic zeros.
 * No hashrate is derived.
 */

function bs_clock_log_max_bytes() {
    return 2097152;
}

function bs_clock_log_max_rows() {
    return 300;
}

function bs_clock_log_max_depth() {
    return 64;
}

function bs_clock_log_max_output_bytes() {
    return 262144;
}

function bs_clock_log_fail($size, $error, $truncated) {
    return array(
        'ok' => false,
        'text' => '',
        'error' => (string) $error,
        'format' => 'readable',
        'size' => (int) $size,
        'shown' => 0,
        'truncated' => $truncated ? true : false,
        'rows' => 0,
    );
}

function bs_clock_log_is_list($value) {
    if (!is_array($value)) return false;
    $i = 0;
    foreach ($value as $k => $unused) {
        if ($k !== $i) return false;
        $i++;
    }
    return true;
}

function bs_clock_log_number($value) {
    if (is_bool($value) || is_array($value) || is_object($value) || $value === null) return null;
    if (is_int($value)) return $value;
    if (is_float($value)) {
        if (!is_finite($value)) return null;
        if ($value > 9007199254740992.0 || $value < -9007199254740992.0) return null;
        return $value;
    }
    if (!is_string($value)) return null;
    if (!preg_match('/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/', $value)) return null;
    if (!is_numeric($value)) return null;
    if (strpos($value, '.') === false) {
        $as_int = (int) $value;
        if ((string) $as_int !== $value) return null;
        return $as_int;
    }
    $as_float = (float) $value;
    if (!is_finite($as_float)) return null;
    return $as_float;
}

function bs_clock_log_int_text($value) {
    $n = bs_clock_log_number($value);
    if (is_float($n)) {
        if (floor($n) != $n) return null;
        if ($n < 0 || $n > 9007199254740992.0) return null;
        if ($n <= PHP_INT_MAX) {
            $as_int = (int) $n;
            if ((float) $as_int === $n) return (string) $as_int;
        }
        $text = sprintf('%.0f', $n);
        if (!preg_match('/^(?:0|[1-9][0-9]*)$/', $text)) return null;
        return $text;
    }
    if (!is_int($n) || $n < 0) return null;
    return (string) $n;
}

function bs_clock_log_float_text($value) {
    $n = bs_clock_log_number($value);
    if ($n === null) return null;
    if (is_int($n)) return (string) $n;
    if (!is_float($n)) return null;
    $text = rtrim(rtrim(sprintf('%.6f', $n), '0'), '.');
    if ($text === '' || $text === '-0') return '0';
    return $text;
}

function bs_clock_log_opt_int($sample, $key) {
    if (!array_key_exists($key, $sample)) return 'n/a';
    $text = bs_clock_log_int_text($sample[$key]);
    return ($text === null) ? 'n/a' : $text;
}

function bs_clock_log_opt_float($sample, $key) {
    if (!array_key_exists($key, $sample)) return 'n/a';
    $text = bs_clock_log_float_text($sample[$key]);
    return ($text === null) ? 'n/a' : $text;
}

function bs_clock_log_mode_text($sample) {
    if (!array_key_exists('cm', $sample)) return 'n/a';
    $cm = $sample['cm'];
    if (is_int($cm) || is_float($cm)) {
        $text = bs_clock_log_int_text($cm);
        return ($text === null) ? 'n/a' : $text;
    }
    if (!is_string($cm)) return 'n/a';
    if (preg_match('/[\x00-\x1F]/', $cm)) return 'n/a';
    $cm = trim($cm);
    if ($cm === '' || strlen($cm) > 64) return 'n/a';
    return $cm;
}

function bs_clock_log_command($sample) {
    if (!array_key_exists('cv', $sample)) return 'n/a';
    $cv = $sample['cv'];
    if ($cv === true || $cv === 1 || $cv === '1') return 'verified';
    if ($cv === false || $cv === 0 || $cv === '0') return 'unverified';
    return 'n/a';
}

function bs_clock_log_timestamp($value) {
    if (is_bool($value) || is_array($value) || is_object($value) || $value === null) return null;
    $n = bs_clock_log_number($value);
    if (is_float($n)) {
        if (floor($n) != $n || $n < 0 || $n > PHP_INT_MAX) return null;
        $as_int = (int) $n;
        if ((float) $as_int !== $n) return null;
        $n = $as_int;
    }
    if (!is_int($n) || $n < 0) return null;
    $text = gmdate('Y-m-d\TH:i:s\Z', $n);
    if (!is_string($text) || !preg_match('/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/', $text)) return null;
    return array('unix' => $n, 'text' => $text);
}

function bs_clock_log_format_row($stamp, $board_id, $sample) {
    $requested = bs_clock_log_opt_int($sample, 'pi');
    if ($requested === '0' && array_key_exists('xp', $sample)
            && in_array($sample['xp'], array(0, '0', false), true)) {
        $requested = 'none(normal)';
    }
    return $stamp
        . ' board=' . $board_id
        . ' mode=' . bs_clock_log_mode_text($sample)
        . ' experimental=' . bs_clock_log_opt_int($sample, 'xp')
        . ' requested_clock_index=' . $requested
        . ' sent_clock_index=' . bs_clock_log_opt_int($sample, 'scc')
        . ' command=' . bs_clock_log_command($sample)
        . ' requested_label=' . bs_clock_log_opt_int($sample, 'rc')
        . ' echo=' . bs_clock_log_opt_int($sample, 'c') . ' (bridge echo, not physical MHz)'
        . ' temp=' . bs_clock_log_opt_float($sample, 'tmp')
        . ' fan=' . bs_clock_log_opt_float($sample, 'fan')
        . ' accepted=' . bs_clock_log_opt_int($sample, 'a')
        . ' difficulty=' . bs_clock_log_opt_float($sample, 'da')
        . ' rejected=' . bs_clock_log_opt_int($sample, 'r')
        . ' hw=' . bs_clock_log_opt_int($sample, 'hw');
}

function bs_clock_log_header() {
    return '# UTC clock-history. requested_clock_index is the raw override (none in normal mode). sent_clock_index is the sent clock code. command is the verified flag. echo is the bridge status echo, not physical MHz. temp is Celsius; fan is percent. Missing values are n/a. A shown 0 is a real zero. No hashrate is derived.';
}

function bs_clock_log_collect($doc) {
    $rows = array();
    $omitted = 0;
    $ord = 0;
    $total = 0;
    foreach ($doc['boards'] as $board) {
        if (!is_array($board) || !array_key_exists('id', $board) || !bs_clock_log_is_list(isset($board['samples']) ? $board['samples'] : null)) {
            $omitted++;
            continue;
        }
        $board_id = bs_clock_log_int_text($board['id']);
        if ($board_id === null) {
            $omitted++;
            continue;
        }
        foreach ($board['samples'] as $sample) {
            $ord++;
            if (!is_array($sample) || !array_key_exists('t', $sample)) {
                $omitted++;
                continue;
            }
            $stamp = bs_clock_log_timestamp($sample['t']);
            if ($stamp === null) {
                $omitted++;
                continue;
            }
            $rows[] = array(
                'unix' => $stamp['unix'],
                'stamp' => $stamp['text'],
                'board_sort' => (int) $board_id,
                'board_id' => $board_id,
                'sample' => $sample,
                'ord' => $ord,
            );
            $total++;
            // Keep only the newest window while scanning all boards.
            if (count($rows) >= bs_clock_log_max_rows() * 2) {
                usort($rows, 'bs_clock_log_cmp');
                $rows = array_slice($rows, -bs_clock_log_max_rows());
            }
        }
    }
    usort($rows, 'bs_clock_log_cmp');
    return array('rows' => array_slice($rows, -bs_clock_log_max_rows()), 'omitted' => $omitted, 'total' => $total);
}

function bs_clock_log_cmp($a, $b) {
    if ($a['unix'] < $b['unix']) return -1;
    if ($a['unix'] > $b['unix']) return 1;
    if ($a['board_sort'] < $b['board_sort']) return -1;
    if ($a['board_sort'] > $b['board_sort']) return 1;
    if ($a['ord'] < $b['ord']) return -1;
    if ($a['ord'] > $b['ord']) return 1;
    return 0;
}

function bs_clock_log_render($collected) {
    $all = $collected['rows'];
    $total = $collected['total'];
    $window = $all;
    $truncated = false;
    if ($total > bs_clock_log_max_rows()) {
        $window = array_slice($all, -bs_clock_log_max_rows());
        $truncated = true;
    }
    $lines = array();
    foreach ($window as $row) {
        $lines[] = bs_clock_log_format_row($row['stamp'], $row['board_id'], $row['sample']);
    }
    $max_out = bs_clock_log_max_output_bytes();
    while (true) {
        $notes = array(bs_clock_log_header());
        if ($collected['omitted'] > 0) {
            $notes[] = '# omitted ' . (int) $collected['omitted'] . ' invalid board or sample records';
        }
        if ($truncated) {
            $notes[] = '# showing ' . count($lines) . ' most recent of ' . $total . ' valid records';
        } else if (count($lines) === 0) {
            $notes[] = '# no samples';
        }
        $text = implode("\n", array_merge($notes, $lines)) . "\n";
        if (strlen($text) <= $max_out) {
            return array('text' => $text, 'truncated' => $truncated, 'rows' => count($lines));
        }
        if (count($lines) === 0) {
            $text = bs_clock_log_header() . "\n# showing 0 most recent of " . $total . " valid records\n";
            return array('text' => $text, 'truncated' => true, 'rows' => 0);
        }
        array_shift($lines);
        $truncated = true;
    }
}

function bs_clock_log_view($path) {
    if (!is_string($path) || $path === '' || !is_file($path) || !is_readable($path)) {
        return bs_clock_log_fail(0, 'clock-history file is not readable', false);
    }
    $size = @filesize($path);
    if ($size === false) {
        return bs_clock_log_fail(0, 'clock-history size could not be read', false);
    }
    $size = (int) $size;
    $max = bs_clock_log_max_bytes();
    if ($size > $max) {
        return bs_clock_log_fail(
            $size,
            'clock-history is ' . $size . ' bytes, over the ' . $max
                . ' byte read limit. A truncated slice was refused. Open the raw view or download the file.',
            true
        );
    }
    $raw = @file_get_contents($path, false, null, 0, $max + 1);
    if ($raw === false) {
        return bs_clock_log_fail($size, 'clock-history could not be read. Open the raw view or download the file.', false);
    }
    $read_len = strlen($raw);
    if ($read_len > $max) {
        return bs_clock_log_fail(
            $read_len,
            'clock-history is ' . $read_len . ' bytes, over the ' . $max
                . ' byte read limit. A truncated slice was refused. Open the raw view or download the file.',
            true
        );
    }
    $doc = json_decode($raw, true, bs_clock_log_max_depth());
    $lead = ltrim($raw, " \t\r\n");
    $decoded_object = is_array($doc) && $lead !== '' && $lead[0] === '{';
    if (!$decoded_object) {
        $msg = function_exists('json_last_error_msg') ? json_last_error_msg() : 'JSON error';
        if (is_array($doc)) $msg = 'document is not an object';
        return bs_clock_log_fail(
            $read_len,
            'clock-history JSON could not be decoded (' . $msg . '). A truncated slice was not used. Open the raw view or download the file.',
            false
        );
    }
    if (array_key_exists('version', $doc) && $doc['version'] !== 2 && $doc['version'] !== '2') {
        return bs_clock_log_fail($read_len, 'clock-history version is not supported. Open the raw view or download the file.', false);
    }
    if (!isset($doc['boards']) || !bs_clock_log_is_list($doc['boards'])) {
        return bs_clock_log_fail($read_len, 'clock-history JSON has no boards list. Open the raw view or download the file.', false);
    }
    $rendered = bs_clock_log_render(bs_clock_log_collect($doc));
    return array(
        'ok' => true,
        'text' => $rendered['text'],
        'error' => '',
        'format' => 'readable',
        'size' => $read_len,
        'shown' => strlen($rendered['text']),
        'truncated' => $rendered['truncated'] ? true : false,
        'rows' => (int) $rendered['rows'],
    );
}
