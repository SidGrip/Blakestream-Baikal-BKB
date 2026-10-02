<?php
function expect($ok, $message) { if (!$ok) { fwrite(STDERR, "FAIL $message\n"); exit(1); } }
$tmp = sys_get_temp_dir() . '/bkb-diagnostic-' . uniqid();
mkdir($tmp); mkdir($tmp . '/ram'); mkdir($tmp . '/sd');
register_shutdown_function(function() use ($tmp) {
    foreach (array('ram','sd') as $dir) {
        foreach (glob($tmp . '/' . $dir . '/*') as $file) unlink($file);
        rmdir($tmp . '/' . $dir);
    }
    foreach (glob($tmp . '/*') as $file) unlink($file);
    rmdir($tmp);
});
$source = file_get_contents(__DIR__ . '/../overlay/var/www/inc/diagnostic-logs.php');
eval(str_replace(array('<?php', '/run/blakestream/', '/opt/scripta/var/'), array('', $tmp.'/ram/', $tmp.'/sd/'), $source));
$conf = $tmp . '/scripta.conf';
file_put_contents($conf, json_encode(array('unrelated'=>'retain')));
foreach (bs_diagnostic_logs() as $name => $spec) {
    expect(bs_diagnostic_target($spec, array()) === 'RAM', 'default RAM');
    expect(bs_diagnostic_source_path($spec['source'], array()) === $tmp.'/ram/'.$name, 'source follows RAM');
    expect(bs_diagnostic_source_path($spec['source'], array($spec['key']=>'SD')) === $tmp.'/sd/'.$name, 'source follows SD');
    $ram = bs_diagnostic_path($name, 'RAM'); $sd = bs_diagnostic_path($name, 'SD');
    file_put_contents($ram, "event fixture\n");
    expect(bs_diagnostic_set_target($name, 'SD', $conf)['ok'], 'move to SD');
    expect(!file_exists($ram) && file_get_contents($sd) === "event fixture\n", 'SD keeps events and retires RAM');
    $cfg = json_decode(file_get_contents($conf), true);
    expect($cfg[$spec['key']] === 'SD' && $cfg['unrelated'] === 'retain', 'preserve other settings');
    file_put_contents($sd, "second event\n", FILE_APPEND);
    expect(bs_diagnostic_set_target($name, 'RAM', $conf)['ok'], 'move back to RAM');
    expect(!file_exists($sd) && file_get_contents($ram) === "event fixture\nsecond event\n", 'RAM keeps both events');
    expect(bs_diagnostic_clear($ram) === 0 && file_get_contents($ram) === '', 'clear active file');
    expect(bs_diagnostic_set_target($name, 'SD', $conf)['ok'], 'empty file migration');
    expect(bs_diagnostic_set_target($name, 'SD', $conf)['ok'], 'repeat target no-op');
}
expect(bs_diagnostic_source_path('arbitrary', array()) === null, 'unknown source rejected');
expect(!bs_diagnostic_set_target('../security.log', 'SD', $conf)['ok'], 'unknown file rejected');
expect(!bs_diagnostic_set_target('network-events.log', '/tmp', $conf)['ok'], 'invalid target rejected');
$ram = bs_diagnostic_path('network-events.log', 'RAM');
$sd = bs_diagnostic_path('network-events.log', 'SD');
file_put_contents($sd, 'keep this');
$lock = bs_diagnostic_lock();
expect($lock !== false, 'lock acquired');
expect(!bs_diagnostic_set_target('network-events.log', 'RAM', $conf)['ok'], 'concurrent move rejected');
expect(bs_diagnostic_clear($sd) === false, 'clear cannot race collector');
bs_diagnostic_unlock($lock);
symlink($conf, $ram);
expect(!bs_diagnostic_set_target('network-events.log', 'RAM', $conf)['ok'], 'symlink rejected');
expect(file_get_contents($sd) === 'keep this', 'failed move retains source');
unlink($ram);
file_put_contents($conf, '{invalid');
expect(!bs_diagnostic_set_target('network-events.log', 'RAM', $conf)['ok'], 'bad config rejected without reset');
expect(file_get_contents($sd) === 'keep this' && file_get_contents($conf) === '{invalid', 'failed settings preserve originals');
echo "PASS diagnostic log target validation, migration, locking, clearing and failure retention\n";
