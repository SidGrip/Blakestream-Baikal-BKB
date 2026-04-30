/* Timezone helper. Format UTC ms in settings.userTimezone via Intl.DateTimeFormat.
 * Falls back to browser-local if the IANA zone is unsupported. */
(function (root) {
  'use strict';

  var activeZone = 'UTC';
  var formatterCache = {};   // tz -> Intl.DateTimeFormat with all parts

  function makeFormatter(tz) {
    try {
      return new Intl.DateTimeFormat('en-GB', {
        timeZone: tz,
        hour12: false,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      });
    } catch (e) {
      return null;
    }
  }

  function getFormatter(tz) {
    if (!formatterCache.hasOwnProperty(tz)) {
      formatterCache[tz] = makeFormatter(tz);
    }
    return formatterCache[tz];
  }

  function partsToObj(parts) {
    var o = {};
    for (var i = 0; i < parts.length; i++) {
      o[parts[i].type] = parts[i].value;
    }
    return o;
  }

  function pad(n) { n = String(n); return n.length < 2 ? '0' + n : n; }

  var TZ = {
    setZone: function (tz) {
      if (!tz) tz = 'UTC';
      // Validate by attempting a formatter; bad tz falls back to UTC.
      if (makeFormatter(tz) === null) tz = 'UTC';
      activeZone = tz;
    },
    getZone: function () { return activeZone; },

    format: function (unixMs, fmt) {
      if (unixMs === null || unixMs === undefined) return '';
      var d = new Date(unixMs);
      if (isNaN(d.getTime())) return '';
      var tz = activeZone;
      var f = getFormatter(tz);
      if (!f || typeof f.formatToParts !== 'function') {
        // Fallback: browser-local rendering via Date methods.
        var H = pad(d.getHours());
        var M = pad(d.getMinutes());
        var S = pad(d.getSeconds());
        var Y = d.getFullYear();
        var Mo = pad(d.getMonth() + 1);
        var Da = pad(d.getDate());
        switch (fmt) {
          case 'HH:mm':            return H + ':' + M;
          case 'HH:mm:ss':         return H + ':' + M + ':' + S;
          case 'yyyy-MM-dd':       return Y + '-' + Mo + '-' + Da;
          case 'yyyy-MM-dd HH:mm': return Y + '-' + Mo + '-' + Da + ' ' + H + ':' + M;
          default:                 return Y + '-' + Mo + '-' + Da + ' ' + H + ':' + M + ':' + S;
        }
      }
      var p = partsToObj(f.formatToParts(d));
      switch (fmt) {
        case 'HH:mm':            return p.hour + ':' + p.minute;
        case 'HH:mm:ss':         return p.hour + ':' + p.minute + ':' + p.second;
        case 'yyyy-MM-dd':       return p.year + '-' + p.month + '-' + p.day;
        case 'yyyy-MM-dd HH:mm': return p.year + '-' + p.month + '-' + p.day + ' ' + p.hour + ':' + p.minute;
        default:                 return p.year + '-' + p.month + '-' + p.day + ' ' + p.hour + ':' + p.minute + ':' + p.second;
      }
    },

    offsetMinutes: function (unixMs) {
      if (unixMs === null || unixMs === undefined) unixMs = Date.now();
      var d = new Date(unixMs);
      if (isNaN(d.getTime())) return 0;
      var f = getFormatter(activeZone);
      if (!f || typeof f.formatToParts !== 'function') {
        // Browser-local fallback. Date.getTimezoneOffset is +minutes WEST of UTC,
        // we return signed minutes EAST of UTC to match other tz APIs.
        return -d.getTimezoneOffset();
      }
      var p = partsToObj(f.formatToParts(d));
      // Reconstruct the local "wall clock" in the chosen tz as a UTC instant.
      // Difference from the original UTC ms -> offset.
      var asUtc = Date.UTC(
        parseInt(p.year, 10),
        parseInt(p.month, 10) - 1,
        parseInt(p.day, 10),
        parseInt(p.hour, 10),
        parseInt(p.minute, 10),
        parseInt(p.second, 10)
      );
      return Math.round((asUtc - d.getTime()) / 60000);
    }
  };

  root.BlakestreamTZ = TZ;
})(typeof window !== 'undefined' ? window : this);
