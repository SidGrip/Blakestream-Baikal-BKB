<?php
/*
 * Dev-only router for previewing the Scripta GUI locally with `php -S`.
 * Auto-authenticates (so we skip login) and serves the www tree. The live data
 * endpoints (f_*.php) talk to a real miner's sgminer API; locally they just
 * degrade to empty/error, which is fine for UI/layout work. NOT for deployment.
 *
 * Run:  php -S 0.0.0.0:8088 -t overlay/var/www dev-gui-router.php
 */
session_start();
$_SESSION['_logged_'] = true;     // bypass login.php gate
$_SESSION['_user_']   = 'dev';

$docroot = realpath($_SERVER['DOCUMENT_ROOT']);
$path = urldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH));
if ($path === '/' || $path === '') $path = '/index.php';

$file = realpath($docroot . $path);
if ($file === false || strpos($file, $docroot) !== 0) {
    http_response_code(404);
    echo "dev-router: not found: " . htmlspecialchars($path);
    return true;
}
if (substr($file, -4) === '.php') {
    chdir(dirname($file));
    require $file;
    return true;
}
return false; // static asset (css/js/html/img) — let PHP serve it
