"""Read-only source guidance using the production profile's own requirements.

This module does not inspect a baseline, execute a packer, select a destination,
approve a model, or claim that a native package can be built. The full build
plan remains responsible for template, identity and package validation.
"""
from __future__ import annotations
import copy

from .car_geometry import alignment
from .car_profiles import PROFILE
from .errors import GarageError
from .fit_review import profile_assembly_review, reference_wheelbase_review
from .mechanical_policy import mechanical_requirements
from .recognition import CORNERS
from .wheel_fit import tyre_fit_report, wheel_source_orientation_report


def source_assessment(scene, project, *, geometry_reports=True):
    """Return the unchanged source gates and fit diagnostics for a full plan.

    Requirement order and messages remain the production plan's public action
    list. Categories are data, never inferred by parsing those messages.
    """
    inspection = project['inspection']
    parts = inspection['parts']
    roles = inspection['role_counts']
    requirements = mechanical_requirements(inspection)
    from .body_fit import resolve_body_fit
    saved_fit = project.get('body_fit')
    body_fit_status = {'approved': False, 'policy': saved_fit.get('policy') if isinstance(saved_fit, dict) else None,
                       'review_digest': saved_fit.get('review_digest') if isinstance(saved_fit, dict) else None,
                       'reason': 'Review and confirm how the body fits the vehicle template.'}
    approved_solution = None
    try:
        approved_solution = resolve_body_fit(scene, project)
        body_fit_status.update(approved=True, reason=None)
    except GarageError as exc:
        body_fit_status['reason'] = str(exc)

    def require(code, category, title, message, action='review_parts', part_ids=()):
        requirements.append({'code': code, 'category': category, 'title': title,
                             'message': message, 'action': action, 'part_ids': list(part_ids)})

    from .f50_wheel_adapter import F50_SOURCE_SHA256
    if scene.digest == F50_SOURCE_SHA256:
        require('source_native_profile', 'native_capability',
                'This source needs its F50 native vehicle profile',
                'The Ferrari F50 needs its separately reviewed native F50/RX-8 donor profile and standalone database identity. Do not overwrite the Supra slot.',
                'save_project')
    if not project.get('approved_roles'):
        require('confirm_source_review', 'model_review', 'Confirm your component review',
                'Confirm the reviewed component roles and front direction.', 'confirm_parts')
    for issue in inspection.get('issues', []):
        if issue.get('code') == 'paint_ambiguous':
            require('paint_ambiguous', 'model_review', 'Choose the paint surfaces',
                    issue['message'], 'review_paint', issue.get('parts', ()))
    for role, label, title in [('headlight', 'headlamp emitting surface', 'Identify the headlights'),
                               ('taillight', 'running/brake tail-light emitting surface', 'Identify the rear lights')]:
        if not roles.get(role):
            require('missing_'+role, 'model_review', title, 'Assign at least one '+label+'.')
    if any(not any(p['role'] == 'rim' and p['corner'] == c for p in parts) for c in CORNERS):
        require('four_rims', 'model_review', 'Assign a rim to each wheel',
                'Assign a custom rim to each wheel corner.', 'review_wheels',
                [p['id'] for p in parts if p['role'] == 'rim'])
    matrix = translation = None
    try:
        if approved_solution is not None:
            matrix, translation, reviewed_fit = approved_solution
            fit = copy.deepcopy(reviewed_fit['fit'])
            fit['body_fit'] = reviewed_fit
        else:
            # Advisory measurements are not an implicit fitting decision.
            matrix, translation, fit = alignment(inspection)
    except GarageError as exc:
        fit = {'maximum_wheel_centre_error_metres': None, 'available': False,
               'fit_scope': 'wheel_centres_only', 'dimensional_review_required': True}
        if isinstance(exc.detail, dict) and exc.detail.get('reason') == 'profile_fit_ratio':
            require('profile_fit_ratio', 'native_capability', 'Wheel proportions need another vehicle profile',
                    str(exc), 'save_project')
        else:
            # Missing, contradictory or stale source measurements remain
            # review tasks. A labelled profile boundary is a tool limitation.
            require('wheel_layout', 'model_review', 'Review the four tyre positions and scale',
                    str(exc), 'review_wheels', [p['id'] for p in parts if p['role'] == 'tyre'])
    if geometry_reports:
        fit['tyre_fit'] = tyre_fit_report(scene, inspection, matrix, translation)
    fit['assembly'] = profile_assembly_review(inspection, project.get('wheelbase_metres'))
    fit['source_units'] = reference_wheelbase_review(fit['assembly'])
    if fit['source_units'].get('action'):
        require('source_units', 'model_review', 'Check the model size',
                fit['source_units']['action'], 'review_scale')
    masked = {p['material'] for p in parts if p['role'] != 'ignore'
              and scene.materials[p['material']].get('alpha_mode', 'OPAQUE') == 'MASK'}
    if masked:
        require('alpha_cutout', 'native_capability', 'Alpha-cutout materials need native support',
                'This donor profile cannot yet reproduce alpha-cutout materials: '
                + ', '.join(str(i) for i in sorted(masked))+'. They will not be silently converted to glass.',
                'save_project', [p['id'] for p in parts if p['role'] != 'ignore' and p['material'] in masked])
    source_wb = project.get('wheelbase_metres')
    explicitly_adapted = body_fit_status['approved'] and body_fit_status['policy'] == 'adapt_body'
    if source_wb and abs(source_wb-2.55) > .08 and not explicitly_adapted:
        require('profile_wheelbase', 'native_capability', 'The entered wheelbase needs another vehicle profile',
                'The chosen 2.55 m Supra chassis differs from the entered wheelbase. Use a matching profile rather than silently stretching the car.',
                'review_scale')
    textured = [p['id'] for p in parts if p['role'] == 'rim'
                and scene.materials[p['material']]['base_texture'] is not None]
    if textured:
        require('textured_rims', 'native_capability', 'Textured rims need native support',
                'The selected Supra native rim recipe cannot transfer textured rim surfaces. Use a compatible donor/profile and reviewed texture recipe.',
                'save_project', textured)
    if not project.get('template_inheritance_approved'):
        require('template_inheritance', 'build_review', 'Review what the vehicle template supplies',
                'Confirm the disclosed inherited chassis, tyre, driver and shadow behavior.',
                'review_inheritance')
    if not body_fit_status['approved']:
        require('body_fit_review', 'build_review', 'Preview and confirm the body fit',
                body_fit_status['reason'], 'review_body_fit')
    return {'requirements': requirements, 'fit': fit,
            'body_fit_status': body_fit_status,
            'source_orientation': wheel_source_orientation_report(scene, inspection) if geometry_reports else None}


