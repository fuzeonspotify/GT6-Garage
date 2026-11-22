/* Executes the actual app.js with a small DOM/loopback transport stand-in.
 * No UI or settings implementation is copied into the test. No dependencies.
 */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const crypto=require('node:crypto');
const sourcePath=process.argv[2]||require('node:path').join(__dirname,'../web/app.js');
const source=fs.readFileSync(sourcePath,'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
const tick=()=>new Promise(setImmediate);
async function until(predicate,label){const deadline=Date.now()+2000;while(Date.now()<deadline){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,1))}throw Error('Timed out: '+label)}
class Element{
 constructor(id=''){this.id=id;this.value='';this.textContent='';this.checked=false;this.disabled=false;this.dataset={};this.children=[];this.style={};this.className='';const set=()=>new Set(this.className.split(/\s+/).filter(Boolean)),save=values=>{this.className=[...values].join(' ')};this.classList={add:(...names)=>{const values=set();names.forEach(name=>values.add(name));save(values)},remove:(...names)=>{const values=set();names.forEach(name=>values.delete(name));save(values)},toggle:(name,force)=>{const values=set(),add=force??!values.has(name);if(add)values.add(name);else values.delete(name);save(values);return add},contains:name=>set().has(name)}}
 append(...values){this.children.push(...values)}replaceChildren(...values){this.children=[...values]}setAttribute(){}scrollIntoView(){this.scrolled=true}focus(){this.focused=true}
}
function readiness(user_tasks=[],capability_limits=[],build_review_tasks=[]){return {schema_version:1,scope:'source_preflight',profile:{id:'synthetic',label:'Synthetic profile'},source_sha256:'a'.repeat(64),user_tasks,capability_limits,build_review_tasks,source_review_complete:!user_tasks.length,build_readiness_checked:false,runtime_verified:false}}
// Controlled server proof for UI transport checks; native resolution is tested in Python.
function shellGearPlan(request={schema:1,mode:'profile'}){
 const front={center_y_metres:.19565,center_z_metres:-1.275,track_metres:1.536523,tyre_diameter_metres:.6433,tyre_width_metres:.235},rear={...front,center_z_metres:1.275,tyre_diameter_metres:.6358,tyre_width_metres:.255};
 const resolved={front,rear,wheelbase_metres:2.55},requested=clone(request),blockers=[];
 if(requested.mode==='custom_dimensions')for(const axle of ['front','rear'])for(const field of Object.keys(resolved[axle]))if(requested[axle][field]!==resolved[axle][field])blockers.push({code:'running_gear_unsupported',field:axle+'.'+field,requested:requested[axle][field],resolved:resolved[axle][field],message:axle+'.'+field+' differs from the inherited profile. Coherent native adjustment is not implemented.'});
 const makeCorners=axles=>Object.fromEntries(['FL','FR','RL','RR'].map(corner=>{const v=axles[corner[0]==='F'?'front':'rear'];return [corner,{center_metres:[(corner[1]==='L'?-1:1)*v.track_metres/2,v.center_y_metres,v.center_z_metres],tyre_radius_metres:v.tyre_diameter_metres/2,tyre_width_metres:v.tyre_width_metres}]}));
 const corners=makeCorners(resolved),frames=Object.fromEntries(Object.entries(corners).map(([corner,v])=>[corner,{reference_local_center_metres:[v.tyre_width_metres/2,0,0],local_to_reference_rotation:[[1,0,0],[0,1,0],[0,0,1]],local_to_reference_translation_metres:v.center_metres}]));
 const plan={schema:1,kind:'inherited_running_gear_plan',requested,resolved,corners,requested_corners:requested.mode==='custom_dimensions'?makeCorners(requested):clone(corners),frames,native_no_op:!blockers.length,ready:!blockers.length,blockers,capability_revision:2,capability_digest:'c'.repeat(64),profile_id:'synthetic-reviewed-profile',profile_contract_sha256:'b'.repeat(64),asset_sha256:{'hq/wheel':'e'.repeat(64),'race/wheel':'f'.repeat(64)},wheel_output_sha256:{'hq/wheel':'e'.repeat(64),'race/wheel':'a'.repeat(64)},native_policy:'stock_packed_race_wheel_and_template_showroom',configuration_policy:'inherit_selected_destination_unchanged',requires_template_authentication:true,current_mount_verified:false,runtime_verified:false,physics_changed:false,rounding_applied:false};
 plan.plan_digest=crypto.createHash('sha256').update(JSON.stringify(plan)).digest('hex');return plan;
}
function shellReference(plan){return {profile_id:plan.profile_id,profile_contract_sha256:plan.profile_contract_sha256,asset_sha256:clone(plan.asset_sha256),wheel_geometry:clone(plan.corners),running_gear_plan_digest:plan.plan_digest}}
function fixture(id='a',name='1997_toyota_supra_rz'){
 const part={id:'n0p0',name:'body',material:0,material_name:'Paint',role:'body',corner:null,confidence:1,reason:'source',triangles:1,warnings:[]};
 return {id,name,source_filename:'car.glb',created:1,part_count:1,triangles:1,forward:'+Z',source_up_axis:'+Y',metres_per_unit:1,wheelbase_metres:2.5,approved_roles:false,template_inheritance_approved:false,known_recipe_available:false,overrides:{},installation:{mode:null,replace_car_id:null},source_warnings:[],import_readiness:readiness([{code:'confirm_source_review',title:'Confirm your component review',detail:'Confirm the reviewed component roles and front direction.',action:'confirm_parts',part_ids:[]}]),inspection:{parts:[part],role_counts:{body:1},wheelbase:2.5,issues:[]}};
}
function mechanicalFixture(id='mechanical'){
 const value=fixture(id,'Reviewed brake group'),base=value.inspection.parts[0];
 const group={...base,id:'brake_group',name:'Brake surfaces',material_name:'brake_disc',role:'brake_rotor',role_review_required:true,confidence:.6,triangles:8};
 const tyres=['FL','FR','RL','RR'].map(corner=>({...base,id:'tyre_'+corner,name:'Tyre '+corner,role:'tyre',corner,triangles:2}));
 value.inspection.parts=[group,...tyres,{...base,id:'shell',name:'Shell'}];value.inspection.role_counts={brake_rotor:1,tyre:4,body:1};
 value.inspection.wheel_centers=Object.fromEntries(tyres.map(part=>[part.corner,[part.corner[1]==='L'?1:-1,0,part.corner[0]==='F'?1:-1]]));
 value.part_count=6;value.triangles=17;value.wheel_partitions=[];value.approved_roles=true;
 value.installation={mode:'replace_existing',replace_car_id:82};return value;
}
function environment(){
 const elements=new Map(),calls=[],errors=[],records=new Map([['a',fixture()]]),jobs=new Map(),viewerLoads=[],viewerViews=[];let sequence=0,settingsGate=null,jobGate=null,activeSettings=0,maxActiveSettings=0,pollFailures=0,pickerResult={cancelled:true},tyreFit=null,orientation=null,geometry=null,calibrationGate=null,partitionGate=null,discoveryGate=null,applyDiscoveryGate=null,previewGate=null,previewFailures=0,bodyFitGate=null,bodyFitPreviewGate=null,bodyFitApprovalGate=null,bodyFitPreviewFailures=0,bodyFitOverride={},wrongBodyFitDigest=false,bodyFitSoftware=false,shellReviewGate=null,shellPreviewGate=null,shellApprovalGate=null,shellPreviewFailures=0,shellReportOverride={},shellPreviewOverride={},wrongShellDigest=false,shellSoftware=false,nativeShellGeometry=true;const projectGates=new Map(),appState={version:'test',projects:[],car_baseline:'baseline',car_packer:'packer'};
 const get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id)};get('roleFilter').value='all';
 const document={getElementById:get,createElement:()=>new Element(),querySelectorAll:()=>[],activeElement:null,body:new Element('body')};
 const response=data=>({ok:true,json:async()=>clone(data),arrayBuffer:async()=>{const buffer=new ArrayBuffer(8);if(data.fit_digest)buffer.bodyFitDigest=data.fit_digest;if(data.shell_digest){buffer.shellDigest=data.shell_digest;buffer.nativeShellGeometry=data.native_shell_geometry;buffer.runningGearPlanDigest=data.running_gear_plan_digest;buffer.runningGearNativeNoOp=data.running_gear_native_no_op}return buffer}});
 const context={document,location:{hash:'#token',pathname:'/'},history:{replaceState(){}},sessionStorage:{getItem(){return null},setItem(){},removeItem(){}},window:{addEventListener(){}},Option:class extends Element{constructor(text,value){super();this.textContent=text;this.value=value}},Viewer:class{constructor(canvas){this.canvasId=canvas.id;this.gl=(canvas.id==='bodyFitViewer'&&bodyFitSoftware)||(canvas.id==='shellViewer'&&shellSoftware)?null:{};this.mode='test';this.parts={}}load(buffer,parts){this.setParts(parts);this.selected=null;this.isolated=null;this.meta={body_fit_digest:buffer.bodyFitDigest,template_shell_digest:buffer.shellDigest,actual_native_wheel_geometry:buffer.nativeShellGeometry,running_gear_plan_digest:buffer.runningGearPlanDigest,running_gear_native_no_op:buffer.runningGearNativeNoOp};viewerLoads.push({viewer:this.canvasId,parts:parts.map(part=>part.id),software:!this.gl})}setParts(parts){this.parts=Object.fromEntries(parts.map(part=>[part.id,part]))}invalidate(){}view(kind,forward){viewerViews.push({viewer:this.canvasId,kind,forward})}},confirm:()=>true,console:{error:e=>errors.push(String(e)),log(){}},setInterval:()=>0,setTimeout:(fn,ms)=>setTimeout(fn,ms===400||ms===7000?0:ms),clearTimeout,fixture:fixture(),fetch:async(url,options={})=>{
  const data=options.body&&typeof options.body==='string'?JSON.parse(options.body):{};calls.push({url,data:clone(data)});
  if(url==='/api/state')return response(appState);
  if(url.startsWith('/preview/')){const gate=previewGate;previewGate=null;if(gate)await gate.promise;if(previewFailures>0){previewFailures--;throw Error('Simulated prepared preview failure')}return response({})}
  if(url.startsWith('/api/project/')){const id=url.split('/').pop(),gate=projectGates.get(id);projectGates.delete(id);if(gate)await gate.promise;return response(records.get(id))}
  if(url==='/api/settings'){
   activeSettings++;maxActiveSettings=Math.max(maxActiveSettings,activeSettings);const gate=settingsGate;settingsGate=null;const previous=clone(records.get(data.id));
   if(gate)await gate.promise;
   const updated={...previous,...data.changes};
   if(['assembly_mode','template_shell_settings','overrides','forward','approved_roles','template_inheritance_approved','installation','source_up_axis','metres_per_unit'].some(k=>k in data.changes))updated.template_shell_status={approved:false};
   if(data.changes.overrides)updated.inspection={...previous.inspection,parts:previous.inspection.parts.map(part=>data.changes.overrides[part.id]?{...part,...data.changes.overrides[part.id],role_review_required:false,confidence:1}:part)};
   records.set(data.id,updated);activeSettings--;return response(updated);
  }
  if(url==='/api/calibrate-units'){
   const previous=clone(records.get(data.id)),gate=calibrationGate;calibrationGate=null;if(gate)await gate.promise;
   const updated={...previous,metres_per_unit:previous.metres_per_unit*previous.wheelbase_metres/previous.inspection.wheelbase,approved_roles:false,inspection:{...previous.inspection,wheelbase:previous.wheelbase_metres}};
   records.set(data.id,updated);return response(updated);
  }
  if(url==='/api/partition-wheel-group'){
   const previous=clone(records.get(data.id)),gate=partitionGate;partitionGate=null;if(gate)await gate.promise;
   const parent=previous.inspection.parts.find(part=>part.id===data.part_id);if(!parent)throw Error('Selected source group no longer exists');
   const children=['FL','FR','RL','RR'].map(corner=>({...parent,id:data.part_id+'__wheel_'+corner,name:parent.name+' '+corner,role:data.role,corner,role_review_required:false,confidence:1,triangles:parent.triangles/4}));
   const parts=previous.inspection.parts.flatMap(part=>part.id===parent.id?children:[part]);
   const overrides={...previous.overrides};delete overrides[parent.id];for(const child of children)overrides[child.id]={role:child.role,corner:child.corner};
   const updated={...previous,approved_roles:false,part_count:parts.length,overrides,
    inspection:{...previous.inspection,parts,role_counts:parts.reduce((counts,part)=>({...counts,[part.role]:(counts[part.role]||0)+1}),{})},
    wheel_partitions:[...previous.wheel_partitions,{source_part:{id:parent.id},groups:children.map(part=>({id:part.id,corner:part.corner}))}],
    partition_result:{source_part:parent.id,role:data.role,children:children.map(part=>part.id),triangles_preserved:parent.triangles,source_bytes_unchanged:true,runtime_verified:false}};
   records.set(data.id,updated);return response(updated);
  }
  if(url==='/api/discover-tyres'){
   const previous=clone(records.get(data.id)),gate=discoveryGate;discoveryGate=null;if(gate)await gate.promise;
   const parent=previous.inspection.parts.find(part=>part.id===data.part_id);if(parent.role!=='tyre')throw Error('Choose the tyre role first');
   return response({schema_version:1,id:data.id,part_id:data.part_id,proposal_token:'reviewed-token',forward:previous.forward,source_up_axis:previous.source_up_axis,metres_per_unit:previous.metres_per_unit,corners:['FL','FR','RL','RR'].map(corner=>({corner,triangles:parent.triangles/4,vertices:8,center_metres:[corner[1]==='L'?1:-1,0,corner[0]==='F'?1:-1]})),triangles_preserved:parent.triangles,source_bytes_unchanged:true,runtime_verified:false,review_required:true});
  }
  if(url==='/api/apply-tyre-discovery'){
   const previous=clone(records.get(data.id)),gate=applyDiscoveryGate;applyDiscoveryGate=null;if(gate)await gate.promise;
   assert.equal(data.confirmed_tyre_only,true);assert.equal(data.proposal_token,'reviewed-token');
   const parent=previous.inspection.parts.find(part=>part.id===data.part_id);
   const children=['FL','FR','RL','RR'].map(corner=>({...parent,id:parent.id+'__wheel_'+corner,name:parent.name+' '+corner,role:'tyre',corner,triangles:parent.triangles/4}));
   const parts=previous.inspection.parts.flatMap(part=>part.id===parent.id?children:[part]);
   const updated={...previous,approved_roles:false,part_count:parts.length,
    inspection:{...previous.inspection,parts,wheel_centers:Object.fromEntries(children.map(part=>[part.corner,[part.corner[1]==='L'?1:-1,0,part.corner[0]==='F'?1:-1]])),role_counts:{...previous.inspection.role_counts,tyre:4}},
    wheel_partitions:[{source_part:{id:parent.id},groups:children.map(part=>({id:part.id,corner:part.corner}))}],
    tyre_discovery_result:{source_part:parent.id,children:children.map(part=>part.id),source_bytes_unchanged:true,runtime_verified:false}};
   records.set(data.id,updated);return response(updated);
  }
  if(url==='/api/body-fit-review'){
   const previous=clone(records.get(data.id)),gate=bodyFitGate;bodyFitGate=null;if(gate)await gate.promise;
   const report={schema:1,scope:'static_body_fit_review',id:data.id,policy:data.policy,available:true,within_profile_limits:true,issue:null,
    review_digest:(data.policy==='adapt_body'?'b':'c').repeat(64),source:{source_sha256:previous.source_sha256},
    scale_change_percent_xyz:data.policy==='adapt_body'?[-4,1,1]:[0,0,0],maximum_wheel_centre_error_metres:.0015,
    reference:{profile_id:'synthetic-reviewed-profile',forward:'-Z',current_mount_verified:false,requires_template_authentication:true},
    wheel_conversion:{policy:'existing_profile_radial_and_axial_resize',native_tyres_retained:true,source_rim_rigidity_guaranteed:false},
    preview_parts:[{id:'fitted_body',role:'body'},{id:'template_tyre_FL',role:'tyre'}],physics_changed:false,source_geometry_changed:false,runtime_verified:false,...clone(bodyFitOverride)};
   return response(report);
  }
  if(url.startsWith('/api/body-fit-preview/')){
   const gate=bodyFitPreviewGate;bodyFitPreviewGate=null;if(gate)await gate.promise;
   if(bodyFitPreviewFailures>0){bodyFitPreviewFailures--;throw Error('Simulated body-fit preview failure')}
   const query=new URL(url,'http://localhost').searchParams;
   return response({fit_digest:wrongBodyFitDigest?'f'.repeat(64):query.get('review_digest')});
  }
  if(url==='/api/approve-body-fit'){
   const previous=clone(records.get(data.id)),gate=bodyFitApprovalGate;bodyFitApprovalGate=null;if(gate)await gate.promise;
   assert.equal(data.confirmed,true);assert.ok(['keep_proportions','adapt_body'].includes(data.policy));assert.match(data.review_digest,/^[a-f0-9]{64}$/);
   const updated={...previous,body_fit_status:{approved:true,reason:'current',policy:data.policy,review_digest:data.review_digest}};
   records.set(data.id,updated);return response(updated);
  }
  if(url==='/api/template-shell-review'){
   const previous=clone(records.get(data.id)),gate=shellReviewGate;shellReviewGate=null;if(gate)await gate.promise;
   const options=previous.template_shell_settings,gear=shellGearPlan(options.running_gear);
   return response({schema:2,algorithm_revision:6,scope:'template_shell_developer_review',id:data.id,available:gear.ready,ready_for_candidate:gear.ready,within_profile_limits:gear.ready,preview_available:gear.ready,review_digest:gear.ready?'d'.repeat(64):null,source:gear.ready?{source_sha256:previous.source_sha256}:null,placement:clone(options.placement),lighting:clone(options.lighting),running_gear:clone(gear.requested),running_gear_plan:gear,reference:gear.ready?shellReference(gear):null,blockers:clone(gear.blockers),triangle_dispositions:{emit:previous.triangles,excluded:0},preview_parts:[{id:'body',role:'body',moves_with_body:true},{id:'native_FL',role:'tyre',moves_with_body:false}],physics_changed:false,source_geometry_changed:false,runtime_verified:false,...clone(shellReportOverride)});
  }
  if(url.startsWith('/api/template-shell-preview/')){
   const gate=shellPreviewGate;shellPreviewGate=null;if(gate)await gate.promise;
   if(shellPreviewFailures>0){shellPreviewFailures--;throw Error('Simulated assembly preview failure')}
   const query=new URL(url,'http://localhost').searchParams,pid=decodeURIComponent(url.split('/')[3].split('?')[0]),gear=shellGearPlan(records.get(pid).template_shell_settings.running_gear);
   return response({shell_digest:wrongShellDigest?'f'.repeat(64):query.get('review_digest'),native_shell_geometry:nativeShellGeometry,running_gear_plan_digest:gear.plan_digest,running_gear_native_no_op:gear.native_no_op,...clone(shellPreviewOverride)});
  }
  if(url==='/api/approve-template-shell'){
   const previous=clone(records.get(data.id)),gate=shellApprovalGate;shellApprovalGate=null;if(gate)await gate.promise;
   assert.equal(data.confirmed,true);assert.match(data.review_digest,/^[a-f0-9]{64}$/);
   const updated={...previous,template_shell_status:{approved:true,review_digest:data.review_digest}};records.set(data.id,updated);return response(updated);
  }
  if(url==='/api/car-plan'||url==='/api/build-car'){
   const id='job'+(++sequence),project=clone(records.get(data.id)),gate=jobGate;jobGate=null;
   const result=url.endsWith('car-plan')?{ready:true,inherited:[],actions:[],fit:{maximum_wheel_centre_error_metres:0,tyre_fit:tyreFit,...(project.assembly_mode==='template_shell'?{uniform_scale:project.template_shell_settings.placement.scale}:{}),...(geometry?.fit||{})},source_orientation:orientation,selected_target_fit:geometry?.nominal,installation:{ready:true,model_path:'0080/0010',display_name:project.name,target_geometry:geometry?.target},output_kind:project.name}:{path:'/tmp/'+project.name+'.zip',name:project.name,complete_native_package:true,native_files:43,game_ready:false};
   jobs.set(id,{id,state:'complete',result,gate,project});return response({id,label:'Test job',state:'running'});
  }
  if(url.startsWith('/api/job/')){if(pollFailures>0){pollFailures--;throw Error('Temporary local connection failure')}const job=jobs.get(url.split('/').pop());if(job.gate)await job.gate.promise;const {gate,project,...publicJob}=job;return response(publicJob)}
  if(url==='/api/pick'){if(data.kind!=='model')return response({path:'new baseline'});if(pickerResult instanceof Error)throw pickerResult;return response(pickerResult)}
  if(url==='/api/use-built-baseline')return response({});
  throw Error('Unexpected API '+url);
 }};
 vm.createContext(context);
 const helperPath=require('node:path').join(require('node:path').dirname(sourcePath),'material-help.js');
 vm.runInContext(fs.readFileSync(helperPath,'utf8').replace('export function materialHelpPanel','function materialHelpPanel'),context,{filename:'material-help.js'});
 vm.runInContext(source.replace(/^import\s+\{(?:Viewer|materialHelpPanel)\}[^\n]*\n/gm,''),context,{filename:'app.js'});
 return {context,get,document,calls,errors,records,jobs,viewerLoads,viewerViews,appState,run:code=>vm.runInContext(code,context),gateSettings:()=>settingsGate=deferred(),gateJob:()=>jobGate=deferred(),maxActive:()=>maxActiveSettings,failPolls:n=>{pollFailures=n},gateProject:id=>{const gate=deferred();projectGates.set(id,gate);return gate},picker:value=>{pickerResult=value},tyreFit:value=>{tyreFit=clone(value)},orientation:value=>{orientation=clone(value)},geometry:value=>{geometry=clone(value)},gateCalibration:()=>calibrationGate=deferred(),gatePartition:()=>partitionGate=deferred(),gateDiscovery:()=>discoveryGate=deferred(),gateApplyDiscovery:()=>applyDiscoveryGate=deferred(),gatePreview:()=>previewGate=deferred(),failPreviews:n=>{previewFailures=n},gateBodyFit:()=>bodyFitGate=deferred(),gateBodyFitPreview:()=>bodyFitPreviewGate=deferred(),gateBodyFitApproval:()=>bodyFitApprovalGate=deferred(),failBodyFitPreviews:n=>{bodyFitPreviewFailures=n},bodyFitReport:value=>{bodyFitOverride=clone(value)},wrongBodyFitDigest:value=>{wrongBodyFitDigest=value},bodyFitSoftware:value=>{bodyFitSoftware=value},gateShellReview:()=>shellReviewGate=deferred(),gateShellPreview:()=>shellPreviewGate=deferred(),gateShellApproval:()=>shellApprovalGate=deferred(),failShellPreviews:n=>{shellPreviewFailures=n},shellReport:value=>{shellReportOverride=clone(value)},shellPreview:value=>{shellPreviewOverride=clone(value)},wrongShellDigest:value=>{wrongShellDigest=value},shellSoftware:value=>{shellSoftware=value},nativeShellGeometry:value=>{nativeShellGeometry=value}};
}
async function mechanicalEnvironment(software=false){
 const env=environment();await tick();const value=mechanicalFixture();env.records.set(value.id,value);env.context.mechanical=value;
 if(software)env.run('viewer.gl=null');await env.run('loadProject(mechanical)');env.run("selectPart('brake_group')");return env;
}
async function tyreEnvironment(software=false){
 const env=environment();await tick();const value=fixture('tyres','One group containing four tyres'),base=value.inspection.parts[0];
 value.inspection.parts=[{...base,id:'tyre_group',name:'Combined tyres',role:'body',corner:null,triangles:8},{...base,id:'shell',name:'Body shell'}];
 value.inspection.role_counts={body:2};value.part_count=2;value.triangles=9;value.wheel_partitions=[];value.approved_roles=true;value.installation={mode:'replace',replace_car_id:82};
 value.import_readiness=readiness([{code:'wheel_layout',title:'Review the four tyre positions and scale',detail:'Assign four tyre corners.',action:'review_wheels',part_ids:['tyre_group']}]);
 env.context.tyres=value;env.records.set(value.id,value);if(software)env.run('viewer.gl=null');await env.run('loadProject(tyres)');env.run("selectPart('tyre_group')");return env;
}
async function bodyFitEnvironment(software=false){
 const env=environment();await tick();const value=fixture('fit_source','Player custom car');
 value.source_sha256='a'.repeat(64);value.installation={mode:'new_entry',replace_car_id:null};value.approved_roles=true;
 value.body_fit_status={approved:false,reason:'not_selected',policy:null,review_digest:null};value.import_readiness=readiness();
 env.context.fitSource=value;env.records.set(value.id,value);env.bodyFitSoftware(software);await env.run('loadProject(fitSource)');env.run("page('export')");return env;
}
async function requestBodyFit(env,policy='adapt_body'){
 env.get('bodyFitPolicy').value=policy;env.get('bodyFitPolicy').onchange();await env.get('reviewBodyFit').onclick();
}
async function main(){
 const checks=[];const env=environment();await tick();await env.run('loadProject(fixture)');
 const {get,document,calls,records}=env;
 // Reproduce the observed Windows order: mode save remains in flight while
 // the player types a new name, before blur/change has happened.
 const modeGate=env.gateSettings();get('installMode').value='new_entry';const modeSave=get('installMode').onchange();
 await until(()=>calls.some(c=>c.url==='/api/settings'),'mode settings request');
 document.activeElement=get('carName');get('carName').value='Garage Generic Supra Check';get('carName').oninput();
 modeGate.resolve();await modeSave;
 assert.equal(get('carName').value,'Garage Generic Supra Check');assert.equal(env.run('project.name'),'Garage Generic Supra Check');
 assert.equal(records.get('a').name,'1997_toyota_supra_rz');
 // Plan click deliberately occurs without blur. It must flush the draft.
 await get('reviewCarPlan').onclick();assert.equal(records.get('a').name,'Garage Generic Supra Check');
 assert.equal(get('buildCar').disabled,false);await get('buildCar').onclick();
 assert.equal([...env.jobs.values()].at(-1).project.name,'Garage Generic Supra Check');
 checks.push('Delayed mode response preserves focused unsent name; plan flushes it and build uses exact typed name');
 // Two rapid saves cannot POST concurrently or let an earlier full project
 // response overwrite the later draft.
 document.activeElement=null;const firstGate=env.gateSettings();const count=calls.filter(c=>c.url==='/api/settings').length;
 env.context.change1={name:'First name'};const first=env.run('settings(change1)');await until(()=>calls.filter(c=>c.url==='/api/settings').length===count+1,'first serialized save');
 env.context.change2={name:'Final name',approved_roles:true};const second=env.run('settings(change2)');await tick();
 assert.equal(calls.filter(c=>c.url==='/api/settings').length,count+1);firstGate.resolve();await Promise.all([first,second]);
 assert.equal(records.get('a').name,'Final name');assert.equal(records.get('a').approved_roles,true);assert.equal(env.run('project.name'),'Final name');assert.equal(env.maxActive(),1);
 checks.push('Rapid settings requests serialize and final name/approval survive older responses');
 // A plan/build snapshot remains stable while it runs. The user may draft
 // another edit, but the transport waits for the terminal job state.
 const planGate=env.gateJob();const plan=get('reviewCarPlan').onclick();await until(()=>env.run('currentJob!==null'),'active plan job');
 const before=calls.filter(c=>c.url==='/api/settings').length;get('carName').value='Edited during plan';document.activeElement=get('carName');get('carName').oninput();const pendingName=get('carName').onchange();await tick();
 assert.equal(calls.filter(c=>c.url==='/api/settings').length,before);assert.equal(get('buildCar').disabled,true);
 planGate.resolve();await plan;await pendingName;assert.equal(records.get('a').name,'Edited during plan');assert.equal(get('buildCar').disabled,true);
 await get('reviewCarPlan').onclick();await get('buildCar').onclick();assert.equal([...env.jobs.values()].at(-1).project.name,'Edited during plan');
 checks.push('Edits during plan wait for job completion, invalidate stale approval and reach the next reviewed build');
 // Native build, not just planning, keeps source-changing writes queued.
 await get('reviewCarPlan').onclick();const buildGate=env.gateJob();const building=get('buildCar').onclick();await until(()=>env.run('currentJob!==null'),'active build job');
 const beforeBuildEdits=calls.filter(c=>c.url==='/api/settings').length;get('carName').value='Next car name';get('carName').oninput();const nextName=get('carName').onchange();await tick();
 assert.equal(calls.filter(c=>c.url==='/api/settings').length,beforeBuildEdits);assert.equal([...env.jobs.values()].at(-1).project.name,'Edited during plan');
 buildGate.resolve();await building;await nextName;assert.equal(records.get('a').name,'Next car name');
 checks.push('Active native build keeps its original snapshot while subsequent edits remain queued');
 // Switching project must refresh even a still-focused field, as headless
 // file selection can leave the old name input focused.
 const other=fixture('b','Different project');records.set('b',other);env.context.other=other;await env.run('loadProject(other)');
 assert.equal(get('carName').value,'Different project');assert.equal(env.run('project.id'),'b');
 checks.push('New project bind refreshes focused controls instead of retaining the previous car name');
 // Configuration changes cannot overtake an active conversion or plan.
 const configGate=env.gateJob();const planning=get('reviewCarPlan').onclick();await until(()=>env.run('currentJob!==null'),'plan before configuration');
 const configCount=calls.filter(c=>c.url==='/api/pick').length;const configure=get('pickCarBaseline').onclick();await tick();assert.equal(calls.filter(c=>c.url==='/api/pick').length,configCount);
 configGate.resolve();await planning;await configure;assert.equal(calls.filter(c=>c.url==='/api/pick').length,configCount+1);
 checks.push('Baseline changes wait until the active job reaches a terminal state');
 // Direct coordinator context test covers a late response after a genuinely
 // different project is bound, independent of the UI navigation wait.
 const coordinatorSource=source.split('// SETTINGS_COORDINATOR_BEGIN')[1].split('// SETTINGS_COORDINATOR_END')[0];
 const Coordinator=vm.runInNewContext(coordinatorSource+';SettingsCoordinator');const gate=deferred(),published=[];
 const queue=new Coordinator(async(id,changes)=>{await gate.promise;return {id,name:changes.name}},async p=>published.push(p));
 queue.bind({id:'old',name:'Old'});queue.stage({name:'Old edited'});const obsolete=queue.save();await tick();queue.bind({id:'new',name:'New'});gate.resolve();await obsolete;
 assert.equal(queue.snapshot().id,'new');assert.equal(queue.snapshot().name,'New');assert.equal(published.length,0);
 checks.push('Late response for an old project cannot publish into a newly bound project');
 // A transient status failure must not abandon the watcher or unlock model
 // writes while the server job could still be running.
 const retryGate=env.gateJob();env.failPolls(1);const reconnecting=get('reviewCarPlan').onclick();
 await until(()=>get('jobstatus').textContent.includes('connection interrupted'),'polling failure status');
 const reconnectCount=calls.filter(c=>c.url==='/api/settings').length;get('carName').value='After reconnect';get('carName').oninput();const queuedReconnect=get('carName').onchange();await tick();
 assert.notEqual(env.run('activeUiJob'),null);assert.equal(calls.filter(c=>c.url==='/api/settings').length,reconnectCount);
 retryGate.resolve();await reconnecting;await queuedReconnect;assert.equal(records.get('b').name,'After reconnect');assert.equal(env.run('activeUiJob'),null);
 checks.push('Transient job polling failure reconnects, preserves the mutation barrier and then releases queued edits');
 // Navigation intent starts before GET, not after its delayed response.
 const slowProject=env.gateProject('a');const getsBefore=calls.filter(c=>c.url==='/api/project/a').length;
 const earlier=env.run("openProject('a')");await until(()=>calls.filter(c=>c.url==='/api/project/a').length>getsBefore,'earlier project GET');
 await env.run("openProject('b')");slowProject.resolve();await earlier;
 assert.equal(env.run('project.id'),'b');assert.equal(get('carName').value,'After reconnect');
 checks.push('Earlier slow project GET cannot override a later project-card navigation intent');
 // Native Open Model uses the same barrier for cancellation, errors and a
 // successful import handoff, instead of leaving an unowned server import.
 env.picker({cancelled:true});await env.run('pickModel()');assert.equal(env.run('activeUiJob'),null);
 env.picker(Error('Native picker failed'));await assert.rejects(env.run('pickModel()'),/Native picker failed/);assert.equal(env.run('activeUiJob'),null);
 env.context.pickerRecovery={name:'Picker recovered'};await env.run('settings(pickerRecovery)');assert.equal(records.get('b').name,'Picker recovered');
 const imported=fixture('native','Native opened model');records.set('native',imported);env.jobs.set('native-import',{id:'native-import',state:'complete',result:imported,gate:null,project:imported});
 env.picker({id:'native-import',label:'Read native-picked source',state:'running'});await env.run('pickModel()');assert.equal(env.run('project.id'),'native');assert.equal(env.run('activeUiJob'),null);
 checks.push('Native picker cancellation/error release the barrier and successful picker import transfers to the watched job');
 // Failed mutation or preview publication reports the failure without
 // poisoning all subsequent flush/navigation operations.
 let publicationCalls=0;const recoverable=new Coordinator(async(id,changes)=>({id,name:changes.name}),async()=>{if(++publicationCalls===1)throw Error('Preview unavailable')});
 recoverable.bind({id:'recovery',name:'Original'});await assert.rejects(recoverable.mutate(async()=>{throw Error('Recipe rejected')}),/Recipe rejected/);await recoverable.flush();
 recoverable.stage({name:'Saved before preview failure'});await assert.rejects(recoverable.save(),/Preview unavailable/);await recoverable.flush();assert.equal(recoverable.snapshot().name,'Saved before preview failure');
 recoverable.stage({name:'Successful retry'});await recoverable.save();await recoverable.flush();assert.equal(recoverable.snapshot().name,'Successful retry');
 checks.push('Failed mutation and preview publication preserve caller errors while allowing later flush and successful retry');
 // Coincident wheel centres do not justify a general tyre-fit claim. Keep
 // per-corner measured sizes visible without inventing a fit cutoff.
 env.tyreFit({status:'measured',corners:['FL','FR','RL','RR'].map(corner=>({corner,available:true,body:{radius_metres:.326,width_metres:.243},native:{radius_metres:.322,width_metres:.235}}))});
 await get('reviewCarPlan').onclick();assert.match(get('carPlan').textContent,/FL: 326\.0 × 243\.0 \/ 322\.0 × 235\.0/);assert.match(get('carPlan').textContent,/RR:/);assert.match(get('carPlan').textContent,/Matching wheel centres does not establish tyre compatibility/);assert.equal(get('buildCar').disabled,false);
 env.tyreFit({status:'incomplete',corners:[{corner:'FL',available:false,body:null,native:{radius_metres:.322,width_metres:.235}}]});
 await get('reviewCarPlan').onclick();assert.match(get('carPlan').textContent,/FL: Measurement incomplete/);assert.doesNotMatch(get('carPlan').textContent,/NaN|undefined/);
 checks.push('Build plan shows per-corner tyre dimensions and incomplete measurements without claiming compatibility');
 const destination=clone(records.get('native').installation);
 env.orientation({review_required:true,normal_disagreement_parts:1,double_sided_parts:1,parts:[{part:'valve',name:'Right valve',role:'valve',corner:'FR',normal_disagreement:true,source_double_sided:true,source_normal_winding:{opposed_triangles:378,assessed_triangles:378}}]});
 await get('reviewCarPlan').onclick();assert.match(get('carPlan').textContent,/FR valve \(Right valve\): normals oppose 378 of 378 assessed faces/);assert.match(get('carPlan').textContent,/native two-sided rendering still needs verification/);assert.equal(get('buildCar').disabled,false);assert.deepEqual(records.get('native').installation,destination);
 checks.push('Build plan exposes source wheel normal conflicts and two-sided materials while retaining the selected destination');
 // Reference typing is a draft. The explicit calibration waits for saved
 // settings, then refreshes source geometry and invalidates prior approval.
 const refBefore=records.get('native').metres_per_unit;
 get('wheelbase').value='2.55';get('wheelbase').oninput();
 assert.equal(records.get('native').metres_per_unit,refBefore);
 const calibrationGate=env.gateCalibration(),previewCount=calls.filter(c=>c.url.startsWith('/preview/')).length;
 const calibrating=get('calibrateUnits').onclick();await until(()=>calls.some(c=>c.url==='/api/calibrate-units'),'calibration request');
 assert.equal(records.get('native').wheelbase_metres,2.55);
 assert.equal(get('confirmParts').disabled,true);get('confirmParts').checked=true;await get('confirmParts').onchange();
 assert.equal(get('confirmParts').checked,false);
 get('carName').value='Name typed during calibration';get('carName').oninput();const calibrationName=get('carName').onchange();
 calibrationGate.resolve();await calibrating;await calibrationName;
 assert.equal(records.get('native').metres_per_unit,refBefore*2.55/2.5);
 assert.equal(records.get('native').approved_roles,false);assert.equal(records.get('native').name,'Name typed during calibration');
 assert.equal(env.run('project.metres_per_unit'),records.get('native').metres_per_unit);assert.equal(get('buildCar').disabled,true);
 assert.ok(calls.filter(c=>c.url.startsWith('/preview/')).length>previewCount);assert.deepEqual(records.get('native').installation,destination);
 checks.push('Explicit wheelbase calibration flushes reference drafts, refreshes geometry and preserves later edits and destination');
 assert.equal(get('confirmParts').disabled,false);assert.equal(get('confirmParts').checked,false);
 checks.push('Calibration blocks approval of the old preview until refreshed geometry is displayed');
 const unchangedPreviewCount=calls.filter(c=>c.url.startsWith('/preview/')).length;
 await get('calibrateUnits').onclick();
 assert.ok(calls.filter(c=>c.url.startsWith('/preview/')).length>unchangedPreviewCount);
 assert.equal(records.get('native').approved_roles,false);
 checks.push('Explicit calibration refreshes the displayed preview even when the resulting units are unchanged');
 env.failPreviews(1);await get('calibrateUnits').onclick();assert.equal(get('confirmParts').disabled,true);assert.equal(get('calibrateUnits').disabled,false);
 assert.match(env.errors.at(-1),/Simulated prepared preview failure/);env.errors.pop();
 get('confirmParts').checked=true;await get('confirmParts').onchange();assert.equal(records.get('native').approved_roles,false);
 await get('calibrateUnits').onclick();assert.equal(get('confirmParts').disabled,false);assert.equal(get('confirmParts').checked,false);
 checks.push('Failed calibrated preview keeps confirmation blocked until a successful refresh');
 // Calibration response must not leak into a newly bound project.
 const lateCalibration=deferred(),calibrationPublications=[];
 const calibrationQueue=new Coordinator(async(id,changes)=>({id,...changes}),async value=>calibrationPublications.push(value));
 calibrationQueue.bind({id:'first',metres_per_unit:1});const late=calibrationQueue.mutate(async id=>{await lateCalibration.promise;return {id,metres_per_unit:.01}});await tick();
 calibrationQueue.bind({id:'second',metres_per_unit:2});lateCalibration.resolve();await late;
 assert.equal(calibrationQueue.snapshot().id,'second');assert.equal(calibrationQueue.snapshot().metres_per_unit,2);assert.equal(calibrationPublications.length,0);
 checks.push('Late calibration response cannot change units or publish a preview into another project');
 const comparison={available:true,source:{dimensions:{wheelbase_metres:.255,front_track_metres:.153,rear_track_metres:.154},entered_wheelbase:{metres:2.55,measured_prepared_metres:.255}},target:{dimensions:{wheelbase_metres:2.55,front_track_metres:1.536,rear_track_metres:1.536},target_y_known:true},rigid_fit:{maximum_corner_error_metres:.0988}};
 const nominal=clone(comparison);nominal.rigid_fit=null;nominal.planar_fit={maximum_corner_error_metres:.0988};nominal.target.target_y_known=false;
 const tyre={section_width_metres:.225,aspect_percent:45,rim_diameter_inches:18,outer_radius_metres:.32985};
 env.geometry({fit:{uniform_scale:10,lateral_fit_ratio:1,assembly:comparison},nominal,target:{target:{car_id:76,label:'Selected target'},physical:{front_tyre:{nominal:tyre},rear_tyre:{nominal:tyre}},issues:[{message:'Stock visual tyre dimensions differ from physical specifications.'}]}});
 await get('reviewCarPlan').onclick();const displayed=get('carPlan').textContent;
 assert.match(displayed,/Applied profile body scale: 10\.00000×/);assert.match(displayed,/Wheelbase 0\.255 \/ 2\.550/);assert.match(displayed,/98\.8 mm maximum corner residual/);
 assert.match(displayed,/225\/45 R18/);assert.match(displayed,/horizontal comparison only/);assert.match(displayed,/Stock visual tyre dimensions differ/);assert.doesNotMatch(displayed,/NaN|undefined/);
 checks.push('Plan separates measured units, applied profile scale, rigid residuals and physical versus visual target data');
 // Exercise the actual list and actual Viewer.visible implementation, while
 // the DOM harness continues to avoid requiring a browser/GPU.
 const visible=await mechanicalEnvironment();
 assert.ok(visible.get('partRole').children.some(option=>option.value==='brake_rotor'&&option.textContent.includes('spins')));
 assert.ok(visible.get('partRole').children.some(option=>option.value==='brake_caliper'&&option.textContent.includes('does not spin')));
 assert.match(visible.get('selectedReason').textContent,/Review the brake surfaces/);
 visible.get('roleFilter').value='wheels';visible.get('roleFilter').onchange();assert.equal(visible.get('partList').children.length,5);
 const viewerSource=fs.readFileSync(require('node:path').join(require('node:path').dirname(sourcePath),'viewer.js'),'utf8');
 const ActualViewer=vm.runInNewContext(viewerSource.replace('export class Viewer','class Viewer')+';Viewer');
 const visibilityState={parts:{rotor:{role:'brake_rotor'},caliper:{role:'brake_caliper'},body:{role:'body'},ignored:{role:'ignore'}},onlyWheels:true,isolated:null};
 assert.equal(ActualViewer.prototype.visible.call(visibilityState,{id:'rotor'}),true);assert.equal(ActualViewer.prototype.visible.call(visibilityState,{id:'caliper'}),true);
 assert.equal(ActualViewer.prototype.visible.call(visibilityState,{id:'body'}),false);assert.equal(ActualViewer.prototype.visible.call(visibilityState,{id:'ignored'}),false);
 checks.push('Brake roles remain visible as reviewable types in wheel lists and the actual viewer filter');
 // A split clicked while its role save is still pending must capture that
 // component/role, serialize the write and refuse duplicate submissions.
 const splitting=await mechanicalEnvironment(),splitSettings=splitting.gateSettings(),splitGate=splitting.gatePartition();
 const splitDestination=clone(splitting.records.get('mechanical').installation);
 splitting.get('partRole').value='brake_caliper';const savedRole=splitting.get('partRole').onchange();
 await until(()=>splitting.calls.some(call=>call.url==='/api/settings'),'role save before partition');
 const splitRequest=splitting.get('partitionWheelGroup').onclick();await tick();
 assert.equal(splitting.get('partitionWheelGroup').disabled,true);assert.equal(splitting.get('confirmParts').disabled,true);
 await splitting.get('partitionWheelGroup').onclick();assert.equal(splitting.calls.filter(call=>call.url==='/api/partition-wheel-group').length,0);
 splitting.get('confirmParts').checked=true;await splitting.get('confirmParts').onchange();assert.equal(splitting.get('confirmParts').checked,false);
 splitting.run("selectPart('shell')");splitSettings.resolve();await savedRole;
 await until(()=>splitting.calls.some(call=>call.url==='/api/partition-wheel-group'),'serialized partition request');
 const sentSplit=splitting.calls.find(call=>call.url==='/api/partition-wheel-group');assert.deepEqual(sentSplit.data,{id:'mechanical',part_id:'brake_group',role:'brake_caliper'});
 assert.equal(splitting.records.get('mechanical').overrides.brake_group.role,'brake_caliper');assert.equal(splitting.records.get('mechanical').approved_roles,false);
 const splitPreview=splitting.gatePreview(),loadedBefore=splitting.viewerLoads.length;splitGate.resolve();
 await until(()=>splitting.calls.filter(call=>call.url.startsWith('/preview/')).length===2,'partition preview refresh');
 assert.equal(splitting.get('confirmParts').disabled,true);assert.equal(splitting.viewerLoads.length,loadedBefore);
 splitPreview.resolve();await splitRequest;
 assert.equal(splitting.calls.filter(call=>call.url==='/api/partition-wheel-group').length,1);assert.equal(splitting.run('selected'),'shell');
 assert.equal(splitting.get('confirmParts').disabled,false);assert.equal(splitting.get('confirmParts').checked,false);
 assert.deepEqual(splitting.records.get('mechanical').installation,splitDestination);assert.equal(splitting.get('buildCar').disabled,true);assert.deepEqual(splitting.errors,[]);
 checks.push('Wheel partition flushes role edits, blocks duplicate actions and approval, and keeps the captured component and destination');
 // Both rendering modes need the new mesh IDs even though axes/units did
 // not change. An isolated parent no longer exists after the split.
 for(const software of [false,true]){
  const mode=await mechanicalEnvironment(software),beforeUnits=mode.records.get('mechanical').metres_per_unit;
  mode.get('isolate').onclick();assert.equal(mode.run('viewer.isolated'),'brake_group');const loaded=mode.viewerLoads.length;mode.document.activeElement=mode.get('partCorner');
  await mode.get('partitionWheelGroup').onclick();
  assert.equal(mode.viewerLoads.length,loaded+1);assert.equal(mode.viewerLoads.at(-1).software,software);
  assert.equal(mode.calls.filter(call=>call.url.startsWith('/preview/')).at(-1).url,'/preview/mechanical'+(software?'?quality=software':''));
  assert.ok(mode.viewerLoads.at(-1).parts.includes('brake_group__wheel_FL'));assert.ok(!mode.viewerLoads.at(-1).parts.includes('brake_group'));
  assert.equal(mode.records.get('mechanical').metres_per_unit,beforeUnits);assert.equal(mode.run('selected'),'brake_group__wheel_FL');assert.equal(mode.get('partCorner').value,'FL');
  assert.equal(mode.run('viewer.isolated'),null);assert.equal(mode.get('isolate').textContent,'Isolate selected part');
  assert.equal(mode.get('partitionWheelGroup').disabled,true);assert.equal(mode.get('confirmParts').checked,false);assert.deepEqual(mode.errors,[]);
 }
 checks.push('Wheel partition refreshes WebGL and software previews with unchanged units and clears obsolete selection and isolation');
 const rejected=await mechanicalEnvironment(),rejection=rejected.gatePartition();
 const rejectedRequest=rejected.get('partitionWheelGroup').onclick();await until(()=>rejected.calls.some(call=>call.url==='/api/partition-wheel-group'),'rejected partition request');
 rejection.reject(Error('Triangle crosses corner ownership'));await rejectedRequest;
 assert.ok(rejected.records.get('mechanical').inspection.parts.some(part=>part.id==='brake_group'));
 assert.equal(rejected.records.get('mechanical').wheel_partitions.length,0);assert.equal(rejected.get('confirmParts').disabled,true);
 assert.equal(rejected.run('preparationPending'),null);assert.match(rejected.errors.pop(),/Triangle crosses corner ownership/);
 await rejected.run("openProject('mechanical')");assert.equal(rejected.get('confirmParts').disabled,false);assert.equal(rejected.get('confirmParts').checked,false);assert.deepEqual(rejected.errors,[]);
 checks.push('Rejected wheel partition preserves source groups and blocks confirmation until the project is refreshed');
 const lostPreview=await mechanicalEnvironment();lostPreview.failPreviews(1);const oldMeshIds=lostPreview.run('Object.keys(viewer.parts)');
 await lostPreview.get('partitionWheelGroup').onclick();
 assert.equal(lostPreview.records.get('mechanical').part_count,9);assert.equal(lostPreview.get('confirmParts').disabled,true);
 assert.equal(lostPreview.run('selected'),null);assert.deepEqual(lostPreview.run('Object.keys(viewer.parts)'),oldMeshIds);
 assert.match(lostPreview.get('previewLabel').textContent,/NEEDS REFRESH/);assert.match(lostPreview.errors.pop(),/Simulated prepared preview failure/);
 lostPreview.get('confirmParts').checked=true;await lostPreview.get('confirmParts').onchange();assert.equal(lostPreview.records.get('mechanical').approved_roles,false);
 await lostPreview.run("openProject('mechanical')");assert.ok(lostPreview.viewerLoads.at(-1).parts.includes('brake_group__wheel_FL'));
 assert.equal(lostPreview.get('confirmParts').disabled,false);assert.equal(lostPreview.get('confirmParts').checked,false);assert.deepEqual(lostPreview.errors,[]);
 checks.push('Failed partition preview cannot approve stale geometry and reopening displays the saved children');
 // Force a new binding at the response boundary to exercise the actual
 // operation's publication/finally guards, including an older failure.
 for(const fails of [false,true]){
  const stale=await mechanicalEnvironment(),gate=stale.gatePartition();const operation=stale.get('partitionWheelGroup').onclick();
  await until(()=>stale.calls.some(call=>call.url==='/api/partition-wheel-group'),'old partition request');
  const current=mechanicalFixture('other');stale.context.otherMechanical=current;stale.records.set(current.id,current);
  stale.run('project=editState.bind(otherMechanical);selected=null;preparationPending={context:editState.context,kind:"calibration"};previewUnverifiedContext=editState.context;renderProject()');
  const newContext=stale.run('editState.context'),previewBefore=stale.calls.filter(call=>call.url.startsWith('/preview/')).length;
  if(fails)gate.reject(Error('Older partition failed'));else gate.resolve();await operation;
  assert.equal(stale.run('project.id'),'other');assert.equal(stale.run('preparationPending.context'),newContext);
  assert.equal(stale.run('previewUnverifiedContext'),newContext);assert.equal(stale.get('confirmParts').disabled,true);
  assert.equal(stale.calls.filter(call=>call.url.startsWith('/preview/')).length,previewBefore);
  if(fails)assert.match(stale.errors.pop(),/Older partition failed/);assert.deepEqual(stale.errors,[]);
 }
 checks.push('Late wheel partition success or failure cannot change another project or clear its preparation guard');
 const invalidSplit=await mechanicalEnvironment();invalidSplit.get('partRole').value='body';await invalidSplit.get('partRole').onchange();
 assert.equal(invalidSplit.get('partitionWheelGroup').disabled,true);await invalidSplit.get('partitionWheelGroup').onclick();assert.match(invalidSplit.errors.pop(),/Choose a rim/);
 invalidSplit.get('partRole').value='brake_rotor';await invalidSplit.get('partRole').onchange();
 invalidSplit.run('delete project.inspection.wheel_centers.RR;updatePartitionControl()');assert.equal(invalidSplit.get('partitionWheelGroup').disabled,true);
 await invalidSplit.get('partitionWheelGroup').onclick();assert.match(invalidSplit.errors.pop(),/Assign all four tyre groups/);
 assert.equal(invalidSplit.calls.filter(call=>call.url==='/api/partition-wheel-group').length,0);
 const childSplit=await mechanicalEnvironment();await childSplit.get('partitionWheelGroup').onclick();await childSplit.get('partitionWheelGroup').onclick();
 assert.equal(childSplit.calls.filter(call=>call.url==='/api/partition-wheel-group').length,1);assert.match(childSplit.errors.pop(),/already belongs to a saved corner partition/);
 checks.push('Wheel partition refuses unsupported roles, incomplete tyre groups and already split children');
 // The source guide is available before a game template is configured and
 // preserves the player's explicit, initially empty installation choice.
 const guide=await mechanicalEnvironment();guide.appState.car_baseline=null;guide.appState.car_packer=null;
 guide.context.sourceAdvice=readiness([{code:'mechanical_corners',title:'Assign the brake components to their wheels',detail:'Review this group before splitting.',action:'review_parts',part_ids:['brake_group']}],[{code:'mechanical_native_route',title:'Custom brakes need a supported native vehicle profile',detail:'The current profile cannot build custom brakes.',action:'save_project',part_ids:['brake_group']}]);
 guide.run('project.import_readiness=sourceAdvice;project.installation={mode:null,replace_car_id:null};renderProject()');await guide.run('refresh()');
 assert.match(guide.get('guideTitle').textContent,/brake components/);assert.equal(guide.get('guideCapability').classList.contains('hidden'),false);
 assert.match(guide.get('guideCapabilityDetail').textContent,/native vehicle profile/);assert.equal(guide.get('buildCar').disabled,true);
 const settingsBefore=guide.calls.filter(call=>call.url==='/api/settings').length;await guide.get('guideAction').onclick();
 assert.equal(guide.run('selected'),'brake_group');assert.equal(guide.calls.filter(call=>call.url==='/api/settings').length,settingsBefore);
 assert.equal(guide.run('project.installation.mode'),null);assert.equal(guide.calls.some(call=>['/api/car-plan','/api/pick'].includes(call.url)),false);
 guide.context.sourceAdvice=readiness();guide.run('project.import_readiness=sourceAdvice;renderImportGuide()');
 assert.equal(guide.get('guideAction').dataset.action,'choose_destination');await guide.get('guideAction').onclick();assert.equal(guide.get('installMode').focused,true);
 assert.equal(guide.run('project.installation.mode'),null);guide.run('project.installation={mode:"new_entry",replace_car_id:null};renderImportGuide()');
 assert.equal(guide.get('guideAction').dataset.action,'choose_baseline');await guide.get('guideAction').onclick();assert.equal(guide.get('gameSetup').open,true);
 assert.equal(guide.calls.some(call=>call.url==='/api/pick'),false);assert.equal(guide.get('buildCar').disabled,true);
 checks.push('Early source guide separates model tasks from native limits before setup and preserves explicit destination choice');
 // A saved summary is stale while new inputs are queued. An older button
 // closure must not navigate or mutate a newly bound project's review.
 const draftGuide=environment();await tick();await draftGuide.run('loadProject(fixture)');const oldAction=draftGuide.get('guideAction').onclick;
 const saveGate=draftGuide.gateSettings();draftGuide.get('carName').value='Unsent source change';draftGuide.get('carName').oninput();
 assert.equal(draftGuide.get('guideAction').dataset.action,'save_changes');const pendingSave=draftGuide.get('carName').onchange();
 await until(()=>draftGuide.calls.some(call=>call.url==='/api/settings'),'guide pending save');assert.match(draftGuide.get('guideTitle').textContent,/Saving/);assert.equal(draftGuide.get('guideAction').disabled,true);
 const newer=fixture('new_guide','New source');newer.import_readiness=readiness([],[{code:'alpha_cutout',title:'New source material needs native support',detail:'Keep the authored cutout material.',action:'save_project',part_ids:[]}]);draftGuide.context.newGuide=newer;
 draftGuide.run('project=editState.bind(newGuide);previewUnverifiedContext=null;renderProject()');saveGate.resolve();await pendingSave;await oldAction();
 assert.equal(draftGuide.get('guideAction').dataset.projectId,'new_guide');assert.equal(draftGuide.get('guideAction').dataset.action,'save_project');
 assert.match(draftGuide.get('guideCapabilityDetail').textContent,/New source material/);assert.equal(draftGuide.run('project.id'),'new_guide');assert.deepEqual(draftGuide.errors,[]);
 checks.push('Source guide pauses during saved changes and old actions or delayed responses cannot replace another project’s advice');
 // Generic grouped tyres do not have to depend on pre-existing tyre centres.
 // The actual handler saves the explicit role, proposes without changing the
 // project, and requires a separate reviewed application.
 for(const software of [false,true]){
  const tyres=await tyreEnvironment(software);assert.equal(tyres.get('discoverTyres').disabled,true);assert.equal(tyres.run('hasFourTyreGroups()'),false);
  const roleGate=tyres.gateSettings();tyres.get('partRole').value='tyre';const roleSave=tyres.get('partRole').onchange();
  await until(()=>tyres.calls.some(call=>call.url==='/api/settings'),'tyre role save');const discovering=tyres.get('discoverTyres').onclick();await tick();
  assert.equal(tyres.calls.filter(call=>call.url==='/api/discover-tyres').length,0);roleGate.resolve();await roleSave;await discovering;
  assert.equal(tyres.records.get('tyres').part_count,2);assert.equal(tyres.get('tyreProposalCorners').children.length,4);assert.equal(tyres.get('applyTyreDiscovery').disabled,true);
  await tyres.get('applyTyreDiscovery').onclick();assert.match(tyres.errors.pop(),/Confirm this group/);assert.equal(tyres.calls.some(call=>call.url==='/api/apply-tyre-discovery'),false);
  tyres.get('confirmTyreOnly').checked=true;tyres.get('confirmTyreOnly').onchange();assert.equal(tyres.get('applyTyreDiscovery').disabled,false);
  const previewCount=tyres.calls.filter(call=>call.url.startsWith('/preview/')).length,applyGate=tyres.gateApplyDiscovery();const applying=tyres.get('applyTyreDiscovery').onclick();
  await until(()=>tyres.calls.some(call=>call.url==='/api/apply-tyre-discovery'),'tyre apply');assert.equal(tyres.get('confirmParts').disabled,true);await tyres.get('applyTyreDiscovery').onclick();
  assert.equal(tyres.calls.filter(call=>call.url==='/api/apply-tyre-discovery').length,1);applyGate.resolve();await applying;
  assert.equal(tyres.records.get('tyres').part_count,5);assert.equal(tyres.records.get('tyres').approved_roles,false);assert.equal(tyres.run('hasFourTyreGroups()'),true);
  assert.equal(tyres.calls.filter(call=>call.url.startsWith('/preview/')).length,previewCount+1);assert.equal(tyres.viewerLoads.at(-1).software,software);
  assert.equal(tyres.run('selected'),'tyre_group__wheel_FL');assert.equal(tyres.get('partCorner').value,'FL');assert.equal(tyres.run('project.installation.replace_car_id'),82);
  assert.equal(tyres.get('confirmParts').disabled,false);assert.equal(tyres.get('confirmParts').checked,false);assert.deepEqual(tyres.errors,[]);
 }
 checks.push('Grouped tyre discovery flushes explicit roles, requires reviewed confirmation and refreshes both preview paths without choosing a destination');
 for(const change of ['draft','project','selection']){
  const staleTyres=await tyreEnvironment();staleTyres.get('partRole').value='tyre';await staleTyres.get('partRole').onchange();const gate=staleTyres.gateDiscovery();const requesting=staleTyres.get('discoverTyres').onclick();
  await until(()=>staleTyres.calls.some(call=>call.url==='/api/discover-tyres'),'late tyre proposal');
  if(change==='draft'){staleTyres.get('carName').value='Changed source review';staleTyres.get('carName').oninput()}
  if(change==='project'){staleTyres.context.other=fixture('different','Other project');staleTyres.run('project=editState.bind(other);selected=null;renderProject()')}
  if(change==='selection')staleTyres.run("selectPart('shell')");gate.resolve();await requesting;
  assert.equal(staleTyres.run('tyreProposal'),null);assert.equal(staleTyres.get('applyTyreDiscovery').disabled,true);assert.equal(staleTyres.calls.some(call=>call.url==='/api/apply-tyre-discovery'),false);assert.deepEqual(staleTyres.errors,[]);
 }
 checks.push('Tyre proposals are discarded after draft changes, component selection or project switches');
 for(const fails of [false,true]){
  const staleApply=await tyreEnvironment();staleApply.get('partRole').value='tyre';await staleApply.get('partRole').onchange();await staleApply.get('discoverTyres').onclick();
  staleApply.get('confirmTyreOnly').checked=true;const gate=staleApply.gateApplyDiscovery(),applying=staleApply.get('applyTyreDiscovery').onclick();
  await until(()=>staleApply.calls.some(call=>call.url==='/api/apply-tyre-discovery'),'stale tyre apply');
  staleApply.context.other=fixture('newer','Newer project');staleApply.run('project=editState.bind(other);selected=null;preparationPending={context:editState.context,kind:"calibration"};previewUnverifiedContext=editState.context;renderProject()');
  const currentContext=staleApply.run('editState.context'),previews=staleApply.calls.filter(call=>call.url.startsWith('/preview/')).length;
  if(fails)gate.reject(Error('Old tyre apply rejected'));else gate.resolve();await applying;
  assert.equal(staleApply.run('project.id'),'newer');assert.equal(staleApply.run('preparationPending.context'),currentContext);assert.equal(staleApply.run('previewUnverifiedContext'),currentContext);
  assert.equal(staleApply.calls.filter(call=>call.url.startsWith('/preview/')).length,previews);assert.equal(staleApply.get('confirmParts').disabled,true);
  if(fails)assert.match(staleApply.errors.pop(),/Old tyre apply rejected/);assert.deepEqual(staleApply.errors,[]);
 }
 checks.push('Late tyre apply success or failure cannot publish geometry or clear another project’s preparation guard');
 const tyrePreview=await tyreEnvironment();tyrePreview.get('partRole').value='tyre';await tyrePreview.get('partRole').onchange();await tyrePreview.get('discoverTyres').onclick();tyrePreview.get('confirmTyreOnly').checked=true;
 tyrePreview.failPreviews(1);await tyrePreview.get('applyTyreDiscovery').onclick();assert.equal(tyrePreview.records.get('tyres').part_count,5);assert.equal(tyrePreview.get('confirmParts').disabled,true);
 assert.equal(tyrePreview.get('guideAction').dataset.action,'refresh_project');assert.match(tyrePreview.errors.pop(),/Simulated prepared preview failure/);
 tyrePreview.get('confirmParts').checked=true;await tyrePreview.get('confirmParts').onchange();assert.equal(tyrePreview.records.get('tyres').approved_roles,false);
 await tyrePreview.get('guideAction').onclick();assert.equal(tyrePreview.get('confirmParts').disabled,false);assert.equal(tyrePreview.get('confirmParts').checked,false);assert.deepEqual(tyrePreview.errors,[]);
 checks.push('Failed tyre preparation preview keeps approval blocked and the guide leads to a safe project refresh');
 // Body fit has an independent preview and never chooses a policy or
 // changes the source viewport while proposing a native body transform.
 for(const software of [false,true]){
  const fit=await bodyFitEnvironment(software),destination=clone(fit.records.get('fit_source').installation);
  assert.equal(fit.get('bodyFitPolicy').value,'');assert.equal(fit.run('bodyFitViewer'),null);assert.equal(fit.get('approveBodyFit').disabled,true);
  assert.equal(fit.get('guideAction').dataset.action,'review_body_fit');await fit.get('guideAction').onclick();assert.equal(fit.get('bodyFitPolicy').focused,true);
  await fit.get('reviewBodyFit').onclick();assert.match(fit.errors.pop(),/Choose a body policy/);assert.equal(fit.calls.some(call=>call.url==='/api/body-fit-review'),false);
  fit.run("selectPart('n0p0');viewer.isolated='n0p0'");const sourceParts=clone(fit.run('Object.keys(viewer.parts)'));
  await requestBodyFit(fit);assert.equal(fit.run('selected'),'n0p0');assert.equal(fit.run('viewer.isolated'),'n0p0');assert.deepEqual(clone(fit.run('Object.keys(viewer.parts)')),sourceParts);
  assert.equal(fit.viewerLoads.filter(row=>row.viewer==='viewer').length,1);assert.equal(fit.viewerLoads.filter(row=>row.viewer==='bodyFitViewer').length,1);
  assert.equal(fit.viewerLoads.at(-1).software,software);assert.equal(fit.get('bodyFitStage').classList.contains('hidden'),false);
  const request=fit.calls.find(call=>call.url.startsWith('/api/body-fit-preview/'));
  assert.match(request.url,/policy=adapt_body&review_digest=b{64}/);assert.equal(request.url.includes('quality=software'),software);
  assert.deepEqual(fit.get('bodyFitMetrics').children.map(item=>item.children[1].textContent),['-4.00%','+1.00%','+1.00%','1.50 mm']);
  assert.equal(fit.get('confirmBodyFit').checked,false);assert.equal(fit.get('approveBodyFit').disabled,true);
  await fit.get('approveBodyFit').onclick();assert.match(fit.errors.pop(),/confirm the measurements/);assert.equal(fit.calls.some(call=>call.url==='/api/approve-body-fit'),false);
  for(const id of ['bodyFitView3d','bodyFitViewSide','bodyFitViewTop'])fit.get(id).onclick();
  assert.deepEqual(fit.viewerViews.map(row=>[row.viewer,row.kind,row.forward]),[['bodyFitViewer','fit','-Z'],['bodyFitViewer','side','-Z'],['bodyFitViewer','top','-Z']]);
  fit.get('confirmBodyFit').checked=true;fit.get('confirmBodyFit').onchange();assert.equal(fit.get('approveBodyFit').disabled,false);await fit.get('approveBodyFit').onclick();
  const approval=fit.calls.find(call=>call.url==='/api/approve-body-fit');assert.deepEqual(approval.data,{id:'fit_source',policy:'adapt_body',review_digest:'b'.repeat(64),confirmed:true});
  assert.equal(fit.records.get('fit_source').body_fit_status.approved,true);assert.deepEqual(fit.records.get('fit_source').installation,destination);
  assert.equal(fit.run('bodyFitReview'),null);assert.equal(fit.get('confirmBodyFit').checked,false);assert.match(fit.get('bodyFitStatus').textContent,/Approved body fit/);
  assert.equal(fit.viewerLoads.filter(row=>row.viewer==='viewer').length,1);assert.equal(fit.get('buildCar').disabled,true);assert.deepEqual(fit.errors,[]);
 }
 checks.push('Body fit keeps explicit policy choice, loads a separate matching WebGL/software preview, discloses scale and residuals, and saves only after confirmation');
 // Approval remains impossible between JSON receipt and binary preview.
 const pendingFit=await bodyFitEnvironment(),fitPreviewGate=pendingFit.gateBodyFitPreview();
 pendingFit.get('bodyFitPolicy').value='adapt_body';pendingFit.get('bodyFitPolicy').onchange();const fitLoading=pendingFit.get('reviewBodyFit').onclick();
 await until(()=>pendingFit.calls.some(call=>call.url.startsWith('/api/body-fit-preview/')),'body preview loading');
 assert.equal(pendingFit.get('confirmBodyFit').disabled,true);assert.equal(pendingFit.get('approveBodyFit').disabled,true);assert.equal(pendingFit.get('bodyFitStage').classList.contains('hidden'),true);
 pendingFit.get('confirmBodyFit').checked=true;await pendingFit.get('approveBodyFit').onclick();await pendingFit.get('reviewBodyFit').onclick();
 assert.equal(pendingFit.calls.filter(call=>call.url==='/api/body-fit-review').length,1);assert.equal(pendingFit.calls.some(call=>call.url==='/api/approve-body-fit'),false);
 fitPreviewGate.resolve();await fitLoading;assert.equal(pendingFit.get('confirmBodyFit').checked,false);assert.equal(pendingFit.get('approveBodyFit').disabled,true);assert.deepEqual(pendingFit.errors,[]);
 checks.push('Pending body preview blocks approval and duplicate review requests until its matching geometry has loaded');
 // A failed transport or a buffer carrying another digest cannot approve
 // a stale preview, while the original source viewport remains usable.
 for(const mismatch of [false,true]){
  const broken=await bodyFitEnvironment();if(mismatch)broken.wrongBodyFitDigest(true);else broken.failBodyFitPreviews(1);
  await requestBodyFit(broken);assert.equal(broken.run('bodyFitReview'),null);assert.equal(broken.get('bodyFitStage').classList.contains('hidden'),true);
  assert.equal(broken.get('approveBodyFit').disabled,true);assert.equal(broken.records.get('fit_source').body_fit_status.approved,false);
  assert.match(broken.errors.pop(),mismatch?/does not match its review/:/Simulated body-fit preview failure/);
  assert.equal(broken.get('confirmParts').disabled,false);assert.equal(broken.viewerLoads.filter(row=>row.viewer==='viewer').length,1);
  broken.wrongBodyFitDigest(false);await requestBodyFit(broken);assert.equal(broken.get('confirmBodyFit').disabled,false);assert.equal(broken.get('confirmBodyFit').checked,false);assert.deepEqual(broken.errors,[]);
 }
 checks.push('Failed or mismatched body-fit preview cannot be approved and retry preserves the original source viewport');
 // Valid diagnostic geometry outside the profile limits remains viewable,
 // with no automatic switch from preserve-size to body adaptation.
 const outside=await bodyFitEnvironment();outside.bodyFitReport({within_profile_limits:false,issue:'Wheel centres exceed the reviewed profile limit.'});
 await requestBodyFit(outside,'keep_proportions');assert.equal(outside.get('bodyFitPolicy').value,'keep_proportions');assert.equal(outside.get('bodyFitStage').classList.contains('hidden'),false);
 assert.equal(outside.get('confirmBodyFit').disabled,true);assert.match(outside.get('bodyFitIssue').textContent,/profile limit/);
 outside.get('confirmBodyFit').checked=true;await outside.get('approveBodyFit').onclick();assert.match(outside.errors.pop(),/outside the current profile limits/);assert.equal(outside.calls.some(call=>call.url==='/api/approve-body-fit'),false);
 const unavailable=await bodyFitEnvironment();unavailable.bodyFitReport({available:false,within_profile_limits:false,review_digest:null,scale_change_percent_xyz:null,maximum_wheel_centre_error_metres:null,preview_parts:[],issue:'Assign the four tyre corners first.'});
 await requestBodyFit(unavailable,'keep_proportions');assert.equal(unavailable.calls.some(call=>call.url.startsWith('/api/body-fit-preview/')),false);assert.match(unavailable.get('bodyFitIssue').textContent,/four tyre corners/);assert.equal(unavailable.get('approveBodyFit').disabled,true);
 checks.push('Unavailable or out-of-limit body fits explain the blocker, remain unapproved and never silently choose body adaptation');
 // Stale replies must not survive edits, a new project, configuration intent,
 // or a different local policy, including a delayed binary response.
 for(const change of ['draft','project','configuration','configuration-refresh','policy']){
  const staleFit=await bodyFitEnvironment(),binary=change==='project'||change==='policy',gate=binary?staleFit.gateBodyFitPreview():staleFit.gateBodyFit();
  staleFit.get('bodyFitPolicy').value='adapt_body';staleFit.get('bodyFitPolicy').onchange();const request=staleFit.get('reviewBodyFit').onclick();
  await until(()=>staleFit.calls.some(call=>binary?call.url.startsWith('/api/body-fit-preview/'):call.url==='/api/body-fit-review'),'stale body-fit request');
  let configuration=null;
  if(change==='draft'){staleFit.get('carName').value='Edited during body fit';staleFit.get('carName').oninput()}
  if(change==='project'){const other=fixture('another_fit','Another model');other.source_sha256='d'.repeat(64);other.body_fit_status={approved:false,policy:null,reason:'not_selected'};staleFit.context.otherFit=other;staleFit.records.set(other.id,other);staleFit.run('project=editState.bind(otherFit);selected=null;previewUnverifiedContext=null;renderProject()')}
  if(change==='configuration')configuration=staleFit.get('pickCarBaseline').onclick();
  if(change==='configuration-refresh'){staleFit.appState.native_template='new native template';await staleFit.run('refresh()')}
  if(change==='policy'){staleFit.get('bodyFitPolicy').value='keep_proportions';staleFit.get('bodyFitPolicy').onchange()}
  gate.resolve();await request;if(configuration)await configuration;
  assert.equal(staleFit.run('bodyFitReview'),null);assert.equal(staleFit.get('approveBodyFit').disabled,true);assert.equal(staleFit.calls.some(call=>call.url==='/api/approve-body-fit'),false);assert.deepEqual(staleFit.errors,[]);
 }
 checks.push('Body-fit JSON and preview responses are discarded after edits, project switches, configuration changes or policy changes');
 // Approval is a settings-coordinator mutation: writes cannot overtake it,
 // and late responses cannot replace a newly bound project or its guard.
 const serializedFit=await bodyFitEnvironment();await requestBodyFit(serializedFit);serializedFit.get('confirmBodyFit').checked=true;
 const approvalGate=serializedFit.gateBodyFitApproval(),approving=serializedFit.get('approveBodyFit').onclick();
 await until(()=>serializedFit.calls.some(call=>call.url==='/api/approve-body-fit'),'body-fit approval');
 serializedFit.get('carName').value='Name after fit';serializedFit.get('carName').oninput();const nameAfterFit=serializedFit.get('carName').onchange();await tick();
 assert.equal(serializedFit.calls.filter(call=>call.url==='/api/settings').length,0);await serializedFit.get('approveBodyFit').onclick();assert.equal(serializedFit.calls.filter(call=>call.url==='/api/approve-body-fit').length,1);
 approvalGate.resolve();await approving;await nameAfterFit;assert.equal(serializedFit.records.get('fit_source').name,'Name after fit');assert.equal(serializedFit.get('approveBodyFit').disabled,true);assert.deepEqual(serializedFit.errors,[]);
 for(const fails of [false,true]){
  const oldFit=await bodyFitEnvironment();await requestBodyFit(oldFit);oldFit.get('confirmBodyFit').checked=true;const gate=oldFit.gateBodyFitApproval(),saving=oldFit.get('approveBodyFit').onclick();
  await until(()=>oldFit.calls.some(call=>call.url==='/api/approve-body-fit'),'old body-fit approval');
  const other=fixture('new_fit_context','New fit context');other.body_fit_status={approved:false,policy:null,reason:'not_selected'};oldFit.context.otherFit=other;oldFit.records.set(other.id,other);
  oldFit.run('project=editState.bind(otherFit);selected=null;bodyFitPending={context:editState.context,pid:project.id,kind:"review"};previewUnverifiedContext=editState.context;renderProject()');
  const currentContext=oldFit.run('editState.context');if(fails)gate.reject(Error('Old body-fit approval failed'));else gate.resolve();await saving;
  assert.equal(oldFit.run('project.id'),'new_fit_context');assert.equal(oldFit.run('project.body_fit_status.approved'),false);assert.equal(oldFit.run('bodyFitPending.context'),currentContext);assert.equal(oldFit.run('previewUnverifiedContext'),currentContext);
  if(fails)assert.match(oldFit.errors.pop(),/Old body-fit approval failed/);assert.deepEqual(oldFit.errors,[]);
 }
 checks.push('Body-fit approval serializes with settings and late success or failure cannot publish into another project or clear its guard');
 // Browsing a different policy must not rebuild using a previously saved
 // policy behind the player’s back; restore it or review the new choice.
 const changedPolicy=await bodyFitEnvironment();await requestBodyFit(changedPolicy);changedPolicy.get('confirmBodyFit').checked=true;await changedPolicy.get('approveBodyFit').onclick();
 changedPolicy.get('bodyFitPolicy').value='keep_proportions';changedPolicy.get('bodyFitPolicy').onchange();assert.equal(changedPolicy.get('guideAction').dataset.action,'review_body_fit');
 const beforePlan=changedPolicy.calls.filter(call=>call.url==='/api/car-plan').length;await changedPolicy.get('reviewCarPlan').onclick();assert.equal(changedPolicy.calls.filter(call=>call.url==='/api/car-plan').length,beforePlan);assert.match(changedPolicy.errors.pop(),/Approve the selected body fit/);
 changedPolicy.get('bodyFitPolicy').value='adapt_body';changedPolicy.get('bodyFitPolicy').onchange();assert.equal(changedPolicy.get('confirmBodyFit').checked,false);await changedPolicy.get('reviewCarPlan').onclick();assert.equal(changedPolicy.calls.filter(call=>call.url==='/api/car-plan').length,beforePlan+1);assert.deepEqual(changedPolicy.errors,[]);
 checks.push('Changing a saved body policy requires fresh review or explicit restoration before the full build plan can run');
 const busyFit=await bodyFitEnvironment();await requestBodyFit(busyFit);busyFit.get('confirmBodyFit').checked=true;
 const busyGate=busyFit.gateJob(),busyPlan=busyFit.get('reviewCarPlan').onclick();await until(()=>busyFit.run('currentJob!==null'),'active job before body fit');
 const reviewsBefore=busyFit.calls.filter(call=>call.url==='/api/body-fit-review').length;await busyFit.get('reviewBodyFit').onclick();await busyFit.get('approveBodyFit').onclick();
 assert.equal(busyFit.calls.filter(call=>call.url==='/api/body-fit-review').length,reviewsBefore);assert.equal(busyFit.calls.some(call=>call.url==='/api/approve-body-fit'),false);assert.equal(busyFit.get('confirmBodyFit').disabled,true);busyGate.resolve();await busyPlan;assert.deepEqual(busyFit.errors,[]);
 const wrongProject=await bodyFitEnvironment();wrongProject.bodyFitReport({id:'other_project'});await requestBodyFit(wrongProject);assert.equal(wrongProject.run('bodyFitReview'),null);assert.equal(wrongProject.calls.some(call=>call.url.startsWith('/api/body-fit-preview/')),false);assert.match(wrongProject.errors.pop(),/does not match this saved project/);
 checks.push('Body-fit controls respect active jobs and reject a review returned for a different project');
 assert.deepEqual(env.errors,[]);console.log(JSON.stringify({passed:true,checks:checks.length,details:checks,settings_requests:calls.filter(c=>c.url==='/api/settings').length,max_concurrent_settings:env.maxActive()},null,2));
}
module.exports={environment,fixture,readiness,tick,until,shellGearPlan,shellReference};
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1});
