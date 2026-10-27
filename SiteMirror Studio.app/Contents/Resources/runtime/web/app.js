const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const API = localStorage.getItem('sitemirror_api') || '';
let currentProject = null;
let projectData = null;
let pollTimer = null;
let recentPollTimer = null;
let recentPollInFlight = false;
let recentProjects = [];
let showAllRecents = false;
let actionProject = null;
const TERMINAL_STATUSES = new Set(['complete','failed','stopped','data_removed']);
let currentView = 'overview';
let systemInfo = null;
const queryParams = new URLSearchParams(location.search);
const IS_NATIVE = queryParams.get('native') === '1';

const fmtDate = value => {
  if (!value) return 'Not available';
  const d = new Date(value);
  return isNaN(d) ? value : new Intl.DateTimeFormat(undefined,{year:'numeric',month:'short',day:'numeric'}).format(d);
};
const bytes = n => !n ? '—' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(1)} MB`;
const esc = s => String(s ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const seoStatus = obj => (obj&&obj.status)||'—';
const seoBadge = obj => { const st=seoStatus(obj); const label=st==='pass'?'Pass':st==='fail'?'Fail':st==='warning'?'Warn':'—'; return `<span class="badge seo-${st}">${label}</span>` };
const issueObjects = p => (p?.seo?.issues||[]).filter(x=>x&&typeof x==='object');
const issueSeverityRank = s => s==='fail'?0:s==='warning'?1:2;
function toast(msg){const t=$('#toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2600)}
async function api(path, options={}){const r=await fetch(API+path,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});if(!r.ok)throw new Error(await r.text());return r.json()}

function normalizeWebsiteUrl(value){
  let raw=String(value||'').trim();
  if(!raw)return '';
  if(raw.startsWith('//')) raw='https:'+raw;
  else if(!/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) raw='https://'+raw;
  try{const u=new URL(raw);if(!['http:','https:'].includes(u.protocol)||!u.hostname)return '';return u.href}catch(e){return ''}
}
function contactMarkup(items,type){
  const list=Array.isArray(items)?items:[];
  if(!list.length)return '<span class="contact-empty">Not found</span>';
  return list.slice(0,8).map(item=>{
    const value=typeof item==='string'?item:(item?.value||'');
    if(!value)return '';
    const count=typeof item==='object'?(item.page_count||item.pages?.length||0):0;
    const sources=typeof item==='object'?(item.sources||[]):[];
    const tip=[count?`Found on ${count} page${count===1?'':'s'}`:'',sources.length?`Sources: ${sources.join(', ')}`:''].filter(Boolean).join(' · ');
    const href=type==='email'?`mailto:${value}`:`tel:${value.replace(/[^+\d]/g,'')}`;
    return `<a class="contact-chip" href="${esc(href)}" title="${esc(tip)}">${esc(value)}</a>`;
  }).join('')+(list.length>8?`<span class="contact-more">+${list.length-8} more</span>`:'');
}

function isActiveProject(p){return !!p && !TERMINAL_STATUSES.has(p.status)}
function friendlyStatus(status){
  const labels={
    queued:'Queued',starting:'Starting',discovering_direct:'Discovering',crawling_direct:'Crawling',browser_fallback:'Rendering',
    browser_waiting_user:'Waiting for you',browser_attaching:'Attaching browser',browser_verification:'Verification',verification_required:'Verification',crawling_verified_session:'Verified crawl',stop_requested:'Stopping',
    complete:'Complete',failed:'Failed',stopped:'Stopped',data_removed:'Data removed'
  };
  return labels[status]||String(status||'Unknown').replaceAll('_',' ');
}
function showLanding(){
  currentProject=null;projectData=null;clearInterval(pollTimer);pollTimer=null;
  $('#landing').classList.remove('hidden');$('#workspace').classList.add('hidden');loadRecents();
}
function showWorkspace(){
  clearInterval(recentPollTimer);recentPollTimer=null;
  $('#landing').classList.add('hidden');$('#workspace').classList.remove('hidden');
}
function switchView(view){currentView=view;$$('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.view===view));$$('.view-panel').forEach(x=>x.classList.add('hidden'));$(`#view-${view}`)?.classList.remove('hidden');if(view==='compare')fillCompareProjects()}
$$('.nav-item').forEach(b=>b.onclick=()=>switchView(b.dataset.view));$$('[data-view-jump]').forEach(b=>b.onclick=()=>switchView(b.dataset.viewJump));

async function startCrawl(url){
  const normalized=normalizeWebsiteUrl(url);
  if(!normalized){toast('Enter a valid domain or website URL');return}
  try{
    const r=await api('/api/crawl',{method:'POST',body:JSON.stringify({url:normalized,max_pages:1000})});
    currentProject=r.project_id;showWorkspace();$('#crawl-strip').classList.remove('hidden');$('#site-title').textContent='Discovering website…';$('#site-domain').textContent=new URL(normalized).hostname;$('#site-domain').href=normalized;beginPoll();
  }catch(e){toast('Could not start crawl: '+e.message)}
}
$('#url-form').onsubmit=e=>{e.preventDefault();startCrawl($('#url-input').value)};
$('#new-site-side').onclick=showLanding;
$('#refresh-project').onclick=()=>currentProject&&loadProject(currentProject);
$('#refresh-recents').onclick=loadRecents;
$('#mobile-menu').onclick=()=>$('#sidebar').classList.toggle('open');

