"""Explainable component inference: names, explicit tags, materials and symmetry.
No network, model weights or language model. Low-confidence choices stay visible.
"""
from __future__ import annotations
import re, math
import numpy as np
from .errors import GarageError
from .glb import Scene

ROLES=('body','paint','glass','headlight','taillight','brakelight','rim','tyre','valve','brake_rotor','brake_caliper','interior','ignore')
CORNERS=('FL','FR','RL','RR')
WHEEL_ROLES=('rim','tyre','valve')
MECHANICAL_ROLES=('brake_rotor','brake_caliper')
CORNER_ROLES=WHEEL_ROLES+MECHANICAL_ROLES
MOTION_INTENTS={**{role:'wheel_spin' for role in WHEEL_ROLES},
                'brake_rotor':'wheel_spin','brake_caliper':'upright_nonspin'}


def _assignment(part,overrides):
    """Resolve semantic authority before geometry can propose another role.

    A saved unassigned corner is deliberate and must remain unassigned. An
    authored wheel role without a valid corner can still receive a spatial
    corner proposal; its role itself is never a geometric guess.
    """
    if part.id in overrides:
        value=overrides[part.id]
        if not isinstance(value,dict) or value.get('role') not in ROLES:
            raise GarageError('Unknown component role.')
        role=value['role'];corner=value.get('corner') or None
        if corner is not None and corner not in CORNERS:raise GarageError('Unknown wheel corner.')
        return {'role':role,'corner':corner if role in CORNER_ROLES else None,
                'corner_fixed':True,'confidence':1.,'reason':'Your saved assignment','priority':2}
    role=part.extras.get('gt6_role')
    if role not in ROLES:return None
    corner=part.extras.get('gt6_corner')
    corner=corner if role in CORNER_ROLES and corner in CORNERS else None
    return {'role':role,'corner':corner,'corner_fixed':corner is not None,
            'confidence':.99,'reason':'Blender/GLB role tag','priority':1}


def _tyre_dimensions(scene,records,long):
    """Measure only the currently assigned tyre groups, including split parts."""
    centers={};radii={}
    for k in CORNERS:
        pp=[p for p in scene.parts if records[p.id]['role']=='tyre' and records[p.id]['corner']==k]
        if pp:
            bounds=np.array([np.min([p.bounds[0] for p in pp],axis=0),np.max([p.bounds[1] for p in pp],axis=0)])
            centers[k]=bounds.mean(0);radii[k]=float(max(bounds[1,1]-bounds[0,1],bounds[1,long]-bounds[0,long])/2)
    return centers,radii

def _words(name):
    # Asset exporters mix HeadLampL, HEADLamp_L, head_lamp.L and numbered copies.
    name = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', name)
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    return ' '.join(re.findall(r'[a-z]+', name.casefold()))


_AUX_LAMP = re.compile(r'\b(?:blinkers?|indicators?|turn ?signals?|turn ?lights?|directionals?|'
                       r'revers(?:e|ing)(?: ?(?:lights?|lamps?))?|back ?up ?(?:lights?|lamps?)|'
                       r'fog(?:s| ?(?:lights?|lamps?))?|side ?markers?|reflectors?|'
                       r'(?:cop|police|emergency|warning|dash|interior|roof) ?(?:lights?|lamps?)|'
                       r'strobes?|beacons?|sirens?|light ?bars?|drls?|daytime ?running)\b')
_LAMP_HARDWARE = re.compile(r'\b(?:housings?|buckets?|bezels?|brackets?|surrounds?|seals?|'
                            r'frames?|mounts?|trims?|inserts?|backs?|covers?)\b')
_WHEEL_TRIM = re.compile(r'\b(?:wheel ?(?:arches|arch|wells?|houses?|housings?|liners?|openings?)|'
                         r'(?:arch|well) ?liners?|mud ?(?:flaps?|guards?)|fenders?|'
                         r'(?:wind(?:screen|shield) ?)?wipers?|grill(?:e)?s?)\b')


