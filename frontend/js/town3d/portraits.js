// Resident portraits for the UI (live column, inspector, chips): each 3-D figure's head
// and shoulders, rendered once into a small image with a throwaway renderer.
import {reactive} from 'vue';

export const PORTRAIT = reactive({});
let started = false;
export function ensurePortraits() {
  if (started) return; started = true;
  setTimeout(() => render().catch(e => console.warn('portraits', e)), 400);
}
async function render() {
  const THREE = await import('three');
  const {Figure} = await import('./figure.js');
  const FS = await (await fetch('/static/js/town3d/residents.json')).json();
  const S = 128, r = new THREE.WebGLRenderer({alpha: true, antialias: true, preserveDrawingBuffer: true});
  r.setSize(S, S); r.setPixelRatio(1); r.outputColorSpace = THREE.SRGBColorSpace; r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = 1.0;
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xe8f2f6, 0xc8b8a0, 1.6));
  const key = new THREE.DirectionalLight(0xfff4e6, 2.2); key.position.set(2, 4, 5); scene.add(key);
  const cam = new THREE.PerspectiveCamera(24, 1, 0.1, 50);
  const css = getComputedStyle(document.documentElement);
  for (const id of Object.keys(FS.residents)) {
    const f = new Figure(FS.residents[id], FS, css.getPropertyValue(`--r-${id}`).trim() || '#888');
    f.seed = 0; f.t = 0; f.pose('idle', 0.001); f.head.rotation.set(-f.stoop * 0.6, 0.18, 0);
    scene.add(f.root); f.root.updateMatrixWorld(true);
    const h = new THREE.Vector3(); f.head.getWorldPosition(h);
    cam.position.set(h.x + 0.55, h.y + 0.15, h.z + 2.6); cam.lookAt(h.x, h.y - 0.12, h.z);
    r.render(scene, cam);
    PORTRAIT[id] = r.domElement.toDataURL('image/png');
    scene.remove(f.root);
  }
  r.dispose(); r.forceContextLoss();
}
