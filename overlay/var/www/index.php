<?php
session_start();
if (!isset($_SESSION['_logged_']) || $_SESSION['_logged_'] === false) {
    header('Location: login.php' . (isset($_GET['network']) && $_GET['network'] === '1' ? '?network=1' : ''));
    die();
}
if (empty($_SESSION['bs_view_session'])) $_SESSION['bs_view_session'] = bin2hex(openssl_random_pseudo_bytes(16));
?>
<!DOCTYPE html>
<html lang="en" ng-app="Scripta">
<head>
    <meta charset="utf-8">
    <title>Blakestream-GaintB v2.2</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="Blakestream miner controller">
    <meta name="author" content="Blakestream">
    <link rel="shortcut icon" type="image/x-icon" href="img/favicon.ico"/>
    <link href="css/bootstrap.min.css" rel="stylesheet">
    <link href="css/font-awesome.min.css" rel="stylesheet">
    <link rel="stylesheet" type="text/css" href="css/shellinabox.css">

    <link href="css/custom.css" rel="stylesheet">
    <link rel="stylesheet" href="css/ionicons.min.css">
    <link rel="stylesheet" href="css/AdminLTE.min.css">
    <link rel="stylesheet" href="css/skins/skin-blue.min.css">

    <link href="css/theme.css" rel="stylesheet">
    <link href="css/alertify.css" rel="stylesheet">
    <link href='css/css.css' rel="stylesheet" type='text/css'>
    <link href="css/blakestream-dark.css?v=collector-wait-1" rel="stylesheet">
    <script src="js/blakestream-theme.js?v=wifi-ui-1"></script>
    <script src="js/blakestream-tooltips.js?v=20260930-clock-compact"></script>
</head>


<body class="hold-transition skin-blue layout-top-nav bs-dark" ng-controller="CtrlMain" data-view-session="<?php echo htmlspecialchars($_SESSION['bs_view_session'], ENT_QUOTES, 'UTF-8'); ?>">
<script>window.BlakestreamTheme.init();</script>
<header class="main-header scripta-trigger">
    <nav class="navbar navbar-static-top">
        <div class="container-fluid">
            <div class="navbar-header">
                <a href="index.php" class="navbar-brand"><b>Blake</b>stream</a>
                <button type="button" class="navbar-toggle collapsed" data-toggle="collapse"
                    data-target="#navbar-collapse">
                <i class="fa fa-bars"></i>
                </button>
            </div>

            <!-- Collect the nav links, forms, and other content for toggling -->
            <div class="collapse navbar-collapse" id="navbar-collapse">
                <ul class="nav navbar-nav">

                    <li menu-active>
                        <a ng-href="#/status">
                        <b>{{title}}</b>
                        </a>
                    </li>
                    <li menu-active><a ng-href="#/miner">Miner</a></li>
                    <li menu-active><a ng-href="#/settings">Settings</a></li>
                    <li menu-active><a ng-href="#/backup">Backup</a></li>
                    <li menu-active><a ng-href="#/pool-history">Pool History</a></li>
                    <li menu-active><a ng-href="#/failover-history">Failover History</a></li>
                    <li menu-active ng-show="settings.userAdjustableClocks"><a ng-href="#/clock-history">Clock History</a></li>
                    <li menu-active><a ng-href="#/terminal">Terminal</a></li>
                    <li menu-active ng-show="settings.userDev"><a ng-href="#/dev-tools">Dev Tools</a></li>

                </ul>
                <ul class="nav navbar-nav navbar-right">

                    <span class="navbar-text scripta-more">{{counter}}s</span>

                    <button class="btn btn-danger btn-flat navbar-btn ng-cloak" ng-show="downNow"
                        title="Hopefully it's restarting">
                    {{downTime}}s downtime
                    </button>

                    <button class="btn btn-default btn-flat navbar-btn bs-navbar-action bs-tip bs-tip-down bs-tip-right" ng-click="tick(1,1)"
                        aria-label="Refresh now" data-tip="Auto refresh in {{counter}}s">
                    <i class="fa fa-refresh " ng-class="{'fa-spin':counter<2}"></i>
                    </button>

                    <button class="btn btn-default btn-flat navbar-btn bs-theme-toggle bs-tip bs-tip-down bs-tip-right" type="button"
                        aria-label="Toggle dark / light mode" data-tip="Toggle dark / light mode"
                        onclick="if(window.BlakestreamTheme){window.BlakestreamTheme.toggle();}else{document.body.classList.toggle('bs-dark');try{localStorage.setItem('bsTheme',document.body.classList.contains('bs-dark')?'dark':'light');}catch(e){}}">☾</button>

                    <span class="navbar-btn bs-connection-indicator bs-tip bs-tip-down bs-tip-right" role="img"
                          data-tip="{{bsConnectionLabel()}}" aria-label="{{bsConnectionLabel()}}">
                        <i class="fa" ng-class="bsConnectionIcon()" aria-hidden="true"></i>
                    </span>

                    <button class="btn btn-default btn-flat navbar-btn bs-navbar-action"
                        onclick="location.href='f_logout.php'">
                    Logout
                    </button>

                </ul>
            </div>
            <!-- /.navbar-collapse -->
        </div>
        <!-- /.container-fluid -->
    </nav>