def stationary_part_role(part_name, material_name=''):
    """Recognize parts that must survive wheel replacement or lamp inference."""
    label = _words(part_name+' '+material_name)
    if re.search(r'\b(?:steer(?:ing)? ?wheel|wheel ?steering)\b', label):
        return 'interior'
    if _AUX_LAMP.search(label):
        return 'body'
    if re.search(r'light|lamp', label) and _LAMP_HARDWARE.search(label):
        return 'glass' if re.search(r'\bglass\b', label) and 'cover' in label else 'body'
    part_words=_words(part_name)
    trim=_WHEEL_TRIM.search(part_words)
    if trim and trim[0].startswith('grill') and re.search(r'\b(?:wheel|rim|tyre|tire)s?\b',part_words):
        trim=None
    if trim or _WHEEL_TRIM.fullmatch(_words(material_name)):
        return 'body'
    return None


def _name_role(name):
    s = _words(name)
    if re.search(r'\b(?:steer(?:ing)? ?wheel|wheel ?steering)\b',s):
        return 'interior'
    patterns = (
        ('brakelight', r'\b(?:brake ?(?:lights?|lamps?)|stop ?(?:lights?|lamps?)|(?:light|lamp)s? (?:brake|stop)|high ?(?:mount(?:ed)? )?stop|chmsl)\b'),
        ('headlight', r'\b(?:head ?(?:lights?|lamps?)|front ?(?:lights?|lamps?)|(?:light|lamp)s? front|(?:low|high|dipped|main) ?beams?)\b'),
        ('taillight', r'\b(?:(?:tail|rear) ?(?:lights?|lamps?)|(?:light|lamp)s? rear|rear ?running ?lights?)\b'),
        ('tyre', r'\b(?:tyres?|tires?|treads?|rubber)\b'),
        ('valve', r'\b(?:valves?|valve ?stems?)\b'),
        ('rim', r'\b(?:rims?|alloys?|wheels?|hub ?caps?)\b'),
        ('glass', r'\b(?:glass|windscreens?|windshields?|windows?|backlight ?glass)\b'),
        ('interior', r'\b(?:interiors?|cockpits?|dashboards?|steering|seats?|seat ?belts?)\b'),
        ('paint', r'\b(?:body ?paint|paintwork|car ?paint)\b'))
    return next((role for role, pattern in patterns if re.search(pattern, s)), None)


def _mechanical_names(name):
    """Names can suggest review; they cannot prove a part is one motion group."""
    name=_words(name)
    patterns=(('brake_rotor',r'(?<![a-z0-9])(?:brake[ _.-]*(?:disc|disk|rotor)s?|rotors?)(?![a-z0-9])'),
              ('brake_caliper',r'(?<![a-z0-9])(?:brake[ _.-]*)?calipers?(?![a-z0-9])'))
    return [role for role,pattern in patterns if re.search(pattern,name)]


def component_name_role(part_name,material_name):
    return stationary_part_role(part_name, material_name) or _name_role(part_name+' '+material_name)


def _mechanical_corner(part,centers,radii,long,lat):
    """Propose one nearby corner only when the entire part belongs there.

    A multi-corner material group is deliberately left unassigned. This is a
    source proposal, not a rotor/caliper semantic or native-mount verification.
    """
    options=[]
    for corner,center in centers.items():
        radius=radii[corner];delta=part.center-center
        if math.hypot(float(delta[1]),float(delta[long]))<.85*radius and abs(float(delta[lat]))<.75*radius:
            options.append(corner)
    if len(options)!=1:return None
    corner=options[0];ordered=np.array([centers[k] for k in CORNERS])
    # Bound temporary distance storage for large, material-group primitives.
    for start in range(0,len(part.positions),65536):
        delta=part.positions[start:start+65536,None,:].astype(float)-ordered[None,:,:]
        distances=np.sum(delta*delta,axis=2)
        if not np.isfinite(distances).all():return None
        nearest=np.argmin(distances,axis=1)
        if np.any(nearest!=CORNERS.index(corner)):return None
        sorted_distances=np.sort(distances,axis=1)
        tolerance=64*np.finfo(float).eps*np.maximum(sorted_distances[:,1],np.finfo(float).tiny)
        if np.any(sorted_distances[:,1]-sorted_distances[:,0]<=tolerance):return None
    return corner

