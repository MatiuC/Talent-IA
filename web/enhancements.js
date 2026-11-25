'use strict';
let calendarMonth=new Date(new Date().getFullYear(),new Date().getMonth(),1),selectedDay='';
const dateKey=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const eventKey=i=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/Guayaquil',year:'numeric',month:'2-digit',day:'2-digit'}).format(dateObj(i.fecha_hora));
const demoMeet=i=>i.modo==='real'?(i.payload?.meet_url||i.enlace_evento||''):`https://meet.google.com/demo-${String(i.id).padStart(3,'0')}-talentia`;
const previousSettings=settingsPage,previousBinding=bindPage,previousFilter=filteredCandidates;
labels.ANALIZANDO=['Analizando en tu equipo','blue'];
let sortMode='best';
filteredCandidates=function(){const list=previousFilter().filter(c=>c.estado!=='ERROR_PROCESAMIENTO');return sortMode==='least'?list.sort((a,b)=>(a.puntaje??99)-(b.puntaje??99)):sortMode==='recent'?list.sort((a,b)=>b.id-a.id):list};
settingsPage=function(){return `<section class="panel setting-card section-gap"><h2>Motor de este computador</h2><p>La web guarda tus documentos y resultados en el servidor. Qwen analiza el texto en tu computador mediante el asistente local.</p><div class="detail-actions"><a class="btn btn-secondary" href="/assets/downloads/TalentIA-Asistente.zip" download>${icon('download')} Descargar asistente Windows</a><a class="text-link" href="http://127.0.0.1:11435" target="_blank" rel="noopener">Abrir mi clave local</a></div><p class="section-gap">Con LM Studio abierto y un modelo de conversación cargado, ejecuta companion/Iniciar-LMStudio.ps1 desde la carpeta del proyecto (o Iniciar-LMStudio.ps1 si descargaste el asistente). Mantén esa terminal abierta y después pulsa «Abrir mi clave local». Abrir solo LM Studio no inicia el asistente. Permite el acceso local si el navegador lo solicita.</p><div class="field"><label for="companion-key">Clave de vinculación del asistente</label><input id="companion-key" type="password" autocomplete="off" placeholder="Pega la clave que muestra tu asistente"></div><button class="btn btn-primary" data-action="connect-local">Conectar mi computador</button> <button class="btn btn-secondary" data-action="pull-local">Comprobar modelo local</button><div class="notice blue section-gap" id="motor-state">El modelo se descarga una vez por computador y se reutiliza en las siguientes visitas.</div></section>`+previousSettings().replace(/<div class="metric-row"><span>Motor de análisis<\/span>[\s\S]*?<\/div>/,'')};
async function bridge(path,body){const key=sessionStorage.getItem('talentia-companion')||'';if(!key)throw new Error('Conecta tu asistente en Configuración antes de analizar.');let response;try{response=await fetch('http://127.0.0.1:11435'+path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+key,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(path==='/chat'?660000:15000)})}catch{throw new Error('No se puede conectar al asistente. Ábrelo y permite el acceso local del navegador para esta web.')}const result=await response.json();if(!response.ok)throw new Error(result.error||'El asistente no pudo completar la solicitud.');return result}

