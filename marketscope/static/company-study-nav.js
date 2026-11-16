const navigation=document.querySelector('#navigation')||document.querySelector('#resultsNavigation nav');
if(navigation&&!navigation.querySelector('[href="/company-research"]')){
 const link=document.createElement('a');
 link.href='/company-research';link.className='results-nav-link';
 link.textContent='◈ Earnings study';link.setAttribute('aria-label','Company earnings study and model readiness');
 const before=navigation.querySelector('[data-view="methodology"], [href="/#methodology"]');
 navigation.insertBefore(link,before);
}
