#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'images', 'bkb-logic-analyzer-2x17-pinout.svg');

const pinNames = [
  'PE2', 'PE3', 'PE4', 'PE5', 'PE6', 'VBAT', 'PC13', 'PC14', 'PC15', 'VSS',
  'VDD', 'PH0', 'PH1', 'NRST', 'PC0', 'PC1', 'PC2', 'PC3', 'VDD', 'VSSA',
  'VREF+', 'VDDA', 'PA0', 'PA1', 'PA2', 'PA3', 'VSS', 'VDD', 'PA4', 'PA5',
  'PA6', 'PA7', 'PC4', 'PC5', 'PB0', 'PB1', 'PB2', 'PE7', 'PE8', 'PE9',
  'PE10', 'PE11', 'PE12', 'PE13', 'PE14', 'PE15', 'PB10', 'PB11', 'VCAP_1', 'VDD',
  'PB12', 'PB13', 'PB14', 'PB15', 'PD8', 'PD9', 'PD10', 'PD11', 'PD12', 'PD13',
  'PD14', 'PD15', 'PC6', 'PC7', 'PC8', 'PC9', 'PA8', 'PA9', 'PA10', 'PA11',
  'PA12', 'PA13', 'VCAP_2', 'VSS', 'VDD', 'PA14', 'PA15', 'PC10', 'PC11', 'PC12',
  'PD0', 'PD1', 'PD2', 'PD3', 'PD4', 'PD5', 'PD6', 'PD7', 'PB3', 'PB4',
  'PB5', 'PB6', 'PB7', 'BOOT0', 'PB8', 'PB9', 'PE0', 'PE1', 'VSS', 'VDD',
];

const groups = {
  spi1: { color: '#079bc4', label: 'Cyan: SPI1' },
  spi2: { color: '#d6a900', label: 'Yellow: SPI2' },
  spi3: { color: '#198b2c', label: 'Green: SPI3' },
  setup: { color: '#f05a16', label: 'Orange: SETUP' },
  restore: { color: '#df0879', label: 'Magenta: RESTORE' },
  ground: { color: '#151515', label: 'Black: BOARD GND' },
};

const header = [
  [1, 'PA5', 30, 'spi1', 'SPI1', 'SCK'],
  [2, 'PA7', 32, 'spi1', 'SPI1', 'MOSI'],
  [3, 'PA6', 31, 'spi1', 'SPI1', 'MISO'],
  [4, 'PB13', 52, 'spi2', 'SPI2', 'SCK'],
  [5, 'PB15', 54, 'spi2', 'SPI2', 'MOSI'],
  [6, 'PB14', 53, 'spi2', 'SPI2', 'MISO'],
  [7, 'PC10', 78, 'spi3', 'SPI3', 'SCK'],
  [8, 'PC12', 80, 'spi3', 'SPI3', 'MOSI'],
  [9, 'PC11', 79, 'spi3', 'SPI3', 'MISO'],
  [10, 'PE5', 4, 'setup', 'B0', 'SETUP'],
  [11, 'PE4', 3, 'setup', 'B0', 'SETUP'],
  [12, 'PE3', 2, 'setup', 'B0', 'SETUP'],
  [13, 'PE2', 1, 'setup', 'B0', 'SETUP'],
  [14, 'PE6', 5, 'restore', 'B0', 'RESTORE'],
  [15, 'PC13', 7, 'restore', 'B0', 'RESTORE'],
  [16, 'PC14', 8, 'restore', 'B0', 'RESTORE'],
  [17, 'PC15', 9, 'restore', 'B0', 'RESTORE'],
  [18, 'PE15', 46, 'setup', 'B1', 'SETUP'],
  [19, 'PE14', 45, 'setup', 'B1', 'SETUP'],
  [20, 'PE13', 44, 'setup', 'B1', 'SETUP'],
  [21, 'PE12', 43, 'setup', 'B1', 'SETUP'],
  [22, 'PC4', 33, 'restore', 'B1', 'RESTORE'],
  [23, 'PC5', 34, 'restore', 'B1', 'RESTORE'],
  [24, 'PB0', 35, 'restore', 'B1', 'RESTORE'],
  [25, 'PB1', 36, 'restore', 'B1', 'RESTORE'],
  [26, 'PD14', 61, 'setup', 'B2', 'SETUP'],
  [27, 'PD15', 62, 'setup', 'B2', 'SETUP'],
  [28, 'PC6', 63, 'setup', 'B2', 'SETUP'],
  [29, 'PC7', 64, 'setup', 'B2', 'SETUP'],
  [30, 'PD4', 85, 'restore', 'B2', 'RESTORE'],
  [31, 'PD5', 86, 'restore', 'B2', 'RESTORE'],
  [32, 'PD6', 87, 'restore', 'B2', 'RESTORE'],
  [33, 'PD7', 88, 'restore', 'B2', 'RESTORE'],
  [34, 'BOARD GND', null, 'ground', 'BOARD', 'GND'],
];

