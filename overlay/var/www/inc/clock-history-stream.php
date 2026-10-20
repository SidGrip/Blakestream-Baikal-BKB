<?php
require_once __DIR__ . '/hashrate-history.php';

// Samples stay in native-decoded batches; the sort holds only references.
class BSClockHistory {
    public $handle = false;
    public $reader;
    public $fields = array();
    public $boards = array();
    public $batches = array();
    public $valid = true;
    private $path;
    private $identity;
    private $cache = array();
    private $minimum;
    private $maximum;

    public function __construct($path, $minimum = null, $maximum = null) {
        $this->path = $path;
        $this->minimum = $minimum;
        $this->maximum = $maximum;
        $this->handle = @fopen($path, 'rb');
        if ($this->handle === false) {
            $this->valid = !file_exists($path);
            if (!$this->valid) error_log('clock-history: source unreadable');
            return;
        }
        $this->identity = fstat($this->handle);
        $this->reader = new BSHistoryJsonReader($this->handle);
        try {
            $self = $this;
            $this->reader->members(function($reader, $key, $depth) use ($self) {
                $start = $reader->offset();
                if ($key === 'boards') {
                    $self->boards = array();
                    $reader->members(function($reader, $key, $depth) use ($self) {
                        $self->boards[$key] = $self->board($depth);
                    }, $depth);
                } else {
                    $reader->value(false, $depth);
                }
                $self->fields[$key] = array($start, $reader->offset() - $start, $depth);
            });
            $this->reader->whitespace();
            if ($this->reader->peek() !== '') throw new RuntimeException('Trailing history data');
        } catch (RuntimeException $e) {
            error_log('clock-history: ' . $e->getMessage());
            $this->valid = false;
            $this->fields = $this->boards = $this->batches = array();
        }
    }

    public function __destruct() {
        if (is_resource($this->handle)) fclose($this->handle);
    }

    public function unchanged() {
        clearstatcache(true, $this->path);
        $current = @stat($this->path);
        return bs_clock_history_same_file($this->identity, $current);
    }

    public function field($key, $default) {
        if (!isset($this->fields[$key])) return $default;
        $location = $this->fields[$key];
        $this->reader->seek($location[0]);
        $value = $this->reader->value(true, $location[2]);
        return $value === null ? $default : $value;
    }

    public function board($depth) {
        $reader = $this->reader;
        $start = $reader->offset();
        $board = array('id' => 0, 'fields' => array(), 'batches' => null, 'refs' => array());
        $self = $this;
        $reader->members(function($reader, $key, $depth) use (&$board, $self) {
            $start = $reader->offset();
            if ($key === 'samples' && ($reader->peek() === '[' || $reader->peek() === '{')) {
                $board['refs'] = array();
                $board['batches'] = $self->sampleBatches($depth, $board['refs']);
            } else {
                $value = $reader->value($key === 'id', $depth);
                if ($key === 'id') $board['id'] = $value === null ? 0 : (int)$value;
                if ($key === 'samples') {
                    $board['batches'] = null;
                    $board['refs'] = array();
                }
            }
            $board['fields'][$key] = array($start, $reader->offset() - $start, $depth);
        }, $depth);
        $board['location'] = array($start, $reader->offset() - $start, $depth);
        return $board;
    }

    private function remember($offset, $length, $depth, $values, &$refs, $single = false) {
        $id = count($this->batches);
        $this->batches[] = array($offset, $length, $depth, $single);
        foreach ($values as $n => $sample) {
            if ($this->minimum === null || !is_array($sample) || !isset($sample['t'])) continue;
            $t = (int)$sample['t'];
            if ($t >= $this->minimum && ($this->maximum === null || $t <= $this->maximum)) {
                $refs[] = $t . ':' . $id . ':' . $n;
            }
        }
        return $id;
    }

