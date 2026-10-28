/**
 * RxTrail — Voice module: STT (speech-to-text) + TTS (text-to-speech).
 * Uses browser-native Web Speech API. No external dependencies.
 *
 * STT: SpeechRecognition (el-GR) — for voice intake, visit notes
 * TTS: SpeechSynthesis (el-GR) — for reading queue items, brief, insights
 */

// ── STT: Speech-to-Text ────────────────────────────────────────────────────

/**
 * Start listening for Greek speech. Returns a Promise that resolves with the transcript.
 * Also returns an abort function so the caller can stop listening manually.
 * 
 * @param {number} timeoutMs — max wait time before auto-stopping
 * @returns {{ promise: Promise<string>, abort: Function }}
 */
export function listenGreek(timeoutMs = 60000) {
  let rec = null;
  let timeout = null;
  let settled = false;

  const promise = new Promise((resolve, reject) => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      reject(new Error('To mic den ypostirizetai'));
      return;
    }

    // Try server-side Whisper first (more accurate)
    let mediaRecorder = null;
    let audioChunks = [];

    navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
      // Start Web Speech API as primary (fast, low latency)
      rec = new SpeechRecognition();
      rec.lang = 'el-GR';
      rec.interimResults = true;
      rec.continuous = false;
      rec.maxAlternatives = 1;

      let lastInterim = '';

      // Also start recording for Whisper fallback (server-side, more accurate)
      try {
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
        mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.start();
      } catch (e) { /* WebM not supported, skip recording */ }

      timeout = setTimeout(async () => {
        if (settled) return;
        settled = true;
        rec.stop();
        if (mediaRecorder) mediaRecorder.stop();
        // Try Whisper server-side first
        if (audioChunks.length > 0) {
          try {
            const token = localStorage.getItem('rxtrail_token');
            const blob = new Blob(audioChunks, { type: 'audio/webm' });
            const resp = await fetch('/api/llm/stt', {
              method: 'POST', headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'audio/webm' },
              body: blob,
            });
            const data = await resp.json();
            if (data.text && data.text.trim()) {
              stream.getTracks().forEach(t => t.stop());
              resolve(data.text);
              return;
            }
          } catch (whisperErr) { /* fallback to Web Speech */ }
        }
        if (lastInterim) resolve(lastInterim);
        else reject(new Error('Den akoustike omilia'));
      }, timeoutMs);

      rec.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        lastInterim = transcript;
        if (event.results[0].isFinal) {
          clearTimeout(timeout);
          if (settled) return;
          settled = true;
          rec.stop();
          if (mediaRecorder) mediaRecorder.stop();
          stream.getTracks().forEach(t => t.stop());
          resolve(transcript);
        }
      };

      rec.onerror = (event) => {
        clearTimeout(timeout);
        if (settled) return;
        settled = true;
        if (mediaRecorder) mediaRecorder.stop();
        // Try Whisper on Web Speech error
        if (audioChunks.length > 0) {
          resolveViaWhisper(audioChunks, stream, resolve);
          return;
        }
        const messages = {
          'not-allowed': 'Den epitrepetai to mikrofono',
          'no-speech': 'Den akoustike omilia',
          'audio-capture': 'Den vrethike mikrofono',
          'network': 'Sfalma diktyou',
        };
        reject(new Error(messages[event.error] || 'Sfalma mikrofwnou'));
      };

      rec.start();
    }).catch(reject);
  });

  const abort = () => {
    if (settled) return;
    settled = true;
    clearTimeout(timeout);
    if (rec) try { rec.stop(); } catch (e) { }
  };

  return { promise, abort };
}

async function resolveViaWhisper(chunks, stream, resolve) {
  try {
    const token = localStorage.getItem('rxtrail_token');
    const blob = new Blob(chunks, { type: 'audio/webm' });
    const resp = await fetch('/api/llm/stt', {
      method: 'POST', headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'audio/webm' },
      body: blob,
    });
    const data = await resp.json();
    if (data.text && data.text.trim()) {
      stream.getTracks().forEach(t => t.stop());
      resolve(data.text);
      return;
    }
  } catch (e) { }
  reject(new Error('Den akoustike omilia'));
}

/**
 * Quick voice dictation: fill a textarea with spoken text.
 * @param {HTMLTextAreaElement|HTMLInputElement} targetEl — the element to fill
 * @param {HTMLElement} statusEl — optional status indicator
 * @returns {{ promise: Promise<void>, abort: Function }}
 */
export function dictationFill(targetEl, statusEl = null) {
  if (statusEl) statusEl.textContent = '🎤 Ακούω... (πάτα Stop για να σταματήσεις)';
  const { promise, abort } = listenGreek(30000);
  
  const wrappedPromise = promise.then(text => {
    targetEl.value = (targetEl.value ? targetEl.value + ' ' : '') + text;
    if (statusEl) statusEl.textContent = '✅ Έτοιμο';
    setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 1500);
  }).catch(err => {
    if (statusEl) statusEl.textContent = '❌ ' + err.message;
    setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 3000);
  });

  return { promise: wrappedPromise, abort };
}


// ── TTS: Text-to-Speech ────────────────────────────────────────────────────

let _speaking = false;
let _utterance = null;
let _voicesLoaded = false;
let _cachedGreekVoice = null;

/**
 * Ensure voices are loaded (Chrome loads them async).
 */
function _ensureVoices() {
  if (_voicesLoaded && _cachedGreekVoice) return;
  
  const voices = window.speechSynthesis.getVoices();
  if (voices.length > 0) {
    _voicesLoaded = true;
    // Prefer Greek voice, fallback to any el-* variant
    _cachedGreekVoice = voices.find(v => v.lang === 'el-GR') 
                     || voices.find(v => v.lang.startsWith('el'))
                     || null;
  }
}

