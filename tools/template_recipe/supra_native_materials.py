from __future__ import annotations

import struct

import build_zonda_geometry_v001 as g

class MaterialMeta(g.Meta):
 def __init__(self,raw,label):
  super().__init__(raw,label)
  self.tail=bytearray(self.tail);self.inline_images=[];self.active_image_table=None
  self.native_image_table=self.tx+g.u32(raw,self.tx+28)-g.u32(raw,self.tx+8)
  self.native_image_count=g.u16(raw,self.tx+22)
  self.image_rows=[bytearray(raw[self.native_image_table+32*i:self.native_image_table+32*(i+1)])for i in range(self.native_image_count)]
  self.ms=g.u32(raw,0x44);self.original_material_count=g.u16(raw,self.ms);self.banks=raw[self.ms+4]
  g.need(self.banks in(1,2,7)and self.original_material_count%self.banks==0,'Material bank layout')
  self.old_bank_size=self.original_material_count//self.banks
  self.original_material_pointer=g.u32(raw,self.ms+8);self.original_parameter_pointer=g.u32(raw,self.ms+20)
  self.original_parameter_count=g.u16(raw,self.ms+6)
  self.parameter_rows=[bytearray(raw[self.original_parameter_pointer+68*i:self.original_parameter_pointer+68*(i+1)])for i in range(self.original_parameter_count)]
  self.new_parameter_cache={};self.source_image_map={};self.material_receipts=[];self.parameter_receipts=[]
  self.original_key_pointer=g.u32(raw,0x90);self.original_key_count=g.u16(raw,0x8e)
  self.key_rows=bytearray(raw[self.original_key_pointer:self.original_key_pointer+8*self.original_key_count])
  self.source_material_map={};self.active_material_rows=None;self.pruning_receipt=None;self.key_reference_receipts=[]

 def prune_unused_images(self,unused_ids,protected_ids):
  """Replace only closed, unused image slots with one shared neutral resource.

  The caller must independently remove these IDs from external stream binding
  tables. Original CPU image bytes remain untouched. Source clones always read
  the immutable original sampler record, never this redirected active table.
  """
  unused=set(unused_ids);protected=set(protected_ids)
  g.need(unused and not unused&protected and unused|protected==set(range(self.native_image_count)),'Incomplete image closure')
  dummy_id=min(unused);template=next(r for r in self.image_rows if r[8]==0 and r[9]in(134,136)and r[11]==1)
  # One BC1 block: selector zero chooses the black RGB565 endpoint, alpha255.
  payload=bytes(8);off=g.align(len(self.tail),128);self.tail.extend(bytes(off-len(self.tail)))
  self.tail.extend(payload+bytes(120));row=bytearray(template)
  struct.pack_into('>II',row,0,0,128);row[9:12]=bytes((134,1,1));struct.pack_into('>HH',row,12,1,1)
  for iid in sorted(unused):self.image_rows[iid]=bytearray(row)
  redirects=[]
  for pid,r in enumerate(self.parameter_rows):
   old_iid=g.u32(r,56)
   if old_iid in unused:
    r[9:11]=bytes((1,134));r[11]=(r[11]&~3)|1;struct.pack_into('>HH',r,28,1,1);struct.pack_into('>I',r,56,dummy_id)
    redirects.append({'parameter':pid,'old_image':old_iid,'new_image':dummy_id})
  self.reusable_image_ids=sorted(unused-{dummy_id});self.dummy_ids=set(unused)
  self.dummy_image={'source_image':None,'image_id':dummy_id,'tail_offset':off,'bytes':8,'allocation_bytes':128,'sha256':g.sha(payload),'width':1,'height':1,'format':134,'mips':1,'location_code':1}
  self.inline_images.append(self.dummy_image)
  self.pruning_receipt={'unused_original_image_ids':sorted(unused),'protected_original_image_ids':sorted(protected),'dummy_image_id':dummy_id,'redirected_unused_native_parameters':redirects,'CPU_image_payloads_not_edited':True}

 def add_inline_images(self,images,payloads):
  # Source inline storage uses the observed native HQ-wheel local/GPU location.
  # Exact location-bit evidence is checked by the caller before any build.
  templates=[r for r in self.image_rows if r[8]==0 and r[9]in(134,136)and r[11]==1]
  g.need(templates,'No inspected 2D local image template')
  for image in sorted(images,key=lambda x:x['id']):
   sid=image['id'];data=payloads[sid];g.need(g.sha(data)==image['sha256']and len(data)==image['bytes'],'Source texture identity')
   w,h=image['width'],image['height'];fmt=image['native_format_byte'];g.need(fmt in(134,136)and image['mip_count']==1,'Source compression/mips')
   g.need(len(data)==((w+3)//4)*((h+3)//4)*(8 if fmt==134 else 16),'Source BC block size')
   off=g.align(len(self.tail),128);self.tail.extend(bytes(off-len(self.tail)));allocation=g.align(len(data),128)
   self.tail.extend(data);self.tail.extend(bytes(allocation-len(data)))
   row=bytearray(templates[0]);struct.pack_into('>II',row,0,0,allocation);row[9]=fmt;row[10]=1;row[11]=1
   struct.pack_into('>HH',row,12,w,h);g.need(g.u32(row,24)==0,'Uninspected source image extension')
   if getattr(self,'reusable_image_ids',[]):
    iid=self.reusable_image_ids.pop(0);self.image_rows[iid]=row;self.dummy_ids.remove(iid)
   else:iid=len(self.image_rows);self.image_rows.append(row)
   self.source_image_map[sid]=iid
   self.inline_images.append({'source_image':sid,'image_id':iid,'tail_offset':off,'bytes':len(data),'allocation_bytes':allocation,'sha256':g.sha(data),'width':w,'height':h,'format':fmt,'mips':1,'location_code':1})

 def source_parameter(self,original_pid,source_image,sampler,image):
  g.need(0<=original_pid<self.original_parameter_count,'Source sampler lacks a native ordinary template')
  p=self.original_parameter_pointer+68*original_pid;r=bytearray(self.raw[p:p+68]);g.need(g.u32(r,4)==0,'Native sampler has unexpected initial offset')
  # Location is a two-bit packed field at byte11. The evidence-backed caller
  # must confirm code1 for inline/local resources before invoking this module.
  r[9]=1;r[10]=image['native_format_byte'];r[11]=(r[11]&~3)|1
  address=g.u32(r,12);g.need(sampler['wrap_S']==sampler['wrap_T']==1,'Only authored repeat is specified')
  address=(address&~0xf0f)|1|(1<<8)
  # Source color is sRGB; alpha remains linear. Native gamma occupies bits20..23.
  g.need(sampler['gamma_RGBA_mask']in(0,7),'Unsupported source gamma mask')
  address=(address&~(15<<20))|(sampler['gamma_RGBA_mask']<<20)
  struct.pack_into('>I',r,12,address);struct.pack_into('>HH',r,28,image['width'],image['height'])
  struct.pack_into('>I',r,56,self.source_image_map[source_image]);struct.pack_into('>I',r,64,0)
  signature=bytes(r[:64])
  if signature in self.new_parameter_cache:return self.new_parameter_cache[signature]
  pid=len(self.parameter_rows);name=self.string('Pagani/source_'+str(source_image)+'_p'+str(pid)+'.dds');struct.pack_into('>I',r,64,name)
  self.parameter_rows.append(r);self.new_parameter_cache[signature]=pid
  self.parameter_receipts.append({'id':pid,'native_template_parameter':original_pid,'source_image':source_image,'image_id':self.source_image_map[source_image],'location_code':r[11]&3,'format':r[10],'width':g.u16(r,28),'height':g.u16(r,30),'wrap_S':address&15,'wrap_T':(address>>8)&15,'gamma_RGBA_mask':(address>>20)&15,'record_sha256':g.sha(r)})
  return pid

 def clone_source_materials(self,plans,lod,image_by_id,route,*,asset='body',replace_slots=None):
  raw=self.raw;native_by_id={m['id']:m for m in route['materials']};new_by_bank=[{}for _ in range(self.banks)]
  if replace_slots is not None:g.need(len(set(replace_slots.values()))==len(plans) and all(0<=i<self.old_bank_size for i in replace_slots.values()),'Unsafe replacement material slots')
  for plan in sorted(plans,key=lambda p:p['source_material_id']):
   sid=plan['source_material_id'];binding=plan['bindings'][lod];tid=binding['template_material_id']
   g.need(binding['asset']==lod+'_'+asset,'Material recipe asset mismatch')
   g.need(native_by_id[tid]['shader_key']==binding['shader_key'],'Material recipe shader key mismatch')
   material_id=replace_slots[sid] if replace_slots is not None else self.old_bank_size+len(self.source_material_map);self.source_material_map[sid]=material_id
   banks=[]
   for bank in range(self.banks):
    op=self.original_material_pointer+(bank*self.old_bank_size+tid)*52;r=bytearray(raw[op:op+52])
    kp=g.u32(r,12);n=g.u16(r,10);g.need(self.original_key_pointer<=kp<=self.original_key_pointer+8*self.original_key_count-8*n,'Native sampler array outside aggregate')
    original_keys=[raw[kp+8*j:kp+8*(j+1)]for j in range(n)]
    names={g.cstr(raw,g.u32(k,0)):g.u32(k,4)for k in original_keys};g.need(len(names)==n,'Duplicate native sampler role')
    replacements={};alias_receipts=[]
    for sampler in binding['samplers']:
     aliases=sampler['aliases'];g.need(set(aliases)<=set(names),'Sampler aliases absent from native template')
     pids={names[a]for a in aliases};g.need(len(pids)==1,'Named roles do not cohere on native parameter')
     img=image_by_id[sampler['image_id']];pid=self.source_parameter(next(iter(pids)),sampler['image_id'],sampler,img)
     for name in aliases:
      g.need(name not in replacements,'Recipe binds a named role twice');replacements[name]=pid
     alias_receipts.append({'hardware_unit':sampler['unit'],'aliases':aliases,'native_parameter':next(iter(pids)),'new_parameter':pid,'source_image':sampler['image_id']})
    start=len(self.key_rows)
    for key in original_keys:
     row=bytearray(key);name=g.cstr(raw,g.u32(row,0))
     if name in replacements:struct.pack_into('>I',row,4,replacements[name])
     self.key_rows.extend(row)
    struct.pack_into('>I',r,12,start) # temporary offset, resolved at final table allocation
    new_by_bank[bank][material_id]=r;banks.append({'bank':bank,'native_template':tid,'key_relative_offset':start,'alias_groups':alias_receipts,'retained_native_roles':{k:v for k,v in names.items()if k not in replacements}})
   self.material_receipts.append({'source_material':sid,'material_id':material_id,'shader_key':binding['shader_key'],'family':plan['family'],'banks':banks,'native_GCM_and_constants_retained':True,'source_double_sided':plan['double_sided_source'],'native_culling_preserved_initially':True})
  new_key_pointer=self.alloc('aggregate_native_and_source_texture_keys',self.key_rows)
  known={0x90};all_materials=[]
  # PDTools MDL3MaterialData (40 bytes): signed keyCount+10, keysOffset+16,
  # shaderReference+20. Its paired MDL3MaterialShaderReference (40 bytes)
  # lives in header+0x88's table, with keysOffset+28. Reference+24 is named
  # typeOrVersion by the primary reader; the audited native values equal the
  # material-data count, but its general semantic meaning is not invented.
  md=g.u32(raw,self.ms+12);count=g.u16(raw,self.ms+2);refs=g.u32(raw,0x88)
  g.need(count==g.u16(raw,0x2c),'Material-data/reference table count mismatch')
  for i in range(count):
   p=md+40*i;n=struct.unpack_from('>h',raw,p+10)[0];kp=g.u32(raw,p+16);rp=g.u32(raw,p+20)
   g.need(rp==refs+40*i and g.u32(raw,rp+28)==kp and g.u32(raw,rp+24)==n>=0,'Typed native key-pair relation differs')
   if n:
    g.need(self.original_key_pointer<=kp<=self.original_key_pointer+8*self.original_key_count-8*n,'Paired key range outside aggregate')
    target=new_key_pointer+kp-self.original_key_pointer
    for field in(p+16,rp+28):known.add(field);self.s32(field,target,'typed_material_data_or_shader_reference_key_pointer')
    self.key_reference_receipts.append({'pair':i,'material_data_field':p+16,'shader_reference_field':rp+28,'old_pointer':kp,'new_pointer':target,'documented_material_data_key_count':n,'observed_shader_reference_plus24':g.u32(raw,rp+24),'keys_sha256':g.sha(raw[kp:kp+8*n]),'remaining_record_bytes_preserved':True})
   else:g.need(kp==0,'Empty native paired keys have nonzero pointer')
  for bank in range(self.banks):
   for mid in range(self.old_bank_size):
    p=self.original_material_pointer+(bank*self.old_bank_size+mid)*52;r=bytearray(raw[p:p+52]);n=g.u16(r,10);kp=g.u32(r,12)
    if n:
     g.need(self.original_key_pointer<=kp<=self.original_key_pointer+8*self.original_key_count-8*n,'Original material key range')
     known.add(p+12);struct.pack_into('>I',r,12,new_key_pointer+kp-self.original_key_pointer)
    if mid in new_by_bank[bank]:
     r=new_by_bank[bank][mid];struct.pack_into('>I',r,12,new_key_pointer+g.u32(r,12))
    all_materials.append(r)
   for mid,r in sorted(new_by_bank[bank].items()):
    if mid>=self.old_bank_size:struct.pack_into('>I',r,12,new_key_pointer+g.u32(r,12));all_materials.append(r)
  observed={p for p in range(0,self.cut-3,4)if self.original_key_pointer<=g.u32(raw,p)<self.original_key_pointer+8*self.original_key_count and (g.u32(raw,p)-self.original_key_pointer)%8==0}
  g.need(observed<=known,'Unclassified incoming aggregate-key references '+str(sorted(observed-known)))
  material_pointer=self.alloc('original_and_source_material_banks',b''.join(all_materials))
  self.active_material_rows=all_materials
  self.s16(self.ms,len(all_materials),'material_count');self.s32(self.ms+8,material_pointer,'material_table')
  self.s16(0x8e,len(self.key_rows)//8,'aggregate_key_count');self.s32(0x90,new_key_pointer,'aggregate_key_table')
  return dict(self.source_material_map)

 def finish_material_tables(self):
  self.active_image_table=self.alloc('original_and_source_image_table',b''.join(self.image_rows))
  self.s16(self.tx+22,len(self.image_rows),'image_count');self.s32(self.tx+28,self.active_image_table-self.tx+g.u32(self.raw,self.tx+8),'image_table')
  pp=self.alloc('original_and_source_PGLU_table',b''.join(self.parameter_rows))
  self.s16(self.ms+6,len(self.parameter_rows),'PGLU_count');self.s32(self.ms+20,pp,'PGLU_table')

 def finish(self):
  g.need(self.active_image_table is not None,'Material/image tables incomplete')
  newcut=g.align(len(self.b),128);self.b.extend(bytes(newcut-len(self.b)));delta=newcut-self.cut
  for p in self.mirrors:self.s32(p,newcut,'relocation_extent')
  self.s32(4,newcut+len(self.tail),'physical_length');relocations=[]
  fpcount=g.u16(self.raw,self.sh+0x34);fptable=g.u32(self.raw,self.sh+0x40)
  for i in range(fpcount):
   field=fptable+32*i+4;p=g.u32(self.raw,field);n=g.u32(self.raw,field+4);g.need(self.cut<=p<=len(self.raw)-n,'Native fragment tail range')
   self.s32(field,p+delta,'fragment_program_pointer');relocations.append({'field':field,'old':p,'new':p+delta,'bytes':n,'sha256':g.sha(self.raw[p:p+n])})
  for i in range(self.native_image_count):
   field=self.native_image_table+32*i;p=g.u32(self.raw,field);n=g.u32(self.raw,field+4)
   if p:
    g.need(self.cut<=p<=len(self.raw)-n,'Native inline image tail range');self.s32(field,p+delta,'native_old_image_table_pointer')
    # A resource closure may replace an unused native active descriptor.
    if g.u32(self.b,self.active_image_table+32*i)==p:self.s32(self.active_image_table+32*i,p+delta,'native_active_inline_image_pointer')
    relocations.append({'image':i,'field':field,'old':p,'new':p+delta,'bytes':n,'sha256':g.sha(self.raw[p:p+n])})
  for im in self.inline_images:
   p=newcut+im['tail_offset'];self.s32(self.active_image_table+32*im['image_id'],p,'source_inline_image_pointer');im['file_offset']=p
  if self.pruning_receipt:
   for iid in sorted(self.dummy_ids-{self.dummy_image['image_id']}):self.s32(self.active_image_table+32*iid,newcut+self.dummy_image['tail_offset'],'shared_unused_dummy_image_pointer')
  out=bytes(self.b)+bytes(self.tail)
  g.need(out[newcut:newcut+len(self.raw)-self.cut]==self.raw[self.cut:],'Native original tail bytes changed')
  g.need({i for i,(a,b)in enumerate(zip(self.raw[:self.cut],out[:self.cut]))if a!=b}<=self.allowed,'Unclassified original metadata edit')
  for r in relocations:g.need(g.sha(out[r['new']:r['new']+r['bytes']])==r['sha256'],'Native tail relocation mismatch')
  for im in self.inline_images:g.need(g.sha(out[im['file_offset']:im['file_offset']+im['bytes']])==im['sha256'],'Source texture payload changed')
  return out,{'old_cut':self.cut,'new_cut':newcut,'native_tail_prefix_exact':True,'relocations':relocations,'images':self.inline_images,'materials':self.material_receipts,'source_PGLUs':self.parameter_receipts,'paired_key_references':self.key_reference_receipts,'resource_closure':self.pruning_receipt,'changes':self.changes,'allocations':self.allocations}