def axes(forward):
    if forward not in ('+Z','-Z','+X','-X'):raise GarageError('Choose one of +Z, -Z, +X or -X for the front direction.')
    f=np.array([0,0,1.] if forward[-1]=='Z' else [1.,0,0])*(1 if forward[0]=='+' else -1)
    up=np.array([0.,1,0]);left=np.cross(up,f)
    return left,up,f

def _named_wheel_forward(scene, extent):
    """Use both wheels on each named axle when they agree on a long axis."""
    axles = {'front': {}, 'rear': {}}
    for part in scene.parts:
        name = re.sub(r'([a-z])([A-Z])', r'\1 \2', part.name).casefold()
        tokens = set(re.findall('[a-z]+', name))
        if not tokens.intersection(('wheel','tire','tyre','rim')) or tokens.intersection(('steering','spare','arch','well')):
            continue
        front = bool(tokens.intersection(('front','fl','fr','lf','rf')))
        rear = bool(tokens.intersection(('rear','back','rl','rr','lr')))
        if front != rear:
            axles['front' if front else 'rear'].setdefault(part.node, []).append(part.center)
    if any(len(nodes)<2 for nodes in axles.values()):
        return None
    points = {end:np.array([np.mean(parts,axis=0) for parts in nodes.values()])
              for end,nodes in axles.items()}
    delta = points['front'].mean(0) - points['rear'].mean(0)
    axis = 0 if abs(delta[0]) > abs(delta[2]) else 2
    lateral = 2-axis
    separation = abs(delta[axis])
    if (separation < .35*extent[axis] or separation<=0 or abs(delta[lateral])>.15*separation
            or any(np.ptp(row[:,axis])>.25*separation or np.ptp(row[:,lateral])<.35*extent[lateral]
                   for row in points.values())):
        return None
    return ('+' if delta[axis]>0 else '-') + ('X' if axis==0 else 'Z')


def _paired_lenses(scene, records, assignments, front, lat, long):
    """Require a unique mirrored pair with matching material, dimensions and end."""
    bounds = scene.bounds
    middle = bounds.mean(0)
    size = np.maximum(bounds[1]-bounds[0],1e-8)
    groups = {}
    for part in scene.parts:
        row = records[part.id]
        if part.id in assignments or row['role'] not in ('body','glass'):
            continue
        label = _words(part.name+' '+row['material_name'])
        if stationary_part_role(part.name,row['material_name']) or not re.search(
                r'\b(?:(?:light|lamp) ?(?:cluster|lens|lenses|bulbs?)|projector ?lens)\b',label):
            continue
        relative = (part.bounds-middle)/size
        z = relative[:,long]*front[long]
        if min(z)>.22:
            role='headlight'
        elif max(z)<-.22:
            role='taillight'
        else:
            continue
        low, high = relative[:,lat]
        if low<=.13 and high>=-.13:
            continue
        material = scene.materials[part.material]
        red,green,blue = material['emissive'] if any(material['emissive']) else material['color'][:3]
        if role=='taillight' and not (red>.1 and red>3*max(green,blue)):
            continue
        if role=='headlight' and (min(red,green,blue)<.45*max(red,green,blue) or max(red,green,blue)<.1):
            continue
        groups.setdefault((role,part.material),[]).append(part)
    existing={r['role'] for r in records.values()}
    matches={}
    for (role,_), pair in groups.items():
        if len(pair)!=2 or role in existing:
            continue
        a,b=pair
        offset=(a.center-middle)/size
        offset[lat]*=-1
        if (np.max(np.abs(offset-(b.center-middle)/size))>.035 or
                np.any(np.abs(a.extent-b.extent)>.25*np.maximum(a.extent,b.extent)+size*.002)):
            continue
        matches.setdefault(role,[]).append(pair)
    for role,pairs in matches.items():
        if len(pairs)!=1:
            continue
        for part in pairs[0]:
            records[part.id].update(role=role,confidence=.86,paired_lens=True,
                reason='Matching left and right lamp lenses at the same end, with compatible colour')


