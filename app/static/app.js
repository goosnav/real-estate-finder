"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const human = value => String(value || "unknown").replaceAll("_", " ");
  const words = value => String(value || "").trim().split(/\s+/).filter(Boolean).length;
  const clamp = value => Math.max(0, Math.min(100, Number(value) || 0));
  const number = value => value == null ? "—" : Number(value).toLocaleString(undefined, {maximumFractionDigits: 2});
  const date = value => {if (!value) return "Not checked"; const d = new Date(value); return Number.isNaN(d.getTime()) ? "Not stated" : d.toLocaleDateString(undefined, {month:"short",day:"numeric",year:"numeric"});};
  const url = value => {try {const u = new URL(value); return ["http:","https:"].includes(u.protocol) && !u.username && !u.password ? u.href : "";} catch {return "";}};
  const host = value => {try {return new URL(value).hostname.replace(/^www\./, "");} catch {return "";}};
  const link = (value, label, cls = "") => url(value) ? `<a class="${esc(cls)}" href="${esc(url(value))}" target="_blank" rel="noopener noreferrer">${esc(label || host(value))} ↗</a>` : "";
  const tone = value => ["available","eligible","approved","verified","completed","matched","met","confirmed","strong"].includes(value) ? "good" : ["blocked","unavailable","closed","failed","rejected","suppressed","invalid","conflict","bounced"].includes(value) ? "bad" : ["review","running","paused","budget_exhausted","unresolved","partial"].includes(value) ? "attention" : "";
  const badge = (value, color = tone(value)) => `<span class="badge ${esc(color)}">${esc(human(value))}</span>`;
  const money = (value, currency = "USD") => {if (value == null) return "Unknown"; try {return new Intl.NumberFormat(undefined, {style:"currency",currency:currency || "USD",maximumFractionDigits:2,minimumFractionDigits:0}).format(value);} catch {return `${number(value)} ${currency}`;}};
  const modes = [["residential_rent","Rent a home"],["residential_buy","Buy a home"],["commercial_lease","Lease commercial"],["commercial_buy","Buy commercial"]];
  const modeName = mode => modes.find(([v]) => v === mode)?.[1] || human(mode);
  const priceBasis = {total:"asking price",month:"/ month",year:"/ year",sqft_year:"/ sq ft / year",sqft_month:"/ sq ft / month",unknown:"basis unknown"};
  const stages = [["pipeline","Full workflow"],["profile","Build brief"],["discover","Find properties"],["research","Research & compare"],["verify","Recheck sources"],["draft","Write inquiries"]];
  const workflows = ["new","shortlisted","contacted","replied","tour_scheduled","toured","applied","offered","negotiating","closed","rejected","not_interested","bounced"];
  const defaults = {
    name:"",mode:"residential_rent",objective:"",demo:false,
    client:{name:"",email:"",phone:"",organization:"",brief_text:""},
    preferences:{locations:[],property_types:[],currency:"USD",budget_monthly_max:null,purchase_price_max:null,cash_available:null,min_area_sqft:null,max_area_sqft:null,min_bedrooms:null,min_bathrooms:null,intended_use:"",must_have:"",exclude:"",move_by:"",max_listing_age_days:60},
    cost_assumptions:{financing:"mortgage",down_payment_percent:20,interest_rate_percent:null,loan_years:30,closing_cost_percent:null,property_tax_annual:null,insurance_annual:null,hoa_monthly:null,maintenance_monthly:null,mortgage_insurance_monthly:null,utilities_monthly:null,other_monthly:null,repairs_upfront:null,fit_out_upfront:null,moving_upfront:null},
    settings:{provider:"codex",model:"",routes:{profile:{provider:"inherit",model:""},discovery:{provider:"inherit",model:""},research:{provider:"inherit",model:""},outreach:{provider:"inherit",model:""}},target_results:25,min_fit:55,discovery_queries:6,max_agent_calls:100,max_minutes:90,concurrency:2,agent_timeout_seconds:180,max_turns:8,max_cost_usd:10,research_pages:6,seed_urls:[],weights:{property:25,location:20,budget:25,requirements:25,timing:5}}
  };
  const state = {session:null,searches:[],activeId:null,detail:null,view:"properties",filter:"",statusFilter:"all",selected:new Set(),run:null,events:[],pollTimer:null,polling:false,logOpen:false,dismissed:new Set(),configDirty:false,original:null,working:null,detailTab:"costs",dirty:false,exportScope:"all",busy:0};
  const search = () => state.detail?.search;
  const properties = () => state.detail?.properties || state.detail?.opportunities || [];
  const demo = () => Boolean(search()?.demo || search()?.settings?.provider === "demo");
  const active = (run = state.run) => Boolean(run && ["running","queued"].includes(run.status));
  const list = (items, empty = "Nothing recorded yet.") => items?.length ? `<ul class="plain-list">${items.map(x => `<li>${esc(typeof x === "string" ? x : x.reason || x.text || "")}</li>`).join("")}</ul>` : `<p class="small muted">${esc(empty)}</p>`;
  const stats = () => ({total:properties().length,available:properties().filter(p=>p.listing_status==="available").length,shortlisted:properties().filter(p=>p.workflow_status==="shortlisted").length,tours:properties().filter(p=>["tour_scheduled","toured"].includes(p.workflow_status)).length,drafted:properties().filter(p=>p.drafts?.length).length,ready:properties().filter(p=>p.ready_to_contact).length});

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.session?.token) headers.set("X-Session-Token", state.session.token);
    if (options.body && !(options.body instanceof FormData) && typeof options.body !== "string") {headers.set("Content-Type", "application/json"); options.body = JSON.stringify(options.body);}
    let response;
    try {response = await fetch(path, {...options,headers,cache:"no-store",credentials:"same-origin"});} catch {throw new Error("Cannot reach Real Estate Finder. Reopen the app, then retry.");}
    if (!response.ok) {let body={}; try {body=await response.json();} catch {} throw new Error(body.error?.message || body.message || `Request failed (${response.status}).`);}
    return options.download ? response : response.status === 204 ? {} : response.json();
  }
  function toast(message, error = false) {
    const dialog = $$("dialog[open]").at(-1); let region = dialog ? $(".dialog-toast-region",dialog) : $("#toast-region");
    if (!region) {region=document.createElement("div");region.className="toast-region dialog-toast-region";region.setAttribute("role","status");region.setAttribute("aria-live","polite");dialog.append(region);}
    const item=document.createElement("div");item.className=`toast${error?" error":""}`;
    const content=document.createElement("span");content.textContent=message;item.append(content);
    const close=document.createElement("button");close.type="button";close.textContent="×";close.setAttribute("aria-label","Dismiss notification");close.onclick=()=>item.remove();item.append(close);region.append(item);
    while(region.children.length>3)region.firstElementChild.remove();setTimeout(()=>item.remove(),error?10000:5000);
  }
  async function busy(label, work, button) {
    state.busy++;$("#request-status").textContent=label;if(button)button.disabled=true;
    try {return await work();} catch(error) {toast(error.message || "The operation failed.",true);return null;} finally {state.busy--;if(!state.busy)$("#request-status").textContent="";if(button?.isConnected)button.disabled=button.dataset.action==="run"&&active();}
  }
  function openDialog(id) {if(!$(id).open)$(id).showModal();}
  function closeDialog(dialog) {if(dialog.id==="property-dialog"&&state.dirty&&!confirm("Discard unsaved property edits?"))return;dialog.close();if(dialog.id==="property-dialog")state.dirty=false;}
  function leaveConfig() {return !state.configDirty || confirm("Leave without saving these search changes?");}
  let fieldSequence=0;
  function field(label,name,value,{type="text",placeholder="",min,max,step,rows=3,required=false,help=""}={}) {
    const id=`field-${name.replaceAll(".","-")}-${++fieldSequence}`;
    const attrs=`id="${id}" name="${esc(name)}"${required?" required":""}${min!==undefined?` min="${min}"`:""}${max!==undefined?` max="${max}"`:""}${step!==undefined?` step="${step}"`:""} placeholder="${esc(placeholder)}"${help?` aria-describedby="${id}-help"`:""}`;
    return `<div class="field"><label for="${id}">${esc(label)}</label>${type==="textarea"?`<textarea ${attrs} rows="${rows}">${esc(value)}</textarea>`:`<input ${attrs} type="${type}" value="${esc(value??"")}">`}${help?`<small id="${id}-help">${esc(help)}</small>`:""}</div>`;
  }
  function select(label,name,value,choices) {const id=`field-${name.replaceAll(".","-")}-${++fieldSequence}`;return `<div class="field"><label for="${id}">${esc(label)}</label><select id="${id}" name="${esc(name)}">${choices.map(([v,l])=>`<option value="${esc(v)}"${String(value)===String(v)?" selected":""}>${esc(l)}</option>`).join("")}</select></div>`;}
  const numeric = (label,name,value,options={}) => field(label,name,value,{type:"number",min:0,step:"any",placeholder:"Unknown",required:name.startsWith("settings.")||["preferences.max_listing_age_days","cost_assumptions.down_payment_percent","cost_assumptions.loan_years"].includes(name),...options});
  const sectionHead = (number,title,extra="") => `<div class="section-head"><h2><span class="section-number">${number}</span>${title}</h2>${extra?`<span class="small muted">${extra}</span>`:""}</div>`;
  const disclosure = (title,body,open=false) => `<details class="disclosure"${open?" open":""}><summary>${esc(title)}</summary><div class="disclosure-content">${body}</div></details>`;
  function providerNotice(provider) {
    if(provider==="demo")return '<p class="small muted">Fictional properties. No model calls.</p>';
    const p=state.session?.providers?.find(x=>x.id===provider);
    return p?.available?'<p class="small muted">CLI found. Uses its existing sign-in.</p>':`<div class="notice">${esc(p?.message||`Install and sign in to ${provider==="claude"?"Claude Code":"Codex"} to run.`)} <button type="button" class="button quiet small" data-action="help">Setup ↗</button></div>`;
  }
  function sidebar() {
    $("#search-count").textContent=state.searches.length||"";
    $("#search-list").innerHTML=state.searches.length?state.searches.map((s,i)=>`<button type="button" class="search-link${s.id===state.activeId?" active":""}" data-action="open-search" data-id="${esc(s.id)}" title="${esc(s.name)}"><span>${String(i+1).padStart(2,"0")}</span><span>${esc(s.name)}</span></button>`).join(""):'<span class="small muted">No searches yet.</span>';
    $("#active-name").textContent=search()?.name||"Workspace";$("#demo-badge").hidden=!demo();
  }
  function empty() {
    return `<section class="empty-workspace"><span class="eyebrow">A SPACE FOR YOUR NEXT CHAPTER</span><h1>FIND YOUR<br>NEXT SPACE.</h1><p class="prose">Homes, apartments, and commercial spaces. A researched shortlist with the costs and questions in view.</p><div class="actions"><button type="button" class="button primary" data-action="new">Create a search ↗</button><button type="button" class="button" data-action="demo-picker">Try a demo</button></div><div class="empty-process"><div><span class="eyebrow">01</span><h3>Set the brief</h3><p>Location, budget, and what matters.</p></div><div><span class="eyebrow">02</span><h3>Compare the facts</h3><p>Fit, total costs, and dated evidence.</p></div><div><span class="eyebrow">03</span><h3>Make your move</h3><p>Review inquiries. Book your own tour.</p></div></div><p class="empty-footnote">Your Codex or Claude Code CLI does the research. <button class="button quiet small" data-action="help" type="button">Setup & CLI ↗</button></p></section>`;
  }
  function heading() {
    const copy={brief:["Set your direction.","A clear brief makes a useful shortlist."],properties:["Find a place that fits.","Compare the space, the cost, and the evidence."],inquiries:["Start the conversation.","Short inquiries. Specific questions. Your review."],export:["Take the next step.","Your shortlist, sources, and drafts in one file."]};
    const [title,subtitle]=copy[state.view];
    return `<div class="page-heading"><div><span class="eyebrow">${esc(modeName(search().mode))}</span><h1>${title}</h1><p>${subtitle}</p></div><div class="actions heading-actions"><button type="button" class="button" data-action="import">Import</button><select id="run-stage" aria-label="Workflow stage">${stages.map(([v,l])=>`<option value="${v}"${state.view==="inquiries"&&v==="draft"?" selected":""}>${l}</option>`).join("")}</select><button type="button" class="button primary" data-action="run"${active()?" disabled":""}>Run ↗</button></div></div>`;
  }
  function tabs() {const n=stats();return `<nav class="tabs" aria-label="Search sections">${[["brief","Brief",null],["properties","Properties",n.total],["inquiries","Inquiries",n.drafted],["export","Export",null]].map(([v,label,count])=>`<button type="button" data-action="view" data-view="${v}"${state.view===v?' aria-current="page"':""}>${label}${count?`<span>${count}</span>`:""}</button>`).join("")}</nav>`;}
  function metrics() {const n=stats();return `<div class="metrics">${[[n.total,"Properties"],[n.available,"Advertised available"],[n.shortlisted,"Shortlisted"],[n.tours,"Tours"]].map(([v,l])=>`<div class="metric"><strong>${v}</strong><small>${l}</small></div>`).join("")}</div>`;}
  function propertyPanel() {
    return `<div id="metrics-region">${metrics()}</div><section aria-label="Ranked properties"><div class="toolbar"><input id="property-filter" class="search-control" type="search" placeholder="Search places or properties" aria-label="Search properties" value="${esc(state.filter)}"><div class="filters"><select id="status-filter" aria-label="Filter fit or progress">${[["all","All properties"],["shortlisted","Shortlisted"],["eligible","Fits requirements"],["review","Needs a check"],["blocked","Has a conflict"],["available","Advertised available"],["70","Fit 70+"]].map(([v,l])=>`<option value="${v}"${state.statusFilter===v?" selected":""}>${l}</option>`).join("")}</select><button type="button" class="button quiet" data-action="refresh" aria-label="Refresh properties">↻</button></div></div><div id="property-table-region"></div></section>`;
  }
  function filtered() {
    const q=state.filter.trim().toLowerCase();
    return properties().filter(p=>{if(state.view==="inquiries"&&!p.drafts?.length)return false;if(q&&![p.title,p.address,p.location,p.property_type,p.contact_name,p.email].some(v=>String(v||"").toLowerCase().includes(q)))return false;const f=state.statusFilter;if(["eligible","review","blocked"].includes(f)&&p.eligibility!==f)return false;if(f==="shortlisted"&&p.workflow_status!==f)return false;if(f==="available"&&p.listing_status!==f)return false;return f!=="70"||p.fit_score>=70;});
  }
  function propertyFacts(p) {return [p.area_sqft!=null?`${number(p.area_sqft)} sq ft`:null,p.bedrooms!=null?`${number(p.bedrooms)} bed`:null,p.bathrooms!=null?`${number(p.bathrooms)} bath`:null].filter(Boolean).join(" · ") || "Size not stated";}
  function table() {
    const region=$("#property-table-region");if(!region)return;
    const rows=filtered(),selected=state.selected.size?`<div class="bulk-bar"><span>${state.selected.size} selected</span><button class="button small" type="button" data-action="bulk-shortlist">Shortlist</button><button class="button quiet small" type="button" data-action="bulk-skip">Skip</button><button class="button quiet small" type="button" data-action="clear-selection">Clear</button></div>`:"";
    if(!rows.length){region.innerHTML=`${selected}<div class="empty-table"><h2>${state.view==="inquiries"?"Research first. Then inquire.":properties().length?"No matching properties.":"Your shortlist starts here."}</h2><p>${state.view==="inquiries"?"Run research and drafting to prepare a specific inquiry for each property.":properties().length?"Try another filter.":"Run your search, or import listings you have already found."}</p><div class="actions"><button class="button primary" type="button" data-action="${properties().length&&state.view!=="inquiries"?"reset-filters":"run"}">${properties().length&&state.view!=="inquiries"?"Reset filters":"Start research"}</button></div></div>`;return;}
    region.innerHTML=`${selected}<div class="table-wrap" role="region" aria-label="Property comparison" tabindex="0"><table class="property-table"><thead><tr><th class="check-cell"><input type="checkbox" id="select-all" aria-label="Select visible properties"${rows.every(p=>state.selected.has(p.id))?" checked":""}></th><th>Property</th><th>Advertised</th><th>Monthly estimate</th><th>Fit</th><th>Next step</th></tr></thead><tbody>${rows.map(p=>{
      const e=p.estimate||{},currency=e.currency||p.currency||search().preferences.currency;
      const monthly=e.monthly_total!=null?money(e.monthly_total,currency):e.known_monthly!=null?`${money(e.known_monthly,currency)}+`:"Unknown";
      const next=p.next_action==="contact"?"Review inquiry":p.next_action==="skip"?"See conflict":p.next_action==="research"?"Research needed":"Review property";
      return `<tr data-property="${esc(p.id)}"><td class="check-cell"><input type="checkbox" data-select="${esc(p.id)}" aria-label="Select ${esc(p.title)}"${state.selected.has(p.id)?" checked":""}></td><td><div class="row-title"><span class="row-rank">${p.rank||properties().findIndex(x=>x.id===p.id)+1}</span><div><button type="button" class="property-title" data-action="open-property" data-id="${esc(p.id)}">${esc(p.title)}</button><span class="subline">${esc(p.location||p.address||"Location not stated")}</span><span class="subline">${esc(propertyFacts(p))}</span><span class="row-badges">${badge(p.listing_status)}${p.workflow_status!=="new"?badge(p.workflow_status):""}</span><span class="mobile-meta">Ask ${esc(money(p.price_amount,p.currency))} ${esc(priceBasis[p.price_basis]||"")}<br>Fit ${p.research_status==="complete"?p.fit_score:"—"} · ${p.confidence||0}% evidence${p.deal_score!=null?` · Deal ${p.deal_score}`:""}</span></div></div></td><td><span class="money">${esc(money(p.price_amount,p.currency))}</span><span class="subline">${esc(priceBasis[p.price_basis]||"basis unknown")}</span>${p.mode==="commercial_lease"?`<span class="subline">${esc(human(p.lease_type))} lease</span>`:""}${p.deal_score!=null?`<span class="subline">Deal ${p.deal_score} · ${p.deal?.comparable_count||0} comps</span>`:'<span class="subline">Deal not established</span>'}</td><td><span class="money">${esc(monthly)}</span><span class="subline">${e.monthly_total!=null?"Estimated total":"Known costs · extras unknown"}</span><span class="subline">Cash ${esc(e.cash_needed!=null?money(e.cash_needed,currency):e.known_cash_needed!=null?money(e.known_cash_needed,currency)+"+":"unknown")}</span></td><td><span class="score">${p.research_status==="complete"?p.fit_score:"—"}<small>/ 100</small></span><span class="score-track"><span style="width:${clamp(p.fit_score)}%"></span></span><span class="subline">${p.confidence||0}% evidence</span></td><td><button class="next-step" type="button" data-action="open-property" data-id="${esc(p.id)}">${next} ↗</button><div class="row-badges">${badge(p.eligibility==="review"?"Needs check":p.eligibility,tone(p.eligibility))}</div></td></tr>`;
    }).join("")}</tbody></table></div><div class="table-bottom"><span>${rows.length} of ${properties().length} properties</span><span>Fit first · costs include stated assumptions</span></div>`;
  }

  function brief() {
    const s=search(),c=s.client,p=s.preferences,a=s.cost_assumptions,settings=s.settings,docs=state.detail.documents||[],profile=s.profile||{};
    const routes=[["profile","Brief & priorities"],["discovery","Property discovery"],["research","Research & fit"],["outreach","Inquiry writing"]].map(([key,label])=>{const r=settings.routes[key];return `<div class="route-row"><span>${label}</span><label class="sr-only" for="route-${key}-provider">${label} provider</label><select id="route-${key}-provider" name="settings.routes.${key}.provider">${[["inherit","Use default"],["codex","Codex"],["claude","Claude Code"]].map(([v,l])=>`<option value="${v}"${r.provider===v?" selected":""}>${l}</option>`).join("")}</select><label class="sr-only" for="route-${key}-model">${label} model</label><input id="route-${key}-model" name="settings.routes.${key}.model" value="${esc(r.model)}" placeholder="Provider default"></div>`;}).join("");
    return `<form id="brief-form"><div class="form-grid">
      <section class="form-section span-all">${sectionHead("01","Your search")}
        <div class="field-row">${field("Search name","name",s.name,{required:true})}${select("I want to","mode",s.mode,modes)}</div>
        ${field("What are you looking for?","objective",s.objective,{type:"textarea",rows:3,placeholder:"The place, the budget, and what would make it a good fit.",required:true})}
        <div class="field-row">${field("Locations","preferences.locations",p.locations.join("; "),{placeholder:"City or area; another area",help:"Separate locations with semicolons."})}${field("Property types","preferences.property_types",p.property_types.join(", "),{placeholder:"Apartment, house, industrial, office…"})}</div>
        <div class="upload-row"><div><strong>Supporting documents</strong><small>Requirements, space brief, or a listing brochure.<br>TXT, MD, PDF, DOCX, CSV · up to 8 MB each</small></div><button class="button" type="button" data-action="documents">Add file</button></div>
        ${docs.length?`<ul class="document-list">${docs.map(d=>`<li><span>${esc(d.name)}<small>${number(d.characters||d.char_count||d.text_length||0)} characters</small></span><button class="button quiet small" type="button" data-action="remove-document" data-id="${esc(d.id)}">Remove</button></li>`).join("")}</ul>`:""}
        ${disclosure("Add more context",field("Needs, constraints, or business context","client.brief_text",c.brief_text,{type:"textarea",rows:5,placeholder:"Only include information you want your selected AI provider to receive."}))}
      </section>
      <section class="form-section">${sectionHead("02","Fit & limits")}
        <div class="field-row">${numeric("Monthly budget","preferences.budget_monthly_max",p.budget_monthly_max,{min:1,help:"Target for all recurring costs."})}${field("Currency","preferences.currency",p.currency,{placeholder:"USD",help:"Three-letter currency code."})}</div>
        <div class="field-row"><div data-mode-group="buy">${numeric("Maximum purchase price","preferences.purchase_price_max",p.purchase_price_max,{min:1})}</div>${numeric("Cash available","preferences.cash_available",p.cash_available)}</div>
        <div class="field-row">${numeric("Minimum area · sq ft","preferences.min_area_sqft",p.min_area_sqft)}${numeric("Maximum area · sq ft","preferences.max_area_sqft",p.max_area_sqft,{min:1})}</div>
        <div class="field-row" data-mode-group="residential">${numeric("Minimum bedrooms","preferences.min_bedrooms",p.min_bedrooms,{step:.5})}${numeric("Minimum bathrooms","preferences.min_bathrooms",p.min_bathrooms,{step:.5})}</div>
        <div data-mode-group="commercial">${field("Intended business use","preferences.intended_use",p.intended_use,{type:"textarea",rows:2,placeholder:"Use, equipment, power, access, and operating needs."})}</div>
        ${field("Must have","preferences.must_have",p.must_have,{type:"textarea",rows:2,placeholder:"Parking, accessible entrance, loading dock…"})}
        ${field("Exclude","preferences.exclude",p.exclude,{type:"textarea",rows:2,placeholder:"Property constraints or specific areas to avoid."})}
        <div class="field-row">${field("Target move date","preferences.move_by",p.move_by.slice(0,10),{type:"date"})}${numeric("Listing age limit · days","preferences.max_listing_age_days",p.max_listing_age_days,{min:1,max:730,step:1})}</div>
      </section>
      <section class="form-section">${sectionHead("03","Cost assumptions","Blank means unknown")}
        <p class="small muted comparison-note">Entered values override listing costs for this scenario. Blank uses the listing value. Enter zero only when justified.</p>
        <div data-mode-group="buy">
          ${select("Payment method","cost_assumptions.financing",a.financing,[["mortgage","Financed purchase"],["cash","Cash purchase"]])}
          <div class="field-row" data-financing>${numeric("Down payment · %","cost_assumptions.down_payment_percent",a.down_payment_percent,{min:0,max:100})}${numeric("Interest rate · %","cost_assumptions.interest_rate_percent",a.interest_rate_percent,{max:40})}</div>
          <div class="field-row">${numeric("Loan term · years","cost_assumptions.loan_years",a.loan_years,{min:1,max:50,step:1})}${numeric("Closing costs · %","cost_assumptions.closing_cost_percent",a.closing_cost_percent,{max:30})}</div>
          <div class="field-row">${numeric("Property tax · annual","cost_assumptions.property_tax_annual",a.property_tax_annual)}${numeric("Insurance · annual","cost_assumptions.insurance_annual",a.insurance_annual)}</div>
          <div class="field-row">${numeric("HOA · monthly","cost_assumptions.hoa_monthly",a.hoa_monthly)}${numeric("Maintenance · monthly","cost_assumptions.maintenance_monthly",a.maintenance_monthly)}</div>
          ${numeric("Mortgage insurance · monthly","cost_assumptions.mortgage_insurance_monthly",a.mortgage_insurance_monthly)}
        </div>
        <div class="field-row">${numeric("Utilities · monthly","cost_assumptions.utilities_monthly",a.utilities_monthly)}${numeric("Other costs · monthly","cost_assumptions.other_monthly",a.other_monthly)}</div>
        ${disclosure("Upfront allowances",`<div class="field-row">${numeric("Repairs","cost_assumptions.repairs_upfront",a.repairs_upfront)}${numeric("Moving","cost_assumptions.moving_upfront",a.moving_upfront)}</div><div data-mode-group="commercial">${numeric("Fit-out / improvements","cost_assumptions.fit_out_upfront",a.fit_out_upfront)}</div>`)}
        ${disclosure("Your inquiry signature",`<div class="field-row">${field("Your name","client.name",c.name)}${field("Reply email","client.email",c.email,{type:"email"})}</div><div class="field-row">${field("Phone","client.phone",c.phone,{type:"tel"})}${field("Organization","client.organization",c.organization)}</div><p class="small muted">Drafts can use supplied facts. The app never sends an inquiry.</p>`)}
      </section>
      <section class="form-section span-all">${sectionHead("04","Your agents & limits")}
        <div class="field-row three">${select("Default agent","settings.provider",settings.provider,[["codex","Codex"],["claude","Claude Code"],...(demo()?[["demo","Demo · offline"]]:[])])}${field("Default model","settings.model",settings.model,{placeholder:"Provider default"})}${numeric("Target properties","settings.target_results",settings.target_results,{min:1,max:1000,step:1})}</div>
        <div id="provider-notice">${providerNotice(settings.provider)}</div>
        ${disclosure("Models by task",`<div class="route-grid"><div class="route-row route-header"><span>TASK</span><span>PROVIDER</span><span>MODEL</span></div>${routes}</div><p class="small muted">A blank model uses that provider’s default. Research and writing can use different models.</p>`)}
        ${disclosure("Run limits & sources",`<div class="field-row four">${numeric("Agent call cap","settings.max_agent_calls",settings.max_agent_calls,{min:1,max:5000,step:1})}${numeric("Minutes per run","settings.max_minutes",settings.max_minutes,{min:1,max:1440,step:1})}${numeric("Parallel tasks","settings.concurrency",settings.concurrency,{min:1,max:4,step:1})}${numeric("Claude allowance · USD","settings.max_cost_usd",settings.max_cost_usd,{min:.1,max:1000,step:.1})}</div><div class="field-row four">${numeric("Discovery passes","settings.discovery_queries",settings.discovery_queries,{min:1,max:40,step:1})}${numeric("Pages per property","settings.research_pages",settings.research_pages,{min:1,max:10,step:1})}${numeric("Task timeout · seconds","settings.agent_timeout_seconds",settings.agent_timeout_seconds,{min:15,max:900,step:1})}${numeric("Max agent turns","settings.max_turns",settings.max_turns,{min:2,max:40,step:1})}</div>${field("Starting URLs","settings.seed_urls",settings.seed_urls.join("\n"),{type:"textarea",rows:3,placeholder:"Public listings, broker pages, or market sources · one URL per line"})}<p class="small muted">Calls and elapsed time are capped for both providers. The dollar allowance applies to Claude. Codex cost may be unreported.</p>`)}
        ${disclosure("Ranking weights",`<div class="field-row three">${Object.entries(settings.weights).map(([key,v])=>numeric(human(key),`settings.weights.${key}`,v,{max:100,step:1})).join("")}${numeric("Minimum fit for inquiry export","settings.min_fit",settings.min_fit,{max:100,step:1})}</div><p class="small muted">Fit, comparable value, and evidence confidence remain separate.</p>`)}
      </section>
      ${profile.summary?`<section class="form-section span-all">${sectionHead("05","Research direction")}<p class="prose">${esc(profile.summary)}</p><div class="profile-columns"><div><h3>Priorities</h3>${list(profile.priorities)}</div><div><h3>Questions</h3>${list(profile.unknowns)}</div><div><h3>Source plan</h3>${list(profile.source_plan)}</div></div>${disclosure("Search terms",list(profile.search_terms))}</section>`:""}
    </div><div class="save-bar"><span id="save-message">Saved locally</span><button type="submit" class="button primary">Save brief</button></div></form>`;
  }
  function updateModeVisibility(form = $("#brief-form")) {
    if(!form)return;
    const modeInput=$("[name=mode]",form);if(form.id==="brief-form"&&modeInput){modeInput.disabled=properties().length>0;modeInput.title=modeInput.disabled?"Create a new search to change type after adding properties.":"";const existing=$(".mode-lock-note",modeInput.parentElement);if(modeInput.disabled&&!existing){const note=document.createElement("small");note.className="mode-lock-note";note.textContent="Create a new search to change type.";modeInput.parentElement.append(note);}else if(!modeInput.disabled&&existing)existing.remove();}
    const mode=$("[name=mode]",form)?.value||search()?.mode||"residential_rent";
    $$("[data-mode-group]",form).forEach(el=>{const group=el.dataset.modeGroup;el.hidden=group==="buy"?!mode.endsWith("buy"):group==="commercial"?!mode.startsWith("commercial"):!mode.startsWith("residential");});
    const financing=$("[name='cost_assumptions.financing']",form)?.value;
    $$('[data-financing]',form).forEach(el=>el.hidden=financing==="cash");
    $$('[data-mode-group] input,[data-mode-group] select,[data-mode-group] textarea,[data-financing] input',form).forEach(input=>input.disabled=Boolean(input.closest('[hidden]')));
  }
  function formSearch(form,base=search()) {
    const value=structuredClone(base||defaults);for(const key of Object.keys(value))if(!(key in defaults))delete value[key];
    const arrays=new Set(["preferences.locations","preferences.property_types","settings.seed_urls"]);
    for(const input of $$("[name]",form)){
      if(input.disabled)continue;
      const parts=input.name.split(".");let target=value;for(const part of parts.slice(0,-1))target=target[part]??={};let v=input.value;
      if(arrays.has(input.name))v=v.split(input.name==="preferences.locations"?/[;\n]/:input.name==="settings.seed_urls"?/\n/:/[,\n]/).map(x=>x.trim()).filter(Boolean);
      else if(input.type==="number")v=v===""?null:Number(v);
      if(input.name==="preferences.currency")v=v.toUpperCase();
      target[parts.at(-1)]=v;
    }
    return value;
  }
  async function saveSearch({rerender=true}={}) {
    const form=$("#brief-form");if(!form)return true;if(!form.reportValidity())return false;
    const values=formSearch(form);if(values.preferences.min_area_sqft!=null&&values.preferences.max_area_sqft!=null&&values.preferences.min_area_sqft>values.preferences.max_area_sqft){const input=$('[name="preferences.min_area_sqft"]',form);input.setAttribute("aria-invalid","true");input.focus();throw new Error("Minimum area must be less than or equal to maximum area.");}
    const result=await api(`/api/searches/${state.activeId}`,{method:"PATCH",body:values});state.detail.search=result.search||result;state.configDirty=false;await refresh({quiet:!rerender});if(!rerender&&$("#save-message"))$("#save-message").textContent="Saved locally";return true;
  }
  function newSearchDialog() {
    $("#search-dialog").innerHTML=`<form id="new-search-form"><div class="dialog-header"><div><span class="eyebrow">A NEW SEARCH</span><h2 id="search-dialog-title">What’s your next move?</h2></div><button class="icon-button" type="button" data-action="close-dialog" aria-label="Close">×</button></div><div class="dialog-body">${select("I want to","mode","residential_rent",modes)}${field("Search name","name","",{required:true,placeholder:"My next space"})}${field("Objective","objective","",{type:"textarea",rows:4,required:true,placeholder:"The space you need, where, and what matters most."})}${field("Locations","preferences.locations","",{placeholder:"City or area; another area"})}<div class="field-row">${select("Agent","settings.provider","codex",[["codex","Codex"],["claude","Claude Code"]])}${numeric("Target properties","settings.target_results",25,{min:1,max:1000,step:1})}</div><p class="small muted">Add budget, requirements, and documents next.</p></div><div class="dialog-footer"><button class="button" type="button" data-action="close-dialog">Cancel</button><button class="button primary" type="submit">Create search ↗</button></div></form>`;openDialog("#search-dialog");
  }
  function demoPicker() {
    $("#utility-dialog").innerHTML=`<div class="dialog-header"><div><span class="eyebrow">NO MODEL CALLS</span><h2 id="utility-dialog-title">Choose a demo</h2></div><button class="icon-button" data-action="close-dialog" type="button" aria-label="Close">×</button></div><div class="dialog-body"><p class="prose">See the workflow with clearly fictional properties and contacts.</p><div class="two-col" style="margin-top:24px">${modes.map(([v,l])=>`<button type="button" class="button" data-action="demo" data-mode="${v}">${l} ↗</button>`).join("")}</div></div>`;openDialog("#utility-dialog");
  }

  function exportPanel() {
    const n=stats(),choices=[["all","Full research",`${n.total} properties · all evidence and drafts`],["shortlist","Your shortlist",`${n.shortlisted} selected properties`],["outreach","Approved inquiries",`${n.ready} contacts ready for export`]];
    return `<div class="export-grid"><section><h2>Choose your rows.</h2>${choices.map(([v,label,description])=>`<label class="export-choice${state.exportScope===v?" selected":""}"><input type="radio" name="export-scope" value="${v}"${state.exportScope===v?" checked":""}><span><strong>${label}</strong><small>${description}</small></span></label>`).join("")}${demo()?'<p class="notice">Demo exports contain fictional data. Inquiry exports stay disabled.</p>':""}<div class="export-buttons"><button class="button primary" type="button" data-action="export" data-format="csv">Download CSV ↓</button><button class="button" type="button" data-action="export" data-format="xlsx">Excel ↓</button></div><p id="export-note" class="small muted" style="margin-top:12px"></p></section><section><h2>The whole picture.</h2><p class="export-description">One row per property, with its costs, sources, questions, and next action.</p><ul class="plain-list"><li>Fit, deal score, evidence confidence, and rank</li><li>Address, listing link, dimensions, and advertised price</li><li>Estimated monthly cost and cash needed</li><li>Assumptions, unknown costs, and comparable evidence</li><li>Contact sources and three short inquiry drafts</li><li>Due diligence, tour progress, and follow-up date</li></ul><div class="readiness-row"><span>Ready for inquiry export</span><strong>${n.ready}</strong></div><div class="readiness-row"><span>Drafts to review</span><strong>${n.drafted}</strong></div><div class="actions" style="margin-top:22px"><button class="button small" type="button" data-action="verification">Email verification</button><button class="button small" type="button" data-action="outcomes">Import progress</button></div></section></div>`;
  }
  function exportReadiness() {
    if(state.view!=="export")return;const n=stats(),count=state.exportScope==="outreach"?n.ready:state.exportScope==="shortlist"?n.shortlisted:n.total;
    const disabled=count===0||(demo()&&state.exportScope==="outreach");$$('[data-action="export"]').forEach(b=>b.disabled=disabled);$("#export-note").textContent=demo()&&state.exportScope==="outreach"?"Fictional contacts cannot be exported for outreach.":count===0?"No rows in this selection yet.":`${count} properties · sources and assumptions included`;
  }
  function render() {
    sidebar();if(!search()){$("#main").innerHTML=empty();$("#run-region").innerHTML="";return;}
    $("#main").innerHTML=heading()+tabs()+(state.view==="brief"?brief():state.view==="export"?exportPanel():propertyPanel());table();updateModeVisibility();exportReadiness();renderRun();
  }
  async function listSearches() {const result=await api("/api/searches");state.searches=Array.isArray(result)?result:result.searches||[];sidebar();}
  function latestRun() {const runs=state.detail?.runs||[];return runs.find(r=>["queued","running","paused"].includes(r.status))||runs[0]||null;}
  async function loadSearch(id,{view,reset=true}={}) {
    clearTimeout(state.pollTimer);state.detail=await api(`/api/searches/${id}`);state.activeId=id;if(view)state.view=view;
    if(reset){state.selected.clear();state.filter="";state.statusFilter="all";state.configDirty=false;state.exportScope="all";}
    try{localStorage.setItem("real-estate-finder-active-search",id);}catch{}
    state.run=latestRun();state.events=[];render();if(state.run)await pollRun();
  }
  async function refresh({quiet=false}={}) {
    if(!state.activeId)return;const id=state.activeId,detail=await api(`/api/searches/${id}`);if(id!==state.activeId)return;state.detail=detail;await listSearches();
    if(!quiet){const next=latestRun();if(next&&next.id!==state.run?.id){state.run=next;state.events=[];if(active())state.pollTimer=setTimeout(pollRun,100);}}
    if(state.view==="brief"&&(state.configDirty||quiet)){sidebar();renderRun();return;}
    if(quiet&&["properties","inquiries"].includes(state.view)){$("#metrics-region").innerHTML=metrics();$(".tabs").outerHTML=tabs();table();renderRun();}else render();
  }
  function renderRun() {
    $$('[data-action="run"]').forEach(button=>button.disabled=active());const region=$("#run-region"),r=state.run;if(!r||state.dismissed.has(r.id)){region.innerHTML="";return;}
    const total=Number(r.total||0),done=Number(r.completed||0),stageIndex=Number(r.stage_index||0),stageCount=Number(r.stage_count||1),fraction=total?done/total:0;
    const pct=r.status==="completed"?100:r.stage==="pipeline"&&stageIndex?clamp((stageIndex-1+fraction)/stageCount*100):clamp(fraction*100);
    const title={profile:"Building your brief",discover:"Finding properties",research:"Researching properties",verify:"Checking sources",draft:"Writing inquiries",pipeline:"Full workflow"}[r.current_stage||r.stage]||human(r.current_stage||r.stage);
    const announcement=`${title}. ${human(r.status)}.${total?` ${done} of ${total} complete.`:""}`;if($("#run-announcement").textContent!==announcement)$("#run-announcement").textContent=announcement;
    const controls=active(r)?'<button class="button small" data-action="pause-run" type="button">Pause</button><button class="button quiet small" data-action="cancel-run" type="button">Cancel</button>':["paused","failed","budget_exhausted"].includes(r.status)?'<button class="button small" data-action="resume-run" type="button">Resume</button><button class="button quiet small" data-action="cancel-run" type="button">Cancel</button>':'<button class="button quiet small" data-action="dismiss-run" type="button" aria-label="Dismiss run">×</button>';
    const log=state.events.slice(-30).map(e=>`<div><time>${esc(e.created_at?new Date(e.created_at).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit"}):"")}</time><span>${esc(e.message||e.event||e.text||human(e.type))}</span></div>`).join("");
    const elapsed=Number(r.elapsed_seconds||0),tokens=(r.input_tokens||0)+(r.output_tokens||0),cost=r.cost_usd!=null?`$${Number(r.cost_usd).toFixed(2)} reported`:r.claude_cost_usd?`$${Number(r.claude_cost_usd).toFixed(2)} Claude`:"";
    region.innerHTML=`<section class="run-panel" aria-label="Workflow progress"><div class="run-heading"><div><span class="run-step">${stageIndex?String(stageIndex).padStart(2,"0"):"RUN"}</span><strong>${esc(title)}</strong>${badge(r.status)}</div><div class="actions"><button class="button quiet small" type="button" data-action="toggle-log" aria-expanded="${state.logOpen}">Activity ${state.logOpen?"−":"+"}</button>${controls}</div></div>${r.current_item?`<div class="run-item">${esc(r.current_item)}</div>`:""}<div class="run-progress"><progress aria-label="Workflow progress" max="100"${total||!active(r)?` value="${pct}"`:""}></progress><span>${r.stage==="pipeline"?`${stageIndex}/${stageCount} stages · `:""}${total?`${done}/${total}`:r.status==="completed"?"Done":active(r)?"Working…":human(r.status)}</span></div><div class="run-meta"><span>${Number(r.agent_calls||0)}${r.max_agent_calls?`/${r.max_agent_calls}`:""} agent calls</span>${elapsed?`<span>${Math.floor(elapsed/60)}m ${Math.floor(elapsed%60)}s elapsed</span>`:""}${tokens?`<span>${tokens.toLocaleString()} tokens reported</span>`:""}${cost?`<span>${cost}</span>`:""}${r.unpriced_calls?`<span>${r.unpriced_calls} unpriced calls</span>`:""}${r.error_count?`<span>${r.error_count} tasks need attention</span>`:""}</div>${r.error?`<div class="run-error" role="status">${esc(r.error)}</div>`:""}${state.logOpen?`<div class="run-log" aria-label="Recent activity">${log||'<span class="small muted">Waiting for activity.</span>'}</div>`:""}</section>`;
  }
  async function pollRun() {
    if(!state.run||state.polling)return;const id=state.run.id,searchId=state.activeId;state.polling=true;
    try{const result=await api(`/api/runs/${id}`);if(searchId!==state.activeId||state.run?.id!==id)return;state.run=result.run||result;state.events=result.events||state.events;renderRun();await refresh({quiet:true});}catch(error){toast(error.message,true);}finally{state.polling=false;if(state.run?.id===id&&active())state.pollTimer=setTimeout(pollRun,1600);}
  }
  async function startRun() {
    if(state.configDirty&&!(await saveSearch({rerender:false})))return;const stage=$("#run-stage")?.value||"pipeline",result=await api(`/api/searches/${state.activeId}/runs`,{method:"POST",body:{stage}});state.run=result.run||result;state.events=[];state.dismissed.delete(state.run.id);renderRun();await pollRun();
  }

  function costRows(rows,currency) {return (rows||[]).map(row=>`<tr><td>${esc(row.label)}</td><td>${row.amount==null?"Unknown":esc(money(row.amount,currency))}</td><td>${esc(({sqft_year:"From sq ft / year",sqft_month:"From sq ft / month",month:"Monthly listing",year:"Annual listing","user assumption":"Your assumption","listing; review source":"Listing"})[row.basis]||human(row.basis||""))}</td></tr>`).join("");}
  function costDetails(p) {
    const e=p.estimate||{},currency=e.currency||p.currency||search().preferences.currency,c=p.costs||{};
    const listingCosts=[["property_tax_annual","Property tax · annual"],["insurance_annual","Insurance · annual"],["hoa_monthly","HOA · monthly"],["utilities_monthly","Utilities · monthly"],["parking_monthly","Parking · monthly"],["pet_monthly","Pet fees · monthly"],["other_monthly","Other · monthly"],["operating_expenses","NNN / operating expenses"],["deposit","Refundable deposit"],["application_fee","Application fee"],["other_upfront","Other upfront fees"],["concession_total","Total concession"]];
    return `<section class="detail-section"><p class="detail-summary">${esc(p.research_summary||p.description||"Research this listing to compare the fit, costs, and open questions.")}</p><div class="fact-grid"><div class="fact"><small>Advertised price</small><strong>${esc(money(p.price_amount,p.currency))}</strong><span class="subline">${esc(priceBasis[p.price_basis])}</span></div><div class="fact"><small>Monthly estimate</small><strong>${esc(e.monthly_total!=null?money(e.monthly_total,currency):e.known_monthly!=null?money(e.known_monthly,currency)+"+":"Unknown")}</strong><span class="subline">${e.monthly_total!=null?"Including stated assumptions":"Known costs · more to confirm"}</span></div><div class="fact"><small>Cash needed</small><strong>${esc(e.cash_needed!=null?money(e.cash_needed,currency):e.known_cash_needed!=null?money(e.known_cash_needed,currency)+"+":"Unknown")}</strong><span class="subline">${e.cash_needed!=null?"Estimated upfront funds":"Known amounts · more to confirm"}</span></div></div>${p.blockers?.length?`<div class="error-line">${list(p.blockers)}</div>`:""}</section>
      <section class="detail-section"><h3>Monthly cost</h3><div class="table-wrap" role="region" aria-label="Cost comparison" tabindex="0"><table class="cost-table"><thead><tr><th>Item</th><th>Amount</th><th>Basis</th></tr></thead><tbody>${costRows(e.monthly_breakdown,currency)}<tr class="total"><td>${e.monthly_total!=null?"Estimated total":"Known subtotal"}</td><td>${esc(money(e.monthly_total??e.known_monthly,currency))}</td><td>/ month</td></tr></tbody></table></div>${e.effective_monthly!=null?`<p class="cost-note">After stated concession: ${esc(money(e.effective_monthly,currency))} / month over the stated lease. Cash payments can differ.</p>`:""}</section>
      <section class="detail-section"><h3>Upfront funds</h3><div class="table-wrap" role="region" aria-label="Cost comparison" tabindex="0"><table class="cost-table"><thead><tr><th>Item</th><th>Amount</th><th>Basis</th></tr></thead><tbody>${costRows(e.cash_breakdown,currency)}<tr class="total"><td>${e.cash_needed!=null?"Estimated cash needed":"Known subtotal"}</td><td>${esc(money(e.cash_needed??e.known_cash_needed,currency))}</td><td>upfront</td></tr></tbody></table></div></section>
      ${e.unknowns?.length?`<section class="detail-section"><h3>Costs to confirm</h3>${list(e.unknowns)}</section>`:""}
      ${e.assumptions?.length?`<section class="detail-section"><h3>Assumptions used</h3>${list(e.assumptions)}<p class="cost-note">Edit search-wide assumptions in Brief. Entered assumptions override listing values; blank uses the listing.</p></section>`:""}
      <section class="detail-section"><h3>Comparable value</h3><div class="fact-grid"><div class="fact"><small>Deal score</small><strong>${p.deal_score??"—"}</strong><span class="subline">${esc(p.deal?.label||"Insufficient comparable evidence")}</span></div><div class="fact"><small>Compatible comps</small><strong>${p.deal?.comparable_count||0}</strong><span class="subline">${p.deal?.excluded_count||0} excluded</span></div><div class="fact"><small>Asking price difference</small><strong>${p.deal?.discount_percent==null?"—":p.deal.discount_percent===0?"At median":`${number(Math.abs(p.deal.discount_percent))}% ${p.deal.discount_percent>0?"below":"above"}`}</strong><span class="subline">${esc(p.deal?.unit||"Compare like for like")}</span></div></div>${p.comparables?.length?`<div class="table-wrap" role="region" aria-label="Cost comparison" tabindex="0"><table class="cost-table"><thead><tr><th>Comparable</th><th>Price</th><th>Evidence</th></tr></thead><tbody>${p.comparables.map(comp=>`<tr><td>${link(comp.url,comp.title)}<span class="subline">${esc(comp.location)} · ${esc(human(comp.basis))}</span></td><td>${esc(money(comp.price_amount,comp.currency))}<span class="subline">${esc(priceBasis[comp.price_basis])}</span></td><td>${p.verified_comparable_urls?.includes(comp.url)?"Source matched":"Unverified"}<span class="subline">${esc(date(comp.observed_at))}</span></td></tr>`).join("")}</tbody></table></div>`:""}<p class="cost-note">A relative comparison of compatible sourced listings. Asking prices are not completed sale prices or an appraisal.</p></section>
      ${disclosure("Edit listing facts & costs",`<p class="small muted comparison-note">Keep a source for each change. Blank means unknown; zero is a specific claim. Edits require fresh checks.</p><div class="field-row">${numeric("Advertised price","price_amount",p.price_amount)}${select("Price basis","price_basis",p.price_basis,Object.entries(priceBasis))}</div><div class="field-row three">${field("Currency","currency",p.currency)}${numeric("Area · sq ft","area_sqft",p.area_sqft,{min:1})}${select("Lease type","lease_type",p.lease_type,[["unknown","Unknown"],["gross","Gross"],["modified_gross","Modified gross"],["nnn","NNN"]])}</div><div class="field-row">${numeric("Bedrooms","bedrooms",p.bedrooms)}${numeric("Bathrooms","bathrooms",p.bathrooms)}</div><div class="field-row">${listingCosts.map(([k,l])=>numeric(l,`costs.${k}`,c[k])).join("")}</div><div class="field-row">${select("Operating expense basis","costs.operating_expenses_basis",c.operating_expenses_basis,Object.entries(priceBasis))}${numeric("Lease term · months","costs.lease_months",c.lease_months,{min:1,max:1200,step:1})}</div>${field("Cost source URL","costs.source_url",c.source_url,{type:"url"})}${field("Cost notes","costs.notes",c.notes,{type:"textarea",rows:3})}`)}
    `;
  }
  function fitDetails(p) {
    const scores=p.score_breakdown?.length?p.score_breakdown:p.match_components||[];
    return `<section class="detail-section"><div class="fact-grid"><div class="fact"><small>Fit score</small><strong>${p.fit_score||0} / 100</strong><span class="subline">Match to your stated requirements</span></div><div class="fact"><small>Evidence confidence</small><strong>${p.confidence||0}%</strong><span class="subline">How much has source support</span></div><div class="fact"><small>Readiness</small>${badge(p.eligibility==="review"?"Needs check":p.eligibility,tone(p.eligibility))}<span class="subline">${esc(human(p.next_action))}</span></div></div>${p.blockers?.length?`<h3>Conflicts to resolve</h3>${list(p.blockers)}`:""}</section>
      <section class="detail-section"><h3>Why it ranks here</h3>${scores.length?`<div class="score-grid">${scores.map(c=>{const weight=Number(c.weight??search().settings.weights[c.criterion]??0),points=Number(c.earned??c.points??c.score??0),percent=c.percent??(weight?points/weight*100:0);return `<div class="score-item"><div><strong>${esc(human(c.criterion||c.name))}</strong>${badge(c.known===undefined?(c.match||c.status||"unknown"):c.known?"Assessed":"Unknown",c.known?"blue":"")}<span class="mono small">${number(points)} / ${number(weight)}</span></div><span class="score-track"><span style="width:${clamp(percent)}%"></span></span><p>${esc(c.reason||"No explanation recorded.")}</p>${link(c.source_url,"Source","small")}</div>`;}).join("")}</div>`:'<p class="small muted">Run research to build an evidence-based assessment.</p>'}<p class="fit-note">Scores organize research. They do not predict returns, certify a property, or replace a visit.</p></section>
      ${p.requirement_checks?.length?`<section class="detail-section"><h3>Requirements</h3><div class="checklist">${p.requirement_checks.map(c=>`<div class="checklist-row"><div><strong>${esc(human(c.criterion))}</strong>${badge(c.status)}</div><p>${esc(c.reason)}</p>${link(c.source_url,"Source","small")}</div>`).join("")}</div></section>`:""}
      <section class="detail-section"><h3>Questions before your next move</h3>${list(p.questions,"Research will surface specific questions for this property.")}</section>
      ${p.due_diligence?.length?`<section class="detail-section"><h3>Due diligence</h3><div class="checklist">${p.due_diligence.map((d,i)=>`<div class="checklist-row"><div><strong>${esc(d.topic)}</strong>${badge(d.status)}</div><p>${esc(d.question)}</p>${link(d.source_url,"Source","small")}${select(`Your check · ${d.topic}`,`due_diligence.${i}.status`,d.status,[["unresolved","Unresolved"],["source_found","Source found"],["confirmed","I confirmed this"],["not_applicable","Not applicable"]])}</div>`).join("")}</div><p class="fit-note">Mark confirmed only after checking the specific question with the appropriate source or person.</p></section>`:""}
      ${p.risks?.length?`<section class="detail-section"><h3>Risks to inspect</h3>${list(p.risks)}</section>`:""}`;
  }
  function evidenceDetails(p) {
    return `<section class="detail-section"><h3>Listing freshness</h3><div class="fact-grid"><div class="fact"><small>Posted</small><strong style="font-size:16px">${esc(date(p.posted_at))}</strong><span class="subline">Available ${esc(p.available_from?date(p.available_from):"date not stated")}</span></div><div class="fact"><small>Last checked</small><strong style="font-size:16px">${esc(date(p.last_checked_at))}</strong><span class="subline">${esc(human(p.source_check))}</span></div><div class="fact"><small>Advertised status</small>${badge(p.listing_status)}<span class="subline">${esc(human(p.listing_source))}</span></div></div><div class="actions">${link(p.listing_url,"Original listing","button small")}${link(p.broker_url,"Owner / broker","button small")}${link(p.inquiry_url,"Inquiry page","button small")}</div></section>
      ${p.signals?.length?`<section class="detail-section"><h3>Market signals</h3><p class="small muted">Signals are leads to investigate. They do not confirm a bargain or availability.</p>${p.signals.map(s=>`<article class="evidence"><div class="evidence-head">${badge(s.kind)}${link(s.url,host(s.url))}</div><p>${esc(s.summary)}</p><div class="evidence-meta" style="margin-top:13px"><span>Published ${esc(date(s.published_at))}</span><span>Event ${esc(date(s.event_at))}</span></div></article>`).join("")}</section>`:""}
      <section class="detail-section"><h3>Research evidence</h3>${p.evidence?.length?p.evidence.map(e=>`<article class="evidence"><div class="evidence-head">${link(e.url,e.title||host(e.url))}${badge(e.field)}</div><blockquote>${esc(e.quote)}</blockquote><div class="evidence-meta"><span>${esc(host(e.url))}</span><span>Checked ${esc(date(e.checked_at))}</span></div></article>`).join(""):'<p class="small muted">No source excerpts recorded. Run research to collect them.</p>'}</section>
      ${p.source_urls?.length?`<section class="detail-section"><h3>Additional sources</h3><ul class="source-list">${p.source_urls.map(u=>`<li>${link(u,u)}</li>`).join("")}</ul></section>`:""}`;
  }
  function inquiryDetails(p) {
    if(!p.drafts?.length)return `<div class="empty-table"><h3>A specific inquiry starts with research.</h3><p>Research the property, then draft a short introduction and two follow-ups.</p><button type="button" class="button primary" data-action="draft-from-detail">Write inquiries ↗</button></div>`;
    return `<section class="detail-section"><p class="detail-summary">${esc(p.outreach_angle||"Review the facts, wording, and recipient before approval.")}</p><div class="actions"><strong class="small">${esc(p.contact_name||"Contact to confirm")}</strong><span class="small muted">${esc(p.email||"No published email found")}</span>${badge(p.email_status)}</div>${p.export_blockers?.length?disclosure(`${p.export_blockers.length} export checks remaining`,list(p.export_blockers)):""}</section><div class="email-sequence">${p.drafts.map((d,i)=>`<article class="email-card"><div class="email-head"><strong><span class="step">0${i+1}</span>${i===0?"First inquiry":i===1?"Follow-up":"Final follow-up"}</strong><label class="email-delay">Day <input type="number" min="0" max="60" name="drafts.${i}.delay_days" value="${Number(d.delay_days)||0}" aria-label="Email ${i+1} delay in days"></label></div><div class="email-editor"><div class="label-row"><label for="draft-subject-${i}">Subject</label><span class="word-count${words(d.subject)>5?" over":""}" data-count="drafts.${i}.subject">${words(d.subject)}/5 words</span></div><input id="draft-subject-${i}" name="drafts.${i}.subject" value="${esc(d.subject)}"><div class="label-row"><label for="draft-body-${i}">Message</label><span class="word-count${words(d.body)>(i===0?90:65)?" over":""}" data-count="drafts.${i}.body">${words(d.body)}/${i===0?90:65} words</span></div><textarea id="draft-body-${i}" name="drafts.${i}.body" rows="7">${esc(d.body)}</textarea>${d.angle?`<div class="email-angle"><small>WHY THIS ANGLE</small><p>${esc(d.angle)}</p></div>`:""}${d.source_urls?.length?`<div class="source-links">${d.source_urls.map(v=>link(v,host(v))).join("")}</div>`:""}${d.user_claims?.length?disclosure("Your supplied facts",list(d.user_claims)):""}</div></article>`).join("")}</div>${p.qa_issues?.length?`<section class="detail-section" style="margin-top:25px"><h3>Copy checks</h3>${list(p.qa_issues)}</section>`:""}<p class="fit-note">Approval records your review. Export also checks the contact source and freshness. Day offsets are suggestions; no messages are scheduled or sent.</p>`;
  }
  function activityDetails(p) {
    return `<section class="detail-section"><h3>Your progress</h3><div class="field-row">${select("Stage","workflow_status",p.workflow_status,workflows.map(x=>[x,human(x)]))}${field("Next follow-up","next_follow_up",(p.next_follow_up||"").slice(0,10),{type:"date"})}</div>${field("Your notes","review_notes",p.review_notes,{type:"textarea",rows:4,placeholder:"Tour notes, questions, or the next step."})}<p class="small muted">Update after you contact, tour, apply, or make an offer yourself.</p></section><section class="detail-section"><h3>Public contact</h3><div class="field-row">${field("Name","contact_name",p.contact_name)}${field("Role","contact_role",p.contact_role)}</div><div class="field-row">${field("Email","email",p.email,{type:"email"})}${field("Phone","phone",p.phone,{type:"tel"})}</div>${field("Email source URL","email_source",p.email_source,{type:"url"})}${field("Contact source URL","contact_source",p.contact_source,{type:"url"})}<div class="actions">${badge(p.email_status)}<span class="small muted">Published or MX-found does not verify a mailbox.</span></div>${p.verification_at?`<p class="small muted" style="margin-top:10px">Verification: ${esc(p.verification_source)} · ${esc(date(p.verification_at))}</p>`:""}<button type="button" class="button quiet danger small" data-action="suppress-property" style="margin-top:18px">Do not contact ${p.email?"this address":"this domain"}</button></section>`;
  }
  function openProperty(id) {const p=properties().find(x=>x.id===id);if(!p)return;state.original=structuredClone(p);state.working=structuredClone(p);state.detailTab=state.view==="inquiries"?"inquiry":"costs";state.dirty=false;renderProperty();openDialog("#property-dialog");sizeDrafts();}
  function renderProperty() {
    const p=state.working;if(!p)return;
    const action=demo()?'<span class="small muted">Fictional demo · illustrative links</span>':link(p.inquiry_url||p.listing_url,"Open listing","button primary small");
    const tabs=[["costs","Costs"],["fit","Fit & checks"],["evidence","Evidence"],["inquiry","Inquiry"],["activity","Activity"]];
    $("#property-dialog").innerHTML=`<div class="dialog-header"><div class="property-title-block"><span class="eyebrow">${esc(p.property_type||modeName(p.mode))} ${p.unit?`/ ${esc(p.unit)}`:""}</span><h2 id="property-dialog-title">${esc(p.title)}</h2><div class="property-subtitle"><span>${esc(p.address||p.location||"Address not stated")}</span><span>${esc(propertyFacts(p))}</span><span>Fit ${p.fit_score||0} · ${p.confidence||0}% evidence</span></div><div class="actions">${action}${badge(p.listing_status)}</div></div><button class="icon-button" type="button" data-action="close-dialog" aria-label="Close property">×</button></div><div class="detail-tabs" role="tablist" aria-label="Property details">${tabs.map(([v,l])=>`<button type="button" id="detail-tab-${v}" role="tab" aria-selected="${state.detailTab===v}" aria-controls="property-tab-panel" tabindex="${state.detailTab===v?0:-1}" data-action="detail-tab" data-tab="${v}">${l}</button>`).join("")}</div><div id="property-tab-panel" class="detail-body" role="tabpanel" aria-labelledby="detail-tab-${state.detailTab}">${state.detailTab==="costs"?costDetails(p):state.detailTab==="fit"?fitDetails(p):state.detailTab==="evidence"?evidenceDetails(p):state.detailTab==="inquiry"?inquiryDetails(p):activityDetails(p)}</div><div class="dialog-footer"><span class="small muted" id="property-save-indicator">${state.dirty?"Unsaved changes":p.status==="approved"?"Inquiry approved":"Saved locally"}</span><div class="actions"><button class="button quiet small" type="button" data-action="skip-property">Skip</button><button class="button small" type="button" data-action="shortlist-property">${p.workflow_status==="shortlisted"?"Shortlisted ✓":"Shortlist"}</button><button class="button small${p.drafts?.length?"":" primary"}" type="button" data-action="save-property">Save edits</button>${p.drafts?.length?`<button class="button primary small" type="button" data-action="approve-property">${p.status==="approved"?"Approved ✓":"Approve inquiry"}</button>`:""}</div></div>`;
    sizeDrafts();
  }
  function sizeDrafts() {if(!$("#property-dialog").open)return;for(const input of $$('textarea[name^="drafts."]',$("#property-dialog"))){input.style.height="auto";input.style.height=`${Math.max(180,input.scrollHeight+2)}px`;}}
  function captureProperty() {
    if(!state.working)return;for(const input of $$("[name]",$("#property-dialog"))){const parts=input.name.split(".");let target=state.working;for(const key of parts.slice(0,-1))target=target[key];target[parts.at(-1)]=input.type==="number"?(input.value===""?null:Number(input.value)):input.value;}
  }
  async function saveProperty(patch={}) {
    captureProperty();const p=state.working;if(!p)return;
    const fields=["workflow_status","next_follow_up","review_notes","contact_name","contact_role","email","email_source","contact_source","phone","drafts","price_amount","price_basis","currency","area_sqft","bedrooms","bathrooms","lease_type","costs","due_diligence"];
    const body={};for(const key of fields)if(JSON.stringify(p[key])!==JSON.stringify(state.original[key]))body[key]=p[key];
    const result=await api(`/api/properties/${p.id}`,{method:"PATCH",body:{...body,...patch}});state.working=structuredClone(result.property||result.opportunity||result);state.original=structuredClone(state.working);state.dirty=false;await refresh({quiet:true});renderProperty();toast(patch.status==="approved"?"Inquiry approved.":patch.workflow_status==="shortlisted"?"Added to your shortlist.":"Changes saved.");
  }
  async function bulkUpdate(patch) {for(const id of state.selected)await api(`/api/properties/${id}`,{method:"PATCH",body:patch});state.selected.clear();await refresh();toast("Progress updated.");}
  async function download(format) {
    const response=await api(`/api/searches/${state.activeId}/export?format=${encodeURIComponent(format)}&scope=${encodeURIComponent(state.exportScope)}`,{download:true}),blob=await response.blob(),href=URL.createObjectURL(blob),a=document.createElement("a");a.href=href;a.download=`${search().name.replace(/[^a-zA-Z0-9_-]+/g,"-").toLowerCase()}-${state.exportScope}.${format}`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),1500);toast(`${format.toUpperCase()} downloaded.`);
  }
  function importDialog(kind="properties") {
    const title={properties:"Import properties",verification:"Import email verification",outcomes:"Import progress"}[kind];
    const copy={properties:"CSV or Excel with title, mode, listing_url, address, price_amount, price_basis, currency, and known facts. Matching property identities are deduplicated.",verification:"CSV from your verification tool: email, status, checked_at. Verified, risky, catch_all, and invalid remain distinct.",outcomes:"CSV with workflow_status and a property ID or listing URL. Add next_follow_up to record your next check-in."}[kind];
    $("#utility-dialog").innerHTML=`<form id="import-form" data-kind="${kind}"><div class="dialog-header"><div><span class="eyebrow">BRING YOUR RESEARCH</span><h2 id="utility-dialog-title">${title}</h2></div><button class="icon-button" data-action="close-dialog" type="button" aria-label="Close">×</button></div><div class="dialog-body"><p class="small muted" style="margin-bottom:22px">${copy}</p><div class="field"><label for="import-file">${kind==="properties"?"CSV or Excel":"CSV"} file</label><input id="import-file" name="file" type="file" accept="${kind==="properties"?".csv,.xlsx":".csv"}" required></div>${kind==="verification"?field("Verification provider","source","",{required:true,placeholder:"Your email verification service"}):""}<p class="small muted">Up to 8 MB. Imported facts need source checks before inquiry export.</p></div><div class="dialog-footer"><button class="button" type="button" data-action="close-dialog">Cancel</button><button class="button primary" type="submit">Import</button></div></form>`;openDialog("#utility-dialog");
  }
  async function importFile(form) {
    const file=$("[name=file]",form).files[0];if(!file)return;if(file.size>8*1024*1024)throw new Error("Choose a file smaller than 8 MB.");const kind=form.dataset.kind,endpoint=kind==="verification"?"verification/import":kind==="outcomes"?"outcomes/import":"import",data=new FormData();data.append("file",file);if(kind==="verification")data.append("source",$("[name=source]",form).value);
    const result=await api(`/api/searches/${state.activeId}/${endpoint}`,{method:"POST",body:data});await refresh();$("#utility-dialog").close();const count=result.imported??result.updated??result.created??result.count;toast(count!=null?`${count} ${kind==="properties"?"properties imported":"records updated"}.`:"Import complete.");if(result.errors?.length)toast(`${result.errors.length} rows need attention. ${typeof result.errors[0]==="string"?result.errors[0]:result.errors[0].error||result.errors[0].message||"Check your file."}`,true);
  }
  async function uploadDocuments(files) {
    if(state.configDirty&&!(await saveSearch({rerender:false})))return;for(const file of files){if(file.size>8*1024*1024)throw new Error(`${file.name} is over 8 MB.`);const data=new FormData();data.append("file",file);await api(`/api/searches/${state.activeId}/documents`,{method:"POST",body:data});}await refresh();toast("Documents added to your brief.");
  }
  async function suppressionsDialog() {
    const result=await api("/api/suppressions"),entries=Array.isArray(result)?result:result.suppressions||[];
    $("#utility-dialog").innerHTML=`<div class="dialog-header"><div><span class="eyebrow">ACROSS YOUR SEARCHES</span><h2 id="utility-dialog-title">Do not contact</h2></div><button class="icon-button" data-action="close-dialog" type="button" aria-label="Close">×</button></div><div class="dialog-body"><p class="small muted" style="margin-bottom:20px">Excluded addresses and domains stay out of inquiry exports.</p><form id="suppression-form"><div class="field-row">${select("Type","kind","email",[["email","Email"],["domain","Domain"]])}${field("Address or domain","value","",{required:true,placeholder:"broker@example.com"})}</div>${field("Reason","reason","",{placeholder:"Optional"})}<button class="button primary" type="submit">Add to list</button></form><div style="margin-top:25px">${entries.length?entries.map(e=>`<div class="suppression-row"><div><strong>${esc(e.value)}</strong><small>${esc(e.reason)}</small></div>${badge(e.kind)}</div>`).join(""):'<p class="small muted">No excluded contacts.</p>'}</div></div><div class="dialog-footer"><button class="button" type="button" data-action="suppression-import">Import CSV</button><button class="button primary" type="button" data-action="close-dialog">Done</button></div>`;openDialog("#utility-dialog");
  }
  function helpDialog() {
    const providers=(state.session?.providers||[]).map(p=>`<div class="provider-row"><div><strong>${esc(p.name||p.id)}</strong>${badge(p.available?"CLI found":"Not found",p.available?"good":"")}</div><p>${esc(p.message||p.path||"Uses your locally installed CLI and existing sign-in.")}</p></div>`).join("");
    $("#utility-dialog").innerHTML=`<div class="dialog-header"><div><span class="eyebrow">GOOSNAV LABS</span><h2 id="utility-dialog-title">Ready when you are.</h2></div><button class="icon-button" type="button" data-action="close-dialog" aria-label="Close">×</button></div><div class="dialog-body"><h3>Your agents</h3>${providers||'<p class="small muted">Install and sign in to Codex or Claude Code.</p>'}<p class="small muted" style="margin:15px 0 24px">Your brief and document excerpts are sent to the provider selected for each task. Workspace data is stored locally.</p><h3>From the terminal</h3><pre class="help-command">uv run --project app --no-dev python app/run.py doctor --json
uv run --project app --no-dev python app/run.py search create --help
uv run --project app --no-dev python app/run.py run SEARCH_ID --stage pipeline
uv run --project app --no-dev python app/run.py export SEARCH_ID --output properties.csv --scope all</pre><h3>From brief to shortlist</h3><ol class="plain-list" style="margin:17px 0 24px"><li>Set your mode, location, budget, and requirements.</li><li>Choose agents and limits, then run.</li><li>Review costs, source dates, and open questions.</li><li>Shortlist properties and edit your inquiries.</li><li>Export, contact the listing yourself, and track progress.</li></ol><p class="small muted">A listing is a research lead until verified. Inquiries, applications, offers, and payments stay in your hands.</p>${state.session?.data_directory?`<div style="border-top:1px solid var(--line);margin-top:24px;padding-top:16px"><p class="small muted">Local workspace</p><code class="small" style="overflow-wrap:anywhere">${esc(state.session.data_directory)}</code></div>`:""}</div><div class="dialog-footer"><button class="button" type="button" data-action="demo-picker">Try a demo</button><button class="button" type="button" data-action="suppressions">Do not contact</button><button class="button primary" type="button" data-action="close-dialog">Got it</button></div>`;openDialog("#utility-dialog");
  }

  document.addEventListener("click",event=>{
    const button=event.target.closest("[data-action]");if(!button){const row=event.target.closest("tr[data-property]");if(row&&!event.target.closest("input,a,button"))openProperty(row.dataset.property);return;}
    const action=button.dataset.action;
    if(action==="close-dialog"){closeDialog(button.closest("dialog"));return;}
    if(action==="home"){event.preventDefault();if(!leaveConfig())return;state.configDirty=false;state.view="properties";render();return;}
    if(action==="new"){if(leaveConfig())newSearchDialog();return;}
    if(action==="help"){helpDialog();return;}
    if(action==="demo-picker"){demoPicker();return;}
    if(action==="view"){if(!leaveConfig())return;state.configDirty=false;state.view=button.dataset.view;state.selected.clear();render();return;}
    if(action==="open-property"){openProperty(button.dataset.id);return;}
    if(action==="detail-tab"){captureProperty();state.detailTab=button.dataset.tab;renderProperty();$(`#detail-tab-${state.detailTab}`).focus();return;}
    if(action==="clear-selection"){state.selected.clear();table();return;}
    if(action==="reset-filters"){state.filter="";state.statusFilter="all";render();return;}
    if(action==="toggle-log"){state.logOpen=!state.logOpen;renderRun();return;}
    if(action==="dismiss-run"){state.dismissed.add(state.run.id);renderRun();return;}
    if(action==="import"){importDialog();return;}
    if(action==="verification"){importDialog("verification");return;}
    if(action==="outcomes"){importDialog("outcomes");return;}
    if(action==="documents"){$("#documents-input").click();return;}
    if(action==="suppression-import"){$("#suppression-input").click();return;}
    busy("Working…",async()=>{
      if(action==="open-search"){if(leaveConfig())await loadSearch(button.dataset.id);}
      else if(action==="demo"){if(!leaveConfig())return;const result=await api(`/api/demo?mode=${encodeURIComponent(button.dataset.mode)}`,{method:"POST"});$("#utility-dialog").close();await listSearches();await loadSearch((result.search||result).id,{view:"properties"});toast("Demo ready. All properties and contacts are fictional.");}
      else if(action==="refresh"){if(!leaveConfig())return;state.configDirty=false;await refresh();}
      else if(action==="run")await startRun();
      else if(["pause-run","resume-run","cancel-run"].includes(action)){const verb=action.split("-")[0],result=await api(`/api/runs/${state.run.id}/${verb}`,{method:"POST"});state.run=result.run||result;renderRun();await pollRun();}
      else if(action==="bulk-shortlist")await bulkUpdate({workflow_status:"shortlisted"});
      else if(action==="bulk-skip")await bulkUpdate({workflow_status:"not_interested"});
      else if(action==="save-property")await saveProperty();
      else if(action==="shortlist-property")await saveProperty({workflow_status:"shortlisted"});
      else if(action==="skip-property")await saveProperty({workflow_status:"not_interested"});
      else if(action==="approve-property")await saveProperty({status:"approved"});
      else if(action==="draft-from-detail"){if(state.dirty)await saveProperty();$("#property-dialog").close();state.view="inquiries";render();$("#run-stage").value="draft";await startRun();}
      else if(action==="export")await download(button.dataset.format);
      else if(action==="remove-document"){if(state.configDirty&&!(await saveSearch({rerender:false})))return;await api(`/api/searches/${state.activeId}/documents/${button.dataset.id}`,{method:"DELETE"});await refresh();toast("Document removed.");}
      else if(action==="suppressions")await suppressionsDialog();
      else if(action==="suppress-property"){const p=state.working;if(!p.email&&!p.domain)throw new Error("Add a public email or listing domain first.");await api("/api/suppressions",{method:"POST",body:{kind:p.email?"email":"domain",value:p.email||p.domain,reason:"User marked do not contact"}});state.dirty=false;$("#property-dialog").close();await refresh();toast("Added to the do not contact list.");}
      else if(action==="quit"){if(!confirm("Close Real Estate Finder? Active work will be paused."))return;await api("/api/quit",{method:"POST"});clearTimeout(state.pollTimer);$("#main").innerHTML='<section class="empty-workspace"><span class="eyebrow">WORKSPACE CLOSED</span><h1>SEE YOU<br>NEXT TIME.</h1><p class="prose">Your searches are saved. Reopen Real Estate Finder to continue.</p></section>';$("#run-region").innerHTML="";}
      else if(action==="retry")await init();
    },button);
  });
  document.addEventListener("submit",event=>{
    const form=event.target;if(!["brief-form","new-search-form","import-form","suppression-form"].includes(form.id))return;event.preventDefault();
    busy("Saving…",async()=>{
      if(form.id==="brief-form"){if(await saveSearch())toast("Brief saved.");}
      else if(form.id==="new-search-form"){const result=await api("/api/searches",{method:"POST",body:formSearch(form,defaults)});$("#search-dialog").close();await listSearches();await loadSearch((result.search||result).id,{view:"brief"});toast("Search created. Add your budget and requirements.");}
      else if(form.id==="import-form")await importFile(form);
      else if(form.id==="suppression-form"){const data=Object.fromEntries(new FormData(form));if(!data.reason)data.reason="Do not contact";await api("/api/suppressions",{method:"POST",body:data});await suppressionsDialog();if(state.activeId)await refresh();toast("Added to the do not contact list.");}
    },event.submitter);
  });
  function markDirty(el) {
    if(el.hasAttribute("aria-invalid"))el.removeAttribute("aria-invalid");
    if(el.closest("#brief-form")){state.configDirty=true;$("#save-message").textContent="Unsaved changes";}
    if(el.closest("#property-dialog")&&el.name){state.dirty=true;$("#property-save-indicator").textContent="Unsaved changes";}
  }
  document.addEventListener("input",event=>{
    const el=event.target;if(el.id==="property-filter"){state.filter=el.value;table();}
    markDirty(el);
    if(el.name?.startsWith("drafts.")){const [,index,key]=el.name.split("."),count=$(`[data-count="${el.name}"]`);if(count){const max=key==="subject"?5:Number(index)===0?90:65;count.textContent=`${words(el.value)}/${max} words`;count.classList.toggle("over",words(el.value)>max);}if(key==="body")sizeDrafts();}
  });
  document.addEventListener("change",event=>{
    const el=event.target;
    if(el.id==="status-filter"){state.statusFilter=el.value;table();}
    else if(el.name==="mode"||el.name==="cost_assumptions.financing")updateModeVisibility(el.closest("form"));
    else if(el.name==="settings.provider"&&$("#provider-notice"))$("#provider-notice").innerHTML=providerNotice(el.value);
    else if(el.dataset.select){if(el.checked)state.selected.add(el.dataset.select);else state.selected.delete(el.dataset.select);table();}
    else if(el.id==="select-all"){for(const p of filtered()){if(el.checked)state.selected.add(p.id);else state.selected.delete(p.id);}table();}
    else if(el.name==="export-scope"){state.exportScope=el.value;$$(".export-choice").forEach(l=>l.classList.toggle("selected",$("input",l).checked));exportReadiness();}
    else if(el.id==="documents-input"){const files=[...el.files];el.value="";if(files.length)busy("Reading documents…",()=>uploadDocuments(files));}
    else if(el.id==="suppression-input"){const file=el.files[0];el.value="";if(file)busy("Importing…",async()=>{const data=new FormData();data.append("file",file);await api("/api/suppressions/import",{method:"POST",body:data});await suppressionsDialog();if(state.activeId)await refresh();toast("Do not contact list imported.");});}
    markDirty(el);
  });
  document.addEventListener("keydown",event=>{
    if(event.target.matches('[role="tab"]')&&["ArrowLeft","ArrowRight","Home","End"].includes(event.key)){event.preventDefault();const tabs=$$('[role="tab"]',$("#property-dialog"));let i=tabs.indexOf(event.target);i=event.key==="Home"?0:event.key==="End"?tabs.length-1:(i+(event.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;tabs[i].click();}
  });
  $$("dialog").forEach(dialog=>dialog.addEventListener("cancel",event=>{event.preventDefault();closeDialog(dialog);}));
  window.addEventListener("resize",sizeDrafts);
  window.addEventListener("beforeunload",event=>{if(state.configDirty||state.dirty){event.preventDefault();event.returnValue="";}});
  async function init() {
    $("#request-status").textContent="Opening workspace…";
    try{state.session=await api("/api/session");await listSearches();let saved="";try{saved=localStorage.getItem("real-estate-finder-active-search")||"";}catch{}const first=state.searches.find(s=>s.id===saved)||state.searches[0];if(first)await loadSearch(first.id);else render();}
    catch(error){$("#main").innerHTML=`<section class="empty-workspace"><span class="eyebrow">CONNECTION NEEDED</span><h1>LET’S<br>RECONNECT.</h1><p class="prose">${esc(error.message)}</p><div class="actions"><button class="button primary" type="button" data-action="retry">Retry</button></div></section>`;}
    finally{$("#request-status").textContent="";}
  }
  init();
})();
