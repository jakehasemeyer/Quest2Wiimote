import * as T from './vendor/three.module.js';
import { OrbitControls } from './vendor/OrbitControls.js';

export function trackingScene(container) {
  const renderer = new T.WebGLRenderer({antialias:true,alpha:false});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  renderer.setClearColor(0x10161f);
  container.prepend(renderer.domElement);
  const scene = new T.Scene();
  scene.fog = new T.FogExp2(0x10161f,.085);
  const camera = new T.PerspectiveCamera(43,1,.025,30);
  const controls = new OrbitControls(camera,renderer.domElement);
  controls.enableDamping = false;
  controls.minDistance = .5; controls.maxDistance = 10;
  controls.maxPolarAngle = Math.PI*.85;
  scene.add(new T.HemisphereLight(0xd2e6ff,0x1b2330,2.2));
  const key = new T.DirectionalLight(0xffffff,3); key.position.set(1,3,-2);scene.add(key);
  const rim = new T.DirectionalLight(0x6be5be,1.8);rim.position.set(-2,1,1);scene.add(rim);
  const grid = new T.GridHelper(8,32,0x44566a,0x263447);grid.position.y=-.75;scene.add(grid);
  const floor = new T.Mesh(new T.PlaneGeometry(8,8),new T.MeshStandardMaterial({color:0x111923,roughness:1,transparent:true,opacity:.3}));
  floor.rotation.x=-Math.PI/2;floor.position.y=-.755;scene.add(floor);
  const material = (color,opacity=1) => new T.MeshStandardMaterial({color,roughness:.55,metalness:.15,transparent:opacity<1,opacity});
  function mesh(group,geometry,mat,position=[0,0,0]) {
    const m=new T.Mesh(geometry,mat);m.position.set(...position);group.add(m);return m;
  }
  const head=new T.Group();scene.add(head);
  mesh(head,new T.BoxGeometry(.29,.155,.18),material(0xc3cbd5));
  mesh(head,new T.BoxGeometry(.265,.135,.018),material(0x8996a6),[0,0,-.092]);
  mesh(head,new T.BoxGeometry(.27,.13,.04),material(0x263341),[0,0,.105]);
  const strap=mesh(head,new T.TorusGeometry(.16,.013,8,40,Math.PI*1.8),material(0x697687),[0,.0,.075]);strap.rotation.x=Math.PI/2;
  const top=mesh(head,new T.BoxGeometry(.045,.016,.22),material(0x94a0b0),[0,.09,.08]);top.rotation.x=-.1;
  const cones = new T.Group();head.add(cones);
  let range = 1.2;
  const cameras=[];
  for (const sx of [-1,1]) for (const sy of [-1,1]) {
    const c=new T.Group();c.position.set(sx*.13,sy*.061,-.105);c.rotation.set(sy*.28,-sx*.5,0);cones.add(c);cameras.push(c);
    const lens=mesh(head,new T.CylinderGeometry(.012,.014,.012,16),material(0x141e2a),[sx*.13,sy*.061,-.106]);lens.rotation.x=Math.PI/2;
    mesh(head,new T.SphereGeometry(.006,10,8),material(0x658199),[sx*.13,sy*.061,-.114]);
  }
  function updateCones() {
    for(const c of cameras) {
      for(const child of [...c.children]) {c.remove(child);child.geometry?.dispose();child.material?.dispose();}
      const w=range*Math.tan(T.MathUtils.degToRad(42)),h=range*Math.tan(T.MathUtils.degToRad(37));
      const corners=[[-w,-h,-range],[w,-h,-range],[w,h,-range],[-w,h,-range]];
      const points=[];
      for(let i=0;i<4;i++) points.push(new T.Vector3(),new T.Vector3(...corners[i]),new T.Vector3(...corners[i]),new T.Vector3(...corners[(i+1)%4]));
      const lines=new T.LineSegments(new T.BufferGeometry().setFromPoints(points),new T.LineBasicMaterial({color:0xd9ad66,transparent:true,opacity:.26}));c.add(lines);
      // Camera-style top marker, as in a 3D editor's camera frustum.
      const marker=[[-.04,.023,-.075],[0,.07,-.075],[.04,.023,-.075]];
      c.add(new T.LineLoop(new T.BufferGeometry().setFromPoints(marker.map(p=>new T.Vector3(...p))),new T.LineBasicMaterial({color:0xeac47c,transparent:true,opacity:.65})));
    }
  }
  updateCones();
  const hands=[];
  for(const [index,color] of [0x6be5be,0x82acff].entries()) {
    const group=new T.Group();scene.add(group);hands.push(group);
    const body=mesh(group,new T.CapsuleGeometry(.026,.095,6,12),material(0xd3dbe6),[0,-.055,.01]);body.rotation.x=-.26;
    const pad=mesh(group,new T.SphereGeometry(.043,20,12),material(0xb3bfcd),[0,.014,-.012]);pad.scale.set(1,.35,1.25);
    const ring=mesh(group,new T.TorusGeometry(.064,.007,8,40),material(color),[0,.063,-.033]);ring.rotation.x=-.38;
    mesh(group,new T.CylinderGeometry(.01,.01,.006,12),material(0x27313d),[-.012,.033,-.015]);
    mesh(group,new T.SphereGeometry(.006,8,8),material(color),[.018,.032,-.014]);
    const ray=new T.Line(new T.BufferGeometry().setFromPoints([new T.Vector3(0,0,-.04),new T.Vector3(0,0,-.45)]),new T.LineBasicMaterial({color,transparent:true,opacity:.45}));group.add(ray);
    const halo=mesh(group,new T.RingGeometry(.078,.081,48),new T.MeshBasicMaterial({color,transparent:true,opacity:.3,side:T.DoubleSide}),[0,-.12,.01]);halo.rotation.x=-Math.PI/2;
    group.position.set(index===0?.38:-.38,-.2,-.68);
  }
  const labels=[['head-label',head,new T.Vector3(0,.19,0)],['right-label',hands[0],new T.Vector3(0,.16,0)],['left-label',hands[1],new T.Vector3(0,.16,0)]];
  let origin=null;
  function reset() {camera.position.set(1.15,.95,-1.65);controls.target.set(0,-.12,-.35);controls.update();draw();}
  function draw() {
    renderer.render(scene,camera);
    for(const [id,object,offset] of labels) {
      const v=object.position.clone().add(offset).project(camera),el=document.getElementById(id);
      el.style.left=((v.x+1)*container.clientWidth/2)+'px';el.style.top=((1-v.y)*container.clientHeight/2)+'px';
      el.hidden=v.z>1||v.z< -1;
    }
  }
  controls.addEventListener('change',draw);
  new ResizeObserver(()=>{renderer.setSize(container.clientWidth,container.clientHeight);camera.aspect=container.clientWidth/container.clientHeight;camera.updateProjectionMatrix();draw();}).observe(container);
  reset();
  return {
    reset,
    setFrusta(value){cones.visible=value;draw();},
    setRange(value){range=value;updateCones();draw();},
    update(state){
      if(state.live && !origin && (state.head.flags&2)) origin=new T.Vector3(...state.head.p);
      if(origin && state.head.flags&1) {
        if(state.head.flags&2) head.position.fromArray(state.head.p).sub(origin);
        head.quaternion.fromArray(state.head.q);
      }
      for(let i=0;i<2;i++) {
        const hand=state.hands[i],object=hands[i],tracked=state.live&&state.motion[i].tracked;
        if(origin&&(hand.flags&2)) object.position.fromArray(hand.p).sub(origin);
        if(hand.flags&1) object.quaternion.fromArray(hand.q);
        object.traverse(child=>{if(child.material){child.material.transparent=true;child.material.opacity=tracked?1:.55;}});
      }
      draw();
    }
  };
}
