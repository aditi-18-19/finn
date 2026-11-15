'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

const API = process.env.NEXT_PUBLIC_FINN_API_URL || 'http://127.0.0.1:8000';

type Tab = 'today' | 'alerts' | 'company' | 'research';
type AnyObj = Record<string, any>;
type FeedPayload = { items?: AnyObj[]; count?: number; summary?: AnyObj };
type AlertPayload = { alerts?: AnyObj[]; count?: number; summary?: AnyObj; config?: AnyObj };
type Rules = { price_change_pct: number; volume_ratio: number; material_categories: string[] };

const CATEGORIES = [
  'results','M&A','fund_raise','management_change','litigation','insider_trading',
  'credit_rating','pledging','capex','dividend','bonus_split','earnings_call',
  'investor_meetings','press_release','postal_ballot','agm_egm'
];

function fmtDate(v: any) {
  if (!v) return 'Date unavailable';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleDateString('en-IN', { day:'numeric', month:'short', year:'numeric' });
}
function val(o: AnyObj, ...keys: string[]): any {
  for (const k of keys) if (o?.[k] !== undefined && o?.[k] !== null && o?.[k] !== '') return o[k];
  return undefined;
}
function money(v: any) {
  if (v === undefined || v === null) return '—';
  const n = Number(v);
  return Number.isFinite(n) ? `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : String(v);
}
function priorityClass(p: any) {
  const s = String(p || '').toLowerCase();
  return s === 'high' ? 'priority high' : s === 'medium' ? 'priority medium' : 'priority routine';
}
function titleOf(x: AnyObj) {
  return val(x,'headline','subject','title','event_title') || 'Filing update';
}
function whyOf(x: AnyObj) {
  return val(x,'why_it_matters','rationale','summary','description','category') || 'Open the source filing to review the details.';
}
function companyOf(x: AnyObj) {
  return val(x,'company','company_name','name','nse_symbol','symbol') || 'Company';
}
function symbolOf(x: AnyObj) {
  return String(val(x,'nse_symbol','symbol','ticker') || '').toUpperCase();
}

function Card({ children, className='' }: {children: React.ReactNode; className?: string}) {
  return <article className={`card ${className}`}>{children}</article>;
}
function Pill({ children, tone='neutral' }: {children: React.ReactNode; tone?: string}) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export default function Home() {
  const [tab,setTab] = useState<Tab>('today');
  const [period,setPeriod] = useState('24h');
  const [customHours,setCustomHours] = useState(48);
  const [feed,setFeed] = useState<FeedPayload>({});
  const [alerts,setAlerts] = useState<AlertPayload>({});
  const [rules,setRules] = useState<Rules>({price_change_pct:5,volume_ratio:2,material_categories:['results','M&A','fund_raise','management_change','litigation','insider_trading','credit_rating','pledging','capex']});
  const [routineOpen,setRoutineOpen] = useState(false);
  const [symbol,setSymbol] = useState('RELIANCE');
  const [company,setCompany] = useState<AnyObj|null>(null);
  const [question,setQuestion] = useState('');
  const [answer,setAnswer] = useState<AnyObj|null>(null);
  const [researchStatus,setResearchStatus] = useState('');
  const [researchResult,setResearchResult] = useState<AnyObj|null>(null);
  const [loading,setLoading] = useState(false);
  const [saving,setSaving] = useState(false);
  const [error,setError] = useState('');
  const [apiUp,setApiUp] = useState(false);

  const hours = period === '3d' ? 72 : period === '7d' ? 168 : period === 'custom' ? customHours : 24;
  const loadFeed = useCallback(async () => {
    const r = await fetch(`${API}/feed?hours=${hours}`);
    if (!r.ok) throw new Error(`Feed request failed (${r.status})`);
    setFeed(await r.json());
  },[hours]);
  const loadAlerts = useCallback(async () => {
    const r = await fetch(`${API}/alerts`);
    if (!r.ok) throw new Error(`Alerts request failed (${r.status})`);
    setAlerts(await r.json());
  },[]);
  const loadRules = useCallback(async () => {
    const r = await fetch(`${API}/alert-rules`);
    if (!r.ok) throw new Error(`Rules request failed (${r.status})`);
    setRules(await r.json());
  },[]);
  useEffect(() => {
    Promise.all([fetch(`${API}/health`),loadFeed(),loadAlerts(),loadRules()])
      .then(([h]) => { if (!h.ok) throw new Error('API health check failed'); setApiUp(true); })
      .catch(e => setError(e.message));
  },[loadFeed,loadAlerts,loadRules]);

  const items = feed.items || [];
  const visibleItems = items.filter(x => String(val(x,'priority','priority_label') || '').toLowerCase() !== 'routine');
  const routineItems = items.filter(x => String(val(x,'priority','priority_label') || '').toLowerCase() === 'routine');
  const high = visibleItems.filter(x => String(val(x,'priority','priority_label')).toLowerCase() === 'high');
  const medium = visibleItems.filter(x => String(val(x,'priority','priority_label')).toLowerCase() !== 'high');

  async function loadCompany() {
    setLoading(true); setError(''); setCompany(null);
    try {
      const r = await fetch(`${API}/company/${encodeURIComponent(symbol.trim().toUpperCase())}`);
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || `Company request failed (${r.status})`);
      setCompany(data);
    } catch(e:any) { setError(e.message); }
    finally { setLoading(false); }
  }
  async function saveRules() {
    setSaving(true); setError('');
    try {
      const r = await fetch(`${API}/alert-rules`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(rules)});
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || 'Could not save alert rules');
      setRules(data);
      await loadAlerts();
    } catch(e:any) { setError(e.message); }
    finally { setSaving(false); }
  }
  async function ask(questionText=question) {
    if (!questionText.trim()) return;
    setLoading(true); setError(''); setAnswer(null);
    try {
      const r = await fetch(`${API}/ask`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:symbol.trim().toUpperCase(),question:questionText,top_k:10})});
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || 'Research request failed');
      setAnswer(data); setQuestion('');
    } catch(e:any) { setError(e.message); }
    finally { setLoading(false); }
  }
  async function runResearch() {
    setResearchResult(null); setResearchStatus('Connecting to research pipeline…'); setError('');
    const controller = new AbortController();
    try {
      const response = await fetch(`${API}/research/${encodeURIComponent(symbol.trim().toUpperCase())}`,{signal:controller.signal});
      if (!response.ok || !response.body) throw new Error(`Research endpoint failed (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {done,value} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value,{stream:true});
        const events = buffer.split('\n\n'); buffer = events.pop() || '';
        for (const block of events) {
          const event = block.match(/^event:\s*(.+)$/m)?.[1];
          const raw = block.match(/^data:\s*(.+)$/m)?.[1];
          if (!raw) continue;
          const data = JSON.parse(raw);
          if (event === 'progress') setResearchStatus(typeof data === 'string' ? data : data.message || JSON.stringify(data));
          if (event === 'result') { setResearchResult(data); setResearchStatus('Research run finished.'); }
          if (event === 'error') throw new Error(data.message || 'Research failed');
        }
      }
    } catch(e:any) { setError(e.message); setResearchStatus('Research did not complete.'); }
  }

  const companyName = company ? val(company.company || {},'name','company_name') || symbol : symbol;
  const priceRecords = company?.price?.records || company?.price?.history || [];
  const latestPrice = company?.latest_price || priceRecords[priceRecords.length-1];
  const announcements = company?.announcements || [];
  const resultCards = company?.results_cards || [];
  const managementCards = company?.management_cards || [];
  const alertRows = alerts.alerts || [];

  return <main className="app-shell">
    <header className="topbar">
      <div><div className="brand">FINN<span>.</span></div><div className="subbrand">INVESTMENT INTELLIGENCE</div></div>
      <div className="connection"><i className={apiUp?'online':''}/>{apiUp?'Connected':'Connecting'}</div>
    </header>

    <section className="welcome">
      <div className="eyebrow">YOUR PORTFOLIO, IN FOCUS</div>
      <h1>{tab==='today'?'Today’s signal.':tab==='alerts'?'What needs attention?':tab==='company'?'Company intelligence.':'Research with evidence.'}</h1>
      <p>Primary-source filings, market signals and company research.</p>
    </section>

    {error && <div className="error-banner"><strong>Something needs attention</strong><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss">×</button></div>}

    <nav className="tabs" aria-label="Main navigation">
      {(['today','alerts','company','research'] as Tab[]).map(t=><button key={t} className={tab===t?'active':''} onClick={()=>{setTab(t);setError('');}}>{t==='today'?'Today':t==='alerts'?'Alerts':t==='company'?'Company':'Research'}</button>)}
    </nav>

    {tab==='today' && <section className="screen">
      <div className="section-head"><div><div className="eyebrow dark">PORTFOLIO FEED</div><h2>Latest developments</h2></div><Pill tone="blue">{feed.count ?? items.length} filings</Pill></div>
      <div className="periods">{['24h','3d','7d','custom'].map(p=><button key={p} className={period===p?'selected':''} onClick={()=>{if(p==='custom'){const raw=window.prompt('Enter number of hours (1–2160)','48');if(raw && Number(raw)>=1 && Number(raw)<=2160){setCustomHours(Number(raw));setPeriod('custom');}} else setPeriod(p);}}>{p==='custom'?'Custom':p}</button>)}</div>
      <div className="summary-grid">
        <div><b>{high.length}</b><span>High priority</span></div><div><b>{medium.length}</b><span>Medium priority</span></div><div><b>{routineItems.length || Number(val(feed.summary || {},'routine_collapsed','routine_hidden','routine_count') || 0)}</b><span>Routine hidden</span></div>
      </div>
      {high.length>0 && <><h3 className="group-title">High priority</h3>{high.map((x,i)=><FeedCard key={i} item={x} onAsk={()=>{setSymbol(symbolOf(x)||symbol);setTab('research');setQuestion(`Explain the significance of this filing: ${titleOf(x)}`);}} onCompany={()=>{setSymbol(symbolOf(x)||symbol);setTab('company');}}/> )}</>}
      {medium.length>0 && <><h3 className="group-title">Medium priority</h3>{medium.map((x,i)=><FeedCard key={i} item={x} onAsk={()=>{setSymbol(symbolOf(x)||symbol);setTab('research');setQuestion(`Explain the significance of this filing: ${titleOf(x)}`);}} onCompany={()=>{setSymbol(symbolOf(x)||symbol);setTab('company');}}/> )}</>}
      {visibleItems.length===0 && <Card><p className="muted">No High or Medium filings in this time window.</p></Card>}
      <button className="routine-toggle" onClick={()=>setRoutineOpen(v=>!v)}>{routineItems.length || Number(val(feed.summary || {},'routine_collapsed','routine_hidden','routine_count') || 0)} routine filings hidden. {routineOpen?'Hide':'Tap to see'} <span>{routineOpen?'−':'+'}</span></button>
      {routineOpen && routineItems.length === 0 && <Card><p className="muted">The current digest output contains only the collapsed routine count, not the individual routine filings. Rebuild the digest with routine items included to display them here.</p></Card>}
      {routineOpen && routineItems.map((x,i)=><FeedCard key={i} item={x} onAsk={()=>{setSymbol(symbolOf(x)||symbol);setTab('research');}} onCompany={()=>{setSymbol(symbolOf(x)||symbol);setTab('company');}}/>)}
      <p className="footnote">Showing the last {hours} hours. Source links open the original filing where available.</p>
    </section>}

    {tab==='alerts' && <section className="screen">
      <div className="section-head"><div><div className="eyebrow dark">TRIGGER SETTINGS</div><h2>Alert rules</h2></div><Pill tone="blue">{alertRows.length} active results</Pill></div>
      <Card>
        <label className="range-label"><span>Price move threshold</span><strong>±{rules.price_change_pct.toFixed(1)}%</strong></label>
        <input type="range" min="0.5" max="20" step="0.5" value={rules.price_change_pct} onChange={e=>setRules({...rules,price_change_pct:Number(e.target.value)})}/>
        <label className="range-label second"><span>Volume multiple</span><strong>{rules.volume_ratio.toFixed(1)}×</strong></label>
        <input type="range" min="1" max="10" step="0.5" value={rules.volume_ratio} onChange={e=>setRules({...rules,volume_ratio:Number(e.target.value)})}/>
        <div className="divider"/>
        <h3 className="group-title no-top">Material categories</h3>
        <div className="check-grid">{CATEGORIES.map(c=><label key={c} className="check"><input type="checkbox" checked={rules.material_categories.includes(c)} onChange={e=>setRules({...rules,material_categories:e.target.checked?[...rules.material_categories,c]:rules.material_categories.filter(x=>x!==c)})}/><span>{c.replaceAll('_',' ')}</span></label>)}</div>
        <button className="primary-btn full" disabled={saving} onClick={saveRules}>{saving?'Saving…':'Save alert rules'}</button>
        <p className="footnote">Saving updates config/rules.json. Run the alert pipeline again to recalculate triggered alerts.</p>
      </Card>
      <div className="section-head list-head"><h2>Triggered alerts</h2><button className="text-btn" onClick={loadAlerts}>Refresh</button></div>
      {alertRows.map((a,i)=><AlertCard key={i} item={a}/>)}
      {!alertRows.length && <Card><p className="muted">No triggered alerts in the current alert output.</p></Card>}
    </section>}

    {tab==='company' && <section className="screen">
      <div className="section-head"><div><div className="eyebrow dark">COMPANY VIEW</div><h2>Company intelligence</h2></div></div>
      <div className="search-row"><input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} placeholder="NSE ticker, e.g. RELIANCE"/><button className="primary-btn" onClick={loadCompany} disabled={loading}>{loading?'Loading…':'Open'}</button></div>
      {company && <>
        <Card className="company-hero"><div className="eyebrow">COMPANY PROFILE</div><h2>{companyName}</h2><p>{val(company.company||{},'sector','industry')||'Sector not available'} · {symbol}</p>
          <div className="price-strip"><div><span>Latest available price</span><b>{money(val(latestPrice||{},'close','Close','price','adjclose'))}</b></div><div><span>Daily change</span><b className={Number(val(latestPrice||{},'daily_change_pct','change_pct')||0)>=0?'positive':'negative'}>{val(latestPrice||{},'daily_change_pct','change_pct')!==undefined?`${Number(val(latestPrice||{},'daily_change_pct','change_pct')).toFixed(2)}%`:'—'}</b></div></div>
        </Card>
        <div className="section-head list-head"><h2>Recent filings</h2><Pill>{announcements.length}</Pill></div>
        {announcements.map((x:any,i:number)=><FeedCard key={i} item={x} onAsk={()=>{setTab('research');setQuestion(`Summarise this filing: ${titleOf(x)}`);}} onCompany={()=>{}}/>)}
        <div className="section-head list-head"><h2>Results cards</h2></div>
        {resultCards.map((r:any,i:number)=><Card key={i}><h3>{val(r,'period','quarter','title')||'Financial results'}</h3><MetricTable data={r}/></Card>)}
        <div className="section-head list-head"><h2>Management changes</h2></div>
        {managementCards.map((m:any,i:number)=><Card key={i}><div className="item-top"><b>{val(m,'person','person_name','name')||'Person not identified in filing'}</b><Pill>{val(m,'action','event_type')||'Management change'}</Pill></div><p className="muted">{val(m,'role','designation')||'Role not stated'} · Effective {val(m,'effective_date')||'date not stated'}</p><p>{val(m,'bio','background')||'Background not available from verified sources.'}</p></Card>)}
        <button className="primary-btn full" onClick={()=>setTab('research')}>Research this company</button>
      </>}
      {!company && <Card><h3 className="item-title">Look up a company</h3><p className="muted">Enter an NSE ticker above to load its available price history, filings, results and management changes. Try RELIANCE, AXISBANK or TCS.</p></Card>}
    </section>}

    {tab==='research' && <section className="screen">
      <div className="section-head"><div><div className="eyebrow dark">PRIMARY-SOURCE RESEARCH</div><h2>Ask FINN</h2></div></div>
      <div className="search-row"><input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} placeholder="NSE ticker"/><button className="secondary-btn" onClick={runResearch}>Run research</button></div>
      <div className="suggestions">{['What is management’s current growth outlook?','What are the key risks disclosed?','What capex or investment plans are stated?'].map(q=><button key={q} onClick={()=>setQuestion(q)}>{q}</button>)}</div>
      <Card className="chat-card">
        <label className="field-label">Your question</label>
        <textarea rows={3} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="Ask about guidance, financial performance, risks or strategy…"/>
        <button className="primary-btn full" disabled={loading || !question.trim()} onClick={()=>ask()}>{loading?'Analysing…':'Ask FINN'}</button>
      </Card>
      {answer && <Card><div className="item-top"><h3>Answer · {answer.symbol}</h3><Pill tone="blue">Source-grounded</Pill></div><p className="answer-text">{answer.answer}</p>{(answer.claims||[]).map((c:any,i:number)=><div className="citation" key={i}><b>{c.document || c.source || 'Primary source'} · p. {c.page_start ?? c.page ?? '—'}{c.page_end && c.page_end!==c.page_start?`–${c.page_end}`:''}</b><p>“{c.quote}”</p></div>)}</Card>}
      <Card className="research-run"><h3>On-demand research</h3><p className="muted">Fetch available annual reports and transcripts, extract and index them, then analyse retrieved passages.</p><button className="secondary-btn full" onClick={runResearch}>Start research for {symbol}</button>{researchStatus && <p className="status-line">{researchStatus}</p>}{researchResult && <pre className="result-json">{JSON.stringify(researchResult,null,2)}</pre>}</Card>
      <p className="footnote">FINN only displays supported claims and source references returned by the research pipeline. Missing documents and model quota failures are reported rather than filled with invented content.</p>
    </section>}

    <nav className="bottom-nav">
      {(['today','alerts','company','research'] as Tab[]).map(t=><button key={t} className={tab===t?'active':''} onClick={()=>setTab(t)}><span>{t==='today'?'◫':t==='alerts'?'⌁':t==='company'?'▤':'⌕'}</span>{t==='today'?'Today':t==='alerts'?'Alerts':t==='company'?'Company':'Research'}</button>)}
    </nav>
  </main>;
}