if (pinNames.length !== 100 || header.length !== 34) {
  throw new Error('Expected 100 package pins and 34 header positions');
}

const usedNets = new Set();
for (const [position, net, physical] of header) {
  if (position === 34) continue;
  if (pinNames[physical - 1] !== net) {
    throw new Error(`Header ${position}: ${net} does not match LQFP100 pin ${physical} (${pinNames[physical - 1]})`);
  }
  if (usedNets.has(net)) throw new Error(`Duplicate header net: ${net}`);
  usedNets.add(net);
}
if (usedNets.size !== 33) throw new Error(`Expected 33 unique signals, got ${usedNets.size}`);

const signalByPhysical = new Map(
  header.filter(([, , pin]) => pin !== null).map((entry) => [entry[2], entry])
);

const escape = (value) => String(value)
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const text = (x, y, value, attrs = '') =>
  `<text x="${x}" y="${y}" ${attrs}>${escape(value)}</text>`;
const line = (x1, y1, x2, y2, attrs = '') =>
  `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" ${attrs}/>`;
const rect = (x, y, width, height, attrs = '') =>
  `<rect x="${x}" y="${y}" width="${width}" height="${height}" ${attrs}/>`;

const svg = [];
svg.push(`<svg xmlns="http://www.w3.org/2000/svg" width="1536" height="1024" viewBox="0 0 1536 1024">`);
svg.push(`<rect width="1536" height="1024" fill="#f8f9fb"/>`);
svg.push(`<style>
  text { font-family: Arial, Helvetica, sans-serif; fill: #141414; }
  .title { font-size: 31px; font-weight: 800; }
  .subtitle { font-size: 18px; font-weight: 700; }
  .pin { font-size: 12px; font-weight: 700; }
  .pin-number { font-size: 11px; font-weight: 700; }
  .cell-net { font-size: 13px; font-weight: 800; }
  .cell-pin { font-size: 11px; font-weight: 700; }
  .cell-role { font-size: 9px; font-weight: 700; }
  .table { font-size: 13px; font-weight: 700; }
</style>`);

svg.push(text(768, 42, 'STM32F407VET6 Logic Analyzer Breakout - Full Controller Pin Assignment', 'text-anchor="middle" class="title"'));
svg.push(text(768, 72, 'LQFP100 TOP VIEW - straight 2x17 socket - 33 signals + board ground - no power connection', 'text-anchor="middle" class="subtitle"'));

const chip = { x: 86, y: 190, w: 620, h: 555 };
svg.push(`<path d="M ${chip.x + 18} ${chip.y} H ${chip.x + chip.w - 18} L ${chip.x + chip.w} ${chip.y + 18} V ${chip.y + chip.h - 18} L ${chip.x + chip.w - 18} ${chip.y + chip.h} H ${chip.x + 18} L ${chip.x} ${chip.y + chip.h - 18} V ${chip.y + 18} Z" fill="#fff" stroke="#151515" stroke-width="2"/>`);
svg.push(text(chip.x + chip.w / 2, chip.y + 255, 'STM32F407VET6', 'text-anchor="middle" font-size="30" font-weight="800"'));
svg.push(text(chip.x + chip.w / 2, chip.y + 300, 'LQFP100', 'text-anchor="middle" font-size="30" font-weight="800"'));
svg.push(text(chip.x + chip.w / 2, chip.y + 345, 'TOP VIEW', 'text-anchor="middle" font-size="30" font-weight="800"'));

