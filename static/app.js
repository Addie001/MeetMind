const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = {meetings:[],config:{}}, active = null, busy = false, noticeTimer;
let manualFileVersion = 0;
let expanded = null, projects = [], projectId = '', remoteMeetings = [], selectedMeeting = null, importing = false, searchVersion = 0;
const drafts = new Map(), collapsed = new Set();
const statusName = {new:'待分析',analyzing:'分析中',review:'待审阅',error:'分析失败'};
const draftKey = id => `${active}:${id}`;
const current = () => state.meetings.find(m => m.id === active);
function notice(message) { $('#notice').textContent=message; $('#notice').hidden=false; clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>$('#notice').hidden=true,8000); }
async function api(path,body,method=body===undefined?'GET':'POST') {
  const response=await fetch(path,method==='GET'?{}:{method,headers:{'Content-Type':'application/json','X-MeetMind-Token':state.token},body:JSON.stringify(body)});
  const data=await response.json(); if(!response.ok) throw Error(data.error||'请求失败，请重试'); return data;
}
async function refresh(renderPage=true) { state=await api('/api/state'); if(!state.meetings.some(m=>m.id===active)) {active=state.meetings[0]?.id||null;expanded=null;} if(renderPage) render(); }
function hasDrafts() { return current()?.report?.actions.some(a=>drafts.has(draftKey(a.id))); }
function syncEnabled() { return !busy&&!hasDrafts()&&current()?.report?.actions.some(a=>a.selected&&a.status==='pending'); }
function updateSync() { const button=$('[data-command="sync"]'); if(button) button.disabled=!syncEnabled(); }
function projectOptions() { return '<option value="">选择目标清单</option>'+projects.map(p=>`<option value="${esc(p.id)}" ${p.id===projectId?'selected':''}>${esc(p.name)}</option>`).join(''); }
function actionStatus(a) {
  if(a.status==='synced') return ['已同步',''];
  if(a.status==='syncing') return ['同步中',''];
  if(a.status==='uncertain') return ['待核实','error'];
  if(drafts.has(draftKey(a.id))) return ['未保存','warn'];
  if(a.sync_error) return ['同步失败','error'];
  return a.confirmed?['已确认','']:['待确认','warn'];
}
function render() {
  $('#mode').textContent=state.config.llm?'模型已配置':'离线规则模式';
  $('#meeting-count').textContent=state.meetings.length;
  $('#meetings').innerHTML=state.meetings.map(m=>`<div class="meeting-item ${m.id===active?'active':''}"><button class="meeting-link" data-meeting="${m.id}" ${m.id===active?'aria-current="page"':''}>${esc(m.title)}<small>${esc(m.date)} · ${statusName[m.status]}</small></button><button class="meeting-delete" data-delete-meeting="${m.id}" aria-label="删除会议：${esc(m.title)}" title="删除会议" ${busy?'disabled':''}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg></button></div>`).join('');
  const m=current();
  if(!m) { $('#content').innerHTML='<div class="hero"><h1>暂无会议</h1><button data-command="import">导入会议</button></div>'; return; }
  const report=m.report;
  $('#content').innerHTML=`<div class="meeting-heading"><h1>${esc(m.title)}</h1><div class="meeting-info"><span>◷ ${esc(m.date)}</span><span>${m.paragraphs.length} 段原始转写</span><span class="badge ${m.status==='error'?'error':''}">${statusName[m.status]}</span></div></div><div class="report-grid"><section class="task-column">${m.error?`<div class="summary"><span class="badge error">处理失败</span><p>${esc(m.error)}</p></div>`:''}${report?`<div class="section-heading"><h3>会议行动项</h3></div><div class="toolbar"><span class="progress">${report.actions.length} 项行动 · ${report.actions.filter(a=>a.confirmed).length} 项已确认</span><button class="quiet" data-command="projects">加载清单</button><select id="project" aria-label="目标滴答清单">${projectOptions()}</select><button data-command="sync" ${syncEnabled()?'':'disabled'}>同步已勾选项</button></div><div class="summary"><div class="summary-label">一句话总结</div><p>${esc(report.summary)}</p></div>${renderActions(report.actions)}${renderAgentReview(report)}${renderPending(report.actions)}`:`<div class="summary">${m.status==='analyzing'?'<p class="muted">正在获取转写并分析…</p>':''}<button data-command="analyze" ${m.status==='analyzing'?'disabled':''}>${m.status==='analyzing'?'后台处理中…':'分析这场会议'}</button></div>`}</section><section class="evidence-column"><div class="evidence-heading"><h3>原始转写</h3><span class="badge">证据来源</span></div><div class="transcript">${m.paragraphs.length?m.paragraphs.map(p=>`<div class="paragraph" id="p-${esc(p.id)}"><div class="meta"><span class="speaker">${esc(p.speaker)}</span><span>${esc(p.timestamp)}</span></div><p>${esc(p.text)}</p></div>`).join(''):'<div class="empty">暂无转写</div>'}</div></section></div>`;
}
function renderAgentReview(report) {
  if(report.mode!=='dual_agent') return '';
  const pending=report.actions.filter(a=>a.verification?.verdict!=='pass');
  const rejected=report.rejected_actions||[];
  return `<details class="secondary-panel verification-panel"><summary>证据核验 <span class="panel-count">${pending.length+rejected.length} 项需关注</span></summary><div class="secondary-content"><strong>${report.verification_status==='completed'?'证据核验已完成':'未完成证据核验，请人工审查'}</strong>${pending.length?`<ul>${pending.map(a=>`<li>${esc(a.title)}：${esc((a.verification?.issues||[]).join('；'))}</li>`).join('')}</ul>`:''}${rejected.length?`<details class="nested-panel"><summary>查看建议排除项</summary><ul>${rejected.map(a=>`<li>${esc(a.title)}：${esc((a.verification?.issues||[]).join('；'))}</li>`).join('')}</ul><p>请对照右侧原文核实。</p></details>`:''}</div></details>`;
}
function renderPending(actions) {
  const pending=actions.filter(a=>a.status==='pending');
  if(!pending.length)return '';
  const topicCounts = pending.reduce((map,a)=>(map[a.topic||'—']=(map[a.topic||'—']||0)+1,map),{}), topicSeen={};
  return `<details class="secondary-panel pending-panel"><summary>待同步 <span class="panel-count">${pending.length} 项</span></summary><div class="secondary-content"><div class="pending-table-wrap"><table class="pending-table"><thead><tr><th>行动</th><th>议题</th><th>失败原因</th></tr></thead><tbody>${pending.map(a=>{const topic=a.topic||'—';const topicCell=topicSeen[topic]?'' :(topicSeen[topic]=true,`<td rowspan="${topicCounts[topic]}">${esc(topic)}</td>`);return `<tr><td>${esc(a.title)}</td>${topicCell}<td>${esc(a.sync_error||'待同步')}</td></tr>`;}).join('')}</tbody></table></div></div></details>`;
}
function renderVerification(a) {
  if(a.status==='uncertain')return `<section class="verification"><strong>请先到滴答核实这条任务</strong><p>所属分组：${esc(a.topic)}</p><p>上次失败原因：${esc(a.sync_error||'未记录')}</p><div class="card-actions"><button class="quiet" data-action="resolve-created">确认已创建</button><button class="quiet" data-action="resolve-not-created">确认未创建，重新同步</button></div></section>`;
  const latest=a.verification_history?.at(-1);
  return latest?`<p class="verification-record">手动核实：${latest.resolution==='created'?'已创建':'未创建'} · ${esc(latest.verified_at)}</p>`:'';
}
function renderActions(actions) {
  if(!actions.length) return '<div class="empty">没有发现明确的行动项，可核对右侧原文。</div>';
  return [...new Set(actions.map(a=>a.topic))].map(topic=>{
    const items=actions.filter(a=>a.topic===topic), key=active+':'+topic;
    return `<details class="topic" data-topic="${esc(key)}" ${collapsed.has(key)?'':'open'}><summary>${esc(topic)}<span class="topic-count">${items.length} 个任务</span></summary><div class="list-head" aria-hidden="true"><span></span><span>行动内容</span><span>负责人</span><span>截止日期</span><span>状态</span><span></span></div>${items.map(a=>{
      const draft=drafts.get(draftKey(a.id)), v=draft||a, locked=a.status!=='pending', opened=expanded===a.id, [status,kind]=actionStatus(a);
      return `<article class="action ${opened?'expanded':''}" data-id="${a.id}" ${draft?'data-dirty="true"':''}><div class="action-row" tabindex="0" role="button" aria-label="${opened?'收起':'编辑'}行动：${esc(a.title)}" aria-expanded="${opened}"><input type="checkbox" data-field="selected" aria-label="选择同步：${esc(a.title)}" ${a.selected&&!draft?'checked':''} ${!a.confirmed||locked||draft?'disabled':''}><span class="task-title">${esc(v.title)}</span><span class="owner" title="${esc(v.owner||'待确认')}">${esc(v.owner||'待确认')}</span><span class="due" title="${esc(v.due_date||'未设置')}">${esc(v.due_date||'未设置')}</span><span class="badge ${kind}">${status}</span><button class="row-edit" data-action="edit" aria-label="${opened?'收起':'展开编辑'}">${opened?'⌃':'⌄'}</button></div>${a.sync_error?`<p class="sync-error" role="alert">${esc(a.sync_error)}</p>`:''}${renderVerification(a)}<div class="task-editor" ${opened?'':'hidden'}><div class="fields"><label class="wide">行动内容<textarea data-field="title" rows="2" ${locked?'disabled':''}>${esc(v.title)}</textarea></label><label>议题<input data-field="topic" value="${esc(v.topic)}" ${locked?'disabled':''}></label><label>负责人<input data-field="owner" value="${esc(v.owner)}" placeholder="待确认" ${locked?'disabled':''}></label><label>截止日期<input type="date" data-field="due_date" value="${esc(v.due_date)}" ${locked?'disabled':''}></label><label>验收标准<input data-field="acceptance_criteria" value="${esc(v.acceptance_criteria)}" placeholder="待确认" ${locked?'disabled':''}></label></div><div class="card-actions"><button class="quiet" data-action="evidence">查看原文 ↗</button>${locked?'':`<button class="quiet" data-action="save">保存修改</button><button data-action="confirm">${draft?'保存后重新确认':a.confirmed?'撤销确认':'确认此项'}</button><button class="danger" data-action="delete">删除</button>`}</div></div></article>`;
    }).join('')}</details>`;
  }).join('');
}
function openImport() {
  if(busy) return;
  remoteMeetings=[];selectedMeeting=null;searchVersion++;
  $('#find-meetings').disabled=false;$('#find-meetings').textContent='导入';
  $('#remote-meetings').innerHTML='';$('#meeting-preview').hidden=true;$('#import-status').textContent='';$('#import-status').className='';
  $('#import-disclosure').textContent=state.config.llm?'点击后会获取完整原始转写，并发送至已配置的模型服务分析。':'点击后会获取原始转写，使用本地规则筛选候选行动项。';
  setImportMode('tencent');
  if(!$('#manual-form').elements.date.value) $('#manual-form').elements.date.value=new Date().toLocaleDateString('sv-SE');
  $('#import-dialog').showModal();
}
function setImportMode(mode) {
  if(importing)return;
  for(const name of ['tencent','manual']) {
    const selected=mode===name, tab=$('#tab-'+name);
    tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;
    $('#'+name+'-panel').hidden=!selected;
  }
}
for(const name of ['tencent','manual']) {
  $('#tab-'+name).onclick=()=>setImportMode(name);
  $('#tab-'+name).onkeydown=e=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
    e.preventDefault();if(importing)return;
    const next=e.key==='Home'?'tencent':e.key==='End'?'manual':name==='tencent'?'manual':'tencent';
    setImportMode(next);$('#tab-'+next).focus();
  };
}
$('#manual-file').onchange=async e=>{
  const file=e.target.files[0],version=++manualFileVersion;
  if(!file)return;
  try {
    if(file.size>2_000_000)throw Error('文件不能超过 2 MB');
    if(!/\.(txt|json)$/i.test(file.name))throw Error('请选择 TXT 或 JSON 文件');
    const text=await file.text();
    if(version!==manualFileVersion)return;
    $('#manual-form').elements.transcript.value=text;
    $('#manual-status').textContent='文件已载入，可以核对内容后导入。';
  } catch(err){$('#manual-status').textContent=err.message;}
};
$('#manual-form').elements.transcript.oninput=()=>{manualFileVersion++;};
$('#manual-sample').onclick=()=>{
  manualFileVersion++;
  const form=$('#manual-form');
  form.elements.title.value='产品项目周会 · 示例';
  form.elements.date.value=new Date().toLocaleDateString('sv-SE');
  form.elements.transcript.value='[00:00] 小林：议题：接口联调\n[00:15] 小林：我负责提交接口文档。\n[00:45] 小周：我会完成登录接口联调。\n[01:10] 主持人：议题：上线验收\n[01:25] 主持人：需要跟进验收清单整理。';
  $('#manual-file').value='';$('#manual-status').textContent='示例已填入。导入后可手动启动分析。';
};
$('#manual-form').onsubmit=async e=>{
  e.preventDefault();if(importing)return;
  const body=Object.fromEntries(new FormData(e.target));
  if(new TextEncoder().encode(JSON.stringify(body)).length>2_000_000) {$('#manual-status').textContent='内容过大，请将请求内容缩减至 2 MB 以内。';return;}
  importing=true;manualFileVersion++;
  const controls=[...e.target.querySelectorAll('input,textarea,button'),$('#cancel-import'),$('#tab-tencent'),$('#tab-manual')];
  controls.forEach(el=>el.disabled=true);$('#manual-status').textContent='正在导入…';
  try {
    const meeting=await api('/api/meetings',body);
    active=meeting.id;expanded=null;$('#import-dialog').close();
    e.target.reset();e.target.elements.date.value=new Date().toLocaleDateString('sv-SE');
    $('#manual-status').textContent='';await refresh();notice('会议已导入，核对转写后可点击「分析这场会议」。');
  } catch(err){$('#manual-status').textContent=err.message;}
  finally {importing=false;controls.forEach(el=>el.disabled=false);}
};
$('#new-meeting').onclick=openImport;
$('#cancel-import').onclick=()=>{if(!importing) {searchVersion++;$('#import-dialog').close();}};
$('#import-dialog').addEventListener('cancel',e=>{if(importing)e.preventDefault();else searchVersion++;});
function renderRemote() {
  $('#remote-meetings').innerHTML=remoteMeetings.map((m,i)=>`<button type="button" class="remote-meeting ${selectedMeeting===i?'selected':''}" data-choice="${i}" ${!m.available&&!m.imported_id?'disabled':''}><span class="radio"></span><span>${esc(m.title)}<small>${esc(m.time)}${!m.available?' · '+esc(m.reason):''}</small></span>${m.imported_id?'<span class="badge">已导入</span>':''}</button>`).join('');
}
$('#import-form').onsubmit=async e=>{
  e.preventDefault();if(importing) return;
  const version=++searchVersion;selectedMeeting=null;$('#meeting-preview').hidden=true;$('#remote-meetings').innerHTML='';
  $('#find-meetings').disabled=true;$('#find-meetings').textContent='查询中…';$('#import-status').className='';$('#import-status').textContent='正在获取腾讯会议列表…';
  try {
    const rows=await api('/api/tencent/meetings',{days:Number($('#time-range').value)});
    if(version!==searchVersion)return;
    remoteMeetings=rows;renderRemote();$('#import-status').textContent=rows.length?`找到 ${rows.length} 场已结束会议，请选择一场。`:'这个时间范围内没有已结束会议，可扩大范围后重试。';
  } catch(err) {if(version===searchVersion) {$('#import-status').textContent=err.message+'，可点击导入重试。';$('#import-status').className='error';}}
  finally {if(version===searchVersion) {$('#find-meetings').disabled=false;$('#find-meetings').textContent='导入';}}
};
$('#time-range').onchange=()=>{searchVersion++;remoteMeetings=[];selectedMeeting=null;$('#remote-meetings').innerHTML='';$('#meeting-preview').hidden=true;$('#import-status').textContent='点击导入，查询所选时间范围。';$('#find-meetings').disabled=false;$('#find-meetings').textContent='导入';};
$('#remote-meetings').onclick=e=>{
  const button=e.target.closest('[data-choice]');if(!button||importing)return;
  selectedMeeting=Number(button.dataset.choice);const m=remoteMeetings[selectedMeeting];renderRemote();
  $('#preview-title').value=m.title;$('#preview-date').value=m.time;$('#meeting-preview').hidden=false;$('#confirm-import').textContent=m.imported_id?'打开已导入会议 →':'导入并分析 →';
};
$('#confirm-import').onclick=async()=>{
  if(selectedMeeting===null||importing)return;
  const m=remoteMeetings[selectedMeeting];
  if(m.imported_id) {active=m.imported_id;expanded=null;$('#import-dialog').close();await refresh();return;}
  importing=true;$('#confirm-import').disabled=true;$('#cancel-import').disabled=true;$('#find-meetings').disabled=true;$('#time-range').disabled=true;
  try {const imported=await api('/api/tencent/import',{choice_id:m.choice_id});active=imported.id;expanded=null;$('#import-dialog').close();await refresh();notice('会议已导入，正在后台获取转写并分析。');}
  catch(err){$('#import-status').textContent=err.message;$('#import-status').className='error';}
  finally {importing=false;$('#confirm-import').disabled=false;$('#cancel-import').disabled=false;$('#find-meetings').disabled=false;$('#time-range').disabled=false;}
};
$('#meetings').onclick=async e=>{
  const remove=e.target.closest('[data-delete-meeting]');
  if(remove) {
    if(busy||importing)return;
    const mid=remove.dataset.deleteMeeting,meeting=state.meetings.find(m=>m.id===mid);
    if(!meeting)return;
    if(!confirm(`确定要删除「${meeting.title}」的会议记录及分析结果吗？此操作不可恢复。\n仅删除本地记录，不删除腾讯会议或已同步的滴答任务。`))return;
    busy=true;remove.disabled=true;updateSync();
    try {
      await api('/api/meetings/'+encodeURIComponent(mid),undefined,'DELETE');
      for(const key of drafts.keys())if(key.startsWith(mid+':'))drafts.delete(key);
      for(const key of collapsed)if(key.startsWith(mid+':'))collapsed.delete(key);
      remoteMeetings=remoteMeetings.map(m=>m.imported_id===mid?{...m,imported_id:null}:m);
      if(active===mid){active=null;expanded=null;}
      await refresh();notice('会议已删除');
    } catch(err){notice(err.message);}
    finally {busy=false;document.querySelectorAll('.meeting-delete').forEach(b=>b.disabled=false);updateSync();}
    return;
  }
  const button=e.target.closest('[data-meeting]');
  if(button&&!busy){active=button.dataset.meeting;expanded=null;render();}
};
function values(card) {return Object.fromEntries([...card.querySelectorAll('[data-field]:not([type=checkbox])')].map(el=>[el.dataset.field,el.value]));}
function toggleEditor(card) {if(busy)return;expanded=expanded===card.dataset.id?null:card.dataset.id;render();}
$('#content').addEventListener('toggle',e=>{const el=e.target;if(el.matches('.topic')) {if(el.open)collapsed.delete(el.dataset.topic);else collapsed.add(el.dataset.topic);}},true);
$('#content').addEventListener('keydown',e=>{if(e.target.matches('.action-row')&&(e.key==='Enter'||e.key===' ')) {e.preventDefault();toggleEditor(e.target.closest('.action'));}});
$('#content').addEventListener('input',e=>{
  if(!e.target.matches('[data-field]:not([type=checkbox])'))return;
  const card=e.target.closest('.action');drafts.set(draftKey(card.dataset.id),values(card));card.dataset.dirty='true';
  const checkbox=card.querySelector('[data-field=selected]');checkbox.disabled=true;checkbox.checked=false;
  const badge=card.querySelector('.action-row .badge');badge.textContent='未保存';badge.className='badge warn';
  const button=card.querySelector('[data-action=confirm]');if(button)button.textContent='保存后重新确认';updateSync();
});
$('#content').addEventListener('change',async e=>{
  if(e.target.id==='project'){projectId=e.target.value;return;}
  if(e.target.dataset.field!=='selected')return;
  if(busy){e.target.checked=!e.target.checked;return;}
  busy=true;updateSync();
  try {await api('/api/review',{meeting_id:active,action_id:e.target.closest('.action').dataset.id,selected:e.target.checked});await refresh(false);}
  catch(err){notice(err.message);await refresh();}
  finally {busy=false;updateSync();}
});
$('#content').onclick=async e=>{
  const button=e.target.closest('button'), row=e.target.closest('.action-row');
  if(row&&!e.target.closest('input')&&(!button||button.dataset.action==='edit')) {toggleEditor(row.closest('.action'));return;}
  if(!button||busy)return;
  const command=button.dataset.command,action=button.dataset.action;
  if(command==='import')return openImport();
  const mid=active;
  busy=true;button.disabled=true;updateSync();
  try {
    if(command==='analyze'){await api('/api/analyze',{meeting_id:mid});await refresh();}
    if(command==='projects'){projects=await api('/api/projects');$('#project').innerHTML=projectOptions();}
    if(command==='sync'){
      if(hasDrafts())throw Error('请先保存修改并重新确认');
      if(!projectId)throw Error('请先加载并选择目标清单');
      const ids=current().report.actions.filter(a=>a.selected&&a.status==='pending').map(a=>a.id);
      if(!ids.length)throw Error('请勾选已确认的行动项');
      if(!confirm(`将 ${ids.length} 条已确认行动写入「${$('#project').selectedOptions[0].textContent}」？\n将按议题名分组，没有同名分组时自动创建。`))return;
      const result=await api('/api/sync',{meeting_id:mid,action_ids:ids,project_id:projectId});await refresh();
      const sent=result.report.actions.filter(a=>ids.includes(a.id));
      const done=sent.filter(a=>a.status==='synced').length;
      const unknown=sent.filter(a=>a.status==='uncertain').length;
      notice(`已同步 ${done} 项，未写入 ${sent.length-done-unknown} 项，待核实 ${unknown} 项。`);
    }
    if(action){
      const card=button.closest('.action'),a=current().report.actions.find(a=>a.id===card.dataset.id);
      if(action==='evidence'){
        document.querySelectorAll('.highlight').forEach(el=>el.classList.remove('highlight'));
        a.evidence_ref.forEach(id=>document.getElementById('p-'+id)?.classList.add('highlight'));
        document.getElementById('p-'+a.evidence_ref[0])?.scrollIntoView({behavior:'smooth',block:'nearest'});return;
      }
      if(action==='resolve-created'||action==='resolve-not-created'){
        const created=action==='resolve-created';
        const warning=created?'确认已在滴答找到对应任务？此项将标记为已同步，不再写入。':'确认已在滴答核实该任务未创建？此项将恢复待同步；之后仍需点击“同步已勾选项”才会创建。';
        if(!confirm(`${warning}\n任务：${a.title}\n所属分组：${a.topic}\n上次失败原因：${a.sync_error||'未记录'}`))return;
        await api('/api/resolve-uncertain',{meeting_id:mid,action_id:a.id,resolution:created?'created':'not_created'});
        await refresh();notice(created?'已记录手动核实，任务已标记为已同步。':'已恢复待同步，请核对后手动同步。');return;
      }
      if(action==='delete'&&!confirm('删除这条候选行动？'))return;
      const dirty=drafts.has(draftKey(a.id));
      await api('/api/review',{meeting_id:mid,action_id:a.id,values:values(card),delete:action==='delete',...(action==='confirm'&&!dirty?{confirmed:!a.confirmed}:{})});
      drafts.delete(draftKey(a.id));if(action==='delete')expanded=null;await refresh();if(dirty)notice('修改已保存，请核对后重新确认。');
    }
  } catch(err){notice(err.message);} finally {busy=false;button.disabled=false;updateSync();}
};
$('#open-settings').onclick=()=>{$('#connections').innerHTML=['llm','tencent','dida'].map((k,i)=>`<p>${['分析模型','腾讯会议','滴答清单'][i]}<span class="badge ${state.config[k]?'':'warn'}">${state.config[k]?'已配置':'未配置'}</span></p>`).join('');$('#settings').showModal();};
$('#close-settings').onclick=()=>$('#settings').close();
refresh().catch(err=>notice(err.message));
setInterval(async()=>{if(!busy&&current()?.status==='analyzing') {try{await refresh();}catch(err){notice(err.message);}}},2000);