const analysisBatch={
 visible:false,running:false,phase:'idle',vacancy:'',vacancyTitle:'',
 total:0,done:0,ok:0,manual:0,errors:0,file:'',errorMessage:'',recoveryVacancies:[]
};
let analysisDom=null,checkedPendingUser=null,pendingCheckPromise=null;
function progressStorageKey(){return 'talentia-analysis-'+(state.user?.id||state.user?.email||'guest')}
function clearSavedProgress(){try{sessionStorage.removeItem(progressStorageKey())}catch{}}
function isConnectionErrorMessage(msg){
 return /No se puede conectar al asistente|Origen o clave|Conecta tu asistente|Prepara el modelo|Tu sesión terminó|Failed to fetch|NetworkError|Load failed|servidor de TalentIA/i.test(String(msg||''));
}
function ensureAnalysisDom(){
 const panel=document.getElementById('analysis-floating');
 if(!panel)return null;
 if(!analysisDom||!panel.dataset.mounted){
  panel.dataset.mounted='1';
  panel.innerHTML=`
   <div class="analysis-floating-head">
    <div class="analysis-floating-title">
     <strong data-role="title"></strong>
     <span class="analysis-badge" data-role="badge"></span>
    </div>
    <button type="button" class="analysis-close" data-role="close" aria-label="Cerrar progreso">×</button>
   </div>
   <div class="analysis-bars">
    <div class="analysis-bar-activity" data-role="activity" aria-hidden="true"><i></i></div>
    <div class="analysis-bar-track" data-role="track" role="progressbar" aria-label="Avance de currículums" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
     <span class="analysis-bar-fill" data-role="fill"></span>
    </div>
   </div>
   <div class="analysis-counts" role="status" aria-live="polite" data-role="counts"></div>
   <div class="analysis-breakdown" data-role="breakdown"></div>
   <div class="analysis-file" data-role="file"></div>
   <div class="analysis-error" role="alert" data-role="error"></div>
   <div class="analysis-note" data-role="note"></div>
   <div class="analysis-actions" data-role="actions">
    <button type="button" class="text-link" data-role="resume">Retomar pendientes</button>
   </div>`;
  const q=r=>panel.querySelector(`[data-role="${r}"]`);
  analysisDom={panel,title:q('title'),badge:q('badge'),close:q('close'),activity:q('activity'),track:q('track'),fill:q('fill'),counts:q('counts'),breakdown:q('breakdown'),file:q('file'),error:q('error'),note:q('note'),actions:q('actions'),resume:q('resume')};
  analysisDom.close.addEventListener('click',()=>{
   if(analysisBatch.running)return;
   Object.assign(analysisBatch,{visible:false,phase:'idle',errorMessage:'',recoveryVacancies:[]});
   clearSavedProgress();
   paintAnalysisPanel();
  });
  analysisDom.resume.addEventListener('click',()=>{
   if(analysisBatch.running||state.uploading)return;
   const targetVacancy=analysisBatch.vacancy||String(analysisBatch.recoveryVacancies[0]?.vacante_id||'');
   if(!targetVacancy){toast('Selecciona una vacante para retomar pendientes.',true);return}
   runLocalAnalysis({vacancy:targetVacancy,resume:true});
  });
 }
 return analysisDom;
}
function paintAnalysisPanel(){
 const dom=ensureAnalysisDom();
 if(!dom)return;
 const shouldShow=analysisBatch.visible&&!!state.user&&!state.demo;
 dom.panel.hidden=!shouldShow;
 const uploadNote=document.getElementById('upload-progress');
 if(uploadNote){
  if(state.page==='upload'&&analysisBatch.running){
   uploadNote.innerHTML='<div class="notice blue analysis-inline-note">El análisis está en curso en el indicador superior derecho. Puedes cambiar de sección; mantén esta pestaña abierta hasta terminar.</div>';
  }else if(state.page==='upload'){uploadNote.innerHTML=''}
 }
 if(!shouldShow)return;
 const b=analysisBatch,remaining=Math.max(0,b.total-b.done);
 const pct=b.total>0?Math.min(100,Math.round((b.done/b.total)*100)):0;
 const hasErrors=b.phase==='interrupted'||b.errors>0;
 dom.panel.classList.toggle('is-error',hasErrors);
 const titles={preparing:'Preparando análisis…',running:'Analizando currículums',finished:'Análisis finalizado',interrupted:'Análisis interrumpido',recovery:'Pendientes por retomar'};
 dom.title.textContent=titles[b.phase]||'Progreso de análisis';
 dom.badge.textContent=b.phase==='recovery'?`${b.total} pend.`:b.total>0?`${pct}%`:'…';
 dom.close.hidden=b.running;
 dom.activity.hidden=!b.running;
 dom.track.hidden=b.total<=0;
 dom.track.setAttribute('aria-valuenow',String(pct));
 dom.fill.style.width=`${pct}%`;
 if(b.phase==='recovery'){
  dom.counts.textContent=`${b.total} currículum${b.total===1?'':'s'} guardado${b.total===1?'':'s'} sin completar`;
  dom.breakdown.textContent=b.recoveryVacancies.map(v=>`${v.titulo}: ${v.pendientes}`).join(' · ');
 }else if(b.total>0){
  dom.counts.textContent=`Total: ${b.total} · Procesados: ${b.done} · Pendientes: ${remaining}`;
  dom.breakdown.textContent=`Correctos: ${b.ok} · Revisión manual: ${b.manual} · Errores: ${b.errors}`;
 }else{
  dom.counts.textContent='Comprobando asistente y preparando archivos…';
  dom.breakdown.textContent='Correctos: 0 · Revisión manual: 0 · Errores: 0';
 }
 dom.file.hidden=!(b.running&&b.file);
 dom.file.textContent=b.file?`Archivo actual: ${b.file}`:'';
 dom.file.title=b.file||'';
 dom.error.hidden=!b.errorMessage;
 dom.error.textContent=b.errorMessage||'';
 dom.note.hidden=!b.running;
 dom.note.textContent=b.running?'Puedes cambiar de sección; mantén esta pestaña abierta hasta terminar.':'';
 const showResume=!b.running&&(b.phase==='interrupted'||b.errors>0||b.phase==='recovery')&&(b.vacancy||b.recoveryVacancies.length>0);
 dom.actions.hidden=!showResume;
 dom.resume.disabled=b.running||state.uploading;
}
function syncCountsAndPage(){
 const cc=document.getElementById('count-candidates'),cv=document.getElementById('count-vacancies');
 if(cc)cc.textContent=state.candidates.length;
 if(cv)cv.textContent=state.vacancies.length;
 const active=document.activeElement;
 const editing=active&&document.getElementById('page')?.contains(active)&&/^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
 if(!document.getElementById('modal')?.open&&!editing&&state.page!=='upload'){
  render();
 }else if(state.page==='upload'){
  document.querySelectorAll('#upload-form button, #upload-vacancy, #pdf-files').forEach(el=>{el.disabled=state.uploading});
 }
 paintAnalysisPanel();
}
async function checkSavedPending(force=false){
 if(!state.user||state.demo||!state.loaded)return;
 if(!force&&checkedPendingUser===state.user.id)return;
 if(pendingCheckPromise)return pendingCheckPromise;
 const uid=state.user.id;
 pendingCheckPromise=api('/pendientes-local/resumen').then(res=>{
  checkedPendingUser=uid;
  if(analysisBatch.running)return;
  if(res.total_pendientes>0){
   const first=res.vacantes[0];
   Object.assign(analysisBatch,{
    visible:true,running:false,phase:'recovery',
    vacancy:String(first.vacante_id),vacancyTitle:first.titulo,
    total:res.total_pendientes,done:0,ok:0,manual:0,
    errors:res.vacantes.reduce((s,v)=>s+(v.errores||0),0),
    file:'',errorMessage:'',recoveryVacancies:res.vacantes
   });
  }else{
   clearSavedProgress();
   if(analysisBatch.phase==='recovery'){
    Object.assign(analysisBatch,{visible:false,phase:'idle',recoveryVacancies:[],errorMessage:''});
   }
  }
  paintAnalysisPanel();
 }).catch(()=>{}).finally(()=>{pendingCheckPromise=null});
 return pendingCheckPromise;
}
window.talentiaAnalysis={
 resetOnLogout(){
  checkedPendingUser=null;
  clearSavedProgress();
  Object.assign(analysisBatch,{visible:false,running:false,phase:'idle',vacancy:'',vacancyTitle:'',total:0,done:0,ok:0,manual:0,errors:0,file:'',errorMessage:'',recoveryVacancies:[]});
  paintAnalysisPanel();
 },
 paint:paintAnalysisPanel
};
async function runLocalAnalysis({vacancy,files=[],resume=false}){
 if(isDemo()||state.uploading||analysisBatch.running)return;
 if(!vacancy){toast('Selecciona una vacante.',true);return}
 if(!resume&&!files.length){toast('Selecciona al menos un PDF.',true);return}
 const batchFiles=[...files];
 state.uploading=true;
 const prevPhase=analysisBatch.phase,prevVisible=analysisBatch.visible;
 const vacObj=state.vacancies.find(v=>String(v.id)===String(vacancy));
 try{
  const status=await bridge('/status');
  if(!status.ready)throw new Error('Prepara el modelo desde Configuración antes de analizar.');
  Object.assign(analysisBatch,{
   visible:true,running:true,phase:'preparing',vacancy:String(vacancy),vacancyTitle:vacObj?.titulo||'',
   total:resume?0:batchFiles.length,done:0,ok:0,manual:0,errors:0,file:'',errorMessage:'',recoveryVacancies:[]
  });
  paintAnalysisPanel();
  if(state.page==='upload')render();
  const data=new FormData();
  batchFiles.forEach(f=>data.append('archivos',f));
  const batch=resume?await api(`/vacantes/${vacancy}/pendientes-local`):await api(`/vacantes/${vacancy}/preparar-local`,{method:'POST',body:data});
  state.files=[];
  if(!batch.jobs.length){
   Object.assign(analysisBatch,{visible:false,running:false,phase:'idle',total:0,done:0,ok:0,manual:0,errors:0,file:'',errorMessage:'',recoveryVacancies:[]});
   clearSavedProgress();
   await refreshData();
   toast('No hay currículums pendientes por retomar en esta vacante.');
   return;
  }
  Object.assign(analysisBatch,{phase:'running',total:batch.jobs.length});
  paintAnalysisPanel();
  let interruptedByConnection=false;
  for(const job of batch.jobs){
   analysisBatch.file=job.archivo;
   paintAnalysisPanel();
   if(!job.legible){
    analysisBatch.done+=1;analysisBatch.manual+=1;
    paintAnalysisPanel();
    continue;
   }
   const start=performance.now();
   let body=null,connectionError='';
   try{
    const raw=await bridge('/chat',{messages:job.messages,format:job.format});
    const content=raw.message.content.replace(/<think>[\s\S]*?<\/think>/g,'').replace(/^```(?:json)?\s*|\s*```$/g,'').trim();
    body={evaluacion:JSON.parse(content),modelo:raw.model||'modelo local',latencia:(performance.now()-start)/1000,throughput:raw.eval_duration?raw.eval_count/(raw.eval_duration/1e9):0,tokens:Number(raw.eval_count||0)};
   }catch(error){
    const msg=error?.message||'Error del asistente local.';
    if(isConnectionErrorMessage(msg)){
     connectionError='Se perdió la conexión con el asistente local. Verifica que esté abierto y vuelve a intentar con Retomar pendientes.';
    }else{
     try{
      const check=await bridge('/status');
      if(!check.ready)connectionError='Se perdió la conexión con el modelo en LM Studio. Comprueba que siga cargado y usa Retomar pendientes.';
     }catch{
      connectionError='Se perdió la conexión con el asistente local o con LM Studio. Reconéctalo y usa Retomar pendientes.';
     }
    }
    if(!connectionError){
     body={error:msg.slice(0,1000),latencia:(performance.now()-start)/1000};
    }
   }
   if(connectionError){
    analysisBatch.errorMessage=connectionError;
    interruptedByConnection=true;
    break;
   }
   try{
    await api(`/candidatos/${job.id}/resultado-local`,{method:'POST',body});
   }catch(saveError){
    analysisBatch.errorMessage=`Se perdió la conexión con el servidor de TalentIA al guardar: ${saveError.message}`;
    interruptedByConnection=true;
    break;
   }
   analysisBatch.done+=1;
   if(body.error)analysisBatch.errors+=1;else analysisBatch.ok+=1;
   paintAnalysisPanel();
   try{await refreshData();syncCountsAndPage()}catch{}
  }
  analysisBatch.running=false;analysisBatch.file='';
  analysisBatch.phase=interruptedByConnection?'interrupted':'finished';
  try{await refreshData()}catch{}
  if(interruptedByConnection){
   toast(analysisBatch.errorMessage,true);
  }else{
   toast(`${analysisBatch.ok} evaluados · ${analysisBatch.manual} para revisión manual · ${analysisBatch.errors} errores`,analysisBatch.errors>0);
  }
 }catch(error){
  analysisBatch.running=false;analysisBatch.file='';
  if(analysisBatch.phase==='preparing'){
   analysisBatch.phase='interrupted';
   analysisBatch.errorMessage=error.message;
  }else{
   Object.assign(analysisBatch,{visible:prevVisible,phase:prevPhase});
   if(prevPhase==='recovery')analysisBatch.errorMessage=error.message;
  }
  toast(error.message,true);
 }finally{
  state.uploading=false;
  analysisBatch.running=false;
  if(state.page==='upload')render();else syncCountsAndPage();
  paintAnalysisPanel();
 }
}
let motorTimer;
async function motorStatus(){try{const s=await bridge('/status');const el=$('#motor-state');if(el)el.textContent=s.ready?'Motor listo: '+s.model+' · '+(s.provider||'Ollama')+'. No necesitas descargarlo nuevamente.':s.status+(s.total?' · '+Math.round(s.completed/s.total*100)+'%':'');if(s.running)motorTimer=setTimeout(motorStatus,2000)}catch(e){if($('#motor-state'))$('#motor-state').textContent=e.message}}