_TASK_ORDER = {'wheel_layout': 0, 'source_units': 1, 'four_rims': 2,
               'mechanical_role_review': 3, 'mechanical_corners': 4,
               'paint_ambiguous': 5, 'missing_headlight': 6, 'missing_taillight': 7,
               'confirm_source_review': 100}


def import_readiness(scene, project):
    """Small JSON summary available immediately, before game-file setup."""
    assessment = source_assessment(scene, project, geometry_reports=False)

    def tasks(category):
        rows = [{'code': r['code'], 'title': r['title'], 'detail': r['message'],
                 'action': r['action'], 'part_ids': list(r['part_ids'])}
                for r in assessment['requirements'] if r['category'] == category]
        return sorted(rows, key=lambda row: _TASK_ORDER.get(row['code'], 50))

    user_tasks = tasks('model_review')
    return {'schema_version': 1, 'scope': 'source_preflight',
            'profile': {'id': PROFILE['id'], 'label': PROFILE['label']},
            'source_sha256': scene.digest, 'user_tasks': user_tasks,
            'capability_limits': tasks('native_capability'),
            'build_review_tasks': tasks('build_review'),
            'body_fit_status': assessment['body_fit_status'],
            'source_review_complete': not user_tasks,
            'build_readiness_checked': False, 'runtime_verified': False}
