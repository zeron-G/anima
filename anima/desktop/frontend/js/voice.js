/**
 * Voice Manager — TTS with viseme lip sync + STT
 *
 * Uses VRMAvatar.startLipSync(url) to pre-analyze audio,
 * then VRMAvatar.playLipSync(audio) for synced playback.
 * Falls back to simple setMouthOpen for Live2D mode.
 */

import { setMouthOpen } from './app.js';

// Access to VRM avatar for viseme sync — set by app.js
let _getVrmAvatar = null;
export function setVrmAvatarGetter(fn) { _getVrmAvatar = fn; }

export class VoiceManager {
  constructor() {
    this.autoTTS = true;
    this.isRecording = false;
    this._mediaStream = null;
    this._recorder = null;
    this._chunks = [];
    this._audio = null;
    this._mouthInterval = null;
  }

  init() {
    const ttsBtn = document.getElementById('btn-tts');
    const micBtn = document.getElementById('btn-mic');

    ttsBtn.addEventListener('click', () => {
      this.autoTTS = !this.autoTTS;
      ttsBtn.classList.toggle('tts-active', this.autoTTS);
    });

    micBtn.addEventListener('mousedown', () => this.startRec());
    micBtn.addEventListener('mouseup', () => this.stopRec());
    micBtn.addEventListener('mouseleave', () => { if (this.isRecording) this.stopRec(); });
  }

  async playUrl(url) {
    this._stopPlayback();
    try {
      const audio = new Audio(url + '?t=' + Date.now());
      this._audio = audio;

      const vrm = _getVrmAvatar ? _getVrmAvatar() : null;
      if (vrm && vrm.startLipSync) {
        try {
          await vrm.startLipSync(url);
          audio.addEventListener('play', () => vrm.playLipSync(audio));
        } catch (_) {}
      } else {
        audio.addEventListener('play', () => this._startSimpleMouth(audio));
      }

      audio.addEventListener('ended', () => this._stopPlayback());
      audio.addEventListener('error', () => this._stopPlayback());
      await audio.play();
    } catch (e) { console.warn('TTS playUrl error:', e); }
  }

  async speak(text) {
    this._stopPlayback();

    try {
      const r = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const d = await r.json();
      if (!d.ok || !d.url) return;

      const audioUrl = d.url + '?t=' + Date.now(); // cache-bust
      const audio = new Audio(audioUrl);
      this._audio = audio;

      // Try viseme lip sync (VRM mode)
      const vrm = _getVrmAvatar ? _getVrmAvatar() : null;
      if (vrm && vrm.startLipSync) {
        try {
          await vrm.startLipSync(d.url); // original URL for fetch (no cache-bust needed for analysis)
          audio.addEventListener('play', () => vrm.playLipSync(audio));
          audio.addEventListener('ended', () => this._stopPlayback());
          audio.addEventListener('error', () => this._stopPlayback());
          await audio.play();
          return;
        } catch (e) {
          console.warn('Viseme sync failed, using fallback:', e);
        }
      }

      // Fallback: simple timer-based mouth for Live2D
      audio.addEventListener('play', () => this._startSimpleMouth(audio));
      audio.addEventListener('ended', () => this._stopPlayback());
      audio.addEventListener('pause', () => this._stopMouth());
      audio.addEventListener('error', () => this._stopPlayback());
      await audio.play();
    } catch (e) {
      console.warn('TTS error:', e);
    }
  }

  _stopPlayback() {
    this._stopMouth();
    if (this._audio) { this._audio.pause(); this._audio.src = ''; this._audio = null; }
    // Reset VRM viseme state
    const vrm = _getVrmAvatar ? _getVrmAvatar() : null;
    if (vrm) {
      vrm._lipSyncActive = false;
      vrm._visemeTimeline = null;
    }
  }

  _startSimpleMouth(audio) {
    this._stopMouth();
    let lastTime = 0;
    this._mouthInterval = setInterval(() => {
      if (!audio || audio.paused) { setMouthOpen(0); return; }
      const t = audio.currentTime;
      const delta = t - lastTime;
      lastTime = t;
      if (delta > 0) {
        setMouthOpen(Math.max(0, Math.min(1, Math.sin(t * 12) * 0.3 + Math.sin(t * 7.3) * 0.2 + 0.3)));
      } else {
        setMouthOpen(0);
      }
    }, 33);
  }

  _stopMouth() {
    if (this._mouthInterval) { clearInterval(this._mouthInterval); this._mouthInterval = null; }
    setMouthOpen(0);
  }

  // ── STT ──
  async startRec() {
    if (this.isRecording) return;
    try {
      this._mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this._recorder = new MediaRecorder(this._mediaStream);
      this._chunks = [];
      this._recorder.ondataavailable = (e) => { if (e.data.size > 0) this._chunks.push(e.data); };
      this._recorder.onstop = () => this._sendAudio();
      this._recorder.start();
      this.isRecording = true;
      document.getElementById('btn-mic').classList.add('recording');
    } catch (e) { console.warn('Mic denied:', e); }
  }

  stopRec() {
    if (!this.isRecording) return;
    document.getElementById('btn-mic').classList.remove('recording');
    this._recorder?.stop();
    this.isRecording = false;
    if (this._mediaStream) { this._mediaStream.getTracks().forEach(t => t.stop()); this._mediaStream = null; }
  }

  async _sendAudio() {
    try {
      const blob = new Blob(this._chunks, { type: 'audio/webm' });
      const fd = new FormData();
      fd.append('audio', blob, 'rec.webm');
      const r = await fetch('/api/stt', { method: 'POST', body: fd });
      const d = await r.json();
      if (d.text) {
        document.getElementById('chat-text').value = d.text;
        document.getElementById('btn-send').click();
      }
    } catch (e) { console.warn('STT error:', e); }
  }
}