function recentMatchesStatus(p,mode){
  if(mode==='all')return true;
  if(mode==='active')return isActiveProject(p);
  if(mode==='complete')return p.status==='complete';
  if(mode==='data_removed')return p.status==='data_removed';
  if(mode==='attention')return p.status==='failed'||Number(p.errors||0)>0||Number(p.website?.issues||0)>0;
  return true;
}
function getFilteredRecents(list=recentProjects){
  const query=($('#recent-filter')?.value||'').trim().toLowerCase();
  const mode=$('#recent-status-filter')?.value||'all';
  return list.filter(p=>{
    const w=p.website||{};
    const hay=JSON.stringify([p.url,w.title,w.domain,p.status]).toLowerCase();
    return (!query||hay.includes(query))&&recentMatchesStatus(p,mode);
  });
}
function getVisibleRecents(list=recentProjects){
  const query=($('#recent-filter')?.value||'').trim();
  const mode=$('#recent-status-filter')?.value||'all';
  const filtered=getFilteredRecents(list);
  const forcedAll=!!query||mode!=='all';
  return (showAllRecents||forcedAll)?filtered:filtered.slice(0,6);
}
function recentStatusMarkup(p){
  const active=isActiveProject(p),pct=Math.max(0,Math.min(100,Number(p.progress||0)));
  const verify=['browser_waiting_user','browser_attaching','browser_verification','verification_required'].includes(p.status);
  const stopping=p.status==='stop_requested';
  const status=friendlyStatus(p.status);
  if(stopping)return `<span class="recent-state state-stopping">Stopping…</span>`;
  if(!active)return `<span class="recent-state state-${esc(p.status||'unknown')}">${esc(status)}</span>`;
  return `<div class="recent-progress ${verify?'verification':''}" style="--p:${Math.max(4,pct*3.6)}deg" title="${esc(status)} · ${pct}%"><span>${verify?'!':pct+'%'}</span></div>`;
}
function updateRecentHeader(){
  const activeCount=recentProjects.filter(isActiveProject).length;
  $('#recent-count').textContent=`${recentProjects.length} project${recentProjects.length===1?'':'s'}${activeCount?` · ${activeCount} active`:''}`;
  const query=($('#recent-filter')?.value||'').trim();
  const mode=$('#recent-status-filter')?.value||'all';
  const toggle=$('#toggle-all-recents');
  toggle.classList.toggle('hidden',recentProjects.length<=6&&mode==='all'&&!query);
  toggle.textContent=showAllRecents&&!query&&mode==='all'?'Show less':`View all${recentProjects.length>6?` (${recentProjects.length})`:''}`;
}
function renderRecents(){
  const grid=$('#recent-grid');
  updateRecentHeader();
  const visible=getVisibleRecents();
  if(!visible.length){grid.innerHTML='<div class="empty-card">No matching projects. Change the filter or inspect a new website.</div>';return}
  grid.innerHTML=visible.map(p=>{
    const w=p.website||{},icon=w.favicon||'/app-icon.svg',active=isActiveProject(p),pct=Math.max(0,Math.min(100,Number(p.progress||0)));
    const status=friendlyStatus(p.status);
    const dataTag=p.data_present===false?'<span class="data-removed-chip">files removed</span>':'';
    const stopBtn=active&&p.status!=='stop_requested'?`<button class="recent-stop-btn" data-stop-pid="${esc(p.id)}" type="button" title="Stop crawl">Stop</button>`:'';
    const failReason=p.status==='failed'&&p.browser_reason?`<small class="recent-fail-reason" title="${esc(p.browser_reason)}">${esc(p.browser_reason)}</small>`:'';
    return `<article class="recent-card ${active?'running':''}" data-pid="${esc(p.id)}" data-status="${esc(p.status||'')}">
      <div class="recent-card-top">
        <div class="recent-site"><img src="${esc(icon)}" data-icon-src="${esc(icon)}" onerror="this.onerror=null;this.src='/app-icon.svg'"><div><strong title="${esc(w.title||p.url)}">${esc(w.title||p.url)}</strong><small title="${esc(w.domain||p.url)}">${esc(w.domain||p.url)}</small></div></div>
        <div class="recent-card-controls"><button class="recent-menu-btn" data-menu-pid="${esc(p.id)}" aria-label="Project options" title="Project options">•••</button></div>
      </div>
      <div class="recent-meta"><span class="recent-pages">${p.pages||0} pages</span><span class="recent-assets">${p.assets||0} assets</span><span class="recent-data-tag">${dataTag}</span>${stopBtn}<div class="recent-run-state">${recentStatusMarkup(p)}</div></div>
      ${failReason}
      <div class="recent-progress-line ${active?'':'hidden'}"><i style="width:${pct}%"></i></div>
    </article>`;
  }).join('');
}
function patchRecents(next){
  const previous=recentProjects;
  recentProjects=next;
  const cards=[...document.querySelectorAll('#recent-grid .recent-card')];
  const currentIds=cards.map(c=>c.dataset.pid);
  const nextVisible=getVisibleRecents(next);
  const nextIds=nextVisible.map(p=>String(p.id));
  if(currentIds.length!==nextIds.length||currentIds.some((id,i)=>id!==nextIds[i])){renderRecents();return}
  updateRecentHeader();
  nextVisible.forEach(p=>{
    const card=document.querySelector(`#recent-grid .recent-card[data-pid="${CSS.escape(String(p.id))}"]`);if(!card)return;
    const w=p.website||{},active=isActiveProject(p),pct=Math.max(0,Math.min(100,Number(p.progress||0))),status=friendlyStatus(p.status);
    card.classList.toggle('running',active);card.dataset.status=p.status||'';
    const title=card.querySelector('.recent-site strong');if(title){title.textContent=w.title||p.url;title.title=w.title||p.url}
    const domain=card.querySelector('.recent-site small');if(domain)domain.textContent=w.domain||p.url;
    const img=card.querySelector('.recent-site img'),icon=w.favicon||'/app-icon.svg';
    if(img&&img.dataset.iconSrc!==icon){img.dataset.iconSrc=icon;img.onerror=()=>{img.onerror=null;img.src='/app-icon.svg'};img.src=icon}
    const run=card.querySelector('.recent-run-state');if(run)run.innerHTML=recentStatusMarkup(p);
    let stop=card.querySelector('.recent-stop-btn');
    if(active&&p.status!=='stop_requested'){
      if(!stop){stop=document.createElement('button');stop.className='recent-stop-btn';stop.type='button';stop.dataset.stopPid=p.id;stop.title='Stop crawl';stop.textContent='Stop';const meta=card.querySelector('.recent-meta'),runState=card.querySelector('.recent-run-state');meta?.insertBefore(stop,runState)}
      stop.disabled=false;stop.textContent='Stop';
    }else if(stop){stop.remove()}
    const pages=card.querySelector('.recent-pages');if(pages)pages.textContent=`${p.pages||0} pages`;
    const assets=card.querySelector('.recent-assets');if(assets)assets.textContent=`${p.assets||0} assets`;
    const dataTag=card.querySelector('.recent-data-tag');if(dataTag)dataTag.innerHTML=p.data_present===false?'<span class="data-removed-chip">files removed</span>':'';
    const line=card.querySelector('.recent-progress-line');if(line){line.classList.toggle('hidden',!active);const bar=line.querySelector('i');if(bar)bar.style.width=`${pct}%`}
  });
}
async function loadRecents(incremental=false){
  if(recentPollInFlight)return;
  recentPollInFlight=true;
  try{
    const next=await api('/api/projects');
    if(incremental&&recentProjects.length&&document.querySelector('#recent-grid .recent-card'))patchRecents(next);else{recentProjects=next;renderRecents()}
    const hasActive=recentProjects.some(isActiveProject);
    if(hasActive&&!$('#landing').classList.contains('hidden')&&!recentPollTimer){recentPollTimer=setInterval(()=>{if(!document.hidden)loadRecents(true)},1800)}
    if(!hasActive&&recentPollTimer){clearInterval(recentPollTimer);recentPollTimer=null}
  }catch(e){console.warn(e)}finally{recentPollInFlight=false}
}
$('#recent-filter').oninput=renderRecents;
$('#recent-status-filter').onchange=renderRecents;
$('#toggle-all-recents').onclick=()=>{showAllRecents=!showAllRecents;renderRecents()};
$('#recent-grid').addEventListener('click',e=>{
  const stop=e.target.closest('.recent-stop-btn');if(stop){e.stopPropagation();requestStop(stop.dataset.stopPid,stop);return}
  const menu=e.target.closest('.recent-menu-btn');if(menu){e.stopPropagation();openProjectActions(menu.dataset.menuPid);return}
  const card=e.target.closest('.recent-card');if(card)openProject(card.dataset.pid);
});