interviews=function(){
 const first=new Date(calendarMonth),start=new Date(first);start.setDate(1-((first.getDay()+6)%7));
 const filtered=selectedDay?state.interviews.filter(i=>eventKey(i)===selectedDay):state.interviews;
 return heading('Espacio para una buena conversación.','Organiza tus entrevistas y conoce a las personas detrás del perfil.','','CALENDARIO DE ENTREVISTAS')+
 `<div class="calendar-layout"><section class="panel"><div class="panel-header"><div class="flex"><button class="icon-btn" data-action="month-prev" aria-label="Mes anterior">${icon('left')}</button><h2 class="calendar-title">${calendarMonth.toLocaleDateString('es-EC',{month:'long',year:'numeric'})}</h2><button class="icon-btn" data-action="month-next" aria-label="Mes siguiente">${icon('arrow')}</button></div><button class="btn btn-secondary" data-action="month-today">Hoy</button></div><div class="calendar-scroll"><div class="calendar-grid">${['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'].map(d=>`<div class="calendar-weekday">${d}</div>`).join('')}${Array.from({length:42},(_,n)=>{const d=new Date(start);d.setDate(start.getDate()+n);const key=dateKey(d),events=state.interviews.filter(i=>eventKey(i)===key);return `<div class="calendar-day ${d.getMonth()!==first.getMonth()?'outside':''} ${selectedDay===key?'selected':''}"><button class="calendar-number ${key===dateKey(new Date())?'today':''}" data-action="calendar-day" data-date="${key}" aria-label="Ver entrevistas del ${key}">${d.getDate()}</button>${events.map(i=>`<button class="calendar-event" data-action="interview-detail" data-id="${i.id}" title="${esc(i.nombre_candidato)}">${time(i.fecha_hora)}<strong>${esc(i.nombre_candidato)}</strong></button>`).join('')}</div>`}).join('')}</div></div></section><aside class="panel setting-card"><div class="flex between"><h2>${selectedDay?'Agenda · '+selectedDay:'Agenda de entrevistas'}</h2><button class="text-link" data-action="all-days">Todas</button></div><p class="small muted">Horario de Ecuador · selecciona una fecha o una entrevista.</p>${filtered.length?filtered.sort((a,b)=>dateObj(a.fecha_hora)-dateObj(b.fecha_hora)).map(i=>`<button class="agenda-event" data-action="interview-detail" data-id="${i.id}"><strong>${esc(i.nombre_candidato)}</strong><span>${day(i.fecha_hora)} · ${time(i.fecha_hora)} · ${i.duracion_minutos} min</span><span>${esc(jobTitle(i.vacante_id))}</span><span class="pill ${i.modo==='simulacion'?'amber':'blue'}">${i.modo==='simulacion'?'Simulación':'Registrada'}</span></button>`).join(''):empty('No hay entrevistas','Selecciona otro día o abre un candidato para programar una conversación.','','calendar')}</aside></div>`;
};
function interviewDetail(id){const i=state.interviews.find(i=>i.id===Number(id));if(!i)return;const video=/video|meet|virtual/i.test(i.modalidad);showModal('Tu próxima conversación',`<div class="detail-hero">${avatar(i.nombre_candidato)}<div><h2>${esc(i.nombre_candidato)}</h2><p>${esc(jobTitle(i.vacante_id))}</p></div></div><p>${fullDate(i.fecha_hora)} · ${time(i.fecha_hora)} · ${i.duracion_minutos} minutos<br>${esc(i.modalidad)}</p>${video?`<div class="notice blue"><div><strong>${i.modo==='real'?'Google Meet / Calendar':'Enlace de videollamada · demostración'}</strong><p><a href="${demoMeet(i)}" target="_blank" rel="noopener noreferrer" style="text-decoration:underline">${demoMeet(i)}</a></p><small>${i.modo==='real'?'Abre el enlace de la entrevista.':'Es un enlace simulado; no crea una reunión real.'}</small></div></div>`:''}<div class="detail-actions"><button class="btn btn-primary" data-action="email-interview" data-id="${i.id}">${icon('mail')} Preparar correo</button><button class="btn btn-secondary" data-action="candidate" data-id="${i.candidato_id}">Ver perfil</button></div><p class="small muted section-gap">El correo se abre en tu aplicación de correo. Tú revisas y envías la invitación.</p>`)}
function invitationMail(id){const i=state.interviews.find(i=>i.id===Number(id)),c=state.candidates.find(c=>c.id===i.candidato_id);if(state.demo){toast('En la demostración no se envían correos.');return}if(!c?.email){toast('El candidato no tiene un correo válido.',true);return}const message=`Hola ${i.nombre_candidato},\n\nTe invitamos a una entrevista para ${jobTitle(i.vacante_id)} el ${fullDate(i.fecha_hora)} a las ${time(i.fecha_hora)} (Ecuador), de ${i.duracion_minutos} minutos.\nModalidad: ${i.modalidad}.\n${/video|meet|virtual/i.test(i.modalidad)?'Enlace de muestra (debe reemplazarse por una reunión real antes de enviar): '+demoMeet(i):''}\n\nSaludos,\nEquipo de selección`;location.href=`mailto:${encodeURIComponent(c.email)}?subject=${encodeURIComponent('Entrevista · '+jobTitle(i.vacante_id))}&body=${encodeURIComponent(message)}`}

