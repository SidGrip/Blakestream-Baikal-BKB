<?php
// Blakestream-GaintB login page (rebranded from factory Baikal Scripta).
// The factory version tried to fetch a fresh banner image from
// http://image.baikalminer.com/loginbanner.jpg on every request — that domain
// is dead, the fetch failed, and the failures showed as PHP warnings on the
// page. We use a static local banner instead.

require_once __DIR__ . '/inc/web-security.php';
bs_secure_session_start();
$loginTarget = isset($_GET['network']) && $_GET['network'] === '1' ? 'index.php#/settings?section=network' : 'index.php';
if ( isset($_SESSION['_logged_']) and ($_SESSION['_logged_'] === true) )  {
        header('location: ' . $loginTarget);
        exit();
}
?>


<!DOCTYPE html>
<html lang="en" ng-app="Scripta">
<head>
  <meta charset="utf-8">
  <title>Blakestream-GaintB v2.2 &mdash; Sign In</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="Blakestream miner controller">
  <meta name="author" content="Blakestream">
  <link rel="shortcut icon" type="image/x-icon" href="img/favicon.ico" />
  <link href="css/bootstrap.min.css" rel="stylesheet">
  <link href="css/font-awesome.min.css" rel="stylesheet">
  <link rel="stylesheet" href="css/ionicons.min.css">
  <link rel="stylesheet" href="css/AdminLTE.min.css">
  <link href="css/alertify.css" rel="stylesheet">
    <link href="css/blakestream-dark.css?v=network-indicator-1" rel="stylesheet">
  <script src="js/blakestream-theme.js?v=wifi-ui-1"></script>
  <style>
    /* Tight banner */
    .blakestream-banner {
      width: 100%;
      background: linear-gradient(135deg, #0f1316 0%, #1a4661 50%, #4fc3f7 100%);
      color: #fff;
      text-align: center;
      padding: 18px 20px 14px;
      margin-bottom: 0;
    }
    .blakestream-banner h1 {
      font-size: 36px;
      margin: 0 0 4px 0;
      font-weight: 300;
      letter-spacing: 1px;
      line-height: 1.1;
    }
    .blakestream-banner h1 b { font-weight: 700; }
    .blakestream-banner p {
      font-size: 13px;
      opacity: 0.85;
      margin: 0;
    }
    body.bs-dark .blakestream-banner {
      background: linear-gradient(135deg, #0f1316 0%, #15334a 50%, #29b6f6 100%);
    }

    /* Login layout and controls. */
    .lockscreen-wrapper {
      margin-top: 50px;
    }
    .lockscreen-item {
      background: #f4f4f4;
      width: 320px;
      padding: 0;
      box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    }
    .lockscreen-credentials {
      margin-left: 0;
    }
    .lockscreen-credentials .input-group {
      border-collapse: separate;
      border-spacing: 6px 0;
    }
    .lockscreen-credentials .form-control {
      border: 0;
      border-radius: 4px;
      height: 42px;
      font-size: 15px;
      padding: 8px 12px;
    }
    .lockscreen-credentials .btn {
      background-color: #f4f4f4;
      border: 0;
      border-radius: 4px;
      padding: 0 14px;
      height: 42px;
    }
    .lockscreen-credentials .btn:hover {
      background-color: #e8e8e8;
    }
    .lockscreen-logo {
      margin-top: 30px;
      margin-bottom: 18px;
    }

    /* Dark mode overrides for lockscreen */
    body.bs-dark.lockscreen,
    body.bs-dark .lockscreen-wrapper {
      background-color: #1a1d21 !important;
    }
    body.bs-dark .lockscreen-item {
      background: #232830;
      box-shadow: 0 2px 12px rgba(0,0,0,0.6);
    }
    body.bs-dark .lockscreen-credentials .form-control {
      background-color: #232830;
      color: #e6f3ff;
    }
    body.bs-dark .lockscreen-credentials .form-control::placeholder {
      color: #5f6f80;
    }
    body.bs-dark .lockscreen-credentials .btn {
      background-color: #232830;
      color: #4fc3f7;
    }
    body.bs-dark .lockscreen-credentials .btn:hover {
      background-color: #2d3540;
    }
    body.bs-dark .lockscreen-credentials .btn .text-muted {
      color: #4fc3f7 !important;
    }
    body.bs-dark .lockscreen-logo a {
      color: #4fc3f7 !important;
    }
  </style>
</head>


<div class="blakestream-banner">
  <h1><b>Blake</b>stream<span style="opacity:0.7">-GaintB</span></h1>
  <p>Custom firmware for Baikal BK-B &mdash; mining Blakecoin (BLAKE-256 R8)</p>
</div>

<body class="hold-transition lockscreen bs-dark">
<script>window.BlakestreamTheme.init();</script>
    <div class="lockscreen-wrapper">
      <div class="lockscreen-logo">
        <span><b>Baikal</b>Miner</span>
      </div>

      <div class="lockscreen-item">

        <form class="lockscreen-credentials" name="formLogin" id="formLogin" method="post">


          <div class="input-group">
             <input type="password" placeholder="Password" id="userPassword" name="userPassword"  class="form-control">
            <div class="input-group-btn">
              <button class="btn" id=loginbutton type="button" ><i class="fa fa-arrow-right text-muted"></i></button>
            </div>
          </div>


        </form><!-- /.lockscreen credentials -->

      </div><!-- /.lockscreen-item -->

    </div>


  <footer>
    <div class="container text-center">
      <hr />
      <p>
        <a href='http://www.lateralfactory.com/scripta/' target='_blank' rel='noopener noreferrer'>Scripta</a> by <a href='http://www.lateralfactory.com' target='_blank' rel='noopener noreferrer'>Lateral Factory</a>, modified by Baikal Miner, rebranded as <a href='https://github.com/SidGrip/Blakestream-Baikal-BKB' target='_blank' rel='noopener noreferrer'>Blakestream-GaintB v2.2</a> &mdash; GPLv3
      </p>
    </div>
  </footer>
  <script src="js/alertify.min.js"></script>
  <script src="js/blakestream-alertify-safe.js?v=20261024-text-only"></script>
  <script src="js/jquery.min.js"></script>
  <script src="js/network-login.js?v=20260929-network-compact"></script>
  <script src="js/highcharts.js"></script>
  <script src="js/bootstrap.min.js"></script>
  <script>
      $(document).ready(function() {
		$('#formLogin').on('submit', function(e) {
			e.preventDefault();
			return false;
		});

		$('#userPassword').on('keydown', function(e) {
			if (e.which == 13) {
				e.preventDefault();
				e.stopPropagation();
				return false;
			}
		});

		$('#loginbutton').click(function(e){
			e.preventDefault();

			var sData = $("#formLogin").serialize();
			$.ajax({
				type: "POST",
				url: "f_login.php",
				data: sData,
				success: function(returnMessage) {
					if (returnMessage == 1)
						bsNetworkLoginRedirect(<?php echo json_encode($loginTarget); ?>);
					else
						alert("Incorrect password");

				},
				error: function(returnMessage) {
					alert("Error");
					window.location = "login.php";
				}
			});
		});
	});
  </script>
</body>
</html>
