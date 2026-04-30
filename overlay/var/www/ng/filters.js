'use strict';

/* Filters */

angular.module('Scripta.filters', [])
.filter('shortUrl', function() {
  return function(temp) {
    return temp.replace('//', '').split(':')[1];
  }
})
.filter('mhs', function() {
  return function(hs) {
    if(hs<1) {
        hs *= 1000;
        return (hs).toPrecision(3)+' K';
    }
    if(hs<1000){
      return hs+' M';
    }
    hs/=1000;
    return (hs<1000)?(hs).toPrecision(4)+' G':(hs/1000).toPrecision(4)+' T';
  }
})
.filter('duration', function() {
  return function(s) {
    if(!s) return 'loading';
    var d=Math.floor(s%60)+'s';
    if(s < 60){return d;} s/=60; d=Math.floor(s%60)+'m '+d;
    if(s < 60){return d;} s/=60; d=Math.floor(s%60)+'h '+d;
    if(s < 24){return d;} s/=24; d=Math.floor(s%24)+'d '+d;
    return d;
  }
})
/* Blakestream-GaintB: clean up sgminer's mangled pool URL display.
 * sgminer's API returns the URL with the quota:N; prefix embedded and a
 * leading http:// it added because it didn't recognize the quota: scheme.
 * Strip both so the user sees a normal stratum+tcp://host:port string.
 * Examples:
 *   "http://quota:2;stratum+tcp://eu3.blakecoin.com:3334" -> "stratum+tcp://eu3.blakecoin.com:3334"
 *   "stratum+tcp://eu3.blakecoin.com:3334"                -> "stratum+tcp://eu3.blakecoin.com:3334" (unchanged)
 *   "http://stratum+tcp://...:3334"                       -> "stratum+tcp://...:3334"
 */
.filter('cleanPoolUrl', function() {
  return function(url) {
    if (!url) return url;
    var s = url;
    // Strip leading http:// (sgminer adds this when it doesn't recognize the scheme)
    s = s.replace(/^https?:\/\//, '');
    // Strip leading quota:N; that we put in the URL prefix to set load-balance weight
    s = s.replace(/^quota:\d+;/, '');
    return s;
  };
})

/* Format a UTC unix-ms timestamp in settings.userTimezone.
 * Example: {{1000*p.LastShareTime | bsTzDate:'HH:mm:ss'}} */
.filter('bsTzDate', function() {
  return function(unixMs, fmt) {
    if (typeof BlakestreamTZ === 'undefined') return unixMs;  // helper not yet loaded
    return BlakestreamTZ.format(unixMs, fmt || 'yyyy-MM-dd HH:mm:ss');
  };
});