function signalColor(pin) {
  const entry = signalByPhysical.get(pin);
  return entry ? groups[entry[3]].color : '#151515';
}

// Left edge: pins 1-25, top to bottom.
for (let pin = 1; pin <= 25; pin++) {
  const y = chip.y + 22 + (pin - 1) * ((chip.h - 44) / 24);
  const color = signalColor(pin);
  svg.push(line(chip.x - 8, y, chip.x, y, `stroke="${color}" stroke-width="2"`));
  svg.push(text(chip.x - 16, y + 4, pinNames[pin - 1], `text-anchor="end" class="pin" fill="${color}" style="fill:${color}"`));
  svg.push(text(chip.x + 8, y + 4, pin, `class="pin-number" fill="${color}" style="fill:${color}"`));
}

// Right edge: pins 51-75, bottom to top.
for (let pin = 51; pin <= 75; pin++) {
  const y = chip.y + chip.h - 22 - (pin - 51) * ((chip.h - 44) / 24);
  const color = signalColor(pin);
  svg.push(line(chip.x + chip.w, y, chip.x + chip.w + 8, y, `stroke="${color}" stroke-width="2"`));
  svg.push(text(chip.x + chip.w - 8, y + 4, pin, `text-anchor="end" class="pin-number" fill="${color}" style="fill:${color}"`));
  svg.push(text(chip.x + chip.w + 16, y + 4, pinNames[pin - 1], `class="pin" fill="${color}" style="fill:${color}"`));
}

// Bottom edge: pins 26-50, left to right.
for (let pin = 26; pin <= 50; pin++) {
  const x = chip.x + 22 + (pin - 26) * ((chip.w - 44) / 24);
  const color = signalColor(pin);
  svg.push(line(x, chip.y + chip.h, x, chip.y + chip.h + 8, `stroke="${color}" stroke-width="2"`));
  svg.push(text(x, chip.y + chip.h - 8, pin, `text-anchor="middle" class="pin-number" fill="${color}" style="fill:${color}"`));
  svg.push(`<g transform="translate(${x + 4},${chip.y + chip.h + 18}) rotate(90)">${text(0, 0, pinNames[pin - 1], `class="pin" fill="${color}" style="fill:${color}"`)}</g>`);
}

// Top edge: pins 76-100, right to left.
for (let pin = 76; pin <= 100; pin++) {
  const x = chip.x + chip.w - 22 - (pin - 76) * ((chip.w - 44) / 24);
  const color = signalColor(pin);
  svg.push(line(x, chip.y - 8, x, chip.y, `stroke="${color}" stroke-width="2"`));
  svg.push(text(x, chip.y + 15, pin, `text-anchor="middle" class="pin-number" fill="${color}" style="fill:${color}"`));
  svg.push(`<g transform="translate(${x + 4},${chip.y - 15}) rotate(-90)">${text(0, 0, pinNames[pin - 1], `class="pin" fill="${color}" style="fill:${color}"`)}</g>`);
}

// Header, conventional odd/even dual-row numbering as viewed from the mating face.
const hx = 792, hy = 185, cw = 42, ch = 132;
svg.push(text(hx + (17 * cw) / 2, 134, 'STRAIGHT 2x17 FEMALE SOCKET - FRONT / MATING-FACE VIEW', 'text-anchor="middle" class="subtitle"'));
svg.push(`<path d="M ${hx - 16} ${hy - 18} l 13 5 l -8 11 z" fill="#111"/>`);
svg.push(text(hx - 22, hy - 23, 'PIN 1', 'text-anchor="middle" font-size="11" font-weight="800"'));

