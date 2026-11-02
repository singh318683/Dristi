const puppeteer = require('puppeteer-core');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const person = [{id: 'p1', name: 'Test', relation: 'Self', gender: 'Female', date: '1990-06-15', time: '12:00', place: {name: 'Delhi, India', lat: 28.65195, lon: 77.23149, tz: 'Asia/Kolkata'}}];
const android = 'Mozilla/5.0 (Linux; Android 12; SM-G970F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36';

function snap() {
  const a = musicAnalyser;
  let peak = -Infinity;
  if (a && musicCtx) {
    const bins = a.frequencyBinCount;
    const data = new Float32Array(bins);
    a.getFloatFrequencyData(data);
    const hz = musicCtx.sampleRate / a.fftSize;
    for (let i = 0; i < bins; i++) {
      const f = i * hz;
      if (f >= 200 && f <= 1200 && data[i] > peak) peak = data[i];
    }
  }
  const out = document.getElementById('musicOut');
  const dbg = document.getElementById('audioDebug');
  return {
    state: musicCtx ? musicCtx.state : 'none',
    built: musicBuilt,
    playing: musicPlaying,
    on: musicOn(),
    level: musicLevel(),
    phone: musicPhone(),
    rms: musicRms(),
    peak,
    gain: musicHeardGain(),
    route: out ? 'element' : 'destination',
    paused: out ? out.paused : null,
    playsinline: out ? out.hasAttribute('playsinline') : false,
    srcObject: !!(out && out.srcObject),
    debug: dbg ? dbg.textContent : '',
  };
}

async function boot(page, {ua, debug, music} = {}) {
  if (ua) await page.setUserAgent(ua);
  const base = process.env.APP_URL || 'http://127.0.0.1:8792/';
  await page.goto(base + (debug ? '?audiodebug=1' : ''), {waitUntil: 'domcontentloaded'});
  await page.evaluate((people, musicFlag) => {
    localStorage.setItem('dr-profiles', JSON.stringify(people));
    localStorage.setItem('dr-active', JSON.stringify(people[0].id));
    localStorage.setItem('dr-lang', '"en"');
    if (musicFlag === false) localStorage.setItem('dr-music', 'false');
    else localStorage.removeItem('dr-music');
  }, person, music);
  await page.reload({waitUntil: 'domcontentloaded'});
  await page.waitForFunction(() => typeof musicUnlock === 'function' && document.querySelector('.home-greet, #gitaContinue'));
}

async function hideSplash(page) {
  await page.evaluate(() => {
    const s = document.getElementById('gita-splash');
    if (s) s.hidden = true;
    document.body.classList.remove('gita-lock');
  });
}

