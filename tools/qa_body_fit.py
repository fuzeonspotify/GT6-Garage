"""Real browser body-fit review and complete new-entry build; never launches GT6.

The caller owns the Garage process/workspace and supplies its existing session,
authorized Chromium/Edge executable, genuine Supra GLB and an output directory.
Baseline and packer configuration must already exist. No routes are mocked and
no approval, plan or native build is injected through an API shortcut.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('session', 'browser', 'model', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--software', action='store_true')
    parser.add_argument('--name', default='Garage Body Fit QA')
    args = parser.parse_args()
    args.name = args.name.strip()
    if not args.name or len(args.name) > 120:
        parser.error('--name must contain between 1 and 120 characters')
    from playwright.sync_api import sync_playwright

    args.output.mkdir(parents=True, exist_ok=True)
    session = json.loads(args.session.read_text(encoding='utf-8-sig'))
    base, token = session['url'].split('#', 1)
    base = base.rstrip('/') + '/'
    headers = {'X-Garage-Token': token}
    checks, errors, console_errors, http_errors, request_failures = [], [], [], [], []
    report = {
        'passed': False, 'kind': 'actual_browser_body_fit_and_native_new_entry',
        'game_launched': False, 'runtime_verified': False,
        'native_build_completed': False, 'software_requested': args.software,
        'source_file': str(args.model), 'source_sha256': sha256(args.model),
        'checks': checks, 'javascript_errors': errors, 'console_errors': console_errors,
        'http_errors': http_errors, 'request_failures': request_failures,
        'reviews': {}, 'jobs': {}, 'screenshots': [],
    }
    started = time.monotonic()

    def save_json(name, value):
        (args.output / name).write_text(
            json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n',
            encoding='utf-8')

    def checkpoint(phase):
        report['phase'] = phase
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        save_json('report.json', report)
        print(json.dumps({'phase': phase, 'elapsed_seconds': report['elapsed_seconds']}), flush=True)

    def check(label, value):
        if not value:
            raise AssertionError(label)
        checks.append(label)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(args.browser), headless=True)
        context = browser.new_context(viewport={'width': 1500, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(30000)
        if args.software:
            page.add_init_script("""const original=HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext=function(name,...args){
                    if(name==='webgl2'||name==='webgl')return null;
                    return original.call(this,name,...args)
                }""")
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: console_errors.append({
            'text': message.text, 'location': message.location}) if message.type == 'error' else None)
        page.on('requestfailed', lambda request: request_failures.append({
            'path': urlsplit(request.url).path, 'failure': request.failure}))
        page.on('dialog', lambda dialog: dialog.accept())
        preview_responses = []

        def observe_response(response):
            path = urlsplit(response.url).path
            if path.startswith('/api/body-fit-preview/'):
                preview_responses.append(response)
            if response.status >= 400:
                http_errors.append({'path': path, 'status': response.status})

        page.on('response', observe_response)
        pid = None

        def get_json(path):
            response = page.request.get(base + path.lstrip('/'), headers=headers, timeout=120000)
            if response.status != 200:
                raise AssertionError(f'GET {path}: {response.status}: {response.text()[:500]}')
            return response.json()

        def current_project():
            return get_json('/api/project/' + pid)

        def source_evidence(project):
            return {key: project.get(key) for key in (
                'source_sha256', 'source_up_axis', 'metres_per_unit', 'forward',
                'wheelbase_metres', 'overrides', 'wheel_partitions', 'inspection',
                'part_count', 'triangles')}

        def frames():
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

        def screenshot(name, selector):
            frames()
            path = args.output / (name + '.png')
            page.locator(selector).screenshot(path=str(path))
            report['screenshots'].append(str(path))
            return str(path)

        def post_from_ui(endpoint, action, expected=200):
            with page.expect_response(lambda response: response.request.method == 'POST'
                                      and urlsplit(response.url).path == endpoint,
                                      timeout=180000) as received:
                action()
            response = received.value
            if response.status != expected:
                raise AssertionError(f'{endpoint}: {response.status}: {response.text()[:1000]}')
            return response.json()

        def run_job(label, endpoint, action, timeout=240):
            submitted = post_from_ui(endpoint, action, expected=202)
            check(label + ' submitted a real application job', bool(submitted.get('id')))
            if endpoint == '/api/build-car':
                report['build_job_id'] = submitted['id']
            deadline = time.monotonic() + timeout
            previous_progress = None
            while time.monotonic() < deadline:
                status = get_json('/api/job/' + submitted['id'])
                report['jobs'][label] = status
                progress = (status['state'], status.get('progress'))
                if progress != previous_progress:
                    checkpoint(label + ': ' + str(status.get('progress') or status['state']))
                    previous_progress = progress
                if status['state'] in ('error', 'cancelled'):
                    raise AssertionError(label + ': ' + json.dumps(status, ensure_ascii=False))
                if status['state'] == 'complete':
                    page.wait_for_function("() => document.getElementById('jobbar').classList.contains('hidden')")
                    return status['result']
                page.wait_for_timeout(300)
            raise AssertionError(label + ' exceeded its QA deadline; the application job was not replaced or faked')

        def import_file(path, label):
            nonlocal pid
            endpoint = '/api/import-project' if path.suffix.lower() == '.gt6car' else '/api/import'
            imported = run_job(label, endpoint, lambda: page.set_input_files('#modelInput', str(path)))
            pid = imported['id']
            page.wait_for_function("n => Number(document.getElementById('statParts').textContent) === n && !document.getElementById('confirmParts').disabled", arg=imported['part_count'])
            page.locator('#modelName').filter(has_text=imported['name']).wait_for()
            return current_project()

        def change_setting(selector, value, checkbox=False):
            def action():
                control = page.locator(selector)
                if checkbox:
                    control.set_checked(value)
                elif selector == '#carName':
                    control.fill(value)
                    control.press('Tab')
                else:
                    control.select_option(value)
            saved = post_from_ui('/api/settings', action)
            check('saved ' + selector, saved['id'] == pid)
            page.wait_for_function("() => !document.getElementById('guideTitle').textContent.startsWith('Saving')")
            return saved

        def review_fit(policy, label, capture_views=False):
            page.locator('#bodyFitPolicy').select_option(policy)
            start = len(preview_responses)
            reviewed = post_from_ui('/api/body-fit-review', lambda: page.locator('#reviewBodyFit').click())
            entry = {'report': reviewed, 'screenshots': []}
            report['reviews'][label] = entry
            save_json(label + '_review.json', reviewed)
            check(label + ' matches project and explicit policy', reviewed['id'] == pid and reviewed['policy'] == policy)
            page.wait_for_function("() => !document.getElementById('reviewBodyFit').disabled", timeout=180000)
            check(label + ' never confirms itself', not page.locator('#confirmBodyFit').is_checked())
            check(label + ' requires explicit approval', page.locator('#approveBodyFit').is_disabled())
            if reviewed['available']:
                check(label + ' displays loaded geometry', page.locator('#bodyFitStage').is_visible())
                matching = [response for response in preview_responses[start:]
                            if parse_qs(urlsplit(response.url).query).get('review_digest') == [reviewed['review_digest']]]
                check(label + ' requested its matching binary through the UI', len(matching) == 1 and matching[0].status == 200)
                raw = matching[0].body()
                check(label + ' returned a real GGV1 preview', len(raw) > 16 and raw[:4] == b'GGV1')
                length = struct.unpack_from('<I', raw, 4)[0]
                header = json.loads(raw[16:16 + length])
                check(label + ' binary carries the exact review digest', header.get('body_fit_digest') == reviewed['review_digest'])
                entry['preview'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'header': header}
                entry['preview_mode'] = page.locator('#bodyFitPreviewMode').inner_text()
                if args.software:
                    check(label + ' uses the software preview path', 'Software' in entry['preview_mode']
                          and parse_qs(urlsplit(matching[0].url).query).get('quality') == ['software'])
                check(label + ' approval follows measured profile limits', page.locator('#confirmBodyFit').is_disabled() == (not reviewed['within_profile_limits']))
                entry['metrics'] = page.locator('#bodyFitMetrics').inner_text()
                if capture_views:
                    for view, control in (('side', 'bodyFitViewSide'), ('top', 'bodyFitViewTop'), ('3d', 'bodyFitView3d')):
                        page.locator('#' + control).click()
                        entry['screenshots'].append(screenshot(label + '_' + view, '#bodyFitPanel'))
            else:
                check(label + ' unavailable fit remains blocked', page.locator('#confirmBodyFit').is_disabled()
                      and not page.locator('#bodyFitStage').is_visible() and bool(page.locator('#bodyFitIssue').inner_text()))
                entry['screenshots'].append(screenshot(label + '_unavailable', '#bodyFitPanel'))
            checkpoint(label + ' reviewed')
            return reviewed

        def approve_fit(review, label):
            check(label + ' is eligible for explicit approval', review['available'] and review['within_profile_limits'])
            page.locator('#confirmBodyFit').check()
            saved = post_from_ui('/api/approve-body-fit', lambda: page.locator('#approveBodyFit').click())
            page.locator('#bodyFitStatus').filter(has_text='Approved body fit:').wait_for()
            status = saved['body_fit_status']
            check(label + ' saved the displayed proof', status['approved'] and status['policy'] == review['policy']
                  and status['review_digest'] == review['review_digest'])
            check(label + ' preserved the new-entry destination', saved['installation'] == {'mode': 'new_entry', 'replace_car_id': None})
            return saved

        def plan(label):
            result = run_job(label, '/api/car-plan', lambda: page.locator('#reviewCarPlan').click())
            text = 'Ready to build.' if result['ready'] else 'Needs attention:'
            page.locator('#carPlan').filter(has_text=text).wait_for()
            save_json(label + '.json', result)
            return result

        try:
            checkpoint('opening owned Garage session')
            page.goto(session['url'])
            page.locator('#version').filter(has_text='Desktop preview').wait_for()
            initial_state = get_json('/api/state')
            report['application'] = {'version': initial_state['version'], 'workspace': initial_state['workspace'],
                                     'baseline': initial_state.get('car_baseline'), 'packer': initial_state.get('car_packer'),
                                     'browser_version': browser.version}
            check('wrapper supplied baseline and packer', bool(initial_state.get('car_baseline') and initial_state.get('car_packer')))
            if initial_state['projects']:
                page.wait_for_function("() => !document.getElementById('confirmParts').disabled")
            imported = import_file(args.model, 'source_import')
            report['imported_project'] = imported
            check('genuine uploaded source bytes are retained', imported['source_sha256'] == report['source_sha256'])
            check('import does not choose a destination or body policy', imported['installation']['mode'] is None
                  and not imported['body_fit_status']['approved'] and page.locator('#bodyFitPolicy').input_value() == '')
            check('reviewed Supra imports at its verified source units', imported['source_up_axis'] == '+Y' and imported['metres_per_unit'] == 1)
            check('source has all four tyre and rim corners', all(any(part['role'] == role and part.get('corner') == corner
                  for part in imported['inspection']['parts']) for role in ('tyre', 'rim') for corner in ('FL', 'FR', 'RL', 'RR')))
            screenshot('source_workshop', '#stage')
            page.locator('[data-page="export"]').click()
            if imported.get('known_recipe_available'):
                post_from_ui('/api/apply-known-recipe', lambda: page.locator('#knownCarRecipe').click())
            page.locator('[data-page="workshop"]').click()
            change_setting('#confirmParts', True, checkbox=True)
            page.locator('[data-page="export"]').click()
            change_setting('#installMode', 'new_entry')
            change_setting('#carName', args.name[:100] + ' Before Rename')
            change_setting('#confirmInheritance', True, checkbox=True)
            prepared = current_project()
            preserved_source = source_evidence(prepared)
            report['prepared_project'] = prepared
            check('source and inherited driving behavior were explicitly confirmed', prepared['approved_roles'] and prepared['template_inheritance_approved'])
            check('guide requires a body-fit decision', page.locator('#guideAction').get_attribute('data-action') == 'review_body_fit')
            blocked = plan('initial_fit_required_plan')
            check('real native plan blocks missing fit approval', not blocked['ready']
                  and not blocked['body_fit_status']['approved'] and page.locator('#buildCar').is_disabled())
            screenshot('initial_fit_required', '#bodyFitPanel')

            review_fit('keep_proportions', 'keep_original', capture_views=True)
            review_fit('adapt_body', 'adapt_before_policy_switch', capture_views=True)
            page.locator('#confirmBodyFit').check()
            page.locator('#bodyFitPolicy').select_option('keep_proportions')
            check('policy switch discards displayed geometry and confirmation', not page.locator('#confirmBodyFit').is_checked()
                  and page.locator('#approveBodyFit').is_disabled() and not page.locator('#bodyFitReview').is_visible())
            adapted = review_fit('adapt_body', 'adapt_for_approval')
            approved = approve_fit(adapted, 'first body-fit approval')

            change_setting('#installMode', 'replace')
            changed = current_project()
            report['after_destination_change'] = changed
            check('replacement is explicit and has no automatically selected target', changed['installation'] == {'mode': 'replace', 'replace_car_id': None})
            check('destination change invalidates the saved fit proof', not changed['body_fit_status']['approved']
                  and not page.locator('#bodyFitReview').is_visible() and not page.locator('#confirmBodyFit').is_checked())
            change_setting('#installMode', 'new_entry')
            report['after_destination_restore'] = current_project()['body_fit_status']
            adapted = review_fit('adapt_body', 'restored_new_entry')
            approved = approve_fit(adapted, 'restored new-entry approval')
            proof = approved['body_fit_status']['review_digest']
            renamed = change_setting('#carName', args.name)
            check('rename keeps the same verified fit while clearing displayed approval', renamed['name'] == args.name
                  and renamed['body_fit_status']['approved'] and renamed['body_fit_status']['review_digest'] == proof
                  and not page.locator('#bodyFitReview').is_visible() and not page.locator('#confirmBodyFit').is_checked())
            check('all fit decisions preserve source geometry and assignments', source_evidence(current_project()) == preserved_source)

            run_job('editable_project_export', '/api/export-project', lambda: page.locator('#exportProject').click())
            page.locator('#exportResult h2').filter(has_text='Export created').wait_for()
            archive = args.output / 'approved_body_fit.gt6car'
            with page.expect_download(timeout=120000) as download:
                page.locator('#exportResult a[href^="/download/"]').click()
            download.value.save_as(str(archive))
            report['editable_project_archive'] = {'path': str(archive), 'bytes': archive.stat().st_size, 'sha256': sha256(archive)}
            prior_pid = pid
            restored = import_file(archive, 'editable_project_reopen')
            check('reopen creates an independent project with the same source', restored['id'] != prior_pid
                  and source_evidence(restored) == preserved_source)
            check('portable project retains name, new-entry intent and verified body decision', restored['name'] == args.name
                  and restored['installation'] == {'mode': 'new_entry', 'replace_car_id': None}
                  and restored['body_fit_status']['approved'] and restored['body_fit_status']['review_digest'] == proof)
            page.locator('[data-page="export"]').click()
            check('reopening never invents a displayed preview or confirmation', not page.locator('#bodyFitReview').is_visible()
                  and not page.locator('#confirmBodyFit').is_checked())
            report['project'] = current_project()
            save_json('final_project.json', report['project'])
            final_plan = plan('final_native_plan')
            report['plan'] = final_plan
            check('real native plan permits the reviewed new separate car', final_plan['ready']
                  and final_plan['installation']['mode'] == 'new_entry'
                  and final_plan['installation']['display_name'] == args.name
                  and final_plan['body_fit_status']['approved'] and not page.locator('#buildCar').is_disabled())
            screenshot('final_native_plan', '#carPlan')
            check('frontend has no errors before the native build', not errors and not console_errors and not http_errors and not request_failures)
            built = run_job('complete_native_build', '/api/build-car', lambda: page.locator('#buildCar').click(), timeout=1800)
            report['build'] = built
            save_json('native_build_result.json', built)
            page.locator('#exportResult h2').filter(has_text='Complete native test package').wait_for()
            package = Path(built['path'])
            check('actual complete native build produced its package on disk', built.get('complete_native_package') is True
                  and built.get('native_files', 0) > 0 and bool(built.get('build_folder'))
                  and package.is_file() and package.stat().st_size == built['bytes'] and sha256(package) == built['sha256'])
            check('built identity matches the checked new-entry destination', built['installation']['mode'] == 'new_entry'
                  and built['installation']['car_id'] == final_plan['installation']['car_id']
                  and built['installation']['model_path'] == final_plan['installation']['model_path'])
            check('native result does not claim runtime acceptance', built.get('runtime_verified') is False and built.get('game_ready') is False)
            report['native_build_completed'] = True
            screenshot('complete_native_build', '#exportResult')
            check('original uploaded model remains unchanged after the build', sha256(args.model) == report['source_sha256'])
            check('frontend completed without hidden errors', not errors and not console_errors and not http_errors and not request_failures)
            report.update(passed=True, project_id=pid, preview_mode=page.locator('#previewMode').inner_text())
            checkpoint('complete')
        except Exception as exc:
            report.update(error=str(exc), traceback=traceback.format_exc())
            try:
                report['failure_ui'] = {key: page.locator('#' + key).inner_text()
                                        for key in ('notice', 'bodyFitStatus', 'bodyFitIssue', 'carPlan', 'jobstatus')}
                page.screenshot(path=str(args.output / 'failure.png'), full_page=True)
            except Exception as capture_error:
                report['failure_capture_error'] = str(capture_error)
            checkpoint('failed')
            raise
        finally:
            save_json('report.json', report)
            browser.close()
    print(json.dumps({'passed': report['passed'], 'checks': len(checks), 'project_id': pid,
                      'native_build_completed': report['native_build_completed'], 'report': str(args.output / 'report.json')}), flush=True)


if __name__ == '__main__':
    main()
