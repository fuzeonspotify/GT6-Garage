"""Real DOM supplied-wheel assembly, portable project and one native build.

Runs a separate headless Edge/Chromium against a caller-owned Garage session.
All project mutations, reviews, approvals and jobs use the visible application
controls. Read-only authenticated requests inspect results. It never launches
GT6, installs a package or controls another desktop process.
"""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import re
import struct
import sys
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
    for key in ('session', 'browser', 'model', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--exclude-wheels', action='store_true')
    parser.add_argument('--scale', type=float, default=1.)
    parser.add_argument('--y', type=float, default=0.)
    parser.add_argument('--z', type=float, default=0.)
    parser.add_argument('--front-light-height', type=float, help='Supplied front-light height ratio, 0.15 to 0.80')
    parser.add_argument('--rear-light-height', type=float, help='Supplied rear-light height ratio, 0.15 to 0.80')
    parser.add_argument('--light-spacing', type=float, help='Each supplied light\'s distance from centre / body width, 0.15 to 0.40')
    parser.add_argument('--software', action='store_true')
    args = parser.parse_args()
    args.name = args.name.strip()
    if not args.name or len(args.name) > 120:
        parser.error('--name must have 1 to 120 characters')
    if not all(math.isfinite(x) for x in (args.scale, args.y, args.z)) or args.scale <= 0:
        parser.error('Placement must be finite and --scale must be positive')
    requested_lighting = {}
    for flag, key, value, maximum in (
        ('--front-light-height', 'front_height_ratio', args.front_light_height, .80),
        ('--rear-light-height', 'rear_height_ratio', args.rear_light_height, .80),
        ('--light-spacing', 'half_spacing_ratio', args.light_spacing, .40)):
        if value is None:
            continue
        if not math.isfinite(value) or not .15 <= value <= maximum:
            parser.error(flag + ' must be a finite ratio between 0.15 and ' + str(maximum))
        requested_lighting[key] = value
    from playwright.sync_api import sync_playwright

    args.output.mkdir(parents=True, exist_ok=True)
    session = json.loads(args.session.read_text(encoding='utf-8-sig'))
    base, token = session['url'].split('#', 1)
    base = base.rstrip('/') + '/'
    headers = {'X-Garage-Token': token}
    checks, errors, console_errors, http_errors, request_failures = [], [], [], [], []
    report = {
        'passed': False, 'kind': 'actual_browser_template_shell_and_native_new_entry',
        'game_launched': False, 'runtime_verified': False, 'native_build_completed': False,
        'native_build_count': 0, 'software_requested': args.software,
        'source_file': str(args.model), 'source_sha256': sha256(args.model),
        'placement_requested': {'scale': args.scale, 'translation_metres': [0., args.y, args.z]},
        'exclude_wheels_requested': args.exclude_wheels,
        'lighting_requested': {'mode': 'supplied', **requested_lighting},
        'checks': checks, 'javascript_errors': errors, 'console_errors': console_errors,
        'http_errors': http_errors, 'request_failures': request_failures,
        'reviews': {}, 'jobs': {}, 'screenshots': [], 'picking': [],
        'scope': 'Actual headless application UI and native package; reference-pose geometry and simplified materials. No game/loaded-suspension/clearance acceptance.'}
    started = time.monotonic()

    def redact(value):
        if isinstance(value, str):
            return value.replace(token, '[REDACTED]')
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [redact(item) for item in value]
        return value

    def save_json(name, value):
        (args.output / name).write_text(json.dumps(redact(value), indent=2,
            ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')

    def checkpoint(phase):
        report.update(phase=phase, elapsed_seconds=round(time.monotonic() - started, 2))
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
        page.on('pageerror', lambda error: errors.append(redact(str(error))))
        page.on('console', lambda message: console_errors.append(redact({
            'text': message.text, 'location': message.location})) if message.type == 'error' else None)
        page.on('requestfailed', lambda request: request_failures.append({
            'path': urlsplit(request.url).path, 'failure': redact(request.failure)}))
        page.on('dialog', lambda dialog: dialog.accept())
        preview_responses = []

        def observe_response(response):
            path = urlsplit(response.url).path
            if path.startswith('/api/template-shell-preview/'):
                preview_responses.append(response)
            if response.status >= 400:
                http_errors.append({'path': path, 'status': response.status})

        page.on('response', observe_response)
        pid = None

        def get_json(path):
            response = page.request.get(base + path.lstrip('/'), headers=headers, timeout=120000)
            if response.status != 200:
                raise AssertionError(f'GET {path}: {response.status}: {redact(response.text()[:500])}')
            return response.json()

        def current_project():
            return get_json('/api/project/' + pid)

        def source_evidence(project):
            return {key: project.get(key) for key in ('source_sha256', 'source_up_axis',
                'metres_per_unit', 'forward', 'overrides', 'wheel_partitions',
                'inspection', 'part_count', 'triangles')}

        def frames():
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

        def screenshot(name, selector='#shellStage'):
            frames()
            path = args.output / (name + '.png')
            page.locator(selector).screenshot(path=str(path))
            report['screenshots'].append({'path': str(path), 'sha256': sha256(path),
                'source': 'actual headless Garage preview', 'game_footage': False})
            return str(path)

        def post_from_ui(endpoint, action, expected=200):
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == endpoint, timeout=180000) as received:
                action()
            response = received.value
            if response.status != expected:
                raise AssertionError(f'{endpoint}: {response.status}: {redact(response.text()[:1200])}')
            return response.json()

        def run_job(label, endpoint, action, timeout=240):
            if endpoint == '/api/build-car':
                check('only one native build is requested', report['native_build_count'] == 0)
                report['native_build_count'] += 1
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
                    raise AssertionError(label + ': ' + json.dumps(redact(status), ensure_ascii=False))
                if status['state'] == 'complete':
                    page.wait_for_function("() => document.getElementById('jobbar').classList.contains('hidden')")
                    return status['result']
                page.wait_for_timeout(300)
            raise AssertionError(label + ' exceeded its deadline; no replacement job was started')

        def import_file(path, label):
            nonlocal pid
            endpoint = '/api/import-project' if path.suffix.lower() == '.gt6car' else '/api/import'
            imported = run_job(label, endpoint, lambda: page.set_input_files('#modelInput', str(path)))
            pid = imported['id']
            page.wait_for_function("value => Number(document.getElementById('statParts').textContent) === value.parts && !document.getElementById('confirmParts').disabled && document.getElementById('guideAction').dataset.projectId === value.id", arg={'parts': imported['part_count'], 'id': pid})
            page.locator('#modelName').filter(has_text=imported['name']).wait_for()
            return current_project()

        def change_setting(selector, value, kind='select'):
            control = page.locator(selector)
            current = control.is_checked() if kind == 'checkbox' else control.input_value()
            same = current == value if kind == 'checkbox' else (float(current) == float(value) if kind == 'number' and current else current == str(value))
            if same:
                return current_project()
            def action():
                if kind == 'checkbox':
                    control.set_checked(value)
                elif kind in ('number', 'text'):
                    control.fill(str(value));control.press('Tab')
                else:
                    control.select_option(value)
            saved = post_from_ui('/api/settings', action)
            check('saved ' + selector, saved['id'] == pid)
            page.wait_for_function("() => !document.getElementById('guideTitle').textContent.startsWith('Saving')")
            return saved

        def apply_component_dispositions(imported):
            parts = imported['inspection']['parts']
            selected = []
            for index, part in enumerate(parts):
                wheel = part['role'] in ('rim', 'tyre', 'valve', 'brake_rotor', 'brake_caliper') or bool(re.search(r'(^|[^a-z])wheels?([^a-z]|$)', part['name'], re.I))
                lamp = part['role'] in ('headlight', 'taillight', 'brakelight')
                if (args.exclude_wheels and wheel) or lamp:
                    selected.append((index, part, 'Replaced by the supplied wheel assembly.' if wheel else 'Replaced by the supplied light kit.'))
            if not selected:
                return current_project()
            if page.locator('#shellComponents').get_attribute('open') is None:
                page.locator('#shellComponents > summary').click()
            for index, part, reason in selected:
                row = page.locator('.shellComponentRow').nth(index)
                check('component choice targets inspected source ' + part['id'], row.locator('strong').inner_text() == part['name'])
                row.locator('select').select_option('ignore')
                row.locator('input').fill(reason)
            check('unsaved exclusions block assembly preview', page.locator('#reviewTemplateShell').is_disabled())
            saved = post_from_ui('/api/settings', lambda: page.locator('#saveShellComponents').click())
            choices = saved['template_shell_settings']['dispositions']
            check('explicit exclusions and roles persist together', all(choices[part['id']]['reason'] == reason
                and saved['overrides'][part['id']]['role'] == 'ignore' for _, part, reason in selected))
            check('component edit requires renewed manual source review', saved['approved_roles'] is False)
            report['excluded_source_components'] = [{'id': part['id'], 'name': part['name'],
                'triangles': part['triangles'], 'reason': reason} for _, part, reason in selected]
            page.locator('#shellComponents > summary').click()
            return saved

        def review_shell(label, capture=False, observed=None, preview_start=None):
            start = len(preview_responses) if preview_start is None else preview_start
            reviewed = observed if observed is not None else post_from_ui('/api/template-shell-review', lambda: page.locator('#reviewTemplateShell').click())
            entry = {'report': reviewed};report['reviews'][label] = entry;save_json(label + '_review.json', reviewed)
            check(label + ' identifies the saved source and project', reviewed['id'] == pid and reviewed['source']['source_sha256'] == report['source_sha256'])
            check(label + ' is a complete bounded assembly candidate', reviewed['available'] and reviewed['ready_for_candidate'] and reviewed['preview_available'])
            page.wait_for_function("() => !document.getElementById('reviewTemplateShell').disabled", timeout=180000)
            check(label + ' displays actual assembled geometry without auto confirmation', page.locator('#shellStage').is_visible()
                and not page.locator('#confirmShellAssembly').is_checked() and page.locator('#approveShellAssembly').is_disabled())
            matching = [response for response in preview_responses[start:]
                if parse_qs(urlsplit(response.url).query).get('review_digest') == [reviewed['review_digest']]]
            check(label + ' UI requested its matching binary', len(matching) == 1 and matching[0].status == 200)
            raw = matching[0].body()
            check(label + ' returned genuine GGV1 geometry', len(raw) > 16 and raw[:4] == b'GGV1')
            length, vertices, indices = struct.unpack_from('<III', raw, 4)
            header = json.loads(raw[16:16 + length])
            check(label + ' binary binds the exact review and actual native wheels', header.get('template_shell_digest') == reviewed['review_digest']
                and header.get('actual_native_wheel_geometry') is True)
            parts = reviewed['preview_parts'];ids = {part['id'] for part in parts}
            check(label + ' every preview mesh has declared component ownership', all(mesh['id'] in ids for mesh in header['meshes']))
            source = [part for part in parts if part.get('component_origin') == 'source']
            wheels = [part for part in parts if part.get('component_origin') == 'native_template']
            lamps = [part for part in parts if part.get('component_origin') == 'supplied_lamp_kit']
            check(label + ' includes source body and separately owned supplied parts', bool(source) and bool(wheels) and bool(lamps))
            check(label + ' body and supplied lamps move while native wheels stay fixed', all(p.get('moves_with_body') is True for p in source + lamps)
                and all(p.get('moves_with_body') is False for p in wheels))
            check(label + ' source accounting is complete', sum(reviewed['triangle_dispositions'].values()) == current_project()['triangles'])
            mode = page.locator('#shellPreviewMode').inner_text()
            if args.software:
                check(label + ' uses the software preview route', 'Software' in mode
                    and parse_qs(urlsplit(matching[0].url).query).get('software') == ['1'])
            entry['preview'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                'vertices': vertices, 'indices': indices, 'header': header, 'mode': mode,
                'source_parts': len(source), 'supplied_wheel_parts': len(wheels), 'generated_lamp_parts': len(lamps)}
            if capture:
                for view, button in (('side', 'shellViewSide'), ('3d', 'shellView3d')):
                    page.locator('#' + button).click();screenshot(label + '_' + view)
                # The UI exposes orbit instead of a Front shortcut. Orbit the
                # actual canvas from its reset 3D camera, without injecting state.
                canvas = page.locator('#shellViewer');box = canvas.bounding_box()
                check('assembled canvas can be inspected', box and box['width'] > 500 and box['height'] > 200)
                x, y = box['x'] + box['width'] * .82, box['y'] + box['height'] * .55
                page.mouse.move(x, y);page.mouse.down();page.mouse.move(x - (math.pi - .65) / .007, y + (.12 - .28) / .007, steps=20);page.mouse.up()
                screenshot(label + '_front_orbit')
                page.locator('#shellView3d').click();inspect_pointer_highlight(raw, header, parts, label)
            checkpoint(label + ' reviewed')
            return reviewed

        def inspect_body_drag(initial_review):
            before = current_project();before_placement = before['template_shell_settings']['placement']
            page.locator('#shellViewSide').click()
            canvas = page.locator('#shellViewer');canvas.scroll_into_view_if_needed();box = canvas.bounding_box()
            check('side canvas is available for a real body drag', box and box['width'] > 200 and box['height'] > 150)
            x, y = box['x'] + box['width'] * .5, box['y'] + box['height'] * .5
            page.mouse.move(x, y);page.mouse.down()
            check('body drag disables placement and approval controls at pointer start', all(
                page.locator(selector).is_disabled() for selector in (
                    '#shellHeight', '#shellForward', '#shellScale', '#confirmShellAssembly', '#approveShellAssembly')))
            page.mouse.move(x + 18, y - 12, steps=6)
            check('body movement clears visual confirmation before release', not page.locator('#confirmShellAssembly').is_checked())
            preview_start = len(preview_responses)
            with page.expect_response(lambda response: response.request.method == 'POST'
                    and urlsplit(response.url).path == '/api/template-shell-review', timeout=180000) as refreshed:
                saved = post_from_ui('/api/settings', lambda: page.mouse.up())
            placement = saved['template_shell_settings']['placement']
            old_xyz, new_xyz = before_placement['translation_metres'], placement['translation_metres']
            check('real Side drag saves upward and forward body movement', new_xyz[0] == old_xyz[0]
                and new_xyz[1] > old_xyz[1] and new_xyz[2] < old_xyz[2]
                and all(math.isfinite(value) for value in new_xyz))
            check('body drag retains source units roles lights exclusions and body proportions', source_evidence(saved) == source_evidence(before)
                and placement['scale'] == before_placement['scale'] and placement['flip_front'] == before_placement['flip_front']
                and all(saved['template_shell_settings'][key] == before['template_shell_settings'][key] for key in ('lighting', 'dispositions')))
            check('body drag saves without assembly approval', saved['template_shell_status']['approved'] is False)
            response = refreshed.value
            check('body release automatically requests a new assembly review', response.status == 200)
            dragged = review_shell('drag_assembly', observed=response.json(), preview_start=preview_start)
            check('body drag produces a different current preview digest', dragged['review_digest'] != initial_review['review_digest'])
            report['body_drag'] = {'pointer_delta_pixels': [18, -12], 'before': before_placement,
                'after': placement, 'review_digest': dragged['review_digest'], 'source_preserved': True}
            screenshot('drag_assembly_side')
            change_setting('#shellHeight', args.y, 'number');change_setting('#shellForward', -args.z, 'number')
            restored = current_project()
            check('visible controls restore the requested placement without restoring approval',
                restored['template_shell_settings']['placement'] == before_placement
                and source_evidence(restored) == source_evidence(before)
                and restored['template_shell_status']['approved'] is False)
            return review_shell('restored_assembly')

        def inspect_pointer_highlight(raw, header, parts, label):
            """Use decoded triangles to aim real clicks; do not inject selection."""
            length, nv, ni = struct.unpack_from('<III', raw, 4)
            values = array.array('f');values.frombytes(raw[16 + length:16 + length + nv * 24])
            faces = array.array('I');faces.frombytes(raw[16 + length + nv * 24:16 + length + nv * 24 + ni * 4])
            if sys.byteorder != 'little':
                values.byteswap();faces.byteswap()
            low, high = header['bounds'];center = [(a + b) / 2 for a, b in zip(low, high)]
            radius = max(b - a for a, b in zip(low, high)) * .58;distance = radius * 2.8
            yaw, pitch = .65, .28
            zaxis = [math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)]
            xaxis = [math.cos(yaw), 0, -math.sin(yaw)]
            yaxis = [-math.sin(yaw) * math.sin(pitch), math.cos(pitch), -math.cos(yaw) * math.sin(pitch)]
            eye = [v + distance * axis for v, axis in zip(center, zaxis)]
            lookup = {part['id']: part for part in parts}
            canvas = page.locator('#shellViewer');canvas.scroll_into_view_if_needed();box = canvas.bounding_box()
            aspect = box['width'] / box['height'];focal = 1 / math.tan(.65 / 2)
            for origin in ('source', 'native_template'):
                candidates = []
                for mesh in header['meshes']:
                    if lookup[mesh['id']].get('component_origin') != origin:
                        continue
                    for index in range(mesh['index_start'], mesh['index_start'] + mesh['index_count'], 3):
                        point = [sum(values[faces[index + j] * 6 + axis] for j in range(3)) / 3 for axis in range(3)]
                        delta = [v - e for v, e in zip(point, eye)]
                        depth = -sum(a * b for a, b in zip(delta, zaxis))
                        if depth <= 0:
                            continue
                        x = sum(a * b for a, b in zip(delta, xaxis)) * focal / aspect / depth
                        y = sum(a * b for a, b in zip(delta, yaxis)) * focal / depth
                        if abs(x) < .93 and abs(y) < .88:
                            candidates.append((depth, mesh['id'], box['x'] + (.5 + x / 2) * box['width'], box['y'] + (.5 - y / 2) * box['height']))
                candidates.sort()
                changed = None
                for _, part_id, x, y in candidates[:40]:
                    before = canvas.screenshot();page.mouse.click(x, y);frames();after = canvas.screenshot()
                    if before != after:
                        changed = {'target_triangle_part': part_id, 'component_origin': origin,
                            'before_sha256': hashlib.sha256(before).hexdigest(), 'after_sha256': hashlib.sha256(after).hexdigest()}
                        break
                check(label + ' real pointer click changes highlight for visible ' + origin + ' geometry', changed is not None)
                report['picking'].append({**changed, 'scope': 'Pointer/render response at a projected triangle; exact selected-ID ownership is not exposed by the public UI.'})

        def approve_shell(review, label):
            page.locator('#confirmShellAssembly').check()
            saved = post_from_ui('/api/approve-template-shell', lambda: page.locator('#approveShellAssembly').click())
            page.locator('#shellStatus').filter(has_text='Assembly approved').wait_for()
            status = saved['template_shell_status']
            check(label + ' saves the displayed assembly proof', status['approved'] and status['review_digest'] == review['review_digest'])
            check(label + ' keeps a separate-car destination', saved['installation'] == {'mode': 'new_entry', 'replace_car_id': None})
            return saved

        try:
            checkpoint('opening owned Garage session')
            page.goto(session['url']);page.locator('#version').filter(has_text='Desktop preview').wait_for()
            state = get_json('/api/state')
            report['application'] = {'version': state['version'], 'workspace': state['workspace'],
                'baseline': state.get('car_baseline'), 'packer': state.get('car_packer'), 'browser_version': browser.version}
            check('caller configured owned baseline and packer', bool(state.get('car_baseline') and state.get('car_packer')))
            if state['projects']:
                page.wait_for_function("() => !document.getElementById('confirmParts').disabled")
            imported = import_file(args.model, 'source_import');report['imported_project'] = imported
            check('uploaded source bytes are retained', imported['source_sha256'] == report['source_sha256'])
            change_setting('#assemblyMode', 'template_shell')
            page.locator('[data-page="export"]').click()
            check('supplied-wheel method replaces authored fit controls', page.locator('#templateShellPanel').is_visible() and not page.locator('#bodyFitPanel').is_visible())
            change_setting('#installMode', 'new_entry');change_setting('#carName', args.name, 'text')
            change_setting('#shellLights', 'supplied')
            if requested_lighting:
                if page.locator('#shellLightAdjust').get_attribute('open') is None:
                    page.locator('#shellLightAdjust > summary').click()
                for selector, key in (('#shellFrontLightHeight', 'front_height_ratio'),
                                      ('#shellRearLightHeight', 'rear_height_ratio'),
                                      ('#shellLightSpacing', 'half_spacing_ratio')):
                    if key in requested_lighting:
                        change_setting(selector, requested_lighting[key] * 100, 'number')
                lighting = current_project()['template_shell_settings']['lighting']
                check('advanced supplied-light settings persist through visible percentage controls',
                    all(math.isclose(lighting[key], value, abs_tol=1e-12) for key, value in requested_lighting.items()))
                page.locator('#shellLightAdjust > summary').click()
            apply_component_dispositions(current_project())
            change_setting('#shellScale', 1.1, 'number')
            flipped = post_from_ui('/api/settings', lambda: page.locator('#shellFlip').click())
            check('Flip front is an explicit saved placement choice', flipped['template_shell_settings']['placement']['flip_front'] is True)
            original = post_from_ui('/api/settings', lambda: page.locator('#shellOriginalSize').click())
            check('Original size changes uniform size without changing units or flip', original['template_shell_settings']['placement']['scale'] == 1
                and original['template_shell_settings']['placement']['flip_front'] is True and original['metres_per_unit'] == imported['metres_per_unit'])
            reset = post_from_ui('/api/settings', lambda: page.locator('#shellReset').click())
            check('Reset placement restores size orientation and position', reset['template_shell_settings']['placement'] == {'scale': 1., 'translation_metres': [0., 0., 0.], 'flip_front': False})
            change_setting('#shellScale', args.scale, 'number');change_setting('#shellHeight', args.y, 'number');change_setting('#shellForward', -args.z, 'number')
            change_setting('#confirmShellComponents', True, 'checkbox');change_setting('#confirmInheritance', True, 'checkbox')
            prepared = current_project();preserved = source_evidence(prepared);report['prepared_project'] = prepared
            check('source and supplied setup are explicitly confirmed', prepared['approved_roles'] and prepared['template_inheritance_approved'])
            check('guide asks for assembly without source-wheel requirements', page.locator('#guideAction').get_attribute('data-action') == 'review_shell')
            reviewed = review_shell('initial_assembly', capture=True)
            reviewed = inspect_body_drag(reviewed)
            approved = approve_shell(reviewed, 'initial approval');proof = approved['template_shell_status']['review_digest']
            run_job('editable_project_export', '/api/export-project', lambda: page.locator('#exportProject').click())
            archive = args.output / 'reviewed_template_shell.gt6car'
            with page.expect_download(timeout=120000) as download:
                page.locator('#exportResult a[href^="/download/"]').click()
            download.value.save_as(str(archive))
            report['editable_project_archive'] = {'path': str(archive), 'bytes': archive.stat().st_size, 'sha256': sha256(archive)}
            previous_pid = pid;restored = import_file(archive, 'editable_project_reopen')
            check('archive reopens independent project with identical source and assignments', restored['id'] != previous_pid and source_evidence(restored) == preserved)
            check('archive retains assembly placement lights exclusions and approved proof', restored['assembly_mode'] == 'template_shell'
                and restored['template_shell_settings'] == approved['template_shell_settings'] and restored['template_shell_status']['approved']
                and restored['template_shell_status']['review_digest'] == proof)
            page.locator('[data-page="export"]').click()
            check('reopen does not invent a visible preview or confirmation', not page.locator('#shellStage').is_visible() and not page.locator('#confirmShellAssembly').is_checked())
            moved = change_setting('#shellHeight', args.y + .03, 'number')
            check('placement change invalidates saved approval and build', not moved['template_shell_status']['approved']
                and page.locator('#approveShellAssembly').is_disabled() and page.locator('#buildCar').is_disabled())
            change_setting('#shellHeight', args.y, 'number')
            check('restoring a coordinate does not silently restore approval', not current_project()['template_shell_status']['approved'])
            reviewed = review_shell('final_assembly');approved = approve_shell(reviewed, 'final approval')
            report['project'] = current_project();save_json('final_project.json', report['project'])
            check('assembly review preserves source geometry and assignments', source_evidence(report['project']) == preserved)
            final_plan = run_job('final_native_plan', '/api/car-plan', lambda: page.locator('#reviewCarPlan').click())
            report['plan'] = final_plan;save_json('final_native_plan.json', final_plan)
            page.locator('#carPlan').filter(has_text='Ready to build.').wait_for()
            check('actual plan permits reviewed separate-car assembly', final_plan['ready'] and final_plan['assembly_mode'] == 'template_shell'
                and final_plan['installation']['mode'] == 'new_entry' and final_plan['installation']['display_name'] == args.name
                and not page.locator('#buildCar').is_disabled())
            check('plan renders uniform shell placement without fabricated axle fit', 'Uniform body size:' in page.locator('#carPlan').inner_text()
                and 'Source axle measurements are not required.' in page.locator('#carPlan').inner_text())
            check('browser has no errors before native build', not errors and not console_errors and not http_errors and not request_failures)
            built = run_job('complete_native_build', '/api/build-car', lambda: page.locator('#buildCar').click(), timeout=1800)
            report['build'] = built;save_json('native_build_result.json', built)
            page.locator('#exportResult h2').filter(has_text='Complete native test package').wait_for()
            package = Path(built['path'])
            check('one complete native build created its package on disk', report['native_build_count'] == 1 and built.get('complete_native_package') is True
                and built.get('native_files', 0) > 0 and bool(built.get('build_folder')) and package.is_file()
                and package.stat().st_size == built['bytes'] and sha256(package) == built['sha256'])
            check('built identity matches reviewed separate destination', built['installation']['mode'] == 'new_entry'
                and built['installation']['car_id'] == final_plan['installation']['car_id'] and built['installation']['model_path'] == final_plan['installation']['model_path'])
            check('native result does not claim game acceptance', built.get('runtime_verified') is False and built.get('game_ready') is False)
            downloaded = args.output / 'native_candidate_download.zip'
            with page.expect_download(timeout=180000) as download:
                page.locator('#exportResult a[href^="/download/"]').click()
            download.value.save_as(str(downloaded))
            check('player download serves the exact built package', downloaded.stat().st_size == built['bytes'] and sha256(downloaded) == built['sha256'])
            report['downloaded_package'] = {'path': str(downloaded), 'bytes': downloaded.stat().st_size, 'sha256': sha256(downloaded)}
            report['native_build_completed'] = True;screenshot('complete_native_build', '#exportResult')
            check('original input remains unchanged after the complete flow', sha256(args.model) == report['source_sha256'])
            check('browser completed without hidden errors', not errors and not console_errors and not http_errors and not request_failures)
            report.update(passed=True, project_id=pid);checkpoint('complete')
        except Exception as exc:
            report.update(error=redact(str(exc)), traceback=redact(traceback.format_exc()))
            try:
                report['failure_ui'] = {key: page.locator('#' + key).inner_text()
                    for key in ('notice', 'shellStatus', 'shellIssues', 'carPlan', 'jobstatus')}
                page.screenshot(path=str(args.output / 'failure.png'), full_page=True)
            except Exception as capture_error:
                report['failure_capture_error'] = redact(str(capture_error))
            checkpoint('failed')
            raise RuntimeError(redact(str(exc))) from None
        finally:
            save_json('report.json', report);browser.close()
    print(json.dumps({'passed': report['passed'], 'checks': len(checks), 'project_id': pid,
        'native_build_completed': report['native_build_completed'], 'report': str(args.output / 'report.json')}), flush=True)


if __name__ == '__main__':
    main()
