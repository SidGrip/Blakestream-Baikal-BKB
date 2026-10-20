<?php

// PHP 5.6: keep board metadata/offsets and bounded sample batches in memory. Scalar
// syntax, escapes and UTF-8 are validated by PHP's own JSON decoder.
class BSHistoryJsonReader {
    const VALUE_LIMIT = 65536;
    private $handle;
    private $buffer = '';
    private $position = 0;
    private $base = 0;

    public function __construct($handle) {
        $this->handle = $handle;
    }

    public function peek() {
        if ($this->position === strlen($this->buffer)) {
            $this->base += $this->position;
            $this->position = 0;
            $this->buffer = @fread($this->handle, 8192);
            if ($this->buffer === false) throw new RuntimeException('History read failed');
        }
        return $this->buffer === '' ? '' : $this->buffer[$this->position];
    }

    public function whitespace() {
        while ($this->peek() !== '') {
            $length = strspn($this->buffer, " \t\r\n", $this->position);
            if (!$length) break;
            $this->position += $length;
        }
    }

    public function offset() {
        return $this->base + $this->position;
    }

    public function seek($offset) {
        if (fseek($this->handle, $offset) !== 0) throw new RuntimeException('History seek failed');
        $this->buffer = '';
        $this->position = 0;
        $this->base = $offset;
    }

    private function consume($expected) {
        $this->whitespace();
        if ($this->peek() !== $expected) throw new RuntimeException('Invalid history JSON');
        $this->position++;
    }

    private function scalar() {
        $raw = '';
        if ($this->peek() === '"') {
            $raw = '"';
            $this->position++;
            while (true) {
                if ($this->peek() === '') throw new RuntimeException('Unterminated history string');
                $length = strcspn($this->buffer, '"\\', $this->position);
                $raw .= substr($this->buffer, $this->position, $length);
                if (strlen($raw) > self::VALUE_LIMIT) throw new RuntimeException('History value too large');
                $this->position += $length;
                $char = $this->peek();
                if ($char === '') throw new RuntimeException('Unterminated history string');
                $raw .= $char;
                $this->position++;
                if ($char === '"') break;
                if ($char === '\\') {
                    $char = $this->peek();
                    if ($char === '') throw new RuntimeException('Unterminated history escape');
                    $raw .= $char;
                    $this->position++;
                }
            }
        } else {
            while ($this->peek() !== '') {
                $length = strcspn($this->buffer, " \t\r\n,]}", $this->position);
                if (!$length) break;
                $raw .= substr($this->buffer, $this->position, $length);
                if (strlen($raw) > self::VALUE_LIMIT) throw new RuntimeException('History value too large');
                $this->position += $length;
            }
        }
        if ($raw === '') throw new RuntimeException('Missing history value');
        if (strlen($raw) > self::VALUE_LIMIT) throw new RuntimeException('History value too large');
        // PHP 5.6 accepts some invalid bare numbers (e.g. 01); validate in
        // container context, as the original whole-document decode did.
        $value = json_decode('[' . $raw . ']', true);
        if (json_last_error() !== JSON_ERROR_NONE) throw new RuntimeException('Invalid history scalar');
        return $value[0];
    }

    private function compound($depth) {
        $raw = '';
        $nesting = 0;
        $string = false;
        do {
            if ($this->peek() === '') throw new RuntimeException('Unterminated history value');
            $length = strcspn($this->buffer, $string ? '"\\' : '[]{}"', $this->position);
            $raw .= substr($this->buffer, $this->position, $length);
            if (strlen($raw) > self::VALUE_LIMIT) throw new RuntimeException('History value too large');
            $this->position += $length;
            $char = $this->peek();
            if ($char === '') throw new RuntimeException('Unterminated history value');
            $raw .= $char;
            $this->position++;
            if ($char === '"') {
                $string = !$string;
            } elseif ($string && $char === '\\') {
                $char = $this->peek();
                if ($char === '') throw new RuntimeException('Unterminated history escape');
                $raw .= $char;
                $this->position++;
            } elseif (!$string) {
                if ($char === '[' || $char === '{') $nesting++;
                if ($char === ']' || $char === '}') $nesting--;
                if ($depth + $nesting >= 512) throw new RuntimeException('History JSON too deep');
            }
        } while ($nesting > 0 || $string);
        if (strlen($raw) > self::VALUE_LIMIT) throw new RuntimeException('History value too large');
        // Decode a complete sample in C, not each individual scalar in PHP.
        $value = json_decode($raw, true, 512 - $depth);
        if (json_last_error() !== JSON_ERROR_NONE) throw new RuntimeException('Invalid history value');
        return $value;
    }