    public function sampleBatches($depth, &$refs) {
        $reader = $this->reader;
        $ids = array();
        if ($reader->peek() === '{') {
            // Legacy keyed collections use the last value at each key.
            $entries = array();
            $self = $this;
            $reader->members(function($reader, $key, $depth) use (&$entries, $self) {
                $start = $reader->offset();
                $value = $reader->value(true, $depth);
                $refs = array();
                $id = $self->remember($start, $reader->offset() - $start, $depth, array($value), $refs, true);
                $entries[$key] = array($id, $refs);
            }, $depth);
            foreach ($entries as $entry) {
                $ids[] = $entry[0];
                foreach ($entry[1] as $ref) $refs[] = $ref;
            }
            return $ids;
        }

        $base = $reader->offset() + 1;
        if (fseek($this->handle, $base) !== 0) throw new RuntimeException('History seek failed');
        $buffer = '';
        $scan = 0;
        $nesting = 0;
        $count = 0;
        $last = 0;
        $previous = false;
        // PCRE skips complete strings in C. Only brackets and an incomplete
        // trailing string reach PHP, not every key/value in a clock sample.
        $pattern = '~"(?:[^"\\\\]++|\\\\.)*"(*SKIP)(*F)|"(?:[^"\\\\]++|\\\\.)*\\\\?$|[][{}]~s';
        while (true) {
            if ($scan === strlen($buffer)) {
                $chunk = @fread($this->handle, 8192);
                if ($chunk === false || $chunk === '') throw new RuntimeException('Unterminated clock samples');
                $buffer .= $chunk;
            }
            $matched = preg_match_all($pattern, $buffer, $tokens, PREG_OFFSET_CAPTURE, $scan);
            if ($matched === false) throw new RuntimeException('Clock sample scan failed');
            $incomplete = null;
            $end = null;
            $flush = null;
            foreach ($tokens[0] as $token) {
                $char = $token[0][0];
                $position = $token[1];
                if ($char === '"') { $incomplete = $position; break; }
                if ($char === ']' && $nesting === 0) { $end = $position; break; }
                if ($char === '[' || $char === '{') $nesting++;
                else $nesting--;
                if ($nesting < 0 || $depth + $nesting >= 512) throw new RuntimeException('Invalid clock sample nesting');
                if ($nesting === 0) {
                    if ($position + 1 - $last > 65536) throw new RuntimeException('Clock sample exceeds 64 KiB');
                    $last = $position + 1;
                    $count++;
                    if ($count >= 128 || $last >= 32768) { $flush = $last; break; }
                }
            }
            $length = $end !== null ? $end : $flush;
            if ($length !== null) {
                $raw = substr($buffer, 0, $length);
                $skip = strspn($raw, " \t\r\n");
                if ($skip < strlen($raw) && $previous) {
                    if ($raw[$skip] !== ',') throw new RuntimeException('Missing clock sample separator');
                    $skip++;
                    if (trim(substr($raw, $skip)) === '') throw new RuntimeException('Trailing clock sample separator');
                }
                $raw = substr($raw, $skip);
                if (strlen($raw) > 131072) throw new RuntimeException('Clock batch exceeds limit');
                $values = json_decode('[' . $raw . ']', true, 512 - $depth);
                if (json_last_error() !== JSON_ERROR_NONE) throw new RuntimeException('Invalid clock sample batch');
                if (count($values)) {
                    $ids[] = $this->remember($base + $skip, strlen($raw), $depth, $values, $refs);
                    $previous = true;
                }
                if ($end !== null) {
                    $reader->seek($base + $end + 1);
                    return $ids;
                }
                $base += $length;
                $buffer = substr($buffer, $length);
                $scan = $nesting = $count = $last = 0;
            } else {
                if (strlen($buffer) > 131072) throw new RuntimeException('Clock sample exceeds 64 KiB');
                $scan = $incomplete === null ? strlen($buffer) : $incomplete;
                if ($incomplete !== null) {
                    $chunk = @fread($this->handle, 8192);
                    if ($chunk === false || $chunk === '') throw new RuntimeException('Unterminated clock sample string');
                    $buffer .= $chunk;
                }
            }
        }
    }