def infer(scene:Scene,forward=None,overrides=None):
    b=scene.bounds;extent=b[1]-b[0];origin=b.mean(0)
    if forward is None:
        forward=_named_wheel_forward(scene,extent) or ('+Z' if extent[2]>=extent[0] else '+X')
    left,up,front=axes(forward);lat=int(np.argmax(np.abs(left)));long=int(np.argmax(np.abs(front)))
    car_length=max(float(extent[long]),1e-6);car_width=max(float(extent[lat]),1e-6);car_height=max(float(extent[1]),1e-6)
    candidates=[];records={};mats={m['id']:m for m in scene.materials};overrides=overrides or {}
    assignments={p.id:a for p in scene.parts if (a:=_assignment(p,overrides)) is not None}
    for p in scene.parts:
        m=mats[p.material];e=p.extent;c=p.center
        assigned=assignments.get(p.id);named=component_name_role(p.name,m['name'])
        mechanical=[] if assigned else _mechanical_names(p.name+' '+m['name'])
        role=assigned['role'] if assigned else named
        confidence=assigned['confidence'] if assigned else .94 if named else .52
        why=assigned['reason'] if assigned else 'Part or material name' if named else 'Rigid body component'
        if mechanical:
            role=mechanical[0] if len(mechanical)==1 else 'body'
            confidence=.60
            why='Review proposal: brake name does not establish a single rotor/caliper motion group'
        alpha=float(m['color'][3]);lum=float(np.mean(m['color'][:3]));metal=m['metallic']
        if role is None:
            if m['alpha_mode']=='BLEND' or m['transmission']>.05:role='glass';confidence=.84;why='Transparent/transmissive source material'
            else:role='body'
        elif not assigned and role=='body' and m['alpha_mode']=='BLEND' and stationary_part_role(p.name,m['name']):
            role='glass';why='Stationary transparent detail; preserve its optical surface'
        radial=max(float(e[1]),float(e[long]),1e-8);roundness=min(float(e[1]),float(e[long]))/radial
        low=c[1]<b[0,1]+.53*car_height
        outer=abs(c[lat]-origin[lat])>.20*car_width
        thin=.06<e[lat]/radial<.75
        radius_ok=.035<radial/car_length<.30
        if assigned:
            # Authored/saved non-tyres cannot establish an axle, even when dark
            # circular geometry resembles a tyre. Explicit tyres do not need
            # to pass the heuristic mesh-density/name/colour thresholds.
            if role=='tyre' and not (assigned['corner_fixed'] and assigned['corner'] is None):
                candidates.append((10.*assigned['priority'],p))
        elif not mechanical and named in (None,'tyre','rim') and roundness>.80 and low and outer and thin and radius_ok and len(p.indices)>80:
            score=2*roundness+(1-lum)+(.4 if role=='tyre' else 0)+(.2 if metal<.2 else 0)
            if role=='tyre' or (lum<.16 and metal<.35):candidates.append((score,p))
        records[p.id]=dict(id=p.id,name=p.name,node=p.node,primitive=p.primitive,material=p.material,material_name=m['name'],role=role,corner=assigned['corner'] if assigned else None,confidence=round(confidence,2),reason=why,triangles=len(p.indices),vertices=len(p.positions),bounds=p.bounds.tolist(),center=p.center.tolist(),warnings=p.warnings.copy(),source_color=m['color'],mechanical_role_proposals=mechanical,role_review_required=bool(mechanical))
    _paired_lenses(scene, records, assignments, front, lat, long)
    best={}
    for score,p in candidates:
        c=p.center;f='F' if float((c-origin)@front)>0 else 'R';s='L' if float((c-origin)@left)>0 else 'R';corner=f+s
        assigned=assignments.get(p.id)
        if assigned and assigned['corner'] in CORNERS:corner=assigned['corner']
        if corner not in best or score>best[corner][0]:best[corner]=(score,p)
    wheel_centers={k:p.center for k,(_,p) in best.items()};wheel_radii={k:float(max(p.extent[1],p.extent[long])/2) for k,(_,p) in best.items()}
    quartet=len(best)==4
    if quartet:
        rs=list(wheel_radii.values());quartet=min(rs)>0 and max(rs)/min(rs)<1.8
    if not quartet:
        # Avoid manufacturing four tyres out of unrelated circular objects.
        best={k:v for k,v in best.items() if records[v[1].id]['role']=='tyre'}
    for k,(_,p) in best.items():
        if p.id in assignments:
            records[p.id]['corner']=k
            if not assignments[p.id]['corner_fixed']:
                records[p.id]['reason']+='; corner proposed from position'
        else:records[p.id].update(role='tyre',corner=k,confidence=.98 if quartet else .8,reason='Four symmetric low-mounted circular rubber meshes' if quartet else 'Named or explicitly assigned tyre')
    wheel_centers,wheel_radii=_tyre_dimensions(scene,records,long)
    mechanical_centres_valid=False
    if len(wheel_centers)==4 and any(row['role'] in MECHANICAL_ROLES for row in records.values()):
        from .assembly_fit import assembly_fit_report
        try:
            assembly_fit_report({'forward':forward,'wheel_centers':wheel_centers},
                                {'kind':'explicit_centres','forward':forward,'wheel_centers_metres':wheel_centers})
            mechanical_centres_valid=True
        except GarageError:
            pass
    for p in scene.parts:
        row=records[p.id];assigned=assignments.get(p.id)
        if row['corner'] or row['role'] in ('ignore','glass','headlight','taillight','brakelight'):continue
        if not assigned and stationary_part_role(p.name,row['material_name']):continue
        if assigned and (assigned['role'] not in CORNER_ROLES or assigned['corner_fixed']):continue
        if row['role'] in MECHANICAL_ROLES or row['mechanical_role_proposals']:
            if row['role'] in MECHANICAL_ROLES and mechanical_centres_valid:
                corner=_mechanical_corner(p,wheel_centers,wheel_radii,long,lat)
                if corner:
                    row['corner']=corner;row['reason']+='; corner proposed near one detected tyre'
            continue
        options=[]
        for k,center in wheel_centers.items():
            r=wheel_radii[k];delta=p.center-center;radialdist=math.hypot(float(delta[1]),float(delta[long]));lateral=abs(float(delta[lat]))
            if radialdist<.85*r and lateral<.75*r:
                if assigned:
                    options.append((np.linalg.norm(delta),k,assigned['role'],assigned['confidence'],assigned['reason']+'; corner near a detected tyre'))
                    continue
                radialsize=max(float(p.extent[1]),float(p.extent[long]))
                circular=min(float(p.extent[1]),float(p.extent[long]))/max(radialsize,1e-8)
                if circular>.80 and .8*r<radialsize<2.1*r and p.extent[lat]>.025*r and len(p.indices)>100:
                    options.append((np.linalg.norm(delta),k,'rim',.93,'Circular mesh concentric with a detected tyre'))
                elif radialsize<.38*r and .30*r<radialdist<.85*r and max(p.extent)<.5*r:
                    options.append((np.linalg.norm(delta),k,'valve',.80,'Small part inside the wheel envelope'))
                elif row['role'] in ('rim','valve','tyre'):options.append((np.linalg.norm(delta),k,row['role'],.9,'Named wheel part close to an axle'))
        if options:
            _,k,role,conf,why=min(options)
            row.update(role=role,corner=k,confidence=conf,reason=why)
    from .paint_roles import propose_paint
    paint_proposal=propose_paint(scene,records,overrides,lat,long)
    for pid,ov in overrides.items():
        if pid not in records:continue
        if ov.get('role') not in ROLES:raise GarageError('Unknown component role.')
        corner=ov.get('corner') or None
        if corner is not None and corner not in CORNERS:raise GarageError('Unknown wheel corner.')
        if ov['role'] not in CORNER_ROLES:corner=None
        records[pid].update(role=ov['role'],corner=corner,confidence=1.,reason='Your saved assignment')
        if 'use_native_optics' in ov:
            if ov['use_native_optics'] is not True or ov['role'] not in ('headlight','taillight','brakelight','glass'):
                raise GarageError('Native exterior optics require a reviewed lamp or glass assignment.')
            records[pid]['use_native_optics'] = True
        if 'use_native_finish' in ov:
            if ov['use_native_finish'] is not True or ov['role'] not in ('body','paint','interior','glass','headlight','taillight','brakelight'):
                raise GarageError('Using a GT6 surface finish requires a static body, glass or lamp assignment.')
            records[pid]['use_native_finish'] = True
    for row in records.values():row['motion_intent']=MOTION_INTENTS.get(row['role'])
    # Recompute centers from explicit tyre assignments; preserve model-space units.
    measured,radii=_tyre_dimensions(scene,records,long);centers={k:v.tolist() for k,v in measured.items()}
    wheelbase=None;track=None
    if len(centers)==4:
        cc={k:np.array(v) for k,v in centers.items()};wheelbase=abs(float(((cc['FL']+cc['FR']-cc['RL']-cc['RR'])/2)@front));track=abs(float(((cc['FL']+cc['RL']-cc['FR']-cc['RR'])/2)@left))
    issues=[]
    mechanical_review=[row['id'] for row in records.values() if row['role_review_required']]
    if mechanical_review:
        issues.append(dict(level='action',code='mechanical_role_review',parts=mechanical_review,
                           message='Review the proposed brake roles. A material group can contain both rotating rotors and non-spinning calipers; confirm or separate those surfaces before conversion.'))
    missing_mechanical=[row['id'] for row in records.values() if row['role'] in MECHANICAL_ROLES and row['corner'] not in CORNERS]
    if missing_mechanical:
        issues.append(dict(level='action',code='mechanical_corners',parts=missing_mechanical,
                           message='Assign one corner to each reviewed brake component, or partition a multi-corner group. Brake parts do not establish tyre centres.'))
    if paint_proposal['status']=='ambiguous':issues.append(dict(level='action',code='paint_ambiguous',message=paint_proposal['message']))
    if len(centers)!=4:issues.append(dict(level='action',code='four_tyres',message='Confirm the four tyre groups to establish scale and axle positions.'))
    for k in CORNERS:
        if not any(r['role']=='rim' and r['corner']==k for r in records.values()):issues.append(dict(level='action',code='rim_'+k,message=f'Assign the {k} rim before native wheel conversion.'))
    if not any(r['role']=='taillight' for r in records.values()):
        issues.append(dict(level='action',code='rear_running_lamp',
                           message='No reviewed rear running-lamp emitter is assigned. Review rear lamp surfaces before native conversion.'))
    from .f50_wheel_adapter import F50_SOURCE_SHA256
    if scene.digest==F50_SOURCE_SHA256 and records.get('n10p0',{}).get('role')=='taillight':
        issues.append(dict(level='notice',code='shared_brake_running_lenses',
                           message='F50 red rear lenses use one authored brakelight mesh for both running and brighter brake shader branches. There is no separate authored high-stop lamp; functional switching requires in-game verification.'))
    rim_textured=[p.id for p in scene.parts if records[p.id]['role']=='rim' and scene.materials[p.material]['base_texture'] is not None]
    if rim_textured:
        issues.append(dict(level='notice',code='textured_rim_recipe',
                           message='Textured rim components require a matching native material recipe; this Supra wheel template uses flat-colour rims.'))
    if not .2<car_length<30:issues.append(dict(level='action',code='scale',message='Model dimensions look unusual. Confirm metres or set the wheelbase.'))
    if scene.triangles>300000:issues.append(dict(level='notice',code='detail',message='High-detail source. A game-target profile must enforce its own geometry budget. The preview is reduced; source data is preserved.'))
    uncertain=sum(r['confidence']<.75 for r in records.values())
    return dict(paint_proposal=paint_proposal,forward=forward,parts=list(records.values()),bounds=b.tolist(),dimensions=dict(length=car_length,width=car_width,height=car_height),wheel_centers=centers,wheel_radii=radii,wheelbase=wheelbase,track=track,issues=issues,review_count=uncertain,role_counts={r:sum(p['role']==r for p in records.values()) for r in ROLES},notes=['Front direction is a proposal, not inferred engineering data. Use Flip front if needed.','Body includes unlabelled rigid details. Confirm lamp/glass roles before a full-car conversion.'])
