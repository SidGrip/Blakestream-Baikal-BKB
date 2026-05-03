<?php
session_start();

if ( !isset($_SESSION['_logged_']) || $_SESSION['_logged_'] === false ) {
	die();
}

/*
f_status gets values that people want to see in realtime
returns success, status data and errors

Blakestream-GaintB modification: include disabled (ascdisable'd) boards in
the devs list so the Status page can still show them with a Pool dropdown.
The factory version filtered them out, which made idle boards disappear
entirely from the UI.
*/
header('Content-type: application/json');

include('inc/cgminer.inc.php');

// Miner data
$devs=cgminer('devs');
$pools=cgminer('pools');
$stats=cgminer('stats');

if(!empty($devs['data']['DEVS'])){
  // Blakestream-GaintB: include ALL devices, not just Enabled=Y. Disabled
  // boards still appear in the UI so the user can re-enable them by
  // assigning a pool.
  foreach ($devs['data']['DEVS'] as $id => $vdev) {
    $r['status']['devs'][$id] = $vdev;
  }

  foreach ($r['status']['devs'] as $id => $dev) {
      $r['status']['devs'][$id]['Chips']=$stats['data']['STATS'][$id]["ChipCount"];
      $r['status']['devs'][$id]['Clock']=$stats['data']['STATS'][$id]["Clock"];
      $r['status']['devs'][$id]['Algo']=$stats['data']['STATS'][$id]["Algo"];
  }
}
else{
  $r['status']['devs'] = array();
}

if(!empty($pools['data']['POOLS'])){
  $r['status']['pools'] = $pools['data']['POOLS'];
  $r['status']['minerUp'] = true;
  $r['status']['minerDown'] = false;
}
else{
  $r['status']['pools'] = array();
  $r['status']['minerUp'] = false;
  $r['status']['minerDown'] = true;
}

// Debug miner data
if(!empty($_REQUEST['dev']) && $r['status']['minerUp']){
  $r['status']['devs'][]=array('Name'=>'Hoeba','ID'=>0,'Temperature'=>rand(20,35),'MHS5s'=>rand(80000,100000),'MHSav'=>rand(90000,100000),'LongPoll'=>'N','Getworks'=>200,'Accepted'=>rand(70,200),'Rejected'=>rand(1,10),'HardwareErrors'=>rand(0,50),'Utility'=>1.2,'LastShareTime'=>time()-rand(0,10));
  $r['status']['devs'][]=array('Name'=>'Debug','ID'=>1,'Temperature'=>rand(20,35),'MHS5s'=>rand(40000,50000),'MHSav'=>rand(45000,50000),'LongPoll'=>'N','Getworks'=>1076,'Accepted'=>1324,'Rejected'=>1,'HardwareErrors'=>46,'Utility'=>1.2,'LastShareTime'=>time()-rand(0,40));
}

$devices = 0;
$MHSav = 0;
$MHS5s = 0;
$Accepted = 0;
$Rejected = 0;
$HardwareErrors = 0;
$Utility = 0;

if(!empty($r['status']['devs'])){
  foreach ($r['status']['devs'] as $id => $dev) {
    // Compute TotalShares for ALL devices so the UI can render them.
    // Only count enabled+hashing devices in the totals.
    $r['status']['devs'][$id]['TotalShares']=$dev['Accepted']+$dev['Rejected']+$dev['HardwareErrors'];

    // Blakestream-GaintB: zero out MHSav for boards that are administratively
    // disabled AND not currently hashing. sgminer's API returns the cumulative
    // average since startup, which keeps showing a non-zero value (slowly
    // decaying) for a board that was hashing earlier in the session and has
    // since been idled. That's mathematically correct but visually misleading
    // ("idle board doing 200 Mh/s?"). For UI display purposes, treat
    // MHS5s == 0 AND Enabled == 'N' as truly idle and report Hashrate av = 0.
    // Underlying sgminer counters are unchanged (Total MH, Diff1 Work, etc).
    if ($dev['Enabled'] == 'N' && $dev['MHS5s'] == 0) {
      $r['status']['devs'][$id]['MHSav'] = 0;
    }

    if(($dev['Enabled'] == 'Y')) {
        $devices += $dev['MHS5s']>0?1:0; // Only count hashing devices
        $MHS5s += $dev['MHS5s'];
        $MHSav += $dev['MHSav'];
        $Accepted += $dev['Accepted'];
        $Rejected += $dev['Rejected'];
        $HardwareErrors += $dev['HardwareErrors'];
        $Utility += $dev['Utility'];
    }
  }
}


$ret = explode(' ',$MHS5s);
$KHS5s = ($ret[0]/1024). " Kh/s";
$ret = explode(' ',$MHSav);
$KHSav = ($ret[0]/1024). " Kh/s";

$r['status']['dtot']=array(
  'devices'=>$devices,
  'MHS5s'=>$MHS5s,
  'MHSav'=>$MHSav,
  'KHS5s'=>$KHS5s,
  'KHSav'=>$KHSav,
  'Accepted'=>$Accepted,
  'Rejected'=>$Rejected,
  'HardwareErrors'=>$HardwareErrors,
  'Utility'=>$Utility,
  'TotalShares'=>$Accepted+$Rejected+$HardwareErrors);

// CPU intensive stuff
if(!empty($_REQUEST['all'])){
  $ret = sys_getloadavg();
  $r['status']['pi']['load'] = $ret[2];
  $ret = explode(' ', exec('cat /proc/uptime'));
  $r['status']['pi']['uptime'] = $ret[0];
  $r['status']['pi']['temp'] = exec('cat /sys/class/thermal/thermal_zone0/temp');

  // What other interesting stuff is in summary?
  $summary=cgminer('summary');
  if(!empty($summary['data']['SUMMARY'][0]['Elapsed'])){
    $r['status']['uptime'] = $summary['data']['SUMMARY'][0]['Elapsed'];
  }
  else{
    $r['status']['uptime'] = 0;
  }
}

$r['status']['time'] = time();

echo json_encode($r);
?>