bindPage=function(){previousBinding();if(state.page==='candidates'){const select=document.createElement('select');select.className='filter-input';select.setAttribute('aria-label','Ordenar por ajuste al perfil');select.innerHTML='<option value="best">Mayor ajuste primero</option><option value="least">Menor ajuste primero</option><option value="recent">Más recientes</option>';select.value=sortMode;$('.filters').append(select);select.onchange=()=>{sortMode=select.value;$('#candidate-results').innerHTML=candidateResults()}}if(state.page==='upload'){
 const old=$('#upload-form');const fresh=old.cloneNode(true);old.replaceWith(fresh);
 $('#upload-vacancy').onchange=e=>state.vacancy=e.target.value;$('#pdf-files').onchange=e=>{addFiles([...e.target.files]);e.target.value=''};
 const dz=$('#dropzone');dz.ondragover=e=>{e.preventDefault();dz.classList.add('dragover')};dz.ondragleave=()=>dz.classList.remove('dragover');dz.ondrop=e=>{e.preventDefault();dz.classList.remove('dragover');addFiles([...e.dataTransfer.files])};dz.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('#pdf-files').click()}};
 const warning=$('#page>.notice');if(warning)warning.remove();
 const note=fresh.querySelector('.notice');if(note)note.innerHTML=icon('shield')+'Los PDF se guardan en el servidor. El análisis con Qwen se ejecuta en tu computador. Conecta el asistente desde Configuración.';
 fresh.onsubmit=e=>{e.preventDefault();return runLocalAnalysis({vacancy:$('#upload-vacancy').value,files:state.files,resume:!!e.resume})};
 const resume=document.createElement('button');resume.type='button';resume.className='btn btn-secondary';resume.textContent='Retomar pendientes';resume.disabled=state.uploading||analysisBatch.running;resume.onclick=()=>{if(!$('#upload-vacancy').value){toast('Selecciona una vacante.',true);return}runLocalAnalysis({vacancy:$('#upload-vacancy').value,resume:true})};fresh.append(resume);
 }if(state.page==='settings'&&sessionStorage.getItem('talentia-companion'))motorStatus()};
