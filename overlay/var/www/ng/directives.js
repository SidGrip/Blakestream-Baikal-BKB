'use strict';

/* Directives */

// Highcharts treats incoming ms as UTC; BlakestreamTZ does the per-tick
// formatting in the user's selected zone (handles DST correctly).
if (typeof Highcharts !== 'undefined' && Highcharts.setOptions) {
  Highcharts.setOptions({global: {useUTC: true}});
}

angular.module('Scripta.directives', [])

// Sets background color by interpolating between green and red.
// Thinking about oth interpolation functions or maybe more colors
.directive('statusItem', function() {
  return function(scope, element, attrs) {
    var i=attrs.statusItem;
    var g=attrs.good; // Threshold good: green
    var b=attrs.bad; // Threshold bad: red

    function update(){
      var x=2*(i-g)/(b-g);
      x= x<0 ?0:x;
      x= x>2 ?2:x;
      element.css('background',(b==g)?'#666':'rgb('+Math.round(Math.min(x, 1)*(217-92)+92)+','+Math.round((2 - Math.max(x, 1)) * (184-83)+83)+',85)');
      element.css('color','#fff');
    }

    scope.$watch(attrs.good,       function(v) {g=v;update();});
    scope.$watch(attrs.bad,        function(v) {b=v;update();});
    scope.$watch(attrs.statusItem, function(v) {i=v;update();});
  }
})
// Toggles .active based on $location.path()
.directive('menuActive', function($rootScope,$location) {
  return function(scope, element, attrs) {
    $rootScope.$on('$routeChangeStart', function (event, next, current) {
      (element.children()[0].hash === '#'+$location.path()) ? element.addClass('active') : element.removeClass('active');
    });
  }
})

.directive('graphLive', function () {
  return {
    restrict: 'C',
    scope: {
      live: '='
    },
    controller: function ($scope, $element, $attrs) {
    },
    link: function (scope, element, attrs) {
      var chart = new Highcharts.Chart({
        chart: {
          renderTo: attrs.id,
          type: 'areaspline',
          spacingLeft: 0,
          spacingRight: 0
        },
        colors:   ['rgb(0,0,0)'],
        legend:   {enabled: false},
        subtitle: {text: ''},
        title: {
          text: 'Hashrate',
          align: 'center',
          verticalAlign: 'bottom',
        },
        xAxis: {
          type: 'datetime',
          minPadding: 0,
          maxPadding: 0,
          tickPixelInterval: 120
        },
        yAxis: {
          tickPixelInterval: 30,
          title: {
            text: ''
          },
          opposite: true
        },
        tooltip: {
          formatter: function() {
            var hs=this.y/1000,h=this.y+' ';
            if(hs > 10){h=hs.toPrecision(4)+' k';}hs/=1000;
            if(hs > 10){h=hs.toPrecision(4)+' M';}hs/=1000;
            if(hs > 10){h=hs.toPrecision(4)+' G';}hs/=1000;
            if(hs > 10){h=hs.toPrecision(4)+' T';}
            return BlakestreamTZ.format(this.x, 'yyyy-MM-dd HH:mm:ss') +'<br/>'+ h +'h/s';
          }
        },
        plotOptions: {
          areaspline: {
            fillOpacity: 0.1,
            marker: {
              enabled: false,
              states: {
                hover: {
                  enabled: true
                }
              }
            }
          }
        },
        series: [{
          name: 'hashrate',
          data: [[Date.now(),0]]
        }]
      });

var liveTrack=0;
scope.$watch('live', function (newlist) {
  var n=angular.copy(newlist);
  if(!n || !n.length)return;
  if(liveTrack<2){
    chart.series[0].setData(n);
  }
  else{
    chart.series[0].addPoint(n[n.length-1],true,n.length<chart.pointCount);
  }
  liveTrack=n.length;
}, true);
}
}
})

