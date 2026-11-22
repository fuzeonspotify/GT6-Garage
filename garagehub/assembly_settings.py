"""Saved assembly choices, distinct from review approval and native capability."""
from __future__ import annotations

import copy

from .errors import GarageError


def assembly_mode(value='authored'):
    if value not in ('authored', 'template_shell'):
        raise GarageError('Choose your model parts or the supplied-wheel assembly.', 'assembly_mode')
    return value


def saved_shell_decision(project, *, allow_legacy=False):
    """A saved approval cannot authorize settings that differ from the UI draft."""
    from .template_shell import SCHEMA, normalize_shell_settings
    decision = project.get('template_shell_decision')
    legacy = (allow_legacy and isinstance(decision, dict)
              and type(decision.get('schema')) is int and decision['schema'] == 1)
    fields = ({'schema', 'placement', 'dispositions', 'review_digest'},
              {'schema', 'placement', 'dispositions', 'lighting', 'review_digest'}) if legacy else (
              {'schema', 'placement', 'dispositions', 'lighting', 'running_gear', 'review_digest'},)
    if (not isinstance(decision, dict)
            or set(decision) not in fields
            or type(decision.get('schema')) is not int
            or decision['schema'] != (1 if legacy else SCHEMA)
            or not isinstance(decision.get('review_digest'), str)
            or len(decision['review_digest']) != 64
            or any(c not in '0123456789abcdef' for c in decision['review_digest'])):
        raise GarageError('Preview and confirm the assembled car using the current running-gear review.', 'shell_stale')
    settings = normalize_shell_settings(project.get('template_shell_settings'))
    approved = normalize_shell_settings({key: copy.deepcopy(decision[key])
                                        for key in ('placement', 'dispositions', 'lighting', 'running_gear')
                                        if key in decision})
    if settings != approved:
        raise GarageError('The assembly settings changed after review. Preview and confirm the current car.',
                          'shell_stale')
    return copy.deepcopy(decision)


def shell_status(scene, project):
    from .template_shell import resolve_shell
    result = {'approved': False, 'review_digest': None,
              'reason': 'Preview and confirm the assembled car.',
              'runtime_verified': False, 'development_evaluation': True}
    try:
        decision = saved_shell_decision(project)
        _, _, review = resolve_shell(scene, project, decision)
        result.update(approved=True, review_digest=review['review_digest'], reason=None)
    except GarageError as exc:
        result['reason'] = str(exc)
    return result