document.addEventListener('click',async e=>{const b=e.target.closest('[data-action]');if(!b)return;try{switch(b.dataset.action){case 'workspace':showModal('Mi espacio de trabajo',`<p>Un espacio compartido para el equipo de selección.</p><div class="metric-row"><span>Vacantes</span><strong>${state.vacancies.length}</strong></div><div class="metric-row"><span>Candidatos</span><strong>${state.candidates.length}</strong></div><div class="detail-actions"><button class="btn btn-primary" data-action="workspace-settings">Configurar equipo y motor local</button><button class="btn btn-secondary" data-action="seed-jobs">Crear vacantes de prueba</button><button class="btn btn-secondary" data-action="workspace-home">Ir al resumen</button></div>`);break;case 'workspace-settings':closeModal();go('settings');break;case 'workspace-home':closeModal();go('dashboard');break;case 'month-prev':calendarMonth.setMonth(calendarMonth.getMonth()-1);render();break;case 'month-next':calendarMonth.setMonth(calendarMonth.getMonth()+1);render();break;case 'month-today':calendarMonth=new Date(new Date().getFullYear(),new Date().getMonth(),1);selectedDay=dateKey(new Date());render();break;case 'calendar-day':selectedDay=b.dataset.date;render();break;case 'all-days':selectedDay='';render();break;case 'interview-detail':interviewDetail(b.dataset.id);break;case 'email-interview':invitationMail(b.dataset.id);break;case 'connect-local':sessionStorage.setItem('talentia-companion',$('#companion-key').value.trim());await motorStatus();break;case 'pull-local':if(isDemo())return;await bridge('/pull',{});clearTimeout(motorTimer);await motorStatus();break;case 'candidate':if($('#modal').open)closeModal();break}}catch(error){toast(error.message,true)}});

