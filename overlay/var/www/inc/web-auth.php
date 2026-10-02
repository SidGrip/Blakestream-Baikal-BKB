<?php

function bs_web_auth_password_valid($password) {
    return is_string($password) && strlen($password) > 0 && strlen($password) <= 72
        && strpos($password, "\0") === false;
}

function bs_web_auth_hash($password) {
    if (!bs_web_auth_password_valid($password) || !function_exists('password_hash')) return false;
    try {
        $hash = @password_hash($password, PASSWORD_BCRYPT, array('cost' => 10));
    } catch (Exception $e) {
        return false;
    } catch (Error $e) {
        return false;
    }
    return is_string($hash) && preg_match('/\A\$2y\$10\$[.\/A-Za-z0-9]{53}\z/', $hash) ? $hash : false;
}

function bs_web_auth_verify($password, $record) {
    if (!bs_web_auth_password_valid($password) || !is_string($record)
        || !function_exists('password_verify')) return false;
    if (!preg_match('/\Ascripta:(\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[.\/A-Za-z0-9]{53})(?:\r?\n)?\z/', $record, $match)) return false;
    return password_verify($password, $match[1]);
}

function bs_web_auth_check($path, $password) {
    if (!bs_web_auth_password_valid($password) || is_link($path) || !is_file($path)) return false;
    return bs_web_auth_verify($password, @file_get_contents($path, false, null, 0, 128));
}

function bs_web_auth_save($path, $password) {
    if (!bs_web_auth_password_valid($password) || is_link($path)
        || (file_exists($path) && !is_file($path))) return false;
    $dir = dirname($path);
    if (!is_dir($dir) || !is_writable($dir)) return false;
    $hash = bs_web_auth_hash($password);
    if ($hash === false) return false;
    $record = 'scripta:' . $hash;
    $tmp = @tempnam($dir, '.web-auth-');
    if ($tmp === false) return false;
    $ok = @file_put_contents($tmp, $record, LOCK_EX) === strlen($record)
        && @chmod($tmp, 0600) && !is_link($path) && @rename($tmp, $path);
    if (!$ok) @unlink($tmp);
    return $ok;
}

function bs_web_auth_redact_fields($text, $replacement = 'pw="[REDACTED]"') {
    // Legacy quotes were replaced before logging; backslashes are not escapes.
    $pattern = '/\bpw[ \t]*=[ \t]*(?:"[^"]*(?:"|$)|\'[^\']*(?:\'|$)|[^\s]*)/i';
    $tokens = preg_match_all('/\bpw[ \t]*=/i', $text, $unused);
    $fields = preg_match_all($pattern, $text, $unused);
    if ($tokens === false || $fields === false) return null;
    // Untrusted legacy headers can nest a real password field inside a fake one.
    // Its quoting is ambiguous: retain the event prefix, discard the unsafe tail.
    if ($tokens !== $fields) return preg_replace('/\bpw[ \t]*=[\s\S]*$/i', $replacement, $text, 1);
    return preg_replace($pattern, $replacement, $text);
}

function bs_web_auth_redact_log($text) {
    if (!is_string($text)) return false;
    $lines = explode("\n", $text);
    foreach ($lines as $i => $line) {
        $body = rtrim($line, "\r");
        if ($body === '' || $body === '... trimmed ...' || $body === '[REDACTED legacy fragment]') continue;
        $known = preg_match('/\A(?:\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (?:web login-(?:OK|FAIL)\b|devtools\b)|ssh \d{4}-\d{2}-\d{2}T\S+ )/', $line);
        // A byte-trimmed fragment may begin inside a password without its key.
        $lines[$i] = $known ? bs_web_auth_redact_fields($line)
            : '[REDACTED legacy fragment]' . (substr($line, -1) === "\r" ? "\r" : '');
        if ($lines[$i] === null) return false;
    }
    return implode("\n", $lines);
}

function bs_web_auth_metadata($text, $password, $limit) {
    if (!is_string($text) || strlen($text) > 4096 || !bs_web_auth_password_valid($password)) return '-';
    $text = str_replace($password, '[REDACTED]', $text);
    $text = preg_replace('/[^\x20-\x7e]/', ' ', $text);
    $normalized = preg_replace('/[^\x20-\x7e]/', ' ', $password);
    if ($normalized !== '') $text = str_replace($normalized, '[REDACTED]', $text);
    $text = bs_web_auth_redact_fields($text, '[REDACTED]');
    if ($text === null) return '-';
    return str_replace(array('\\', '"'), array('\\\\', '\\"'), substr($text, 0, $limit));
}

