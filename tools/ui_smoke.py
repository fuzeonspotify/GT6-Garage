"""Interactive UI smoke-test template. Supply an authorized existing Edge/Chromium.
No browser security policy is changed. Only a local Garage QA instance is used.
"""
from pathlib import Path
import argparse,json,time
from playwright.sync_api import sync_playwright

def main():
    p=argparse.ArgumentParser();p.add_argument('--session',required=True,type=Path);p.add_argument('--browser',required=True,type=Path);p.add_argument('--model',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--software',action='store_true');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);session=json.loads(a.session.read_text(encoding='utf-8'));errors=[]
    with sync_playwright() as pw:
        b=pw.chromium.launch(executable_path=str(a.browser),headless=True);page=b.new_page(viewport={'width':1500,'height':1000})
        if a.software:page.add_init_script("const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(name,...args){if(name==='webgl2'||name==='webgl')return null;return original.call(this,name,...args)}")
        page.on('pageerror',lambda e:errors.append(str(e)));page.goto(session['url']);page.set_input_files('#modelInput',str(a.model));page.locator('#modelMeta').filter(has_text=a.model.name).wait_for(timeout=45000);page.wait_for_function("Number(document.querySelector('#statParts').textContent)>0");page.wait_for_timeout(1000);page.screenshot(path=str(a.output/'workshop.png'),full_page=True)
        parts=int(page.locator('#statParts').inner_text());mode=page.locator('#previewMode').inner_text()
        page.locator('#confirmParts').check();page.wait_for_timeout(500);page.locator('#goExport').click();page.locator('#exportProject').click();page.locator('#exportResult h2').filter(has_text='Export created').wait_for(timeout=30000);page.screenshot(path=str(a.output/'export.png'),full_page=True);b.close()
    assert not errors,errors
    (a.output/'report.json').write_text(json.dumps({'passed':True,'source':a.model.name,'parts':parts,'preview_mode':mode,'game_launched':False,'javascript_errors':errors},indent=2),encoding='utf-8')
if __name__=='__main__':main()