function openProjectActions(pid){
  const p=recentProjects.find(x=>x.id===pid);if(!p)return;
  actionProject=p;
  const w=p.website||{};
  $('#project-actions-title').textContent=w.title||w.domain||p.url||'Website project';
  $('#project-actions-url').textContent=p.url||w.domain||'—';
  $('#project-action-summary').innerHTML=`<span>${p.pages||0} pages</span><span>${p.assets||0} assets</span><span>${esc(friendlyStatus(p.status))}</span>${p.data_present===false?'<span>Data removed</span>':''}`;
  const active=isActiveProject(p);
  $('#project-action-warning').classList.toggle('hidden',!active);
  const stopRow=$('#project-action-stop');stopRow.classList.toggle('hidden',!active);stopRow.disabled=p.status==='stop_requested';
  stopRow.querySelector('strong').textContent=p.status==='stop_requested'?'Stopping…':'Stop crawl';
  $$('[data-delete-mode]').forEach(btn=>{
    btn.disabled=active||(btn.dataset.deleteMode==='data'&&p.data_present===false);
  });
  $('#project-actions-dialog').showModal();
}
$('#project-actions-close').onclick=()=>$('#project-actions-dialog').close();
$('#project-actions-dialog').addEventListener('click',e=>{if(e.target===$('#project-actions-dialog'))$('#project-actions-dialog').close()});
async function deleteProjectMode(mode){
  if(!actionProject)return;
  const p=actionProject;
  const messages={
    history:'Remove this project from SiteMirror history? Downloaded files will stay on disk.',
    data:'Delete the downloaded mirror, assets, reports and generated ZIP? The history entry will remain.',
    all:'Permanently delete this project AND all downloaded SiteMirror data? This cannot be undone.'
  };
  if(!confirm(messages[mode]))return;
  try{
    const r=await api(`/api/projects/${p.id}?mode=${encodeURIComponent(mode)}`,{method:'DELETE'});
    $('#project-actions-dialog').close();actionProject=null;toast(r.message||'Project updated.');await loadRecents();
  }catch(e){toast('Could not delete project: '+e.message)}
}
$$('[data-delete-mode]').forEach(btn=>btn.onclick=()=>deleteProjectMode(btn.dataset.deleteMode));

async function openProject(pid){currentProject=pid;showWorkspace();await loadProject(pid);await loadLocations();const s=projectData.state;if(!TERMINAL_STATUSES.has(s.status))beginPoll()}
function beginPoll(){clearInterval(pollTimer);loadProject(currentProject);pollTimer=setInterval(async()=>{if(!currentProject||document.hidden)return;await loadProject(currentProject);if(TERMINAL_STATUSES.has(projectData?.state?.status)){clearInterval(pollTimer);pollTimer=null}},1500)}
document.addEventListener('visibilitychange',()=>{if(document.hidden)return;if(currentProject)loadProject(currentProject);else loadRecents()})