// Listen for async voice loading (critical on Chrome Android)
if ('speechSynthesis' in window) {
  window.speechSynthesis.onvoiceschanged = () => {
    _ensureVoices();
  };
  // Also try immediately (some browsers have them cached)
  _ensureVoices();
}

/**
 * Speak Greek text aloud. Automatically chunks long text to avoid
 * Chrome's ~15s speech timeout. Voice-first design for driving.
 */
export function speakGreek(text, rate = 1.0, pitch = 1.0) {
  // For very long text, chunk at sentence boundaries to avoid Chrome timeout
  const MAX_CHUNK = 300; // chars per chunk — keeps each utterance under ~15s
  
  if (text.length <= MAX_CHUNK) {
    return _speakChunk(text, rate, pitch);
  }
  
  // Split at sentence boundaries (., !, ?, newline)
  return _speakInChunks(text, rate, pitch, MAX_CHUNK);
}

async function _speakInChunks(text, rate, pitch, maxChunk) {
  const sentences = text.split(/(?<=[.!?\n])\s+/);
  let currentChunk = '';
  
  for (const sentence of sentences) {
    if (currentChunk.length + sentence.length > maxChunk && currentChunk.length > 0) {
      await _speakChunk(currentChunk.trim(), rate, pitch);
      currentChunk = sentence;
    } else {
      currentChunk += (currentChunk ? ' ' : '') + sentence;
    }
  }
  
  if (currentChunk.trim()) {
    await _speakChunk(currentChunk.trim(), rate, pitch);
  }
}

function _speakChunk(text, rate, pitch) {
  return new Promise((resolve) => {
    if (!('speechSynthesis' in window)) {
      console.warn('TTS not supported');
      resolve();
      return;
    }

    // Stop any current speech
    stopSpeaking();

    // Ensure voices are ready
    _ensureVoices();

    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = 'el-GR';
    utter.rate = rate;
    utter.pitch = pitch;

    // Use cached Greek voice if available
    if (_cachedGreekVoice) {
      utter.voice = _cachedGreekVoice;
      console.log('TTS: using voice', _cachedGreekVoice.name, _cachedGreekVoice.lang);
    } else {
      console.warn('TTS: no Greek voice found, using default. Available voices:', 
        window.speechSynthesis.getVoices().map(v => v.lang).join(', '));
    }

    utter.onstart = () => {
      _speaking = true;
      _utterance = utter;
      console.log('TTS: speaking', text.substring(0, 60) + '...');
    };

    utter.onend = () => {
      _speaking = false;
      _utterance = null;
      console.log('TTS: done');
      resolve();
    };

    utter.onerror = (e) => {
      _speaking = false;
      _utterance = null;
      console.error('TTS error:', e.error, e.message);
      // Retry once without specific voice (sometimes the voice is the problem)
      if (_cachedGreekVoice && e.error === 'synthesis-failed') {
        console.warn('TTS: retrying without Greek voice');
        _cachedGreekVoice = null;
        utter.voice = null;
        window.speechSynthesis.speak(utter);
        // Don't resolve yet — let the retry's onend handle it
        _speaking = true;
        _utterance = utter;
        return;
      }
      resolve();
    };

    window.speechSynthesis.speak(utter);
  });
}

/**
 * Stop any currently playing speech.
 */
export function stopSpeaking() {
  if (_speaking) {
    window.speechSynthesis.cancel();
    _speaking = false;
    _utterance = null;
  }
}

/**
 * Check if speech is currently playing.
 */
export function isSpeaking() {
  return _speaking;
}

/**
 * Speak a brief summary (shortened for voice).
 * Truncates long text to ~150 chars for TTS.
 */
export async function speakBrief(text, label = '') {
  const short = text.length > 200 ? text.substring(0, 197) + '...' : text;
  const toSpeak = label ? `${label}. ${short}` : short;
  await speakGreek(toSpeak, 1.1);
}


// ── Preload voices (call on app init) ──────────────────────────────────────

export function preloadVoices() {
  if ('speechSynthesis' in window) {
    // Trigger voice loading
    window.speechSynthesis.getVoices();
    window.speechSynthesis.onvoiceschanged = () => {
      window.speechSynthesis.getVoices();
    };
  }
}


// ── Mic button component ───────────────────────────────────────────────────

/**
 * Create a mic button element that fills a target on click.
 * @param {HTMLElement} targetEl — textarea or input to fill
 * @returns {HTMLButtonElement}
 */
export function createMicButton(targetEl) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'icon-btn';
  btn.title = 'Μικρόφωνο (ομιλία σε κείμενο)';
  btn.textContent = '🎤';
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = '🔴';
    await dictationFill(targetEl, btn);
    btn.disabled = false;
    btn.textContent = '🎤';
  });
  return btn;
}

/**
 * Create a speaker button that reads text aloud.
 * @param {string} text — text to speak
 * @param {string} label — optional label prefix
 * @returns {HTMLButtonElement}
 */
export function createSpeakerButton(text, label = '') {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'icon-btn';
  btn.title = 'Ανάγνωση';
  btn.textContent = '🔊';
  btn.addEventListener('click', async () => {
    if (isSpeaking()) { stopSpeaking(); btn.textContent = '🔊'; return; }
    btn.textContent = '⏹';
    await speakBrief(text, label);
    btn.textContent = '🔊';
  });
  return btn;
}
