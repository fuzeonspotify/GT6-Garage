"""Find a reviewable seat and steering-wheel pair in the assembled source cabin."""
import re

import numpy as np


_STEERING = re.compile(r'steer(?:ing)?[ _.-]*(?:wheel|rim)|(?:wheel|rim)[ _.-]*steer|volant|lenkrad', re.I)
_SEAT = re.compile(r'(?<![a-z])(?:seats?|sitz|sedile)(?![a-z])', re.I)


def _islands(positions, faces):
    # Welding coincident positions reconnects UV and hard-normal seams. This
    # is used for recognition only. The exported source mesh stays intact.
    points, vertices = np.unique(np.round(positions, 5), axis=0, return_inverse=True)
    parent = list(range(len(points)))

    def root(index):
        while index != parent[index]:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for a, b, c in vertices[faces]:
        owner = root(a)
        parent[root(b)] = owner
        parent[root(c)] = owner
    roots = np.fromiter((root(i) for i in range(len(points))), dtype=int)
    order = np.argsort(roots, kind='stable')
    for group in np.split(order, np.flatnonzero(np.diff(roots[order])) + 1):
        if len(group) >= 8:
            yield points[group]


def _steering_shape(points, extent):
    width, height, depth = extent
    if not (.20 <= width <= .55 and .15 <= height <= .55 and
            .55 <= height / width <= 1.25 and depth < .8 * width):
        return False
    eigenvalues, axes = np.linalg.eigh(np.cov(points.T))
    if (eigenvalues[1] <= 0 or eigenvalues[0] > .14 * eigenvalues[1]
            or abs(axes[2, 0]) < .55 or abs(axes[0, 0]) > .45):
        return False
    # A ring surrounds its centre in all directions. Flat switch panels
    # and thin dashboard strips also pass a simple planarity test.
    flat = points @ axes[:, 1:]
    half = np.ptp(flat, axis=0) / 2
    if half.min() <= 0 or half.min() < .66 * half.max():
        return False
    flat = (flat - (flat.max(axis=0) + flat.min(axis=0)) / 2) / half
    outer = flat[np.linalg.norm(flat, axis=1) >= .73]
    bins = np.floor((np.arctan2(outer[:, 1], outer[:, 0]) + np.pi) * 8 / np.pi).astype(int) % 16
    return len(np.unique(bins)) >= 12


def suggest_cabin(components, reference):
    seats, wheels = [], []
    remaining = 150000
    for label, part_id, role, positions, faces in components:
        named_seat, named_wheel = bool(_SEAT.search(label)), bool(_STEERING.search(label))
        if not named_seat and not named_wheel and role != 'interior':
            continue
        if len(faces) > min(60000, remaining) or not len(faces):
            continue
        remaining -= len(faces)
        for points in _islands(positions, faces):
            low, high = points.min(axis=0), points.max(axis=0)
            size = high-low
            center = (low+high)/2
            if _steering_shape(points, size) and not (named_seat and not named_wheel):
                wheels.append((center, part_id, named_wheel))
            width, height, depth = size
            if (not named_wheel and .27 <= width <= .78 and .30 <= height <= 1.17
                    and .32 <= depth <= 1.16 and height > .7 * width):
                # Sample the lower portion of the seat, below its backrest.
                pan = [center[0], low[1]+min(.085, .10*height), low[2]+.45*depth]
                seats.append((np.array(pan), part_id, named_seat))
    pairs = []
    for seat, seat_id, seat_label in seats:
        for wheel, wheel_id, wheel_label in wheels:
            lateral, rise, forward = wheel - seat
            if abs(lateral) > .19 or not .16 <= rise <= .75 or not -.88 <= forward <= -.15:
                continue
            score = (abs(lateral) + .25*abs(forward+.38) + .15*abs(rise-.39)
                     - .09*int(seat_label) - .09*int(wheel_label))
            pairs.append((score, seat, wheel, seat_id, wheel_id))
    if not pairs:
        return None
    pairs.sort(key=lambda item: item[0])
    score, seat, wheel, seat_id, wheel_id = pairs[0]
    if any(np.any(point < [-2., -1., -4.]) or np.any(point > [2., 2., 4.]) for point in (seat, wheel)):
        return None
    for other, other_seat, other_wheel, _, _ in pairs[1:]:
        if other > score+.08:
            break
        if np.linalg.norm(other_seat-seat) > .13 or np.linalg.norm(other_wheel-wheel) > .10:
            return None
    native = np.array(reference['steering'])-reference['seat']
    reach = np.clip(np.linalg.norm((wheel-seat)[1:])/max(.001, np.linalg.norm(native[1:])), .60, 1.30)
    return {'seat_part':seat_id, 'steering_part':wheel_id, 'settings':{
        'seat_metres':seat.round(4).tolist(), 'reach':round(float(reach), 2),
        'targets':{'steering':wheel.round(4).tolist()}}}
