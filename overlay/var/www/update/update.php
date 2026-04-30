<?php
// No-op stub. The factory auto-updater is replaced because its update path
// was a placeholder URL and represented unused dead code.
class AutoUpdate {
    public function __construct($log = false) {}
    public function checkUpdate() { return false; }
    public function update() { return false; }
    public function getLastError() { return ''; }
}
