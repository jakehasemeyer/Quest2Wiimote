import {trackingScene} from './scene.js';
const $=id=>document.getElementById(id);
let scene, lastSequence=-1, busy=false, toastTimer;
try {scene=trackingScene($('viewport'));} catch(error) {$('scene-error').hidden=false;$('scene-error').textContent='3D view unavailable: '+error.message;}
function toast(text,error=false){const el=$('toast');el.textContent=text;el.className=error?'error':'';el.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.hidden=true,error?12000:7000);}
async function command(action){
  if(busy)return;
  busy=true;document.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);
  const button=document.querySelector(`[data-action="${action}"]`),label=button?.textContent;
  if(button)button.textContent=action==='install'?'Installing…':'Working…';
  try{
    const r=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({serial:$('device').value,passthrough:$('passthrough').checked,ip:$('quest-ip').value})});
    const data=await r.json();if(data.error)throw Error(data.error);
    if(action==='logs'){$('log-text').textContent=data.message;$('log-dialog').showModal();}
    else if(data.message)toast(data.message);
    await poll();
  }catch(error){toast(error.message,true);}finally{busy=false;document.querySelectorAll('[data-action]').forEach(b=>b.disabled=false);if(button)button.textContent=label;}
}
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>command(button.dataset.action)));
$('refresh').onclick=()=>command('refresh');
$('setup-open').onclick=()=>$('setup-dialog').showModal();$('setup-close').onclick=()=>$('setup-dialog').close();$('log-close').onclick=()=>$('log-dialog').close();
$('reset-view').onclick=()=>scene?.reset();$('frusta').onchange=e=>scene?.setFrusta(e.target.checked);
$('range').oninput=e=>{$('range-value').textContent=Number(e.target.value).toFixed(1)+' m';scene?.setRange(Number(e.target.value));};
const history=[[],[]];
function graph(canvas,values,color){const c=canvas.getContext('2d'),w=canvas.width,h=canvas.height;c.clearRect(0,0,w,h);c.strokeStyle='#2b3644';c.beginPath();c.moveTo(0,h-4);c.lineTo(w,h-4);c.stroke();if(values.length<2)return;c.strokeStyle=color;c.lineWidth=1.7;c.beginPath();values.forEach((v,i)=>{const x=i*w/79,y=h-4-Math.min(v/240,1)*(h-8);i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
let lastDevices='',selectedDevice='';
$('device').onchange=()=>{selectedDevice=$('device').value;};
async function poll(){
  try{
    const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('Server unavailable');const s=await response.json();
    const deviceKey=JSON.stringify(s.devices);
    if(lastDevices!==deviceKey){
      lastDevices=deviceKey;const select=$('device');select.replaceChildren();
      for(const device of s.devices){const option=document.createElement('option');option.value=device.serial;option.textContent=device.serial+' · '+device.status;option.disabled=device.status!=='device';select.append(option);}
      if(!s.devices.some(d=>d.status==='device')){const option=document.createElement('option');option.value='';option.textContent='No authorized device';select.prepend(option);select.value='';}
      else select.value=s.devices.some(d=>d.serial===selectedDevice&&d.status==='device')?selectedDevice:s.device;
    }
    const unauthorized=s.devices.some(d=>d.status==='unauthorized'),available=s.devices.some(d=>d.status==='device');
    $('device-status').textContent=s.live?'Streaming controller data':available?'ADB connected':unauthorized?'USB authorization needed':'Not connected';
    $('station-dot').className='dot '+(s.live?'green':available?'amber':'');
    $('adb-hint').textContent=unauthorized?'Accept “Allow USB debugging” on the Quest.':available?'Ready. Install & start enables off-head keep-awake.':'Connect your Quest by USB and allow USB debugging.';
    $('connection-badge').className='pill'+(s.live?' live':'');$('connection-badge').replaceChildren();const dot=document.createElement('i');dot.className='dot'+(s.live?' green':'');$('connection-badge').append(dot,document.createTextNode(s.live?'Quest connected':s.age_ms!==null&&s.age_ms<500?'Session not focused':'Waiting for Quest'));
    $('transport').textContent=s.transport==='USB'?'USB / ADB':s.transport;
    $('hz').textContent=s.hz||'—';$('age').textContent=s.age_ms!==null&&s.age_ms<10000?s.age_ms:'—';
    $('dsu-status').textContent=s.dsu_clients?'Sending to Dolphin':'Listening';$('dsu-detail').textContent=s.dsu_clients?`${s.dsu_clients} slot subscription${s.dsu_clients===1?'':'s'} · ${s.dsu_packets.toLocaleString()} packets`:'Waiting for a DSU client';
    $('scene-status').querySelector('span').textContent=s.live?'Live tracking · 0.25 m grid':s.age_ms!==null?'Tracking paused · last known poses':'Reference layout · no live data';
    $('scene-status').querySelector('i').className='dot'+(s.live?' green':'');
    $('session-focus').textContent=s.live?'OpenXR focused':s.age_ms!==null&&s.age_ms<1000?'OpenXR not focused':'No active session';
    for(let i=0;i<2;i++){
      const card=$(i===0?'right-card':'left-card'),hand=s.hands[i],motion=s.motion[i],valid=s.live&&hand.active;
      const label=card.querySelector('.tracking-state');label.textContent=valid?(motion.tracked?'6DoF tracked':motion.orientation?'Rotation only':'Tracking lost'):'Offline';label.className='tracking-state'+(valid?(motion.tracked?' good':' warn'):'');
      const fmt=values=>values.map(v=>(Math.abs(v)<.005?0:v).toFixed(2)).join('  ');
      card.querySelector('.accel-value').textContent=valid?fmt(motion.accel):'—   —   —';card.querySelector('.gyro-value').textContent=valid?fmt(motion.gyro):'—   —   —';
      card.querySelector('.trigger-meter').value=valid?hand.trigger:0;card.querySelector('.grip-meter').value=valid?hand.squeeze:0;
      card.querySelectorAll('[data-bit]').forEach(el=>el.classList.toggle('active',valid&&!!(hand.buttons&Number(el.dataset.bit))));
      if(s.seq!==lastSequence){history[i].push(valid?Math.hypot(...motion.gyro):0);if(history[i].length>80)history[i].shift();}
      graph(card.querySelector('.spark'),history[i],i===0?'#6be5be':'#82acff');
    }
    lastSequence=s.seq;scene?.update(s);
  }catch(error){$('connection-badge').textContent='Desktop server offline';$('connection-badge').className='pill';}
}
async function loop(){await poll();setTimeout(loop,70);}loop();
