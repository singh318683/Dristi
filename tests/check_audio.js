const puppeteer = require('puppeteer-core');

const person = [{id: 'p1', name: 'Test', relation: 'Self', gender: 'Female', date: '1990-06-15', time: '12:00', place: {name: 'Delhi, India', lat: 28.65195, lon: 77.23149, tz: 'Asia/Kolkata'}}];
const android = 'Mozilla/5.0 (Linux; Android 12; SM-G970F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36';

function quiet() {
  return {
    button: !!document.getElementById('musicBtn'),
    out: !!document.getElementById('musicOut'),
    ctx: typeof musicCtx !== 'undefined' && !!musicCtx,
    unlock: typeof musicUnlock === 'function',
  };
}

async function boot(page, ua) {
  if (ua) await page.setUserAgent(ua);
  const base = process.env.APP_URL || 'http://127.0.0.1:8792/';
  await page.goto(base, {waitUntil: 'domcontentloaded'});
  await page.evaluate(people => {
    localStorage.setItem('dr-profiles', JSON.stringify(people));
    localStorage.setItem('dr-active', JSON.stringify(people[0].id));
    localStorage.setItem('dr-lang', '"en"');
  }, person);
  await page.reload({waitUntil: 'domcontentloaded'});
  await page.waitForFunction(() => document.querySelector('.home-greet, #gitaContinue'));
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
    await page.setViewport({width: 390, height: 844, isMobile: true, hasTouch: true});
    await boot(page, android);
    const before = await page.evaluate(quiet);
    check('no music control or context before a tap', !before.button && !before.out && !before.ctx && !before.unlock, before);
    const splash = await page.$('#gitaContinue');
    if (splash) await page.tap('#gitaContinue');
    await page.evaluate(() => {
      const s = document.getElementById('gita-splash');
      if (s) s.hidden = true;
      document.body.classList.remove('gita-lock');
    });
    const greet = await page.$('.home-greet');
    if (greet) await page.tap('.home-greet');
    const after = await page.evaluate(quiet);
    check('a tap still does not start background music', !after.button && !after.out && !after.ctx && !after.unlock, after);
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