async function loadProject(pid){
  try{projectData=await api(`/api/projects/${pid}`);renderProject(projectData)}catch(e){console.warn(e)}
}
function renderProject(data){
  const s=data.state||{},w=data.website||s.website||{},pages=data.pages||[],assets=data.assets||[],errors=data.errors||[],discovery=data.discovery||{};
  const running=!TERMINAL_STATUSES.has(s.status);
  $('#crawl-strip').classList.toggle('hidden',!running);
  const stopButton=$('#stop-crawl');
  if(stopButton){const stopping=s.status==='stop_requested';stopButton.disabled=stopping;stopButton.innerHTML=stopping?'Stopping…':'<span aria-hidden="true">■</span> Stop crawl'}
  const verificationRequired=['browser_waiting_user','browser_verification','verification_required'].includes(s.status);
  $('#verification-panel')?.classList.toggle('hidden',!verificationRequired);
  const browserReadyBtn=$('#browser-ready-btn');
  if(browserReadyBtn){browserReadyBtn.disabled=s.status!=='browser_waiting_user';browserReadyBtn.textContent=s.status==='browser_waiting_user'?'Continue — the real website is visible':'Waiting for crawler…'}
  if(running){
    $('#progress-bar').style.width=`${s.progress||0}%`;$('#progress-label').textContent=`${s.progress||0}%`;
    const labels={
      discovering_direct:'Checking robots.txt & sitemaps…',
      crawling_direct:'Crawling without a browser…',
      browser_fallback:'Using background browser fallback…',
      browser_waiting_user:'Browser opened — waiting for you…',
      browser_attaching:'Attaching to the browser you confirmed…',
      browser_verification:'Human verification required…',
      verification_required:'Security verification required…',
      crawling_verified_session:'Crawling with your verified browser session…',
      stop_requested:'Stopping crawl…',
      starting:'Starting discovery…'
    };
    $('#crawl-title').textContent=labels[s.status]||'Crawling website…';
    $('#crawl-current').textContent=verificationRequired
      ? (s.current_url||s.url||'Complete verification in the dedicated SiteMirror browser')
      : (s.current_url||(s.status==='discovering_direct'?'Inspecting standards, sitemap indexes, HTML sitemaps and feeds':s.status==='crawling_verified_session'?'Same verified session · crawler browser minimized':'Discovering pages and assets'));
  }
  $('#site-title').textContent=w.title||new URL(s.url).hostname;
  $('#site-domain').textContent=w.domain||new URL(s.url).hostname;$('#site-domain').href=s.url;
  const logo=w.logo||w.favicon;$('#site-logo').src=logo||'';$('#site-logo').style.display=logo?'block':'none';$('#site-logo-fallback').style.display=logo?'none':'block';$('#site-logo-fallback').textContent=(w.title||'W')[0].toUpperCase();
  $('#browser-label').textContent=w.browser_engine||s.browser_engine||'Crawler ready';$('#engine-pill').textContent=w.browser_engine||s.browser_engine||'Crawler';
  $('#stat-pages').textContent=s.pages??w.pages??pages.length;$('#stat-assets').textContent=s.assets??w.assets??assets.length;$('#stat-issues').textContent=w.issues??issueCount(pages,errors);
  $('#nav-pages').textContent=s.pages??pages.length;$('#nav-assets').textContent=s.assets??assets.length;$('#nav-issues').textContent=w.issues??issueCount(pages,errors);$('#nav-discovery').textContent=(discovery.sitemaps||[]).filter(x=>x.valid).length||'0';
  const issues=issueCount(pages,errors);const seo=w.seo||{};const score=Number.isFinite(seo.score)?seo.score:(pages.length?Math.max(0,Math.round(100-(issues/Math.max(1,pages.length*4)*100))):0);$('#health-score').textContent=pages.length?score:'—';$('#health-bar').style.width=(pages.length?score:0)+'%';$('#health-copy').textContent=pages.length?`${pages.length} pages checked · ${seo.failed||0} failures · ${seo.warnings||0} warnings.`:'Scan the website to calculate coverage.';
  const fidelity=w.mirror_fidelity||{};$('#mirror-fidelity').textContent=Number.isFinite(fidelity.score)?`${fidelity.score}%`:'—';$('#seo-fail').textContent=seo.failed??'—';$('#seo-warn').textContent=seo.warnings??'—';$('#seo-pass').textContent=seo.passed_pages??'—';$('#localized-assets').textContent=fidelity.localized??'—';
  const d=w.domain_registration||{};$('#domain-age').textContent=d.domain_age||'Not available';$('#domain-age').title=d.registered_at?`RDAP registration: ${fmtDate(d.registered_at)} · ${d.domain_age_days??'—'} days · ${d.registrar||'registrar unavailable'}`:'RDAP registration date not available';$('#registered-at').textContent=fmtDate(d.registered_at);$('#expires-at').textContent=fmtDate(d.expires_at);$('#registrar').textContent=d.registrar||'Not available';$('#last-modified').textContent=fmtDate(w.last_modified);$('#last-published').textContent=fmtDate(w.last_published);
  const contacts=w.contacts||{};$('#contact-emails').innerHTML=contactMarkup(contacts.emails||[],'email');$('#contact-phones').innerHTML=contactMarkup(contacts.phones||[],'phone');
  renderIssueSummary(pages,errors,w.seo||{});renderOverviewPages(pages);renderPages(pages);renderAssets(assets);renderIssues(pages,errors,w.seo||{});renderDiscovery(discovery,w,s);
}

