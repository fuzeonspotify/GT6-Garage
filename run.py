from __future__ import annotations
import json, sys
from pathlib import Path
from garagehub.util import atomic_json

from garagehub.desktop import desktop_parser, dialog, open_workshop

def main():
    from garagehub.template_build import dispatch
    dispatch()
    ap=desktop_parser();ap.add_argument('--self-test',action='store_true');ap.add_argument('--self-test-report',type=Path)
    ap.add_argument('--f50-rim-lab',action='store_true',help='Expert-only, non-installable F50-to-RX8 loose native wheel experiment.')
    ap.add_argument('--engineering-history-report',type=Path,help='Audit the bug ledger and live regression references without executing tests or launching the game.')
    ap.add_argument('--f50-body-geometry-lab',action='store_true',help='Expert-only; detached native F50 body shapes, NOT an installable car.')
    ap.add_argument('--f50-original-slot-lab',action='store_true',help='Expert-only F50 body routed through RX8 original slots; NOT INSTALLABLE.')
    ap.add_argument('--f50-lamp-slot-lab',action='store_true',help='Expert-only F50 linked body with native head/running/brake switching; NOT INSTALLABLE.')
    ap.add_argument('--f50-sidecar-slot-lab',action='store_true',help='NON-INSTALLABLE native F50 A/B trial for stale RX8 stream auxiliary metadata.')
    ap.add_argument('--source-glb',type=Path);ap.add_argument('--donor-root',type=Path);ap.add_argument('--output-dir',type=Path)
    a=ap.parse_args()
    if a.dialog:return dialog(a.dialog,a.dialog_result)
    if a.engineering_history_report:
        from garagehub.engineering_history import audit_engineering_history
        r=audit_engineering_history();a.engineering_history_report.parent.mkdir(parents=True,exist_ok=True);atomic_json(a.engineering_history_report,r)
        if sys.stdout is not None:print(json.dumps(r))
        return 0 if r['passed'] else 1
    if a.f50_rim_lab:
        from garagehub.rx8_wheel_cli import run_f50_wheel_lab
        if not all((a.source_glb,a.donor_root,a.output_dir)):
            ap.error('The rim lab requires --source-glb, --donor-root and --output-dir.')
        result=run_f50_wheel_lab(a.source_glb,a.donor_root,a.output_dir)
        if sys.stdout is not None:print(json.dumps(result),flush=True)
        return 0
    if a.f50_body_geometry_lab:
        from garagehub.rx8_wheel_cli import validate_lab_paths
        from garagehub.rx8_body_geometry_lab import experiment
        if not all((a.source_glb,a.donor_root,a.output_dir)):
            ap.error('F50 body geometry lab requires --source-glb, --donor-root, --output-dir.')
        src,donor,out=validate_lab_paths(a.source_glb,a.donor_root,a.output_dir)
        result=experiment(src,donor,out)
        if sys.stdout is not None:print(json.dumps(result),flush=True)
        return 0
    if a.f50_sidecar_slot_lab:
        from garagehub.rx8_wheel_cli import validate_lab_paths
        from garagehub.rx8_slot_body_lab import experiment
        if not all((a.source_glb,a.donor_root,a.output_dir)):
            ap.error('F50 sidecar A/B experiment requires original --source-glb, --donor-root and fresh --output-dir.')
        src,donor,out=validate_lab_paths(a.source_glb,a.donor_root,a.output_dir)
        result=experiment(src,donor,out,lamps=True,sidecar_variant=True)
        if sys.stdout is not None:print(json.dumps(result),flush=True)
        return 0
    if a.f50_lamp_slot_lab:
        from garagehub.rx8_wheel_cli import validate_lab_paths
        from garagehub.rx8_slot_body_lab import experiment
        if not all((a.source_glb,a.donor_root,a.output_dir)):
            ap.error('F50 lamp slot lab requires --source-glb, --donor-root, --output-dir.')
        src,donor,out=validate_lab_paths(a.source_glb,a.donor_root,a.output_dir)
        result=experiment(src,donor,out,lamps=True)
        if sys.stdout is not None:print(json.dumps(result),flush=True)
        return 0
    if a.f50_original_slot_lab:
        from garagehub.rx8_wheel_cli import validate_lab_paths
        from garagehub.rx8_slot_body_lab import experiment
        if not all((a.source_glb,a.donor_root,a.output_dir)):
            ap.error('F50 original slot lab requires --source-glb, --donor-root, --output-dir.')
        src,donor,out=validate_lab_paths(a.source_glb,a.donor_root,a.output_dir)
        result=experiment(src,donor,out)
        if sys.stdout is not None:print(json.dumps(result),flush=True)
        return 0
    if a.self_test or a.self_test_report:
        from garagehub.selftest import selftest
        r=selftest()
        if a.self_test_report:
            a.self_test_report.parent.mkdir(parents=True,exist_ok=True);atomic_json(a.self_test_report,r)
        if sys.stdout is not None:print(json.dumps(r))
        return 0 if r['passed'] else 1
    return open_workshop(a)
if __name__=='__main__':raise SystemExit(main())
