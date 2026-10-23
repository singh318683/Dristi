// Screenshots of the running app. Usage: node tools/shots.js [baseUrl]
const puppeteer = require('/workspace/tools/node_modules/puppeteer-core');
const BASE = process.argv[2] || 'http://localhost:8792';
const OUT = '/workspace/jyotish-ai/shots/';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const PROFILES = [
  {id: 'p1', name: 'Ananya', relation: 'Self', gender: 'Female', date: '1994-03-14', time: '06:45', place: {name: 'Varanasi, Uttar Pradesh, India', lat: 25.31668, lon: 83.01041, tz: 'Asia/Kolkata'}, offset: null},
  {id: 'p2', name: 'Rohan', relation: 'Spouse', gender: 'Male', date: '1991-11-02', time: '21:10', place: {name: 'Pune, Maharashtra, India', lat: 18.51957, lon: 73.85535, tz: 'Asia/Kolkata'}, offset: null},
  {id: 'p3', name: 'Maa', relation: 'Parent', gender: 'Female', date: '1966-07-21', time: null, place: {name: 'Jaipur, Rajasthan, India', lat: 26.91962, lon: 75.78781, tz: 'Asia/Kolkata'}, offset: null},
];
(async () => {
  const b = await puppeteer.launch({executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox']});
  const p = await b.newPage();
  await p.setViewport({width: 390, height: 844, deviceScaleFactor: 2});
  await p.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}]);
  const shot = async (n, full = false) => { await sleep(700); if (full) { const h = await p.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)); await p.setViewport({width: 390, height: h + 80, deviceScaleFactor: 2}); await sleep(400); } await p.screenshot({path: OUT + n}); if (full) await p.setViewport({width: 390, height: 844, deviceScaleFactor: 2}); console.log('saved', n); };
  // 1. onboarding (fresh device)
  await p.goto(BASE + '/', {waitUntil: 'networkidle0'});
  await p.evaluate(() => localStorage.clear()); await p.reload({waitUntil: 'networkidle0'});
  await p.evaluateHandle('document.fonts.ready');
  await shot('01_onboarding_welcome.png');
  await p.click('#go1'); await p.type('#nm', 'Ananya');
  await p.click('#gen button[data-v="Female"]'); await shot('02_onboarding_name.png');
  await p.click('#go2');
  await p.$eval('#dob', e => { e.value = '1994-03-14'; e.dispatchEvent(new Event('change')); });
  await p.$eval('#tob', e => { e.value = '06:45'; e.dispatchEvent(new Event('change')); });
  await p.type('#pl', 'Varan'); await sleep(900);
  await shot('03_onboarding_birth_city_search.png');
  await p.keyboard.press('ArrowDown'); await p.keyboard.press('Enter'); await sleep(800);
  await shot('04_onboarding_birth_resolved.png');
  // seed several profiles
  await p.evaluate(ps => { localStorage.setItem('dr-profiles', JSON.stringify(ps)); localStorage.setItem('dr-active', JSON.stringify('p1')); localStorage.setItem('dr-lang', '"en"'); localStorage.setItem('dr-style', '"north"'); }, PROFILES);
  await p.reload({waitUntil: 'networkidle0'}); await sleep(1200);
  await shot('05_home.png', true);
  await p.evaluate(() => { S.tab = 'kundli'; render(); }); await sleep(1200);
  await shot('06_chart_d1_north.png');
  await shot('06b_kundli_full_page.png', true);
  await p.evaluate(() => document.querySelector('#sty button[data-v="south"]').click()); await shot('07_chart_d1_south.png');
  await p.evaluate(() => document.querySelector('#div button[data-v="d9"]').click()); await shot('08_chart_d9_south.png');
  await p.evaluate(() => { document.querySelector('#sty button[data-v="north"]').click(); }); await shot('09_chart_d9_north.png');
  // chat, no key
  await p.evaluate(() => { S.tab = 'ask'; render(); }); await sleep(800);
  await shot('10_chat_empty_nokey.png');
  await p.evaluate(() => document.querySelector('#qchips [data-q]').click()); await sleep(1200);
  await shot('11_chat_nokey_reply.png');
  await p.evaluate(() => { S.lang = 'hi'; render(); }); await sleep(800);
  await shot('12_chat_hindi_toggle.png');
  await p.evaluate(() => { S.lang = 'en'; store.set('lang', 'en'); });
  // match
  await p.evaluate(() => { S.tab = 'match'; S.match = {girl: 'p1', boy: 'p2', result: null}; render(); }); await sleep(500);
  await p.click('#doM'); await sleep(1500);
  await shot('13_kundli_matching.png');
  await shot('13b_kundli_matching_full.png', true);
  // profile & wallet
  await p.evaluate(() => { S.tab = 'me'; render(); }); await sleep(800);
  await shot('14_profiles_wallet.png', true);
  await p.goto(BASE + '/privacy.html', {waitUntil: 'networkidle0'}); await shot('15_privacy.png');
  await b.close();
})();