function renderDiscovery(d,w,s){
  const robots=d.robots||{},sitemaps=d.sitemaps||[],htmlMaps=d.html_sitemaps||[],feeds=d.feeds||[],standards=d.standards||[];
  const validMaps=sitemaps.filter(x=>x.valid);
  $('#disc-robots').textContent=robots.present?'Found':(robots.status===404||robots.status===410?'Not present':robots.status?'Check':'—');
  $('#disc-robots-copy').textContent=robots.present?`${robots.allow_rules||0} allow · ${robots.disallow_rules||0} disallow rules`:(robots.status===404||robots.status===410?'Allowed; no robots rules published':'Site-root audit');
  $('#disc-sitemaps').textContent=validMaps.length;
  $('#disc-urls').textContent=d.urls_discovered??'—';
  const visible=d.visible_browser_used??w.visible_browser_used??false,browserUsed=d.browser_used??w.browser_used??false;
  $('#disc-browser').textContent=visible?'Visible':browserUsed?'Background':'Not used';
  $('#disc-browser-copy').textContent=visible?(d.browser_reason||w.browser_reason||'Human verification required'):browserUsed?'Background rendering only; no user action required':'Direct HTTP was sufficient';
  $('#robots-url').textContent=robots.url||'/robots.txt';$('#robots-http').textContent=robots.status??'—';$('#robots-allow').textContent=robots.allow_rules??'—';$('#robots-disallow').textContent=robots.disallow_rules??'—';$('#robots-sitemap-count').textContent=(robots.sitemaps||[]).length;
  const robotsGood=robots.valid!==false;$('#robots-status-pill').textContent=robots.present?'Present':(robots.status===404||robots.status===410?'Optional / absent':robotsGood?'OK':'Issue');
  $('#robots-assessment').textContent=robotsGood?'Acceptable':'Needs attention';
  const rnotes=[...(robots.issues||[]),...(robots.notes||[])];$('#robots-notes').textContent=rnotes.length?rnotes.join(' · '):'robots.txt is checked at the site root. Missing robots.txt is not automatically an error.';

  $('#standards-list').innerHTML=standards.length?standards.map(x=>`<div class="issue-row"><i class="issue-dot ${x.status==='good'?'good-dot':x.status==='issue'?'bad-dot':'info-dot'}"></i><strong>${esc(x.name)}</strong><small>${esc(x.detail||x.status)}</small></div>`).join(''):'<div class="empty-mini">Discovery audit is running.</div>';
  $('#sitemaps-body').innerHTML=sitemaps.length?sitemaps.map(x=>{const issues=[...(x.issues||[]),...(x.notes||[])];return `<tr><td class="cell-title"><strong>${esc((x.final_url||x.url||'').split('/').pop()||x.url)}</strong><small>${esc(x.url||'')}</small></td><td>${esc(x.source||'—')}</td><td>${esc(x.kind||'—')}</td><td><span class="badge ${x.status&&x.status<400?'good':'warn'}">${x.status??'—'}</span></td><td>${x.url_count??0}</td><td>${x.child_sitemaps??0}</td><td>${x.lastmod_count??0}</td><td><span class="badge ${x.valid?'good':'warn'}" title="${esc(issues.join(' · '))}">${x.valid?'Valid':issues[0]||'Check'}</span></td></tr>`}).join(''):'<tr><td colspan="8"><div class="empty-mini">No sitemap object found yet.</div></td></tr>';
  const alts=[...htmlMaps,...feeds];$('#alternate-discovery-body').innerHTML=alts.length?alts.map(x=>`<tr><td class="cell-title"><strong>${esc((x.url||'').split('/').pop()||x.url)}</strong><small>${esc(x.url||'')}</small></td><td>${esc(x.kind||'—')}</td><td>${esc(x.source||'—')}</td><td>${x.status??'—'}</td><td>${x.url_count??0}</td><td><span class="badge ${x.valid?'good':'warn'}">${x.valid?'Useful':'Check'}</span></td></tr>`).join(''):'<tr><td colspan="6"><div class="empty-mini">No HTML sitemap or feed discovered.</div></td></tr>';
}
function issueCount(pages,errors){return pages.reduce((n,p)=>n+(p?.seo?.issues?.length??(p.issues||[]).length),0)+errors.length}
function renderIssueSummary(pages,errors,seoSummary={}){
  const map={};
  const summaryIssues=seoSummary.issues||[];
  if(summaryIssues.length){summaryIssues.forEach(i=>{const k=i.message||i.code;const row=map[k]||(map[k]={count:0,severity:i.severity||'warning'});row.count++;if(issueSeverityRank(i.severity)<issueSeverityRank(row.severity))row.severity=i.severity})}else pages.forEach(p=>issueObjects(p).forEach(i=>{const k=i.message||i.code;const row=map[k]||(map[k]={count:0,severity:i.severity||'warning'});row.count++;if(issueSeverityRank(i.severity)<issueSeverityRank(row.severity))row.severity=i.severity}));
  errors.forEach(e=>{const k=e.type==='asset'?'Asset download failed':'Crawler error';const row=map[k]||(map[k]={count:0,severity:'fail'});row.count++});
  const rows=Object.entries(map).sort((a,b)=>issueSeverityRank(a[1].severity)-issueSeverityRank(b[1].severity)||b[1].count-a[1].count).slice(0,6);
  $('#issue-summary').innerHTML=rows.length?rows.map(([k,v])=>`<div class="issue-row"><i class="issue-dot sev-dot-${esc(v.severity)}"></i><strong>${esc(k)}</strong><small>${v.count}</small></div>`).join(''):'<div class="empty-mini">No SEO or crawl issues found in the available data.</div>'
}
function renderOverviewPages(pages){$('#overview-pages').innerHTML=pages.length?pages.slice(0,7).map((p,i)=>`<div class="page-row" data-page-index="${i}"><div class="page-main"><strong>${esc(p.title||p.h1||'(Untitled page)')}</strong><small>${esc(new URL(p.url).pathname||'/')}</small></div><span class="${(p.issues||[]).length?'status-warn':'status-ok'}">${p.status||'—'} ${(p.issues||[]).length?'Issue':'Good'}</span><small>${fmtDate(p.modified_at)}</small><span>→</span></div>`).join(''):'<div class="empty-mini">Pages will appear while the scan runs.</div>';$$('#overview-pages .page-row').forEach((r)=>r.onclick=()=>openPage(pages[+r.dataset.pageIndex]))}
function renderPages(pages){
  const q=($('#page-filter').value||'').toLowerCase(),mode=$('#page-status-filter').value;
  const filtered=pages.filter(p=>{const hit=!q||JSON.stringify([p.url,p.title,p.h1,p.meta_description,p.contacts]).toLowerCase().includes(q);const issues=issueObjects(p).length||(p.issues||[]).length;return hit&&(mode==='all'||mode==='issues'&&issues||mode==='good'&&!issues)});
  $('#pages-body').innerHTML=filtered.map(p=>{const titleSeo=p.seo?.title||{},descSeo=p.seo?.description||{},age=p.url_age||{};return `<tr data-url="${encodeURIComponent(p.url)}"><td class="cell-title"><strong>${esc(p.title||'(Untitled page)')}</strong><small>${esc(p.url)}</small></td><td><span class="badge ${p.status&&p.status<400?'good':'warn'}">${p.status||'—'}</span></td><td><span class="age-cell" title="${esc(age.source?`${age.source} · ${age.meaning||''}`:'No creation-like date evidence found')}">${esc(age.label||'—')}</span></td><td>${seoBadge(titleSeo)} <small class="metric-mini">${titleSeo.chars??0}c · ~${titleSeo.estimated_px??0}px</small></td><td>${seoBadge(descSeo)} <small class="metric-mini">${descSeo.chars??0}c · ~${descSeo.estimated_px??0}px</small></td><td><span title="${esc(p.published_source||'')}">${fmtDate(p.published_at)}</span></td><td><span title="${esc(p.modified_source||'')}">${fmtDate(p.modified_at)}</span></td><td><span class="badge ${issueObjects(p).some(i=>i.severity==='fail')?'seo-fail':issueObjects(p).length?'seo-warning':'good'}">${issueObjects(p).length||(p.issues||[]).length}</span></td><td>→</td></tr>`}).join('');
  $$('#pages-body tr').forEach(r=>r.onclick=()=>openPage(pages.find(p=>p.url===decodeURIComponent(r.dataset.url))))
}
$('#page-filter').oninput=()=>projectData&&renderPages(projectData.pages||[]);$('#page-status-filter').onchange=()=>projectData&&renderPages(projectData.pages||[]);
function renderAssets(assets){const q=($('#asset-filter').value||'').toLowerCase();const list=assets.filter(a=>!q||JSON.stringify(a).toLowerCase().includes(q));$('#assets-body').innerHTML=list.slice(0,3000).map(a=>`<tr><td class="cell-title"><strong>${esc((a.url||'').split('/').pop()||a.url)}</strong><small>${esc(a.url)}</small></td><td>${esc(a.content_type||'file')}</td><td>${bytes(a.bytes)}</td><td>${esc(a.saved_path||'—')}</td></tr>`).join('')}
$('#asset-filter').oninput=()=>projectData&&renderAssets(projectData.assets||[]);
function renderIssues(pages,errors,seoSummary={}){
  let rows=[];
  if((seoSummary.issues||[]).length){rows=(seoSummary.issues||[]).map(i=>({...i,pageTitle:i.title||i.url||''}))}else pages.forEach(p=>{const objs=issueObjects(p);if(objs.length)objs.forEach(i=>rows.push({...i,url:p.url,pageTitle:p.title||p.h1||p.url}));else (p.issues||[]).forEach(i=>rows.push({message:i,severity:'warning',category:'legacy',url:p.url,pageTitle:p.title||p.h1||p.url}))});
  errors.forEach(e=>rows.push({message:e.type==='asset'?'Asset could not be saved':'Crawler error',severity:'fail',category:e.type||'crawler',url:e.url||'',pageTitle:e.error||(`HTTP ${e.status}`),recommendation:'Review the crawl error or retry the affected resource.'}));
  rows.sort((a,b)=>issueSeverityRank(a.severity)-issueSeverityRank(b.severity));
  $('#issues-list').innerHTML=rows.length?rows.map(x=>`<div class="issue-card severity-${esc(x.severity||'warning')}"><i></i><div><div class="issue-meta"><span class="sev ${esc(x.severity||'warning')}">${esc(x.severity||'warning')}</span><span>${esc(x.category||'SEO')}</span></div><strong>${esc(x.message||x.code)}</strong><small>${esc(x.pageTitle||'')}</small>${x.evidence?`<small><b>Evidence:</b> ${esc(x.evidence)}</small>`:''}${x.recommendation?`<small><b>Fix:</b> ${esc(x.recommendation)}</small>`:''}</div>${x.url?`<a href="${esc(x.url)}" target="_blank">Open ↗</a>`:''}</div>`).join(''):'<div class="empty-mini">Nothing needs attention based on the collected data.</div>'
}
function openPage(p){
  if(!p)return;
  const t=p.seo?.title||{},d=p.seo?.description||{},age=p.url_age||{},contacts=p.contacts||{};
  const issueHtml=issueObjects(p).length?issueObjects(p).map(i=>`<div class="mini-seo-issue severity-${esc(i.severity)}"><span class="sev ${esc(i.severity)}">${esc(i.severity)}</span><div><strong>${esc(i.message)}</strong><small>${esc(i.recommendation||'')}</small></div></div>`).join(''):'<div class="empty-mini">No page-level SEO issues found.</div>';
  $('#page-dialog-content').innerHTML=`<div class="detail-title"><div><span class="object-kicker">Page object</span><h2>${esc(p.title||p.h1||'Untitled page')}</h2><p>${esc(p.url)}</p></div></div>
  <div class="seo-preview-grid"><div class="seo-preview"><span>Meta title ${seoBadge(t)}</span><strong>${esc(p.title||'Not found')}</strong><small>${t.chars??0} chars · ~${t.estimated_px??0}px · ${esc(t.hard_limit===false?'display heuristic, not a hard Google limit':'')}</small></div><div class="seo-preview"><span>Meta description ${seoBadge(d)}</span><strong>${esc(p.meta_description||'Not found')}</strong><small>${d.chars??0} chars · ~${d.estimated_px??0}px · display heuristic</small></div></div>
  <div class="detail-grid"><div class="detail-item"><span>Status</span><strong>${p.status||'—'}</strong></div><div class="detail-item"><span>H1</span><strong>${esc(p.h1||'Not found')} ${p.seo?.h1_count?`(${p.seo.h1_count})`:''}</strong></div><div class="detail-item"><span>URL age evidence <i class="info-tip" data-tip="Published-date evidence is used when available. Sitemap lastmod is not treated as creation age.">i</i></span><strong>${esc(age.label||'Not established')} ${age.source?`· ${esc(age.source)}`:''}</strong><button class="micro-btn" id="check-url-history">Check historical first-seen</button><small id="url-history-result">Internet Archive lookup is on-demand so scans stay fast.</small></div><div class="detail-item"><span>Modified</span><strong>${fmtDate(p.modified_at)}${p.modified_source?` · ${esc(p.modified_source)}`:''}</strong></div><div class="detail-item"><span>Published</span><strong>${fmtDate(p.published_at)}${p.published_source?` · ${esc(p.published_source)}`:''}</strong></div><div class="detail-item"><span>Canonical</span><strong>${esc(p.canonical||'Not found')}</strong></div><div class="detail-item"><span>Mirror fidelity</span><strong>${p.mirror_fidelity?.asset_fidelity??'—'}%</strong></div><div class="detail-item"><span>Internal inlinks</span><strong>${p.inlinks??'—'}</strong></div><div class="detail-item"><span>Email found on page</span><div class="contact-values">${contactMarkup(contacts.emails||[],'email')}</div></div><div class="detail-item"><span>Phone found on page</span><div class="contact-values">${contactMarkup(contacts.phones||[],'phone')}</div></div></div>
  <div class="detail-issues"><span class="object-kicker">SEO issue objects</span><h3>Page audit</h3>${issueHtml}</div>`;
  $('#page-dialog').showModal();
  const btn=$('#check-url-history');if(btn)btn.onclick=async()=>{btn.disabled=true;btn.textContent='Checking…';const out=$('#url-history-result');try{const r=await api(`/api/url-history?url=${encodeURIComponent(p.url)}`);if(r.available){out.innerHTML=`First public archive capture: <b>${fmtDate(r.first_seen_at)}</b> · approximately <b>${esc(r.url_age||'—')}</b> ago. <a href="${esc(r.capture_url)}" target="_blank">Open capture ↗</a><br><em>${esc(r.note||'')}</em>`}else{out.textContent=`No public historical capture returned${r.error?`: ${r.error}`:'.'}`}}catch(e){out.textContent='Historical lookup failed: '+e.message}finally{btn.disabled=false;btn.textContent='Check historical first-seen'}}
}
$('#dialog-close').onclick=()=>$('#page-dialog').close();