function FeedCard({item,onAsk,onCompany}:{item:AnyObj;onAsk:()=>void;onCompany:()=>void}) {
  const priority = String(val(item,'priority','priority_label')||'Routine');
  const source = val(item,'pdf_link','source_url','url');
  return <Card>
    <div className="item-top"><div className="company-link" onClick={onCompany}>{companyOf(item)} <span>{symbolOf(item)}</span></div><Pill tone={priority.toLowerCase()}>{priority}</Pill></div>
    <h3 className="item-title">{titleOf(item)}</h3>
    <p className="item-why">{whyOf(item)}</p>
    <div className="item-meta"><span>{val(item,'category','event_type')||'Filing'} · {fmtDate(val(item,'date_time','timestamp'))}</span><span>{val(item,'price_change_pct','daily_change_pct')!==undefined?`Price ${Number(val(item,'price_change_pct','daily_change_pct')).toFixed(2)}%`:'Price reaction unavailable'}</span></div>
    <div className="card-actions"><button onClick={onAsk}>Ask FINN</button>{source && <a href={source} target="_blank" rel="noreferrer">Open source ↗</a>}</div>
  </Card>;
}
function AlertCard({item}:{item:AnyObj}) {
  return <Card><div className="item-top"><b>{val(item,'company','name','symbol')||'Market alert'}</b><Pill tone="blue">{val(item,'type','alert_type')||'Alert'}</Pill></div><h3 className="item-title">{val(item,'headline','message','subject')||'Triggered rule'}</h3><p className="item-why">{val(item,'reason','description','details')||'Review the associated market signal or filing.'}</p><p className="item-meta">{fmtDate(val(item,'timestamp','date_time'))}</p></Card>;
}
function MetricTable({data}:{data:AnyObj}) {
  const rows = [
    ['Revenue',['revenue','total_income']],
    ['EBITDA',['ebitda']],
    ['PBT',['pbt','profit_before_tax']],
    ['PAT',['pat','profit_after_tax']]
  ];
  return <div className="metric-table">{rows.map(([label,keys]:any)=><div key={label}><span>{label}</span><b>{money(val(data,...keys))}</b><small>QoQ {val(data,`${keys[0]}_qoq_pct`,'qoq_pct')??'—'} · YoY {val(data,`${keys[0]}_yoy_pct`,'yoy_pct')??'—'}</small></div>)}</div>;
}
