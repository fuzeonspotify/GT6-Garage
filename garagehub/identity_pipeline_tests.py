"""Focused cross-stage identity/namespace gates with an in-memory fake packer.

These checks exercise package routing and evidence binding. They deliberately
do not claim encryption, native geometry or game compatibility; actual Windows
packer/extraction and runtime checks remain separate.
"""
from __future__ import annotations
import copy,json,shutil,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from .errors import GarageError
from .util import atomic_json,hash_file
from .car_profiles import PROFILE,PROFILE_ID,ASSETS
from .identity_pipeline import DATABASE_PATH,identity_plan,route_candidate
from .car_pack import BASE_HEADER,baseline,pack_candidate


class IdentityPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name)
        self.base=self.root/'base';self.candidate=self.root/'candidate';self.output=self.root/'output'
        self.base.mkdir();self.candidate.mkdir()
        self.name_path='specdb/GT6/US/CarName.dat';self.new_model='car/0004/9998'
        self.calls=[];self.namespace_error=None;self.packed={};self.index={}

    def tearDown(self):self.temporary.cleanup()

    def _write(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return hash_file(path)

    def _setup_package(self):
        old={DATABASE_PATH:b'baseline encrypted database',self.name_path:b'baseline names'}
        old.update({PROFILE['model_path']+'/'+rel:('old '+rel).encode() for rel in ASSETS})
        original={rel:self._write(self.base/'Input'/rel,data) for rel,data in old.items()}
        self.seed={rel:self._write(self.base/'BaselineSeed'/rel,data) for rel,data in {'K/4D':b'seed header','9/AA/BB':b'seed payload'}.items()}
        self.original_receipt={'passed':True,'header_sha256':BASE_HEADER,'source_virtual_assets':original}
        atomic_json(self.base/'package_receipt.json',self.original_receipt)
        self.before={rel:i for i,rel in enumerate([*old,'stock/untouched'],1)}
        self._listing(self.base/'packed_entries.txt',self.before)
        new={self.new_model+'/'+rel:('new '+rel).encode() for rel in ASSETS}
        new.update({DATABASE_PATH:b'new encrypted database',self.name_path:b'new names'})
        hashes={rel:self._write(self.candidate/rel,data) for rel,data in new.items()}
        identity={'ready':True,'mode':'new_entry','model_path':self.new_model,'car_id':2146,
                  'display_name':'Generic car','name_files':{self.name_path:original[self.name_path]},
                  'encrypted_source_sha256':original[DATABASE_PATH]}
        self.build={'passed':True,'profile_id':PROFILE_ID,'kind':'identity-native-exterior-candidate',
                    'model_path':self.new_model,'installation':identity,'database_crypto_roundtrip':True,
                    'source_virtual_assets':hashes,'additional_database_entry':True,'limitations':[]}
        atomic_json(self.candidate/'build_receipt.json',self.build)

    def _listing(self,path,mapping):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('\n'.join(f'{rel} - Flags: Uncompressed | FileIndex: {index} (9/AA/BB) | test' for rel,index in mapping.items()),encoding='utf-8')

    def _execute(self,packer,args,work,logname,cancel):
        self.calls.append(args[0])
        value=lambda option:Path(args[args.index(option)+1])
        if args[0]=='pack':
            source=value('-p');self.packed={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*') if p.is_file()}
            self.index=dict(self.before)
            for rel in self.packed:
                if rel not in self.index:self.index[rel]=max(self.index.values())+1
            if self.namespace_error=='missing':self.index.pop('stock/untouched')
            if self.namespace_error=='extra':self.index['stock/unplanned']=max(self.index.values())+1
            self._write(value('-o')/'K/4D',b'new packed header')
        elif args[0]=='listfiles':self._listing(value('-o'),self.index)
        elif args[0]=='unpack':
            selected=int(args[args.index('--indices')+1]);name=next(name for name,index in self.index.items() if index==selected)
            self._write(value('-o')/name,self.packed[name])
        else:raise AssertionError('Unexpected fake packer command: '+args[0])

    def _pack(self,corrupt_copy=False):
        original_copy=shutil.copyfile
        def copy_file(source,dest,*args,**kwargs):
            result=original_copy(source,dest,*args,**kwargs)
            if corrupt_copy and Path(source)==self.candidate/self.new_model/'info':Path(dest).write_bytes(b'corrupted staging copy')
            return result
        with ExitStack() as stack:
            stack.enter_context(patch('garagehub.car_pack.trusted_packer',return_value={}))
            stack.enter_context(patch('garagehub.car_pack.baseline',return_value={}))
            stack.enter_context(patch('garagehub.car_pack.MDL3'))
            stack.enter_context(patch('garagehub.car_pack.SEED',self.seed))
            stack.enter_context(patch('garagehub.car_pack.execute',side_effect=self._execute))
            stack.enter_context(patch('garagehub.car_pack.shutil.copyfile',side_effect=copy_file))
            return pack_candidate(self.candidate,self.base,self.root/'fake.exe',self.output)

    def test_unselected_or_incomplete_destination_never_reads_database(self):
        for choice in (None,{'mode':'replace','replace_car_id':None}):
            with self.subTest(choice=choice),patch('garagehub.identity_pipeline.database_context') as context:
                result=identity_plan({'name':'Generic','installation':choice},self.base,None,None)
                self.assertFalse(result['ready']);self.assertTrue(result['blockers']);context.assert_not_called()

    def test_stale_routing_database_is_rejected_before_output_or_crypt(self):
        atomic_json(self.candidate/'build_receipt.json',{'passed':True,'profile_id':PROFILE_ID})
        plan={'ready':True,'mode':'new_entry','model_path':self.new_model,'encrypted_source_sha256':'old','name_files':{}}
        context={'encrypted_sha256':'changed','names':{},'reserved_model_paths':[]}
        with patch('garagehub.identity_pipeline.database_context',return_value=context),patch('garagehub.car_pack.execute') as execute:
            with self.assertRaisesRegex(GarageError,'changed after planning'):
                route_candidate(self.candidate,self.base,None,None,plan,self.output)
            execute.assert_not_called();self.assertFalse(self.output.exists())

    def test_occupied_routing_namespace_is_rejected_before_output(self):
        atomic_json(self.candidate/'build_receipt.json',{'passed':True,'profile_id':PROFILE_ID})
        plan={'ready':True,'mode':'new_entry','model_path':self.new_model,'encrypted_source_sha256':'same','name_files':{}}
        context={'encrypted_sha256':'same','names':{},'reserved_model_paths':[self.new_model+'/race/body']}
        with patch('garagehub.identity_pipeline.database_context',return_value=context):
            with self.assertRaisesRegex(GarageError,'already occupied'):
                route_candidate(self.candidate,self.base,None,None,plan,self.output)
        self.assertFalse(self.output.exists())

    def test_valid_fakepack_adds_only_requested_namespace_and_retains_prior_models(self):
        self._setup_package();receipt=self._pack()
        self.assertTrue(receipt['passed']);self.assertTrue(receipt['additional_car_database_entry'])
        self.assertEqual(receipt['lineage'],{'schema':1,'root_header':BASE_HEADER,'parent_header':BASE_HEADER,'generation':1})
        for rel,digest in self.original_receipt['source_virtual_assets'].items():
            if rel not in self.build['source_virtual_assets']:self.assertEqual(hash_file(self.output/'Input'/rel),digest)
        self.assertEqual(set(self.index),set(self.before)|set(self.build['source_virtual_assets']))

    def test_fakepack_cannot_add_or_remove_unrequested_namespace_entries(self):
        self._setup_package()
        for error in ('missing','extra'):
            with self.subTest(error=error):
                self.namespace_error=error;self.output=self.root/('output_'+error);self.calls=[]
                with self.assertRaisesRegex(GarageError,'unexpected game namespace'):
                    self._pack()
                self.assertNotIn('unpack',self.calls)
                self.assertTrue((self.output/'FAILED.json').is_file())
                self.assertFalse((self.output/'package_receipt.json').exists())

    def test_fakepack_cannot_overwrite_an_existing_allocated_namespace(self):
        self._setup_package();self.before[self.new_model+'/race/body']=max(self.before.values())+1
        self._listing(self.base/'packed_entries.txt',self.before)
        with self.assertRaisesRegex(GarageError,'collides'):
            self._pack()
        self.assertNotIn('unpack',self.calls)

    def test_candidate_copy_must_match_prior_validation_not_just_its_own_extraction(self):
        self._setup_package()
        with self.assertRaises(GarageError):self._pack(corrupt_copy=True)
        self.assertFalse((self.output/'package_receipt.json').exists())

    def test_descendant_baseline_requires_lineage_and_retained_reviewed_template(self):
        self._setup_package();header='e'*64
        receipt={**self.original_receipt,'profile_id':PROFILE_ID,
                 'all_unrelated_archive_entries_preserved':True,'existing_car_assets_preserved':True,
                 'lineage':{'root_header':BASE_HEADER,'parent_header':'a'*64,'generation':2}}
        atomic_json(self.base/'package_receipt.json',receipt)
        manifest={'header_sha256':header,'files':[]}
        with patch('garagehub.car_pack.source_package',return_value=(self.base/'PatchOut',manifest)),patch('garagehub.car_pack.SEED',self.seed),patch('garagehub.car_profiles.validate_template') as template:
            result=baseline(self.base)
            self.assertEqual(result['generation'],2);self.assertEqual(result['header_sha256'],header)
            template.assert_called_once_with(self.base)
            for change in ({'generation':0},{'root_header':'b'*64},{'parent_header':'missing'}):
                bad=copy.deepcopy(receipt);bad['lineage'].update(change)
                atomic_json(self.base/'package_receipt.json',bad)
                with self.subTest(change=change),self.assertRaises(GarageError):baseline(self.base)


if __name__=='__main__':unittest.main()