    public function batch($id) {
        if (isset($this->cache[$id])) return $this->cache[$id];
        $location = $this->batches[$id];
        if (fseek($this->handle, $location[0]) !== 0) throw new RuntimeException('History seek failed');
        $raw = '';
        while (strlen($raw) < $location[1]) {
            $chunk = @fread($this->handle, $location[1] - strlen($raw));
            if ($chunk === false || $chunk === '') throw new RuntimeException('History read failed');
            $raw .= $chunk;
        }
        $value = json_decode($location[3] ? $raw : '[' . $raw . ']', true, 512 - $location[2]);
        if (json_last_error() !== JSON_ERROR_NONE) throw new RuntimeException('Invalid clock sample batch');
        if (count($this->cache) >= 3) {
            reset($this->cache);
            unset($this->cache[key($this->cache)]);
        }
        $this->cache[$id] = $location[3] ? array($value) : $value;
        return $this->cache[$id];
    }

    public function sample($ref) {
        $parts = explode(':', $ref);
        $values = $this->batch((int)$parts[2]);
        return $values[(int)$parts[3]];
    }

    public function references($clear = false) {
        $refs = array();
        $fast = true;
        $seen = array();
        $sequence = 0;
        foreach ($this->boards as &$board) {
            $bid = $board['id'];
            if (isset($seen[$bid]) || $bid < 0) $fast = false;
            $seen[$bid] = true;
            $previous = null;
            foreach ($board['refs'] as $ref) {
                $parts = explode(':', $ref);
                $t = (int)$parts[0];
                if ($t < 0 || ($previous !== null && $t <= $previous)) $fast = false;
                $previous = $t;
                $refs[] = sprintf('%020d:%020d:', $t, $clear ? $sequence++ : $bid) .
                    $parts[1] . ':' . $parts[2] . ($clear ? ':' . $bid : '');
            }
            unset($board['refs']);
        }
        unset($board);
        if ($clear || $fast) sort($refs, SORT_STRING);
        else usort($refs, function($a, $b) {
            $a = explode(':', $a); $b = explode(':', $b);
            $ta = (int)$a[0]; $tb = (int)$b[0];
            return $ta === $tb ? (int)$a[1] - (int)$b[1] : $ta - $tb;
        });
        return $refs;
    }

    public function copy($location, $out) {
        if (fseek($this->handle, $location[0]) !== 0) throw new RuntimeException('History seek failed');
        $remaining = $location[1];
        while ($remaining > 0) {
            $chunk = @fread($this->handle, min(8192, $remaining));
            if ($chunk === false || $chunk === '') throw new RuntimeException('History read failed');
            bs_clock_history_write($out, $chunk);
            $remaining -= strlen($chunk);
        }
    }
}

class BSClockHistorySamples implements Iterator, Countable {
    private $history;
    private $refs;
    private $position = 0;

    public function __construct($history, $refs) {
        $this->history = $history;
        $duplicate = false;
        for ($i = 1; $i < count($refs); $i++) {
            if (substr($refs[$i - 1], 0, 20) === substr($refs[$i], 0, 20)) $duplicate = true;
        }
        // Match the legacy second usort, including PHP 5.6's equal-time order.
        if ($duplicate) usort($refs, function($a, $b) { return (int)$a - (int)$b; });
        $this->refs = $refs;
    }
    public function count() { return count($this->refs); }
    public function rewind() { $this->position = 0; }
    public function key() { return $this->position; }
    public function next() { $this->position++; }
    public function valid() { return isset($this->refs[$this->position]); }
    public function current() { return $this->at($this->position); }
    public function at($index) {
        $sample = $this->history->sample($this->refs[$index]);
        if (isset($sample['pool'])) $sample['pool'] = clean_clock_pool_url($sample['pool']);
        return $sample;
    }
}

