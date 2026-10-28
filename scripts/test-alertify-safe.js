'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const scriptPath = path.join(root, 'overlay/var/www/js/blakestream-alertify-safe.js');
const source = fs.readFileSync(scriptPath, 'utf8');
const calls = [];
const log = {
  create: function(type, message) { calls.push(['create', type, message]); },
  error: function(message) { calls.push(['error', message]); },
  info: function(message) { calls.push(['info', message]); },
  success: function(message) { calls.push(['success', message]); }
};
const context = {window: {Alertify: {log: log}}};
vm.runInNewContext(source, context, {filename: scriptPath});

const payload = '<img src=x onerror="alert(1)">&\'payload\'';
context.window.Alertify.log.success(payload);
context.window.Alertify.log.error(payload);
context.window.Alertify.log.create('danger', payload);
context.window.Alertify.log.log(payload);
for (const call of calls) {
  const message = call[call.length - 1];
  assert(!message.includes('<') && !message.includes('>'), 'toast markup must be encoded');
  assert(message.includes('&lt;img') && message.includes('&amp;'), 'toast text must retain encoded content');
}
assert.strictEqual(calls[2][1], 'danger', 'create keeps the Alertify type argument');
assert.strictEqual(calls[3][0], 'info', 'neutral log alias is backed by wrapped info');

for (const page of ['index.php', 'login.php']) {
  const html = fs.readFileSync(path.join(root, 'overlay/var/www', page), 'utf8');
  assert(html.indexOf('js/alertify.min.js') < html.indexOf('js/blakestream-alertify-safe.js'),
    page + ' loads the text-only wrapper immediately after Alertify');
}
console.log('PASS Alertify toast messages are text-only');
