import {Viewer} from './viewer.js';
const $=id=>document.getElementById(id);let token=location.hash.slice(1)||sessionStorage.getItem('garage-token')||'';if(token)sessionStorage.setItem('garage-token',token);history.replaceState(null,'',location.pathname);let project=null,selected=null,currentJob=null,state=null;let planEpoch=0,approvedPlan=null,targetCatalogue=null;
let navigationEpoch=0,activeUiJob=null,jobBarrier=Promise.resolve(),preparationPending=null,previewUnverifiedContext=null,previewLoadingContext=null;
let activePage="workshop",tyreProposal=null,tyreProposalEpoch=0,tyreProposalPending=null;
let bodyFitViewer=null,bodyFitReview=null,bodyFitEpoch=0,bodyFitPending=null,bodyFitPreviewDigest=null;
let shellViewer=null,shellReview=null,shellEpoch=0,shellPending=null,shellPreviewDigest=null,shellPreviewContext=null,shellViewKind='side',shellDragging=null;
let shellDispositionContext=null,shellDispositionSignature=null,shellDispositionDirty=false,shellDispositionRows=new Map();
let setupProposal=null;
const cornerNames={FL:'Front left',FR:'Front right',RL:'Rear left',RR:'Rear right'};
let tyrePickerSource=null;
const bodyFitPolicies={keep_proportions:"Keep original body size and shape",adapt_body:"Adapt body to template"};
// SETTINGS_COORDINATOR_BEGIN
class SettingsCoordinator{
 constructor(send,publish){this.send=send;this.publish=publish;this.context=0;this.pid=null;this.saved=null;this.drafts=new Map();this.version=0;this.tail=Promise.resolve()}
 bind(value){this.context++;this.pid=value.id;this.saved=value;this.drafts.clear();this.tail=this.tail.catch(()=>{});return this.snapshot()}
 snapshot(){if(!this.saved)return null;const value={...this.saved};for(const [key,draft]of this.drafts)value[key]=draft.value;return value}
 hasDraft(key){return this.drafts.has(key)}
 stage(changes){let changed=false;for(const [key,value]of Object.entries(changes)){const previous=this.drafts.has(key)?this.drafts.get(key).value:this.saved?.[key];if(JSON.stringify(previous)===JSON.stringify(value))continue;this.drafts.set(key,{value:JSON.parse(JSON.stringify(value)),version:++this.version,queued:0});changed=true}return changed}
 save(){
  const context=this.context,pid=this.pid,batch=[...this.drafts].filter(([,draft])=>draft.queued!==draft.version).map(([key,draft])=>({key,value:draft.value,version:draft.version}));
  if(!batch.length)return this.tail;
  for(const item of batch)this.drafts.get(item.key).queued=item.version;
  const changes=Object.fromEntries(batch.map(item=>[item.key,item.value]));
  const request=this.tail.catch(()=>{}).then(async()=>{
   const value=await this.send(pid,changes);
   if(this.context!==context||this.pid!==pid)return value;
   if(value?.id!==pid)throw Error('Saved settings returned a different project. Reopen this project before continuing.');
   this.saved=value;
   for(const item of batch){const draft=this.drafts.get(item.key);if(draft?.version===item.version)this.drafts.delete(item.key)}
   await this.publish(this.snapshot(),changes,context);return value;
  }).catch(error=>{
   if(this.context===context)for(const item of batch){const draft=this.drafts.get(item.key);if(draft?.version===item.version&&draft.queued===item.version)draft.queued=0}
   throw error;
  });
  this.tail=request.catch(()=>{});return request;
 }
 async flush(context=this.context){
  if(context!==this.context)throw Error('The open project changed. Review the current project before continuing.');
  while(this.drafts.size){await this.save();if(context!==this.context)throw Error('The open project changed. Review the current project before continuing.')}
  await this.tail;
  if(context!==this.context)throw Error('The open project changed. Review the current project before continuing.');
  return this.snapshot();
 }
 async mutate(send,publicationChanges={}){
  const context=this.context,pid=this.pid;await this.flush(context);
  const request=this.tail.catch(()=>{}).then(async()=>{const value=await send(pid);if(this.context===context&&this.pid===pid){if(value?.id!==pid)throw Error('The operation returned a different project.');this.saved=value;await this.publish(this.snapshot(),publicationChanges,context)}return value});
  this.tail=request.catch(()=>{});return request;
 }
}
// SETTINGS_COORDINATOR_END
const editState=new SettingsCoordinator(async(id,changes)=>{await jobBarrier;return api('/api/settings',{id,changes})},async(value,changes,context)=>{
 if(editState.context!==context)return;
 const coordinatesChanged=project?.source_up_axis!==value.source_up_axis||project?.metres_per_unit!==value.metres_per_unit;project=value;clearTyreProposal();clearBodyFitReview();
 if(coordinatesChanged||'source_up_axis' in changes||'metres_per_unit' in changes||changes.refresh_previews){
  previewUnverifiedContext=context;previewLoadingContext=context;$('confirmParts').disabled=true;renderImportGuide();
  let raw;try{raw=await api('/preview/'+value.id+(viewer.gl?'':'?quality=software'),undefined,{binary:true})}finally{if(previewLoadingContext===context)previewLoadingContext=null;if(editState.context===context)renderImportGuide()}
  if(editState.context!==context||project?.id!==value.id)return;
  viewer.load(raw,project.inspection.parts);viewer.isolated=null;$('isolate').textContent='Isolate selected part';
  if(selected&&!project.inspection.parts.some(part=>part.id===selected)){
   const result=value.tyre_discovery_result?.source_part===changes.partition_source_part?value.tyre_discovery_result:value.partition_result;
   const children=result?.source_part===changes.partition_source_part?result.children:[];
   selected=selected===changes.partition_source_part?(children||[]).find(id=>project.inspection.parts.some(part=>part.id===id))||null:null;
  }
  if(!editState.hasDraft('source_up_axis')&&!editState.hasDraft('metres_per_unit'))previewUnverifiedContext=null;
 }
 if(editState.context===context)renderProject();
});
function syncControl(id,value,key,property='value',force=false){const control=$(id),context=String(editState.context);if(force||control.dataset.projectContext!==context){control.dataset.projectContext=context;control[property]=value;return}if((property==='value'&&document.activeElement===control)||editState.hasDraft(key))return;control[property]=value}
function stageSettings(changes){if(!project)return false;const changed=editState.stage(changes);project=editState.snapshot();if(changed){clearTyreProposal();clearBodyFitReview();invalidateCarPlan('Settings changed. Check the build plan again.',true)}renderImportGuide();return changed}
async function flushSettings(context=editState.context){if(!project)return;const saving=editState.flush(context);renderImportGuide();try{project=await saving;return project}finally{if(editState.context===context)renderImportGuide()}}
function reserveUiJob(id=null){if(activeUiJob)throw Error('Another operation is running. Wait for it or cancel it.');let release;jobBarrier=new Promise(resolve=>{release=resolve});activeUiJob={id,release};renderImportGuide();return activeUiJob}
function releaseUiJob(owner){if(activeUiJob!==owner)return;activeUiJob=null;owner.release();renderImportGuide()}
async function submitJob(url,data,opts){const owner=reserveUiJob();try{const job=await api(url,data,opts);if(!job?.id)throw Error('The operation did not return a job to watch.');owner.id=job.id;return job}catch(error){releaseUiJob(owner);throw error}}
async function configure(action){clearBodyFitReview();renderBodyFitControls();await flushSettings();await jobBarrier;const owner=reserveUiJob('configuration');try{return await action()}finally{releaseUiJob(owner)}}
async function openProject(id){const opening=++navigationEpoch;await flushSettings();if(opening!==navigationEpoch)return;const value=await api('/api/project/'+id);if(opening!==navigationEpoch)return;return loadProject(value,opening)}
async function pickModel(){await flushSettings();await jobBarrier;const owner=reserveUiJob('model-picker');let result;try{result=await api('/api/pick',{kind:'model'})}catch(error){releaseUiJob(owner);throw error}if(!result.id){releaseUiJob(owner);return result}owner.id=result.id;return waitJob(result,loadProject)}
function invalidateCarPlan(message='Check the build plan for the current project.',clearResult=false){planEpoch++;approvedPlan=null;$('buildCar').disabled=true;$('carPlan').textContent=message;$('carPlan').className='carPlan blocked';if(clearResult)$('exportResult').classList.add('hidden');renderImportGuide()}
const roleLabels={body:'Body detail',paint:'Paint',glass:'Glass',headlight:'Headlight',taillight:'Taillight',brakelight:'Brake light',rim:'Rim',tyre:'Tyre',valve:'Valve',brake_rotor:'Brake rotor (spins)',brake_caliper:'Brake caliper (does not spin)',interior:'Interior',ignore:'Ignore'};
const cornerRoles=['rim','tyre','valve','brake_rotor','brake_caliper'],wheelCorners=['FL','FR','RL','RR'];
const viewer=new Viewer($('viewer'),selectPart);for(const [value,label]of Object.entries(roleLabels)){let o=document.createElement('option');o.value=value;o.textContent=label;$('partRole').append(o)}
function notice(text,success=false){$('notice').textContent=text;$('notice').className='notice'+(success?' success':'');if(success)setTimeout(()=>$('notice').classList.add('hidden'),7000)}
async function api(url,data,opts={}){let headers={'X-Garage-Token':token,...opts.headers};let method=opts.method||((data!==undefined)?'POST':'GET');let body;if(data!==undefined){headers['Content-Type']='application/json';body=JSON.stringify(data)}if(opts.body){body=opts.body;delete headers['Content-Type'];headers['Content-Type']='application/octet-stream'}let r=await fetch(url,{method,headers,body});if(!r.ok){let j=await r.json().catch(()=>({error:r.statusText}));throw Error(j.error||'Request failed')}if(opts.binary)return r.arrayBuffer();return r.json()}
async function safe(fn){try{return await fn()}catch(e){notice(e.message);console.error(e)}}
function page(name){activePage=name;renderImportGuide();for(const e of document.querySelectorAll('.page'))e.classList.toggle('hidden',e.id!=='page-'+name);for(const n of document.querySelectorAll('.nav'))n.classList.toggle('active',n.dataset.page===name);$('pageTitle').textContent={workshop:'Car workshop',projects:'My projects',export:'Build & export',help:'How it works'}[name];if(name==='projects')refreshProjects();if(name==='workshop')viewer.invalidate();if(name==='export'){bodyFitViewer?.invalidate();shellViewer?.invalidate()}}
for(const n of document.querySelectorAll('.nav[data-page]'))n.onclick=()=>page(n.dataset.page);
async function refresh(){const next=await api('/api/state');if(state&&['car_baseline','car_packer','native_template','package_folder'].some(key=>state[key]!==next[key]))clearBodyFitReview();state=next;$('version').textContent='Desktop preview '+state.version;$('footerVersion').textContent=state.version;$('packagePath').textContent=state.package_folder||'No package selected';$('carBaselinePath').textContent=state.car_baseline||'Not configured';$('carPackerPath').textContent=state.car_packer||'Not configured';renderImportGuide();return state}
async function refreshProjects(){await refresh();$('projectGrid').replaceChildren();if(!state.projects.length){let e=document.createElement('p');e.textContent='Import your first model to create a project.';$('projectGrid').append(e)}for(const p of state.projects){let card=document.createElement('article');card.className='projectCard';let tag=document.createElement('span');tag.className='pill green';tag.textContent='SAVED LOCALLY';let h=document.createElement('h2');h.textContent=p.name;let info=document.createElement('p');info.textContent=p.source_filename+' · '+new Date(p.created*1000).toLocaleDateString();let btn=document.createElement('button');btn.className='secondary';btn.textContent='Open project →';btn.onclick=()=>safe(()=>openProject(p.id));card.append(tag,h,info,btn);$('projectGrid').append(card)}}
async function loadProject(p,opening=++navigationEpoch){await flushSettings();if(opening!==navigationEpoch)return;if(project?.id===p.id){p=await api('/api/project/'+p.id);if(opening!==navigationEpoch)return}invalidateCarPlan('Check the build plan for this newly opened project.',true);project=editState.bind(p);clearTyreProposal();clearBodyFitReview();const context=editState.context;selected=null;page('workshop');previewUnverifiedContext=context;previewLoadingContext=context;$('confirmParts').disabled=true;renderImportGuide();let raw;try{raw=await api('/preview/'+p.id+(viewer.gl?'':'?quality=software'),undefined,{binary:true})}finally{if(previewLoadingContext===context)previewLoadingContext=null;if(editState.context===context)renderImportGuide()}if(opening!==navigationEpoch||editState.context!==context)return;viewer.load(raw,p.inspection.parts);previewUnverifiedContext=null;$('empty').classList.add('hidden');for(const id of ['previewLabel','previewMode','viewControls','viewHint','stats'])$(id).classList.remove('hidden');$('previewMode').textContent=viewer.mode;renderProject();notice('Imported '+p.part_count+' parts. Your source is preserved; review the wheel groups and front direction.',true)}
function renderProject(){if(!project)return;let p=project,i=p.inspection;$('modelName').textContent=p.name;$('modelMeta').textContent='Local source · '+p.source_filename+' · '+Object.values(i.role_counts).reduce((a,b)=>a+b,0)+' parts';$('statParts').textContent=p.part_count;$('statTriangles').textContent=p.triangles.toLocaleString();$('statWheels').textContent=new Set(i.parts.filter(x=>x.role==='rim'&&['FL','FR','RL','RR'].includes(x.corner)).map(x=>x.corner)).size+' / 4';$('statWheelbase').textContent=i.wheelbase?.toFixed(3)||'—';syncControl('sourceUp',p.source_up_axis||'+Y','source_up_axis');syncControl('sourceUnits',p.metres_per_unit??1,'metres_per_unit');syncControl('front',p.forward,'forward');$('front').disabled=false;syncControl('wheelbase',p.wheelbase_metres?.toFixed(3)||'','wheelbase_metres');$('wheelbase').disabled=false;for(const id of ['saveProject','confirmParts','goExport','carName','installMode','sourceUp','sourceUnits','calibrateUnits'])$(id).disabled=false;$('confirmParts').disabled=preparationPending?.context===editState.context||previewUnverifiedContext===editState.context;for(const id of ['sourceUp','sourceUnits','front','wheelbase','calibrateUnits'])$(id).disabled=preparationPending?.context===editState.context;renderInstallation();syncControl('confirmParts',p.approved_roles,'approved_roles','checked');syncControl('confirmInheritance',!!p.template_inheritance_approved,'template_inheritance_approved','checked');$('knownCarRecipe').disabled=!p.known_recipe_available||preparationPending?.context===editState.context;$('reviewStep').classList.add('current');$('exportStep').classList.toggle('current',p.approved_roles);$('wheelCards').replaceChildren();for(const k of ['FL','FR','RL','RR']){let e=document.createElement('div');e.textContent=k;let b=document.createElement('b');b.textContent=i.parts.some(x=>x.role==='rim'&&x.corner===k)?'Rim found':'Review';e.append(b);$('wheelCards').append(e)}$('checks').replaceChildren();let good=document.createElement('div');good.className='checkItem good';good.textContent='✓ Source read without external resources';$('checks').append(good);let normals=i.parts.filter(x=>x.warnings.some(s=>s.includes('normals'))).length;if(normals){let e=document.createElement('div');e.className='checkItem good';e.textContent='✓ Normals repaired on '+normals+' parts';$('checks').append(e)}for(const message of p.source_warnings||[]){let e=document.createElement('div');e.className='checkItem';e.textContent=message;$('checks').append(e)}for(const issue of i.issues){if(isTemplateShell()&&(issue.code==='four_tyres'||issue.code?.startsWith('rim_')||issue.code==='mechanical_corners'||(shellOptions().lighting.mode==='supplied'&&issue.code==='rear_running_lamp')))continue;let e=document.createElement('div');e.className='checkItem '+(issue.level==='action'?'action':'');e.textContent=(issue.level==='action'?'! ':'ℹ ')+issue.message;$('checks').append(e)}let reminder=document.createElement('div');reminder.className='checkItem';reminder.textContent='Preview uses simplified materials. It does not prove GT6 compatibility.';$('checks').append(reminder);if(previewUnverifiedContext!==editState.context)viewer.setParts(i.parts);$('previewLabel').textContent=previewUnverifiedContext===editState.context?'PREVIEW NEEDS REFRESH · REOPEN THIS PROJECT':'SOURCE PREVIEW · SIMPLIFIED MATERIALS';if(selected&&i.parts.some(part=>part.id===selected))selectPart(selected);else clearSelectedPart();renderParts();renderShellComponentChoices();renderImportGuide()}
function renderParts(){if(!project)return;let q=$('partSearch').value.toLowerCase(),f=$('roleFilter').value;let parts=project.inspection.parts.filter(p=>(!q||(p.name+' '+p.material_name+' '+p.role+' '+p.id).toLowerCase().includes(q))&&(f==='all'||(f==='wheels'?cornerRoles.includes(p.role):p.role===f)));$('partList').replaceChildren();for(const p of parts){let e=document.createElement('div');e.className='partRow'+(selected===p.id?' selected':'');e.tabIndex=0;let desc=document.createElement('div');let name=document.createElement('strong');name.textContent=p.name;let mat=document.createElement('small');mat.textContent=p.material_name+' · '+p.triangles.toLocaleString()+' triangles';desc.append(name,mat);let role=document.createElement('span');role.textContent=roleLabels[p.role];let corner=document.createElement('span');corner.textContent=p.corner||'—';let conf=document.createElement('span');conf.className='confidence';conf.textContent=Math.round(p.confidence*100)+'%';conf.title=p.reason;e.append(desc,role,corner,conf);e.onclick=()=>selectPart(p.id);e.onkeydown=v=>{if(v.key==='Enter')selectPart(p.id)};$('partList').append(e)}}
function nextImportStep(){
 if(!project)return null;
 const step=(code,title,detail,action,button,extra={})=>({code,title,detail,action,button,...extra});
 if(preparationPending?.context===editState.context||previewLoadingContext===editState.context)return step('preparing','Updating the prepared model','Wait for the new source preview before reviewing or approving it.',null,'Updating…');
 if(editState.drafts.size){
  const saving=[...editState.drafts.values()].some(draft=>draft.queued===draft.version);
  return step('saving',saving?'Saving your changes…':'Save your changes to update this guide','Advice refreshes from the saved project; the previous source check is paused.',saving?null:'save_changes',saving?'Saving…':'Save changes');
 }
 if(previewUnverifiedContext===editState.context)return step('preview','Refresh the prepared preview','Reopen this project to show the saved geometry, then review its parts again.','refresh_project','Refresh preview');
 if(activeUiJob)return step('working','An operation is running','Your model stays available. This guide resumes when the current operation finishes.',null,'Working…');
 if(isTemplateShell())return shellGuidance(step);
 const r=project.import_readiness;
 if(r?.schema_version!==1||r.scope!=='source_preflight'||!Array.isArray(r.user_tasks)||!Array.isArray(r.capability_limits))return step('assessment','Refresh this model’s source check','Open the saved project again to get its current source guidance.','refresh_project','Refresh source check');
 const tasks=r.user_tasks,limits=r.capability_limits;
 const next=tasks.find(task=>task.code!=='confirm_source_review');
 if(next)return {...next,button:guideActionLabel(next.action)};
 if(limits.length)return step('unsupported','This model needs more native support','Your source and assignments can be saved now. The limitations below must be resolved before this profile can build it.','save_project','Save project');
 if(tasks.length)return {...tasks[0],button:guideActionLabel(tasks[0].action)};
 const choice=project.installation||{};
 if(!['new_entry','replace'].includes(choice.mode))return step('destination','Choose where this car should go','Add a new car entry or choose an existing car to replace. Garage will not select a replacement for you.','choose_destination','Choose destination');
 if(bodyFitNeedsReview())return step('body_fit','Review how the body fits the template','Choose a body policy, inspect its preview and approve the measured fit. Custom rims retain their separate conversion rules.','review_body_fit','Review body fit');
 if(!state?.car_baseline)return step('baseline','Choose your owned starting package','Use your owned base package, or your previous combined build to retain cars you already added.','choose_baseline','Choose starting package');
 if(!state?.car_packer)return step('packer','Set up the native packer once','Garage checks the selected packer before using it. Builds run in a separate output folder.','choose_packer','Choose native packer');
 if(choice.mode==='replace'&&(choice.replace_car_id===null||choice.replace_car_id===undefined))return step('target','Choose the exact car to replace','Load the existing cars, then select the car whose identity and driving setup you want to keep.','choose_target','Choose replacement car');
 if(choice.mode==='replace'){
  const target=targetCatalogue?.cars.find(car=>car.car_id===choice.replace_car_id);
  if(target&&!target.replacement_compatible)return step('target_support','This replacement needs another vehicle template',target.compatibility_blockers.join(' '),'choose_target','Review replacement');
 }
 if(r.build_review_tasks?.length)return {...r.build_review_tasks[0],button:guideActionLabel(r.build_review_tasks[0].action)};
 if(approvedPlan?.pid===project.id&&approvedPlan.epoch===planEpoch&&approvedPlan.settingsVersion===editState.version)return step('plan_checked','The full plan permits a test build','Review the checked destination and inherited behavior before building. Appearance and gameplay still need testing.','show_build','Review checked build');
 return step('full_plan','Check the complete build plan','The source review is complete. The full plan still checks the vehicle template, exact destination and package tools.','check_plan','Check build plan');
}
function guideActionLabel(action){return {review_parts:'Review components',review_wheels:'Review wheels',review_paint:'Review paint',review_scale:'Check size and units',confirm_parts:'Review and confirm',review_inheritance:'Review template behavior',review_body_fit:'Review body fit',save_project:'Save project'}[action]||'Review source'}
function renderImportGuide(){
 renderBodyFitControls();renderTemplateShellControls();
 const visible=!!project&&['workshop','export'].includes(activePage);$('importGuide').classList.toggle('hidden',!visible);if(!visible)return;
 const step=nextImportStep(),context=editState.context,pid=project.id,version=editState.version;
 $('guideTitle').textContent=step.title;$('guideDetail').textContent=step.detail;$('guideAction').textContent=step.button;$('guideAction').disabled=!step.action;
 $('guideAction').dataset.action=step.action||'';$('guideAction').dataset.projectId=pid;
 if(step.action==='check_wheels')$('openSetup').classList.add('hidden');
 $('guideAction').onclick=()=>safe(()=>runGuideAction(step,context,pid,version));
 const current=!['preparing','saving','preview','working','assessment'].includes(step.code),r=current?project.import_readiness:null;
 const tasks=isTemplateShell()?[]:r?.user_tasks||[],limits=isTemplateShell()?(shellReviewIsCurrent()?shellReview.blockers.map(item=>({title:typeof item==='string'?item:item.message||item.code,detail:typeof item==='string'?item:item.message||item.code})):[]):r?.capability_limits||[];
 $('guideCapability').classList.toggle('hidden',!limits.length);
 $('guideCapabilityTitle').textContent=limits.length?'Native building is currently blocked for this model':'';
 $('guideCapabilityDetail').textContent=limits.map(item=>item.title).join(' · ');
 $('guideDetails').classList.toggle('hidden',!current);
 $('guideSummary').textContent=tasks.length+' source review '+(tasks.length===1?'task':'tasks')+' · '+limits.length+' native '+(limits.length===1?'limitation':'limitations');
 $('guideTasks').replaceChildren();$('guideLimits').replaceChildren();$('guideLimitSection').classList.toggle('hidden',!limits.length);
 for(const [rows,id,buttons]of [[tasks,'guideTasks',true],[limits,'guideLimits',false]]){
  for(const row of rows){const item=document.createElement('li'),title=document.createElement('strong'),detail=document.createElement('span');title.textContent=row.title;detail.textContent=row.detail;item.append(title,detail);if(buttons){const action=document.createElement('button');action.className='textButton';action.textContent=guideActionLabel(row.action);action.onclick=()=>safe(()=>runGuideAction(row,context,pid,version));item.append(action)}$(id).append(item)}
 }
 if(current&&!tasks.length){const item=document.createElement('li');item.textContent='The source checks have no outstanding review tasks. Full native build and runtime checks are separate.';$('guideTasks').append(item)}
 $('guideScope').textContent=isTemplateShell()?'Assembly review is for development evaluation. The complete plan checks owned game files and destination; game appearance and behavior remain unverified.':'Source checks only. The full build plan still checks your game files and destination. In-game appearance is unverified.';
}
async function runGuideAction(step,context,pid,version){
 if(!project||project.id!==pid||editState.context!==context||editState.version!==version)return;
 if(preparationPending?.context===context||activeUiJob)return;
 if(step.action==='save_changes'){await flushSettings(context);renderImportGuide();return}
 if(editState.drafts.size)return;
 if(step.action==='refresh_project'){await openProject(pid);return}
 if(previewUnverifiedContext===context)return;
 const focus=id=>{$(id).scrollIntoView({block:'center',behavior:'smooth'});$(id).focus?.()};
 if(step.action==='check_wheels')return $('openSetup').onclick();
 if(step.action==='save_shell_components'){page('export');return saveShellComponentChoices()}
 if(step.action==='review_shell_parts'){page('export');$('shellComponents').open=true;focus('shellComponentList');return}
 if(step.action==='review_shell'){page('export');focus('reviewTemplateShell');return}
 if(['review_parts','review_wheels','review_paint'].includes(step.action)){
  page('workshop');$('partSearch').value='';$('roleFilter').value=step.action==='review_wheels'&&project.inspection.parts.some(part=>cornerRoles.includes(part.role))?'wheels':'all';
  const id=(step.part_ids||[]).find(value=>project.inspection.parts.some(part=>part.id===value));if(id)selectPart(id);else clearSelectedPart();renderParts();focus(id?'partRole':'partList');return;
 }
 if(step.action==='review_scale'){page('workshop');focus('sourceUnits');return}
 if(step.action==='confirm_parts'){page('workshop');focus('confirmParts');return}
 if(step.action==='save_project'){return $('saveProject').onclick()}
 page('export');
 if(step.action==='choose_destination'){focus('installMode');return}
 if(step.action==='review_body_fit'){focus('bodyFitPolicy');return}
 if(step.action==='choose_target'){focus(targetCatalogue?'replaceTarget':'loadCarTargets');return}
 if(step.action==='choose_baseline'||step.action==='choose_packer'){$('gameSetup').open=true;focus(step.action==='choose_baseline'?'pickCarBaseline':'pickCarPacker');return}
 if(step.action==='review_inheritance'){focus('confirmInheritance');return}
 if(step.action==='show_build'){focus('carPlan');return}
 if(step.action==='check_plan')return $('reviewCarPlan').onclick();
}
function clearBodyFitReview(){
 clearTemplateShellReview();
 bodyFitEpoch++;bodyFitReview=null;bodyFitPreviewDigest=null;
 $('bodyFitReview').classList.add('hidden');$('bodyFitStage').classList.add('hidden');$('bodyFitViews').classList.add('hidden');
 $('confirmBodyFit').checked=false;$('confirmBodyFit').disabled=true;$('approveBodyFit').disabled=true;
}
function bodyFitSelectionChanged(){
 const policy=$('bodyFitPolicy').value,status=project?.body_fit_status;
 return !!(!isTemplateShell()&&bodyFitPolicies[policy]&&status?.approved&&policy!==status.policy);
}
function bodyFitNeedsReview(){
 return !!project&&!isTemplateShell()&&(bodyFitSelectionChanged()||(project.body_fit_status&&!project.body_fit_status.approved)||project.import_readiness?.build_review_tasks?.some(task=>task.action==='review_body_fit'));
}
function bodyFitReviewIsCurrent(review=bodyFitReview){
 return !!(review&&project&&review.id===project.id&&review.context===editState.context&&review.settingsVersion===editState.version&&review.epoch===bodyFitEpoch&&review.policy===$('bodyFitPolicy').value&&!editState.drafts.size);
}
function renderBodyFitControls(){
 const context=editState.context,status=project?.body_fit_status,control=$('bodyFitPolicy');
 if(project&&control.dataset.projectContext!==String(context)){
  clearBodyFitReview();control.dataset.projectContext=String(context);control.value=bodyFitPolicies[status?.policy]?status.policy:'';
 }
 if(bodyFitReview&&!bodyFitReviewIsCurrent())clearBodyFitReview();
 const pending=bodyFitPending?.context===context,busy=!!(pending||activeUiJob||preparationPending?.context===context||previewLoadingContext===context);
 const sourceReady=!!project&&previewUnverifiedContext!==context,drafts=editState.drafts.size>0;
 const current=bodyFitReviewIsCurrent(),loaded=current&&bodyFitPreviewDigest===bodyFitReview.review_digest&&!!bodyFitPreviewDigest;
 const approvable=loaded&&bodyFitReview.available===true&&bodyFitReview.within_profile_limits===true;
 control.disabled=!project||busy;$('reviewBodyFit').disabled=!sourceReady||busy||drafts||!bodyFitPolicies[control.value];
 $('reviewBodyFit').textContent=pending&&bodyFitPending.kind==='review'?'Preparing body preview…':'Preview body fit';
 $('confirmBodyFit').disabled=!approvable||busy||drafts;
 $('approveBodyFit').disabled=!approvable||busy||drafts||!$('confirmBodyFit').checked;
 for(const id of ['bodyFitView3d','bodyFitViewSide','bodyFitViewTop'])$(id).disabled=!loaded||busy;
 let message='Open a model and choose a body policy to review its fit.';
 if(project){
  if(pending)message=bodyFitPending.kind==='approval'?'Saving the reviewed body fit…':'Measuring the body and loading its matching preview…';
  else if(drafts)message='Save your changes, then request a fresh body-fit preview.';
  else if(!sourceReady)message='Refresh the source preview before reviewing its body fit.';
  else if(bodyFitSelectionChanged())message='This policy differs from the saved fit. Preview and approve it before building.';
  else if(status?.approved)message='Approved body fit: '+(bodyFitPolicies[status.policy]||'Saved policy')+'. The native body builder will use this saved decision.';
  else if(current)message=approvable?'Review the preview and measurements, then confirm this body fit.':'This proposal cannot be approved. Review the explanation below.';
  else message='Choose a body policy and preview it. Changing the choice does not approve or modify the model.';
 }
 $('bodyFitStatus').textContent=message;$('bodyFitStatus').classList.toggle('approved',!!status?.approved&&!drafts&&!pending&&!bodyFitSelectionChanged());
}
function validateBodyFitReview(value,pid,policy){
 const vector=v=>Array.isArray(v)&&v.length===3&&v.every(Number.isFinite);
 if(value?.schema!==1||value.scope!=='static_body_fit_review'||value.id!==pid||value.policy!==policy||typeof value.available!=='boolean'||typeof value.within_profile_limits!=='boolean'||value.physics_changed!==false||value.source_geometry_changed!==false||value.runtime_verified!==false||value.source?.source_sha256!==project.source_sha256)throw Error('The body-fit review does not match this saved project. Request it again.');
 if(value.available&&(!/^[a-f0-9]{64}$/.test(value.review_digest||'')||!vector(value.scale_change_percent_xyz)||!Number.isFinite(value.maximum_wheel_centre_error_metres)||!Array.isArray(value.preview_parts)||!value.preview_parts.length||value.preview_parts.some(part=>typeof part.id!=='string'||!part.id||typeof part.role!=='string')||new Set(value.preview_parts.map(part=>part.id)).size!==value.preview_parts.length))throw Error('The body-fit review is missing its matching geometry or measurements. Request it again.');
}
function showBodyFitReview(value){
 $('bodyFitMetrics').replaceChildren();
 if(Array.isArray(value.scale_change_percent_xyz)){
  for(const [index,label]of ['Body width · X','Body height · Y','Body length · Z'].entries()){
   const item=document.createElement('div'),name=document.createElement('span'),number=document.createElement('strong');item.className='bodyFitMetric';name.textContent=label;
   const change=value.scale_change_percent_xyz[index];number.textContent=Number.isFinite(change)?(change>0?'+':'')+change.toFixed(2)+'%':'Unavailable';item.append(name,number);$('bodyFitMetrics').append(item);
  }
 }
 if(Number.isFinite(value.maximum_wheel_centre_error_metres)){
  const item=document.createElement('div'),name=document.createElement('span'),number=document.createElement('strong');item.className='bodyFitMetric';name.textContent='Max wheel-centre residual';number.textContent=(value.maximum_wheel_centre_error_metres*1000).toFixed(2)+' mm';item.append(name,number);$('bodyFitMetrics').append(item);
 }
 const issue=value.issue||(!value.available?'The current source cannot produce this body-fit preview. Review the source assignments and template.':!value.within_profile_limits?'This body fit is outside the current profile limits. Choose another policy or review the source/template fit.':'');
 $('bodyFitIssue').textContent=issue;$('bodyFitIssue').classList.toggle('hidden',!issue);
 $('confirmBodyFit').checked=false;$('bodyFitReview').classList.remove('hidden');renderBodyFitControls();
}
$('bodyFitPolicy').onchange=()=>{clearBodyFitReview();invalidateCarPlan('The body-fit choice changed. Review it before checking the build plan.',true);renderBodyFitControls()};
$('reviewBodyFit').onclick=()=>safe(async()=>{
 if(!project)throw Error('Open a model first.');
 const context=editState.context,pid=project.id,policy=$('bodyFitPolicy').value;
 if(!bodyFitPolicies[policy])throw Error('Choose a body policy before requesting its preview.');
 if(bodyFitPending?.context===context||activeUiJob||preparationPending?.context===context)return;
 if(previewUnverifiedContext===context)throw Error('Refresh the source preview before reviewing its body fit.');
 clearBodyFitReview();const pending={context,pid,kind:'review'};bodyFitPending=pending;renderImportGuide();
 let owner=null,epoch=null,version=null;
 const current=()=>editState.context===context&&project?.id===pid&&$('bodyFitPolicy').value===policy&&(epoch===null||bodyFitEpoch===epoch)&&(version===null||editState.version===version)&&!editState.drafts.size;
 try{
  await flushSettings(context);await jobBarrier;
  if(!current()||bodyFitPending!==pending||activeUiJob)return;
  epoch=bodyFitEpoch;version=editState.version;owner=reserveUiJob('body-fit-review');
  const value=await api('/api/body-fit-review',{id:pid,policy});
  if(!current())return;
  validateBodyFitReview(value,pid,policy);bodyFitReview={...value,context,epoch,settingsVersion:version};showBodyFitReview(bodyFitReview);
  if(!value.available)return;
  if(!bodyFitViewer)bodyFitViewer=new Viewer($('bodyFitViewer'),()=>{});
  const query='?policy='+encodeURIComponent(policy)+'&review_digest='+encodeURIComponent(value.review_digest)+(bodyFitViewer.gl?'':'&quality=software');
  const raw=await api('/api/body-fit-preview/'+encodeURIComponent(pid)+query,undefined,{binary:true});
  if(!current()||!bodyFitReviewIsCurrent())return;
  bodyFitViewer.load(raw,value.preview_parts);
  if(bodyFitViewer.meta?.body_fit_digest!==value.review_digest)throw Error('The loaded body-fit preview does not match its review. Request a fresh preview.');
  bodyFitPreviewDigest=value.review_digest;$('confirmBodyFit').checked=false;$('bodyFitStage').classList.remove('hidden');$('bodyFitViews').classList.remove('hidden');
  $('bodyFitPreviewMode').textContent=bodyFitViewer.mode.replace('Â·','·');bodyFitViewer.invalidate();
 }catch(error){
  if(!current())return;
  clearBodyFitReview();throw error;
 }finally{
  if(owner)releaseUiJob(owner);if(bodyFitPending===pending)bodyFitPending=null;
  if(editState.context===context&&project?.id===pid)renderImportGuide();
 }
});
$('confirmBodyFit').onchange=renderBodyFitControls;
for(const [id,view]of [['bodyFitView3d','fit'],['bodyFitViewSide','side'],['bodyFitViewTop','top']])$(id).onclick=()=>{if(bodyFitReviewIsCurrent()&&bodyFitPreviewDigest===bodyFitReview.review_digest)bodyFitViewer?.view(view,bodyFitReview.reference?.forward||'-Z')};
$('approveBodyFit').onclick=()=>safe(async()=>{
 if(bodyFitPending?.context===editState.context||activeUiJob||preparationPending?.context===editState.context)return;
 const review=bodyFitReview;
 if(!project||!bodyFitReviewIsCurrent(review)||bodyFitPreviewDigest!==review.review_digest)throw Error('Request and load a current body-fit preview before approving it.');
 if(!review.available||!review.within_profile_limits)throw Error('This body fit is outside the current profile limits and cannot be approved.');
 if(!$('confirmBodyFit').checked)throw Error('Review the body-fit preview and confirm the measurements first.');
 const context=editState.context,pid=project.id,version=editState.version,pending={context,pid,kind:'approval'};bodyFitPending=pending;
 invalidateCarPlan('Saving the reviewed body fit. Check the full build plan afterwards.',true);renderImportGuide();
 try{
  await editState.mutate(async id=>{
   await jobBarrier;
   if(id!==pid||!bodyFitReviewIsCurrent(review)||bodyFitReview!==review||bodyFitPreviewDigest!==review.review_digest||!$('confirmBodyFit').checked)throw Error('The project changed before body-fit approval. Request a fresh preview.');
   const owner=reserveUiJob('body-fit-approval');
   try{return await api('/api/approve-body-fit',{id,policy:review.policy,review_digest:review.review_digest,confirmed:true})}finally{releaseUiJob(owner)}
  },{body_fit:true});
  if(editState.context===context&&project?.id===pid&&editState.version===version&&!editState.drafts.size)notice('Body fit saved. Your source model and car destination are preserved. Check the complete build plan before building.',true);
 }catch(error){
  if(editState.context!==context||project?.id!==pid){console.error(error);return}
  clearBodyFitReview();throw error;
 }finally{
  if(bodyFitPending===pending)bodyFitPending=null;if(editState.context===context&&project?.id===pid)renderImportGuide();
 }
});
// TEMPLATE_SHELL_UI_BEGIN
const shellLightControls=[
 {id:'shellFrontLightHeight',key:'front_height_ratio',label:'Front light height',default:.42,min:15,max:80},
 {id:'shellRearLightHeight',key:'rear_height_ratio',label:'Rear light height',default:.50,min:15,max:80},
 {id:'shellLightSpacing',key:'half_spacing_ratio',label:'Light distance from centre',default:.32,min:15,max:40}
];
function isTemplateShell(){return project?.assembly_mode==='template_shell'}
function shellOptions(){
 const saved=project?.template_shell_settings||{},p=saved.placement||{};
 return {placement:{scale:p.scale??1,translation_metres:[...(p.translation_metres||[0,0,0])],flip_front:p.flip_front===true,...(p.driver?{driver:JSON.parse(JSON.stringify(p.driver))}:{})},lighting:{...(saved.lighting||{mode:'supplied'})},dispositions:JSON.parse(JSON.stringify(saved.dispositions||{})),running_gear:JSON.parse(JSON.stringify(saved.running_gear===undefined?{schema:1,mode:'profile'}:saved.running_gear))};
}
const shellGearFields=['center_y_metres','center_z_metres','track_metres','tyre_diameter_metres','tyre_width_metres'];
const shellRecord=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const shellDigest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const shellJSON=value=>JSON.stringify(value,(_key,item)=>shellRecord(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const shellVector=value=>Array.isArray(value)&&value.length===3&&value.every(Number.isFinite);
const shellGearCorners=value=>shellRecord(value)&&['FL','FR','RL','RR'].every(corner=>shellVector(value[corner]?.center_metres)&&Number.isFinite(value[corner]?.tyre_radius_metres)&&value[corner].tyre_radius_metres>0&&Number.isFinite(value[corner]?.tyre_width_metres)&&value[corner].tyre_width_metres>0);
const shellGearFrames=value=>shellRecord(value)&&['FL','FR','RL','RR'].every(corner=>shellVector(value[corner]?.reference_local_center_metres)&&shellVector(value[corner]?.local_to_reference_translation_metres)&&Array.isArray(value[corner]?.local_to_reference_rotation)&&value[corner].local_to_reference_rotation.length===3&&value[corner].local_to_reference_rotation.every(shellVector));
function shellGearAxlesKey(value){
 if(!['front','rear'].every(axle=>shellRecord(value?.[axle])&&Object.keys(value[axle]).length===shellGearFields.length&&shellGearFields.every(key=>Number.isFinite(value[axle][key]))))return null;
 return JSON.stringify(['front','rear'].map(axle=>shellGearFields.map(key=>value[axle][key])));
}
function shellRunningGearKey(value){
 if(!shellRecord(value)||value.schema!==1)return null;
 if('wheel_set' in value){
  if(value.wheel_set!=='supra_factory')return null;
  const {wheel_set,...fit}=value;
  const key=shellRunningGearKey(fit);
  return key===null?null:'supra_factory/'+key;
 }
 if(value.mode==='profile'&&Object.keys(value).length===2)return 'profile';
 if(value.mode==='checked_stock'){
  if(Object.keys(value).some(key=>!['schema','mode','source_tyres','tyre_size'].includes(key)))return null;
  if(value.tyre_size!==undefined&&value.tyre_size!=='game')return null;
  const picks=value.source_tyres??{};
  if(!shellRecord(picks)||Object.entries(picks).some(([corner,id])=>!wheelCorners.includes(corner)||typeof id!=='string'||!id))return null;
  return 'checked_stock:'+(value.tyre_size==='game'?'game:':'')+shellJSON(picks);
 }
 const axles=shellGearAxlesKey(value);
 return value.mode==='custom_dimensions'&&Object.keys(value).length===4&&axles!==null?'custom_dimensions:'+axles:null;
}
function shellRunningGearPlanMatches(value){
 const plan=value?.running_gear_plan,request=shellRunningGearKey(value?.running_gear);
 const factory=value?.running_gear?.wheel_set==='supra_factory';
 const policy=factory?'factory_showroom_and_race_wheels':'stock_packed_race_wheel_and_template_showroom';
 const showroom=factory?'d7566473f4c9d8162b901330c735d590b42e4fa6be07292c52f9e3f5d9842281':plan?.asset_sha256?.['hq/wheel'];
 return request!==null&&request===shellRunningGearKey(shellOptions().running_gear)&&shellRecord(plan)&&plan.schema===1&&plan.kind==='inherited_running_gear_plan'&&plan.capability_revision===2&&plan.runtime_verified===false&&plan.current_mount_verified===false&&plan.physics_changed===false&&plan.rounding_applied===false&&plan.native_policy===policy&&plan.configuration_policy==='inherit_selected_destination_unchanged'&&plan.requires_template_authentication===true&&request===shellRunningGearKey(plan.requested)&&typeof plan.ready==='boolean'&&typeof plan.native_no_op==='boolean'&&Array.isArray(plan.blockers)&&shellDigest(plan.plan_digest)&&shellDigest(plan.capability_digest)&&shellDigest(plan.profile_contract_sha256)&&typeof plan.profile_id==='string'&&!!plan.profile_id&&shellRecord(plan.asset_sha256)&&Object.keys(plan.asset_sha256).length>0&&Object.values(plan.asset_sha256).every(shellDigest)&&shellRecord(plan.wheel_output_sha256)&&Object.keys(plan.wheel_output_sha256).length===2&&plan.wheel_output_sha256['hq/wheel']===showroom&&shellDigest(plan.wheel_output_sha256['race/wheel'])&&shellGearAxlesKey(plan.resolved)!==null&&Number.isFinite(plan.resolved.wheelbase_metres)&&plan.resolved.wheelbase_metres>0&&shellGearCorners(plan.corners)&&shellGearCorners(plan.requested_corners)&&shellGearFrames(plan.frames);
}
function shellRunningGearReady(value){
 const plan=value?.running_gear_plan;
 return shellRunningGearPlanMatches(value)&&plan.ready===true&&plan.native_no_op===true&&!plan.blockers.length&&shellJSON(plan.requested_corners)===shellJSON(plan.corners)&&(value.running_gear.mode!=='custom_dimensions'||shellGearAxlesKey(value.running_gear)===shellGearAxlesKey(plan.resolved));
}
function shellPreviewMatches(review){
 const meta=shellViewer?.meta;
 return !!(review&&shellPreviewDigest===review.review_digest&&shellRunningGearReady(review)&&meta?.template_shell_digest===review.review_digest&&meta.actual_native_wheel_geometry===true&&meta.running_gear_plan_digest===review.running_gear_plan.plan_digest&&meta.running_gear_native_no_op===true&&(review.running_gear.mode!=='checked_stock'||shellJSON(meta.wheel_check)===shellJSON(review.wheel_check)));
}
function clearTemplateShellReview(keepDisplay=false){
 globalThis.GarageDriverFit?.clear();
 clearSetupProposal();
 shellEpoch++;shellReview=null;shellPreviewDigest=null;
 $('confirmShellAssembly').checked=false;$('confirmShellAssembly').disabled=true;$('approveShellAssembly').disabled=true;
 if(!keepDisplay){$('shellReviewResult').classList.add('hidden');$('shellStage').classList.add('hidden');$('shellViews').classList.add('hidden')}
}
function shellReviewIsCurrent(review=shellReview){
 return !!(isTemplateShell()&&review&&review.id===project.id&&review.context===editState.context&&review.settingsVersion===editState.version&&review.epoch===shellEpoch&&review.runningGearKey===shellRunningGearKey(shellOptions().running_gear)&&!editState.drafts.size&&!shellDispositionDirty&&!shellDragging);
}
function shellGuidance(step){
 if(shellDispositionDirty)return step('shell_components','Save your component choices','Every excluded part needs a reason. Your original model is retained.','save_shell_components','Save component choices');
 if(shellOptions().running_gear.mode==='checked_stock'&&!project.approved_roles&&(project.stock_setup_pending||!Object.keys(shellOptions().dispositions).length)&&!currentSetup())return step('check_wheels','Check wheels for this car','Review the four GT6 hubs and the parts removed from your model.','check_wheels','Check wheels');
 if(!project.approved_roles)return step('shell_parts','Review the body and its source parts','Keep the static body parts you want. Exclude source wheels or moving parts that supplied components will replace; inspect ambiguous fused parts.','review_shell_parts','Review body parts');
 const choice=project.installation||{};
 if(!['new_entry','replace'].includes(choice.mode))return step('destination','Choose where this car should go','Add your model as a separate car, or explicitly choose a compatible replacement.','choose_destination','Choose destination');
 if(choice.mode==='replace'&&(choice.replace_car_id===null||choice.replace_car_id===undefined))return step('target','Choose the exact car to replace','Load the existing cars and select the identity and driving setup to retain.','choose_target','Choose replacement car');
 if(!project.template_inheritance_approved)return step('inheritance','Review the supplied driving setup','This route supplies the template’s wheels, chassis and other retained components. Confirm that choice before reviewing the assembly.','review_inheritance','Review supplied setup');
 if(!state?.car_baseline)return step('baseline','Choose your owned starting package','The assembled preview reads its supplied wheel geometry from your owned compatible template.','choose_baseline','Choose starting package');
 if(!state?.car_packer)return step('packer','Set up the native packer once','Garage checks the selected packer and uses isolated output folders.','choose_packer','Choose native packer');
 if(shellPending?.context===editState.context)return step('shell_pending','Preparing your assembled car','Wait for the matching component preview and its checks to finish.',null,'Preparing…');
 if(!project.template_shell_status?.approved)return step('shell_review','Place your body on the supplied wheels','Choose lights, inspect the assembled preview, adjust the body and confirm the result.','review_shell','Review assembled car');
 if(approvedPlan?.pid===project.id&&approvedPlan.epoch===planEpoch&&approvedPlan.settingsVersion===editState.version)return step('plan_checked','The full plan permits a test build','Your reviewed assembly and separate-car destination are ready for a native test package. Game behavior still needs testing.','show_build','Review checked build');
 return step('full_plan','Check the complete build plan','Your assembly choice is saved. Check the current destination and combined package before building.','check_plan','Check build plan');
}
function renderShellComponentChoices(){
 if(!project)return;
 const signature=JSON.stringify([project.id,project.source_sha256,project.source_up_axis,project.metres_per_unit,project.inspection.parts.map(p=>[p.id,p.role,p.corner]),project.template_shell_settings?.dispositions||{}]);
 if(shellDispositionContext===editState.context&&shellDispositionSignature===signature)return;
 if(shellDispositionContext!==editState.context){shellDispositionDirty=false;shellViewKind='side';shellPreviewContext=null}
 else if(shellDispositionDirty){shellDispositionDirty=false;notice('The saved source assignments changed. Review the component choices again.');}
 shellDispositionContext=editState.context;shellDispositionSignature=signature;
 const config=shellOptions();shellDispositionRows=new Map();$('shellComponentList').replaceChildren();
 for(const part of project.inspection.parts){
  const row=document.createElement('div');row.className='shellComponentRow';
  const name=document.createElement('div'),title=document.createElement('strong'),detail=document.createElement('small');title.textContent=part.name;detail.textContent=part.triangles.toLocaleString()+' triangles · '+part.id;name.append(title,detail);
  const role=document.createElement('select');role.setAttribute('aria-label','Use of '+part.name);
  for(const [value,label]of Object.entries(roleLabels)){const option=document.createElement('option');option.value=value;option.textContent=value==='ignore'?'Exclude this part':label;role.append(option)}
  role.value=part.role;
  const reason=document.createElement('input');reason.type='text';reason.maxLength=2000;reason.placeholder='Why is this part excluded?';reason.setAttribute('aria-label','Exclusion reason for '+part.name);reason.value=config.dispositions[part.id]?.reason||'';reason.classList.toggle('hidden',part.role!=='ignore');
  const changed=()=>{shellDispositionDirty=true;clearTemplateShellReview();invalidateCarPlan('Component choices changed. Save them and review the assembly again.',true);renderTemplateShellControls()};
  role.onchange=()=>{reason.classList.toggle('hidden',role.value!=='ignore');changed()};reason.oninput=changed;
  shellDispositionRows.set(part.id,{part,role,reason});row.append(name,role,reason);$('shellComponentList').append(row);
 }
}
function renderTemplateShellControls(){
 const mode=isTemplateShell(),context=editState.context,pending=shellPending?.context===context;
 const busy=!!(activeUiJob||pending||preparationPending?.context===context||previewLoadingContext===context||shellDragging);
 const setupBusy=!mode||busy||!!editState.drafts.size||shellDispositionDirty||previewUnverifiedContext===context;
 $('openSetup').disabled=!project||busy||!!editState.drafts.size||shellDispositionDirty||previewUnverifiedContext===context;
 $('openSetup').classList.toggle('hidden',mode&&activePage==='export');
 $('suggestSetup').disabled=setupBusy;$('setupSizing').disabled=setupBusy;
 $('applySetup').disabled=setupBusy||!currentSetup();
 const checked=mode&&shellOptions().running_gear.mode==='checked_stock';
 const gameSize=checked&&shellOptions().running_gear.tyre_size==='game';
 renderWheelDefaults();
 for(const id of ['assemblyMode','assemblyModeExport']){syncControl(id,gameSize?'stock_size':checked?'checked_stock':'template_shell','assembly_mode');$(id).disabled=!project||busy}
 $('sourceTyrePicker').classList.toggle('hidden',!checked);
 $('wheelSetDescription').textContent=gameSize?'GT6 supplies all four rims and tyres at the Supra’s stock size. Your model’s hubs guide the placement.':checked?'GT6 supplies the Supra wheels. This check also matches the diameter of the original tyres.':'Place your wheel-free body around the Supra’s four stock hubs in the assembled preview.';
 if(checked)renderTyrePickers(busy||shellDispositionDirty);
 if(!checked&&$('setupSizing').value==='checked_fit')$('setupSizing').value='keep_size';
 $('templateShellPanel').classList.toggle('hidden',!mode);$('bodyFitPanel').classList.toggle('hidden',mode);$('wheelCards').classList.toggle('hidden',mode);
 $('confirmPartsText').textContent=mode?'I reviewed the body parts, front direction and source size. I checked for fused wheels or moving parts.':'I reviewed the parts and direction.';
 $('statWheelTitle').textContent=mode?'WHEEL SOURCE':'RIMMED WHEEL CORNERS';
 if(mode)$('statWheels').textContent='Supplied';
 if(!project)return;
 const opts=shellOptions(),translation=opts.placement.translation_metres;
 for(const [id,value]of [['shellScale',opts.placement.scale],['shellScaleSlider',opts.placement.scale],['shellForward',-translation[2]],['shellHeight',translation[1]],['shellLateral',translation[0]],['shellLights',opts.lighting.mode]])syncControl(id,value,'template_shell_settings');
 $('shellLightAdjust').classList.toggle('hidden',opts.lighting.mode!=='supplied');
 for(const control of shellLightControls){syncControl(control.id,Number(((opts.lighting[control.key]??control.default)*100).toFixed(4)),'template_shell_settings');$(control.id).disabled=!mode||opts.lighting.mode!=='supplied'||busy||previewUnverifiedContext===context}
 syncControl('confirmShellComponents',!!project.approved_roles,'approved_roles','checked');
 $('shellFlip').textContent=opts.placement.flip_front?'Front flipped · Restore front':'Flip front';
 $('shellScaleReadout').textContent=Number.isFinite(opts.placement.scale)?opts.placement.scale.toFixed(3)+'× uniform size':'Enter a size';
 for(const id of ['shellScale','shellScaleSlider','shellForward','shellHeight','shellLateral','shellLights','shellOriginalSize','shellReset','shellFlip','confirmShellComponents'])$(id).disabled=!mode||busy||previewUnverifiedContext===context;
 $('confirmShellComponents').disabled=!mode||busy||shellDispositionDirty||previewUnverifiedContext===context;
 $('saveShellComponents').disabled=!mode||!shellDispositionDirty||busy;
 for(const row of shellDispositionRows.values()){row.role.disabled=!mode||busy;row.reason.disabled=!mode||busy}
 if(shellReview&&!shellReviewIsCurrent()&&!shellDragging)clearTemplateShellReview();
 const current=shellReviewIsCurrent(),loaded=current&&!!shellPreviewDigest&&shellPreviewMatches(shellReview);
 const approvable=loaded&&!globalThis.GarageDriverFit?.dirty&&shellReview.ready_for_candidate===true&&shellReview.preview_available===true;
 $('reviewTemplateShell').disabled=!mode||busy||!!editState.drafts.size||shellDispositionDirty||previewUnverifiedContext===context;
 $('reviewTemplateShell').textContent=pending&&shellPending.kind==='review'?'Checking assembled car…':checked?'Check assembled car':'Preview assembled car';
 $('confirmShellAssembly').disabled=!approvable||busy;$('approveShellAssembly').disabled=!approvable||busy||!$('confirmShellAssembly').checked;
 for(const id of ['shellViewSide','shellViewTop','shellView3d'])$(id).disabled=!loaded||busy;
 let message='Choose your source components and lights, then preview the assembled car.';
 if(pending)message=shellPending.kind==='approval'?'Saving this reviewed assembly…':'Reading the supplied components and building their matching preview…';
 else if(shellDragging)message='Moving the body. Supplied wheels remain fixed; release to save and refresh the preview.';
 else if(shellDispositionDirty)message='Save your component choices before reviewing the assembly.';
 else if(editState.drafts.size)message='Save your changes to refresh the assembly review.';
 else if(project.template_shell_status?.approved)message='Assembly approved for a native test build. Its saved placement and component choices will be used.';
 else if(current)message=approvable?'Inspect the body, supplied wheels and lights. Confirm the placement when you are satisfied.':'This assembly needs attention. Resolve the explanation below, then preview it again.';
 $('shellStatus').textContent=message;$('shellStatus').classList.toggle('approved',!!project.template_shell_status?.approved&&!busy&&!editState.drafts.size&&!shellDispositionDirty);
}
function shellConfigChange(patch,save=false){
 if(!project)return;
 const options=shellOptions();
 if(patch.placement)options.placement={...options.placement,...patch.placement};
 if(patch.lighting)options.lighting=patch.lighting;
 if(patch.running_gear)options.running_gear=patch.running_gear;
 const changes={template_shell_settings:options};
 return save?settings(changes):stageSettings(changes);
}
async function saveShellComponentChoices(){
 if(!isTemplateShell()||shellDispositionContext!==editState.context)return;
 const dispositions={},overrides={...project.overrides};
 for(const [id,row]of shellDispositionRows){
  const role=row.role.value;
  if(!roleLabels[role])throw Error('Choose how to use every source component.');
  if(role==='ignore'){
   const reason=row.reason.value.trim();if(!reason)throw Error('Record why '+row.part.name+' is excluded.');
   dispositions[id]={disposition:'excluded',reason};
  }
  if(role!==row.part.role||role==='ignore')overrides[id]={role,corner:cornerRoles.includes(role)?row.part.corner:null};
 }
 const options=shellOptions();options.dispositions=dispositions;shellDispositionDirty=false;
 await settings({template_shell_settings:options,overrides,approved_roles:false});
 notice('Component choices saved. Review the body and confirm the parts before previewing the assembly.',true);
}
function validateShellReview(value,pid){
 if(value?.schema!==2||value.algorithm_revision!==6||value.scope!=='template_shell_developer_review'||value.id!==pid||typeof value.available!=='boolean'||typeof value.ready_for_candidate!=='boolean'||typeof value.preview_available!=='boolean'||!Array.isArray(value.blockers)||value.physics_changed!==false||value.source_geometry_changed!==false||value.runtime_verified!==false)throw Error('The assembly review does not match this project. Request a fresh preview.');
 if(value.available&&(value.source?.source_sha256!==project.source_sha256||!/^[a-f0-9]{64}$/.test(value.review_digest||'')))throw Error('The assembly review does not match this saved source. Request a fresh preview.');
 if(value.running_gear_plan==null){if(value.available||value.preview_available||value.ready_for_candidate||!value.blockers.length)throw Error('The assembly review is missing its running-gear proof. Request a fresh preview.');return}
 const plan=value.running_gear_plan;
 if(!shellRunningGearPlanMatches(value)||plan.ready!==plan.native_no_op||(plan.ready?plan.blockers.length:!plan.blockers.length))throw Error('The running-gear proof does not match this saved request. Request a fresh preview.');
 if((value.preview_available||value.ready_for_candidate)&&(!value.available||!shellRunningGearReady(value)||value.reference?.profile_id!==plan.profile_id||value.reference?.profile_contract_sha256!==plan.profile_contract_sha256||value.reference?.running_gear_plan_digest!==plan.plan_digest||shellJSON(value.reference?.asset_sha256)!==shellJSON(plan.asset_sha256)||shellJSON(value.reference?.wheel_geometry)!==shellJSON(plan.corners)))throw Error('The assembly cannot use a changed or unverified running-gear plan. Request a fresh preview.');
 // Source readiness can survive a native preview failure; keep its specific blocker visible.
 if(value.ready_for_candidate&&value.preview_available&&value.blockers.length)throw Error('Resolve the assembly blockers before approving it.');
 if(value.running_gear.mode==='checked_stock'&&value.preview_available){
  const check=value.wheel_check;
  if(check?.schema!==1||typeof check.passed!=='boolean'||check.passed!==value.ready_for_candidate||
     shellJSON(check.wheel_files)!==shellJSON(plan.wheel_output_sha256)||
     !['hq','race'].every(lod=>wheelCorners.every(corner=>['tyre','rim'].every(role=>{
       const row=check.visibility?.[lod]?.[corner]?.[role];
       return row&&Number.isInteger(row.tested)&&row.tested>0&&Number.isInteger(row.visible)&&row.visible>=0&&row.visible<=row.tested&&row.fraction===row.visible/row.tested;
     }))))throw Error('The wheel check is incomplete. Check the assembled car again.');
 }
 if(value.preview_available&&(!Array.isArray(value.preview_parts)||!value.preview_parts.length||value.preview_parts.some(p=>typeof p.id!=='string'||!p.id||typeof p.role!=='string')||new Set(value.preview_parts.map(p=>p.id)).size!==value.preview_parts.length))throw Error('The assembled preview is missing its matching components. Request it again.');
}
function showShellReview(value){
 const problems=value.blockers.filter(p=>p?.code!=='wheel_fit'||!value.wheel_check).map(p=>typeof p==='string'?p:p.message||p.detail||p.code).filter(Boolean);
 if(shellOptions().lighting.mode==='supplied'&&value.blockers.some(p=>p?.code==='shell_light_mount'))$('shellLightAdjust').open=true;
 $('shellIssues').textContent=problems.join('\n');$('shellIssues').classList.toggle('hidden',!problems.length);
 const totals=value.triangle_dispositions||{};let text=(totals.emit||0).toLocaleString()+' source triangles included · '+(totals.excluded||0).toLocaleString()+' explicitly excluded.';
 if(value.placement)text+=' Body size '+value.placement.scale.toFixed(3)+'×, position '+value.placement.translation_metres.map(v=>v.toFixed(3)).join(', ')+' m.';
 const fit=wheelFitText(value.source_wheel_fit);
 if(value.wheel_check)showWheelCheck(value.wheel_check,true);
 $('shellAssemblySummary').textContent=[text,fit].filter(Boolean).join(' ');$('shellReviewResult').classList.remove('hidden');$('confirmShellAssembly').checked=false;renderTemplateShellControls();
}
function renderTyrePickers(disabled){
 const parts=project.inspection.parts,picks=shellOptions().running_gear.source_tyres||{};
 const signature=JSON.stringify([editState.context,parts.map(p=>[p.id,p.name,p.material_name]),picks]);
 for(const corner of wheelCorners){
  const select=$('pickTyre'+corner);
  if(signature!==tyrePickerSource){
   const automatic=document.createElement('option');automatic.value='';automatic.textContent='Find automatically';
   select.replaceChildren(automatic);
   for(const part of parts){
    const option=document.createElement('option');option.value=part.id;
    option.textContent=[part.name,part.material_name,part.id].filter(Boolean).join(' · ');
    option.disabled=Object.entries(picks).some(([other,id])=>other!==corner&&id===part.id);
    select.append(option);
   }
   select.value=picks[corner]||'';
  }
  select.disabled=disabled;
 }
 tyrePickerSource=signature;
}
for(const corner of wheelCorners)$('pickTyre'+corner).onchange=()=>safe(async()=>{
 const gear=shellOptions().running_gear;
 if(!isTemplateShell()||gear.mode!=='checked_stock')return;
 const picks={...gear.source_tyres},id=$('pickTyre'+corner).value;
 if(id)picks[corner]=id;else delete picks[corner];
 const request={...gear};delete request.source_tyres;
 if(Object.keys(picks).length)request.source_tyres=picks;
 await shellConfigChange({running_gear:request},true);
});
function showWheelCheck(check,assembled){
 const panel=$('wheelResults'),rows=$('wheelResultRows'),issues=$('wheelResultIssues');
 panel.classList.remove('hidden');rows.replaceChildren();issues.replaceChildren();
 const matched=check.status==='compatible'&&check.search?.matched!==false;
 const full=assembled&&check.passed===true;
 const failed=check.status==='incompatible'||check.search?.matched===false;
 panel.dataset.result=full?'pass':failed?'fail':'review';
 $('wheelResultTitle').textContent=full?'Wheel check passed at all four corners':failed?'This placement does not fit':check.status==='unmeasured'?'Source tyres need identification':matched?'Dimensions fit · assembled check pending':'Wheel check pending';
 let note=check.wheel_files?'Garage and race wheel files verified. ':'';
 if(check.wheel_file_error)note+=check.wheel_file_error+' ';
 if(check.search?.matched&&Math.abs(check.search.factor-1)>1e-6)note+='The fit uses '+Number(Math.abs(100*(1-check.search.factor)).toFixed(2))+'% '+(check.search.factor<1?'less':'more')+' uniform body scale than an exact wheelbase match. ';
 if(check.search?.matched===false&&check.search.placements_sampled)note+='The checked placements leave part of a wheel face covered. ';
 const limits=check.scale_limits;
 if(shellOptions().running_gear.wheel_set==='supra_factory')note+='The preview uses the factory rims and a tyre-size guide. GT6 renders the tyre in game. ';
 if(check.tyre_size==='game'){
   const ratios=(check.corners||[]).map(row=>row.diameter_ratio).filter(Number.isFinite);
   if(ratios.length){const percent=ratios.map(r=>Math.round(r*100));const low=Math.min(...percent),high=Math.max(...percent);note+='The GT6 tyres are '+(low===high?low:low+'–'+high)+'% of the original tyre diameter. Check the visible space inside each arch. ';}
 }else if(limits&&!limits.both){
   const range=s=>s.map(x=>Number(x.toFixed(3))).join('–')+'×';
   if(limits.tyres&&limits.hubs)note+='At this wheel set’s fixed axle spacing, the hub positions need a body scale of '+range(limits.hubs)+' and the tyre diameters need '+range(limits.tyres)+'. ';
   else note+='The four corners require different proportions from this wheel set. ';
   note+='Check that each selected part is the outer tyre. If those picks are correct, this model needs different running gear. ';
 }
 note+=assembled?'Visibility uses the stationary wheel against each GT6 body mesh. During export, Garage sizes body textures to fit the tested loading budget. Steering and suspension move the wheels in game.':'Apply the placement and check the assembled car to see wheel visibility.';
 $('wheelResultNote').textContent=note;
 const mm=v=>Number.isFinite(v)?Math.round(1000*v)+' mm':'—';
 for(const corner of wheelCorners){
  const row=check.corners?.find(item=>item.corner===corner),tr=document.createElement('tr');
  const shown=lod=>{const pair=check.visibility?.[lod]?.[corner];return pair?Math.round(100*pair.rim.fraction)+'% / '+Math.round(100*pair.tyre.fraction)+'%':'—'};
  for(const value of [cornerNames[corner],mm(row?.source_diameter_metres)+' / '+mm(row?.native_diameter_metres),mm(row?.side_offset_metres)+' / '+mm(row?.lateral_offset_metres),shown('hq'),shown('race')]){
   const td=document.createElement('td');td.textContent=value;tr.append(td);
  }
  rows.append(tr);
 }
 for(const issue of check.messages||[]){const li=document.createElement('li');li.textContent=issue;issues.append(li)}
}
function wheelFitText(fit){
 if(!fit?.corners?.length)return '';
 const measured=fit.corners.filter(row=>Number.isFinite(row.supplied_over_source));
 const percentages=measured.map(row=>Math.round(row.supplied_over_source*100));
 const inset=Math.max(0,...fit.corners.map(row=>(row.corner.endsWith('L')?-1:1)*row.center_offset_metres[0]));
 const offset=Math.max(...fit.corners.map(row=>Math.hypot(...row.center_offset_metres)));
 const range=percentages.length?(Math.min(...percentages)===Math.max(...percentages)?String(percentages[0]):Math.min(...percentages)+'–'+Math.max(...percentages))+'%':'';
 const wheelbase='Model wheelbase '+fit.placed_wheelbase_metres.toFixed(3)+' m. Supplied wheelbase '+fit.profile_wheelbase_metres.toFixed(3)+' m. ';
 const ratiosAtWheelbase=measured.map(row=>row.supplied_over_source*fit.placed_wheelbase_metres/fit.profile_wheelbase_metres);
 const mismatch=ratiosAtWheelbase.some(value=>value<.8||value>1.25);
 return wheelbase+(range?'Supplied tyre diameter is '+range+' of the measured source wheel diameter. ':'Only rims could be measured. ')+
  'Largest wheel-centre offset is '+Math.round(1000*offset)+' mm.'+(inset>=.001?' The supplied centres sit up to '+Math.round(1000*inset)+' mm farther inboard.':'')+
  (mismatch?' Even at matching wheelbase, the source wheel diameter and this profile differ substantially. Reproducing both dimensions requires another wheel or chassis profile.':'');
}
function clearSetupProposal(){
 setupProposal=null;$('setupSummary').textContent='';$('applySetup').classList.add('hidden');$('applySetup').disabled=true;
 $('wheelResults').classList.add('hidden');
}
function currentSetup(){
 return setupProposal&&setupProposal.context===editState.context&&setupProposal.version===editState.version&&
  setupProposal.id===project?.id&&setupProposal.sizing===$('setupSizing').value&&!shellDispositionDirty;
}
async function findSetup(){
 const context=editState.context,pid=project.id,sizing=$('setupSizing').value;
 await flushSettings(context);await jobBarrier;
 if(!isTemplateShell()||context!==editState.context||shellDispositionDirty)return;
 clearSetupProposal();
 const version=editState.version,owner=reserveUiJob('find-shell-setup');
 try{
  const result=await api('/api/suggest-shell-setup',{id:pid,sizing});
  if(context!==editState.context||version!==editState.version||project?.id!==pid||shellDispositionDirty)return;
  if(result.id!==pid||result.sizing!==sizing||!shellDigest(result.token))throw Error('Request the setup again for this project.');
  const opts=result.changes.template_shell_settings,p=opts.placement,lights=opts.lighting;
  const lines=['Body scale '+p.scale.toFixed(3)+'×. Position '+p.translation_metres.map(x=>x.toFixed(3)).join(', ')+' m.',
   result.exclusions.length?'Replace '+result.exclusions.map(row=>row.name+' ('+row.id+')').join(', ')+' with supplied wheels.':'',
   lights.mode==='supplied'?'Supplied lamp settings · front height '+Math.round(100*lights.front_height_ratio)+'%, rear '+Math.round(100*lights.rear_height_ratio)+'%, side spacing '+Math.round(100*lights.half_spacing_ratio)+'%.':'Keep the source lamps.',
   result.optics?.length?'Use the GT6 exterior lens and glass shaders for '+result.optics.map(row=>row.material+' ('+row.id+' → '+roleLabels[row.role]+')').join(', ')+'. GT6 controls lamp switching and face visibility.':'',
   result.finishes?.length?'Use GT6 surface finishes. Listed glow factors and glow maps are discarded. GT6 supplies reflections and lamp switching. '+result.finishes.map(row=>row.material+' ('+row.id+', '+row.effects.map(k=>({emissiveFactor:'glow',emissiveTexture:'glow map',KHR_materials_clearcoat:'clear coat',KHR_materials_specular:'specular response',KHR_materials_emissive_strength:'glow intensity'}[k])).join(', ')+')').join(', ')+'.':'',
   wheelFitText(result.fit),...result.notes];
  $('setupSummary').textContent=lines.filter(Boolean).join('\n');$('applySetup').classList.remove('hidden');
  if(result.wheel_check)showWheelCheck(result.wheel_check,false);
  setupProposal={...result,context,version};
 }finally{releaseUiJob(owner)}
}
$('setupSizing').onchange=clearSetupProposal;
function renderWheelDefaults(){
 const placement=state?.wheel_defaults?.placement;
 $('wheelDefaultState').textContent=placement?'New cars start at '+Number(placement.scale.toFixed(4))+'× with offsets '+placement.translation_metres.map(n=>Number(n.toFixed(3))).join(', ')+' m.':'New cars start with the factory Supra wheels centred on their own hubs.';
}
$('saveWheelDefault').onclick=()=>safe(async()=>{
 if(!project)return;
 await flushSettings(editState.context);
 state.wheel_defaults=await api('/api/wheel-defaults',{mode:'from_project',id:project.id});
 renderWheelDefaults();
});
$('autoWheelDefault').onclick=()=>safe(async()=>{
 state.wheel_defaults=await api('/api/wheel-defaults',{mode:'automatic'});
 renderWheelDefaults();
});
$('openSetup').onclick=()=>safe(async()=>{
 const context=editState.context;
 if(!isTemplateShell()||shellOptions().running_gear.mode!=='checked_stock'){
  const options=shellOptions();options.running_gear={schema:1,mode:'checked_stock',tyre_size:'game'};
  await settings({assembly_mode:'template_shell',template_shell_settings:options});
 }
 if(context!==editState.context||!isTemplateShell())return;
 $('setupSizing').value=project.stock_placement_mode==='saved_placement'?'saved_placement':'checked_fit';
 page('export');$('suggestSetup').focus();await findSetup();
});
$('suggestSetup').onclick=()=>safe(findSetup);
$('applySetup').onclick=()=>safe(async()=>{
 if(!currentSetup())throw Error('Find the setup again for the current model.');
 const proposal=setupProposal;
 await editState.mutate(async id=>{
  await jobBarrier;
  if(proposal!==setupProposal||!currentSetup()||editState.drafts.size)throw Error('The setup changed. Find it again.');
  const owner=reserveUiJob('apply-shell-setup');
  try{return await api('/api/apply-shell-setup',{id,sizing:proposal.sizing,token:proposal.token})}
  finally{releaseUiJob(owner)}
 },{template_shell_settings:true,overrides:true});
 if(editState.context===proposal.context){clearTemplateShellReview();renderImportGuide();notice('Setup saved. Review the parts and assembled preview.',true)}
});
async function requestTemplateShellReview(){
 if(globalThis.GarageDriverFit?.dirty)throw Error('Use Apply and preview to save the edited driver placement.');
 if(!isTemplateShell())throw Error('Choose supplied wheels for this assembly review.');
 const context=editState.context,pid=project.id;
 if(shellPending?.context===context||activeUiJob||preparationPending?.context===context)return;
 if(shellDispositionDirty)throw Error('Save the source component choices first.');
 if(previewUnverifiedContext===context)throw Error('Refresh the saved source preview before reviewing the assembly.');
 clearTemplateShellReview();const pending={context,pid,kind:'review'};shellPending=pending;renderImportGuide();
 let owner=null,epoch=null,version=null;
 const current=()=>editState.context===context&&project?.id===pid&&isTemplateShell()&&(epoch===null||shellEpoch===epoch)&&(version===null||editState.version===version)&&!editState.drafts.size&&!shellDispositionDirty;
 try{
  await flushSettings(context);await jobBarrier;
  if(!current()||shellPending!==pending||activeUiJob)return;
  epoch=shellEpoch;version=editState.version;owner=reserveUiJob('template-shell-review');
  const value=await api('/api/template-shell-review',{id:pid});if(!current())return;
  validateShellReview(value,pid);shellReview={...value,context,epoch,settingsVersion:version,runningGearKey:shellRunningGearKey(shellOptions().running_gear)};showShellReview(shellReview);
  if(!value.preview_available)return;
  if(!shellViewer)shellViewer=new Viewer($('shellViewer'),()=>{},{orthographicViews:true,onPlacementDrag:handleShellPlacementDrag});
  const camera=shellPreviewContext===context?{center:shellViewer.center?.slice(),distance:shellViewer.distance,yaw:shellViewer.yaw,pitch:shellViewer.pitch}:null;
  const query='?review_digest='+encodeURIComponent(value.review_digest)+(shellViewer.gl?'':'&software=1');
  const raw=await api('/api/template-shell-preview/'+encodeURIComponent(pid)+query,undefined,{binary:true});
  if(!current()||!shellReviewIsCurrent())return;
  shellViewer.load(raw,value.preview_parts);
  if(shellViewer.meta?.template_shell_digest!==value.review_digest||shellViewer.meta?.actual_native_wheel_geometry!==true||shellViewer.meta?.running_gear_plan_digest!==value.running_gear_plan.plan_digest||shellViewer.meta?.running_gear_native_no_op!==true)throw Error('The loaded assembly does not match its reviewed supplied components and running gear. Request a fresh preview.');
  if(Array.isArray(shellViewer.meta.meshes)){const ids=new Set(value.preview_parts.map(p=>p.id));if(shellViewer.meta.meshes.some(p=>!ids.has(p.id)))throw Error('The preview contains an unreviewed component. Request it again.');}
  shellPreviewDigest=value.review_digest;shellPreviewContext=context;shellViewer.view(shellViewKind,'-Z');
  globalThis.GarageDriverFit?.bind(shellViewer,driver=>safe(async()=>{
   if(project?.id!==pid||editState.context!==context||!shellReviewIsCurrent()||activeUiJob||shellPending)throw Error('Review the current car before saving the driver position.');
   await shellConfigChange({placement:{driver}},true);
   if(project?.id===pid&&editState.context===context)await requestTemplateShellReview();
  }),()=>{
   $('confirmShellAssembly').checked=false;
   invalidateCarPlan('Apply the edited driver placement before building.',true);
  });
  if(camera&&camera.center?.every(Number.isFinite)&&Number.isFinite(camera.distance)){shellViewer.center=camera.center;shellViewer.distance=camera.distance;if(shellViewKind==='fit'){shellViewer.yaw=camera.yaw;shellViewer.pitch=camera.pitch}}
  $('shellStage').classList.remove('hidden');$('shellViews').classList.remove('hidden');$('shellPreviewMode').textContent=shellViewer.mode.replace('Â·','·');shellViewer.invalidate();
 }catch(error){if(!current())return;clearTemplateShellReview();throw error}
 finally{if(owner)releaseUiJob(owner);if(shellPending===pending)shellPending=null;if(editState.context===context&&project?.id===pid)renderImportGuide()}
}
function handleShellPlacementDrag(event){
 if(event.phase==='start'){
  if(!shellReviewIsCurrent()||!shellPreviewMatches(shellReview)||activeUiJob||shellPending)return false;
  shellDragging={context:editState.context,pid:project.id,settingsVersion:editState.version,options:shellOptions(),changed:false};renderTemplateShellControls();return true;
 }
 const drag=shellDragging;if(!drag)return false;
 if(event.phase==='move'&&!drag.changed){drag.changed=true;clearTemplateShellReview(true);invalidateCarPlan('Body position changed. Review the refreshed assembly before building.',true)}
 if(event.phase==='move'){renderTemplateShellControls();return true}
 if(event.phase==='end'||event.phase==='cancel'){
  shellDragging=null;
  if(!drag.changed){renderTemplateShellControls();return true}
  if(drag.context!==editState.context||drag.pid!==project?.id){renderImportGuide();return false}
  if(event.phase==='cancel'){clearTemplateShellReview();renderImportGuide();return true}
  if(!isTemplateShell()||editState.version!==drag.settingsVersion||editState.drafts.size){clearTemplateShellReview();renderImportGuide();notice('The project changed while moving the body. Review its current placement before dragging again.');return false}
  const position=drag.options.placement.translation_metres.map((v,i)=>v+event.delta[i]);
  safe(async()=>{await shellConfigChange({placement:{translation_metres:position}},true);if(editState.context===drag.context&&project?.id===drag.pid)await requestTemplateShellReview()});return true;
 }
 return false;
}
for(const id of ['assemblyMode','assemblyModeExport'])$(id).onchange=()=>safe(async()=>{
 if(!project)return;
 const choice=$(id).value,options=shellOptions(),changes={assembly_mode:'template_shell'};
 if(!['stock_size','checked_stock','template_shell'].includes(choice))throw Error('Choose a GT6 wheel placement.');
 if(choice!=='template_shell'){
  const picks=options.running_gear.source_tyres;
  options.running_gear={schema:1,mode:'checked_stock'};
  if(choice==='stock_size')options.running_gear.tyre_size='game';
  if(picks)options.running_gear.source_tyres=picks;
  changes.template_shell_settings=options;
 }else if(choice==='template_shell'&&(!project.template_shell_settings||options.running_gear.mode==='checked_stock')){
  options.running_gear={schema:1,mode:'profile'};changes.template_shell_settings=options;
 }
 shellDispositionDirty=false;await settings(changes);renderProject();
 if(choice!=='template_shell')$('setupSizing').value='checked_fit';
});
for(const [id,kind,index]of [['shellScale','scale'],['shellScaleSlider','scale'],['shellForward','translation',2],['shellHeight','translation',1],['shellLateral','translation',0]]){
 const change=save=>{const input=$(id),value=input.value===''?null:Number(input.value);if(kind==='scale'){if(id==='shellScaleSlider')$('shellScale').value=value;else $('shellScaleSlider').value=value;return shellConfigChange({placement:{scale:value}},save)}const p=shellOptions().placement.translation_metres;p[index]=id==='shellForward'&&value!==null?-value:value;return shellConfigChange({placement:{translation_metres:p}},save)};
 $(id).oninput=()=>change(false);$(id).onchange=()=>safe(()=>change(true));
}
$('shellLights').onchange=()=>safe(()=>shellConfigChange({lighting:{mode:$('shellLights').value}},true));
for(const control of shellLightControls){
 const change=save=>{
  const options=shellOptions();if(options.lighting.mode!=='supplied')return;
  const raw=$(control.id).value,value=raw===''?null:Number(raw);
  if(save&&(value===null||!Number.isFinite(value)||value<control.min||value>control.max))throw Error(control.label+' must be between '+control.min+'% and '+control.max+'%.');
  return shellConfigChange({lighting:{...options.lighting,[control.key]:value===null?null:value/100}},save);
 };
 $(control.id).oninput=()=>change(false);$(control.id).onchange=()=>safe(()=>change(true));
}
$('shellOriginalSize').onclick=()=>safe(()=>shellConfigChange({placement:{scale:1}},true));
$('shellReset').onclick=()=>safe(()=>shellConfigChange({placement:{scale:1,translation_metres:[0,0,0],flip_front:false}},true));
$('shellFlip').onclick=()=>safe(()=>shellConfigChange({placement:{flip_front:!shellOptions().placement.flip_front}},true));
$('saveShellComponents').onclick=()=>safe(saveShellComponentChoices);
$('confirmShellComponents').onchange=()=>safe(()=>{if(shellDispositionDirty)throw Error('Save the component choices before confirming them.');return settings({approved_roles:$('confirmShellComponents').checked})});
$('reviewTemplateShell').onclick=()=>safe(requestTemplateShellReview);
$('confirmShellAssembly').onchange=renderTemplateShellControls;
for(const [id,view]of [['shellViewSide','side'],['shellViewTop','top'],['shellView3d','fit']])$(id).onclick=()=>{if(shellReviewIsCurrent()&&shellPreviewMatches(shellReview)){shellViewKind=view;shellViewer.view(view,'-Z');$('shellDragHint').textContent=view==='side'?'Drag to move the body · Scroll to zoom':'Drag to orbit · Scroll to zoom'}};
$('approveShellAssembly').onclick=()=>safe(async()=>{
 if(shellPending?.context===editState.context||activeUiJob||preparationPending?.context===editState.context)return;
 const review=shellReview;
 if(!shellReviewIsCurrent(review)||!shellPreviewMatches(review)||!review.preview_available)throw Error('Load a current assembled preview before approving it.');
 validateShellReview(review,project.id);
 if(!review.ready_for_candidate)throw Error('Resolve the assembly blockers before approving it.');
 if(!$('confirmShellAssembly').checked)throw Error('Inspect and confirm the assembled preview first.');
 const context=editState.context,pid=project.id,version=editState.version,pending={context,pid,kind:'approval'};shellPending=pending;
 invalidateCarPlan('Saving the reviewed assembly. Check the full build plan afterwards.',true);renderImportGuide();
 try{
  await editState.mutate(async id=>{await jobBarrier;if(id!==pid||!shellReviewIsCurrent(review)||shellReview!==review||!shellPreviewMatches(review)||!$('confirmShellAssembly').checked)throw Error('The assembly changed before approval. Request a fresh preview.');validateShellReview(review,id);const owner=reserveUiJob('template-shell-approval');try{return await api('/api/approve-template-shell',{id,review_digest:review.review_digest,confirmed:true})}finally{releaseUiJob(owner)}},{template_shell_decision:true});
  if(editState.context===context&&project?.id===pid&&editState.version===version&&!editState.drafts.size)notice('Assembly saved. Check the complete plan to create your separate native test package.',true);
 }catch(error){if(editState.context!==context||project?.id!==pid){console.error(error);return}clearTemplateShellReview();throw error}
 finally{if(shellPending===pending)shellPending=null;if(editState.context===context&&project?.id===pid)renderImportGuide()}
});
// TEMPLATE_SHELL_UI_END

function hasFourTyreGroups(){return project&&wheelCorners.every(corner=>{const centre=project.inspection.wheel_centers?.[corner];return Array.isArray(centre)&&centre.length===3&&centre.every(Number.isFinite)&&project.inspection.parts.some(part=>part.role==='tyre'&&part.corner===corner)})}
function isPartitionChild(id){return (project?.wheel_partitions||[]).some(decision=>(decision.groups||[]).some(group=>group.id===id))}
function updatePartitionControl(){
 const part=project?.inspection.parts.find(row=>row.id===selected),pending=preparationPending?.context===editState.context;
 $('partitionWheelGroup').disabled=!part||pending||!cornerRoles.includes($('partRole').value)||!hasFourTyreGroups()||isPartitionChild(part.id);updateTyreDiscoveryControl();
}
function clearTyreProposal(){
 tyreProposalEpoch++;tyreProposal=null;$('tyreProposal').classList.add('hidden');$('confirmTyreOnly').checked=false;$('applyTyreDiscovery').disabled=true;
}
function updateTyreDiscoveryControl(){
 const part=project?.inspection.parts.find(row=>row.id===selected),context=editState.context;
 const busy=preparationPending?.context===context||tyreProposalPending?.context===context;
 $('discoverTyres').disabled=!part||busy||previewUnverifiedContext===context||$('partRole').value!=='tyre'||isPartitionChild(part.id);
 $('discoverTyres').textContent=tyreProposalPending?.context===context?'Finding tyre corners…':'Find four tyre corners in this group';
 const valid=tyreProposal&&tyreProposal.context===context&&tyreProposal.id===project?.id&&tyreProposal.part_id===selected&&tyreProposal.settingsVersion===editState.version&&!editState.drafts.size;
 $('applyTyreDiscovery').disabled=!valid||busy||!$('confirmTyreOnly').checked;
 $('confirmTyreOnly').disabled=busy;
}
function showTyreProposal(value){
 $('tyreProposalSummary').textContent=value.triangles_preserved.toLocaleString()+' source triangles will be kept. These are measured source positions, not native wheel-fit approval.';
 $('tyreProposalCorners').replaceChildren();
 for(const row of value.corners){const box=document.createElement('div'),label=document.createElement('strong'),detail=document.createElement('span');box.className='tyreCorner';label.textContent=row.corner+' · '+row.triangles.toLocaleString()+' triangles';detail.textContent='Centre (m): '+row.center_metres.map(v=>v.toFixed(3)).join(', ');box.append(label,detail);$('tyreProposalCorners').append(box)}
 $('confirmTyreOnly').checked=false;$('tyreProposal').classList.remove('hidden');updateTyreDiscoveryControl();
}
function validateTyreProposal(value,pid,partId){
 const part=project.inspection.parts.find(row=>row.id===partId),corners=value.corners;
 if(value.schema_version!==1||value.id!==pid||value.part_id!==partId||typeof value.proposal_token!=='string'||!value.proposal_token||value.review_required!==true||value.source_bytes_unchanged!==true||value.runtime_verified!==false||value.forward!==project.forward||value.source_up_axis!==project.source_up_axis||value.metres_per_unit!==project.metres_per_unit||!Array.isArray(corners)||corners.length!==4||new Set(corners.map(row=>row.corner)).size!==4||!corners.every(row=>wheelCorners.includes(row.corner)&&Number.isInteger(row.triangles)&&row.triangles>0&&Number.isInteger(row.vertices)&&row.vertices>0&&Array.isArray(row.center_metres)&&row.center_metres.length===3&&row.center_metres.every(Number.isFinite))||value.triangles_preserved!==part?.triangles||corners.reduce((sum,row)=>sum+row.triangles,0)!==value.triangles_preserved)throw Error('The tyre proposal does not match this saved source group. Review the component and try again.');
}
$('discoverTyres').onclick=()=>safe(async()=>{
 if(!project||!selected)throw Error('Select a component containing the four tyres first.');
 const context=editState.context,pid=project.id,partId=selected;
 if(preparationPending?.context===context||tyreProposalPending?.context===context)return;
 if($('partRole').value!=='tyre')throw Error('Choose the Tyre component type only if this entire group contains tyre surfaces.');
 if(isPartitionChild(partId))throw Error('Choose an original unsplit tyre group.');
 if(previewUnverifiedContext===context)throw Error('Refresh the prepared preview before reviewing a tyre proposal.');
 clearTyreProposal();const pending={context,pid,partId};tyreProposalPending=pending;updateTyreDiscoveryControl();let requestEpoch=null,requestVersion=null;
 try{
  await flushSettings(context);
  if(editState.context!==context||project?.id!==pid||selected!==partId)return;
  if(project.inspection.parts.find(part=>part.id===partId)?.role!=='tyre')throw Error('Save the Tyre role for this component before finding its four corners.');
  const epoch=tyreProposalEpoch,version=editState.version;requestEpoch=epoch;requestVersion=version;
  const value=await api('/api/discover-tyres',{id:pid,part_id:partId});
  if(editState.context!==context||project?.id!==pid||selected!==partId||editState.version!==version||editState.drafts.size||tyreProposalEpoch!==epoch)return;
  validateTyreProposal(value,pid,partId);tyreProposal={...value,context,settingsVersion:version};showTyreProposal(tyreProposal);
 }catch(error){
  if(editState.context!==context||project?.id!==pid||selected!==partId||(requestEpoch!==null&&(tyreProposalEpoch!==requestEpoch||editState.version!==requestVersion)))return;
  throw Error(error.message+' Keep mixed or ambiguous geometry unsplit; choose a tyre-only group or separate it in your modelling app.');
 }finally{if(tyreProposalPending===pending)tyreProposalPending=null;if(editState.context===context)updateTyreDiscoveryControl()}
});
$('confirmTyreOnly').onchange=updateTyreDiscoveryControl;
$('cancelTyreDiscovery').onclick=()=>{clearTyreProposal();updateTyreDiscoveryControl()};
$('applyTyreDiscovery').onclick=()=>safe(async()=>{
 if(preparationPending?.context===editState.context||tyreProposalPending?.context===editState.context)return;
 const proposal=tyreProposal;if(!project||!proposal)throw Error('Request and review a tyre proposal first.');
 const context=editState.context,pid=project.id,partId=selected;
 if(proposal.context!==context||proposal.id!==pid||proposal.part_id!==partId||proposal.settingsVersion!==editState.version||editState.drafts.size)throw Error('The project changed. Request a fresh tyre proposal before applying it.');
 if(!$('confirmTyreOnly').checked)throw Error('Confirm this group contains only four tyres and review the corner labels first.');
 const pending={context,kind:'tyre_discovery',partId};preparationPending=pending;previewUnverifiedContext=context;clearTyreProposal();
 invalidateCarPlan('Preparing the reviewed tyre corners. Inspect the new preview and confirm the parts again.',true);renderProject();
 try{
  await editState.mutate(async id=>{await jobBarrier;return api('/api/apply-tyre-discovery',{id,part_id:partId,proposal_token:proposal.proposal_token,confirmed_tyre_only:true})},{refresh_previews:true,partition_source_part:partId});
  if(editState.context===context&&project?.id===pid)notice('Four tyre corners are now assigned. Source triangles are preserved. Check the labels, size and front direction, then review the other components.',true);
 }catch(error){
  if(editState.context!==context||project?.id!==pid){console.error(error);return}
  throw Error(error.message+' Reopen this project to refresh its prepared preview before confirming the parts.');
 }finally{
  if(preparationPending===pending)preparationPending=null;
  if(editState.context===context&&project?.id===pid)renderProject();
 }
});
function clearSelectedPart(){
 clearTyreProposal();selected=null;viewer.selected=null;viewer.isolated=null;viewer.invalidate();
 $('selectedName').textContent='Select a part';$('selectedName').dataset.partId='';$('selectedReason').textContent='Click the model or a part in the list.';
 $('partRole').value='';$('partCorner').value='';$('isolate').textContent='Isolate selected part';
 for(const id of ['partRole','partCorner','assignMaterial','isolate','partitionWheelGroup','discoverTyres'])$(id).disabled=true;
}
function selectPart(id){
 if(!project)return;const p=project.inspection.parts.find(x=>x.id===id);if(!p)return;
 const changedPart=selected!==id||$('selectedName').dataset.partId!==id;if(changedPart)clearTyreProposal();selected=id;viewer.selected=id;viewer.invalidate();$('selectedName').textContent=p.name;$('selectedName').dataset.partId=id;
 $('selectedReason').textContent=p.reason+' · '+Math.round(p.confidence*100)+'% confidence'+(p.role_review_required?' · Review the brake surfaces before confirming their type.':'');
 syncControl('partRole',p.role,'overrides','value',changedPart);syncControl('partCorner',p.corner||'','overrides','value',changedPart);
 for(const id of ['partRole','partCorner','assignMaterial','isolate'])$(id).disabled=preparationPending?.context===editState.context;
 updatePartitionControl();renderParts();
}
async function settings(changes){if(!project)return;const context=editState.context;stageSettings(changes);const saving=editState.save();renderImportGuide();try{return await saving}catch(error){if(editState.context===context)throw error}finally{if(editState.context===context)renderImportGuide()}}
async function assign(all=false){if(!project||!selected||preparationPending?.context===editState.context)return;let p=project.inspection.parts.find(x=>x.id===selected),role=$('partRole').value,corner=$('partCorner').value||null,overrides={...project.overrides};if(all&&!confirm('Apply '+roleLabels[role]+' to every part using '+p.material_name+'?'))return;for(const row of project.inspection.parts.filter(x=>all?x.material===p.material:x.id===selected))overrides[row.id]={role,corner:all?row.corner:corner};const saving=settings({overrides,approved_roles:false});updatePartitionControl();await saving}
$('partRole').onchange=()=>safe(()=>assign());$('partCorner').onchange=()=>safe(()=>assign());$('assignMaterial').onclick=()=>safe(()=>assign(true));$('isolate').onclick=()=>{viewer.isolated=viewer.isolated?null:selected;$('isolate').textContent=viewer.isolated?'Show all parts':'Isolate selected part';viewer.invalidate()};$('partSearch').oninput=renderParts;$('roleFilter').onchange=renderParts;$('front').onchange=()=>safe(()=>settings({forward:$('front').value,approved_roles:false}));$('wheelbase').onchange=()=>safe(()=>settings({wheelbase_metres:$('wheelbase').value===''?null:Number($('wheelbase').value)}));$('confirmParts').onchange=()=>safe(()=>{if(preparationPending?.context===editState.context||previewUnverifiedContext===editState.context){$('confirmParts').checked=false;notice('Refresh the prepared preview, then review and confirm the parts.');return}return settings({approved_roles:$('confirmParts').checked})});$('goExport').onclick=()=>page('export');
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>viewer.view(b.dataset.view,project?.forward);$('wheelsOnly').onclick=()=>{viewer.onlyWheels=!viewer.onlyWheels;$('wheelsOnly').classList.toggle('active',viewer.onlyWheels);viewer.invalidate()};$('explode').onclick=()=>{viewer.exploded=!viewer.exploded;$('explode').classList.toggle('active',viewer.exploded);viewer.invalidate()};
async function waitJob(job,onDone){
 const owner=activeUiJob||reserveUiJob(job.id);if(owner.id!==job.id)throw Error('Another operation is already being watched.');currentJob=job.id;
 $('jobbar').classList.remove('hidden');$('joblabel').textContent=job.label;$('notice').classList.add('hidden');let failures=0;
 while(true){
  await new Promise(resolve=>setTimeout(resolve,failures?Math.min(3000,400*2**Math.min(failures,3)):400));
  let status;try{status=await api('/api/job/'+job.id);failures=0}catch(error){
   failures++;$('jobstatus').textContent='Status connection interrupted. Reconnecting; your edits remain queued until this operation finishes.';
   if(failures===3)notice('Garage cannot confirm the operation status yet. It will keep reconnecting; cancellation remains available. Reopen Garage if the desktop app has closed.');
   continue;
  }
  $('jobstatus').textContent=status.progress||'Working locally. Your game is not being opened.';
  if(status.state==='complete'){currentJob=null;releaseUiJob(owner);$('jobbar').classList.add('hidden');if(onDone)await onDone(status.result);return status.result}
  if(status.state==='error'||status.state==='cancelled'){currentJob=null;releaseUiJob(owner);$('jobbar').classList.add('hidden');throw Error(status.error||'Operation cancelled.')}
 }
}
async function upload(file){if(!file)return;await flushSettings();const isProject=/\.gt6car$/i.test(file.name);if(file.size>(isProject?532:256)*1024*1024)throw Error(isProject?'Project exceeds its 532 MiB limit.':'Use a model smaller than 256 MiB.');if(!/\.(glb|obj|gltf|fbx|blend|zip|gt6car)$/i.test(file.name))throw Error('Choose GLB, OBJ, glTF, FBX, Blend, a model ZIP or .gt6car.');invalidateCarPlan('Importing a different source. Review its own build plan.',true);let url=file.name.toLowerCase().endsWith('.gt6car')?'/api/import-project':'/api/import';$('notice').classList.add('hidden');let job=await submitJob(url,undefined,{method:'POST',headers:{'X-Filename':encodeURIComponent(file.name)},body:await file.arrayBuffer()});await waitJob(job,loadProject)}
$('importButton').onclick=$('emptyImport').onclick=()=>$('modelInput').click();$('modelInput').onchange=()=>safe(async()=>{let f=$('modelInput').files[0];$('modelInput').value='';await upload(f)});$('openModel').onclick=()=>safe(pickModel);
$('tryExample').onclick=()=>safe(async()=>{
 const response=await fetch('/example-sedan.glb');
 if(!response.ok)throw Error('The example sedan is missing from this installation.');
 await upload(new File([await response.blob()],'Kenney Sedan.glb',{type:'model/gltf-binary'}));
});
let drag=0;window.addEventListener('dragenter',e=>{e.preventDefault();drag++;document.body.classList.add('dragging')});window.addEventListener('dragleave',e=>{if(--drag<=0){drag=0;document.body.classList.remove('dragging')}});window.addEventListener('dragover',e=>e.preventDefault());window.addEventListener('drop',e=>{e.preventDefault();drag=0;document.body.classList.remove('dragging');if(e.dataTransfer.files.length!==1)return notice('Drop one model, model ZIP, or project at a time.');safe(()=>upload(e.dataTransfer.files[0]))});
function showNativeBuildResult(box,result){
 if(result.driver_fit?.mode==='placed'){
  const pose=document.createElement('p'),point=result.driver_fit.native_targets.seat;
  pose.textContent='Animated driver seat saved at '+point.map(n=>n.toFixed(2)).join(', ')+' m. The package includes your steering and pedal targets.';box.append(pose);
 }
 const summary=document.createElement('p');
 summary.textContent=result.native_files+' verified install files. Check the car in a single-player race after installation.';
 box.append(summary);
 for(const [lod,loading] of Object.entries(result.body_texture_loading||{})){
  if(!loading.maximum_texture_edge)continue;
  const line=document.createElement('p');
  const label=lod==='hq'?'Garage':'Race';
  line.textContent=label+' body textures use a maximum edge of '+loading.maximum_texture_edge+' px to fit the tested loading budget.';
  box.append(line);
 }
}
function showResult(result){const box=$('exportResult');box.replaceChildren();box.classList.remove('hidden');let h=document.createElement('h2');h.textContent=result.complete_native_package?'Complete native test package':result.path?'Export created':'Package checks passed';box.append(h);let p=document.createElement('p');p.textContent=result.path||((result.files?.length||0)+' payload files verified · '+result.name+' · '+result.title_id+' '+result.game_update);box.append(p);if(result.download){let a=document.createElement('a');a.href=result.download+'?token='+encodeURIComponent(token);a.textContent='Download '+result.path.split(/[\\/]/).pop();a.setAttribute('download','');box.append(a)}if(result.report){let info=document.createElement('p');info.textContent='Native rim asset built: '+result.report.output_triangles.toLocaleString()+' triangles. In-game validation has not been performed. Body and game installation unchanged.';box.append(info)}if(result.complete_native_package)showNativeBuildResult(box,result);if(result.game_ready===false&&!result.complete_native_package){let e=document.createElement('p');e.textContent='This is an editable preparation artifact, not a game installer.';box.append(e)}box.scrollIntoView({block:'nearest',behavior:'smooth'})}
async function exportAction(url,needsProject=true,expectedSettingsVersion=null){if(needsProject&&!project)throw Error('Import or open a project first.');const context=editState.context,pid=project?.id;await flushSettings(context);if(needsProject&&(editState.context!==context||project?.id!==pid))throw Error('The open project changed. Retry the export for the current project.');if(expectedSettingsVersion!==null&&editState.version!==expectedSettingsVersion)throw Error('Settings changed after the build plan. Check the plan again before building.');let job=await submitJob(url,needsProject?{id:pid}:{});let result=await waitJob(job,r=>{if(needsProject&&(editState.context!==context||project?.id!==pid))return;showResult(r);if(r.complete_native_package)addBaselineAction(job.id)});return result}
$('saveProject').onclick=()=>safe(async()=>{page('export');await exportAction('/api/export-project')});$('exportProject').onclick=()=>safe(()=>exportAction('/api/export-project'));$('exportNeutral').onclick=()=>safe(()=>exportAction('/api/export-neutral'));$('inspectPackage').onclick=()=>safe(()=>exportAction('/api/inspect-package',false));$('exportPackage').onclick=()=>safe(()=>exportAction('/api/export-package',false));$('pickPackage').onclick=()=>safe(()=>configure(async()=>{let r=await api('/api/pick',{kind:'package'});await refresh();if(r.manifest)showResult(r.manifest)}));$('cancelJob').onclick=()=>safe(async()=>{if(currentJob){await api('/api/cancel',{id:currentJob});$('jobstatus').textContent='Cancellation requested. Finishing the current safe step.'}});$('openExports').onclick=()=>safe(()=>api('/api/open-exports',{}));$('quit').onclick=()=>safe(async()=>{if(currentJob&&!confirm('Cancel the operation and close Garage?'))return;await api('/api/quit',{});document.body.replaceChildren();let e=document.createElement('div');e.style.cssText='padding:80px;font-family:Segoe UI;color:#dae8df';let h=document.createElement('h1');h.textContent='Garage is closed.';e.append(h);let p=document.createElement('p');p.textContent='Your projects are saved. You can close this tab.';e.append(p);document.body.append(e)});
setInterval(()=>api('/api/heartbeat',{}).catch(()=>{}),12000);safe(async()=>{await refresh();
 const requested=sessionStorage.getItem('garage-test-project');sessionStorage.removeItem('garage-test-project');
 const selected=state.projects.find(p=>p.id===requested)||state.projects[0];
 if(selected)await openProject(selected.id);});

$('pickCarBaseline').onclick=()=>safe(async()=>{invalidateCarPlan('Baseline changed. Check the destination and build plan again.',true);await configure(async()=>{await api('/api/pick',{kind:'car-baseline'});targetCatalogue=null;await refresh();renderInstallation()})});
$('pickCarPacker').onclick=()=>safe(async()=>{invalidateCarPlan('Packer changed. Check the build plan again.',true);await configure(async()=>{await api('/api/pick',{kind:'car-packer'});await refresh()})});
$('confirmInheritance').onchange=()=>safe(()=>settings({template_inheritance_approved:$('confirmInheritance').checked}));
$('knownCarRecipe').onclick=()=>safe(async()=>{if(!project)throw Error('Open a project first.');if(preparationPending?.context===editState.context)return;const context=editState.context;invalidateCarPlan('Component setup changed. Review and check the build plan again.',true);await editState.mutate(async id=>{await jobBarrier;return api('/api/apply-known-recipe',{id})});if(editState.context===context)notice('The fingerprint-matched paint and lamp setup is applied. Existing manual assignments were kept. Review and confirm the parts.',true)});
function describeTyreFit(fit){
 if(!fit)return '';
 const size=value=>value&&Number.isFinite(value.radius_metres)&&Number.isFinite(value.width_metres)?(value.radius_metres*1000).toFixed(1)+' × '+(value.width_metres*1000).toFixed(1):null;
 const lines=['Tyre size: imported body / retained native tyres (radius × width, mm).'];
 for(const row of fit.corners||[]){const body=size(row.body),native=size(row.native);lines.push(row.corner+': '+(row.available&&body&&native?body+' / '+native:'Measurement incomplete; review tyre assignments.'))}
 lines.push('Review wheel-arch clearance and suspension fit. Matching wheel centres does not establish tyre compatibility.');
 return '\n'+lines.join('\n');
}
function describeWheelOrientation(review){
 if(!review?.review_required)return '';
 const lines=['Source wheel surface review:'];
 for(const row of review.parts||[]){if(!row.normal_disagreement)continue;const n=row.source_normal_winding;lines.push(row.corner+' '+row.role+' ('+row.name+'): normals oppose '+n.opposed_triangles+' of '+n.assessed_triangles+' assessed faces.')}
 if(review.double_sided_parts)lines.push(review.double_sided_parts+' wheel parts use two-sided source materials; native two-sided rendering still needs verification.');
 lines.push('Review these source surfaces. Native shading and visibility remain unverified.');
 return '\n'+lines.join('\n');
}
function describeAssemblyFit(review,label){
 if(!review)return '';
 if(!review.available)return '\n'+label+': '+(review.issue||'Review the four tyre assignments.');
 const dimensions=review.source.dimensions,target=review.target.dimensions,number=x=>Number.isFinite(x)?x.toFixed(3):'unknown';
 const lines=[label+': source / target (m).',
  'Wheelbase '+number(dimensions.wheelbase_metres)+' / '+number(target.wheelbase_metres)+'; front track '+number(dimensions.front_track_metres)+' / '+number(target.front_track_metres)+'; rear track '+number(dimensions.rear_track_metres)+' / '+number(target.rear_track_metres)+'.'];
 const entered=review.source.entered_wheelbase;
 if(entered&&Number.isFinite(entered.metres))lines.push('Entered reference '+number(entered.metres)+' m; prepared measurement '+number(entered.measured_prepared_metres)+' m. Entering a value alone does not change model units.');
 const rigid=review.rigid_fit||review.planar_fit;
 if(rigid&&Number.isFinite(rigid.maximum_corner_error_metres))lines.push('Rigid comparison without scaling: '+(rigid.maximum_corner_error_metres*1000).toFixed(1)+' mm maximum corner residual.');
 if(!review.target.target_y_known)lines.push('Database dimensions do not supply body origin or wheel height. This is a horizontal comparison only.');
 lines.push('Diagnostic only; no shared transform or physics change applied.');
 return '\n'+lines.join('\n');
}
function describeTargetGeometry(target){
 if(!target)return '';
 const lines=['Selected driving setup: '+(target.target?.label||'unresolved')+' · ID '+target.target?.car_id+'.'];
 for(const [key,label]of [['front_tyre','Front'],['rear_tyre','Rear']]){const tyre=target.physical?.[key]?.nominal;lines.push(label+' database tyre: '+(tyre?(tyre.section_width_metres*1000).toFixed(0)+'/'+tyre.aspect_percent+' R'+tyre.rim_diameter_inches+' · nominal outer radius '+(tyre.outer_radius_metres*1000).toFixed(1)+' mm.':'Specification incomplete.'))}
 for(const issue of target.issues||[])lines.push(issue.message);
 lines.push('Physical tyre specifications, visual wheel options and rendered measurements remain separate. Installed upgrades and runtime fit are unverified.');
 return '\n'+lines.join('\n');
}
$('partitionWheelGroup').onclick=()=>safe(async()=>{
 if(!project||!selected)throw Error('Select the source component you want to split.');
 if(preparationPending?.context===editState.context)return;
 const context=editState.context,pid=project.id,partId=selected,role=$('partRole').value,corner=$('partCorner').value||null;
 if(!cornerRoles.includes(role))throw Error('Choose a rim, tyre, valve, brake rotor or brake caliper type shared by every surface in this component.');
 if(!hasFourTyreGroups())throw Error('Assign all four tyre groups before splitting a component by wheel corner.');
 if(isPartitionChild(partId))throw Error('This component already belongs to a saved corner partition. Choose an original unsplit group.');
 const pending={context,kind:'partition',partId};preparationPending=pending;previewUnverifiedContext=context;
 stageSettings({overrides:{...project.overrides,[partId]:{role,corner}},approved_roles:false});
 invalidateCarPlan('Splitting the reviewed component. Inspect the four parts and check the build plan again.',true);renderProject();
 try{
  await editState.mutate(async id=>{await jobBarrier;return api('/api/partition-wheel-group',{id,part_id:partId,role})},
                         {refresh_previews:true,partition_source_part:partId});
  if(editState.context===context&&project?.id===pid)notice('The reviewed group is now four corner components. Source triangles are preserved. Inspect the parts and confirm the assignments again.',true);
 }catch(error){
  if(editState.context!==context||project?.id!==pid){console.error(error);return}
  throw Error(error.message+' Reopen this project to refresh its prepared preview before confirming the parts.');
 }finally{
  if(preparationPending===pending)preparationPending=null;
  if(editState.context===context&&project?.id===pid)renderProject();
 }
});
$('calibrateUnits').onclick=()=>safe(async()=>{
 if(!project)throw Error('Open a project first.');
 if(preparationPending?.context===editState.context)return;
 const pending={context:editState.context};preparationPending=pending;previewUnverifiedContext=pending.context;
 $('confirmParts').disabled=$('calibrateUnits').disabled=true;
 invalidateCarPlan('Updating source units. Review the prepared model and check the plan again.',true);
 try{
  await editState.mutate(async id=>{await jobBarrier;return api('/api/calibrate-units',{id})},{metres_per_unit:true});
  if(editState.context===pending.context)notice('Source units now use your reference wheelbase. Review the rescaled model and confirm the parts again.',true);
 }finally{
  if(preparationPending===pending)preparationPending=null;
  if(editState.context===pending.context)renderProject();
 }
});
$('reviewCarPlan').onclick=()=>safe(async()=>{
 if(globalThis.GarageDriverFit?.dirty)throw Error('Apply the edited driver placement before checking the plan.');
 if(!project)throw Error('Open a project first.');
 const context=editState.context;await flushSettings(context);
 if(isTemplateShell()&&(shellPending?.context===context||shellDispositionDirty||shellDragging))throw Error('Finish and save the assembly review before checking the build plan.');
 if(isTemplateShell()&&!project.template_shell_status?.approved)throw Error('Approve the assembled preview before checking the build plan.');
 if(bodyFitPending?.context===context)throw Error('Wait for the body-fit review to finish before checking the build plan.');
 if(bodyFitSelectionChanged())throw Error('Approve the selected body fit, or restore the saved policy, before checking the build plan.');
 invalidateCarPlan('Checking this project...');const epoch=planEpoch,pid=project.id;
 try{
  const job=await submitJob('/api/car-plan',{id:pid});
  await waitJob(job,r=>{
   if(planEpoch!==epoch||project?.id!==pid)return;
   const e=$('carPlan');e.textContent=(r.ready?'Ready to build.':'Needs attention:')+'\n'+(r.ready?r.inherited.map(x=>'Retained: '+x).join('\n'):r.actions.join('\n'))+'\n'+(r.installation?.ready?r.output_kind+'\nModel folder: '+r.installation.model_path+'\n':'')+(isTemplateShell()?'Assembly: supplied wheels; saved uniform body placement. Source axle measurements are not required.':'Wheel centres: '+(Number.isFinite(r.fit?.maximum_wheel_centre_error_metres)?r.fit.maximum_wheel_centre_error_metres.toFixed(4)+' m maximum error.':'Review wheel assignments and vehicle template.'))+(Number.isFinite(r.fit?.uniform_scale)?(isTemplateShell()?'\nUniform body size: '+r.fit.uniform_scale.toFixed(5)+'×.':'\nApplied profile body scale: '+r.fit.uniform_scale.toFixed(5)+'×'+(Number.isFinite(r.fit.lateral_fit_ratio)?'; additional lateral ratio '+r.fit.lateral_fit_ratio.toFixed(5)+'×.':'.')):'')+describeAssemblyFit(r.fit?.assembly,'Reviewed native profile')+describeTargetGeometry(r.installation?.target_geometry)+describeAssemblyFit(r.selected_target_fit,'Selected target nominal dimensions')+describeTyreFit(r.fit?.tyre_fit)+describeWheelOrientation(r.source_orientation);
   e.className='carPlan '+(r.ready?'ready':'blocked');approvedPlan=r.ready?{pid,epoch,settingsVersion:editState.version}:null;$('buildCar').disabled=!r.ready;renderImportGuide();
  });
 }catch(e){
  if(planEpoch===epoch&&project?.id===pid)invalidateCarPlan('Needs attention: '+e.message);
  throw e;
 }
});
$('buildCar').onclick=()=>safe(async()=>{
 if(globalThis.GarageDriverFit?.dirty)throw Error('Apply the edited driver placement before building.');
 const context=editState.context;await flushSettings(context);
 if(!project||!approvedPlan||approvedPlan.pid!==project.id||approvedPlan.epoch!==planEpoch)throw Error('Check the build plan for the current project before building.');
 const version=approvedPlan.settingsVersion;invalidateCarPlan('Build requested. Check the plan again before another build.');return exportAction('/api/build-car',true,version);
});
invalidateCarPlan('Open a project and check its build plan.');

function renderInstallation(){
 if(!project)return;
 const choice=project.installation||{mode:null,replace_car_id:null};
 syncControl('carName',project.name,'name');syncControl('installMode',choice.mode||'','installation');
 $('replaceFields').classList.toggle('hidden',choice.mode!=='replace');renderTargets();
 if(choice.mode==='new_entry')$('targetSummary').textContent='Adds a separate identity and model folder. Existing cars remain in the combined package. The exact free ID is checked before building.';
 else if(choice.mode==='replace'){
  const selected=targetCatalogue?.cars.find(c=>c.car_id===choice.replace_car_id);
  $('targetSummary').textContent=selected?(selected.replacement_compatible?'Replacement selected: '+selected.display_name+' (ID '+selected.car_id+'). Its existing name and driving setup are retained.':'This target needs another reviewed vehicle template: '+selected.compatibility_blockers.join(' ')):'Load the available cars and choose the exact slot. The donor is not chosen as the replacement automatically.';
 }else $('targetSummary').textContent='Choose how the model should enter your game. No existing car is selected automatically.';
}
function renderTargets(){
 const selected=project?.installation?.replace_car_id;const q=$('targetSearch').value.toLowerCase();const e=$('replaceTarget');e.replaceChildren(new Option('Choose the exact existing car…',''));
 if(targetCatalogue){for(const car of targetCatalogue.cars){if(q&&!((car.display_name+' '+car.label+' '+car.car_id).toLowerCase().includes(q))&&car.car_id!==selected)continue;e.append(new Option(car.display_name+' · ID '+car.car_id+(car.replacement_compatible?'':' · needs another template'),String(car.car_id)))}}
 else if(selected!==null&&selected!==undefined)e.append(new Option('Saved target · ID '+selected+' · load to check',String(selected)));
 e.value=selected===null||selected===undefined?'':String(selected);
}
$('installMode').onchange=()=>safe(()=>settings({installation:{mode:$('installMode').value||null,replace_car_id:null}}));
$('carName').oninput=()=>stageSettings({name:$('carName').value});
$('carName').onchange=()=>safe(()=>settings({name:$('carName').value}));
$('replaceTarget').onchange=()=>safe(()=>settings({installation:{mode:'replace',replace_car_id:$('replaceTarget').value===''?null:Number($('replaceTarget').value)}}));
$('targetSearch').oninput=renderTargets;
$('loadCarTargets').onclick=()=>safe(async()=>{const job=await submitJob('/api/car-targets',{});await waitJob(job,result=>{targetCatalogue=result;renderInstallation();renderImportGuide()})});
function addBaselineAction(jobId){
 const button=document.createElement('button');button.className='secondary';button.textContent='Keep this combined build for the next car';
 button.onclick=()=>safe(async()=>{button.disabled=true;try{await configure(async()=>{await api('/api/use-built-baseline',{job_id:jobId});targetCatalogue=null;await refresh();renderInstallation();invalidateCarPlan('Previous additions are now included. Check the next car’s destination.');notice('This combined package is now the baseline for the next build. Your installed game has not changed.',true)})}catch(e){button.disabled=false;throw e}});
 $('exportResult').append(button);
}

$('sourceUp').onchange=()=>safe(()=>settings({source_up_axis:$('sourceUp').value}));
$('sourceUnits').onchange=()=>safe(()=>settings({metres_per_unit:Number($('sourceUnits').value)}));
$('sourceUnits').oninput=()=>stageSettings({metres_per_unit:$('sourceUnits').value===''?null:Number($('sourceUnits').value)});
$('wheelbase').oninput=()=>stageSettings({wheelbase_metres:$('wheelbase').value===''?null:Number($('wheelbase').value)});
