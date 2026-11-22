"""Build a separate offline experiment from a reviewed native model candidate."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chassis_diagnostics import _tyre_selection, _tyre_request
from garagehub.car_pack import pack_candidate
from garagehub.car_profiles import PROFILE
from garagehub.errors import GarageError
from garagehub.identity_pipeline import identity_plan, route_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('model', 'baseline', 'packer', 'output'):
        parser.add_argument('--'+flag, required=True, type=Path)
    parser.add_argument('--name', required=True)
    _tyre_selection(parser)
    args = parser.parse_args()
    request = _tyre_request(args)
    if request['car_id'] != PROFILE['car_id']:
        parser.error('Select the donor car ID used by the reviewed model profile.')
    selections = [{'table':r['table'], 'donor_part_id':r['part_id'], 'size':r['size']}
                  for r in request['selections']]
    output = args.output.resolve()
    if any(output.is_relative_to(p.resolve()) for p in (args.model, args.baseline)):
        parser.error('Choose a fresh output outside the native model and baseline.')
    project = {'name':args.name, 'installation':{'mode':'new_entry'}}
    output.mkdir()
    cache = output/'database_cache'
    plan = identity_plan(project, args.baseline, args.packer, cache)
    if not plan['ready']:
        raise GarageError(' '.join(plan['blockers']))
    route_candidate(args.model, args.baseline, args.packer, cache, plan, output/'candidate',
                    donor_tyre_selections=selections)
    receipt = pack_candidate(output/'candidate', args.baseline, args.packer, output/'package')
    checked = receipt['extracted_tyre_validation']['validation']
    print(f"Entry {plan['car_id']}, {len(receipt['source_virtual_assets'])} extracted assets, "
          f"{checked['inserted_size_rows']} private sizes, {checked['changed_reference_cells']} redirected references.")
    print(output/'package'/'extracted_tyre_verification.json')


if __name__ == '__main__':
    main()