    // The callback consumes exactly one value; object keys follow json_decode's
    // last-value-wins behavior when assigned to PHP arrays by callers.
    public function members($callback, $depth = 0) {
        $this->whitespace();
        $open = $this->peek();
        if ($open !== '[' && $open !== '{') {
            $this->value(false, $depth);
            return;
        }
        if ($depth >= 511) throw new RuntimeException('History JSON too deep');
        $close = $open === '[' ? ']' : '}';
        $this->position++;
        $this->whitespace();
        if ($this->peek() === $close) {
            $this->position++;
            return;
        }
        $index = 0;
        while (true) {
            $key = $index++;
            if ($open === '{') {
                $this->whitespace();
                if ($this->peek() !== '"') throw new RuntimeException('Invalid history key');
                $key = $this->scalar();
                $this->consume(':');
            }
            $this->whitespace();
            call_user_func($callback, $this, $key, $depth + 1);
            $this->whitespace();
            if ($this->peek() === $close) {
                $this->position++;
                return;
            }
            $this->consume(',');
        }
    }

    public function value($keep = true, $depth = 0) {
        $this->whitespace();
        $char = $this->peek();
        if ($char !== '[' && $char !== '{') return $this->scalar();
        if ($keep) return $this->compound($depth);
        $out = array();
        $this->members(function($reader, $key, $child_depth) use (&$out, $keep) {
            $value = $reader->value($keep, $child_depth);
            if ($keep) $out[$key] = $value;
        }, $depth);
        return $out;
    }

    public function sampleBatches($callback = null, $depth = 0) {
        $this->whitespace();
        if ($this->peek() !== '[') {
            $this->members(function($reader, $key, $child_depth) use ($callback) {
                $sample = $reader->value(true, $child_depth);
                if ($callback !== null) call_user_func($callback, array($sample));
            }, $depth);
            return;
        }
        if ($depth >= 511) throw new RuntimeException('History JSON too deep');
        $this->position++;
        $this->whitespace();
        if ($this->peek() === ']') {
            $this->position++;
            return;
        }
        // Recorder pairs contain only numbers/null. Select complete pairs in
        // the current bounded buffer, then let native json_decode validate
        // their syntax together. Other shapes use the general reader.
        $pair = '\\[[0-9eE+., \\t\\r\\nnulltruefals-]*\\]';
        $pattern = '/\\G(' . $pair . '(?:\\s*,\\s*' . $pair . '){0,127})(?=\\s*[,\\]])/';
        while (true) {
            $this->whitespace();
            if (preg_match($pattern, $this->buffer, $match, 0, $this->position)) {
                $batch = json_decode('[' . $match[1] . ']', true, 512 - $depth);
                if (json_last_error() !== JSON_ERROR_NONE) throw new RuntimeException('Invalid history samples');
                $this->position += strlen($match[1]);
            } else {
                $batch = array($this->value(true, $depth + 1));
            }
            if ($callback !== null) call_user_func($callback, $batch);
            $this->whitespace();
            if ($this->peek() === ']') {
                $this->position++;
                return;
            }
            $this->consume(',');
        }
    }
}

function bs_hashrate_history_index($reader) {
    $history = array();
    $reader->members(function($reader, $key, $depth) use (&$history) {
        if ($key === 'boards') {
            $boards = array();
            $reader->members(function($reader, $key, $depth) use (&$boards) {
                $board = array();
                $reader->members(function($reader, $key, $depth) use (&$board) {
                    if ($key === 'samples' || $key === 'accepted_samples') {
                        $board[$key] = array($reader->offset(), $depth);
                        $reader->sampleBatches(null, $depth);
                    } elseif (in_array($key, array('id', 'enabled', 'primary_pool_no',
                            'effective_pool_no', 'failover_active', 'pool_url'), true)) {
                        $board[$key] = $reader->value(true, $depth);
                    } else {
                        $reader->value(false, $depth);
                    }
                }, $depth);
                $boards[$key] = $board;
            }, $depth);
            $history['boards'] = array_values($boards);
        } elseif ($key === 'updated' || $key === 'step_seconds') {
            $history[$key] = $reader->value(true, $depth);
        } else {
            $reader->value(false, $depth);
        }
    });
    $reader->whitespace();
    if ($reader->peek() !== '') throw new RuntimeException('Trailing history data');
    return $history;
}

