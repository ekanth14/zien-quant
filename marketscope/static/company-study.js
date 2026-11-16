const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number=(value,digits=2)=>typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('en-IN',{maximumFractionDigits:digits}):'Unavailable';
const time=ts=>new Date(ts*1000).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',hour12:false});
let selected='',landmark='09:40',sequence=0;

function chart(bars){
 if(!bars.length)return '<p class="empty">Company prices are missing from this research snapshot.</p>';
 const prices=bars.flatMap(b=>[b.h,b.l]),low=Math.min(...prices),high=Math.max(...prices),span=high-low||1;
 const x=i=>60+i/Math.max(1,bars.length-1)*720,y=p=>190-(p-low)/span*160;
 const lines=[0,.5,1].map(f=>{const v=low+f*span;return `<line class="grid" x1="60" y1="${y(v)}" x2="780" y2="${y(v)}"/><text x="5" y="${y(v)+4}">${number(v)}</text>`}).join('');
 return `<svg class="chart" viewBox="0 0 820 235" role="img" aria-label="Observed company close prices from 09:15 to 11:00 IST">${lines}<line class="reference" x1="60" y1="${y(bars[0].o)}" x2="780" y2="${y(bars[0].o)}"/><polyline class="price" points="${bars.map((b,i)=>`${x(i)},${y(b.c)}`).join(' ')}"/>${[0,Math.floor(bars.length/2),bars.length-1].map(i=>`<text x="${x(i)-18}" y="220">${time(bars[i].ts+300)}</text>`).join('')}</svg><p class="chart-note">Full historical morning · five-minute completed closes · dashed line: 09:15 open. This chart includes later observations and is not a past forecast.</p>`;
}
function renderDetail(detail,landmarks){
 const {event:e,current,previous,forecast}=detail;
 const safeSources=detail.source_urls.filter(url=>{try{const u=new URL(url);return u.protocol==='https:'&&!u.username&&!u.password}catch{return false}});
 const forecastMarkup=forecast.predictions?`<div class="abstain"><h3>Validated conditional research output</h3><p>${esc(forecast.status)}</p><pre>${esc(JSON.stringify(forecast.predictions,null,2))}</pre><small>Conditional estimates from the server model registry.</small></div>`:`<div class="abstain"><h3>Forecast unavailable</h3><p>${esc(forecast.status.replaceAll('_',' '))}</p><ul>${(forecast.blockers||[]).map(v=>`<li>${esc(v.replaceAll('_',' '))}</li>`).join('')}</ul><small>Probabilities and future price levels remain unavailable until model validation passes.</small></div>`;
 $('detail').innerHTML=`<div class="detail-top"><div><div class="eyebrow">${esc(e.symbol)} / ${esc(e.next_trading_date)}</div><h2>${esc(e.company)}</h2><small>Quarter ended ${esc(e.period_end)} · ${esc(current.basis)} · ${esc(current.unit)}</small></div><label>Research checkpoint<select id="landmark">${landmarks.map(t=>`<option ${t===detail.landmark?'selected':''}>${esc(t)}</option>`).join('')}</select></label></div><div class="facts"><div><small>Revenue growth, QoQ</small><strong>${number(e.revenue_qoq_pct)}%</strong></div><div><small>PAT growth, QoQ</small><strong>${number(e.pat_qoq_pct)}%</strong></div><div><small>Observed open → 11:00</small><strong>${e.return_0915_1100_pct===null?'Unavailable':number(e.return_0915_1100_pct)+'%'}</strong></div></div>${chart(detail.candles)}${forecastMarkup}<details><summary>Financial comparison and observed features</summary><table><thead><tr><th>Metric</th><th>Previous quarter</th><th>Current quarter</th></tr></thead><tbody><tr><td>Revenue</td><td>${number(previous.revenue)}</td><td>${number(current.revenue)}</td></tr><tr><td>Profit after tax</td><td>${number(previous.net_profit)}</td><td>${number(current.net_profit)}</td></tr></tbody></table><p>Observed prefix features at ${esc(detail.landmark)} IST; these are descriptive inputs.</p><pre>${esc(JSON.stringify(detail.observed_features,null,2))}</pre><div class="quality">${Object.entries(detail.quality||{}).map(([k,v])=>`<span>${esc(k.replaceAll('_',' '))}: ${v?'recorded':'unverified'}</span>`).join('')}</div></details><details class="sources"><summary>Original source references</summary><p>Financial publication: ${esc(e.announced_at)}. Price sources: ${esc([...new Set(detail.candles.map(b=>b.source))].join('; ')||'Missing')}.</p>${safeSources.map(url=>`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join('')}</details>`;
 $('landmark').onchange=event=>{landmark=event.target.value;load()};
}
async function load(){
 const ticket=++sequence,form=$('filters'),params=new URLSearchParams({search:form.elements.search.value,coverage:form.elements.coverage.value,landmark});
 if(selected)params.set('id',selected);
 try{
  const response=await fetch('/api/company-study?'+params);const data=await response.json();if(!response.ok)throw Error(data.error||'Study unavailable');if(ticket!==sequence)return;
  const c=data.counts;
  $('status').textContent=`Supplied snapshot · ${c.complete_windows} complete company mornings across ${c.dates} dates · ${c.missing_windows} known positive events still lack price windows. Validated forecasts are unavailable.`;
  $('stats').innerHTML=[[c.company_periods,'Company periods in the study'],[c.positive_events,'Known positive financial events'],[c.complete_windows,'Complete company price windows'],[`${c.above_open} / ${c.below_open}`,'Above / below the opening price']].map(([value,label])=>`<div class="stat"><strong>${esc(typeof value==='number'?number(value,0):value)}</strong><small>${esc(label)}</small></div>`).join('');
  $('count').textContent=`${data.events.length} events`;
  $('events').innerHTML=data.events.map(e=>`<button class="event" data-event="${esc(e.id)}" aria-pressed="${selected===e.id}"><strong>${esc(e.symbol)}</strong><small>${esc(e.company)}</small><small>${esc(e.next_trading_date)} · ${esc(e.selected_scope)}</small><span>${e.complete_price_window?'Complete morning chart':'Company prices missing'}</span></button>`).join('')||'<p class="empty">No observations match these filters.</p>';
  $('events').querySelectorAll('[data-event]').forEach(button=>button.onclick=()=>{selected=button.dataset.event;load()});
  $('years').innerHTML=Object.entries(data.years).map(([year,row])=>`<tr><td>${esc(year)}</td>${['events','comparable','positive','positive_complete_mornings'].map(k=>`<td>${number(row[k],0)}</td>`).join('')}</tr>`).join('');
  $('asOf').textContent='Snapshot: '+data.as_of.slice(0,10);
  $('models').innerHTML=Object.entries(data.models).map(([clock,model])=>`<div class="model"><strong>${esc(clock)} IST</strong><small>${esc(model.status.replaceAll('_',' '))}</small></div>`).join('');
  $('limitations').innerHTML=data.limitations.map(value=>`<li>${esc(value)}</li>`).join('');
  if(data.detail)renderDetail(data.detail,data.landmarks);
  else if(data.events.length){selected=(data.events.find(e=>e.complete_price_window)||data.events[0]).id;return load()}
  else $('detail').innerHTML='<p class="empty">Change the filters to view a company observation.</p>';
 }catch(error){if(ticket===sequence)$('status').textContent=error.message}
}
$('filters').onsubmit=event=>{event.preventDefault();selected='';load()};
load();
