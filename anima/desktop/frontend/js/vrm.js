/**
 * VRM Avatar — migrated from verified vrm_lab.py
 *
 * Tested features:
 * - Pose: getNormalizedBoneNode + quaternion.setFromEuler (verified working)
 * - Expressions: aa/ih/ou/ee/oh + ARKit JawOpen/MouthFunnel/MouthSmile (verified working)
 * - Blink: random 2-7s interval
 * - Viseme lip sync: pre-analyzed audio timeline with ZCR vowel classification
 * - LookAt: vrm.lookAt.lookAt(vec3) (NO .target property)
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const MODEL_URL = '/desktop/static/model/flare/Flare.vrm';
let VRMLoaderPlugin = null, VRMUtils = null;

export class VRMAvatar {
  constructor(canvas, container) {
    this.canvas = canvas;
    this.container = container;
    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.vrm = null;
    this.clock = new THREE.Clock();
    this._paused = false;
    this._animId = null;
    this._resizeObs = null;
    this._lastEmo = '';
    this._mx = 0;
    this._my = 0;
    this._lookAtVec = new THREE.Vector3();

    // Blink
    this._blinkTimer = 3;
    this._blinkPhase = 0;
    this._blinkVal = 0;

    // Lip sync state
    this._vis = {aa:0,ih:0,ou:0,ee:0,oh:0,JawOpen:0,MouthFunnel:0,MouthSmileLeft:0,MouthSmileRight:0};
    this._visT = {aa:0,ih:0,ou:0,ee:0,oh:0,JawOpen:0,MouthFunnel:0,MouthSmileLeft:0,MouthSmileRight:0};
    this._lipSyncActive = false;
    this._visemeTimeline = null;
    this._audioCtx = null;

    // Simple mouth (non-viseme fallback)
    this._mouthTarget = 0;
    this._mouthCur = 0;

    this._controls = null;
    this._meshes = []; // for clothing toggle

    this._onMouse = (e) => {
      this._mx = (e.clientX / window.innerWidth) * 2 - 1;
      this._my = -(e.clientY / window.innerHeight) * 2 + 1;
    };
  }

  async init() {
    if (!VRMLoaderPlugin) {
      console.log('VRM: loading three-vrm...');
      const mod = await import('@pixiv/three-vrm');
      VRMLoaderPlugin = mod.VRMLoaderPlugin;
      VRMUtils = mod.VRMUtils;
      console.log('VRM: three-vrm OK');
    }

    const w = this.container.clientWidth, h = this.container.clientHeight;
    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.setSize(w, h);
    this.renderer.setClearColor(0x0a0a0a);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, w / h, 0.1, 100);
    this.camera.position.set(0, 1.3, 3);
    this.camera.lookAt(0, 1, 0);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const dl = new THREE.DirectionalLight(0xffffff, 1);
    dl.position.set(2, 3, 2);
    this.scene.add(dl);

    // OrbitControls — mouse drag to rotate, scroll to zoom
    this._controls = new OrbitControls(this.camera, this.canvas);
    this._controls.target.set(0, 1, 0);
    this._controls.enableDamping = true;
    this._controls.dampingFactor = 0.1;
    this._controls.update();

    this._setupResize();
    document.addEventListener('mousemove', this._onMouse);
    this._animate();

    console.log('VRM: downloading model...');
    try {
      await this._loadModel();
      this._setNaturalPose();
      // Collect meshes for clothing toggle
      this._meshes = [];
      this.vrm.scene.traverse(c => {
        if (c.isMesh || c.isSkinnedMesh) {
          this._meshes.push({ name: c.name || c.uuid.substring(0,8), mesh: c, visible: true });
        }
      });
      console.log('VRM: loaded, ' + this._meshes.length + ' meshes');
    } catch (e) {
      console.error('VRM: load failed:', e);
      throw e;
    }
  }

  _loadModel() {
    return new Promise((resolve, reject) => {
      const loader = new GLTFLoader();
      loader.register(p => new VRMLoaderPlugin(p));
      loader.load(MODEL_URL,
        gltf => {
          const vrm = gltf.userData.vrm;
          if (!vrm) { reject(new Error('No VRM data')); return; }
          VRMUtils.removeUnnecessaryVertices(gltf.scene);
          VRMUtils.rotateVRM0(vrm);
          vrm.scene.rotation.y = Math.PI;  // Face the camera (VRM 0.x faces +Z by default)
          this.scene.add(vrm.scene);
          this.vrm = vrm;
          resolve();
        },
        p => { if (p.total > 0 && Math.round(p.loaded/p.total*100) % 25 === 0) console.log('VRM: ' + Math.round(p.loaded/p.total*100) + '%'); },
        reject
      );
    });
  }

  // ── Pose (verified in Lab) ──
  _getBone(name) {
    if (!this.vrm?.humanoid) return null;
    const hb = this.vrm.humanoid;
    try { return hb.getNormalizedBoneNode(name); } catch (_) {}
    try { return hb.getRawBoneNode(name); } catch (_) {}
    return hb.humanBones[name]?.node || null;
  }

  _setBoneRot(name, x, y, z) {
    const node = this._getBone(name);
    if (!node) return;
    node.quaternion.setFromEuler(new THREE.Euler(x, y, z, 'XYZ'));
  }

  _setNaturalPose() {
    if (!this.vrm?.humanoid) return;
    // Reset bones EXCEPT hips (hips holds the rotateVRM0 orientation)
    for (const bn of Object.keys(this.vrm.humanoid.humanBones)) {
      if (bn === 'hips') continue;
      const node = this._getBone(bn);
      if (node) node.quaternion.identity();
    }
    // Arms down
    this._setBoneRot('leftUpperArm', 0, 0, 1.2);
    this._setBoneRot('rightUpperArm', 0, 0, -1.2);
    this._setBoneRot('leftLowerArm', 0, 0, 0.15);
    this._setBoneRot('rightLowerArm', 0, 0, -0.15);
  }

  // ── Resize ──
  _setupResize() {
    this._resizeObs = new ResizeObserver(() => {
      if (this._paused) return;
      const w = this.container.clientWidth, h = this.container.clientHeight;
      if (w < 1 || h < 1) return;
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(w, h);
    });
    this._resizeObs.observe(this.container);
  }

  // ── Render loop ──
  _animate() {
    if (this._paused) return;
    this._animId = requestAnimationFrame(() => this._animate());
    const dt = this.clock.getDelta();
    const t = this.clock.elapsedTime;

    if (this.vrm) {
      // Breathing
      this.vrm.scene.position.y = Math.sin(t * 1.5) * 0.003;
      this.vrm.scene.rotation.y = Math.PI + Math.sin(t * 0.4) * 0.015;

      // LookAt
      if (this.vrm.lookAt) {
        this._lookAtVec.set(this._mx * 2, this._my * 1.5 + 1.2, 2);
        try { this.vrm.lookAt.lookAt(this._lookAtVec); } catch (_) {}
      }

      // Blink
      this._doBlink(dt);

      // Lip sync
      if (this._lipSyncActive) {
        const sm = 0.22;
        for (const k in this._vis) { this._vis[k] += (this._visT[k] - this._vis[k]) * sm; }
        try {
          for (const k in this._vis) this.vrm.expressionManager.setValue(k, this._vis[k]);
        } catch (_) {}
      } else if (this._mouthTarget > 0.01 || this._mouthCur > 0.01) {
        this._mouthCur += (this._mouthTarget - this._mouthCur) * 0.3;
        if (this._mouthCur < 0.01) this._mouthCur = 0;
        try {
          this.vrm.expressionManager.setValue('JawOpen', this._mouthCur * 0.6);
          this.vrm.expressionManager.setValue('oh', this._mouthCur * 0.4);
        } catch (_) {}
      }

      this.vrm.update(dt);
    }
    if (this._controls) this._controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  _doBlink(dt) {
    this._blinkTimer -= dt;
    if (this._blinkPhase === 0 && this._blinkTimer <= 0) { this._blinkPhase = 1; this._blinkVal = 0; }
    else if (this._blinkPhase === 1) { this._blinkVal += dt * 18; if (this._blinkVal >= 1) { this._blinkVal = 1; this._blinkPhase = 2; } }
    else if (this._blinkPhase === 2) { this._blinkVal -= dt * 12; if (this._blinkVal <= 0) { this._blinkVal = 0; this._blinkPhase = 0; this._blinkTimer = 2 + Math.random() * 5; } }
    try { this.vrm.expressionManager.setValue('blink', this._blinkVal); } catch (_) {}
  }

  // ── Viseme engine (pre-analyze audio → timeline → playback) ──
  async startLipSync(audioUrl) {
    if (!this._audioCtx) this._audioCtx = new AudioContext();
    const resp = await fetch(audioUrl);
    const arrayBuf = await resp.arrayBuffer();
    const audioBuf = await this._audioCtx.decodeAudioData(arrayBuf);

    const sr = audioBuf.sampleRate;
    const raw = audioBuf.getChannelData(0);
    const frameMs = 25;
    const frameSz = Math.floor(sr * frameMs / 1000);
    const numFrames = Math.floor(raw.length / frameSz);
    const timeline = [];

    for (let f = 0; f < numFrames; f++) {
      const offset = f * frameSz;
      const t = f * frameMs / 1000;
      let rms = 0, zeroCross = 0, highE = 0;
      for (let i = 0; i < frameSz && offset + i < raw.length; i++) {
        const s = raw[offset + i];
        rms += s * s;
        if (i > 0 && ((raw[offset + i - 1] >= 0) !== (s >= 0))) zeroCross++;
        if (i > 0) { const d = s - raw[offset + i - 1]; highE += d * d; }
      }
      rms = Math.sqrt(rms / frameSz);
      zeroCross = zeroCross / frameSz * sr;
      highE = Math.sqrt(highE / frameSz);

      const zero = {t, aa:0, ih:0, ou:0, ee:0, oh:0, JawOpen:0, MouthFunnel:0, MouthSmileLeft:0, MouthSmileRight:0};
      if (rms < 0.008) { timeline.push(zero); continue; }

      const energy = Math.min(0.8, rms * 6);
      const hfRatio = highE / Math.max(0.001, rms);
      let aa=0, ih=0, ou=0, ee=0, oh=0, JawOpen=0, MouthFunnel=0, MouthSmileLeft=0, MouthSmileRight=0;

      if (zeroCross < 1500) {
        aa = energy * 0.7; oh = energy * 0.5; JawOpen = energy * 0.7;
        if (hfRatio > 1.2) { ou = energy * 0.5; MouthFunnel = energy * 0.4; }
      } else if (zeroCross < 3500) {
        ee = energy * 0.6; JawOpen = energy * 0.4;
        MouthSmileLeft = energy * 0.3; MouthSmileRight = energy * 0.3;
      } else {
        JawOpen = energy * 0.2;
        if (hfRatio > 1.5) { ih = energy * 0.5; MouthSmileLeft = energy * 0.4; MouthSmileRight = energy * 0.4; }
        else { ou = energy * 0.5; MouthFunnel = energy * 0.4; }
      }
      timeline.push({t, aa, ih, ou, ee, oh, JawOpen, MouthFunnel, MouthSmileLeft, MouthSmileRight});
    }

    // Smooth
    const keys = ['aa','ih','ou','ee','oh','JawOpen','MouthFunnel','MouthSmileLeft','MouthSmileRight'];
    for (let pass = 0; pass < 2; pass++) {
      for (let i = 1; i < timeline.length - 1; i++) {
        for (const k of keys) timeline[i][k] = (timeline[i-1][k] + timeline[i][k] + timeline[i+1][k]) / 3;
      }
    }
    this._visemeTimeline = timeline;
  }

  playLipSync(audio) {
    if (!this._visemeTimeline) return;
    this._lipSyncActive = true;
    const iv = setInterval(() => {
      if (audio.paused || audio.ended) {
        for (const k in this._visT) this._visT[k] = 0;
        setTimeout(() => { this._lipSyncActive = false; this._visemeTimeline = null; }, 200);
        clearInterval(iv);
        return;
      }
      const v = this._lookupViseme(audio.currentTime);
      for (const k in this._visT) this._visT[k] = v[k] || 0;
    }, 16);
  }

  _lookupViseme(t) {
    const tl = this._visemeTimeline;
    const Z = {aa:0,ih:0,ou:0,ee:0,oh:0,JawOpen:0,MouthFunnel:0,MouthSmileLeft:0,MouthSmileRight:0};
    if (!tl || !tl.length) return Z;
    if (t <= tl[0].t) return tl[0];
    if (t >= tl[tl.length-1].t) return Z;
    let lo = 0, hi = tl.length - 1;
    while (lo < hi - 1) { const mid = (lo + hi) >> 1; if (tl[mid].t <= t) lo = mid; else hi = mid; }
    const a = tl[lo], b = tl[hi];
    const frac = (t - a.t) / Math.max(0.001, b.t - a.t);
    const keys = ['aa','ih','ou','ee','oh','JawOpen','MouthFunnel','MouthSmileLeft','MouthSmileRight'];
    const r = {t};
    for (const k of keys) r[k] = a[k] + (b[k] - a[k]) * frac;
    return r;
  }

  // ── Meshes (clothing toggle) ──
  getMeshes() { return this._meshes.map(m => ({ name: m.name, visible: m.mesh.visible })); }
  toggleMesh(name) {
    const m = this._meshes.find(m => m.name === name);
    if (m) { m.mesh.visible = !m.mesh.visible; return m.mesh.visible; }
    return null;
  }

  // ── Simple mouth (fallback when no viseme) ──
  setMouthOpen(v) { this._mouthTarget = Math.max(0, Math.min(1, v)); }

  // ── Emotion ──
  updateEmotion(emotion) {
    if (!this.vrm?.expressionManager) return;
    const emo = getEmo(emotion);
    if (emo === this._lastEmo) return;
    this._lastEmo = emo;
    const m = this.vrm.expressionManager;
    for (const n of ['happy','angry','sad','relaxed','Kirakira','Cheek','BrowInnerUp','BrowOuterUpLeft','BrowOuterUpRight','EyeWideLeft','EyeWideRight','EyeBlinkLeft','EyeBlinkRight']) {
      try { m.setValue(n, 0); } catch (_) {}
    }
    switch (emo) {
      case 'happy': m.setValue('happy', 0.7); break;
      case 'excited': m.setValue('happy', 0.9); m.setValue('Kirakira', 0.5); break;
      case 'sad': m.setValue('sad', 0.6); break;
      case 'worried': m.setValue('sad', 0.3); m.setValue('BrowInnerUp', 0.5); break;
      case 'angry': m.setValue('angry', 0.7); break;
      case 'curious': m.setValue('BrowOuterUpLeft', 0.5); m.setValue('BrowOuterUpRight', 0.5); m.setValue('EyeWideLeft', 0.3); m.setValue('EyeWideRight', 0.3); break;
      case 'sleepy': m.setValue('relaxed', 0.5); m.setValue('EyeBlinkLeft', 0.4); m.setValue('EyeBlinkRight', 0.4); break;
    }
  }

  pause() { this._paused = true; if (this._animId) { cancelAnimationFrame(this._animId); this._animId = null; } }
  resume() {
    if (!this._paused) return;
    this._paused = false;
    this.clock.getDelta();
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (w > 0 && h > 0) { this.camera.aspect = w / h; this.camera.updateProjectionMatrix(); this.renderer.setSize(w, h); }
    this._animate();
  }

  destroy() {
    this.pause();
    if (this._resizeObs) this._resizeObs.disconnect();
    document.removeEventListener('mousemove', this._onMouse);
    if (this.vrm) { try { VRMUtils.deepDispose(this.vrm.scene); this.scene.remove(this.vrm.scene); } catch (_) {} }
    if (this.renderer) this.renderer.dispose();
    this.vrm = null;
  }
}

function getEmo(e) {
  if (!e) return 'neutral';
  if (e.concern > .6) return 'sad'; if (e.curiosity > .7) return 'curious';
  if (e.engagement > .7 && e.confidence > .6) return 'excited';
  if (e.engagement > .6) return 'happy'; if (e.confidence > .7) return 'confident';
  if (e.concern > .4) return 'worried'; return 'neutral';
}
