import {materialHelpPanel} from './material-help.js';
import * as Help from './troubleshooting.js';

const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('garage-token') || '';
if (token) sessionStorage.setItem('garage-token', token);
history.replaceState(null, '', location.pathname);
let results = [], busy = false;
Help.mount(document);
$('showTroubleshooting').onclick=()=>$('troubleshootingTitle').focus();
$('showTroubleshooting').disabled=false;

async function api(path, data, file) {
  const headers = {'X-Garage-Token':token};
  const options = {headers};
  if (file) {
    options.method='POST'; options.body=file; headers['X-Filename']=encodeURIComponent(file.name);
  } else if (data !== undefined) {
    options.method='POST'; headers['Content-Type']='application/json'; options.body=JSON.stringify(data);
  }
  const r=await fetch(path,options), value=await r.json();
  if (!r.ok) throw Object.assign(new Error(value.error || 'Garage could not complete this action.'), {code:value.code});
  return value;
}
function message(text, code) {
  const box=$('notice');
  box.textContent=text;
  box.hidden=!text;
  const topic=Help.forError(code);
  if (topic) box.append(Help.panel(topic,true));
}
function progress(text) { $('progress').textContent=text; $('progress').hidden=!text; }
async function watch(job) {
  if (job.cancelled) return;
  if (!job.id) throw Error('The operation did not start.');
  while (true) {
    const state=await api('/api/job/'+job.id);
    if (state.state==='complete') return state.result;
    if (state.state==='error'||state.state==='cancelled') throw Object.assign(new Error(state.error), {code:state.code});
    progress(state.progress || state.label);
    await new Promise(resolve=>setTimeout(resolve,400));
  }
}
async function task(fn) {
  if (busy) return;
  busy=true; message(''); $('pick').disabled=$('browse').disabled=$('setup').disabled=$('included').disabled=true;
  try { await fn(); } catch (e) { message(e.message,e.code); }
  finally { busy=false; progress(''); $('pick').disabled=$('browse').disabled=$('setup').disabled=$('included').disabled=false; await refresh().catch(e=>message(e.message)); }
}
async function uploads(files) {
  for (const file of files) {
    progress('Reading '+file.name);
    await watch(await api('/api/test-model',undefined,file));
    await refresh();
  }
}
function element(tag,text,cls) {
  const el=document.createElement(tag); if(text!=null)el.textContent=text; if(cls)el.className=cls; return el;
}
function button(text,click) { const b=element('button',text); b.onclick=click; return b; }
function choice(label, options, value) {
  const group=element('label',label+' '), field=element('select');
  for(const [key,text] of options) field.add(new Option(text,key));
  field.value=value; group.append(field);return [group,field];
}
function status(row) {
  if(row.build_attempt?.result==='failed')return 'Build needs attention';
  if(row.build)return 'Game ZIP verified';
  return {ready_to_build:'Ready for visual review',needs_game_files:'Source fits · game files needed',needs_attention:'Needs attention'}[row.status] || row.status;
}
function renderRow(row) {
  const card=element('article'), head=element('div',null,'resultHead');
  head.append(element('h3',row.file),element('span',status(row),row.build_attempt?.result==='failed'||row.status==='needs_attention'&&!row.build?'status attention':'status'));
  card.append(head);
  const facts=[];if(row.parts!=null)facts.push(row.parts+' components');if(row.wheels)facts.push(row.wheels.corners+'/4 wheel positions',row.wheels.excluded_parts+' source wheel components removed');
  if(row.build)facts.push(row.build.resources_verified+' game resources verified');
  else if(row.native_body)facts.push('Garage and race body files verified');
  if(facts.length)card.append(element('p',facts.join(' · '),'detail'));
  if(row.wheels?.diameter_ratio){const values=Object.values(row.wheels.diameter_ratio).filter(Number.isFinite).map(v=>Math.round(v*100));if(values.length){const low=Math.min(...values),high=Math.max(...values);card.append(element('p','GT6 tyres measure '+(low===high?low:low+'–'+high)+'% of the source tyre diameter. Inspect the gap around the arches.','detail'));}}
  if(row.messages?.length){const list=element('ul');for(const msg of row.messages)list.append(element('li',msg));card.append(list);}
  function openWorkshop(partId) {
    sessionStorage.setItem('garage-test-project',row.project_id);
    if(partId)sessionStorage.setItem('garage-test-part',partId);
    else sessionStorage.removeItem('garage-test-part');
    location.href='/';
  }
  if(row.lights || row.material_repairs?.length){
    const repairs=element('details');
    repairs.append(element('summary','Lights and automatic fixes'));
    for(const [end,lights] of Object.entries(row.lights||{})){
      repairs.append(element('h4',end==='front'?'Front lights':'Rear lights'),element('p',lights.message));
      for(const part of lights.parts){
        const label=part.name+' · '+part.material;
        repairs.append(row.project_id?button(label,()=>openWorkshop(part.id)):element('span',label));
        if(part.reason)repairs.append(element('small',part.reason,'detail'));
      }
      if(lights.candidates?.length){
        const possible=element('details');
        possible.append(element('summary','Review possible '+end+' lenses'));
        for(const part of lights.candidates){
          const label=part.material+' · '+part.name;
          possible.append(row.project_id?button(label,()=>openWorkshop(part.id)):element('span',label),element('p',part.reason));
        }
        repairs.append(possible);
      }
    }
    if(row.lamp_mount_checks?.hq?.passed&&row.lamp_mount_checks?.race?.passed){
      const mm=Math.max(...['hq','race'].map(lod=>row.lamp_mount_checks[lod].maximum_surface_shift_metres))*1000;
      repairs.append(element('p','Supplied mounts passed on both the garage and race bodies. Largest movement: '+mm.toFixed(2)+' mm.'));
    }
    if(row.native_body?.hq?.lamp_states_verified&&row.native_body?.race?.lamp_states_verified)
      repairs.append(element('p','All four headlight and brake switch combinations passed in both native body files.'));
    if(row.material_repairs?.length){
      const list=element('ul');
      for(const repair of row.material_repairs){
        const item=element('li',(repair.action==='review'?'Review · ':'Fixed · ')+repair.material+' — '+repair.message);
        if(row.project_id&&repair.part_ids?.length)item.append(' ',button('Show in Parts',()=>openWorkshop(repair.part_ids[0])));
        list.append(item);
      }
      repairs.append(list);
    }
    for(const [lod,parts] of Object.entries(row.detail_reductions||{})){
      const removed=parts.reduce((n,p)=>n+p.source_triangles-p.output_triangles,0);
      repairs.append(element('p',(lod==='hq'?'Garage':'Race')+' body · simplified '+parts.length+' textured parts by '+removed.toLocaleString()+' triangles to fit the game.'));
    }
    card.append(repairs);
  }
  if(row.material_help){
    const part=row.material_help.part_id;
    card.append(materialHelpPanel(row.material_help,row.project_id?()=>openWorkshop(part):null));
  }else if(row.project_id&&row.messages?.some(msg=>msg.includes('separate native recipe')||msg.includes('no implemented native transfer'))){
    card.append(element('p','This saved result has an older warning. Open in workshop and check the assembled car again to see the material and repair steps.'));
  }
  if(row.build_attempt?.result==='failed')card.append(element('p',row.build_attempt.message));
  const issueTopic=Help.forTestResult(row);
  if(issueTopic)card.append(Help.panel(issueTopic,true));
  if(row.native_finish_substitutions?.length)card.append(element('p','The preview uses the GT6 material finish for these source effects: '+row.native_finish_substitutions.join(', ')+'.','detail'));
  if(row.build)card.append(element('p',row.build.zip,'detail'));
  if(row.project_id){const actions=element('div',null,'rowActions');actions.append(button('Open in workshop',()=>openWorkshop()));card.append(actions);}
  const details=element('details');details.append(element('summary','Single-player race result'+(row.race.result==='passed'?' · passed':row.race.result==='needs_fix'?' · needs a fix':'')));
  const fields=element('div',null,'racefields'),[rl,rf]=choice('Result',[['untested','Untested'],['passed','Looks right on track'],['needs_fix','Needs a fix']],row.race.result);
  const [pl,pf]=choice('Platform',[['','Choose platform'],['RPCS3','RPCS3'],['PS3','PS3']],row.race.platform||'');fields.append(rl,pl);details.append(fields);
  const notes=element('textarea');notes.rows=2;notes.maxLength=2000;notes.value=row.race.notes||'';notes.placeholder='Wheel fit, driver position or what happened on track';const label=element('label','Race notes');label.append(notes);details.append(label);
  const save=button('Save race result',()=>task(async()=>{await api('/api/test-race',{id:row.id,result:rf.value,notes:notes.value,platform:pf.value});message('Race result saved.');}));save.className='saveRace';details.append(save);card.append(details);return card;
}
async function refresh() {
  const [state,history]=await Promise.all([api('/api/state'),api('/api/model-tests')]);
  if (state.car_baseline && state.car_packer) {
    $('setupStatus').textContent = state.stock_setup_active ? 'Stock GT6 1.22 is ready. Choose a model to add to your game.' : 'Your saved build is selected. Choose a model to add to it, or choose your stock PDIPFS to start again.';
  } else {
    $('setupStatus').textContent = state.stock_template ? 'Car template selected. Choose your stock GT6 1.22 PDIPFS folder.' : 'First time here? Prepare a car template, or find one from an earlier Garage kit. Then choose your stock PDIPFS folder.';
  }
  const included=state.included_setup;
  $('setup').hidden=included.available;
  $('makeProfile').hidden=$('profileFolder').hidden=Boolean(state.car_baseline&&state.car_packer);
  $('included').hidden=!included.available||included.active;
  if(included.available&&state.car_packer) {
    $('setupStatus').textContent=included.active?'Updated Supra, F50 and Zonda Cinque loaded. Choose a model to add to this build.':'Your saved build is selected. You can continue it or start from the included Supra, F50 and Zonda Cinque.';
  }
  if(included.available&&!state.car_packer) $('setupStatus').textContent='Extract the entire friends ZIP, including Support/GTToolsSharp, then reopen Garage.';
  results=history.tests;$('download').disabled=!results.length;
  $('results').replaceChildren(...(results.length?results.map(renderRow):[element('p','Your model results will appear here.','empty')]));
}
$('pick').onclick=()=>task(async()=>{progress('Choose car models in the file window.');await watch(await api('/api/pick',{kind:'test-models'}));});
$('makeProfile').onclick=()=>task(async()=>{await api('/api/pick',{kind:'profile-builder'});message('Follow the template window, then choose Use my stock PDIPFS.');});
$('profileFolder').onclick=()=>task(async()=>{await api('/api/pick',{kind:'profile-folder'});});
$('setup').onclick=()=>task(async()=>{progress('Choose your local PDIPFS folder.');await watch(await api('/api/pick',{kind:'game-data'}));});
$('included').onclick=()=>task(async()=>{await api('/api/use-included-baseline',{});});
$('browse').onclick=()=>$('files').click();$('files').onchange=()=>{const selected=Array.from($('files').files);$('files').value='';task(()=>uploads(selected));};
for(const name of ['dragover','drop'])window.addEventListener(name,e=>e.preventDefault());
$('drop').ondragover=e=>{e.preventDefault();$('drop').classList.add('over');};$('drop').ondragleave=()=>$('drop').classList.remove('over');
$('drop').ondrop=e=>{e.preventDefault();$('drop').classList.remove('over');const files=Array.from(e.dataTransfer.files);task(()=>uploads(files));};
$('download').onclick=()=>{const report={tool:'GT6 Garage',tests:results.map(({id,project_id,...test})=>test)};const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)+'\n'],{type:'application/json'}));const link=element('a');link.href=url;link.download='GT6-model-test-report.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1500);};
refresh().catch(e=>message(e.message));setInterval(()=>api('/api/heartbeat',{}).catch(()=>{}),12000);