function bs_web_auth_audit_line($success, $server, $password) {
    if (!is_bool($success) || !is_array($server)) return false;
    $ip = isset($server['REMOTE_ADDR']) && is_string($server['REMOTE_ADDR'])
        && filter_var($server['REMOTE_ADDR'], FILTER_VALIDATE_IP) !== false ? $server['REMOTE_ADDR'] : '-';
    if (is_string($password) && $password !== '') $ip = str_replace($password, '[REDACTED]', $ip);
    $xff = array();
    if (bs_web_auth_password_valid($password) && isset($server['HTTP_X_FORWARDED_FOR'])
        && is_string($server['HTTP_X_FORWARDED_FOR']) && strlen($server['HTTP_X_FORWARDED_FOR']) <= 512) {
        foreach (explode(',', $server['HTTP_X_FORWARDED_FOR']) as $address) {
            $address = trim($address);
            if (filter_var($address, FILTER_VALIDATE_IP) === false || count($xff) >= 8) { $xff = array(); break; }
            $xff[] = str_replace($password, '[REDACTED]', $address);
        }
    }
    $ua = bs_web_auth_metadata(isset($server['HTTP_USER_AGENT']) ? $server['HTTP_USER_AGENT'] : '-', $password, 120);
    return date('Y-m-d H:i:s') . ' web ' . ($success ? 'login-OK' : 'login-FAIL') . ' ip=' . $ip
        . ($xff ? ' xff=' . implode(',', $xff) : '') . ' ua="' . $ua . '"' . "\n";
}

function bs_web_auth_write_all($fp, $text) {
    $length = strlen($text);
    $offset = 0;
    while ($offset < $length) {
        $written = @fwrite($fp, substr($text, $offset));
        if ($written === false || $written === 0) return false;
        $offset += $written;
    }
    return true;
}

function bs_web_auth_audit_locked($fp, $line, $transform = null) {
    if (@fseek($fp, 0) !== 0) return false;
    $raw = @stream_get_contents($fp, 4194305);
    if ($raw === false || strlen($raw) > 4194304 || !feof($fp)) return false;
    $clean = bs_web_auth_redact_log($raw);
    if ($clean === false) return false;
    if ($transform !== null) {
        try {
            $clean = call_user_func($transform, $clean);
        } catch (Exception $e) {
            return false;
        } catch (Error $e) {
            return false;
        }
        if (!is_string($clean) || strlen($clean) > 4194304) return false;
        $clean = bs_web_auth_redact_log($clean);
        if ($clean === false) return false;
    }
    $line = bs_web_auth_redact_log($line);
    if ($line === false) return false;
    if ($line !== '' && strlen($clean) > 262144) {
        $boundary = strpos($clean, "\n", strlen($clean) - 131072);
        $clean = "... trimmed ...\n" . ($boundary === false ? '' : substr($clean, $boundary + 1));
    }
    if ($line !== '' && $clean !== '' && substr($clean, -1) !== "\n") $clean .= "\n";
    if ($clean === $raw) {
        return $line === '' || (bs_web_auth_write_all($fp, $line) && @fflush($fp));
    }
    $out = $clean . $line;
    return @fseek($fp, 0) === 0 && bs_web_auth_write_all($fp, $out) && @fflush($fp)
        && @ftruncate($fp, strlen($out)) && @fflush($fp);
}

function bs_web_auth_audit_lock($path) {
    // All writers use this stable sidecar, including writers that replace the log.
    $lockPath = $path . '.lock';
    if (is_link($lockPath) || (file_exists($lockPath) && !is_file($lockPath))) return false;
    $new = !file_exists($lockPath);
    $lock = @fopen($lockPath, 'c+b');
    if ($lock === false) return false;
    if (($new && !@chmod($lockPath, 0660)) || !@flock($lock, LOCK_EX)) {
        @fclose($lock);
        return false;
    }
    $stat = @fstat($lock);
    if ($stat === false || ($stat['mode'] & 0170000) !== 0100000) {
        bs_web_auth_audit_unlock($lock);
        return false;
    }
    return $lock;
}

function bs_web_auth_audit_unlock($lock) {
    @flock($lock, LOCK_UN);
    @fclose($lock);
}

function bs_web_auth_update_audit($path, $line, $transform = null) {
    if (!is_string($line) || strlen($line) > 16384 || is_link($path)
        || (file_exists($path) && !is_file($path)) || ($transform !== null && !is_callable($transform))) return false;
    $lock = bs_web_auth_audit_lock($path);
    if ($lock === false) return false;
    clearstatcache(true, $path);
    $ok = false;
    if (is_link($path) || (file_exists($path) && !is_file($path))) {
        $ok = false;
    } elseif ($line === '' && $transform === null && !file_exists($path)) {
        $ok = true;
    } else {
        $fp = @fopen($path, 'c+b');
        if ($fp !== false) {
            $stat = @fstat($fp);
            $ok = $stat !== false && ($stat['mode'] & 0170000) === 0100000
                && bs_web_auth_audit_locked($fp, $line, $transform);
            @fclose($fp);
        }
    }
    bs_web_auth_audit_unlock($lock);
    return $ok;
}

function bs_security_log_update($path, $line = null, $transform = null) {
    return bs_web_auth_update_audit($path, $line === null ? '' : $line, $transform);
}

function bs_web_auth_scrub_log($path) {
    return bs_web_auth_update_audit($path, '');
}

function bs_web_auth_log($path, $success, $server, $password) {
    $line = bs_web_auth_audit_line($success, $server, $password);
    return $line !== false && bs_web_auth_update_audit($path, $line);
}
