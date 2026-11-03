const puppeteer = require('puppeteer-core');
const sleep = ms => new Promise(r => setTimeout(r, ms));

const person = [{id: 'p1', name: 'Neetu', relation: 'Self', gender: 'Female', date: '1990-06-15', time: '12:00', place: {name: 'Varanasi, India', lat: 25.3176, lon: 82.9739, tz: 'Asia/Kolkata'}}];
const planets = ['Sun', 'Moon', 'Mars', 'Mercury', 'Jupiter', 'Venus', 'Saturn', 'Rahu', 'Ketu'];
const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';
const android = 'Mozilla/5.0 (Linux; Android 12; SM-G970F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36';

async function boot(page, lang) {
  await page.goto(process.env.APP_URL || 'http://127.0.0.1:8792/', {waitUntil: 'domcontentloaded'});
  await page.evaluate((people, lang) => {
    localStorage.setItem('dr-profiles', JSON.stringify(people));
    localStorage.setItem('dr-active', JSON.stringify(people[0].id));
    localStorage.setItem('dr-lang', JSON.stringify(lang));
    localStorage.setItem('dr-style', JSON.stringify('north'));
  }, person, lang);
  await page.reload({waitUntil: 'domcontentloaded'});
  await page.evaluate(() => { const s = document.getElementById('gita-splash'); if (s) s.hidden = true; document.body.classList.remove('gita-lock'); go('kundli'); });
  await page.waitForSelector('#chartsvg svg', {timeout: 15000});
  await sleep(80);
}

async function waitSheet(page) {
  await page.waitForFunction(() => {
    const x = document.getElementById('shX');
    const sheet = document.querySelector('.sheet.reading');
    const para = sheet && sheet.querySelector('.read-m');
    if (!x || !sheet || !para) return false;
    const r = x.getBoundingClientRect();
    const s = sheet.getBoundingClientRect();
    return r.width >= 43 && r.top >= 0 && r.bottom <= window.innerHeight + 1 && s.bottom <= window.innerHeight + 1 && s.top < window.innerHeight;
  }, {timeout: 5000});
}
async function openPlanet(page, name) {
  await page.evaluate(p => {
    const g = document.querySelector('#chartsvg [data-p="' + p + '"]');
    g.dispatchEvent(new MouseEvent('click', {bubbles: true}));
  }, name);
  await waitSheet(page);
}

