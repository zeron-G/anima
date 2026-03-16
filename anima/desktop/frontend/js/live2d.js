/**
 * Live2D Avatar — PIXI.js + pixi-live2d-display
 * Canvas is always in DOM and visible (behind VRM). Never destroyed.
 */

const MODEL_URL = '/static/model/PurpleBird/PurpleBird.model3.json';

const EMOTION_MAP = {
  happy:       { expression: null,     params: { ParamEyeLSmile: 0.7, ParamEyeRSmile: 0.7, ParamMouthForm: 0.6 }},
  excited:     { expression: '星星眼', params: { ParamEyeLSmile: 0.5, ParamEyeRSmile: 0.5, ParamMouthForm: 0.8 }},
  sad:         { expression: 'QAQ',    params: { ParamBrowLY: -0.4, ParamBrowRY: -0.4, ParamMouthForm: -0.2 }},
  angry:       { expression: '生气',   params: { Param8: 0.8, ParamBrowLAngle: -0.5, ParamBrowRAngle: -0.5 }},
  curious:     { expression: '问号',   params: { ParamAngleZ: 8.0, ParamBrowLY: 0.3, ParamBrowRY: 0.3 }},
  sleepy:      { expression: null,     params: { ParamEyeLOpen: 0.3, ParamEyeROpen: 0.3 }},
  neutral:     { expression: null,     params: {}},
};

export class Live2DAvatar {
  constructor(canvas, container) {
    this.canvas = canvas;
    this.container = container;
    this.app = null;
    this.model = null;
    this._mouthOpen = 0;
    this._lastEmo = '';
    this._resizeObs = null;
    this._boundMouse = (e) => { if (this.model) this.model.focus(e.clientX, e.clientY); };
  }

  async init() {
    // Wait for PIXI + Cubism plugin
    await new Promise(resolve => {
      const check = () => (typeof PIXI !== 'undefined' && PIXI.live2d) ? resolve() : setTimeout(check, 100);
      check();
    });

    console.log('Live2D: creating PIXI app...');

    // Canvas is always in DOM and has size (position:absolute, inset:0)
    this.app = new PIXI.Application({
      view: this.canvas,
      autoStart: true,
      backgroundColor: 0x0a0a0a,   // Opaque background, match app
      backgroundAlpha: 1,
      resizeTo: this.container,
    });

    console.log('Live2D: loading model...');
    const model = await PIXI.live2d.Live2DModel.from(MODEL_URL, { autoInteract: false });

    this.app.stage.addChild(model);
    this.model = model;

    const reposition = () => {
      const w = this.container.clientWidth, h = this.container.clientHeight;
      const mw = model.internalModel?.originalWidth || model.width;
      const mh = model.internalModel?.originalHeight || model.height;
      if (!mw || !mh || !w || !h) return;
      const scale = Math.min((h * .95) / mh, (w * .95) / mw) * 2.2;
      model.scale.set(scale);
      model.anchor.set(0.5, 0.5);
      model.x = w / 2;
      model.y = h;
    };
    reposition();

    this._resizeObs = new ResizeObserver(reposition);
    this._resizeObs.observe(this.container);
    document.addEventListener('mousemove', this._boundMouse);

    // Mouth sync
    this.app.ticker.add(() => {
      try {
        const cm = model.internalModel?.coreModel;
        if (cm) cm.setParameterValueById('ParamMouthOpenY', this._mouthOpen);
      } catch (_) {}
    });

    // Start paused — VRM is default front
    this.app.stop();
    console.log('Live2D: ready (paused)');
  }

  setMouthOpen(v) { this._mouthOpen = Math.max(0, Math.min(1, v)); }

  updateEmotion(emotion) {
    if (!this.model) return;
    const emo = getEmo(emotion);
    if (emo === this._lastEmo) return;
    this._lastEmo = emo;
    const mapping = EMOTION_MAP[emo] || EMOTION_MAP.neutral;
    try {
      if (mapping.expression) {
        const em = this.model.internalModel?.motionManager?.expressionManager;
        if (em) em.setExpression(mapping.expression);
      }
      const cm = this.model.internalModel?.coreModel;
      if (cm) {
        for (const p in mapping.params) {
          try { cm.setParameterValueById(p, mapping.params[p]); } catch (_) {}
        }
      }
    } catch (_) {}
  }

  destroy() {
    if (this._resizeObs) this._resizeObs.disconnect();
    document.removeEventListener('mousemove', this._boundMouse);
    if (this.app) { this.app.stop(); this.app.destroy(false); }
    this.model = null; this.app = null;
  }
}

function getEmo(e) {
  if (!e) return 'neutral';
  if (e.concern > .6) return 'sad';
  if (e.curiosity > .7) return 'curious';
  if (e.engagement > .7 && e.confidence > .6) return 'excited';
  if (e.engagement > .6) return 'happy';
  if (e.confidence > .7) return 'confident';
  if (e.concern > .4) return 'worried';
  return 'neutral';
}