for (const entry of header) {
  const [position, net, physical, group, role1, role2] = entry;
  const col = Math.floor((position - 1) / 2);
  const row = position % 2 === 1 ? 0 : 1;
  const x = hx + col * cw;
  const y = hy + row * ch;
  const color = groups[group].color;
  const displayNet = position === 34 ? 'GND' : net;
  svg.push(rect(x, y, cw, ch, 'fill="#fff" stroke="#111" stroke-width="1.2"'));
  svg.push(text(x + cw / 2, y - (row === 0 ? 7 : -ch - 18), position,
    `text-anchor="middle" font-size="13" font-weight="800"`));
  svg.push(text(x + cw / 2, y + 31, displayNet, `text-anchor="middle" class="cell-net" fill="${color}" style="fill:${color}"`));
  if (physical !== null) {
    svg.push(text(x + cw / 2, y + 53, `P${physical}`, 'text-anchor="middle" class="cell-pin"'));
  }
  svg.push(text(x + cw / 2, y + 83, role1, 'text-anchor="middle" class="cell-role"'));
  svg.push(text(x + cw / 2, y + 98, role2, 'text-anchor="middle" class="cell-role"'));
}
svg.push(text(hx + (17 * cw) / 2, hy + 2 * ch + 35, 'Rear solder-side view is mirrored.', 'text-anchor="middle" font-size="14" font-weight="700"'));

// Analyzer hookup table.
const tx = 835, ty = 555, tw = 650, rowH = 39;
const cols = [0, 100, 265, 425, 565, 650];
svg.push(rect(tx, ty, tw, rowH * 5, 'fill="#fff" stroke="#111" stroke-width="2"'));
svg.push(text(tx + tw / 2, ty + 27, 'SALEAE HOOKUP - ONE BOARD PER CAPTURE', 'text-anchor="middle" font-size="18" font-weight="800"'));
for (let r = 1; r < 5; r++) svg.push(line(tx, ty + r * rowH, tx + tw, ty + r * rowH, 'stroke="#111" stroke-width="1"'));
for (let c = 1; c < cols.length - 1; c++) svg.push(line(tx + cols[c], ty + rowH, tx + cols[c], ty + rowH * 5, 'stroke="#111" stroke-width="1"'));
const headings = ['Board', 'D0-D2', 'D3-D5', 'D6-D13', 'GND'];
for (let i = 0; i < headings.length; i++) {
  const cx = tx + (cols[i] + cols[i + 1]) / 2;
  svg.push(text(cx, ty + rowH + 26, headings[i], 'text-anchor="middle" class="table"'));
}
const hookup = [
  ['Board 0', 'pins 1-3', 'pins 7-9 (SPI3)', 'pins 10-17', 'pin 34'],
  ['Board 1', 'pins 1-3', 'pins 7-9 (SPI3)', 'pins 18-25', 'pin 34'],
  ['Board 2', 'pins 1-3', 'pins 4-6 (SPI2)', 'pins 26-33', 'pin 34'],
];
for (let r = 0; r < hookup.length; r++) {
  for (let c = 0; c < hookup[r].length; c++) {
    const cx = tx + (cols[c] + cols[c + 1]) / 2;
    const color = c === 1 ? groups.spi1.color :
      c === 2 ? (r === 2 ? groups.spi2.color : groups.spi3.color) :
      c === 3 ? groups.restore.color : '#151515';
    svg.push(text(cx, ty + (r + 2) * rowH + 26, hookup[r][c],
      `text-anchor="middle" class="table" fill="${color}" style="fill:${color}"`));
  }
}

const notes = [
  'Connect only analyzer digital inputs and ground.',
  'Do not connect analyzer power.',
  'Verify board ground and every net by continuity before soldering.',
  'One Logic Pro 16 captures one board at a time.',
];
for (let i = 0; i < notes.length; i++) {
  svg.push(text(850, 790 + i * 30, `•  ${notes[i]}`, 'font-size="15" font-weight="700"'));
}

// Footer legend.
svg.push(rect(85, 930, 1365, 59, 'fill="#fff" stroke="#111" stroke-width="2"'));
let lx = 128;
for (const key of ['spi1', 'spi2', 'spi3', 'setup', 'restore', 'ground']) {
  const group = groups[key];
  svg.push(text(lx, 967, group.label, `font-size="20" font-weight="800" fill="${group.color}" style="fill:${group.color}"`));
  lx += key === 'ground' ? 0 : 205;
}

svg.push('</svg>');
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, svg.join('\n') + '\n');
console.log(`Wrote ${output}`);
console.log('Validated 33 unique header signals against the official LQFP100 pin map.');
