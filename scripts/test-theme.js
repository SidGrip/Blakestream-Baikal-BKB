'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const code = fs.readFileSync(path.join(root, 'overlay/var/www/js/blakestream-theme.js'), 'utf8');
function element() {
  const classes = new Set();
  return {classList: {
    add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
    toggle: (c, enabled) => enabled ? classes.add(c) : classes.delete(c)
  }};
}
function load(stored, blocked) {
  const listeners = {}, buttons = [{setAttribute() {}, removeAttribute() {}}], events = [];
  const document = {documentElement: element(), body: null, readyState: 'loading',
    querySelectorAll: () => buttons, addEventListener: (name, fn) => { listeners[name] = fn; }};
  const storage = {bsTheme: stored};
  const window = {dispatchEvent: event => events.push(event)};
  vm.runInNewContext(code, {document, window, localStorage: {
    getItem: key => { if (blocked) throw Error('blocked'); return storage[key]; },
    setItem: (key, value) => { if (blocked) throw Error('blocked'); storage[key] = value; }
  }, CustomEvent: function(name, data) { this.type = name; this.detail = data.detail; }});
  assert.equal(document.documentElement.classList.contains('bs-dark'), blocked || stored !== 'light');
  document.body = element();
  window.BlakestreamTheme.init();
  listeners.DOMContentLoaded();
  return {document, window, storage, events};
}
for (const stored of [undefined, '', 'invalid', 'dark', 'light']) {
  const app = load(stored, false);
  const dark = stored !== 'light';
  assert.equal(app.document.body.classList.contains('bs-dark'), dark);
  app.window.BlakestreamTheme.toggle();
  assert.equal(app.document.body.classList.contains('bs-dark'), !dark);
  assert.equal(app.document.documentElement.classList.contains('bs-dark'), !dark);
  assert.equal(app.storage.bsTheme, dark ? 'light' : 'dark');
  assert.equal(app.events.at(-1).detail.theme, dark ? 'light' : 'dark');
}
const blocked = load('light', true);
assert.equal(blocked.document.body.classList.contains('bs-dark'), true);
blocked.window.BlakestreamTheme.toggle();
assert.equal(blocked.document.body.classList.contains('bs-dark'), false);
blocked.window.BlakestreamTheme.toggle();
assert.equal(blocked.document.body.classList.contains('bs-dark'), true);
for (const file of ['index.php', 'login.php', 'index.html']) {
  const html = fs.readFileSync(path.join(root, 'overlay/var/www', file), 'utf8');
  assert(/<body[^>]*class="[^"]*bs-dark/.test(html));
  assert(html.includes('window.BlakestreamTheme.init();'));
}
console.log('PASS dark first visit, saved light choice, blocked storage, pre-paint root/body theme and toggle');
