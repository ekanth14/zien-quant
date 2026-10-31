import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';
import { parseHTML } from 'linkedom';

const root = new URL('../marketscope/static/', import.meta.url);
const html = await readFile(new URL('workspace.html', root), 'utf8');
const source = await readFile(new URL('historical-chart.js', root), 'utf8');

async function drawer() {
  const { document, window } = parseHTML(html);
  let focused = document.querySelector('#searchButton');
  Object.defineProperty(document, 'activeElement', { get: () => focused });
  window.HTMLElement.prototype.focus = function () { focused = this; };
  const selectValue = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value');
  Object.defineProperty(window.HTMLSelectElement.prototype, 'value', {
    configurable: true, get: selectValue.get,
    set(value) { for (const option of this.querySelectorAll('option')) option.selected = option.value === String(value); },
  });
  const frames = [];
  const context = vm.createContext({ document, window, console, Intl, Date, URL,
    setTimeout: () => 1, clearTimeout() {}, requestAnimationFrame: callback => frames.push(callback),
    ResizeObserver: class { observe() {} },
  });
  const module = new vm.SourceTextModule(source, { context });
  await module.link(() => {});
  await module.evaluate();
  const chart = module.namespace.createHistoricalChart({ api: async () => ({ instruments: [] }) });
  chart.bind();
  return { document, window, chart, flush: () => { while (frames.length) frames.shift()(); } };
}

function key(window, target, value, shiftKey = false) {
  const event = new window.Event('keydown', { bubbles: true, cancelable: true });
  Object.assign(event, { key: value, shiftKey });
  target.dispatchEvent(event);
  return event;
}

test('historical dialog traps both Tab boundaries and skips hidden candle controls', async () => {
  const { document, window, chart, flush } = await drawer();
  await chart.open('TCS'); flush();
  const panel = document.querySelector('#historyPanel');
  assert.equal(panel.getAttribute('role'), 'dialog');
  assert.equal(panel.getAttribute('aria-modal'), 'true');
  assert.equal(document.activeElement.id, 'historySymbolSearch');
  // In normal mode the intraday card is hidden with CSS. Simulate that state
  // in the layout-free DOM and ensure its controls are not focus boundaries.
  document.querySelector('#historyIntradayCard').hidden = true;
  const first = document.querySelector('#historyExpand');
  const last = document.querySelector('#historyLineChart');
  last.focus();
  assert.equal(key(window, last, 'Tab').defaultPrevented, true);
  assert.equal(document.activeElement, first);
  assert.equal(key(window, first, 'Tab', true).defaultPrevented, true);
  assert.equal(document.activeElement, last);
  document.querySelector('#historySymbolSearch').focus();
  assert.equal(key(window, document.activeElement, 'Tab').defaultPrevented, false);
});

test('Escape closes only the historical drawer and returns focus to its launcher', async () => {
  const { document, window, chart, flush } = await drawer();
  const launcher = document.activeElement;
  let escapedToDocument = false;
  document.addEventListener('keydown', () => { escapedToDocument = true; });
  await chart.open('TCS'); flush();
  const event = key(window, document.activeElement, 'Escape');
  assert.equal(event.defaultPrevented, true);
  assert.equal(escapedToDocument, false);
  assert.equal(document.querySelector('#historyPanel').hidden, true);
  assert.equal(document.querySelector('#historyPanel').hasAttribute('aria-modal'), false);
  assert.equal(document.activeElement, launcher);
});

test('pending initial focus cannot reopen focus after the drawer is closed', async () => {
  const { document, chart, flush } = await drawer();
  const launcher = document.activeElement;
  await chart.open('TCS');
  chart.close(); flush();
  assert.equal(document.activeElement, launcher);
});
