"""Production export gates until a profile covers custom brake motion."""
from .errors import GarageError
from .recognition import MECHANICAL_ROLES, CORNERS


def mechanical_requirements(inspection):
    """Typed requirements shared by the source guide and production gates."""
    rows=inspection.get('parts',[]);requirements=[]
    unresolved=[r['id'] for r in rows if r.get('role_review_required') or r.get('mechanical_role_proposals')]
    if unresolved:
        requirements.append({'code':'mechanical_role_review','category':'model_review',
            'title':'Review the brake components','action':'review_parts','part_ids':unresolved,
            'message':'Review the proposed brake roles before conversion: '+', '.join(unresolved)+'. Mixed rotating discs and non-spinning calipers must be separated.'})
    mechanical=[r for r in rows if r.get('role') in MECHANICAL_ROLES]
    missing=[r['id'] for r in mechanical if r.get('corner') not in CORNERS]
    if missing:
        requirements.append({'code':'mechanical_corners','category':'model_review',
            'title':'Assign the brake components to their wheels','action':'review_parts','part_ids':missing,
            'message':'Assign one wheel corner to each brake component, or split its same-role group by wheel corner: '+', '.join(missing)+'.'})
    if mechanical:
        requirements.append({'code':'mechanical_native_route','category':'native_capability',
            'title':'Custom brakes need a supported native vehicle profile','action':'save_project',
            'part_ids':[r['id'] for r in mechanical],
            'message':'This production profile has no reviewed route for custom brake discs and calipers. Keep their assignments for a compatible native profile; they cannot be baked into the static body or emitted as rims.'})
    return requirements


def mechanical_actions(inspection):
    return [item['message'] for item in mechanical_requirements(inspection)]


def require_static_compatible(inspection):
    actions=mechanical_actions(inspection)
    if actions:raise GarageError(' '.join(actions),'mechanical_route')
