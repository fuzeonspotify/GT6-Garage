/* Focused behavior checks against the real app.js and viewer.js. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {environment,fixture,readiness,tick,until,shellGearPlan,shellReference}=require('./ui_settings_regression.cjs');
const clone=value=>JSON.parse(JSON.stringify(value));
async function shellEnvironment(software=false,runningGear){
 const env=environment();await tick();const value=fixture('shell','Independent body');
 value.source_sha256='a'.repeat(64);value.assembly_mode='template_shell';value.approved_roles=true;value.template_inheritance_approved=true;value.installation={mode:'new_entry',replace_car_id:null};
 value.template_shell_settings={placement:{scale:1,translation_metres:[0,.3,0],flip_front:false},lighting:{mode:'supplied'},dispositions:{}};value.template_shell_status={approved:false};
 if(runningGear!==undefined)value.template_shell_settings.running_gear=clone(runningGear);
 value.import_readiness=readiness([{code:'wheel_layout',title:'Find four source tyres',detail:'Authored route only',action:'review_wheels'}],[{title:'Missing source wheels',detail:'Authored route only'}]);
 env.records.set(value.id,value);env.context.shellFixture=value;env.shellSoftware(software);await env.run('loadProject(shellFixture)');return env;
}
async function preview(env){await env.get('reviewTemplateShell').onclick()}
async function approve(env){env.get('confirmShellAssembly').checked=true;env.get('confirmShellAssembly').onchange();await env.get('approveShellAssembly').onclick()}
async function main(){
 const checks=[];
 const guides=await shellEnvironment();
 guides.run("showWheelCheck({status:'unmeasured'},false)");
 assert.match(guides.get('wheelHowTo').children[0].children[0].textContent,/four tyres/);
 assert.equal(guides.get('wheelHowTo').children[0].open,true);
 guides.run("showWheelCheck({status:'incompatible',visibility:{race:{}}},true)");
 assert.match(guides.get('wheelHowTo').children[0].children[0].textContent,/disappear in a race/);
 guides.run("showWheelCheck({status:'compatible',passed:true},true)");
 assert.equal(guides.get('wheelHowTo').children.length,0);
 assert.equal(guides.run("Help.forTestResult({stage:'wheel placement',status:'needs_attention',wheels:{corners:2}})"),'sourceTyres');
 assert.equal(guides.run("Help.forTestResult({status:'needs_attention',code:'missing_resource'})"),'modelFiles');
 assert.equal(guides.run("Help.forTestResult({status:'needs_attention',code:'unexpected'})"),null);
 checks.push('Wheel instructions follow the failing check and clear on success, including saved model tests from older versions');
 guides.run("notice('A file is missing',false,'missing_resource')");
 assert.match(guides.get('notice').children[0].children[0].textContent,/missing textures/);
 guides.run("showNativeBuildResult($('exportResult'),{native_files:7})");
 const links=guides.get('exportResult').children.slice(-3);
 assert.match(links[0].children[0].textContent,/RPCS3/);
 assert.match(links[1].children[0].textContent,/PS3 FTP/);
 assert.match(links[2].children[0].textContent,/second car/);
 assert.ok(links.every(link=>!link.open));
 checks.push('Import warnings show specific repair steps and completed builds offer both install guides and the next-car guide');
 const restoring=await shellEnvironment();
 const restoreGate=restoring.gateProject('a'),initialOpen=restoring.run("openProject('a',false)");
 await until(()=>restoring.calls.some(call=>call.url==='/api/project/a'),'restoring saved car');
 restoring.run("page('help')");
 restoreGate.resolve();await initialOpen;
 assert.equal(restoring.run('project.id'),'a');
 assert.equal(restoring.run('activePage'),'help');
 await restoring.run("openProject('shell')");
 assert.equal(restoring.run('activePage'),'workshop');
 checks.push('Reading help during startup keeps the guide open while the previous car finishes loading');
 const materialEnv=await shellEnvironment();
 const materialHelp={material:'Paint',part_id:'n0p0',part_name:'body',part_count:1,steps:['Keep the texture on Base Color.']};
 materialEnv.shellReport({available:false,ready_for_candidate:false,preview_available:false,review_digest:null,
   blockers:[{code:'shell_surface_realization',message:'Paint uses transparency.',material_help:materialHelp}]});
 await preview(materialEnv);
 assert.equal(materialEnv.get('guideAction').textContent,'See how to fix it');
 await materialEnv.get('guideAction').onclick();
 assert.equal(materialEnv.get('shellMaterialHelp').focused,true);
 const panel=materialEnv.get('shellMaterialHelp').children[0];
 assert.equal(panel.children[0].textContent,'How to fix Paint');
 panel.children.at(-1).onclick();
 assert.equal(materialEnv.get('selectedName').dataset.partId,'n0p0');
 assert.equal(materialEnv.get('partSearch').value,'Paint');
 assert.equal(materialEnv.get('confirmShellAssembly').disabled,true);
 materialEnv.run('clearTemplateShellReview()');
 assert.equal(materialEnv.get('shellMaterialHelp').children.length,0);
 checks.push('A blocked material shows repair steps, selects the matching component and clears stale guidance');
 const guided=await shellEnvironment();
 assert.equal(guided.get('bodyFitPanel').classList.contains('hidden'),true);assert.equal(guided.get('templateShellPanel').classList.contains('hidden'),false);assert.equal(guided.get('guideAction').dataset.action,'review_shell');assert.equal(guided.get('guideCapability').classList.contains('hidden'),true);assert.doesNotMatch(guided.get('guideTitle').textContent,/four|tyre/i);
 guided.get('assemblyMode').value='stock_size';await guided.get('assemblyMode').onchange();assert.equal(guided.get('bodyFitPanel').classList.contains('hidden'),true);assert.equal(guided.records.get('shell').template_shell_settings.running_gear.tyre_size,'game');assert.equal(guided.records.get('shell').installation.mode,'new_entry');
 checks.push('Changing the GT6 sizing preference retains the native wheel set and destination');

 for(const software of [false,true]){
  const env=await shellEnvironment(software);await preview(env);assert.equal(env.get('confirmShellAssembly').disabled,false);assert.equal(env.get('approveShellAssembly').disabled,true);
  const request=env.calls.find(x=>x.url.startsWith('/api/template-shell-preview/'));assert.equal(request.url.includes('software=1'),software);assert.equal(env.viewerLoads.at(-1).viewer,'shellViewer');assert.equal(env.viewerViews.at(-1).kind,'side');
  await env.get('approveShellAssembly').onclick();assert.match(env.errors.pop(),/confirm/);assert.equal(env.calls.some(x=>x.url==='/api/approve-template-shell'),false);
  await approve(env);assert.equal(env.records.get('shell').template_shell_status.approved,true);assert.equal(env.records.get('shell').installation.mode,'new_entry');assert.equal(env.run('shellReview'),null);await env.get('reviewCarPlan').onclick();assert.equal(env.get('buildCar').disabled,false);assert.match(env.get('carPlan').textContent,/supplied wheels/);assert.match(env.get('carPlan').textContent,/Uniform body size: 1.00000×/);assert.doesNotMatch(env.get('carPlan').textContent,/Review wheel assignments/);assert.deepEqual(env.errors,[]);
 }
 checks.push('Assembly approval requires the matching loaded native-component preview and confirmation in WebGL and software paths before the existing plan flow');

 for(const kind of ['failure','digest','native']){
  const env=await shellEnvironment();if(kind==='failure')env.failShellPreviews(1);if(kind==='digest')env.wrongShellDigest(true);if(kind==='native')env.nativeShellGeometry(false);
  await preview(env);assert.equal(env.run('shellReview'),null);assert.equal(env.get('confirmShellAssembly').disabled,true);assert.equal(env.get('shellStage').classList.contains('hidden'),true);assert.equal(env.calls.some(x=>x.url==='/api/approve-template-shell'),false);assert.ok(env.errors.length);env.errors.length=0;
 }
 checks.push('Failed preview, mismatched review digest and reference-only geometry cannot approve a supplied-wheel assembly');

 const pending=await shellEnvironment(),gate=pending.gateShellPreview(),request=pending.get('reviewTemplateShell').onclick();await until(()=>pending.calls.some(x=>x.url.startsWith('/api/template-shell-preview/')),'pending assembly binary');
 assert.equal(pending.get('approveShellAssembly').disabled,true);await pending.get('approveShellAssembly').onclick();assert.equal(pending.calls.some(x=>x.url==='/api/approve-template-shell'),false);
 pending.get('shellScale').value='1.2';pending.get('shellScale').oninput();gate.resolve();await request;assert.equal(pending.run('shellReview'),null);assert.equal(pending.get('shellStage').classList.contains('hidden'),true);assert.equal(pending.calls.some(x=>x.url==='/api/approve-template-shell'),false);await pending.run('flushSettings()');assert.equal(pending.records.get('shell').template_shell_settings.placement.scale,1.2);
 checks.push('An edit during pending assembly geometry invalidates its review and prevents late binary approval');

 const switched=await shellEnvironment(),switchGate=switched.gateShellReview(),oldReview=switched.get('reviewTemplateShell').onclick();await until(()=>switched.calls.some(x=>x.url==='/api/template-shell-review'),'pending assembly JSON');
 const other=fixture('other','Other project');switched.records.set('other',other);switched.context.otherFixture=other;await switched.run('loadProject(otherFixture)');switchGate.resolve();await oldReview;assert.equal(switched.run('project.id'),'other');assert.equal(switched.run('shellReview'),null);assert.equal(switched.calls.some(x=>x.url.startsWith('/api/template-shell-preview/')),false);
 checks.push('A late assembly review cannot publish into a newly opened project');

 const exclude=await shellEnvironment();await preview(exclude);exclude.run("shellDispositionRows.get('n0p0').role.value='ignore';shellDispositionRows.get('n0p0').role.onchange()");assert.equal(exclude.run('shellDispositionDirty'),true);assert.equal(exclude.get('reviewTemplateShell').disabled,true);await exclude.get('saveShellComponents').onclick();assert.match(exclude.errors.pop(),/Record why/);assert.equal(exclude.calls.some(x=>x.url==='/api/settings'),false);
 exclude.run("shellDispositionRows.get('n0p0').reason.value='Replaced by a reviewed supplied component';shellDispositionRows.get('n0p0').reason.oninput()");await exclude.get('saveShellComponents').onclick();const saved=exclude.records.get('shell');assert.equal(saved.overrides.n0p0.role,'ignore');assert.equal(saved.template_shell_settings.dispositions.n0p0.reason,'Replaced by a reviewed supplied component');assert.equal(saved.approved_roles,false);assert.equal(saved.installation.mode,'new_entry');assert.equal(exclude.run('shellReview'),null);assert.deepEqual(exclude.errors,[]);
 checks.push('Component exclusion requires a reason and saves Ignore assignment, exact disposition and renewed source review together');

 const placement=await shellEnvironment();placement.get('shellForward').value='0.2';await placement.get('shellForward').onchange();assert.deepEqual(placement.records.get('shell').template_shell_settings.placement.translation_metres,[0,.3,-.2]);
 placement.get('shellScale').value='1.4';await placement.get('shellScale').onchange();await placement.get('shellFlip').onclick();let config=placement.records.get('shell').template_shell_settings;assert.equal(config.placement.flip_front,true);assert.equal(config.placement.scale,1.4);assert.equal(placement.records.get('shell').metres_per_unit,1);
 await placement.get('shellOriginalSize').onclick();config=placement.records.get('shell').template_shell_settings;assert.equal(config.placement.scale,1);assert.deepEqual(config.placement.translation_metres,[0,.3,-.2]);assert.equal(config.placement.flip_front,true);await placement.get('shellReset').onclick();assert.deepEqual(placement.records.get('shell').template_shell_settings.placement,{scale:1,translation_metres:[0,0,0],flip_front:false});
 placement.get('shellLights').value='source';await placement.get('shellLights').onchange();assert.equal(placement.records.get('shell').template_shell_settings.lighting.mode,'source');assert.deepEqual(placement.errors,[]);
 checks.push('Uniform size, forward height placement, flip and original-size/reset controls persist independently of source units and destination');

 const staleApproval=await shellEnvironment();await preview(staleApproval);const approvalGate=staleApproval.gateShellApproval();staleApproval.get('confirmShellAssembly').checked=true;const approval=staleApproval.get('approveShellAssembly').onclick();await until(()=>staleApproval.calls.some(x=>x.url==='/api/approve-template-shell'),'pending assembly approval');
 staleApproval.get('shellHeight').value='0.6';staleApproval.get('shellHeight').oninput();approvalGate.resolve();await approval;await staleApproval.run('flushSettings()');assert.equal(staleApproval.records.get('shell').template_shell_status.approved,false);assert.equal(staleApproval.records.get('shell').template_shell_settings.placement.translation_metres[1],.6);await staleApproval.get('reviewCarPlan').onclick();assert.match(staleApproval.errors.pop(),/Approve the assembled preview/);assert.equal(staleApproval.calls.some(x=>x.url==='/api/car-plan'),false);
 checks.push('A queued placement edit supersedes a pending assembly approval and blocks the next plan until fresh review');

 const lights=await shellEnvironment();assert.deepEqual(['shellFrontLightHeight','shellRearLightHeight','shellLightSpacing'].map(id=>Number(lights.get(id).value)),[42,50,32]);await preview(lights);await approve(lights);
 lights.get('shellFrontLightHeight').value='55';lights.get('shellFrontLightHeight').oninput();assert.equal(lights.get('approveShellAssembly').disabled,true);await lights.get('shellFrontLightHeight').onchange();lights.get('shellRearLightHeight').value='35';await lights.get('shellRearLightHeight').onchange();lights.get('shellLightSpacing').value='27';await lights.get('shellLightSpacing').onchange();const lightSettings=lights.records.get('shell').template_shell_settings;assert.deepEqual(lightSettings.lighting,{mode:'supplied',front_height_ratio:.55,rear_height_ratio:.35,half_spacing_ratio:.27});assert.deepEqual(lightSettings.placement,{scale:1,translation_metres:[0,.3,0],flip_front:false});assert.equal(lights.records.get('shell').template_shell_status.approved,false);assert.equal(lights.run('shellReview'),null);
 const lightSaves=lights.calls.filter(x=>x.url==='/api/settings').length;lights.get('shellLightSpacing').value='14';await lights.get('shellLightSpacing').onchange();assert.match(lights.errors.pop(),/between 15% and 40%/);assert.equal(lights.calls.filter(x=>x.url==='/api/settings').length,lightSaves);lights.get('shellLights').value='source';await lights.get('shellLights').onchange();assert.equal(lights.get('shellLightAdjust').classList.contains('hidden'),true);assert.equal(lights.get('shellFrontLightHeight').disabled,true);assert.deepEqual(lights.records.get('shell').template_shell_settings.lighting,{mode:'source'});assert.deepEqual(lights.errors,[]);
 const mountRecovery=await shellEnvironment();mountRecovery.shellReport({available:false,ready_for_candidate:false,preview_available:false,review_digest:null,source:null,placement:null,lighting:null,blockers:[{code:'shell_light_mount',message:'A supplied light spans an opening. Adjust its placement.'}]});await preview(mountRecovery);assert.equal(mountRecovery.get('shellLightAdjust').open,true);assert.equal(mountRecovery.get('shellFrontLightHeight').disabled,false);assert.equal(mountRecovery.get('confirmShellAssembly').disabled,true);assert.match(mountRecovery.get('shellIssues').textContent,/spans an opening/);mountRecovery.get('shellRearLightHeight').value='35';await mountRecovery.get('shellRearLightHeight').onchange();mountRecovery.shellReport({});await preview(mountRecovery);assert.equal(mountRecovery.get('confirmShellAssembly').disabled,false);assert.equal(mountRecovery.get('confirmShellAssembly').checked,false);assert.equal(mountRecovery.get('approveShellAssembly').disabled,true);assert.deepEqual(mountRecovery.errors,[]);
 checks.push('Supplied-light percentage controls preserve other settings, reject out-of-range values, invalidate approval and expose recovery from a blocked mount before fresh review');

 const staleDrag=await shellEnvironment();await preview(staleDrag);assert.equal(staleDrag.run("handleShellPlacementDrag({phase:'start',delta:[0,0,0]})"),true);assert.equal(staleDrag.get('shellHeight').disabled,true);assert.equal(staleDrag.get('confirmShellAssembly').disabled,true);
 // A queued input event can outlive the control becoming disabled. Its newer
 // placement must win over the translation captured when the drag started.
 staleDrag.get('shellHeight').value='0.9';staleDrag.get('shellHeight').oninput();staleDrag.run("handleShellPlacementDrag({phase:'move',delta:[0,.05,0]})");assert.equal(staleDrag.run("handleShellPlacementDrag({phase:'end',delta:[0,.05,0]})"),false);await staleDrag.run('flushSettings()');assert.deepEqual(staleDrag.records.get('shell').template_shell_settings.placement.translation_metres,[0,.9,0]);assert.equal(staleDrag.run('shellReview'),null);assert.equal(staleDrag.run('shellDragging'),null);assert.equal(staleDrag.get('shellHeight').disabled,false);assert.equal(staleDrag.calls.filter(x=>x.url==='/api/template-shell-review').length,1);assert.deepEqual(staleDrag.errors,[]);
 const changedDrag=await shellEnvironment();await preview(changedDrag);changedDrag.run("handleShellPlacementDrag({phase:'start',delta:[0,0,0]});handleShellPlacementDrag({phase:'move',delta:[0,.05,0]})");const otherDrag=clone(changedDrag.records.get('shell'));otherDrag.id='other_drag';changedDrag.records.set(otherDrag.id,otherDrag);changedDrag.context.otherDrag=otherDrag;await changedDrag.run('loadProject(otherDrag)');assert.equal(changedDrag.run("handleShellPlacementDrag({phase:'end',delta:[0,.05,0]})"),false);assert.equal(changedDrag.run('project.id'),'other_drag');assert.deepEqual(changedDrag.records.get('other_drag').template_shell_settings.placement.translation_metres,[0,.3,0]);assert.equal(changedDrag.get('shellHeight').disabled,false);assert.equal(changedDrag.calls.some(x=>x.url==='/api/settings'),false);assert.deepEqual(changedDrag.errors,[]);
 const modeDrag=await shellEnvironment();await preview(modeDrag);modeDrag.run("handleShellPlacementDrag({phase:'start',delta:[0,0,0]});handleShellPlacementDrag({phase:'move',delta:[0,.05,0]})");modeDrag.get('assemblyMode').value='stock_size';await modeDrag.get('assemblyMode').onchange();assert.equal(modeDrag.run("handleShellPlacementDrag({phase:'end',delta:[0,.05,0]})"),false);assert.equal(modeDrag.records.get('shell').template_shell_settings.running_gear.tyre_size,'game');assert.deepEqual(modeDrag.records.get('shell').template_shell_settings.placement.translation_metres,[0,.3,0]);assert.equal(modeDrag.calls.filter(x=>x.url==='/api/settings').length,1);
 const currentDrag=await shellEnvironment();await preview(currentDrag);currentDrag.run("handleShellPlacementDrag({phase:'start',delta:[0,0,0]});handleShellPlacementDrag({phase:'move',delta:[0,.05,-.1]})");assert.equal(currentDrag.run("handleShellPlacementDrag({phase:'end',delta:[0,.05,-.1]})"),true);await until(()=>currentDrag.calls.filter(x=>x.url==='/api/template-shell-review').length===2&&!currentDrag.run('shellPending'),'fresh preview after current drag');assert.deepEqual(currentDrag.records.get('shell').template_shell_settings.placement.translation_metres,[0,.35,-.1]);assert.equal(currentDrag.get('confirmShellAssembly').checked,false);assert.equal(currentDrag.get('approveShellAssembly').disabled,true);assert.deepEqual(currentDrag.errors,[]);
 checks.push('A held drag disables placement controls, rejects newer drafts or changed projects/methods, and only a current drag saves and requests a fresh unapproved preview');

 const inherited=shellGearPlan(),matching={schema:1,mode:'custom_dimensions',front:clone(inherited.resolved.front),rear:clone(inherited.resolved.rear)},changed=clone(matching);changed.front.track_metres=1.7;
 const defaultGear=await shellEnvironment();assert.deepEqual(clone(defaultGear.run('shellOptions().running_gear')),{schema:1,mode:'profile'});await defaultGear.get('shellFlip').onclick();assert.deepEqual(defaultGear.records.get('shell').template_shell_settings.running_gear,{schema:1,mode:'profile'});
 const persistent=await shellEnvironment(false,changed);persistent.run('(()=>{const copy=shellOptions();copy.running_gear.front.track_metres=9})()');assert.deepEqual(clone(persistent.run('project.template_shell_settings.running_gear')),changed);
 for(const [id,value]of [['shellScale','1.25'],['shellForward','.2'],['shellHeight','.4'],['shellLateral','-.1'],['shellFrontLightHeight','55'],['shellRearLightHeight','35'],['shellLightSpacing','27']]){persistent.get(id).value=value;await persistent.get(id).onchange()}
 for(const id of ['shellFlip','shellOriginalSize','shellReset'])await persistent.get(id).onclick();
 for(const mode of ['source','supplied']){persistent.get('shellLights').value=mode;await persistent.get('shellLights').onchange()}
 persistent.run("shellDispositionRows.get('n0p0').role.value='ignore';shellDispositionRows.get('n0p0').role.onchange();shellDispositionRows.get('n0p0').reason.value='Explicit exclusion while retaining the requested chassis dimensions';shellDispositionRows.get('n0p0').reason.oninput()");await persistent.get('saveShellComponents').onclick();
 persistent.get('assemblyMode').value='authored';await persistent.get('assemblyMode').onchange();
 assert.match(persistent.errors.pop(),/Choose a GT6 wheel placement/);
 persistent.get('assemblyMode').value='template_shell';await persistent.get('assemblyMode').onchange();
 persistent.context.reopenedShell=clone(persistent.records.get('shell'));await persistent.run('loadProject(reopenedShell)');
 assert.deepEqual(clone(persistent.run('shellOptions().running_gear')),changed);assert.equal(persistent.records.get('shell').installation.mode,'new_entry');
 for(const call of persistent.calls.filter(call=>call.url==='/api/settings'&&call.data.changes.template_shell_settings))assert.deepEqual(call.data.changes.template_shell_settings.running_gear,changed);
 assert.deepEqual(persistent.errors,[]);
 checks.push('Legacy settings receive an explicit profile request while every body, light, exclusion, reset, method-switch and reopen path preserves the complete saved custom request without aliasing or rounding');

 for(const software of [false,true]){const exact=await shellEnvironment(software,matching);await preview(exact);assert.equal(exact.get('confirmShellAssembly').disabled,false);assert.equal(exact.run('shellReview.running_gear_plan.native_no_op'),true);await approve(exact);assert.equal(exact.records.get('shell').template_shell_status.approved,true);assert.deepEqual(exact.records.get('shell').template_shell_settings.running_gear,matching);assert.equal(exact.records.get('shell').template_shell_settings.running_gear.front.track_metres,1.536523);assert.deepEqual(exact.errors,[])}
 const reordered=await shellEnvironment(false,matching),reverseObject=value=>Object.fromEntries(Object.entries(value).reverse());reordered.shellReport({running_gear:{rear:reverseObject(matching.rear),front:reverseObject(matching.front),mode:'custom_dimensions',schema:1}});await preview(reordered);assert.equal(reordered.get('confirmShellAssembly').disabled,false);assert.deepEqual(reordered.errors,[]);
 checks.push('An exact custom no-op uses the same accepted proof in WebGL and software previews, preserving full dimensions and allowing equivalent JSON key order');

 const blocked=await shellEnvironment(false,changed);await preview(blocked);assert.equal(blocked.run('shellReview.running_gear_plan.native_no_op'),false);assert.match(blocked.get('shellIssues').textContent,/front.track_metres/);assert.equal(blocked.get('confirmShellAssembly').disabled,true);assert.equal(blocked.calls.some(call=>call.url.startsWith('/api/template-shell-preview/')),false);await approve(blocked);assert.equal(blocked.calls.some(call=>call.url==='/api/approve-template-shell'),false);assert.equal(blocked.errors.length,1);
 const unavailable=await shellEnvironment();unavailable.shellReport({available:false,ready_for_candidate:false,preview_available:false,review_digest:null,source:null,placement:null,running_gear:null,running_gear_plan:null,reference:null,blockers:[{code:'shell_placement',message:'Correct the invalid body placement.'}]});await preview(unavailable);assert.match(unavailable.get('shellIssues').textContent,/invalid body placement/);assert.equal(unavailable.get('confirmShellAssembly').disabled,true);assert.equal(unavailable.calls.some(call=>call.url.startsWith('/api/template-shell-preview/')),false);assert.deepEqual(unavailable.errors,[]);
 checks.push('Changed custom dimensions and early unavailable reports display their blockers without loading unsupported geometry or permitting approval');

 const nativeUnavailable=await shellEnvironment();nativeUnavailable.shellReport({preview_available:false,blockers:[{code:'template_native',message:'The owned native wheel file is missing or could not be decoded. Choose the matching template package.'}]});await preview(nativeUnavailable);assert.equal(nativeUnavailable.run('shellReview.ready_for_candidate'),true);assert.match(nativeUnavailable.get('shellIssues').textContent,/owned native wheel file is missing or could not be decoded/);assert.equal(nativeUnavailable.get('confirmShellAssembly').disabled,true);assert.equal(nativeUnavailable.calls.some(call=>call.url.startsWith('/api/template-shell-preview/')),false);assert.deepEqual(nativeUnavailable.errors,[]);await approve(nativeUnavailable);assert.equal(nativeUnavailable.calls.some(call=>call.url==='/api/approve-template-shell'),false);assert.match(nativeUnavailable.errors.pop(),/Load a current assembled preview/);nativeUnavailable.shellReport({});await preview(nativeUnavailable);assert.equal(nativeUnavailable.get('confirmShellAssembly').disabled,false);assert.equal(nativeUnavailable.get('confirmShellAssembly').checked,false);assert.equal(nativeUnavailable.get('approveShellAssembly').disabled,true);assert.deepEqual(nativeUnavailable.errors,[]);
 checks.push('A source-ready review with failed native preview preserves the actionable template error, blocks approval and requires a fresh confirmed preview after recovery');

 const mismatchedRequest={...clone(inherited),requested:matching},wrongAssets=clone(inherited);wrongAssets.asset_sha256['hq/wheel']='9'.repeat(64);
 const forgedDigest={...clone(inherited),plan_digest:'0'.repeat(64)};
 for(const override of [{schema:1,algorithm_revision:3},{running_gear_plan:null},{running_gear_plan:mismatchedRequest},{running_gear_plan:wrongAssets},{running_gear_plan:forgedDigest,reference:shellReference(forgedDigest)}]){
  const invalid=await shellEnvironment();invalid.shellReport(override);await preview(invalid);assert.equal(invalid.run('shellReview'),null);assert.equal(invalid.get('confirmShellAssembly').disabled,true);assert.equal(invalid.get('shellStage').classList.contains('hidden'),true);assert.equal(invalid.calls.some(call=>call.url==='/api/approve-template-shell'),false);assert.ok(invalid.errors.length);
 }
 const forgedCustom=await shellEnvironment(false,changed),pretend=shellGearPlan(changed);pretend.ready=true;pretend.native_no_op=true;pretend.blockers=[];pretend.requested_corners=clone(pretend.corners);forgedCustom.shellReport({available:true,ready_for_candidate:true,preview_available:true,source:{source_sha256:'a'.repeat(64)},review_digest:'d'.repeat(64),blockers:[],running_gear_plan:pretend,reference:shellReference(pretend)});await preview(forgedCustom);assert.equal(forgedCustom.run('shellReview'),null);assert.equal(forgedCustom.calls.some(call=>call.url.startsWith('/api/template-shell-preview/')),false);assert.ok(forgedCustom.errors.length);
 checks.push('Old contracts, missing or stale proof, mismatched authenticated references, forged plan digests and changed dimensions falsely labeled no-op fail before approval');

 for(const metadata of [{running_gear_plan_digest:null},{running_gear_plan_digest:'0'.repeat(64)},{running_gear_native_no_op:false}]){const invalid=await shellEnvironment();invalid.shellPreview(metadata);await preview(invalid);assert.equal(invalid.run('shellReview'),null);assert.equal(invalid.get('confirmShellAssembly').disabled,true);assert.equal(invalid.get('shellStage').classList.contains('hidden'),true);assert.ok(invalid.errors.length)}
 const alteredPreview=await shellEnvironment();await preview(alteredPreview);alteredPreview.run("shellViewer.meta.running_gear_plan_digest='0'.repeat(64)");await approve(alteredPreview);assert.equal(alteredPreview.calls.some(call=>call.url==='/api/approve-template-shell'),false);assert.ok(alteredPreview.errors.length);
 checks.push('Preview metadata must retain the exact reviewed running-gear digest and native-no-op proof both when loaded and when approval is requested');

 const staleGear=await shellEnvironment(false,matching),gearGate=staleGear.gateShellPreview(),gearRequest=staleGear.get('reviewTemplateShell').onclick();await until(()=>staleGear.calls.some(call=>call.url.startsWith('/api/template-shell-preview/')),'pending running-gear preview');staleGear.context.changedRunningGear=changed;staleGear.run('stageSettings({template_shell_settings:{...shellOptions(),running_gear:changedRunningGear}})');gearGate.resolve();await gearRequest;await staleGear.run('flushSettings()');assert.equal(staleGear.run('shellReview'),null);assert.deepEqual(staleGear.records.get('shell').template_shell_settings.running_gear,changed);assert.equal(staleGear.get('confirmShellAssembly').disabled,true);await preview(staleGear);assert.match(staleGear.get('shellIssues').textContent,/front.track_metres/);assert.equal(staleGear.calls.some(call=>call.url==='/api/approve-template-shell'),false);assert.deepEqual(staleGear.errors,[]);
 checks.push('A changed running-gear request invalidates pending preview proof and remains saved as a blocked custom request instead of reverting to profile');

 const viewerSource=fs.readFileSync(path.join(__dirname,'../web/viewer.js'),'utf8');const ActualViewer=vm.runInNewContext(viewerSource.replace('export class Viewer','class Viewer')+';Viewer');
 const proto=Object.create(ActualViewer.prototype);Object.assign(proto,{parts:{body:{moves_with_body:true},lamp:{moves_with_body:true},wheel:{moves_with_body:false}},placementPreviewOffset:[0,.2,-.3],exploded:false,center:[0,0,0],radius:3,distance:7,yaw:Math.PI/2,pitch:0,orthographicViews:true,viewKind:'side',invalidate(){}});
 assert.deepEqual(Array.from(proto.offset({id:'body'})),[0,.2,-.3]);assert.deepEqual(Array.from(proto.offset({id:'lamp'})),[0,.2,-.3]);assert.deepEqual(Array.from(proto.offset({id:'wheel'})),[0,0,0]);const orthographic=proto.camera(800,400);assert.equal(orthographic[15],1);proto.orthographicViews=false;const perspective=proto.camera(800,400);assert.notEqual(perspective[15],1);proto.orthographicViews=true;proto.view('side');assert.equal(proto.pitch,0);proto.view('top');assert.ok(proto.pitch>1.5);

 const handlers={},dragEvents=[],interactive=Object.create(ActualViewer.prototype);Object.assign(interactive,{canvas:{clientHeight:400,addEventListener(name,fn){handlers[name]=fn},setPointerCapture(){}},options:{onPlacementDrag(event){dragEvents.push(clone(event));return true}},orthographicViews:true,viewKind:'side',distance:7,radius:3,placementPreviewOffset:[0,0,0],invalidate(){},pick(){throw Error('Placement drag should not select a source part')}});interactive.events();handlers.pointerdown({button:0,clientX:100,clientY:100,pointerId:1});handlers.pointermove({clientX:120,clientY:80});const delta=Array.from(interactive.placementPreviewOffset);assert.equal(delta[0],0);assert.ok(delta[1]>0);assert.ok(delta[2]<0);handlers.pointerup({});assert.deepEqual(dragEvents.map(e=>e.phase),['start','move','end']);assert.deepEqual(dragEvents.at(-1).delta,delta);assert.deepEqual(Array.from(interactive.placementPreviewOffset),[0,0,0]);
 checks.push('Opt-in orthographic placement moves source and supplied lamp geometry while decoded native wheels remain fixed and ordinary viewers keep perspective');
 console.log(JSON.stringify({passed:true,checks:checks.length,details:checks,scope:'Actual frontend functions and viewer math with controlled DOM/transport; real browser/native verification is separate.'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1});
