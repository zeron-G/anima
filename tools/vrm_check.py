"""Check VRM model expressions — saves results to vrm_report.txt"""
import http.server, threading, webbrowser, json
from pathlib import Path

REPORT = Path(__file__).parent / 'vrm_report.txt'

HTML = r"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>VRM Check</title>
<style>*{margin:0;padding:0}body{background:#111;color:#0f0;font:13px/1.8 monospace;padding:20px;width:45vw}
canvas{position:fixed;right:0;top:0;width:50vw;height:100vh}#out{white-space:pre-wrap}</style></head><body>
<div id="out">Loading...</div><canvas id="c"></canvas>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.min.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/","@pixiv/three-vrm":"https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@3.3.3/lib/three-vrm.module.min.js"}}</script>
<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const out = document.getElementById('out');
const lines = [];
function log(s) { lines.push(s); out.textContent = lines.join('\n'); }

const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.setSize(canvas.clientWidth, canvas.clientHeight);
renderer.setClearColor(0x111111);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30, canvas.clientWidth/canvas.clientHeight, 0.1, 100);
camera.position.set(0, 1.3, 3);
const controls = new OrbitControls(camera, canvas);
controls.target.set(0, 1, 0); controls.update();
scene.add(new THREE.AmbientLight(0xffffff, 0.7));
const dl = new THREE.DirectionalLight(0xffffff, 1); dl.position.set(2,3,2); scene.add(dl);

out.textContent = 'Downloading model...';
const loader = new GLTFLoader();
loader.register(p => new VRMLoaderPlugin(p));
loader.load('/model/Flare.vrm', (gltf) => {
  const vrm = gltf.userData.vrm;
  lines.length = 0;
  log('=== VRM MODEL INFO ===');
  log('Meta name: ' + (vrm.meta?.name || vrm.meta?.title || 'N/A'));
  log('Meta version: ' + (vrm.meta?.metaVersion || vrm.meta?.specVersion || 'N/A'));

  log('\n=== EXPRESSION MANAGER ===');
  const mgr = vrm.expressionManager;
  if (!mgr) { log('NO expressionManager!'); }
  else {
    log('Type: ' + mgr.constructor.name);
    log('Own keys: ' + Object.keys(mgr).join(', '));
    log('Proto methods: ' + Object.getOwnPropertyNames(Object.getPrototypeOf(mgr)).join(', '));

    // List all expressions by iterating
    if (mgr.expressions && Array.isArray(mgr.expressions)) {
      log('\nexpressions array (' + mgr.expressions.length + '):');
      mgr.expressions.forEach((e, i) => {
        log('  [' + i + '] name=' + (e.expressionName || e.name || '?') + ' isBinary=' + !!e.isBinary);
      });
    }

    // Test setting values
    const testNames = [
      'happy','angry','sad','relaxed','surprised','neutral',
      'blink','blinkLeft','blinkRight',
      'aa','ih','ou','ee','oh',
      'fun','joy','sorrow',
      'a','i','u','e','o',
      'lookUp','lookDown','lookLeft','lookRight',
    ];
    log('\n=== EXPRESSION SET TEST ===');
    for (const n of testNames) {
      try {
        mgr.setValue(n, 0.8);
        const v = mgr.getValue(n);
        mgr.setValue(n, 0);
        log('  "' + n + '" -> SET OK (readback=' + v + ')');
      } catch(e) {
        // not available
      }
    }
  }

  log('\n=== LOOKAT ===');
  if (vrm.lookAt) {
    log('exists: true');
    log('constructor: ' + vrm.lookAt.constructor.name);
    log('has .target property: ' + ('target' in vrm.lookAt));
    log('typeof .target: ' + typeof vrm.lookAt.target);
    log('own keys: ' + Object.keys(vrm.lookAt).join(', '));
    log('proto: ' + Object.getOwnPropertyNames(Object.getPrototypeOf(vrm.lookAt)).join(', '));
  } else { log('No lookAt'); }

  log('\n=== HUMANOID BONES ===');
  if (vrm.humanoid) {
    const names = Object.keys(vrm.humanoid.humanBones || {});
    log('Count: ' + names.length);
    log(names.join(', '));
  }

  // Send report to server
  fetch('/report', {method:'POST', headers:{'Content-Type':'text/plain'}, body: lines.join('\n')});

  VRMUtils.rotateVRM0(vrm);
  scene.add(vrm.scene);
  const clock = new THREE.Clock();
  (function anim() { requestAnimationFrame(anim); vrm.update(clock.getDelta()); controls.update(); renderer.render(scene, camera); })();
}, p => { if(p.total) out.textContent = 'Downloading: '+Math.round(p.loaded/p.total*100)+'%'; });
</script></body></html>"""

class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/','/index.html'):
            self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(HTML.encode())
        elif self.path == '/model/Flare.vrm':
            p = Path(__file__).parent.parent / 'anima/desktop/frontend/model/flare/Flare.vrm'
            self.send_response(200); self.send_header('Content-Type','model/gltf-binary'); self.send_header('Content-Length',str(p.stat().st_size)); self.end_headers()
            with open(p,'rb') as f:
                while c:=f.read(65536): self.wfile.write(c)
        else: self.send_error(404)
    def do_POST(self):
        if self.path == '/report':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            REPORT.write_text(body, encoding='utf-8')
            print(f'\n=== REPORT SAVED to {REPORT} ===\n')
            print(body)
            self.send_response(200); self.send_header('Content-Type','text/plain'); self.end_headers(); self.wfile.write(b'ok')
        else: self.send_error(404)
    def log_message(self,*a): pass

s = http.server.HTTPServer(('127.0.0.1',8889),H)
print(f'VRM Check: http://localhost:8889')
print(f'Report will be saved to: {REPORT}')
threading.Timer(1, lambda: webbrowser.open('http://localhost:8889')).start()
try: s.serve_forever()
except KeyboardInterrupt: pass