function bs_clock_history_runs($history, $refs) {
    $runs = array();
    $run = null;
    $start = 0;
    $last = 0;
    for ($i = 0; $i < count($refs);) {
        $begin = $i;
        $t = (int)$refs[$i];
        $rows = array();
        while ($i < count($refs) && (int)$refs[$i] === $t) {
            $parts = explode(':', $refs[$i]);
            $rows[] = array('board_id' => (int)$parts[1], 'sample' => $history->sample($refs[$i]));
            $i++;
        }
        if (!$t) continue;
        $profile = clock_time_profile_from_rows($rows);
        if ($profile === null) continue;
        if ($run === null || $run['run_key'] !== $profile['run_key']) {
            if ($run !== null) $runs[] = json_encode(array($run, $start, $last));
            $start = $begin;
            $run = array('id' => 'run-' . (count($runs) + 1), 'profile_key' => $profile['key'],
                'run_key' => $profile['run_key'], 'clock' => $profile['clock'],
                'experimental' => $profile['experimental'], 'recover' => $profile['recover'],
                'cutoff' => $profile['cutoff'], 'fan' => $profile['fan'], 'fan_mode' => $profile['fan_mode'],
                'start_ts' => $t, 'end_ts' => $t, 'active' => false, 'sample_count' => 0);
        }
        foreach (array('recover', 'cutoff', 'fan', 'fan_mode') as $key) $run[$key] = $profile[$key];
        $run['end_ts'] = $t;
        $run['sample_count'] += count($rows);
        $last = $i;
    }
    if ($run !== null) $runs[] = json_encode(array($run, $start, $last));
    return $runs;
}

function bs_clock_history_emit($path, $hours, $cutoff, $storage) {
    $history = new BSClockHistory($path, $cutoff);
    $step = max(1, (int)$history->field('step_seconds', 60));
    $updated = (int)$history->field('updated', 0);
    $refs = $history->references();
    $runs = bs_clock_history_runs($history, $refs);
    while ($buffer = ob_get_status()) {
        if ($buffer['name'] !== 'default output handler' ||
                ($buffer['chunk_size'] > 0 && $buffer['chunk_size'] <= 8192)) break;
        if (!@ob_end_flush()) break;
    }
    ob_start(null, 8192);
    echo '{"runs":[';
    $comma = '';
    for ($i = count($runs) - 1; $i >= 0; $i--) {
        $plan = json_decode($runs[$i], true);
        $run = $plan[0];
        $run['active'] = $i === count($runs) - 1 && $run['end_ts'] >= 0;
        $boards = array();
        for ($n = $plan[1]; $n < $plan[2]; $n++) {
            if (!(int)$refs[$n]) continue;
            $parts = explode(':', $refs[$n]);
            $boards[(int)$parts[1]][] = $refs[$n];
        }
        ksort($boards, SORT_NUMERIC);
        echo $comma, substr(json_encode($run), 0, -1), ',"boards":[';
        $board_comma = '';
        foreach ($boards as $bid => $board_refs) {
            $samples = new BSClockHistorySamples($history, $board_refs);
            $summary = clock_aggregate_board($bid, $samples);
            unset($summary['samples']);
            echo $board_comma, substr(json_encode($summary), 0, -1), ',"samples":[';
            $sample_comma = '';
            foreach ($samples as $sample) {
                echo $sample_comma, json_encode($sample);
                $sample_comma = ',';
            }
            echo ']}';
            $board_comma = ',';
        }
        echo '],"duration_seconds":', json_encode(max(0, $run['end_ts'] - $run['start_ts'])), '}';
        $comma = ',';
    }
    echo '],"window_hours":', json_encode($hours), ',"step_seconds":', json_encode($step),
        ',"updated":', json_encode($updated), ',"storage":', json_encode($storage),
        ',"read_path":', json_encode($path), '}';
}

function bs_clock_history_same_file($a, $b) {
    if (!$a || !$b) return !$a && !$b;
    foreach (array('dev', 'ino', 'size', 'mtime', 'ctime') as $key) {
        if ($a[$key] !== $b[$key]) return false;
    }
    return true;
}

function bs_clock_history_write($handle, $text) {
    $length = strlen($text);
    $offset = 0;
    while ($offset < $length) {
        $written = @fwrite($handle, substr($text, $offset));
        if ($written === false || $written === 0) throw new RuntimeException('Clock history write failed');
        $offset += $written;
    }
}

function bs_clock_history_json($value) {
    $text = json_encode($value);
    if ($text === false) throw new RuntimeException('Clock history encoding failed');
    return $text;
}

function bs_clock_history_target_times($history, $run_key) {
    $refs = $history->references(true);
    $targets = array();
    for ($i = 0; $i < count($refs);) {
        $t = (int)$refs[$i];
        $rows = array();
        while ($i < count($refs) && (int)$refs[$i] === $t) {
            $parts = explode(':', $refs[$i]);
            $rows[] = array('board_id' => (int)$parts[4], 'sample' => $history->sample($refs[$i]));
            $i++;
        }
        $profile = clock_time_profile_from_rows($rows);
        if ($profile !== null && $profile['run_key'] === $run_key) $targets[$t] = true;
    }
    return $targets;
}

