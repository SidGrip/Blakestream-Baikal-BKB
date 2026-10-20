#!/usr/bin/env node
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const filters = {};
const moduleStub = {
  filter(name, factory) {
    if (typeof factory === 'function') filters[name] = factory();
    return this;
  }
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../overlay/var/www/ng/filters.js'), 'utf8'), {
  angular: {module: () => moduleStub}
});
[
  [0, '0s'], [59.9, '59s'], [60, '1m 0s'], [3599, '59m 59s'],
  [3600, '1h 0m 0s'], [86399, '23h 59m 59s'], [86400, '1d 0h 0m 0s'],
  [7 * 86400 + 10 * 3600 + 293, '7d 10h 4m 53s'],
  [24 * 86400, '24d 0h 0m 0s'], [365 * 86400, '365d 0h 0m 0s'],
  ['839946.12', '9d 17h 19m 6s'],
  [undefined, 'loading'], [null, 'loading'], [-1, 'loading'],
  [NaN, 'loading'], [Infinity, 'loading'], ['invalid', 'loading']
].forEach(([input, expected]) => assert.strictEqual(filters.duration(input), expected, String(input)));
console.log('PASS duration boundaries and long controller uptimes');