async function readingState(page) {
  return page.evaluate(() => {
    const sheet = document.querySelector('.sheet.reading');
    const body = sheet && sheet.querySelector('.sheet-body');
    const para = document.querySelector('.read-m');
    const x = document.getElementById('shX');
    const handle = document.querySelector('.sheet-handle');
    const main = document.getElementById('k');
    const nav = document.querySelector('nav.bottom');
    const xr = x ? x.getBoundingClientRect() : null;
    const pr = para ? para.getBoundingClientRect() : null;
    const br = body ? body.getBoundingClientRect() : null;
    const sr = sheet ? sheet.getBoundingClientRect() : null;
    const hit = xr ? document.elementFromPoint(xr.left + xr.width / 2, xr.top + xr.height / 2) : null;
    const clamp = para ? getComputedStyle(para).webkitLineClamp : '';
    return {
      open: !!(sheet && !document.getElementById('sheet').hidden),
      title: (document.getElementById('readTitle') || {}).textContent || '',
      text: para ? para.textContent : '',
      label: x ? x.getAttribute('aria-label') : '',
      handleLabel: handle ? handle.getAttribute('aria-label') : '',
      xw: xr ? xr.width : 0,
      xh: xr ? xr.height : 0,
      xOnScreen: !!(xr && xr.top >= -1 && xr.bottom <= window.innerHeight + 1 && xr.left >= -1 && xr.right <= window.innerWidth + 1),
      xHit: !!(hit && (hit === x || x.contains(hit))),
      sheetBottom: sr ? sr.bottom : 0,
      viewH: window.innerHeight,
      textVisible: !!(pr && br && pr.top >= br.top - 1 && pr.bottom <= br.bottom + 1),
      bodyScroll: body ? body.scrollHeight - body.clientHeight : 0,
      clamp,
      pageScroll: main ? main.scrollHeight - main.clientHeight : 99,
      locked: document.body.classList.contains('sheet-lock'),
      z: getComputedStyle(document.getElementById('sheet')).zIndex,
      navZ: nav ? getComputedStyle(nav).zIndex : 'auto',
    };
  });
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const fails = [];
  const check = (name, ok, extra) => {
    if (!ok) fails.push(name + (extra ? ' ' + (typeof extra === 'string' ? extra : JSON.stringify(extra)) : ''));
  };
  const page = await browser.newPage();
  const shots = {light: false, dark: false};

  async function sweep(tag, lang) {
    const expectClose = lang === 'hi' ? 'बंद करें' : 'Close';
    const expectHandle = lang === 'hi' ? 'बंद करने के लिए नीचे खींचें' : 'Drag down to close';
    for (const name of planets) {
      await openPlanet(page, name);
      const expected = await page.evaluate(p => planetHL(K.c, p), name);
      const got = await readingState(page);
      const glyph = await page.evaluate(p => planetGlyph(p), name);
      check(tag + ' ' + name + ' text', got.text === expected.m && got.text.length > 40, got.text.slice(0, 80));
      check(tag + ' ' + name + ' title', got.title.includes(expected.t) && got.title.includes(glyph), got.title);
      check(tag + ' ' + name + ' close', got.label === expectClose && got.xw >= 43 && got.xh >= 43 && got.xOnScreen && got.xHit, got);
      check(tag + ' ' + name + ' handle', got.handleLabel === expectHandle, got.handleLabel);
      check(tag + ' ' + name + ' not clamped', got.clamp === 'none' && got.textVisible, {clamp: got.clamp, bodyScroll: got.bodyScroll, textVisible: got.textVisible});
      check(tag + ' ' + name + ' above the page', got.locked && Number(got.z) > 30 && got.sheetBottom <= got.viewH + 1, got);
      if (name === 'Mercury' && tag.startsWith('375') && tag.includes('/en')) {
        const mode = tag.includes('/light/') ? 'light' : tag.includes('/dark/') ? 'dark' : '';
        if (mode && !shots[mode]) {
          await page.screenshot({path: '/opt/cursor/artifacts/mercury_sheet_' + mode + '_375.png'});
          shots[mode] = true;
        }
      }
    }
    await page.evaluate(() => document.querySelector('#chartsvg [data-h="1"]').dispatchEvent(new MouseEvent('click', {bubbles: true})));
    await waitSheet(page);
    const house = await page.evaluate(() => ({got: document.querySelector('.read-m').textContent, exp: houseHL(K.c, 1).m, title: document.getElementById('readTitle').textContent}));
    check(tag + ' house', house.got === house.exp && /House 1|1वाँ भाव|1वां भाव/.test(house.title), house.title);

    await page.keyboard.press('Escape');
    await sleep(30);
    await page.evaluate(() => document.querySelector('#legend button').focus());
    await page.click('#legend button');
    await waitSheet(page);
    const asc = await page.evaluate(() => document.getElementById('readTitle').textContent);
    check(tag + ' ascendant', /Ascendant|लग्न/.test(asc), asc);
    await page.evaluate(() => document.getElementById('shX').click());
    await sleep(40);
    const back = await page.evaluate(() => !!(document.activeElement && document.activeElement.closest('#legend')));
    check(tag + ' focus restored', back);
    const closedScroll = await page.evaluate(() => {
      const main = document.getElementById('k');
      return main.scrollHeight - main.clientHeight;
    });
    check(tag + ' chart page does not scroll', closedScroll <= 2, String(closedScroll));

    await page.evaluate(() => document.querySelector('[data-sheet="doshas"]').click());
    await page.waitForSelector('.sheet.tall .item', {timeout: 5000});
    await page.evaluate(() => document.querySelector('.sheet.tall .item').click());
    await waitSheet(page);
    const dosha = await page.evaluate(() => {
      const text = document.querySelector('.read-m').textContent;
      return {text, open: !!document.querySelector('.sheet.reading'), long: text.length > 8};
    });
    check(tag + ' dosha sheet', dosha.open && dosha.long, dosha.text.slice(0, 60));
    await page.keyboard.press('Escape');
    await sleep(30);
    check(tag + ' escape closes', await page.evaluate(() => document.getElementById('sheet').hidden));

    await openPlanet(page, 'Mercury');
    await page.mouse.click(12, 12);
    await sleep(30);
    check(tag + ' backdrop closes', await page.evaluate(() => document.getElementById('sheet').hidden));

    await openPlanet(page, 'Mercury');
    const handle = await page.$('.sheet-handle');
    const box = await handle.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + 12);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 2, box.y + 140, {steps: 8});
    await page.mouse.up();
    await sleep(40);
    check(tag + ' swipe closes', await page.evaluate(() => document.getElementById('sheet').hidden));

    await page.evaluate(() => document.querySelector('[data-sheet="yogas"]').click());
    await page.waitForSelector('.sheet.tall', {timeout: 5000});
    const yogaCount = await page.evaluate(() => document.querySelectorAll('.sheet.tall .item').length);
    if (yogaCount) {
      await page.evaluate(() => document.querySelector('.sheet.tall .item').click());
      await waitSheet(page);
      const yoga = await page.evaluate(() => document.querySelector('.read-m').textContent);
      check(tag + ' yoga sheet', yoga.length > 8, yoga.slice(0, 60));
    }
    await page.keyboard.press('Escape');
  }

  const sizes = [[320, 568], [375, 812], [390, 844], [430, 932]];
  for (const [width, height] of sizes) {
    await page.setViewport({width, height, isMobile: true, hasTouch: true, deviceScaleFactor: width === 375 ? 2 : 1});
    for (const mode of ['light', 'dark']) {
      await page.emulateMediaFeatures([{name: 'prefers-color-scheme', value: mode}]);
      for (const lang of ['en', 'hi']) {
        await boot(page, lang);
        await sweep(width + 'x' + height + '/' + mode + '/' + lang, lang);
      }
    }
  }

  await page.setUserAgent(iphone);
  await page.setViewport({width: 390, height: 844, isMobile: true, hasTouch: true});
  await page.emulateMediaFeatures([{name: 'prefers-color-scheme', value: 'light'}]);
  await boot(page, 'en');
  await openPlanet(page, 'Mercury');
  const ios = await readingState(page);
  const iosExp = await page.evaluate(() => planetHL(K.c, 'Mercury').m);
  check('iphone safari mercury', ios.text === iosExp && ios.xHit && ios.textVisible && ios.pageScroll <= 2, ios);

  await page.setUserAgent(android);
  await page.setViewport({width: 360, height: 740, isMobile: true, hasTouch: true});
  await page.emulateMediaFeatures([{name: 'prefers-color-scheme', value: 'dark'}]);
  await boot(page, 'hi');
  await openPlanet(page, 'Mercury');
  const and = await readingState(page);
  const andExp = await page.evaluate(() => planetHL(K.c, 'Mercury').m);
  check('android chrome mercury', and.text === andExp && and.label === 'बंद करें' && and.xHit && and.textVisible, and);
  const andScroll = await page.evaluate(() => document.getElementById('k').scrollHeight - document.getElementById('k').clientHeight);
  check('android chart stays put', andScroll <= 2, String(andScroll));

  await browser.close();
  if (!shots.light || !shots.dark) fails.push('missing mercury screenshots ' + JSON.stringify(shots));
  if (fails.length) {
    console.error(fails.join('\n'));
    process.exit(1);
  }
  console.log('READING_OK');
})().catch(err => { console.error(err); process.exit(1); });
