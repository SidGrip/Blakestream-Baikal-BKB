<?php
/*
cgminer.inc.php
default command is summary
r.data
r.error
*/

function cgminerHardCtl($cmd){
openlog("myScriptLog", LOG_PID | LOG_PERROR, LOG_LOCAL0);
                syslog(LOG_WARNING, "Attempting to exec");
                closelog();
 switch ( $cmd ) {
        case '0':
            openlog("myScriptLog", LOG_PID | LOG_PERROR, LOG_LOCAL0);
            exec("sudo /opt/scripta/startup/miner-stop.sh");
            syslog(LOG_WARNING, "Executed exec");
            closelog();
        break;
        case '1':
            exec("sudo /opt/scripta/startup/miner-start.sh");
        break;
        }
    return true;
}


function cgminer($command='summary',$parameter=false){
  $c['command'] = $command;
  $c['parameter']=$parameter;
  $r = array(
    'data' => array(),
    'info' => array(),
  );

  $errno = 0;
  $errorMessage = '';

  // Keep dashboard requests bounded if sgminer's API accepts connections but
  // stops returning data. Without explicit timeouts, f_status.php can hang PHP.
  $client = @stream_socket_client('tcp://127.0.0.1:4028', $errno, $errorMessage, 1);

  if ($client === false) {
    $r['info'][]=array('type' => 'error', 'text' => 'Miner: '.$errno.' '.$errorMessage);
    return $r;
  }

  stream_set_timeout($client, 2);
  fwrite($client, json_encode($c));
  @stream_socket_shutdown($client, STREAM_SHUT_WR);

  $response = '';
  while (!feof($client)) {
    $chunk = fread($client, 8192);
    if ($chunk === false || $chunk === '') {
      $meta = stream_get_meta_data($client);
      if (!empty($meta['timed_out'])) {
        $r['info'][]=array('type' => 'error', 'text' => 'Miner: '.$command.' API timeout');
      }
      break;
    }
    $response .= $chunk;
  }
  fclose($client);

  $response = preg_replace('/[^[:alnum:][:punct:]]/','',$response);
  if ($response === '') {
    if (empty($r['info'])) {
      $r['info'][]=array('type' => 'error', 'text' => 'Miner: '.$command.' empty API response');
    }
    return $r;
  }

  $decoded = json_decode($response, true);
  if (!is_array($decoded)) {
    $r['info'][]=array('type' => 'error', 'text' => 'Miner: '.$command.' invalid API response');
    return $r;
  }

  $r['data'] = $decoded;
  $r['info'][]=array('type' => 'info', 'text' => 'Miner: '.$command);

  return $r;
}
?>