async function fillCompareProjects(){try{const list=(await api('/api/projects')).filter(x=>x.id!==currentProject&&x.status==='complete');$('#compare-project').innerHTML=list.length?list.map(x=>`<option value="${x.id}">${esc(x.website?.title||x.url)}</option>`).join(''):'<option>No other completed project</option>'}catch(e){}}
$('#compare-run').onclick=async()=>{const target=$('#compare-project').value;if(!target||!currentProject)return toast('Create or open another completed website first.');try{const r=await api('/api/compare',{method:'POST',body:JSON.stringify({source_project:currentProject,target_project:target})});$('#compare-result').innerHTML=`<div class="compare-summary"><div class="compare-stat"><strong>${r.summary.matches}</strong><small>Matches</small></div><div class="compare-stat"><strong>${r.summary.changed}</strong><small>Changed</small></div><div class="compare-stat"><strong>${r.summary.missing}</strong><small>Missing</small></div><div class="compare-stat"><strong>${r.summary.new}</strong><small>New</small></div></div><div class="issues-list">${r.rows.filter(x=>x.status!=='match').slice(0,100).map(x=>`<div class="issue-card"><i></i><div><strong>${esc(x.path)}</strong><small>${esc(x.status.replaceAll('_',' '))}</small></div></div>`).join('')}</div>`}catch(e){toast('Comparison failed') }};
function browserDownload(){
  if(!currentProject)return toast('Open a project first.');
  const a=document.createElement('a');
  a.href=API+`/api/projects/${currentProject}/export`;
  a.download='';
  document.body.appendChild(a);a.click();a.remove();
}
async function saveZip(){
  if(!currentProject)return toast('Open a project first.');
  try{const r=await api(`/api/projects/${currentProject}/save-export`,{method:'POST',body:'{}'});if(r.cancelled)return toast('Save cancelled. ZIP is still available in SiteMirror exports.');toast(`Saved: ${r.path}`);await loadLocations();}
  catch(e){toast('Could not save ZIP: '+e.message)}
}
async function previewMirror(){
  if(!currentProject)return toast('Open a project first.');
  if(IS_NATIVE){try{await api(`/api/projects/${currentProject}/open-mirror`,{method:'POST',body:'{}'});toast('Mirror opened in your browser.')}catch(e){toast('Mirror is not ready yet.')}}
  else window.open(API+`/api/projects/${currentProject}/mirror/`,'_blank','noopener');
}
async function reveal(kind){
  if(!currentProject&&['project','mirror','zip'].includes(kind))return toast('Open a project first.');
  try{const path=kind==='data'?'/api/system/reveal-data':kind==='exports'?'/api/system/reveal-exports':`/api/projects/${currentProject}/reveal?kind=${encodeURIComponent(kind)}`;const r=await api(path,{method:'POST',body:'{}'});toast(`${systemInfo?.file_manager||'File manager'} opened.`);if(kind==='zip')await loadLocations();return r}catch(e){toast('Could not open location: '+e.message)}
}
async function openProjectBrowser(){
  try{if(currentProject)await api(`/api/projects/${currentProject}/open-in-browser`,{method:'POST',body:'{}'});else await api('/api/system/open-browser',{method:'POST',body:JSON.stringify({path:'/'})});toast('Opened in your default browser.')}catch(e){toast('Could not open browser: '+e.message)}
}
async function loadSystemInfo(){
  try{systemInfo=await api('/api/system/info');$('#file-manager-label').textContent=systemInfo.file_manager||'Files';$('#storage-data-path').textContent=systemInfo.data_dir||'—';$('#storage-exports-path').textContent=systemInfo.exports_dir||'—';$('#show-files-btn').textContent=`Show in ${systemInfo.file_manager||'Files'}`;if(IS_NATIVE)$('#open-browser-btn').classList.remove('hidden');}catch(e){console.warn(e)}
}
async function loadLocations(){
  if(!currentProject){$('#storage-project-path').textContent='Open a project';$('#storage-mirror-path').textContent='—';return}
  try{const r=await api(`/api/projects/${currentProject}/locations`);$('#storage-project-path').textContent=r.project||'—';$('#storage-mirror-path').textContent=r.mirror||'—';$('#storage-exports-path').textContent=r.exports||systemInfo?.exports_dir||'—';}catch(e){console.warn(e)}
}
$('#export-btn').onclick=()=>{switchView('exports');loadLocations()};
$('#save-zip').onclick=saveZip;
$('#download-zip').onclick=browserDownload;
$('#preview-browser').onclick=previewMirror;
$('#preview-btn').onclick=previewMirror;
$('#show-files-btn').onclick=()=>reveal('project');
$('#reveal-data').onclick=()=>reveal('data');
$('#reveal-project').onclick=()=>reveal('project');
$('#reveal-mirror').onclick=()=>reveal('mirror');
$('#reveal-exports').onclick=()=>reveal('exports');
$('#reveal-zip').onclick=()=>reveal('zip');
$('#open-project-browser').onclick=openProjectBrowser;
$('#open-browser-btn').onclick=openProjectBrowser;