function bs_hashrate_history_samples($reader, $location, $cutoff, $accepted) {
    if ($location === null) return;
    $reader->seek($location[0]);
    $comma = '';
    $emit_batch = function($batch) use ($cutoff, $accepted, &$comma) {
        $window = array();
        foreach ($batch as $s) {
            if (is_array($s) && count($s) >= 2 && (int)$s[0] >= $cutoff) {
                $value = $accepted ? ($s[1] === null ? null : (float)$s[1]) : (int)$s[1];
                $window[] = array((int)$s[0], $value);
            }
        }
        if ($window) {
            echo $comma, substr(json_encode($window), 1, -1);
            $comma = ',';
        }
    };
    if ($reader->peek() === '{') {
        // Legacy object-form collections need key replacement semantics. The
        // recorder writes arrays, whose samples require no per-sample index.
        $offsets = array();
        $reader->members(function($reader, $key, $depth) use (&$offsets) {
            $offsets[$key] = array($reader->offset(), $depth);
            $reader->value(false, $depth);
        }, $location[1]);
        foreach ($offsets as $key => $offset) {
            $reader->seek($offset[0]);
            $emit_batch(array($reader->value(true, $offset[1])));
        }
    } else {
        $reader->sampleBatches($emit_batch, $location[1]);
    }
}

function bs_hashrate_history_emit($path, $hours, $cutoff, $pool_name_by_url) {
    $history = array();
    $handle = @fopen($path, 'rb');
    $reader = null;
    if ($handle !== false) {
        $reader = new BSHistoryJsonReader($handle);
        try {
            // Validate before sending any bytes, preserving the empty-history
            // fallback on corrupt files. Keep this descriptor across both passes
            // so an atomic collector rename cannot mix history generations.
            $history = bs_hashrate_history_index($reader);
        } catch (RuntimeException $e) {
            error_log('Hashrate history unavailable: ' . $e->getMessage());
            $history = array();
        }
    }
    $boards = isset($history['boards']) ? $history['boards'] : array();
    foreach ($boards as &$board) {
        $board['id'] = isset($board['id']) ? (int)$board['id'] : 0;
    }
    unset($board);
    usort($boards, function($a, $b) { return $a['id'] - $b['id']; });
    echo '{"boards":[';
    $comma = '';
    foreach ($boards as $b) {
        $url = isset($b['pool_url']) ? $b['pool_url'] : '';
        $clean = preg_replace('|^https?://|', '', $url);
        $clean = preg_replace('|^quota:\d+;|', '', $clean);
        $name = isset($pool_name_by_url[$clean]) ? $pool_name_by_url[$clean] : null;
        $metadata = array(
            'id' => $b['id'],
            'enabled' => isset($b['enabled']) ? (bool)$b['enabled'] : true,
            'primary_pool_no' => isset($b['primary_pool_no']) ? (int)$b['primary_pool_no'] : -1,
            'effective_pool_no' => isset($b['effective_pool_no']) ? (int)$b['effective_pool_no'] : -1,
            'failover_active' => isset($b['failover_active']) ? (bool)$b['failover_active'] : false,
            'pool_url' => $clean,
            'pool_name' => $name,
        );
        echo $comma, substr(json_encode($metadata), 0, -1), ',"samples":[';
        bs_hashrate_history_samples($reader, isset($b['samples']) ? $b['samples'] : null, $cutoff, false);
        echo '],"accepted_samples":[';
        bs_hashrate_history_samples($reader, isset($b['accepted_samples']) ? $b['accepted_samples'] : null, $cutoff, true);
        echo ']}';
        $comma = ',';
    }
    echo '],"window_hours":', json_encode($hours),
        ',"step_seconds":', json_encode(isset($history['step_seconds']) ? (int)$history['step_seconds'] : 60),
        ',"updated":', json_encode(isset($history['updated']) ? (int)$history['updated'] : 0), '}';
    if ($handle !== false) fclose($handle);
}
