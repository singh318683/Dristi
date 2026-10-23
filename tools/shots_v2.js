// v2 screenshots. Usage: node tools/shots_v2.js [baseUrl]
const puppeteer = require('/workspace/tools/node_modules/puppeteer-core');
const BASE = process.argv[2] || 'http://localhost:8792';
const OUT = process.env.OUT || '/workspace/jyotish-ai/shots/v2/';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const PROFILES = [
  {id: 'p1', name: 'Ananya', relation: 'Self', gender: 'Female', date: '1994-03-14', time: '06:45', place: {name: 'Varanasi, Uttar Pradesh, India', lat: 25.31668, lon: 83.01041, tz: 'Asia/Kolkata'}, offset: null},
  {id: 'p2', name: 'Rohan', relation: 'Spouse', gender: 'Male', date: '1991-11-02', time: '21:10', place: {name: 'Pune, Maharashtra, India', lat: 18.51957, lon: 73.85535, tz: 'Asia/Kolkata'}, offset: null},
  {id: 'p3', name: 'Maa', relation: 'Parent', gender: 'Female', date: '1966-07-21', time: null, place: {name: 'Jaipur, Rajasthan, India', lat: 26.91962, lon: 75.78781, tz: 'Asia/Kolkata'}, offset: null},
];
const only = process.argv[3] ? process.argv[3].split(',') : null;
(async () => {
  const b = await puppeteer.launch({executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox']});
  const p = await b.newPage();
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text()); });
  await p.setViewport({width: 390, height: 844, deviceScaleFactor: 2});
  const scheme = v => p.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}, {name: 'prefers-color-scheme', value: v}]);
  await scheme('light');
  const want = n => !only || only.some(o => n.startsWith(o));
  const shot = async (n, full = false) => { if (!want(n)) return; await sleep(600); if (full) { const h = await p.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)); await p.setViewport({width: 390, height: h, deviceScaleFactor: 2}); await sleep(300); } await p.screenshot({path: OUT + n}); if (full) await p.setViewport({width: 390, height: 844, deviceScaleFactor: 2}); console.log('saved', n); };
  const seed = async (extra = {}) => { await p.evaluate((ps, ex) => { localStorage.clear(); localStorage.setItem('dr-profiles', JSON.stringify(ps)); localStorage.setItem('dr-active', '"p1"'); localStorage.setItem('dr-lang', JSON.stringify(ex.lang || 'en')); localStorage.setItem('dr-style', '"north"'); localStorage.setItem('dr-device', '"shot-device-v2"'); }, PROFILES, extra); await p.reload({waitUntil: 'networkidle0'}); await sleep(900); };
  const tab = async t => { await p.evaluate(t => { S.tab = t; render(); window.scrollTo(0, 0); }, t); await sleep(1100); };
  // onboarding
  await p.goto(BASE + '/', {waitUntil: 'networkidle0'});
  await p.evaluate(() => localStorage.clear()); await p.reload({waitUntil: 'networkidle0'});
  await p.evaluateHandle('document.fonts.ready');
  await shot('01_onboarding_welcome.png');
  await p.click('#go1'); await p.type('#nm', 'Ananya'); await p.click('#gen button[data-v="Female"]');
  await shot('02_onboarding_step1.png');
  await p.click('#go2');
  await p.$eval('#dob', e => { e.value = '1994-03-14'; e.dispatchEvent(new Event('change')); });
  await p.click('#unk'); await sleep(300);
  await shot('03_onboarding_step2_unknown_time.png');
  await p.click('#unk'); await sleep(200);
  await p.$eval('#tob', e => { e.value = '06:45'; e.dispatchEvent(new Event('change')); });
  await p.click('#go3'); await p.type('#pl', 'Varan'); await sleep(900);
  await shot('04_onboarding_step3_city.png');
  await p.keyboard.press('ArrowDown'); await p.keyboard.press('Enter'); await sleep(900);
  await shot('05_onboarding_step3_resolved.png');
  // home
  await seed();
  await shot('10_home.png'); await shot('11_home_full.png', true);
  await p.click('#whoBtn'); await sleep(300); await shot('12_profile_switcher.png');
  await p.keyboard.press('Escape');
  // chart
  await tab('kundli');
  await p.evaluate(() => { const e = document.querySelector('#chartsvg [data-p="Moon"]'); e.dispatchEvent(new MouseEvent('click', {bubbles: true})); }); await sleep(300);
  await shot('20_chart_planet_tapped.png');
  await p.evaluate(() => { document.querySelector('#chartsvg [data-h="7"]').dispatchEvent(new MouseEvent('click', {bubbles: true})); }); await sleep(300);
  await shot('21_chart_house_tapped.png');
  await p.evaluate(() => { S.sel = {p: 'Saturn'}; document.querySelector('#sty button[data-v="south"]').click(); }); await shot('22_chart_south.png');
  await p.evaluate(() => { S.sel = null; document.querySelector('#sty button[data-v="north"]').click(); });
  await shot('23_chart_full.png', true);
  // chat
  await tab('ask'); await shot('30_chat_nokey_welcome.png');
  await p.evaluate(() => document.querySelector('.qgrid [data-q]').click()); await sleep(1500);
  await shot('31_chat_nokey_reply.png');
  // match
  await p.evaluate(() => { S.match = {girl: 'p1', boy: 'p2', result: null}; }); await tab('match');
  await shot('40_match_pick.png');
  await p.click('#doM'); await sleep(1600); await p.evaluate(() => window.scrollTo(0, 0));
  await shot('41_match_result.png'); await shot('42_match_full.png', true);
  // profile
  await tab('me'); await shot('50_profile_full.png', true);
  // hindi
  await seed({lang: 'hi'}); await shot('60_home_hindi.png');
  await tab('kundli'); await p.evaluate(() => { S.sel = {p: 'Venus'}; render(); }); await sleep(1000); await shot('61_chart_hindi.png');
  // dark
  await scheme('dark'); await seed();
  await shot('70_dark_home.png');
  await tab('kundli'); await p.evaluate(() => { S.sel = {p: 'Moon'}; render(); }); await sleep(1000); await shot('71_dark_chart.png');
  await tab('ask'); await shot('72_dark_chat.png');
  await b.close();
})();
