const puppeteer = require('puppeteer-core');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const person = [{id: 'p1', name: 'Neetu', relation: 'Self', gender: 'Female', date: '1990-06-15', time: '12:00', place: {name: 'Varanasi, India', lat: 25.3176, lon: 82.9739, tz: 'Asia/Kolkata'}}];

function offenders() {
  const win = document.documentElement.clientWidth;
  const bad = [];
  const nodes = document.querySelectorAll('#me *');
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.right > win + 1.5) {
      const id = el.id ? '#' + el.id : '';
      bad.push(el.tagName + id + ' right=' + Math.round(r.right));
      if (bad.length > 6) break;
    }
  }
  return bad;
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  const fails = [];
  const sizes = [[320, 568], [375, 667], [390, 844], [430, 932]];
  let shot = false;
  for (const [width, height] of sizes) {
    await page.setViewport({width, height, deviceScaleFactor: width === 390 ? 2 : 1, isMobile: true});
    for (const mode of ['light', 'dark']) {
      await page.emulateMediaFeatures([{name: 'prefers-color-scheme', value: mode}]);
      for (const lang of ['en', 'hi']) {
        const tag = width + 'x' + height + '/' + mode + '/' + lang;
        await page.goto((process.env.APP_URL || 'http://127.0.0.1:8792/'), {waitUntil: 'domcontentloaded'});
        await page.evaluate((people, lang) => {
          localStorage.setItem('dr-profiles', JSON.stringify(people));
          localStorage.setItem('dr-active', JSON.stringify(people[0].id));
          localStorage.setItem('dr-lang', JSON.stringify(lang));
          localStorage.setItem('dr-style', JSON.stringify('north'));
        }, person, lang);
        await page.reload({waitUntil: 'domcontentloaded'});
        await page.evaluate(() => { const s = document.getElementById('gita-splash'); if (s) s.hidden = true; go('me'); });
        await page.waitForSelector('#me');
        await sleep(120);
        const report = await page.evaluate(offenders);
        const info = await page.evaluate(() => {
          const main = document.getElementById('me');
          const sub = (main.querySelector('.sub') || {}).textContent || '';
          const text = main.innerText;
          return {
            scroll: main.scrollHeight - main.clientHeight,
            sub,
            invite: /Invite link|copyRef|The link opens Drishti|आमंत्रण/.test(text),
            signin: /Optional sign-in|Sign-in is optional|Email link|Phone OTP|magicEmail|वैकल्पिक साइन-इन|फ़ोन OTP/.test(text + main.innerHTML),
            kept: !!document.getElementById('personRow') && !!document.getElementById('credN') && !!document.getElementById('prefs') && !!document.getElementById('setLang') && !!document.getElementById('setSty') && !!document.getElementById('aboutBtn') && !!document.getElementById('clr'),
            price: (document.getElementById('credN') || {}).textContent || '',
          };
        });
        if (report.length) fails.push(tag + ' overflow ' + report.join(' | '));
        if (info.scroll > 2) fails.push(tag + ' scrolls ' + info.scroll);
        if (info.invite || info.signin) fails.push(tag + ' removed UI still shown ' + info.sub);
        if (!info.kept) fails.push(tag + ' missing a kept control');
        const music = await page.evaluate(() => {
          const b = document.getElementById('musicBtn');
          return b ? {pressed: b.getAttribute('aria-pressed'), label: b.getAttribute('aria-label')} : null;
        });
        if (!music || music.pressed !== 'false') fails.push(tag + ' music toggle missing ' + JSON.stringify(music));
        if (!info.price.includes('0.99')) fails.push(tag + ' price ' + info.price);
        const expectSub = lang === 'hi' ? 'आचार्य एक AI ज्योतिषी हैं।' : 'Acharya is an AI astrologer.';
        if (info.sub !== expectSub) fails.push(tag + ' subtitle ' + info.sub);
        if (width === 390 && mode === 'dark' && lang === 'en' && !shot) {
          await page.screenshot({path: '/opt/cursor/artifacts/profile_dark_390.png'});
          shot = true;
        }
      }
    }
  }
  await page.setViewport({width: 390, height: 844, isMobile: true});
  await page.emulateMediaFeatures([{name: 'prefers-color-scheme', value: 'dark'}]);
  await page.evaluate(() => document.querySelector('#setLang [data-v="hi"]').click());
  await sleep(150);
  const hi = await page.evaluate(() => ({
    sub: document.querySelector('#me .sub').textContent,
    lang: document.querySelector('#setLang [data-v="hi"]').getAttribute('aria-pressed'),
  }));
  if (hi.sub !== 'आचार्य एक AI ज्योतिषी हैं।' || hi.lang !== 'true') fails.push('language toggle ' + JSON.stringify(hi));
  await page.evaluate(() => document.querySelector('#setSty [data-v="south"]').click());
  await sleep(100);
  const south = await page.evaluate(() => document.querySelector('#setSty [data-v="south"]').getAttribute('aria-pressed'));
  if (south !== 'true') fails.push('chart toggle ' + south);
  const beforeMute = await page.evaluate(() => document.getElementById('musicBtn').getAttribute('aria-pressed'));
  await page.click('#musicBtn');
  await sleep(200);
  const muted = await page.evaluate(() => {
    const b = document.getElementById('musicBtn');
    return {pressed: b.getAttribute('aria-pressed'), label: b.getAttribute('aria-label'), me: !!document.getElementById('me'), invite: !!document.getElementById('copyRef')};
  });
  if (beforeMute !== 'false' || muted.pressed !== 'true' || !/Play background music|संगीत चालू करें/.test(muted.label) || !muted.me || muted.invite) {
    fails.push('music mute ' + JSON.stringify({beforeMute, muted}));
  }
  await page.click('#musicBtn');
  await sleep(200);
  const unmuted = await page.evaluate(() => document.getElementById('musicBtn').getAttribute('aria-pressed'));
  if (unmuted !== 'false') fails.push('music unmute ' + unmuted);
  await browser.close();
  if (fails.length) {
    console.error(fails.join('\n'));
    process.exit(1);
  }
  console.log('PROFILE_OK');
})().catch(err => { console.error(err); process.exit(1); });