// Per-board hashrate areaspline. Rebuilds on theme toggle for clean colors.
.directive('bsHashrateChart', function () {
  var WINDOW_HOURS = {hour: 1, day: 24, week: 168};
  function chartTheme() {
    var dark = document.body && document.body.classList &&
               document.body.classList.contains('bs-dark');
    if (dark) {
      return {
        line:        '#4fc3f7',
        fillOpacity: 0.18,
        axisLine:    '#3a3f47',
        gridLine:    '#2a2d33',
        labelColor:  '#9ba1ab',
        tooltipBg:   '#1a1d21',
        tooltipText: '#fff'
      };
    }
    return {
      line:        '#4fc3f7',
      fillOpacity: 0.20,
      axisLine:    '#c9d1da',
      gridLine:    '#eef2f7',
      labelColor:  '#5a6470',
      tooltipBg:   '#ffffff',
      tooltipText: '#1a1d21'
    };
  }
  return {
    restrict: 'A',
    scope: {
      boardData: '=',
      range: '@'
    },
    link: function (scope, element, attrs) {
      var chart = null;
      var rangeName = function () {
        var r = scope.boardData && scope.boardData.range ? scope.boardData.range : 'hour';
        return WINDOW_HOURS[r] ? r : 'hour';
      };
      var samplesInRange = function () {
        if (!scope.boardData || !scope.boardData.samples) return [];
        var hours = WINDOW_HOURS[rangeName()];
        var cutoff = (Math.floor(Date.now() / 1000) - hours * 3600);
        var out = [];
        for (var i = 0; i < scope.boardData.samples.length; i++) {
          var s = scope.boardData.samples[i];
          if (s && s[0] >= cutoff) out.push([s[0] * 1000, s[1]]);
        }
        return out;
      };
      var rangeBounds = function () {
        var hours = WINDOW_HOURS[rangeName()];
        var now = Date.now();
        return {min: now - hours * 3600 * 1000, max: now};
      };
      var build = function () {
        var data = samplesInRange();
        var b = rangeBounds();
        if (chart) {
          chart.xAxis[0].setExtremes(b.min, b.max, false, false);
          chart.series[0].setData(data, true);
          return;
        }
        var t = chartTheme();
        chart = new Highcharts.Chart({
          chart: {
            renderTo: element[0],
            type: 'areaspline',
            backgroundColor: 'transparent',
            spacingLeft: 0,
            spacingRight: 0,
            spacingTop: 4,
            spacingBottom: 4,
            height: 180
          },
          colors: [t.line],
          credits: {enabled: false},
          legend: {enabled: false},
          title: {text: ''},
          subtitle: {text: ''},
          xAxis: {
            type: 'datetime',
            min: b.min,
            max: b.max,
            minPadding: 0,
            maxPadding: 0,
            tickPixelInterval: 100,
            lineColor: t.axisLine,
            tickColor: t.axisLine,
            labels: {
              style: {color: t.labelColor, fontSize: '10px'},
              formatter: function () {
                var range = WINDOW_HOURS[rangeName()];
                var fmt = (range <= 1) ? 'HH:mm' : (range <= 24) ? 'HH:mm' : 'yyyy-MM-dd';
                return BlakestreamTZ.format(this.value, fmt);
              }
            }
          },
          yAxis: {
            min: 0,
            tickPixelInterval: 35,
            title: {text: ''},
            gridLineColor: t.gridLine,
            labels: {
              style: {color: t.labelColor, fontSize: '10px'},
              formatter: function () {
                var v = this.value;
                if (v >= 1000) return (v / 1000).toFixed(1) + ' GH';
                return v + ' MH';
              }
            },
            opposite: true
          },
          tooltip: {
            backgroundColor: t.tooltipBg,
            borderColor: t.line,
            style: {color: t.tooltipText},
            formatter: function () {
              var v = this.y;
              var unit = ' MH/s';
              if (v >= 1000) { v = (v / 1000).toFixed(2); unit = ' GH/s'; }
              else { v = v.toFixed(0); }
              return BlakestreamTZ.format(this.x, 'yyyy-MM-dd HH:mm') + '<br/><b>' + v + unit + '</b>';
            }
          },
          plotOptions: {
            areaspline: {
              fillOpacity: t.fillOpacity,
              lineWidth: 1.5,
              marker: {enabled: false, states: {hover: {enabled: true, radius: 3}}}
            }
          },
          series: [{name: 'Board ' + (scope.boardData ? scope.boardData.id : ''), data: data}]
        });
      };

      var rebuild = function () {
        if (chart) { chart.destroy(); chart = null; }
        build();
      };

      scope.$watch('boardData.samples', build);
      scope.$watch('boardData.range', build);
      scope.$on('bsTzChanged', function () { if (chart) build(); });

      var onThemeChanged = function () { rebuild(); };
      window.addEventListener('bsThemeChanged', onThemeChanged);

      scope.$on('$destroy', function () {
        window.removeEventListener('bsThemeChanged', onThemeChanged);
        if (chart) { chart.destroy(); chart = null; }
      });
    }
  };
});