$('#command').addEventListener('keydown',e=>{if(e.key==='Enter'){const q=e.target.value.trim();if(/^https?:\/\//i.test(q)||/^\/\//.test(q)||/^[\w.-]+\.[a-z]{2,}(?:[\/:?#]|$)/i.test(q)){startCrawl(q);e.target.value='';return}switchView('pages');$('#page-filter').value=q;projectData&&renderPages(projectData.pages||[])}});document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#command').focus()}});

async function requestStop(pid,button=null){
  if(!pid)return;
  const original=button?.innerHTML;
  if(button){button.disabled=true;button.textContent='Stopping…'}
  const local=recentProjects.find(x=>String(x.id)===String(pid));
  if(local){local.status='stop_requested';patchRecents(recentProjects)}
  try{
    const r=await api(`/api/projects/${pid}/stop`,{method:'POST',body:'{}'});
    if(local)local.status=r.status||'stop_requested';
    toast(r.status==='stopped'?'Crawl stopped. Downloaded data was kept.':'Stopping crawl… downloaded data will be kept.');
    if(String(currentProject)===String(pid))await loadProject(pid);
    if(!$('#landing').classList.contains('hidden'))await loadRecents(true);
    if(r.status!=='stopped'&&String(currentProject)===String(pid))beginPoll();
    return r;
  }catch(e){
    toast('Could not stop crawl: '+e.message);
    if(button){button.disabled=false;button.innerHTML=original||'Stop'}
    throw e;
  }
}
async function confirmBrowserReady(){
  if(!currentProject)return;
  const btn=$('#browser-ready-btn');
  if(btn){btn.disabled=true;btn.textContent='Attaching…'}
  try{
    await api(`/api/projects/${currentProject}/browser-ready`,{method:'POST',body:'{}'});
    toast('Using that exact browser session');
    await loadProject(currentProject);
  }catch(e){
    toast('Could not attach browser: '+e.message);
    if(btn){btn.disabled=false;btn.textContent='Continue — the real website is visible'}
  }
}
async function stopCrawl(){if(currentProject)await requestStop(currentProject,$('#stop-crawl')).catch(()=>{})}
$('#browser-ready-btn').onclick=confirmBrowserReady;
$('#verification-stop-btn').onclick=stopCrawl;
$('#stop-crawl').onclick=stopCrawl;
$('#project-action-stop').onclick=async()=>{
  if(!actionProject)return;
  const pid=actionProject.id;
  await requestStop(pid,$('#project-action-stop')).catch(()=>{});
  $('#project-actions-dialog').close();actionProject=null;await loadRecents(true);
};

if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
loadSystemInfo();
const param=queryParams.get('url'),projectParam=queryParams.get('project');if(projectParam){openProject(projectParam)}else if(param){$('#url-input').value=param;startCrawl(param)}else loadRecents();