function bs_clock_history_kept($history, $out, $ids, $start, $end, $profile_key, $run_key, $targets, &$removed) {
    bs_clock_history_write($out, '[');
    $comma = '';
    foreach ($ids as $id) {
        $kept = array();
        foreach ($history->batch($id) as $sample) {
            $remove = false;
            if (is_array($sample)) {
                $t = isset($sample['t']) ? (int)$sample['t'] : 0;
                $remove = $run_key !== '' ? isset($targets[$t]) :
                    ($t >= $start && $t <= $end && clock_sample_profile_matches($sample, $profile_key));
            }
            if ($remove) $removed++;
            else $kept[] = $sample;
        }
        if (count($kept)) {
            bs_clock_history_write($out, $comma . substr(bs_clock_history_json($kept), 1, -1));
            $comma = ',';
        }
    }
    bs_clock_history_write($out, ']');
}

function bs_clock_history_rewrite($history, $out, $start, $end, $profile_key, $run_key, $targets, $now, &$removed) {
    $fields = $history->fields;
    if (!isset($fields['boards'])) $fields['boards'] = null;
    $fields['updated'] = null;
    $replace = array('updated' => $now);
    foreach (array('version' => 1, 'step_seconds' => 60, 'max_age_seconds' => 7 * 86400) as $key => $default) {
        if ($history->field($key, null) === null) {
            $fields[$key] = null;
            $replace[$key] = $default;
        }
    }
    bs_clock_history_write($out, '{');
    $comma = '';
    foreach ($fields as $key => $location) {
        bs_clock_history_write($out, $comma . bs_clock_history_json((string)$key) . ':');
        if ($key === 'boards') {
            $keys = array_keys($history->boards);
            $list = !$keys || $keys === range(0, count($keys) - 1);
            bs_clock_history_write($out, $list ? '[' : '{');
            $board_comma = '';
            foreach ($history->boards as $board_key => $board) {
                bs_clock_history_write($out, $board_comma);
                if (!$list) bs_clock_history_write($out, bs_clock_history_json((string)$board_key) . ':');
                if ($board['batches'] === null) {
                    $history->copy($board['location'], $out);
                } else {
                    bs_clock_history_write($out, '{');
                    $field_comma = '';
                    foreach ($board['fields'] as $name => $field) {
                        bs_clock_history_write($out, $field_comma . bs_clock_history_json((string)$name) . ':');
                        if ($name === 'samples') {
                            bs_clock_history_kept($history, $out, $board['batches'], $start, $end,
                                $profile_key, $run_key, $targets, $removed);
                        } else {
                            $history->copy($field, $out);
                        }
                        $field_comma = ',';
                    }
                    bs_clock_history_write($out, '}');
                }
                $board_comma = ',';
            }
            bs_clock_history_write($out, $list ? ']' : '}');
        } elseif (array_key_exists($key, $replace)) {
            bs_clock_history_write($out, bs_clock_history_json($replace[$key]));
        } else {
            $history->copy($location, $out);
        }
        $comma = ',';
    }
    bs_clock_history_write($out, '}');
}