const vacancyCardsBefore=vacancyCards;
vacancyCards=function(){let html=vacancyCardsBefore();return html.replace(/(<button class="btn btn-secondary" data-action="view-vacancy" data-id="(\d+)">)/g,(match,button,id)=>{const v=state.vacancies.find(v=>v.id===Number(id));return `<div class="flex between" style="margin-bottom:12px"><span class="pill ${v.activa===false?'gray':''}">${v.activa===false?'Completada':'Abierta'}</span><button class="text-link" data-action="toggle-vacancy" data-id="${id}">${v.activa===false?'Reabrir':'Marcar completada'}</button></div>`+button})};
document.addEventListener('click',async e=>{const b=e.target.closest('[data-action="toggle-vacancy"]');if(!b)return;const v=state.vacancies.find(v=>v.id===Number(b.dataset.id));try{if(state.demo){v.activa=v.activa===false;render();return}b.disabled=true;await api(`/vacantes/${v.id}/estado`,{method:'PATCH',body:{activa:v.activa===false}});await refreshData();render();toast('Estado de la vacante actualizado. Se conserva su historial.')}catch(error){toast(error.message,true);b.disabled=false}});

document.addEventListener('click',async e=>{const b=e.target.closest('[data-action="seed-jobs"]');if(!b)return;if(isDemo())return;b.disabled=true;try{for(const v of [{titulo:'Analista de datos junior',descripcion:'Preparación y consulta de datos para reportes.',habilidades_obligatorias:['SQL','Excel'],anios_experiencia_min:0},{titulo:'Soporte técnico',descripcion:'Diagnóstico de hardware, redes y sistemas.',habilidades_obligatorias:['Hardware','Redes','Linux'],anios_experiencia_min:1},{titulo:'Desarrollador Java junior',descripcion:'Desarrollo y mantenimiento de aplicaciones.',habilidades_obligatorias:['Java','SQL'],anios_experiencia_min:0}]){if(!state.vacancies.some(x=>x.titulo===v.titulo))await api('/vacantes',{method:'POST',body:v})}await refreshData();closeModal();go('vacancies');toast('Vacantes de prueba listas. Puedes usar el mismo CV en cada una.')}catch(error){toast(error.message,true)}finally{b.disabled=false}});

