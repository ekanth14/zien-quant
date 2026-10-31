const safe=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const url=value=>{try{const u=new URL(value);return u.protocol==='https:'?safe(u.href):'#';}catch{return '#';}};
export async function mountQuantResearch(root,api){
 root.innerHTML='<section class="panel paper-review" role="status">Loading research registry…</section>';
 const marker=root.firstElementChild;
 try{
  const data=await api('/api/research/strategies');
  if(!root.contains(marker))return;
  if(!Array.isArray(data.strategies)||!Array.isArray(data.sources))throw Error('Research registry has an invalid format');
  root.innerHTML=`<div class="paper-lab research-workspace">
   <section class="paper-hero"><div><span class="paper-kicker">STRATEGY RESEARCH</span><h2>Research library</h2><p>Inspect the rules, evidence and limitations behind each candidate.</p></div><div class="paper-seal">PAPER<br><b>ONLY</b><span>Research candidates</span></div></section>
   <p class="paper-notice">Experimental · No new strategy activated. No validated accuracy or profit forecast.</p>
   <section class="panel paper-review research-library" aria-label="Research documents and filters"><div class="research-document-links">${data.documents.map(d=>`<a class="button" href="/api/research/document?name=${encodeURIComponent(d.key)}" target="_blank" rel="noopener">${safe(d.title)} ↗</a>`).join(' ')}</div><div class="research-filter-row"><label for="researchSearch">Find strategy</label><input id="researchSearch" type="search" placeholder="Name, formula or entry rule"><label for="researchCategory">Research category</label><select id="researchCategory"><option value="">All categories</option>${data.categories.map(c=>`<option>${safe(c)}</option>`).join('')}</select><p id="researchCount" role="status"></p></div><details><summary>Collection and review coverage</summary><p>${safe(data.coverage.status)}</p><p>${safe(data.coverage.article_review)}</p></details></section>
   <section class="panel paper-review" aria-label="Strategy comparison"><h2>Compare strategy rules</h2><p>Select up to three candidates below. Comparison is read-only; it does not activate a strategy.</p><div id="researchComparison" role="region" aria-label="Selected strategy comparison" tabindex="0" style="overflow-x:auto"><p>No strategies selected.</p></div><p id="comparisonStatus" role="status"></p></section><div id="researchCandidates" class="research-candidates"></div>
   <details class="panel paper-review research-source-list"><summary>Original sources and evidence · ${data.sources.length} references</summary>${data.sources.map(s=>`<p><a href="${url(s.url)}" target="_blank" rel="noopener">${safe(s.id)} · ${safe(s.title)} ↗</a><br><small>${safe(s.evidence)}</small></p>`).join('')}</details>
   <details class="panel paper-review"><summary>Review process before paper monitoring</summary><ol><li>Verify authorized data, calendar, fees and point-in-time availability.</li><li>Freeze rules and parameters; run causal replay with a cash baseline.</li><li>Validate chronologically with purged labels and cost stress tests.</li><li>Review evidence and explicitly approve a version. This view cannot activate it.</li></ol><p>Offline runner: <code>scripts/replay_research_strategy.py</code>. Outputs remain separate from the live simulation ledger.</p></details></div>`;
  const selected=new Set();
  const compare=()=>{
   const rows=data.strategies.filter((s,i)=>selected.has(i));
   const fields=[['Formula','formula'],['Entry','entry_rules'],['Exit','exit_rules'],['Required data','data_requirements'],['Validation','validation'],['Limitations','limitations']];
   root.querySelector('#researchComparison').innerHTML=rows.length?`<table><caption>Experimental candidates · no performance inferred</caption><thead><tr><th scope="col">Rule</th>${rows.map(s=>`<th scope="col">${safe(s.name)} · ${safe(s.version)}</th>`).join('')}</tr></thead><tbody>${fields.map(([label,key])=>`<tr><th scope="row">${label}</th>${rows.map(s=>`<td>${safe(s[key])}</td>`).join('')}</tr>`).join('')}</tbody></table>`:'<p>No strategies selected.</p>';
  };
  const render=()=>{const category=root.querySelector('#researchCategory').value;const query=root.querySelector('#researchSearch').value.trim().toLowerCase();const rows=data.strategies.filter(s=>(!category||s.category===category)&&[s.name,s.formula,s.entry_rules,s.category].join(' ').toLowerCase().includes(query));root.querySelector('#researchCount').textContent=`${rows.length} candidates · 0 newly activated`;
   root.querySelector('#researchCandidates').innerHTML=rows.length?rows.map(s=>`<section class="panel paper-review research-candidate"><div class="research-candidate-heading"><h2>${safe(s.name)}</h2><span class="badge">${safe(s.state)} · DISABLED</span></div><label><input type="checkbox" data-compare="${data.strategies.indexOf(s)}" ${selected.has(data.strategies.indexOf(s))?'checked':''}> Compare ${safe(s.name)}</label><p class="muted">${safe(s.category)} · ${safe(s.version)}</p><p class="research-implementation">${s.executable_offline?'Offline evaluator implemented':'Design only — no evaluator'}</p><details><summary>Formula, rules and evidence</summary><p class="research-formula"><code>${safe(s.formula)}</code></p><dl><dt>Entry</dt><dd>${safe(s.entry_rules)}</dd><dt>Exit</dt><dd>${safe(s.exit_rules)}</dd><dt>Data requirements</dt><dd>${safe(s.data_requirements)}</dd><dt>Limitations</dt><dd>${safe(s.limitations)}</dd></dl><p>${safe(s.validation)}</p><p>Sources: ${safe(s.source_ids.join(', ')||'Mathematical baseline / proposed design')}</p></details></section>`).join(''):'<section class="panel paper-empty">No strategy specified in this category yet. Research coverage is tracked separately; no placeholder signal.</section>';};
  root.querySelector('#researchCategory').onchange=render;
  root.querySelector('#researchSearch').oninput=render;
  root.querySelector('#researchCandidates').onchange=event=>{
   const input=event.target;if(!input.hasAttribute('data-compare'))return;
   const id=Number(input.getAttribute('data-compare'));
   if(input.checked&&selected.size>=3){input.checked=false;root.querySelector('#comparisonStatus').textContent='Select at most three candidates.';return;}
   if(input.checked)selected.add(id);else selected.delete(id);
   root.querySelector('#comparisonStatus').textContent=`${selected.size} candidates selected`;compare();
  };render();
 }catch(error){if(root.contains(marker))root.innerHTML=`<section class="panel paper-empty" role="alert"><h2>Research registry unavailable</h2><p>${safe(error.message)}</p><p>No strategies or performance values asserted. If the server predates this update, restart it to load the research API.</p></section>`;}
}
