/* Cabin fit uses the same car-space coordinates as the exported CAR5 targets. */
(() => {
  const el = id => document.getElementById(id);
  const panel = el('driverFitPanel');
  const layer=document.createElement('canvas');layer.className='driverSceneOverlay';layer.setAttribute('aria-hidden','true');el('shellViewer').after(layer);
  panel.ontoggle=()=>lastViewer?.invalidate();
  const keys = ['seat','steering','left_foot','right_foot'];
  const inputs = ['driverX','driverY','driverZ'].map(el);
  inputs.forEach(input=>{input.step='any';});
  const colors = {seat:'#f9ce74',steering:'#8ce4ed',left_foot:'#b6dfab',right_foot:'#b6dfab'};
  let source, pose, edited = false, commit, onEdit, closeup = true;
  let views = [], suggestion = null, marking = false, markedSeat = null;
  function markStatus() {
    el('driverMark').textContent=marking?'Cancel marking':'Mark seat and steering wheel';
    el('driverLandmarks').textContent=marking?(markedSeat?'Click the steering-wheel centre in the top view.':'Click the driver’s seat cushion in the top view.'):
      suggestion?'The source cabin has a matching seat and steering wheel. Review the suggested pose before applying it.':
      'Place the seat and steering wheel in the top view, then adjust their heights in the side view.';
  }
  function stopMarking() {marking=false;markedSeat=null;markStatus();}
  function targets() {
    const anchors = source.reference_targets;
    return Object.fromEntries(keys.map(name => [name, pose?.targets?.[name] ||
      (pose ? anchors[name].map((v,i) => pose.seat_metres[i]+pose.reach*(v-anchors.seat[i])) : anchors[name]) ]));
  }
  function custom() {
    if (!pose) pose={seat_metres:[...source.reference_targets.seat],reach:1};
    return pose;
  }
  function setPoint(name,point) {
    const config=custom();
    if(name==='seat') {
      const delta=point.map((v,i)=>v-config.seat_metres[i]);
      for(const pinned of Object.values(config.targets||{})) for(let i=0;i<3;i++) pinned[i]+=delta[i];
      config.seat_metres=point;
    } else { (config.targets ||= {})[name]=point; }
  }
  function invalidate() { edited=true; onEdit?.(); }
  function changed() { invalidate(); render(); }
  function viewFor(canvas,kind,viewer) {
    const top=kind==='top', axes=top?[0,2]:[2,1];
    const bounds=viewer.meta.bounds;
    const ranges=closeup?(top?[[-.92,.92],[-.93,.82]]:[[-1.1,.96],[-.05,1.32]]):axes.map(a=>[bounds[0][a]-.08,bounds[1][a]+.08]);
    const w=canvas.width,h=canvas.height;
    const factor=Math.min((w-24)/(ranges[0][1]-ranges[0][0]),(h-16)/(ranges[1][1]-ranges[1][0]));
    const middle=ranges.map(range=>(range[0]+range[1])/2), sy=top?1:-1;
    function xy(p) { return [w/2+(p[axes[0]]-middle[0])*factor,h/2+sy*(p[axes[1]]-middle[1])*factor]; }
    const back=document.createElement('canvas');back.width=w;back.height=h;
    const g=back.getContext('2d');g.fillStyle='#18212a';g.fillRect(0,0,w,h);
    const groups=[[],[],[]];
    for(const mesh of viewer.meta.meshes) {
      const part=viewer.parts[mesh.id]; if(!part) continue;
      const layer=part.role==='interior'?2:part.role==='glass'||part.component_origin==='native_template'?1:0;
      groups[layer].push(mesh);
    }
    for(let layer=0;layer<3;layer++) {
      g.fillStyle=['#364550','#52646c','#596268'][layer];
      const n=groups[layer].reduce((sum,m)=>sum+m.index_count/3,0);
      const step=3*Math.max(1,Math.ceil(n/180000));
      for(const mesh of groups[layer]) {
        g.beginPath();
        for(let i=mesh.index_start;i+2<mesh.index_start+mesh.index_count;i+=step) {
          const points=[0,1,2].map(k=>{const off=viewer.indices[i+k]*6;return xy(viewer.vertices.subarray(off,off+3));});
          const side=(points[1][0]-points[0][0])*(points[2][1]-points[0][1])-(points[1][1]-points[0][1])*(points[2][0]-points[0][0]);
          if(side<0) [points[1],points[2]]=[points[2],points[1]];
          g.moveTo(...points[0]);g.lineTo(...points[1]);g.lineTo(...points[2]);g.closePath();
        }
        g.fill();
      }
    }
    canvas.onclick=ev=>{
      if(!source) return;
      if(marking&&!top)return;
      const rect=canvas.getBoundingClientRect(),name=el('driverTarget').value,pos=[...targets()[name]];
      pos[axes[0]]=middle[0]+((ev.clientX-rect.left)*w/rect.width-w/2)/factor;
      pos[axes[1]]=middle[1]+sy*((ev.clientY-rect.top)*h/rect.height-h/2)/factor;
      const placed=pos.map(v=>Math.round(v*1000)/1000);
      if(marking){
        if(!markedSeat){markedSeat=[placed[0],targets().seat[1],placed[2]];markStatus();render();return;}
        const verticalReach=targets().steering[1]-targets().seat[1];
        setPoint('seat',markedSeat);
        setPoint('steering',[placed[0],targets().seat[1]+verticalReach,placed[2]]);
        el('driverTarget').value='seat';stopMarking();
      }else setPoint(name,placed);
      changed();
    };
    return {canvas,back,xy,factor,top};
  }
  function cabinSlice(view,anchors) {
    const {meta,parts,vertices,indices}=lastViewer, {canvas,xy,top}=view,g=canvas.getContext('2d');
    const axis=top?1:0, levels=top?[anchors.seat[1]+.13,anchors.seat[1]+.36,anchors.steering[1]]:[anchors.seat[0],anchors.seat[0]+.08];
    g.beginPath();
    for(const mesh of meta.meshes) {
      const part=parts[mesh.id];if(!part||part.role!=='interior')continue;
      for(let i=mesh.index_start;i<mesh.index_start+mesh.index_count;i+=3) {
        const ids=[indices[i]*6,indices[i+1]*6,indices[i+2]*6];
        for(const level of levels){
          const hits=[];
          for(let e=0;e<3;e++){
            const a=ids[e],b=ids[(e+1)%3],v=vertices[a+axis],w=vertices[b+axis];if((v<level)===(w<level))continue;
            const f=(level-v)/(w-v);hits.push(xy([0,1,2].map(k=>vertices[a+k]+f*(vertices[b+k]-vertices[a+k]))));
          }
          if(hits.length===2){g.moveTo(...hits[0]);g.lineTo(...hits[1]);}
        }
      }
    }
    g.strokeStyle='#a7b6bb';g.lineWidth=.9;g.stroke();
  }
  function sceneGuide(vp,w,h){
    if(layer.width!==w||layer.height!==h){layer.width=w;layer.height=h;}
    const g=layer.getContext('2d');g.clearRect(0,0,w,h);if(!source||!panel.open)return;
    const a=targets(),seat=a.seat;
    function project(p){const q=[0,0,0,0];for(let r=0;r<4;r++)q[r]=vp[r]*p[0]+vp[r+4]*p[1]+vp[r+8]*p[2]+vp[r+12];return q[3]>.001?[(q[0]/q[3]*.5+.5)*w,(.5-q[1]/q[3]*.5)*h]:null;}
    function link(p,q,color,width){const s=project(p),t=project(q);if(!s||!t)return;g.beginPath();g.moveTo(...s);g.lineTo(...t);g.strokeStyle=color;g.lineWidth=width;g.lineCap='round';g.stroke();}
    const neck=[seat[0],seat[1]+.48,seat[2]+.07],head=[seat[0],seat[1]+.68,seat[2]+.06];
    link(seat,neck,'#f8cd75',5);link(neck,head,'#f8cd75',3);
    for(const [name,sign] of [['left_foot',-1],['right_foot',1]]) {
      const foot=a[name],knee=[(seat[0]+foot[0])/2,Math.max(seat[1],foot[1])+.12,(seat[2]+foot[2])/2-.06];
      link(seat,knee,'#d2c3a5',3);link(knee,foot,colors[name],3);
      const shoulder=[neck[0]+sign*.16,neck[1],neck[2]],hand=[a.steering[0]+sign*.105,a.steering[1],a.steering[2]],elbow=[(shoulder[0]+hand[0])/2,Math.min(shoulder[1],hand[1])-.08,(shoulder[2]+hand[2])/2+.05];
      link(neck,shoulder,'#f8cd75',3);link(shoulder,elbow,'#d2c3a5',3);link(elbow,hand,colors.steering,3);
    }
    for(let axis=0;axis<3;axis++){
      let previous=null;
      for(let i=0;i<=32;i++){const angle=2*Math.PI*i/32,p=[...head],t=(axis+1)%3,u=(axis+2)%3,r=[.119,.15,.137];p[t]+=Math.cos(angle)*r[t];p[u]+=Math.sin(angle)*r[u];if(previous)link(previous,p,'#fcebd0',1.7);previous=p;}
    }
  }
  function draw(view,anchors) {
    const {canvas,back,xy,factor,top}=view, g=canvas.getContext('2d');g.drawImage(back,0,0);cabinSlice(view,anchors);
    const hip=anchors.seat,shoulder=[hip[0],hip[1]+.48,hip[2]+.07],head=[hip[0],hip[1]+.68,hip[2]+.06];
    const line=(a,b,color,width)=>{g.beginPath();g.moveTo(...xy(a));g.lineTo(...xy(b));g.strokeStyle=color;g.lineWidth=width;g.lineCap='round';g.stroke();};
    const oval=(p,rx,ry,color)=>{g.beginPath();g.ellipse(...xy(p),rx*factor,ry*factor,0,0,Math.PI*2);g.fillStyle=color;g.fill();};
    line(hip,shoulder,'#b5976d',Math.max(6,factor*(top?.27:.17)));
    for(const [idx,name] of ['left_foot','right_foot'].entries()) {
      const foot=anchors[name],knee=[(hip[0]+foot[0])/2,Math.max(hip[1],foot[1])+.12,(hip[2]+foot[2])/2-.06];
      line(hip,knee,'#a18b72',factor*.083);line(knee,foot,'#ad9980',factor*.061);
      const side=idx?1:-1,arm=[shoulder[0]+side*.16,shoulder[1],shoulder[2]],hand=[anchors.steering[0]+side*.105,anchors.steering[1],anchors.steering[2]];
      const elbow=[(arm[0]+hand[0])/2,Math.min(arm[1],hand[1])-.08,(arm[2]+hand[2])/2+.05];
      line(arm,elbow,'#c2a98a',factor*.062);line(elbow,hand,'#ccb89c',factor*.048);
    }
    oval(head,top?.119:.137,top?.137:.15,'#ecdbbf');
    for(const name of keys) {
      g.beginPath();g.arc(...xy(anchors[name]),name===el('driverTarget').value?6:4,0,Math.PI*2);
      g.fillStyle=colors[name];g.fill();g.lineWidth=1.5;g.strokeStyle='#17202b';g.stroke();
    }
  }
  function overheadSurface(x,z,seatY) {
    const {meta,parts,vertices,indices}=lastViewer;
    let highest=-Infinity;
    for(const mesh of meta.meshes) {
      if(!['body','glass','paint'].includes(parts[mesh.id]?.role)) continue;
      for(let i=mesh.index_start;i<mesh.index_start+mesh.index_count;i+=3){
        const a=indices[i]*6,b=indices[i+1]*6,c=indices[i+2]*6;
        const ax=vertices[a],az=vertices[a+2],bx=vertices[b]-ax,bz=vertices[b+2]-az,cx=vertices[c]-ax,cz=vertices[c+2]-az;
        const det=bx*cz-bz*cx;if(Math.abs(det)<1e-9)continue;
        const u=((x-ax)*cz-(z-az)*cx)/det,v=(bx*(z-az)-bz*(x-ax))/det;
        if(u<0||v<0||u+v>1)continue;
        const y=vertices[a+1]+u*(vertices[b+1]-vertices[a+1])+v*(vertices[c+1]-vertices[a+1]);
        if(y>seatY+.35)highest=Math.max(highest,y);
      }
    }
    return highest;
  }
  function render(sync=true) {
    if(!source)return;
    const anchors=targets(),name=el('driverTarget').value;
    if(sync) {inputs.forEach((n,i)=>n.value=Number(anchors[name][i].toFixed(4)));el('driverReach').value=Math.round((pose?.reach??1)*100);}
    views.forEach(v=>{
      draw(v,anchors);
      if(v.top&&markedSeat){
        const g=v.canvas.getContext('2d');g.beginPath();g.arc(...v.xy(markedSeat),6,0,2*Math.PI);g.strokeStyle='#fff';g.lineWidth=2;g.stroke();
      }
    });
    lastViewer.invalidate();
    el('driverApply').disabled=!edited||marking;
    const seat=anchors.seat, roof=overheadSurface(seat[0],seat[2]+.06,seat[1]),gap=roof-seat[1]-.83;
    const clearance=Number.isFinite(roof)?(gap>=0?`The helmet guide clears the overhead surface by ${Math.round(gap*1000)} mm.`:`The helmet guide extends ${Math.round(-gap*1000)} mm above the overhead surface.`):'No overhead body surface was found above the helmet guide.';
    el('driverFitReadout').textContent=(edited?'Placement edited. Apply it to save. ':pose?'Saved seating position. ':'Original Supra seating position. ')+clearance;
  }
  function editInputs() {
    if(!source)return;
    if(marking)stopMarking();
    const numbers=inputs.map(n=>n.valueAsNumber);
    if(inputs.some(n=>!n.checkValidity())||!numbers.every(Number.isFinite)) {invalidate(); el('driverApply').disabled=true;return;}
    setPoint(el('driverTarget').value,numbers);invalidate();render(false);
  }
  inputs.forEach(n=>n.oninput=editInputs);
  el('driverTarget').onchange=()=>{stopMarking();render();};
  el('driverReach').oninput=()=>{
    const input=el('driverReach');if(!source)return;
    if(marking)stopMarking();
    if(!input.checkValidity()||!Number.isFinite(input.valueAsNumber)){invalidate();el('driverApply').disabled=true;return;}
    custom().reach=input.valueAsNumber/100;invalidate();render(false);
  };
  for(const [id,sign] of [['driverLeft',-1],['driverRight',1],['driverCenter',0]]) el(id).onclick=()=>{
    stopMarking();
    const seat=[...targets().seat];seat[0]=sign*(Math.abs(seat[0])||Math.abs(source.reference_targets.seat[0]));
    el('driverTarget').value='seat';setPoint('seat',seat);changed();
  };
  el('driverReset').onclick=()=>{stopMarking();pose=null;changed();};
  el('driverMark').onclick=()=>{
    if(marking){stopMarking();onEdit?.();render();return;}
    marking=true;markedSeat=null;markStatus();onEdit?.();render();
  };
  el('driverSuggest').onclick=()=>{
    if(!suggestion)return;
    stopMarking();
    pose=structuredClone(suggestion.settings);
    const hip=pose.seat_metres, roof=overheadSurface(hip[0],hip[2]+.06,hip[1]);
    if(Number.isFinite(roof))hip[1]=Number(Math.max(-1,hip[1]-.12,Math.min(hip[1],roof-.86)).toFixed(4));
    el('driverTarget').value='seat';changed();
  };
  el('driverApply').onclick=async()=>{
    if(!edited||marking||!source||[...inputs,el('driverReach')].some(n=>!n.checkValidity()||!Number.isFinite(n.valueAsNumber)))return;
    el('driverApply').disabled=true;
    try {await commit(pose?structuredClone(pose):null);} finally {if(source)render();}
  };
  const zoom=document.createElement('button');zoom.className='secondary';zoom.type='button';zoom.textContent='Show whole car';
  el('driverCenter').after(zoom);
  let lastViewer;
  function frames() {views=[viewFor(el('driverSide'),'side',lastViewer), viewFor(el('driverTop'),'top',lastViewer)];}
  zoom.onclick=()=>{closeup=!closeup;zoom.textContent=closeup?'Show whole car':'Show cabin';frames();render();};
  window.GarageDriverFit={
    get dirty(){return edited||marking;},
    clear(){
      if(lastViewer){
        delete lastViewer.options.overlay;
        layer.getContext('2d').clearRect(0,0,layer.width,layer.height);
      }
      source=null; suggestion=null; edited=false; lastViewer=null; views=[];
      stopMarking();
      panel.classList.add('hidden');
    },
    bind(viewer,save,dirty){
      const data=viewer.meta?.driver_fit;
      if(!data)return;
      source=data; lastViewer=viewer; lastViewer.options.overlay=sceneGuide;
      pose=data.settings?structuredClone(data.settings):null;
      edited=false; commit=save; onEdit=dirty;
      suggestion=viewer.meta.driver_suggestion||null;
      stopMarking();
      const button=el('driverSuggest');
      button.hidden=!suggestion;
      if(suggestion){
        const x=suggestion.settings.seat_metres[0];
        button.textContent='Use suggested '+(Math.abs(x)<.11?'centre':x<0?'left':'right')+' seat';
      }
      panel.classList.remove('hidden');
      frames();
      const hip=targets().seat, roof=overheadSurface(hip[0],hip[2]+.06,hip[1]);
      if((suggestion&&!pose)||(Number.isFinite(roof)&&roof<hip[1]+.83))panel.open=true;
      render();
    }
  };
})();
