"""Explicit, portable installation intent, separate from model/donor selection."""
from __future__ import annotations
from .errors import GarageError


def installation(value=None):
    if value is None:
        return {'mode': None, 'replace_car_id': None}
    if not isinstance(value, dict) or set(value) - {'mode', 'replace_car_id'}:
        raise GarageError('Choose Add a new car or Replace an existing car.', 'installation_choice')
    mode = value.get('mode')
    if mode not in (None, 'new_entry', 'replace'):
        raise GarageError('Unknown installation mode.', 'installation_choice')
    target = value.get('replace_car_id')
    if target is not None and (type(target) is not int or not 0 <= target <= 2147483647):
        raise GarageError('Choose a valid existing car from the target list.', 'installation_target')
    if mode != 'replace' and target is not None:
        raise GarageError('A replacement target belongs only to Replace an existing car.', 'installation_target')
    return {'mode': mode, 'replace_car_id': target}


def selection_actions(value):
    choice = installation(value)
    if choice['mode'] is None:
        return ['Choose Add a new car or Replace an existing car. The donor does not choose the destination.']
    if choice['mode'] == 'replace' and choice['replace_car_id'] is None:
        return ['Select the existing car to replace. No replacement target is selected automatically.']
    return []
