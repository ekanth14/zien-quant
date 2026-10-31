import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseHTML} from 'linkedom';
import {readFile} from 'node:fs/promises';
import {mountQuantResearch} from '../marketscope/static/quant-research.js';
const registry=JSON.parse(await readFile(new URL('../marketscope/static/data/quant-research.json',import.meta.url),'utf8'));
test('research candidates show disabled evidence, category filtering and no execution controls',async()=>{
 const {document}=parseHTML('<main></main>');const root=document.querySelector('main');
 await mountQuantResearch(root,async()=>registry);
 assert.match(root.textContent,/No new strategy activated/);
 assert.equal(root.querySelectorAll('#researchCandidates section').length,registry.strategies.length);
 const select=root.querySelector('#researchCategory');
 Object.defineProperty(select,'value',{value:'Market microstructure, order flow, and liquidity'});
 select.onchange();assert.match(root.textContent,/No strategy specified in this category/);
 assert.equal(root.querySelectorAll('button').length,0);
});
test('fetch failure and navigation cancellation never show invented performance',async()=>{
 const {document}=parseHTML('<main></main>');const root=document.querySelector('main');
 await mountQuantResearch(root,async()=>{throw Error('offline');});assert.match(root.textContent,/Research registry unavailable/);
 let resolve;const p=mountQuantResearch(root,()=>new Promise(r=>resolve=r));root.innerHTML='<p>Other view</p>';resolve(registry);await p;assert.equal(root.textContent,'Other view');
});
test('source and text rendering do not execute article content',async()=>{
 const {document}=parseHTML('<main></main>');const root=document.querySelector('main');const data=structuredClone(registry);
 data.sources[0].url='javascript:alert(1)';data.strategies[0].name='<img src=x onerror=alert(1)>';
 await mountQuantResearch(root,async()=>data);assert.equal(root.querySelectorAll('img').length,0);assert.equal(root.querySelectorAll('[href^="javascript:"]').length,0);
});

test('research library keeps formulas and source evidence accessible in collapsed reading panels',async()=>{
 const {document}=parseHTML('<main></main>');const root=document.querySelector('main');
 await mountQuantResearch(root,async()=>registry);
 assert.equal(root.querySelector('label[for="researchCategory"]').textContent,'Research category');
 assert.ok(root.querySelector('.research-candidate details .research-formula'));
 assert.ok(!root.querySelector('.research-candidate details').hasAttribute('open'));
 assert.equal(root.querySelectorAll('.research-source-list a').length,registry.sources.length);
 assert.equal(root.querySelectorAll('.research-document-links a').length,registry.documents.length);
});

test('strategy search and comparison preserve rules without activation',async()=>{
 const {document}=parseHTML('<main></main>');const root=document.querySelector('main');
 await mountQuantResearch(root,async()=>registry);
 const input=root.querySelector('[data-compare]');input.checked=true;
 root.querySelector('#researchCandidates').onchange({target:input});
 assert.match(root.querySelector('#researchComparison').textContent,/Experimental candidates/);
 assert.ok(root.querySelector('#researchComparison').textContent.includes(registry.strategies[0].entry_rules));
 const search=root.querySelector('#researchSearch');search.value='no-such-strategy-xyz';search.oninput();
 assert.equal(root.querySelectorAll('[data-compare]').length,0);
 assert.ok(root.querySelector('#researchComparison table'));
 search.value='';search.oninput();assert.equal(root.querySelector('[data-compare]').hasAttribute('checked'),true);
});
