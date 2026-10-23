// v3 screenshots + interaction recording. Usage: node tools/shots_v3.js [baseUrl] [prefixes]
const puppeteer = require('/workspace/tools/node_modules/puppeteer-core');
const BASE = process.argv[2] || 'http://localhost:8792';
const OUT = '/workspace/jyotish-ai/shots/v3/';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const PROFILES = [
  {id: 'p1', name: 'Ananya', relation: 'Self', gender: 'Female', date: '1994-03-14', time: '06:45', place: {name: 'Varanasi, Uttar Pradesh, India', lat: 25.31668, lon: 83.01041, tz: 'Asia/Kolkata'}, offset: null},
  {id: 'p2', name: 'Rohan', relation: 'Spouse', gender: 'Male', date: '1991-11-02', time: '21:10', place: {name: 'Pune, Maharashtra, India', lat: 18.51957, lon: 73.85535, tz: 'Asia/Kolkata'}, offset: null},
  {id: 'p3', name: 'Maa', relation: 'Parent', gender: 'Female', date: '1966-07-21', time: null, place: {name: 'Jaipur, Rajasthan, India', lat: 26.91962, lon: 75.78781, tz: 'Asia/Kolkata'}, offset: null},
];
// Sample assistant reply (seeded locally; no AI key is configured) to show tappable citations.
const SAMPLE = [{role: 'user', content: 'How will my career progress?'}, {role: 'assistant', content: "**Short answer:** steady growth, with a clear lift from late 2026.\n\n- **Jupiter in the 8th house** (Libra) gives depth: research, finance, strategy and other people's resources suit you.\n- **Venus in Pisces** is exalted in your Lagna, so charm and design sense are career assets.\n- Your current period is **Venus › Jupiter** (to May 2029), which activates houses 1, 3, 8 and 9.\n- **Saturn transiting your 1st house** (Sade Sati peak) asks for patience; results come through consistent work.\n\n_Placements used: Jupiter house 8, Venus house 1, Saturn transit house 1, Venus–Jupiter dasha._"}];
const only = process.argv[3] ? process.argv[3].split(',') : null;
(async () => {
  const b = await puppeteer.launch({executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox']});
  const p = await b.newPage();
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text()); });
  await p.setViewport({width: 390, height: 844, deviceScaleFactor: 2});
  const media = (scheme, motion = 'reduce') => p.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: motion}, {name: 'prefers-color-scheme', value: scheme}]);
  await media('light');
  const want = n => !only || only.some(o => n.startsWith(o));
  const shot = async (n, full = false) => { if (!want(n)) return; await sleep(500); if (full) { const h = await p.evaluate(() => document.documentElement.scrollHeight); await p.setViewport({width: 390, height: h, deviceScaleFactor: 2}); await sleep(300); } await p.screenshot({path: OUT + n}); if (full) await p.setViewport({width: 390, height: 844, deviceScaleFactor: 2}); console.log('saved', n); };
  const seed = async (ex = {}) => { await p.evaluate((ps, ex) => { localStorage.clear(); localStorage.setItem('dr-profiles', JSON.stringify(ps)); localStorage.setItem('dr-active', '"p1"'); localStorage.setItem('dr-lang', JSON.stringify(ex.lang || 'en')); localStorage.setItem('dr-style', '"north"'); localStorage.setItem('dr-device', '"shot-device-v3"'); if (ex.chat) localStorage.setItem('dr-chat-p1', JSON.stringify(ex.chat)); }, PROFILES, ex); await p.reload({waitUntil: 'networkidle0'}); await sleep(900); };
  const tab = async t => { await p.evaluate(t => { S.tab = t; render(); window.scrollTo(0, 0); }, t); await sleep(1100); };
  const tapDef = async sel => { await p.evaluate(sel => { const e = document.querySelector(sel); e.scrollIntoView({block: 'center'}); }, sel); await sleep(250); await p.click(sel); await sleep(500); };
  // onboarding step 2
  await p.goto(BASE + '/', {waitUntil: 'networkidle0'});
  await p.evaluate(() => localStorage.clear()); await p.reload({waitUntil: 'networkidle0'});
  if (want('03')) { await p.click('#go1'); await p.type('#nm', 'Ananya'); await p.click('#go2'); await p.$eval('#dob', e => { e.value = '1994-03-14'; e.dispatchEvent(new Event('change')); }); await p.click('#unk'); await shot('03_onboarding_step2.png'); }
  await seed();
  await shot('10_home.png'); await shot('11_home_full.png', true);
  // chart: planet tapped (static, full aspect lines)
  await tab('kundli');
  await p.evaluate(() => { const g = document.querySelector('#chartsvg [data-p="Jupiter"]'); const o = planetHL(K.c, 'Jupiter'); setHL(o, document.querySelector('#pr-Jupiter button')); });
  await shot('20_chart_planet_tapped.png');
  await p.evaluate(() => setHL(houseHL(K.c, 7), document.querySelector('#hm-7'))); await shot('21_chart_house_tapped.png');
  // definition taps -> chart highlight (dock visible while scrolled)
  await p.evaluate(() => setHL(null));
  await tapDef('#k .card .item[data-hl]'); await shot('24_def_tap_yoga_dock.png');
  await tapDef('#k section:nth-of-type(6) .item[data-hl]'); await shot('25_def_tap_manglik_dock.png');
  await tapDef('#dashaSec summary[data-hl]'); await shot('26_def_tap_dasha_dock.png');
  await p.click('#mini'); await sleep(700); await shot('27_def_tap_back_to_full_chart.png');
  await tapDef('#legend button[data-hl]:nth-child(6)'); await p.evaluate(() => document.querySelector('#chartCard').scrollIntoView({block: 'start'})); await p.evaluate(() => window.scrollBy(0, -70)); await shot('28_legend_tap_jupiter.png');
  await p.evaluate(() => { setHL(planetHL(K.c, 'Saturn'), null); document.querySelector('#sty button[data-v="south"]').click(); }); await sleep(400); await p.evaluate(() => window.scrollTo(0, 0)); await shot('29_chart_south_saturn.png');
  await p.evaluate(() => { setHL(null); document.querySelector('#sty button[data-v="north"]').click(); }); await sleep(300);
  // chat with tappable citations
  await seed({chat: SAMPLE}); await tab('ask'); await shot('30_chat_citations.png');
  if (want('31')) { await p.click('.cite'); await sleep(900); await shot('31_chat_cite_minichart.png'); await p.keyboard.press('Escape'); }
  // match
  await p.evaluate(() => { S.match = {girl: 'p1', boy: 'p2', result: null}; }); await tab('match');
  await p.click('#doM'); await sleep(1600); await p.evaluate(() => window.scrollTo(0, 0)); await shot('41_match_result.png');
  // hindi
  await seed({lang: 'hi'}); await shot('60_home_hindi.png');
  await tab('kundli'); await p.evaluate(() => setHL(planetHL(K.c, 'Venus'), document.querySelector('#pr-Venus button'))); await shot('61_chart_hindi_tap.png');
  // dark
  await media('dark'); await seed();
  await shot('70_dark_home.png');
  await tab('kundli'); await p.evaluate(() => setHL(planetHL(K.c, 'Moon'), document.querySelector('#pr-Moon button'))); await shot('71_dark_chart_tap.png');
  // ---- recording (motion on)
  if (want('rec')) {
    await media('light', 'no-preference'); await seed();
    const fs = require('fs'), FR = '/tmp/drishti_rec/'; fs.rmSync(FR, {recursive: true, force: true}); fs.mkdirSync(FR, {recursive: true});
    const cdp = await p.createCDPSession(), frames = [];
    cdp.on('Page.screencastFrame', async f => { const n = FR + String(frames.length).padStart(5, '0') + '.jpg'; fs.writeFileSync(n, Buffer.from(f.data, 'base64')); frames.push([n, f.metadata.timestamp]); cdp.send('Page.screencastFrameAck', {sessionId: f.sessionId}).catch(() => {}); });
    await cdp.send('Page.startScreencast', {format: 'jpeg', quality: 85, maxWidth: 780, maxHeight: 1688, everyNthFrame: 1});
    const rec = {stop: async () => { await cdp.send('Page.stopScreencast'); const end = Date.now() / 1000; let list = ''; frames.forEach(([n, t], i) => { const nx = i + 1 < frames.length ? frames[i + 1][1] : t + 1; list += `file '${n}'\nduration ${Math.max(0.01, nx - t).toFixed(3)}\n`; }); list += `file '${frames[frames.length - 1][0]}'\n`; fs.writeFileSync(FR + 'list.txt', list); }};
    await sleep(600); await p.evaluate(() => { S.tab = 'kundli'; render(); }); await sleep(1800);
    const tapAt = async sel => { const el = await p.$(sel); const bx = await el.boundingBox(); await p.mouse.click(bx.x + bx.width / 2, bx.y + bx.height / 2); };
    await tapAt('#chartsvg [data-p="Jupiter"] text'); await sleep(2600);
    await p.evaluate(() => window.scrollTo({top: 0, behavior: 'smooth'})); await sleep(900);
    await p.click('#div button[data-v="d9"]'); await sleep(1300); await p.click('#div button[data-v="d1"]'); await sleep(1300);
    await p.evaluate(() => document.querySelector('#k .card .item[data-hl]').scrollIntoView({behavior: 'smooth', block: 'center'})); await sleep(1200);
    await p.click('#k .card .item[data-hl]'); await sleep(1600);
    await p.click('#k section:nth-of-type(6) .item[data-hl]'); await sleep(1600);
    await p.evaluate(() => document.querySelector('#dashaSec tr.now').scrollIntoView({behavior: 'smooth', block: 'center'})); await sleep(1100);
    await p.click('#dashaSec tr.now'); await sleep(1800);
    await p.click('#mini'); await sleep(1800);
    await p.evaluate(() => { S.match = {girl: 'p1', boy: 'p2', result: null}; S.tab = 'match'; render(); }); await sleep(900);
    await p.click('#doM'); await sleep(2200);
    await rec.stop(); console.log('frames recorded');
  }
  await b.close();
})();