</header>
<div class="container" ng-class="{down:status.minerDown}">
    <div ng-view>
        <h1 class="text-center">
        Loading Blakestream-GaintB...
        </h1>
    </div>
</div>

<!-- Blakestream-GaintB: in-page confirm modal (replaces browser confirm()/alert()) -->
<div class="bs-modal-overlay ng-cloak" ng-show="bsConfirmShow" ng-click="bsConfirmBackdrop()">
    <div class="bs-modal-box" ng-class="bsConfirmBoxClass" ng-click="$event.stopPropagation()">
        <div class="bs-modal-title" ng-class="bsConfirmTitleClass" ng-show="bsConfirmTitle">{{bsConfirmTitle}}</div>
        <div class="bs-modal-msg" ng-bind-html-unsafe="bsConfirmMsg"></div>
        <div class="bs-modal-actions">
            <button type="button" class="btn btn-primary bs-modal-yes" ng-click="bsConfirmOk()" ng-show="bsConfirmShowOk !== false">{{bsConfirmYesLabel || 'OK'}}</button>
            <button type="button" class="btn btn-default bs-modal-no" ng-click="bsConfirmCancel()" ng-show="bsConfirmShowCancel !== false">{{bsConfirmNoLabel || 'Cancel'}}</button>
        </div>
    </div>
</div>

<footer>
    <div class="container">
        <hr/>
        <p>
        <span class="pull-right">
        Miner {{status.uptime|duration}} &nbsp;-&nbsp; Pi {{status.pi.uptime|duration}} &nbsp;-&nbsp; Temp
        {{status.pi.temp}}°C &nbsp;-&nbsp;
        <span class="bs-tip" data-tip="Load avg {{bsSysStats.load_1|number:2}} / {{bsSysStats.load_5|number:2}} / {{bsSysStats.load_15|number:2}} on {{bsSysStats.cores}} cores">Load {{bsSysStats.load_1|number:2}}</span>
        &nbsp;-&nbsp;
        <span class="bs-tip" data-tip="{{bsSysStats.mem_used_mb}}M used of {{bsSysStats.mem_total_mb}}M total">Mem {{bsSysStats.mem_pct}}%</span>
        </span>
        <a href='http://www.lateralfactory.com/scripta/' target='_blank' rel='noopener noreferrer'>Scripta</a> by <a href='http://www.lateralfactory.com' target='_blank' rel='noopener noreferrer'>Lateral Factory</a>, modified by Baikal Miner, rebranded as <a href='https://github.com/SidGrip/Blakestream-Baikal-BKB' target='_blank' rel='noopener noreferrer'>Blakestream-GaintB v2.2</a> &mdash; GPLv3
        </p>
    </div>
</footer>
<script src="js/alertify.min.js">
</script>
<script src="js/jquery.min.js">
</script>
<script src="js/highcharts.js">
</script>
<script src="js/blakestream-tz.js">
</script>
<script src="js/bootstrap.min.js">
</script>
<script src="js/angular.min.js">
</script>
<script src="js/app.min.js">
</script>

<script src="ng/app.js?v=fan-inputs-1">
</script>
<script src="ng/services.js?v=20260518-clock-diagnostics">
</script>
<script src="ng/dev-activity.js?v=clock-index-1">
</script>
<script src="ng/controllers.js?v=fan-inputs-1">
</script>
<script src="ng/network-settings.js?v=20260930-ui-backup"></script>
<script src="ng/wifi-settings.js?v=wifi-password-2"></script>
<script src="ng/filters.js?v=20260518-clock-diagnostics">
</script>
<script src="ng/directives.js?v=fan-inputs-1">
</script>
<script>
    $(document).ready(function() {

            $.ajax( {
                type: "POST",
                url: "update/ctrl.php",
                success: function(returnMessage) {
                    if (returnMessage != 0) {
                        var r = confirm(returnMessage);
                        if (r == true) {
                            $.ajax( {
                                type: "POST",
                                url: "update/start.php",
                                success: function(returnMessage) {
                                    alert(returnMessage);
                                    window.location = "index.php";
                                }
                            });
                        }
                    }
                },
                error: function(returnMessage) {
                    alert("Error");
                    window.location = "index.php";
                }
            });


        });

    function ctrl_host(cmd) {
        var retVal = prompt("Enter password : ", "");
        if (retVal == null) Alert("Password required!");
        else {
            $.get('f_hostHardCtl.php?command=' + cmd + '&pass=' + retVal, function(data) {

                    alert('msg: ' + data);
                });
        }
    }

    function open_terminal() {
	url='http://'+location.host+':4200';
	window.open(url);

    }
</script>
</body>
</html>
