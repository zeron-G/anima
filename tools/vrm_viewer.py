"""Minimal VRM 3D viewer — standalone test, no ANIMA dependencies.

Usage: python tools/vrm_viewer.py
Opens browser at http://localhost:8888
"""

import http.server
import os
import threading
import webbrowser
from pathlib import Path

PORT = 8888
ROOT = Path(__file__).parent.parent

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>VRM Viewer Test</title>
<style>
* { margin:0; padding:0; }
body { background:#111; color:#fff; font-family:monospace; overflow:hidden; }
canvas { display:block; width:100vw; height:100vh; }
#log { position:fixed; top:10px; left:10px; font-size:12px; color:#0f0; z-index:10; white-space:pre-wrap; max-width:50vw; }
#status { position:fixed; bottom:10px; left:10px; font-size:14px; color:#ff0; z-index:10; }
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="log"></div>
<div id="status">Loading Three.js...</div>

<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.min.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/",
    "@pixiv/three-vrm": "https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@3.3.3/lib/three-vrm.module.min.js"
  }
}
</script>

<script type="module">
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');

function log(msg) {
  const t = new Date().toLocaleTimeString('en', {hour12:false});
  logEl.textContent += t + ' ' + msg + '\\n';
  console.log(msg);
}

function status(msg) { statusEl.textContent = msg; }

// ── Setup ──
log('Three.js loaded: ' + THREE.REVISION);
status('Setting up renderer...');

const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({
  canvas,
  antialias: true,
  alpha: false,
  powerPreference: 'high-performance',
});
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x111111);

log('Renderer: ' + renderer.getContext().getParameter(renderer.getContext().RENDERER));
log('GPU: ' + (renderer.getContext().getExtension('WEBGL_debug_renderer_info')
  ? renderer.getContext().getParameter(renderer.getContext().getExtension('WEBGL_debug_renderer_info').UNMASKED_RENDERER_WEBGL)
  : 'unknown'));

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30, window.innerWidth / window.innerHeight, 0.1, 100);
camera.position.set(0, 1.3, 3);

// Orbit controls for debugging
const controls = new OrbitControls(camera, canvas);
controls.target.set(0, 1, 0);
controls.update();

// Lights
scene.add(new THREE.AmbientLight(0xffffff, 0.7));
const dLight = new THREE.DirectionalLight(0xffffff, 1);
dLight.position.set(2, 3, 2);
scene.add(dLight);

// Grid helper for reference
scene.add(new THREE.GridHelper(10, 10, 0x333333, 0x222222));

status('Loading three-vrm plugin...');

// ── Load VRM ──
let VRMLoaderPlugin, VRMUtils;
try {
  const mod = await import('@pixiv/three-vrm');
  VRMLoaderPlugin = mod.VRMLoaderPlugin;
  VRMUtils = mod.VRMUtils;
  log('three-vrm loaded OK');
} catch (e) {
  log('ERROR loading three-vrm: ' + e.message);
  status('FAILED: ' + e.message);
  throw e;
}

status('Downloading VRM model (86MB)...');

const loader = new GLTFLoader();
loader.register((parser) => new VRMLoaderPlugin(parser));

const MODEL = '/model/Flare.vrm';

loader.load(
  MODEL,
  (gltf) => {
    log('GLTF download complete');
    status('Processing VRM...');

    const vrm = gltf.userData.vrm;
    if (!vrm) {
      log('ERROR: No VRM data in gltf.userData');
      status('FAILED: No VRM data');
      return;
    }

    log('VRM version: ' + (vrm.meta?.metaVersion || 'unknown'));
    log('VRM name: ' + (vrm.meta?.name || 'unknown'));

    try { VRMUtils.removeUnnecessaryVertices(gltf.scene); log('removeUnnecessaryVertices OK'); } catch (e) { log('removeVertices failed: ' + e.message); }
    try { VRMUtils.removeUnnecessaryJoints(gltf.scene); log('removeUnnecessaryJoints OK'); } catch (e) { log('removeJoints failed: ' + e.message); }
    try { VRMUtils.rotateVRM0(vrm); log('rotateVRM0 OK'); } catch (e) { log('rotateVRM0 failed (might be VRM1): ' + e.message); }

    scene.add(vrm.scene);
    log('Model added to scene');
    status('VRM loaded OK! Use mouse to orbit.');

    // Animate
    const clock = new THREE.Clock();
    function animate() {
      requestAnimationFrame(animate);
      const dt = clock.getDelta();
      vrm.update(dt);
      controls.update();
      renderer.render(scene, camera);
    }
    animate();
    log('Render loop running');
  },
  (progress) => {
    if (progress.total > 0) {
      const pct = Math.round(progress.loaded / progress.total * 100);
      const mb = (progress.loaded / 1e6).toFixed(1);
      status('Downloading: ' + pct + '% (' + mb + 'MB)');
      if (pct % 25 === 0) log('Download: ' + pct + '%');
    } else {
      status('Downloading: ' + (progress.loaded / 1e6).toFixed(1) + 'MB...');
    }
  },
  (error) => {
    log('ERROR loading model: ' + error.message);
    status('FAILED: ' + error.message);
  }
);

// Render empty scene while model loads
function renderEmpty() {
  controls.update();
  renderer.render(scene, camera);
  if (!scene.children.some(c => c.isGroup || c.isMesh)) requestAnimationFrame(renderEmpty);
}
renderEmpty();

// Resize
window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
</script>
</body>
</html>
"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path == "/model/Flare.vrm":
            vrm_path = ROOT / "anima" / "desktop" / "frontend" / "model" / "flare" / "Flare.vrm"
            if vrm_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "model/gltf-binary")
                self.send_header("Content-Length", str(vrm_path.stat().st_size))
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                with open(vrm_path, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
            else:
                self.send_error(404, f"VRM not found: {vrm_path}")
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        if "/model/" in args[0]:
            print(f"[server] {args[0]}")


def main():
    os.chdir(str(ROOT))
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"VRM Viewer: http://localhost:{PORT}")
    print(f"Model: {ROOT / 'anima/desktop/frontend/model/flare/Flare.vrm'}")
    print("Press Ctrl+C to stop\n")

    threading.Timer(1, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