// Each rename is atomic. Stage every destination first, retain hard-link
// backups, and roll back completed renames if a later commit fails. The optional
// renamer is an internal test seam, never selected from request parameters.
function bs_clock_history_replace($paths, $writer, $unchanged, $renamer = 'rename') {
    $stages = array();
    $backups = array();
    $before = array();
    $committed = array();
    $installed = array();
    $success = false;
    try {
        foreach (array_unique($paths) as $path) {
            $dir = dirname($path);
            if (!is_dir($dir) && !@mkdir($dir, 0775, true)) throw new RuntimeException('Clock history directory unavailable');
            if (is_link($path) || is_dir($path)) throw new RuntimeException('Invalid clock history destination');
            clearstatcache(true, $path);
            $before[$path] = @stat($path);
            $tmp = @tempnam($dir, '.clock-history-');
            if ($tmp === false || dirname($tmp) !== realpath($dir)) {
                if ($tmp !== false) @unlink($tmp);
                throw new RuntimeException('Clock history staging failed');
            }
            $stages[$path] = $tmp;
            $handle = @fopen($tmp, 'wb');
            if ($handle === false) throw new RuntimeException('Clock history staging failed');
            try {
                if (count($stages) === 1) {
                    call_user_func($writer, $handle);
                } else {
                    $first = reset($stages);
                    $source = @fopen($first, 'rb');
                    if ($source === false) throw new RuntimeException('Clock history staging read failed');
                    try {
                        while (!feof($source)) {
                            $chunk = @fread($source, 8192);
                            if ($chunk === false) throw new RuntimeException('Clock history staging read failed');
                            bs_clock_history_write($handle, $chunk);
                        }
                    } finally { fclose($source); }
                }
                if (!fflush($handle)) throw new RuntimeException('Clock history flush failed');
            } finally { fclose($handle); }
            if (!@chmod($tmp, 0664)) throw new RuntimeException('Clock history permissions failed');
        }
        if (!call_user_func($unchanged)) throw new RuntimeException('Clock history changed during clear');
        foreach ($stages as $path => $tmp) {
            clearstatcache(true, $path);
            if (!bs_clock_history_same_file($before[$path], @stat($path))) throw new RuntimeException('Clock history destination changed');
            if ($before[$path]) {
                $backup = $tmp . '.old';
                if (!@link($path, $backup)) throw new RuntimeException('Clock history backup failed');
                $backups[$path] = $backup;
                // Creating a hard link changes ctime on the original inode.
                clearstatcache(true, $path);
                $before[$path] = @stat($path);
            }
        }
        foreach ($stages as $path => $tmp) {
            clearstatcache(true, $path);
            if (!bs_clock_history_same_file($before[$path], @stat($path))) throw new RuntimeException('Clock history destination changed');
            if (!@call_user_func($renamer, $tmp, $path)) throw new RuntimeException('Clock history commit failed');
            $committed[] = $path;
            clearstatcache(true, $path);
            $installed[$path] = @stat($path);
        }
        $success = true;
    } catch (RuntimeException $e) {
        error_log('clock-history: ' . $e->getMessage());
        foreach (array_reverse($committed) as $path) {
            clearstatcache(true, $path);
            if (!bs_clock_history_same_file($installed[$path], @stat($path))) {
                unset($backups[$path]);
                error_log('clock-history: rollback conflict; recovery backup retained');
                continue;
            }
            if (isset($backups[$path])) {
                if (!@rename($backups[$path], $path)) {
                    // Retain the backup if rollback itself fails.
                    unset($backups[$path]);
                    error_log('clock-history: rollback failed; recovery backup retained');
                }
            } else {
                if (!@unlink($path)) error_log('clock-history: rollback unlink failed');
            }
        }
    }
    foreach ($stages as $tmp) if (file_exists($tmp)) @unlink($tmp);
    foreach ($backups as $backup) if (file_exists($backup)) @unlink($backup);
    return $success;
}

function bs_clock_history_clear($path, $paths, $start, $end, $profile_key, $run_key, $now) {
    $lock = @fopen('/tmp/blakestream-combined-history.lock', 'c');
    if ($lock === false) return false;
    if (!flock($lock, LOCK_EX | LOCK_NB)) { fclose($lock); return false; }
    try {
        $history = new BSClockHistory($path, $run_key === '' ? null : $start, $end);
        if (!$history->valid) return false;
        $targets = $run_key === '' ? array() : bs_clock_history_target_times($history, $run_key);
        $removed = 0;
        $ok = bs_clock_history_replace($paths, function($out) use ($history, $start, $end, $profile_key, $run_key, $targets, $now, &$removed) {
            bs_clock_history_rewrite($history, $out, $start, $end, $profile_key, $run_key, $targets, $now, $removed);
        }, function() use ($history) { return $history->unchanged(); });
        return $ok ? array('ok' => true, 'removed_samples' => $removed) : false;
    } catch (RuntimeException $e) {
        error_log('clock-history: ' . $e->getMessage());
        return false;
    } finally {
        flock($lock, LOCK_UN);
        fclose($lock);
    }
}
