"""VRM Lab v2 — pose debug + TTS mouth sync test."""

import http.server
import threading
import webbrowser
from pathlib import Path

PORT = 8889
ROOT = Path(__file__).parent.parent
VRM_PATH = ROOT / "anima" / "desktop" / "frontend" / "model" / "flare" / "Flare.vrm"
# Find a TTS test audio if exists
TTS_DIR = ROOT / "data" / "voice"

HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>VRM Lab v2</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#111;color:#fff;font:13px/1.5 'Segoe UI',system-ui,sans-serif;display:flex;height:100vh;overflow:hidden}
#vp{flex:1;position:relative}
canvas{display:block;width:100%;height:100%}
#hud{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.7);padding:6px 10px;border-radius:6px;font:11px monospace;color:#aaa}
#hud b{color:#fff}
#panel{width:340px;background:#0a0a0a;border-left:1px solid #333;display:flex;flex-direction:column;overflow:hidden}
.tabs{display:flex;border-bottom:1px solid #333;flex-shrink:0}
.tab{flex:1;padding:7px;text-align:center;cursor:pointer;color:#666;border-bottom:2px solid transparent;font-size:11px}
.tab.on{color:#fff;border-bottom-color:#fff}
.tp{display:none;flex:1;overflow-y:auto;padding:10px}
.tp.on{display:block}
.st{font-size:10px;color:#555;text-transform:uppercase;letter-spacing:1px;margin:10px 0 6px}
.bg{display:flex;flex-wrap:wrap;gap:3px}
.b{padding:3px 7px;background:#222;border:1px solid #444;border-radius:3px;color:#ccc;cursor:pointer;font-size:11px}
.b:hover{background:#333;color:#fff}
.b.on{background:#333;color:#0f0;border-color:#0f0}
.sr{display:flex;align-items:center;gap:6px;margin-bottom:4px}
.sr label{width:90px;font-size:10px;color:#777;flex-shrink:0}
.sr input[type=range]{flex:1;height:4px}
.sr .v{width:40px;text-align:right;font-size:10px;color:#999}
.mi{padding:3px 0;border-bottom:1px solid #1a1a1a;display:flex;justify-content:space-between;align-items:center;font-size:11px}
.mi label{color:#aaa}
#chatlog{height:160px;overflow-y:auto;background:#050505;border:1px solid #333;border-radius:4px;padding:6px;margin-bottom:6px;font-size:11px}
.cm{margin-bottom:3px}.cm.ai{color:#8f8}.cm.em{color:#ff8;font-size:10px}
</style>
</head>
<body>
<div id="vp"><canvas id="c"></canvas><div id="hud">Loading...</div></div>
<div id="panel">
<div class="tabs">
  <div class="tab on" data-t="expr">Expr</div>
  <div class="tab" data-t="pose">Pose</div>
  <div class="tab" data-t="mesh">Mesh</div>
  <div class="tab" data-t="sim">AI Sim</div>
</div>
<div class="tp on" id="t-expr">
  <div class="st">Preset</div><div class="bg" id="ep"></div>
  <div class="st">Custom</div><div class="bg" id="ec"></div>
  <div class="st">Sliders</div><div id="es"></div>
</div>
<div class="tp" id="t-pose">
  <div class="st">Quick Pose</div>
  <div class="bg">
    <button class="b" onclick="doPose('tpose')">T-Pose</button>
    <button class="b" onclick="doPose('natural')">Natural</button>
    <button class="b" onclick="doPose('hip')">Hands Hip</button>
    <button class="b" onclick="doPose('front')">Arms Front</button>
    <button class="b" onclick="doPose('behind')">Hands Behind</button>
  </div>
  <div class="st">Toggles</div>
  <div class="bg">
    <button class="b on" id="tb" onclick="this.classList.toggle('on');blinkOn=!blinkOn">Blink</button>
    <button class="b on" id="tbr" onclick="this.classList.toggle('on');breathOn=!breathOn">Breathe</button>
  </div>
  <div class="st">Bone Rotations (degrees)</div>
  <div id="bs"></div>
  <div class="st">Debug</div>
  <pre id="bdbg" style="font-size:10px;color:#555;max-height:200px;overflow:auto"></pre>
</div>
<div class="tp" id="t-mesh">
  <div class="st">Meshes (toggle visibility)</div>
  <div id="ml"></div>
</div>
<div class="tp" id="t-sim">
  <div id="chatlog"></div>
  <div class="st">Chat</div>
  <div class="bg">
    <button class="b" onclick="simChat()">Random</button>
    <button class="b" onclick="simEmo('happy')">Happy</button>
    <button class="b" onclick="simEmo('sad')">Sad</button>
    <button class="b" onclick="simEmo('angry')">Angry</button>
    <button class="b" onclick="simEmo('excited')">Excited</button>
  </div>
  <div class="st">Mouth Sync</div>
  <div class="bg">
    <button class="b" onclick="simMouth()">Sine Wave (2s)</button>
    <button class="b" onclick="playTTS()">Play TTS Audio</button>
  </div>
  <div class="sr"><label>Mouth</label><input type="range" id="mslider" min="0" max="100" value="0"><span class="v" id="mval">0</span></div>
  <audio id="ttsAudio" style="display:block;width:100%;margin-top:8px" controls></audio>
</div>
</div>

<script type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.min.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/","@pixiv/three-vrm":"https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@3.3.3/lib/three-vrm.module.min.js"}}
</script>
<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const hud = document.getElementById('hud');
const canvas = document.getElementById('c');

const renderer = new THREE.WebGLRenderer({canvas, antialias:true, powerPreference:'high-performance'});
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.setClearColor(0x1a1a1a);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30,1,0.1,100);
camera.position.set(0,1.3,3);

const ctrl = new OrbitControls(camera, canvas);
ctrl.target.set(0,1,0); ctrl.update(); ctrl.enableDamping = true;

scene.add(new THREE.AmbientLight(0xffffff,0.7));
const dl = new THREE.DirectionalLight(0xffffff,1); dl.position.set(2,3,2); scene.add(dl);
scene.add(new THREE.GridHelper(10,10,0x333333,0x222222));

function resize(){const vp=document.getElementById('vp');const w=vp.clientWidth,h=vp.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}
resize(); window.addEventListener('resize',resize);

let vrm=null, blinkOn=true, breathOn=true;
let blinkTimer=3, blinkPhase=0, blinkVal=0;
let mouthTarget=0, mouthCur=0;

// Viseme state — test both VRM presets and ARKit shapes
let vis = {aa:0,ih:0,ou:0,ee:0,oh:0,JawOpen:0,MouthFunnel:0,MouthSmileLeft:0,MouthSmileRight:0};
let visT = {aa:0,ih:0,ou:0,ee:0,oh:0,JawOpen:0,MouthFunnel:0,MouthSmileLeft:0,MouthSmileRight:0};
let lipSyncActive = false;

// ── Load ──
hud.textContent = 'Downloading...';
const loader = new GLTFLoader();
loader.register(p=>new VRMLoaderPlugin(p));
loader.load('/model/Flare.vrm', gltf=>{
  vrm = gltf.userData.vrm;
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.rotateVRM0(vrm);
  scene.add(vrm.scene);

  hud.innerHTML = `<b>Flare</b> | ${vrm.expressionManager.expressions.length} expr | ${Object.keys(vrm.humanoid.humanBones).length} bones`;

  buildExpr();
  buildMesh();
  buildBones();
  debugBones();
  doPose('natural');

  // Check for TTS audio
  fetch('/tts-list').then(r=>r.json()).then(files=>{
    if(files.length){
      document.getElementById('ttsAudio').src = '/tts/' + files[0];
    }
  });
}, p=>{if(p.total) hud.textContent='DL: '+Math.round(p.loaded/p.total*100)+'%';});

// ── Render ──
const clock = new THREE.Clock();
(function anim(){
  requestAnimationFrame(anim);
  const dt = clock.getDelta(), t = clock.elapsedTime;
  if(vrm){
    if(breathOn) vrm.scene.position.y = Math.sin(t*1.5)*0.003;
    if(blinkOn){
      blinkTimer-=dt;
      if(blinkPhase===0&&blinkTimer<=0){blinkPhase=1;blinkVal=0;}
      else if(blinkPhase===1){blinkVal+=dt*18;if(blinkVal>=1){blinkVal=1;blinkPhase=2;}}
      else if(blinkPhase===2){blinkVal-=dt*12;if(blinkVal<=0){blinkVal=0;blinkPhase=0;blinkTimer=2+Math.random()*5;}}
      try{vrm.expressionManager.setValue('blink',blinkVal);}catch(_){}
    }
    // Lip sync
    if (lipSyncActive) {
      const sm = 0.22;
      for (const k in vis) { vis[k] += (visT[k] - vis[k]) * sm; }
      try {
        for (const k in vis) vrm.expressionManager.setValue(k, vis[k]);
      } catch(_) {}
    } else if (mouthTarget > 0.01 || mouthCur > 0.01) {
      // Manual slider / simMouth — only set when actually talking
      mouthCur+=(mouthTarget-mouthCur)*0.3;
      if (mouthCur < 0.01) mouthCur = 0;
      try {
        vrm.expressionManager.setValue('JawOpen', mouthCur * 0.6);
        vrm.expressionManager.setValue('oh', mouthCur * 0.4);
      } catch(_) {}
    }
    vrm.update(dt);
  }
  ctrl.update();
  renderer.render(scene,camera);
})();

// ── Pose ──
// VRM bones use the humanoid's getNormalizedBoneNode / getRawBoneNode
// After rotateVRM0, the coordinate system is: +X=right, +Y=up, +Z=forward
// For arms: rotating around Z axis should lower them from T-pose
window.doPose = function(name){
  if(!vrm) return;

  // Use getNormalizedBoneNode for proper VRM bone access
  const hb = vrm.humanoid;
  const get = (n) => {
    // Try normalized first, then raw
    try { return hb.getNormalizedBoneNode(n); } catch(_) {}
    try { return hb.getRawBoneNode(n); } catch(_) {}
    return hb.humanBones[n]?.node || null;
  };

  // Reset ALL bones to rest pose
  const allBones = Object.keys(hb.humanBones);
  for (const bn of allBones) {
    const node = get(bn);
    if (node) node.quaternion.identity();
  }

  const q = (node, x, y, z) => {
    if (!node) return;
    const euler = new THREE.Euler(x, y, z, 'XYZ');
    node.quaternion.setFromEuler(euler);
  };

  if (name === 'natural') {
    q(get('leftUpperArm'), 0, 0, 1.2);     // arm down
    q(get('rightUpperArm'), 0, 0, -1.2);
    q(get('leftLowerArm'), 0, 0, 0.15);     // slight elbow bend
    q(get('rightLowerArm'), 0, 0, -0.15);
  } else if (name === 'hip') {
    q(get('leftUpperArm'), 0.4, 0, 0.9);
    q(get('rightUpperArm'), 0.4, 0, -0.9);
    q(get('leftLowerArm'), 0, -0.6, 0.6);
    q(get('rightLowerArm'), 0, 0.6, -0.6);
  } else if (name === 'front') {
    q(get('leftUpperArm'), -1.0, 0, 0.4);
    q(get('rightUpperArm'), -1.0, 0, -0.4);
    q(get('leftLowerArm'), 0, -0.8, 0);
    q(get('rightLowerArm'), 0, 0.8, 0);
  } else if (name === 'behind') {
    q(get('leftUpperArm'), 0.5, 0, 1.0);
    q(get('rightUpperArm'), 0.5, 0, -1.0);
    q(get('leftLowerArm'), 0, 0, 1.2);
    q(get('rightLowerArm'), 0, 0, -1.2);
  }
  // tpose = quaternion.identity (already done above)
};

// ── Debug bones ──
function debugBones(){
  if(!vrm) return;
  const dbg = document.getElementById('bdbg');
  const hb = vrm.humanoid;
  let txt = '';
  for (const name of ['leftUpperArm','rightUpperArm','leftLowerArm','rightLowerArm','head','neck','spine']) {
    const direct = hb.humanBones[name]?.node;
    let norm = null, raw = null;
    try { norm = hb.getNormalizedBoneNode(name); } catch(_){}
    try { raw = hb.getRawBoneNode(name); } catch(_){}
    txt += `${name}:\n`;
    txt += `  direct: ${direct ? direct.name : 'null'}\n`;
    txt += `  normalized: ${norm ? norm.name : 'null'}\n`;
    txt += `  raw: ${raw ? raw.name : 'null'}\n`;
    if (direct) txt += `  pos: ${direct.position.toArray().map(v=>v.toFixed(3))}\n`;
    txt += '\n';
  }
  dbg.textContent = txt;
}

// ── Bone sliders ──
function buildBones(){
  const el = document.getElementById('bs');
  const names = ['leftUpperArm','rightUpperArm','leftLowerArm','rightLowerArm','spine','chest','neck','head'];
  const hb = vrm.humanoid;

  for(const bn of names){
    const get = () => {
      try { return hb.getNormalizedBoneNode(bn); } catch(_) {}
      try { return hb.getRawBoneNode(bn); } catch(_) {}
      return hb.humanBones[bn]?.node || null;
    };
    const node = get();
    if(!node) continue;

    for(const axis of ['x','y','z']){
      const row = document.createElement('div');
      row.className='sr';
      row.innerHTML=`<label>${bn}.${axis}</label><input type="range" min="-180" max="180" value="0"><span class="v">0°</span>`;
      const sl=row.querySelector('input'), vl=row.querySelector('.v');
      sl.oninput=()=>{
        const deg=parseInt(sl.value);
        // Use quaternion from euler for proper rotation
        const cur = new THREE.Euler().copy(node.rotation);
        cur[axis] = deg * Math.PI/180;
        node.quaternion.setFromEuler(cur);
        vl.textContent=deg+'°';
      };
      el.appendChild(row);
    }
  }
}

// ── Expressions ──
function buildExpr(){
  const presets=['neutral','happy','angry','sad','relaxed','blink','aa','ih','ou','ee','oh'];
  const ep=document.getElementById('ep'),ec=document.getElementById('ec'),es=document.getElementById('es');
  vrm.expressionManager.expressions.forEach(expr=>{
    const n=expr.expressionName;
    const btn=document.createElement('button');
    btn.className='b'; btn.textContent=n;
    btn.onclick=()=>{vrm.expressionManager.resetValues();vrm.expressionManager.setValue(n,1);setTimeout(()=>vrm.expressionManager.setValue(n,0),1500);};
    (presets.includes(n)?ep:ec).appendChild(btn);
  });
  vrm.expressionManager.expressions.slice(0,15).forEach(expr=>{
    const n=expr.expressionName;
    const row=document.createElement('div');row.className='sr';
    row.innerHTML=`<label>${n}</label><input type="range" min="0" max="100" value="0"><span class="v">0</span>`;
    const sl=row.querySelector('input'),vl=row.querySelector('.v');
    sl.oninput=()=>{const v=sl.value/100;vrm.expressionManager.setValue(n,v);vl.textContent=v.toFixed(2);};
    es.appendChild(row);
  });
}

// ── Mesh ──
function buildMesh(){
  const el=document.getElementById('ml');
  vrm.scene.traverse(c=>{
    if(c.isMesh||c.isSkinnedMesh){
      const div=document.createElement('div');div.className='mi';
      const n=c.name||c.uuid.substring(0,8);
      div.innerHTML=`<label title="${n}">${n.substring(0,28)}</label><input type="checkbox" checked>`;
      div.querySelector('input').onchange=e=>{c.visible=e.target.checked;};
      el.appendChild(div);
    }
  });
}

// ── AI Sim ──
const chatlog=document.getElementById('chatlog');
const phrases=['主人好~','嗯让我想想…','好的！','诶嘿嘿~','有点累了呢…','哇！好厉害！','要不要休息一下？'];
window.simChat=()=>{const t=phrases[Math.floor(Math.random()*phrases.length)];chatlog.innerHTML+=`<div class="cm ai">[Eva] ${t}</div>`;chatlog.scrollTop=1e5;simMouth();simEmo(['happy','relaxed','sad'][Math.floor(Math.random()*3)]);};
window.simEmo=emo=>{if(!vrm)return;chatlog.innerHTML+=`<div class="cm em">[emo: ${emo}]</div>`;chatlog.scrollTop=1e5;vrm.expressionManager.resetValues();const m={happy:()=>vrm.expressionManager.setValue('happy',.8),sad:()=>vrm.expressionManager.setValue('sad',.7),angry:()=>vrm.expressionManager.setValue('angry',.8),excited:()=>{vrm.expressionManager.setValue('happy',.9);vrm.expressionManager.setValue('Kirakira',.5);}};if(m[emo])m[emo]();setTimeout(()=>{if(vrm)vrm.expressionManager.resetValues();},3000);};
window.simMouth=()=>{let s=performance.now();const iv=setInterval(()=>{const e=(performance.now()-s)/1000;if(e>2){mouthTarget=0;clearInterval(iv);return;}mouthTarget=Math.sin(e*12)*.3+Math.sin(e*7)*.2+.3;},33);};

// Manual mouth slider
document.getElementById('mslider').oninput = function() {
  mouthTarget = this.value / 100;
  document.getElementById('mval').textContent = mouthTarget.toFixed(2);
};

// ═══ Viseme Engine — pre-analyze audio → vowel timeline → playback ═══
//
// Pipeline:
//   1. Decode audio → raw PCM samples
//   2. Slice into 20ms frames
//   3. Each frame → FFT → extract F1/F2 formant energies
//   4. F1/F2 → vowel classification (linguistically grounded)
//   5. Build timeline: [{t, aa, ih, ou, ee, oh, jaw}, ...]
//   6. On playback: lerp between timeline keyframes by audio.currentTime
//
// Formant mapping (female voice, approximate):
//   /a/ (aa):  F1≈800Hz  F2≈1200Hz — open, central
//   /i/ (ih):  F1≈300Hz  F2≈2500Hz — closed, front
//   /u/ (ou):  F1≈300Hz  F2≈800Hz  — closed, back
//   /e/ (ee):  F1≈400Hz  F2≈2200Hz — mid, front
//   /o/ (oh):  F1≈500Hz  F2≈900Hz  — mid, back

let audioCtx = null;
let visemeTimeline = null; // [{t, aa, ih, ou, ee, oh, jaw}, ...]

async function buildVisemeTimeline(audioUrl) {
  if (!audioCtx) audioCtx = new AudioContext();

  const resp = await fetch(audioUrl);
  const arrayBuf = await resp.arrayBuffer();
  const audioBuf = await audioCtx.decodeAudioData(arrayBuf);

  const sr = audioBuf.sampleRate;
  const raw = audioBuf.getChannelData(0);
  const frameMs = 25; // 40fps
  const frameSz = Math.floor(sr * frameMs / 1000);
  const numFrames = Math.floor(raw.length / frameSz);
  const timeline = [];

  // Use OfflineAudioContext with AnalyserNode for fast native FFT
  const offCtx = new OfflineAudioContext(1, raw.length, sr);
  const src = offCtx.createBufferSource();
  src.buffer = audioBuf;
  const analyser = offCtx.createAnalyser();
  analyser.fftSize = 512; // fast, 256 bins
  src.connect(analyser);
  analyser.connect(offCtx.destination);
  src.start();

  // Process frame by frame using suspend/resume
  const binHz = sr / analyser.fftSize;
  const freqBuf = new Uint8Array(analyser.frequencyBinCount);

  for (let f = 0; f < numFrames; f++) {
    const t = f * frameMs / 1000;
    const samplePos = f * frameSz;

    // Suspend at each frame position
    try {
      offCtx.suspend(samplePos / sr).then(() => {
        analyser.getByteFrequencyData(freqBuf);
        offCtx.resume();
      });
    } catch(_) {}
  }

  // Can't easily use suspend/resume portably — fallback to raw PCM analysis
  // Use fast autocorrelation-free approach: just band energy from raw samples
  for (let f = 0; f < numFrames; f++) {
    const offset = f * frameSz;
    const t = f * frameMs / 1000;

    // RMS energy in frequency bands (approximate via sample amplitude patterns)
    // Fast approach: compute zero-crossing rate + RMS for F1/F2 proxy
    let rms = 0, zeroCross = 0, highE = 0;
    for (let i = 0; i < frameSz && offset+i < raw.length; i++) {
      const s = raw[offset + i];
      rms += s * s;
      if (i > 0 && ((raw[offset+i-1] >= 0) !== (s >= 0))) zeroCross++;
      // High frequency energy: diff of adjacent samples
      if (i > 0) { const d = s - raw[offset+i-1]; highE += d * d; }
    }
    rms = Math.sqrt(rms / frameSz);
    zeroCross = zeroCross / frameSz * sr; // zero crossing rate in Hz
    highE = Math.sqrt(highE / frameSz);

    // Noise gate
    const zero = {t, aa:0, ih:0, ou:0, ee:0, oh:0, JawOpen:0, MouthFunnel:0, MouthSmileLeft:0, MouthSmileRight:0};
    if (rms < 0.008) {
      timeline.push(zero);
      continue;
    }

    // Map RMS → overall mouth openness (0-0.8)
    const energy = Math.min(0.8, rms * 6);

    // Zero-crossing rate roughly indicates formant frequency:
    //   Low ZCR (<1500) → low vowels /a/, /o/
    //   Mid ZCR (1500-3500) → mid vowels /e/
    //   High ZCR (>3500) → high vowels /i/, /u/
    // High-freq energy ratio indicates front/back
    const hfRatio = highE / Math.max(0.001, rms);

    // Map to both VRM presets + ARKit (test which ones actually work)
    let aa=0, ih=0, ou=0, ee=0, oh=0;
    let JawOpen=0, MouthFunnel=0, MouthSmileLeft=0, MouthSmileRight=0;

    if (zeroCross < 1500) {
      // Low ZCR → open /a/, /o/
      aa = energy * 0.7;          // VRM preset
      oh = energy * 0.5;
      JawOpen = energy * 0.7;    // ARKit
      if (hfRatio > 1.2) {
        ou = energy * 0.5;
        MouthFunnel = energy * 0.4;
      }
    } else if (zeroCross < 3500) {
      // Mid ZCR → /e/
      ee = energy * 0.6;
      JawOpen = energy * 0.4;
      MouthSmileLeft = energy * 0.3;
      MouthSmileRight = energy * 0.3;
    } else {
      // High ZCR → closed /i/, /u/
      JawOpen = energy * 0.2;
      if (hfRatio > 1.5) {
        ih = energy * 0.5;
        MouthSmileLeft = energy * 0.4;
        MouthSmileRight = energy * 0.4;
      } else {
        ou = energy * 0.5;
        MouthFunnel = energy * 0.4;
      }
    }

    timeline.push({t, aa, ih, ou, ee, oh, JawOpen, MouthFunnel, MouthSmileLeft, MouthSmileRight});
  }

  // Smooth (2-pass moving average)
  const keys = ['aa','ih','ou','ee','oh','JawOpen','MouthFunnel','MouthSmileLeft','MouthSmileRight'];
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 1; i < timeline.length - 1; i++) {
      for (const k of keys) {
        timeline[i][k] = (timeline[i-1][k] + timeline[i][k] + timeline[i+1][k]) / 3;
      }
    }
  }

  return timeline;
}

// Lookup timeline at time t with linear interpolation
function lookupViseme(timeline, t) {
  const Z = {aa:0,ih:0,ou:0,ee:0,oh:0,JawOpen:0,MouthFunnel:0,MouthSmileLeft:0,MouthSmileRight:0};
  if (!timeline || !timeline.length) return Z;
  if (t <= timeline[0].t) return timeline[0];
  if (t >= timeline[timeline.length-1].t) return Z;

  // Binary search for frame
  let lo = 0, hi = timeline.length - 1;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (timeline[mid].t <= t) lo = mid; else hi = mid;
  }

  const a = timeline[lo], b = timeline[hi];
  const frac = (t - a.t) / Math.max(0.001, b.t - a.t);
  const keys = ['aa','ih','ou','ee','oh','JawOpen','MouthFunnel','MouthSmileLeft','MouthSmileRight'];
  const result = { t };
  for (const k of keys) result[k] = a[k] + (b[k] - a[k]) * frac;
  return result;
}

window.playTTS = async function() {
  const audio = document.getElementById('ttsAudio');
  if (!audio.src) { chatlog.innerHTML += '<div class="cm em">No TTS audio found</div>'; return; }

  chatlog.innerHTML += '<div class="cm em">[analyzing audio...]</div>';
  chatlog.scrollTop = 1e5;

  // Pre-analyze entire audio
  visemeTimeline = await buildVisemeTimeline(audio.src);
  chatlog.innerHTML += '<div class="cm em">[' + visemeTimeline.length + ' viseme frames built]</div>';
  chatlog.scrollTop = 1e5;

  lipSyncActive = true;
  audio.currentTime = 0;
  audio.play();

  // Playback loop — lookup timeline by audio time
  const iv = setInterval(() => {
    if (audio.paused || audio.ended) {
      for (const k in visT) visT[k] = 0;
      setTimeout(() => { lipSyncActive = false; visemeTimeline = null; }, 200);
      clearInterval(iv);
      return;
    }
    const v = lookupViseme(visemeTimeline, audio.currentTime);
    for (const k in visT) visT[k] = v[k] || 0;
  }, 16); // 60fps playback
};

// Tabs
document.querySelectorAll('.tab').forEach(t=>{t.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));document.querySelectorAll('.tp').forEach(x=>x.classList.remove('on'));t.classList.add('on');document.getElementById('t-'+t.dataset.t).classList.add('on');});});
</script>
</body>
</html>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path == "/model/Flare.vrm":
            self._serve_file(VRM_PATH, "model/gltf-binary")
        elif self.path.startswith("/tts/"):
            fname = self.path[5:]
            p = TTS_DIR / fname
            if p.exists():
                self._serve_file(p, "audio/wav")
            else:
                self.send_error(404)
        elif self.path == "/tts-list":
            files = []
            if TTS_DIR.exists():
                files = [f.name for f in sorted(TTS_DIR.glob("tts_*.wav"), key=lambda f: f.stat().st_mtime, reverse=True)[:5]]
            import json
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(files).encode())
        else:
            self.send_error(404)

    def _serve_file(self, path, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"VRM Lab v2: http://localhost:{PORT}")
    threading.Timer(1, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
