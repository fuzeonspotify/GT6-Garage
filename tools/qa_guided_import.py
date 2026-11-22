"""Actual browser flow for guidance and generic tyre preparation; never launches GT6."""
from __future__ import annotations
import argparse,hashlib,json,time,traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

def main():
    parser=argparse.ArgumentParser()
    for name in ('session','browser','project','model','output'):parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--software',action='store_true')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    session=json.loads(args.session.read_text());url,token=session['url'].split('#',1)
    headers={'X-Garage-Token':token};errors=[];checks=[];report={'passed':False,'checks':checks,'javascript_errors':errors,'game_launched':False,'kind':'actual_browser_source_preparation','native_or_runtime_acceptance':False}
    def check(label,value):
        if not value:raise AssertionError(label)
        checks.append(label)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=str(args.browser),headless=True)
        context=browser.new_context(viewport={'width':1500,'height':1000},accept_downloads=True)
        page=context.new_page();page.set_default_timeout(30000)
        if args.software:page.add_init_script("const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(name,...args){if(name==='webgl2'||name==='webgl')return null;return original.call(this,name,...args)}")
        page.on('pageerror',lambda error:errors.append(str(error)));page.on('dialog',lambda dialog:dialog.accept())
        def state():return page.request.get(url+'api/state',headers=headers).json()
        def latest():
            pid=state()['projects'][0]['id']
            return page.request.get(url+'api/project/'+pid,headers=headers).json()
        def ready():page.wait_for_function("() => !document.getElementById('confirmParts').disabled && document.getElementById('jobbar').classList.contains('hidden')")
        def load(path,count):
            with page.expect_response(lambda r:r.url.endswith('/api/import-project' if path.suffix=='.gt6car' else '/api/import')) as imported:
                page.set_input_files('#modelInput',str(path))
            check('import accepted '+path.name,imported.value.status==202)
            page.locator('#jobbar').wait_for(state='visible')
            page.locator('#jobbar').wait_for(state='hidden')
            page.wait_for_function('(n)=>Number(document.getElementById("statParts").textContent)===n',arg=count)
            ready();page.locator('#importGuide').wait_for(state='visible')
        def choose(selector,value):
            with page.expect_response(lambda r:r.url.endswith('/api/settings') and r.request.method=='POST') as response:
                page.locator(selector).select_option(value)
            check('saved '+selector+'='+value,response.value.status==200);ready()
        def number(selector,value):
            with page.expect_response(lambda r:r.url.endswith('/api/settings') and r.request.method=='POST') as response:
                page.locator(selector).fill(str(value));page.locator(selector).press('Tab')
            check('saved '+selector,response.value.status==200);ready()
        def select(part):
            page.locator('[data-page="workshop"]').click();page.locator('#roleFilter').select_option('all')
            page.locator('#partSearch').fill(part)
            page.locator('#partList .partRow').first.click()
            check('selected '+part,page.locator('#selectedName').get_attribute('data-part-id')==part)
        try:
            page.goto(session['url']);page.locator('#version').filter(has_text='0.4.').wait_for()
            load(args.project,71)
            check('original saved F50 capability limit is visible before game setup',page.locator('#guideCapability').is_visible())
            check('fresh workspace has no configured baseline',not state().get('car_baseline'))
            page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(args.output/'original_f50_guide.png'))
            report['original_project_readiness']=latest()['import_readiness']
            load(args.model,50)
            generic=latest();report['input_sha256']=hashlib.sha256(args.model.read_bytes()).hexdigest()
            check('generic derivative uses unchanged uploaded bytes',generic['source_sha256']==report['input_sha256'])
            check('destination is not selected automatically',generic['installation']['mode'] is None)
            number('#sourceUnits',.907946532743)
            if page.locator('#front').input_value()!='+Z':choose('#front','+Z')
            select('n48p0');choose('#partRole','tyre')
            with page.expect_response(lambda r:r.url.endswith('/api/discover-tyres')) as rejected:
                page.locator('#discoverTyres').click()
            check('sidewall-only group is rejected without mutation',rejected.value.status==400 and latest()['wheel_partitions']==[])
            check('ambiguity explains the occupied bands','occupied left/right bands' in rejected.value.json()['error'])
            select('n47p0');choose('#partRole','tyre')
            with page.expect_response(lambda r:r.url.endswith('/api/discover-tyres')) as proposed:
                page.locator('#discoverTyres').click()
            check('generic tread proposal succeeds',proposed.value.status==200)
            proposal=proposed.value.json();page.locator('#tyreProposal').wait_for(state='visible')
            check('proposal has four unchanged 1200-face groups',len(proposal['corners'])==4 and all(x['triangles']==1200 for x in proposal['corners']))
            check('proposal cannot apply without explicit review',page.locator('#applyTyreDiscovery').is_disabled())
            page.locator('#selectedPanel').screenshot(path=str(args.output/'tyre_proposal.png'))
            page.locator('#cancelTyreDiscovery').click()
            check('cancel leaves original group intact',latest()['part_count']==50 and latest()['wheel_partitions']==[])
            page.locator('#discoverTyres').click();page.locator('#tyreProposal').wait_for(state='visible');page.locator('#confirmTyreOnly').check()
            with page.expect_response(lambda r:r.url.endswith('/api/apply-tyre-discovery')) as applied:
                page.locator('#applyTyreDiscovery').click()
            check('explicit proposal applies',applied.value.status==200)
            page.wait_for_function("() => Number(document.getElementById('statParts').textContent)===53");ready()
            check('apply clears source approval',not page.locator('#confirmParts').is_checked())
            check('apply selects a saved child',page.locator('#selectedName').get_attribute('data-part-id')=='n47p0__wheel_FL')
            select('n48p0');page.locator('#partitionWheelGroup').click()
            page.wait_for_function("() => Number(document.getElementById('statParts').textContent)===56");ready()
            check('sidewalls reuse reviewed tread centres',len(latest()['wheel_partitions'])==2)
            page.locator('#partSearch').fill('');page.locator('[data-view="fit"]').click()
            page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(args.output/'prepared_car_guide.png'))
            page.locator('#stage').screenshot(path=str(args.output/'prepared_source_preview.png'))
            page.locator('[data-page="export"]').click();choose('#installMode','new_entry')
            check('new-entry intent saved explicitly',latest()['installation']=={'mode':'new_entry','replace_car_id':None})
            page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(args.output/'destination_choice.png'))
            page.locator('#installationSetup').screenshot(path=str(args.output/'destination_new_entry.png'))
            page.locator('#exportProject').click();page.locator('#exportResult h2').filter(has_text='Export created').wait_for()
            link=page.locator('#exportResult a[href^="/download/"]')
            with page.expect_download() as download:link.click()
            saved=args.output/'reviewed_generic_tyres.gt6car';download.value.save_as(str(saved))
            load(saved,56)
            restored=latest()
            check('saved archive reopens exact source and partitions',restored['source_sha256']==report['input_sha256'] and len(restored['wheel_partitions'])==2)
            check('new-entry intent survives reopening',restored['installation']=={'mode':'new_entry','replace_car_id':None})
            page.locator('[data-page="export"]').click();choose('#installMode','replace')
            check('replacement choice requires an explicit target',latest()['installation']=={'mode':'replace','replace_car_id':None})
            page.locator('#installationSetup').screenshot(path=str(args.output/'destination_replace.png'))
            check('full native build stays disabled',page.locator('#buildCar').is_disabled())
            report.update(passed=True,prepared_project_id=restored['id'],prepared_parts=restored['part_count'],source_triangles=restored['triangles'],preview_mode=page.locator('#previewMode').inner_text(),readiness=restored['import_readiness'])
            check('browser has no JavaScript errors',not errors)
        except Exception as exc:
            report.update(error=str(exc),traceback=traceback.format_exc())
            page.screenshot(path=str(args.output/'failure.png'),full_page=True)
            raise
        finally:
            (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');browser.close()
    print(json.dumps({'passed':report['passed'],'checks':len(checks),'parts':report['prepared_parts'],'triangles':report['source_triangles'],'preview_mode':report['preview_mode']}))
if __name__=='__main__':main()