const settingsWithMotor=settingsPage;
settingsPage=function(){return settingsWithMotor()+`<section class="panel setting-card section-gap"><h2>Mi cuenta de Google</h2><p>Conecta tu propia cuenta para crear entrevistas en tu Calendar y enviar invitaciones desde tu Gmail. Cada reclutador tiene su conexión independiente.</p><div id="google-state" role="status">Comprobando conexión…</div><div class="detail-actions"><button class="btn btn-primary" data-action="google-connect">Conectar mi Google</button><button class="btn btn-secondary" data-action="google-disconnect">Desconectar mi Google</button></div><p class="small muted">Los envíos requieren confirmar la entrevista. Desconectar elimina el acceso guardado en esta instalación; también puedes revocarlo desde tu cuenta de Google.</p></section>`};
const bindWithMotor=bindPage;
bindPage=function(){bindWithMotor();if(state.page==='settings'&&!state.demo)api('/google/status').then(s=>{const el=$('#google-state');if(el)el.textContent=s.modo==='real'?'Tu cuenta está conectada.':s.configurado?'Sin cuenta conectada.':'El administrador debe configurar Google primero. Consulta GOOGLE.md.'}).catch(e=>{if($('#google-state'))$('#google-state').textContent=e.message})};
document.addEventListener('click',async e=>{const button=e.target.closest('[data-action]');if(!button||!['google-connect','google-disconnect'].includes(button.dataset.action)||isDemo())return;button.disabled=true;try{if(button.dataset.action==='google-connect'){const r=await api('/google/connect',{method:'POST'});location.assign(r.url)}else{await api('/google/disconnect',{method:'POST'});await refreshData();render();toast('Tu cuenta de Google fue desconectada.')}}catch(error){toast(error.message,true);button.disabled=false}});

const bindBeforeProgress=bindPage;
bindPage=function(){bindBeforeProgress();paintAnalysisPanel();checkSavedPending()};