async function until(page, pred, label, ms = 2500) {
  const start = Date.now();
  let last = null;
  while (Date.now() - start < ms) {
    last = await page.evaluate(snap);
    if (pred(last)) return last;
    await sleep(100);
  }
  throw new Error(label + ' ' + JSON.stringify(last));
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--autoplay-policy=document-user-activation-required'],
  });
  const fails = [];
  const check = (name, ok, extra) => {
    if (!ok) fails.push(name + (extra ? ' ' + JSON.stringify(extra) : ''));
    else console.log('ok', name);
  };

  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.setViewport({width: 390, height: 844, isMobile: false, hasTouch: false});
    await boot(page);
    await hideSplash(page);
    const before = await page.evaluate(snap);
    check('desktop starts unmuted and silent', before.on === true && before.built === 0 && before.state === 'none', before);
    await page.click('.home-greet');
    const desk = await until(page, s => s.state === 'running' && s.rms > 0.001 && s.peak > -55, 'desktop tap');
    check('desktop context is running', desk.state === 'running', desk);
    check('desktop rms is audible', desk.rms > 0.001, desk);
    check('desktop mid band has energy', desk.peak > -55, desk);
    check('desktop stays on the destination at 0.25', desk.route === 'destination' && desk.level === 0.25 && desk.built === 1, desk);
    await page.evaluate(() => { go('kundli'); go('home'); });
    const switched = await page.evaluate(snap);
    check('one graph across tabs', switched.built === 1 && switched.state === 'running', switched);

    await page.evaluate(() => musicCtx.suspend());
    await sleep(200);
    const suspended = await page.evaluate(snap);
    check('suspend sticks until the next tap', suspended.state === 'suspended', suspended);
    await page.click('.home-greet');
    const resumed = await until(page, s => s.state === 'running' && s.rms > 0.001, 'desktop retry');
    check('later tap resumes a suspended context', resumed.state === 'running' && resumed.built === 1, resumed);

    const phone = await browser.newPage();
    phone.on('pageerror', e => errors.push('android ' + e));
    await phone.setViewport({width: 390, height: 844, isMobile: true, hasTouch: true});
    await boot(phone, {ua: android});
    await hideSplash(phone);
    await phone.tap('.home-greet');
    const and = await until(phone, s => s.state === 'running' && s.rms > 0.001 && s.peak > -55 && s.route === 'element', 'android tap', 4000);
    check('android context is running', and.state === 'running', and);
    check('android routes through a playing media element', and.route === 'element' && and.playsinline && and.srcObject && and.paused === false, and);
    check('android level is 0.55 and rms is audible', and.level === 0.55 && and.rms > 0.001 && and.peak > -55, and);
    check('android is detected as a phone', and.phone === true, and);

    const splash = await browser.newPage();
    splash.on('pageerror', e => errors.push('splash ' + e));
    await splash.setViewport({width: 390, height: 844, isMobile: true, hasTouch: true});
    await boot(splash, {ua: android});
    const splashUp = await splash.evaluate(() => {
      const s = document.getElementById('gita-splash');
      return !!(s && !s.hidden && document.getElementById('gitaContinue'));
    });
    check('splash is up before the tap', splashUp);
    await splash.tap('#gitaContinue');
    const spl = await until(splash, s => s.state === 'running' && s.rms > 0.001, 'splash tap');
    check('splash tap unlocks audio', spl.state === 'running' && spl.playing === true, spl);

    const hidden = await browser.newPage();
    hidden.on('pageerror', e => errors.push('hidden ' + e));
    await hidden.setViewport({width: 390, height: 844, isMobile: true, hasTouch: true});
    await boot(hidden, {ua: android});
    await hideSplash(hidden);
    await hidden.evaluate(() => {
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
      Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => 'hidden'});
    });
    await hidden.tap('.home-greet');
    const hid = await until(hidden, s => s.state === 'running', 'hidden tap');
    check('a tap still starts audio when hidden is stuck', hid.state === 'running' && hid.playing === true, hid);

    const dbg = await browser.newPage();
    dbg.on('pageerror', e => errors.push('debug ' + e));
    await dbg.setViewport({width: 390, height: 844, isMobile: true, hasTouch: true});
    await boot(dbg, {ua: android, debug: true});
    await hideSplash(dbg);
    const dbgBefore = await dbg.evaluate(snap);
    check('audiodebug overlay is on screen before audio', /audio none/.test(dbgBefore.debug), dbgBefore);
    await dbg.tap('.home-greet');
    const dbgAfter = await until(dbg, s => s.state === 'running' && /running/.test(s.debug) && /gain /.test(s.debug), 'debug overlay');
    check('audiodebug shows running state and gain', /audio running/.test(dbgAfter.debug) && /gain /.test(dbgAfter.debug) && /route element/.test(dbgAfter.debug), dbgAfter);

    const muted = await browser.newPage();
    muted.on('pageerror', e => errors.push('muted ' + e));
    await muted.setViewport({width: 390, height: 844, isMobile: true, hasTouch: true});
    await boot(muted, {ua: android, music: false});
    await hideSplash(muted);
    await muted.tap('.home-greet');
    await sleep(400);
    const mute = await muted.evaluate(snap);
    check('saved mute stays silent', mute.on === false && mute.built === 0 && mute.state === 'none' && mute.playing === false, mute);

    check('no page errors', errors.length === 0, errors);
  } finally {
    await browser.close();
  }

  if (fails.length) {
    console.error('AUDIO_FAIL\n' + fails.join('\n'));
    process.exit(1);
  }
  console.log('AUDIO_OK');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
