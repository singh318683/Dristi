const puppeteer = require('puppeteer-core');
const sleep = ms => new Promise(r => setTimeout(r, ms));

function offenders() {
  const win = document.documentElement.clientWidth;
  const bad = [];
  const doc = document.documentElement.scrollWidth;
  if (doc > win + 1) bad.push('document scrollWidth ' + doc);
  const nodes = document.querySelectorAll('body *');
  for (const el of nodes) {
    if (el.closest('[hidden]')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.right > win + 1.5) {
      const id = el.id ? '#' + el.id : '';
      const cls = (el.className && typeof el.className === 'string') ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
      const text = (el.childNodes.length && el.childNodes[0].nodeType === 3) ? el.childNodes[0].textContent.trim().slice(0, 32) : '';
      bad.push(el.tagName + id + cls + (text ? ' "' + text + '"' : '') + ' right=' + Math.round(r.right));
      if (bad.length > 8) break;
    }
  }
  return bad;
}

async function boot(page, lang, people) {
  await page.goto(process.env.APP_URL || 'http://127.0.0.1:8792/', {waitUntil: 'domcontentloaded'});
  await page.evaluate((lang, people) => {
    localStorage.setItem('dr-profiles', JSON.stringify(people));
    localStorage.setItem('dr-active', JSON.stringify(people[0] ? people[0].id : null));
    localStorage.setItem('dr-lang', JSON.stringify(lang));
    localStorage.removeItem('dr-chat-p1');
    localStorage.removeItem('dr-chat-p2');
  }, lang, people);
  await page.reload({waitUntil: 'domcontentloaded'});
  await page.evaluate(() => { const s = document.getElementById('gita-splash'); if (s) s.hidden = true; });
  await sleep(200);
}

const one = [{id: 'p1', name: 'Neetu', relation: 'Self', gender: 'Female', date: '1990-06-15', time: '12:00', place: {name: 'Delhi, India', lat: 28.65195, lon: 77.23149, tz: 'Asia/Kolkata'}, offset: null}];
const two = one.concat([{id: 'p2', name: 'Amit', relation: 'Family', gender: 'Male', date: '1988-03-02', time: '08:15', place: {name: 'Varanasi, India', lat: 25.32, lon: 82.97, tz: 'Asia/Kolkata'}, offset: null}]);

async function measure(page, name) {
  await sleep(80);
  const bad = await page.evaluate(offenders);
  return {name, bad};
}

(async () => {
  const browser = await puppeteer.launch({executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const page = await browser.newPage();
  const fails = [];
  const widths = [320, 375, 390, 430];
  const modes = ['light', 'dark'];
  const langs = ['en', 'hi'];
  for (const width of widths) {
    await page.setViewport({width, height: 760, deviceScaleFactor: 1, isMobile: true});
    for (const mode of modes) {
      await page.emulateMediaFeatures([{name: 'prefers-color-scheme', value: mode}]);
      for (const lang of langs) {
        const tag = width + '/' + mode + '/' + lang;
        await boot(page, lang, two);
        let rows = [];
        rows.push(await measure(page, 'home'));
        await page.evaluate(() => document.getElementById('tileToday').click());
        await sleep(900);
        rows.push(await measure(page, 'today-sheet'));
        await page.evaluate(() => { const x = document.getElementById('shX'); if (x) x.click(); });
        await page.evaluate(() => document.getElementById('tileChart').click());
        await sleep(400);
        rows.push(await measure(page, 'chart-sheet'));
        await page.evaluate(() => { const x = document.getElementById('shX'); if (x) x.click(); });
        await page.evaluate(() => go('kundli'));
        await page.waitForSelector('#ktools', {timeout: 15000});
        await sleep(200);
        rows.push(await measure(page, 'chart'));
        await page.evaluate(() => document.querySelector('#ktools [data-sheet="planets"]').click());
        await sleep(150);
        rows.push(await measure(page, 'planets-sheet'));
        await page.evaluate(() => { const x = document.getElementById('shX'); if (x) x.click(); });
        await page.evaluate(() => go('match'));
        await sleep(200);
        rows.push(await measure(page, 'match'));
        await page.evaluate(() => { const b = document.getElementById('doM'); if (b) b.click(); });
        await sleep(1200);
        rows.push(await measure(page, 'match-result'));
        await page.evaluate(() => go('me'));
        await sleep(150);
        rows.push(await measure(page, 'profile'));
        await page.evaluate(() => document.getElementById('whoBtn').click());
        await sleep(150);
        rows.push(await measure(page, 'people'));
        await page.evaluate(() => { const x = document.getElementById('shX'); if (x) x.click(); });
        await page.evaluate(() => startOnb());
        await sleep(100);
        rows.push(await measure(page, 'onb-1'));
        await page.evaluate(() => { const n = document.getElementById('nm'); if (n && !n.value) n.value = 'Neetu'; document.getElementById('go2').click(); });
        await sleep(100);
        rows.push(await measure(page, 'onb-2'));
        await page.evaluate(() => document.getElementById('open-date').click());
        await sleep(200);
        rows.push(await measure(page, 'birth-sheet'));
        await page.evaluate(() => document.getElementById('when-done').click());
        await page.evaluate(() => document.getElementById('go3').click());
        await sleep(100);
        rows.push(await measure(page, 'onb-3'));
        await boot(page, lang, []);
        rows.push(await measure(page, 'onb-0'));
        await page.evaluate(() => { const s = document.getElementById('gita-splash'); if (s) { s.hidden = false; } });
        await sleep(80);
        rows.push(await measure(page, 'gita'));
        await boot(page, lang, one);
        await page.evaluate(() => { const s = document.getElementById('gita-splash'); if (s) s.hidden = true; go('ask'); });
        await sleep(150);
        rows.push(await measure(page, 'ask-empty'));
        await page.evaluate(() => { const sample = document.getElementById('askSample'); if (sample) sample.click(); const send = document.getElementById('askSend'); if (send) send.click(); });
        await sleep(500);
        rows.push(await measure(page, 'ask-thread'));
        const dots = await page.evaluate(() => !!document.getElementById('askDots'));
        if (dots) rows.push({name: 'ask-thread-dots', bad: ['example dots still in the conversation composer']});
        for (const row of rows) {
          if (row.bad.length) fails.push(tag + ' ' + row.name + ': ' + row.bad.join(' | '));
        }
      }
    }
  }
  await browser.close();
  if (fails.length) {
    console.error(fails.join('\n'));
    process.exit(1);
  }
  console.log('LAYOUT_OK');
})().catch(e => { console.error(e); process.exit(1); });
