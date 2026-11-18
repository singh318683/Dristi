/* Personal astrologers. The server turns this on with ASTROLOGER_CONNECT_ENABLED. */
(function () {
  'use strict';
  const $ = (s, root) => (root || document).querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tr = (en, hi) => (typeof S !== 'undefined' && S.lang === 'hi') ? hi : en;
  let timer = 0;
  let clock = 0;
  let live = null;
  let keepCallMusic = false;
  const voiceDebugOn = /(?:\?|&)audiodebug=1(?:&|$)/.test(location.search) || (function () {
    try { return localStorage.getItem('dr-audiodebug') === '1'; } catch (e) { return false; }
  })();
  let portalPoll = 0;
  let titleFlash = 0;
  let beating = false;
  let sending = false;
  let generation = 0;
  const seenIncoming = {};

  function on() {
    return !!(typeof S !== 'undefined' && S.status && S.status.astrologer_connect);
  }
  function detailText(detail) {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail.map(item => {
        if (!item || typeof item !== 'object') return item == null ? '' : String(item);
        const loc = Array.isArray(item.loc) && item.loc.length ? String(item.loc[item.loc.length - 1]) : '';
        const msg = typeof item.msg === 'string' ? item.msg : (typeof item.message === 'string' ? item.message : '');
        if (loc && msg) return loc + ': ' + msg;
        return msg || JSON.stringify(item);
      }).filter(Boolean).join('; ');
    }
    if (detail && typeof detail === 'object') {
      if (typeof detail.message === 'string' && detail.message) return detail.message;
      if (typeof detail.error === 'string' && detail.error) return detail.error;
      try { return JSON.stringify(detail); } catch (e) { return ''; }
    }
    return detail == null ? '' : String(detail);
  }
  function api(path, body, auth) {
    const headers = {'Content-Type': 'application/json'};
    if (auth) headers.Authorization = 'Bearer ' + auth;
    return fetch((window.API || '') + path, {
      method: body ? 'POST' : 'GET',
      cache: 'no-store',
      headers: headers,
      body: body ? JSON.stringify(body) : undefined,
    }).then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const raw = data && data.detail;
        const text = detailText(raw);
        const err = new Error(text || ('HTTP ' + r.status));
        err.code = typeof raw === 'string' ? raw : (raw && typeof raw === 'object' && !Array.isArray(raw) && typeof raw.error === 'string' ? raw.error : '');
        err.status = r.status;
        err.body = data;
        throw err;
      }
      return data;
    });
  }
  function stop() {
    if (timer) clearInterval(timer);
    if (clock) clearInterval(clock);
    timer = 0;
    clock = 0;
    generation += 1;
    if (live && live.room && live.room.disconnect) live.room.disconnect();
    if (!keepCallMusic) {
      const audio = document.getElementById('connectRemoteAudio');
      if (audio) audio.remove();
      const enable = document.getElementById('connectEnableAudio');
      if (enable) enable.remove();
      const voiceDebug = document.getElementById('voiceDebug');
      if (voiceDebug) voiceDebug.remove();
      if (window.drishtiMusicRelease) window.drishtiMusicRelease('call');
    }
    live = null;
    beating = false;
  }
  function sheet(html, opts) {
    const sh = document.getElementById('sheet');
    if (!sh) return;
    sh.classList.add('as-bottom', 'connect-offer');
    sh.classList.toggle('connect-live', !!(opts && opts.live));
    sh.innerHTML = html;
    sh.hidden = false;
    document.body.classList.add('sheet-lock', 'connect-open');
    const root = document.getElementById('root');
    if (root) root.setAttribute('aria-hidden', 'true');
    sh.onclick = e => { if (e.target === sh && window.closeSheet) closeSheet(); };
    const x = sh.querySelector('[data-x]');
    if (x) x.onclick = () => closeSheet();
    const dlg = sh.querySelector('.sheet');
    if (dlg && window.bindSheetSwipe) bindSheetSwipe(dlg);
  }
  function offer() {
    sheet(`<div class="sheet connect" role="dialog" aria-modal="true" aria-labelledby="connectTitle">
      <button type="button" class="sheet-handle" aria-label="${tr('Drag down to close', 'बंद करने के लिए नीचे खींचें')}"><i></i></button>
      <h3 id="connectTitle">${tr('Want deeper analysis?', 'और गहराई से समझना चाहेंगे?')}</h3>
      <p class="sub">${tr('Connect with a personal astrologer.', 'व्यक्तिगत ज्योतिषी से जुड़ें।')}</p>
      <button type="button" class="btn block" id="connectGo">${tr('Connect', 'जुड़ें')}</button>
      <button type="button" class="btn quiet block" id="connectLater">${tr('Maybe later', 'बाद में')}</button>
    </div>`);
    $('#connectGo').onclick = () => list().catch(showErr);
    $('#connectLater').onclick = () => closeSheet();
  }
  const PORTAL_ERRORS = {
    not_approved: ['Your application is waiting for approval. You can go online once it\'s approved.', 'आपका आवेदन स्वीकृति की प्रतीक्षा में है। स्वीकृति के बाद आप ऑनलाइन हो सकते हैं।'],
    stripe_not_configured: ['Payments are not set up on the server yet.', 'सर्वर पर भुगतान अभी सेट नहीं है।'],
    connect_not_enabled: ['Payouts are not switched on yet. Please try again later.', 'भुगतान अभी चालू नहीं हैं। बाद में फिर कोशिश करें।'],
    payout_setup_failed: ['Payout setup could not be completed. Please try again later.', 'भुगतान सेटअप पूरा नहीं हो सका। बाद में फिर कोशिश करें।'],
    no_billing_account: ['Choose a monthly or yearly plan to continue after the free trial.', 'निःशुल्क परीक्षण के बाद जारी रखने के लिए मासिक या वार्षिक योजना चुनें।'],
    trial_unavailable: ['The free trial is not available.', 'निःशुल्क परीक्षण उपलब्ध नहीं है।'],
    unauthorized: ['That sign-in was not accepted.', 'यह साइन-इन स्वीकार नहीं हुआ।'],
    invalid_rate: ['Enter a per-minute rate between $0.30 and $5.00.', 'प्रति मिनट दर $0.30 से $5.00 के बीच डालें।'],
    bad_rate: ['Enter a per-minute rate between $0.30 and $5.00.', 'प्रति मिनट दर $0.30 से $5.00 के बीच डालें।'],
    payment_pending: ['The payment is still pending. Your wallet will update when it clears.', 'भुगतान अभी लंबित है। पूरा होते ही वॉलेट अपडेट होगा।'],
  };
  function friendly(code) {
    const row = PORTAL_ERRORS[code];
    return row ? tr(row[0], row[1]) : '';
  }
  function showErr(e) {
    const code = (e && e.code) || '';
    let msg = friendly(code);
    if (!msg) {
      msg = e && typeof e.message === 'string' ? e.message : '';
      if (!msg || msg === '[object Object]') msg = e && e.body ? detailText(e.body.detail) : detailText(e && e.message);
      msg = friendly(msg) || msg;
      if (msg && /^[a-z][a-z0-9_]*$/.test(msg)) msg = '';
    }
    if (!msg) msg = tr('Something went wrong. Please try again.', 'कुछ गड़बड़ हुई। फिर कोशिश करें।');
    if (window.toast) toast(String(msg).slice(0, 180));
  }
  async function list() {
    const data = await api('/api/connect/astrologers?device=' + encodeURIComponent(DEVICE));
    const rows = (data.astrologers || []).map(a => `<div class="connect-row">
      <div class="connect-av" aria-hidden="true">${esc(a.initial)}</div>
      <div class="connect-main">
        <b>${esc(a.name)}</b>
        <p><span class="connect-dot"></span>${tr('Online', 'ऑनलाइन')} · ${esc(a.rate_label)} · ${esc(a.rating_label)} · ${a.years} ${tr('years', 'वर्ष')}</p>
        <p>${esc((a.specialties || []).join(', '))} · ${esc((a.languages || []).join(', '))}</p>
        <div class="connect-actions">
          <button type="button" class="btn" data-mode="chat" data-id="${esc(a.id)}">${tr('Chat', 'चैट')}</button>
          <button type="button" class="btn quiet" data-mode="call" data-id="${esc(a.id)}">${tr('Call', 'कॉल')}</button>
        </div>
      </div>
    </div>`).join('');
    sheet(`<div class="sheet connect" role="dialog" aria-modal="true" aria-label="${tr('Astrologers', 'ज्योतिषी')}">
      <button type="button" class="sheet-handle" aria-label="${tr('Drag down to close', 'बंद करने के लिए नीचे खींचें')}"><i></i></button>
      <div class="spread"><h3>${tr('Astrologers', 'ज्योतिषी')}</h3><button type="button" class="iconbtn" data-x aria-label="${tr('Close', 'बंद करें')}">×</button></div>
      ${rows || `<div class="connect-empty" id="connectEmpty"><p>${tr('No astrologer is online right now.', 'अभी कोई ज्योतिषी ऑनलाइन नहीं है।')}</p><p class="sub">${tr('Check back in a little while.', 'थोड़ी देर बाद फिर देखें।')}</p></div>`}
    </div>`);
    document.querySelectorAll('#sheet [data-mode]').forEach(b => b.onclick = () => checkout(b.dataset.id, b.dataset.mode).catch(showErr));
  }
  function questionForSession(draft, pending, history) {
    const typed = String(draft || '').trim();
    if (typed) return typed.slice(0, 500);
    const held = String(pending || '').trim();
    if (held) return held.slice(0, 500);
    const hist = Array.isArray(history) ? history : [];
    for (let i = hist.length - 1; i >= 0; i--) {
      const item = hist[i];
      if (!item || item.role !== 'user') continue;
      const text = String(item.content || item.text || '').trim();
      if (text) return text.slice(0, 500);
    }
    return '';
  }
  function lastQuestion() {
    let draft = '';
    let pending = '';
    let hist = [];
    try {
      const box = document.querySelector('#ci');
      draft = box && box.value || '';
    } catch (e) { draft = ''; }
    try { pending = (typeof S !== 'undefined' && S.pendingQ) || ''; } catch (e) { pending = ''; }
    try { hist = store.get(chatKey(), []); } catch (e) { hist = []; }
    return questionForSession(draft, pending, hist);
  }
  function coverPack(needCents) {
    const packs = [{id: 'wallet_5', cents: 500}, {id: 'wallet_10', cents: 1000}, {id: 'wallet_20', cents: 2000}];
    const need = Math.max(0, Number(needCents) || 0);
    const hit = packs.find(p => p.cents >= need);
    return (hit || packs[packs.length - 1]).id;
  }
  function dollars(cents) {
    return '$' + (Math.max(0, Number(cents) || 0) / 100).toFixed(2);
  }
  async function checkout(id, mode) {
    if (mode === 'call') preloadLiveKit();
    const q = await api('/api/connect/quote', {device: DEVICE, astrologer_id: id});
    const title = mode === 'call' ? tr('Voice call', 'वॉइस कॉल') : tr('Chat', 'चैट');
    const short = q.balance_cents < q.hold_cents;
    const need = Math.max(0, (q.hold_cents || 0) - (q.balance_cents || 0));
    const suggested = coverPack(need);
    const packBtn = (pid, label) => `<button type="button" class="btn pack${pid === suggested ? ' suggest' : ''}" data-pack="${pid}">${label}</button>`;
    const startLabel = short
      ? tr('Add money to start', 'शुरू करने के लिए पैसे जोड़ें')
      : (mode === 'call' ? tr('Start call', 'कॉल शुरू करें') : tr('Start chat', 'चैट शुरू करें'));
    sheet(`<div class="sheet connect" role="dialog" aria-modal="true" aria-labelledby="checkTitle">
      <button type="button" class="sheet-handle" aria-label="${tr('Drag down to close', 'बंद करने के लिए नीचे खींचें')}"><i></i></button>
      <h3 id="checkTitle">${esc(title)} · ${esc(q.name)}</h3>
      <p>${esc(q.rate_label)} · ${tr('the same rate for chat and call', 'चैट और कॉल दोनों का एक ही शुल्क')}</p>
      <p>${tr('A 5-minute hold', '5 मिनट की होल्ड')} · ${esc(q.hold_label)}. ${tr('Wallet balance', 'वॉलेट')} ${esc(q.balance_label)}.</p>
      <p class="sub">${tr('Your question and birth chart are shared with the astrologer. Billed per minute. End anytime. Unused hold returns to your wallet.', 'आपका प्रश्न और जन्म कुंडली ज्योतिषी के साथ साझा होती है। शुल्क प्रति मिनट। कभी भी समाप्त करें। बची होल्ड वॉलेट में लौटती है।')}</p>
      <p class="sub">${tr('Guidance for reflection, not medical, legal or financial advice.', 'यह चिंतन हेतु मार्गदर्शन है, चिकित्सा, कानूनी या वित्तीय सलाह नहीं।')}</p>
      ${short ? `<div class="connect-packs">${packBtn('wallet_5', '$5')}${packBtn('wallet_10', '$10')}${packBtn('wallet_20', '$20')}</div>
      <p class="sub" id="needMore">${tr('Add at least ' + dollars(need) + ' to start. Tap an amount above.', 'शुरू करने के लिए कम से कम ' + dollars(need) + ' जोड़ें। ऊपर कोई राशि चुनें।')}</p>` : ''}
      <button type="button" class="btn block" id="connectStart">${startLabel}</button>
    </div>`);
    document.querySelectorAll('#sheet [data-pack]').forEach(b => b.onclick = () => topup(b.dataset.pack).then(() => checkout(id, mode)).catch(showErr));
    const start = $('#connectStart');
    if (start) start.onclick = () => {
      if (short) topup(suggested).then(() => checkout(id, mode)).catch(showErr);
      else {
        if (mode === 'call') {
          preloadLiveKit();
          if (window.drishtiMusicHold) window.drishtiMusicHold('call');
          primeCallAudio();
        }
        begin(id, mode).catch(err => {
          if (mode === 'call' && window.drishtiMusicRelease) window.drishtiMusicRelease('call');
          showErr(err);
        });
      }
    };
  }
  function clearPayForm() {
    const box = document.getElementById('stripePay');
    if (box) box.remove();
    const pay = document.getElementById('stripePayBtn');
    if (pay) pay.remove();
  }
  function payLog(step, err) {
    console.error('pay:' + step, err);
  }
  function withTimeout(promise, ms, step) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const err = new Error('timeout');
        err.code = 'timeout';
        err.step = step;
        payLog(step, err);
        reject(err);
      }, ms);
      Promise.resolve(promise).then(value => {
        clearTimeout(timer);
        resolve(value);
      }, err => {
        clearTimeout(timer);
        if (err) err.step = err.step || step;
        payLog(step, err);
        reject(err);
      });
    });
  }
  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
  async function waitForBalance(startCents, signal) {
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      await sleep(3000);
      if (signal.done) return false;
      try {
        const view = await api('/api/connect/wallet?device=' + encodeURIComponent(DEVICE));
        if ((Number(view.balance_cents) || 0) > startCents) return true;
      } catch (err) {
        payLog('wallet-poll', err);
      }
    }
    return false;
  }
  function slowPayError() {
    return new Error(tr(
      'Payment is taking longer than usual. If your card was charged, your balance will update shortly.',
      'भुगतान में सामान्य से अधिक समय लग रहा है। अगर कार्ड से पैसे कट गए, तो बैलेंस जल्दी अपडेट हो जाएगा।'
    ));
  }
  function showPayBusy(pay) {
    pay.disabled = true;
    pay.setAttribute('aria-busy', 'true');
    pay.innerHTML = '<span class="pay-spin" aria-hidden="true"></span>' + tr('Processing…', 'प्रक्रिया जारी…');
  }
  function clearPayBusy(pay) {
    if (!pay || !pay.isConnected) return;
    pay.disabled = false;
    pay.removeAttribute('aria-busy');
    pay.textContent = tr('Pay', 'भुगतान');
  }
  function setPacksDisabled(disabled) {
    document.querySelectorAll('#sheet [data-pack], #connectStart').forEach(b => { b.disabled = !!disabled; });
  }
  async function topup(product) {
    clearPayForm();
    setPacksDisabled(true);
    try {
      const key = S.status && S.status.stripe_publishable_key;
      const intent = await api('/api/connect/wallet/intent', {device: DEVICE, product_id: product, idempotency_key: (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()))});
      if (!key || !intent.client_secret) throw new Error(tr('Card payments are not set up yet.', 'कार्ड भुगतान अभी सेट नहीं है।'));
      if (!window.Stripe) {
        await new Promise((resolve, reject) => {
          const s = document.createElement('script');
          s.src = 'https://js.stripe.com/v3/';
          s.onload = resolve;
          s.onerror = () => reject(new Error('stripe'));
          document.head.appendChild(s);
        });
      }
      clearPayForm();
      const stripe = window.Stripe(key);
      const elements = stripe.elements({clientSecret: intent.client_secret});
      const box = document.createElement('div');
      box.id = 'stripePay';
      const host = document.querySelector('#sheet .sheet');
      host.appendChild(box);
      elements.create('payment', {wallets: {applePay: 'never', googlePay: 'never'}}).mount('#stripePay');
      const pay = document.createElement('button');
      pay.type = 'button';
      pay.id = 'stripePayBtn';
      pay.className = 'btn block';
      pay.textContent = tr('Pay', 'भुगतान');
      host.appendChild(pay);
      const returnUrl = intent.return_url || (location.origin + '/#wallet');
      await new Promise((resolve, reject) => {
        pay.onclick = async () => {
          if (pay.disabled) return;
          showPayBusy(pay);
          const signal = {done: false};
          let settled = false;
          const finish = (err, value) => {
            if (settled) return;
            settled = true;
            signal.done = true;
            if (err) {
              clearPayBusy(pay);
              setPacksDisabled(false);
              reject(err);
            } else resolve(value);
          };
          let startCents = 0;
          try {
            const startView = await api('/api/connect/wallet?device=' + encodeURIComponent(DEVICE));
            startCents = Number(startView.balance_cents) || 0;
          } catch (err) {
            payLog('wallet-before', err);
          }
          const poll = waitForBalance(startCents, signal).then(ok => {
            if (ok) finish(null, {credited: true});
            return ok;
          }).catch(err => {
            payLog('wallet-poll', err);
            return false;
          });
          try {
            const result = await withTimeout(stripe.confirmPayment({
              elements: elements,
              redirect: 'if_required',
              confirmParams: {return_url: returnUrl},
            }), 30000, 'confirmPayment');
            if (settled) return;
            if (result.error) throw new Error(result.error.message);
            const pi = result.paymentIntent || {};
            if (!pi.id || (pi.status && pi.status !== 'succeeded')) {
              const err = new Error('payment_pending');
              err.code = 'payment_pending';
              throw err;
            }
            await withTimeout(api('/api/connect/wallet/confirm', {device: DEVICE, payment_intent: pi.id}), 30000, 'wallet-confirm');
            finish(null, result);
          } catch (err) {
            if (settled) return;
            const step = (err && err.step) || 'confirmPayment';
            payLog(step, err);
            if (err && err.code === 'timeout') {
              const ok = await poll;
              if (ok) { finish(null, {credited: true}); return; }
              finish(slowPayError());
              return;
            }
            finish(err);
          }
        };
      });
    } catch (err) {
      setPacksDisabled(false);
      throw err;
    }
  }
  async function begin(id, mode) {
    const key = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
    let profile = null;
    try { profile = profileBody(activeProfile()); } catch (e) { profile = null; }
    const session = await api('/api/connect/sessions', {
      device: DEVICE, astrologer_id: id, mode: mode, question: lastQuestion(), profile: profile, idempotency_key: key,
    });
    if (mode === 'call') openCall(session);
    else openChat(session);
  }
  function clockReading(session, nowMs) {
    const now = typeof nowMs === 'number' ? nowMs : Date.now();
    if (!session || session.status !== 'active' || !session.started_at) {
      return {time: '00:00', cost: '$0.00', ticking: false};
    }
    const start = Date.parse(session.started_at);
    if (isNaN(start)) return {time: '00:00', cost: '$0.00', ticking: false};
    const sec = Math.max(0, Math.floor((now - start) / 1000));
    const mm = String(Math.floor(sec / 60)).padStart(2, '0');
    const ss = String(sec % 60).padStart(2, '0');
    return {time: mm + ':' + ss, cost: session.cost_label || '$0.00', ticking: true};
  }
  function paintClock() {
    if (!live) return;
    const reading = clockReading(live.session);
    const node = $('#connectClock');
    const cost = $('#connectCost');
    if (node) node.textContent = reading.time;
    if (cost) cost.textContent = reading.cost;
    paintVoiceDebug(live.room);
  }
  function syncClock() {
    const ticking = !!(live && clockReading(live.session).ticking);
    if (!ticking) {
      if (clock) clearInterval(clock);
      clock = 0;
      paintClock();
      return;
    }
    paintClock();
    if (clock) return;
    clock = setInterval(paintClock, 1000);
  }
  function watch(session, role, token) {
    keepCallMusic = !!(session && session.mode === 'call');
    stop();
    keepCallMusic = false;
    if (session && session.mode === 'call' && window.drishtiMusicHold) window.drishtiMusicHold('call');
    live = {id: session.id, room: null, role: role || 'user', token: token || null, session: session, messages: [], gen: generation};
    syncClock();
    timer = setInterval(() => beat().catch(() => {}), 3000);
  }
  function paintMeter(view) {
    const fee = $('#connectFee');
    const net = $('#connectNet');
    if (fee) fee.textContent = view.fee_label || '';
    if (net) net.textContent = view.net_label || '';
  }
  async function beat() {
    if (!live || beating) return;
    beating = true;
    const gen = live.gen;
    const id = live.id;
    const astro = live.role === 'astrologer';
    const token = live.token;
    try {
      const body = astro ? {role: 'astrologer', device: ''} : {role: 'user', device: DEVICE};
      const view = await api('/api/connect/sessions/' + id + '/heartbeat', body, token);
      if (!live || live.gen !== gen) return;
      live.session = view;
      syncClock();
      paintMeter(view);
      if (view.status === 'ended') {
        stop();
        if (astro) showAstroSummary(view);
        else {
          const missed = view.end_reason === 'unanswered';
          if (window.toast) toast(missed
            ? tr('No one accepted in time. Your hold was returned.', 'समय पर कोई स्वीकार नहीं किया। होल्ड लौटा दी गई।')
            : tr('Session ended', 'सत्र समाप्त') + ' · ' + (view.cost_label || ''));
          closeSheet();
        }
        return;
      }
      if (view.status === 'active' && view.mode === 'call') joinVoice();
      if ($('#connectLog')) {
        const q = (astro ? 'after=0' : ('device=' + encodeURIComponent(DEVICE) + '&after=0')) + '&t=' + Date.now();
        const msgs = await api('/api/connect/sessions/' + id + '/messages?' + q, null, token);
        if (!live || live.gen !== gen) return;
        showMessages(msgs && msgs.messages);
      }
    } finally {
      beating = false;
    }
  }
  function mergeById(current, incoming) {
    const map = new Map();
    (current || []).forEach(item => {
      if (!item || item.id == null || (item.sender !== 'user' && item.sender !== 'astrologer')) return;
      map.set(String(item.id), {id: String(item.id), sender: item.sender, body: String(item.body || '')});
    });
    (incoming || []).forEach(item => {
      if (!item || item.id == null || (item.sender !== 'user' && item.sender !== 'astrologer')) return;
      const id = String(item.id);
      const body = String(item.body || '');
      if (id.indexOf('tmp-') !== 0) {
        for (const [key, prev] of map) {
          if (key.indexOf('tmp-') === 0 && prev.sender === item.sender && prev.body === body) {
            map.delete(key);
            break;
          }
        }
      }
      map.set(id, {id: id, sender: item.sender, body: body});
    });
    return Array.from(map.values()).sort((a, b) => {
      const aTmp = a.id.indexOf('tmp-') === 0;
      const bTmp = b.id.indexOf('tmp-') === 0;
      if (aTmp !== bTmp) return aTmp ? 1 : -1;
      return (Number(a.id) || 0) - (Number(b.id) || 0);
    });
  }
  function showMessages(incoming, dropId) {
    if (!live) return;
    let next = mergeById(live.messages, incoming);
    if (dropId) next = next.filter(item => item.id !== String(dropId));
    live.messages = next;
    renderThread();
  }
  function mineRole() {
    return live && live.role === 'astrologer' ? 'astrologer' : 'user';
  }
  function otherName() {
    const session = (live && live.session) || {};
    if (live && live.role === 'astrologer') return seekerLabel(session);
    return session.astrologer_name || tr('Astrologer', 'ज्योतिषी');
  }
  function bubbleNode(m) {
    const node = document.createElement('div');
    const mine = m.sender === mineRole();
    node.className = 'bubble ' + (mine ? 'me' : 'them');
    node.setAttribute('data-id', String(m.id || ''));
    node.setAttribute('data-sender', m.sender || '');
    node.setAttribute('data-body', m.body || '');
    if (String(m.id || '').indexOf('tmp-') === 0) node.setAttribute('data-pending', '1');
    if (!mine) {
      const who = document.createElement('span');
      who.className = 'who';
      who.textContent = otherName();
      node.appendChild(who);
    }
    const p = document.createElement('p');
    p.textContent = m.body || '';
    node.appendChild(p);
    return node;
  }
  function renderThread() {
    const log = $('#connectLog');
    if (!log || !live) return;
    const frag = document.createDocumentFragment();
    const items = live.messages || [];
    if (!items.length && !(live.session && live.session.status === 'active')) {
      const wait = document.createElement('p');
      wait.className = 'them';
      wait.textContent = tr('Waiting for them to join.', 'उनके जुड़ने की प्रतीक्षा।');
      frag.appendChild(wait);
    }
    items.forEach(m => frag.appendChild(bubbleNode(m)));
    const nearEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 48;
    log.textContent = '';
    log.appendChild(frag);
    if (nearEnd) log.scrollTop = log.scrollHeight;
  }
  function openChat(session) {
    if (live && live.id === session.id && live.role !== 'astrologer' && $('#connectLog')) {
      live.session = session;
      syncClock();
      showMessages([]);
      return;
    }
    watch(session);
    sheet(`<div class="sheet connect-live" role="dialog" aria-modal="true" aria-label="${esc(session.astrologer_name)}">
      <div class="connect-head">
        <div class="connect-av" aria-hidden="true">${esc((session.astrologer_name || '?').slice(0, 1))}</div>
        <div class="grow"><b>${esc(session.astrologer_name)}</b><span class="connect-meter"><span id="connectClock">00:00</span> · <span id="connectCost">${esc(session.cost_label)}</span></span></div>
        <button type="button" class="btn quiet" id="connectEnd">${tr('End', 'समाप्त')}</button>
      </div>
      <div class="connect-log" id="connectLog"></div>
      <form id="connectForm" class="ask-bar"><textarea id="connectInput" rows="1" maxlength="2000" aria-label="${tr('Message', 'संदेश')}"></textarea>
        <button type="submit" class="btn" id="connectSend">${tr('Send', 'भेजें')}</button></form>
      <button type="button" class="btn quiet" id="connectReport">${tr('Report', 'रिपोर्ट')}</button>
      <button type="button" class="btn quiet" id="connectBlock">${tr('Block', 'ब्लॉक')}</button>
    </div>`, {live: true});
    $('#connectEnd').onclick = () => finish().catch(showErr);
    $('#connectForm').onsubmit = e => { e.preventDefault(); send().catch(showErr); };
    $('#connectReport').onclick = () => report(session).catch(showErr);
    $('#connectBlock').onclick = () => block(session).catch(showErr);
    paintClock();
    showMessages([]);
  }
  function lockSend(locked) {
    const btn = $('#connectSend');
    const input = $('#connectInput');
    if (btn) btn.disabled = locked;
    if (input) input.disabled = locked;
  }
  async function postChat(sender, token, device) {
    const input = $('#connectInput');
    const text = ((input && input.value) || '').trim();
    if (!text || !live || sending) return;
    sending = true;
    lockSend(true);
    const gen = live.gen;
    const sessionId = live.id;
    input.value = '';
    const tmp = 'tmp-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    showMessages([{id: tmp, sender: sender, body: text}]);
    try {
      const saved = await api('/api/connect/sessions/' + sessionId + '/messages', {sender: sender, body: text, device: device}, token);
      if (live && live.gen === gen && saved && saved.id != null) showMessages([{id: saved.id, sender: sender, body: text}]);
    } catch (err) {
      if (live && live.gen === gen) showMessages([], tmp);
      if (input && input.isConnected && !input.value) input.value = text;
      throw err;
    } finally {
      sending = false;
      lockSend(false);
    }
  }
  async function send() {
    await postChat('user', null, DEVICE);
  }
  function requireVoiceRoom() {
    const person = live && live.room && live.room.localParticipant;
    if (person) return person;
    setVoiceNote(tr('The voice room is not connected yet.', 'वॉइस रूम अभी जुड़ा नहीं है।'));
    return null;
  }
  function bindMute(button) {
    if (!button) return;
    button.onclick = ev => {
      const person = requireVoiceRoom();
      if (!person) return;
      const pressed = ev.currentTarget.getAttribute('aria-pressed') === 'true';
      ev.currentTarget.setAttribute('aria-pressed', pressed ? 'false' : 'true');
      person.setMicrophoneEnabled(pressed).catch(err => setVoiceNote(voiceErrorText(err)));
    };
  }
  function bindSpeaker(button) {
    if (!button) return;
    button.onclick = ev => {
      if (!requireVoiceRoom()) return;
      const pressed = ev.currentTarget.getAttribute('aria-pressed') !== 'true';
      ev.currentTarget.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    };
  }
  function setVoiceNote(text) {
    const note = $('#connectVoice');
    if (note) note.textContent = text || '';
  }
  function voiceErrorText(err) {
    const code = (err && (err.code || err.name)) || '';
    const msg = (err && err.message) || '';
    if (code === 'call_unavailable') return tr('Voice is not set up on the server yet. Ending still stops the bill.', 'वॉइस सर्वर पर अभी सेट नहीं है। समाप्त करने पर शुल्क रुक जाता है।');
    if (code === 'call_not_ready') return tr('Waiting for the call to start.', 'कॉल शुरू होने की प्रतीक्षा।');
    if (code === 'NotAllowedError' || code === 'PermissionDeniedError' || code === 'SecurityError' || /not allowed|permission|microphone|setMutedState|getUserMedia/i.test(msg)) {
      return tr('The microphone is blocked. Allow the microphone for Drishti, then try again.', 'माइक्रोफ़ोन ब्लॉक है। दृष्टि के लिए माइक्रोफ़ोन अनुमति दें, फिर कोशिश करें।');
    }
    return tr('The voice room could not be reached. Check your connection.', 'वॉइस रूम तक नहीं पहुँच सके। कनेक्शन जाँचें।');
  }
  let LiveKit = null;
  let primedRoom = null;
  function preloadLiveKit() {
    if (LiveKit) return;
    import('https://cdn.jsdelivr.net/npm/livekit-client@2.9.0/+esm').then(mod => { LiveKit = mod; }).catch(() => {});
  }
  function ensureRemoteAudio() {
    let audio = document.getElementById('connectRemoteAudio');
    if (!audio) {
      audio = document.createElement('audio');
      audio.id = 'connectRemoteAudio';
      audio.autoplay = true;
      audio.playsInline = true;
      audio.setAttribute('playsinline', '');
      audio.setAttribute('webkit-playsinline', '');
      audio.setAttribute('autoplay', '');
    }
    const host = document.querySelector('.call-screen') || document.getElementById('astroSession') || document.body;
    if (audio.parentNode !== host) host.appendChild(audio);
    return audio;
  }
  function primeCallAudio() {
    const audio = ensureRemoteAudio();
    try { const played = audio.play(); if (played && played.catch) played.catch(() => {}); } catch (e) { /* the Accept tap retries */ }
    if (!LiveKit || !LiveKit.Room) return;
    if (!primedRoom) primedRoom = new LiveKit.Room();
    try {
      const started = primedRoom.startAudio && primedRoom.startAudio();
      if (started && started.catch) started.catch(() => {});
    } catch (e) { /* startAudio is tried again after the room connects */ }
  }
  function countRemoteAudio(room) {
    let people = 0, tracks = 0;
    if (!room || !room.remoteParticipants || !room.remoteParticipants.forEach) return {people: 0, tracks: 0};
    room.remoteParticipants.forEach(person => {
      people += 1;
      if (!person.trackPublications || !person.trackPublications.forEach) return;
      person.trackPublications.forEach(pub => {
        const kind = pub && (pub.kind || (pub.track && pub.track.kind));
        if (kind === 'audio' && pub.isSubscribed !== false) tracks += 1;
      });
    });
    return {people: people, tracks: tracks};
  }
  function paintVoiceDebug(room) {
    if (!voiceDebugOn) return;
    let el = document.getElementById('voiceDebug');
    if (!el) {
      el = document.createElement('p');
      el.id = 'voiceDebug';
      el.className = 'sub';
    }
    const host = document.querySelector('.call-screen') || document.getElementById('astroSession') || document.body;
    if (el.parentNode !== host) host.appendChild(el);
    const counts = countRemoteAudio(room);
    const playback = room && typeof room.canPlaybackAudio !== 'undefined' ? String(room.canPlaybackAudio) : 'n/a';
    el.textContent = 'room ' + ((room && room.state) || 'none')
      + ' · remotes ' + counts.people
      + ' · audio ' + counts.tracks
      + ' · playback ' + playback;
  }
  function attachRemoteAudio(track) {
    if (!track || track.kind !== 'audio' || !track.attach) return;
    const audio = ensureRemoteAudio();
    track.attach(audio);
    try { const played = audio.play(); if (played && played.catch) played.catch(() => {}); } catch (e) { /* Tap to enable audio retries playback */ }
    paintVoiceDebug(live && live.room);
  }
  function showTapAudio(room) {
    if (document.getElementById('connectEnableAudio')) return;
    const host = document.getElementById('connectVoice');
    const btn = document.createElement('button');
    btn.id = 'connectEnableAudio';
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = tr('Tap to enable audio', 'आवाज़ चालू करने के लिए टैप करें');
    if (host && host.parentNode) host.parentNode.insertBefore(btn, host.nextSibling);
    else document.body.appendChild(btn);
    btn.onclick = async () => {
      try {
        if (room.startAudio) await room.startAudio();
        const audio = document.getElementById('connectRemoteAudio');
        if (audio && audio.play) audio.play().catch(() => {});
        if (room.localParticipant) await room.localParticipant.setMicrophoneEnabled(true);
        btn.remove();
        setVoiceNote('');
      } catch (err) {
        setVoiceNote(voiceErrorText(err));
      }
    };
  }
  async function publishMic(room) {
    if (!room || !room.localParticipant) return;
    await room.localParticipant.setMicrophoneEnabled(true);
  }
  function watchPlayback(room, mod) {
    const onPlayback = playing => {
      paintVoiceDebug(room);
      if (playing === false || room.canPlaybackAudio === false) showTapAudio(room);
      else {
        const btn = document.getElementById('connectEnableAudio');
        if (btn) btn.remove();
      }
    };
    const named = mod && mod.RoomEvent && mod.RoomEvent.AudioPlaybackStatusChanged;
    room.on(named || 'audioPlaybackStatusChanged', onPlayback);
  }
  async function attachVoice(info) {
    const mod = LiveKit || await import('https://cdn.jsdelivr.net/npm/livekit-client@2.9.0/+esm');
    LiveKit = mod;
    const room = primedRoom || new mod.Room();
    primedRoom = null;
    const onTrack = track => attachRemoteAudio(track);
    if (mod.RoomEvent && mod.RoomEvent.TrackSubscribed) room.on(mod.RoomEvent.TrackSubscribed, onTrack);
    else room.on('trackSubscribed', onTrack);
    watchPlayback(room, mod);
    if (room.state !== 'connected') await room.connect(info.url, info.token);
    if (live) live.room = room;
    ensureRemoteAudio();
    if (room.remoteParticipants && room.remoteParticipants.forEach) {
      room.remoteParticipants.forEach(person => {
        if (!person.trackPublications) return;
        person.trackPublications.forEach(pub => { if (pub && pub.track) attachRemoteAudio(pub.track); });
      });
    }
    paintVoiceDebug(room);
    let micReady = true;
    try {
      await publishMic(room);
    } catch (err) {
      micReady = false;
      setVoiceNote('');
      showTapAudio(room);
    }
    if (room.canPlaybackAudio === false) {
      showTapAudio(room);
      return;
    }
    if (room.startAudio) {
      try { await room.startAudio(); } catch (err) { showTapAudio(room); return; }
    }
    if (micReady && room.canPlaybackAudio !== false) setVoiceNote('');
    paintVoiceDebug(room);
  }
  async function joinVoice() {
    if (!live || live.voiceJoined || live.joining) return;
    if (!live.session || live.session.mode !== 'call' || live.session.status !== 'active') return;
    live.joining = true;
    setVoiceNote(tr('Connecting to the call.', 'कॉल से जुड़ रहे हैं।'));
    const gen = live.gen;
    const id = live.id;
    const astro = live.role === 'astrologer';
    const auth = astro ? live.token : null;
    let delay = 1000;
    try {
      for (let attempt = 0; attempt < 6; attempt++) {
        if (!live || live.gen !== gen) return;
        try {
          const info = await api('/api/connect/sessions/' + id + '/call-token', astro ? {device: ''} : {device: DEVICE}, auth);
          if (!live || live.gen !== gen) return;
          live.voiceJoined = true;
          await attachVoice(info);
          return;
        } catch (err) {
          if (err && err.code === 'call_not_ready' && attempt < 5) {
            setVoiceNote(voiceErrorText(err));
            await sleep(delay);
            delay = Math.min(delay * 2, 8000);
            continue;
          }
          setVoiceNote(voiceErrorText(err));
          return;
        }
      }
    } finally {
      if (live && live.gen === gen) live.joining = false;
    }
  }
  async function openCall(session) {
    watch(session);
    sheet(`<div class="sheet connect-live" role="dialog" aria-modal="true" aria-label="${esc(session.astrologer_name)}">
      <div class="call-screen">
        <div class="connect-av" aria-hidden="true">${esc((session.astrologer_name || '?').slice(0, 1))}</div>
        <b>${esc(session.astrologer_name)}</b>
        <span class="connect-meter"><span id="connectClock">00:00</span> · <span id="connectCost">${esc(session.cost_label)}</span></span>
        <p class="sub" id="connectVoice">${tr('Waiting for the call to start.', 'कॉल शुरू होने की प्रतीक्षा।')}</p>
        <div class="call-controls">
          <button type="button" class="btn quiet" id="connectMute" aria-pressed="false">${tr('Mute', 'म्यूट')}</button>
          <button type="button" class="btn quiet" id="connectSpeaker" aria-pressed="false">${tr('Speaker', 'स्पीकर')}</button>
          <button type="button" class="btn" id="connectEnd">${tr('End', 'समाप्त')}</button>
        </div>
      </div>
    </div>`, {live: true});
    $('#connectEnd').onclick = () => finish().catch(showErr);
    bindMute($('#connectMute'));
    bindSpeaker($('#connectSpeaker'));
    paintClock();
    if (session.status === 'active') joinVoice();
  }
  async function finish() {
    if (!live) return;
    const id = live.id;
    stop();
    await api('/api/connect/sessions/' + id + '/end', {device: DEVICE});
    closeSheet();
  }
  async function report(session) {
    const reason = tr('Report from the session', 'सत्र से रिपोर्ट');
    await api('/api/connect/report', {device: DEVICE, astrologer_id: session.astrologer_id, session_id: session.id, reason: reason});
    if (window.toast) toast(tr('Report sent', 'रिपोर्ट भेज दी गई'));
  }
  async function block(session) {
    await api('/api/connect/block', {device: DEVICE, astrologer_id: session.astrologer_id});
    if (window.toast) toast(tr('Astrologer blocked', 'ज्योतिषी ब्लॉक हो गए'));
    finish().catch(showErr);
  }
  function portalRoot(title) {
    const root = document.getElementById('root');
    root.innerHTML = `<div class="portal"><div class="spread"><button type="button" class="btn quiet" id="portalBack">${tr('Back', 'वापस')}</button><b>${esc(title)}</b></div><div id="portalBody"></div></div>`;
    document.body.classList.remove('app-fit');
    const back = $('#portalBack');
    if (back) back.onclick = () => { if (location.hash) location.hash = ''; };
    return $('#portalBody');
  }
  function planCards() {
    const days = (S.status && S.status.provider_trial_days) || 90;
    const month = (S.status && S.status.provider_monthly_label) || '$9.99';
    const year = (S.status && S.status.provider_yearly_label) || '$99';
    const low = (S.status && S.status.rate_min_cents) || 30;
    const high = (S.status && S.status.rate_max_cents) || 500;
    return {days, month, year, low, high};
  }
  function startPaidPlan(token, interval) {
    return api('/api/connect/astro/plan', {interval: interval}, token).then(d => { if (d.url) location.href = d.url; });
  }
  function bindPaidButtons(token, monthId, yearId) {
    const month = $(monthId || '#planMonth');
    const year = $(yearId || '#planYear');
    if (month) month.onclick = () => startPaidPlan(token, 'month').catch(showErr);
    if (year) year.onclick = () => startPaidPlan(token, 'year').catch(showErr);
  }
  function trialNote(money) {
    const until = money && money.renews_at ? String(money.renews_at).slice(0, 10) : '';
    if (!until) return tr('Choose a plan to keep your listing after the free trial ends.', 'निःशुल्क परीक्षण के बाद सूची बनाए रखने के लिए एक योजना चुनें।');
    return tr(
      'You\'re on the free trial until ' + until + '. Choose a plan to keep your listing after it ends.',
      'आप ' + until + ' तक निःशुल्क परीक्षण पर हैं। समाप्त होने के बाद सूची बनाए रखने के लिए एक योजना चुनें।'
    );
  }
  function paidPlanHtml(money) {
    const plans = planCards();
    return `<div class="card" id="paidPlans"><h3>${tr('Choose a plan', 'योजना चुनें')}</h3>
      <p class="sub">${esc(trialNote(money))}</p>
      <button type="button" class="btn block" id="paidMonth">${tr('Monthly', 'मासिक')} ${esc(plans.month)}</button>
      <button type="button" class="btn block" id="paidYear">${tr('Yearly', 'वार्षिक')} ${esc(plans.year)}</button>
      <p class="sub">${tr('A card is needed only for the paid plan. It starts when the free trial ends.', 'कार्ड केवल सशुल्क योजना के लिए चाहिए। यह निःशुल्क परीक्षण समाप्त होने पर शुरू होती है।')}</p></div>`;
  }
  function revealPaidPlans(money, token) {
    if ($('#paidPlans')) return;
    const earn = $('#earnings');
    const html = paidPlanHtml(money);
    if (earn) earn.insertAdjacentHTML('beforebegin', html);
    else $('#portalBody').insertAdjacentHTML('beforeend', html);
    bindPaidButtons(token, '#paidMonth', '#paidYear');
  }
  function registerForm() {
    const p = planCards();
    return `<form class="card" id="astroRegister"><h3>${tr('Register as a live astrologer', 'लाइव ज्योतिषी बनें')}</h3>
      <input class="input" id="regName" aria-label="${tr('Name', 'नाम')}" placeholder="${tr('Name', 'नाम')}">
      <input class="input" id="regEmail" type="email" autocomplete="username" aria-label="${tr('Email', 'ईमेल')}" placeholder="${tr('Email', 'ईमेल')}">
      <input class="input" id="regPass" type="password" autocomplete="new-password" aria-label="${tr('Password', 'पासवर्ड')}" placeholder="${tr('Password', 'पासवर्ड')}">
      <input class="input" id="regLang" aria-label="${tr('Languages', 'भाषाएँ')}" placeholder="${tr('Languages', 'भाषाएँ')}">
      <input class="input" id="regSpec" aria-label="${tr('Specialities', 'विशेषज्ञता')}" placeholder="${tr('Specialities', 'विशेषज्ञता')}">
      <input class="input" id="regYears" inputmode="numeric" aria-label="${tr('Years of experience', 'अनुभव (वर्ष)')}" placeholder="${tr('Years of experience', 'अनुभव (वर्ष)')}">
      <textarea class="input" id="regBio" rows="3" aria-label="${tr('Bio', 'परिचय')}" placeholder="${tr('A short bio', 'छोटा परिचय')}"></textarea>
      <input class="input" id="regRate" inputmode="decimal" aria-label="${tr('Your rate per minute', 'आपका प्रति मिनट शुल्क')}" placeholder="${tr('Rate per minute, e.g. 1.00', 'प्रति मिनट शुल्क, जैसे 1.00')}">
      <p class="sub">${tr('Same rate for chat and call.', 'चैट और कॉल का एक ही शुल्क।')} $${(p.low / 100).toFixed(2)}–$${(p.high / 100).toFixed(2)}</p>
      <label class="sub">${tr('Photo', 'फ़ोटो')}<input id="regPhoto" type="file" accept="image/*"></label>
      <button class="btn block" type="submit">${tr('Create account', 'खाता बनाएँ')}</button>
      <button class="btn quiet" type="button" id="showLogin">${tr('I already have an account', 'मेरा खाता पहले से है')}</button></form>`;
  }
  function seekerLabel(session) {
    return (session && session.seeker_name) || tr('Seeker', 'साधक');
  }
  function chartLine(chart) {
    if (!chart || typeof chart !== 'object') return '';
    const place = chart.place && typeof chart.place === 'object' ? (chart.place.name || '') : (chart.place || '');
    return [chart.date || '', chart.time || '', place].filter(Boolean).join(' · ');
  }
  function paintOnline(desk) {
    const box = $('#astroOnline');
    const label = $('#onlineLabel');
    if (!box) return;
    if (desk && desk.in_session) {
      box.checked = false;
      box.disabled = true;
      if (label) label.textContent = tr('In a session', 'सत्र में');
    } else {
      box.disabled = false;
      box.checked = !!(desk && desk.online);
      if (label) label.textContent = document.body.classList.contains('astro-mode') ? tr('Available', 'उपलब्ध') : tr('Online', 'ऑनलाइन');
    }
  }
  function showAstroSummary(view) {
    stopPortalPoll();
    const body = $('#portalBody');
    if (!body) { render(); return; }
    const elapsed = view.elapsed_seconds || 0;
    const clock = String(Math.floor(elapsed / 60)).padStart(2, '0') + ':' + String(elapsed % 60).padStart(2, '0');
    body.innerHTML = `<div class="card astro-summary"><h3>${tr('Session ended', 'सत्र समाप्त')}</h3>
      <p>${tr('Duration', 'अवधि')} ${clock}</p>
      <p>${tr('Gross', 'कुल')} ${esc(view.cost_label || '$0.00')}</p>
      <p>${tr('Drishti fee', 'दृष्टि शुल्क')} ${esc(view.fee_label || '$0.00')}</p>
      <p><b>${tr('Net', 'शुद्ध')} ${esc(view.net_label || '$0.00')}</b></p>
      <button type="button" class="btn block" id="astroDone">${tr('Back to dashboard', 'डैशबोर्ड पर वापस')}</button></div>`;
    const done = $('#astroDone');
    if (done) done.onclick = () => render();
  }
  async function sendAstro(token) {
    await postChat('astrologer', token, '');
  }
  async function finishAstro(token) {
    if (!live) { render(); return; }
    const id = live.id;
    stop();
    const view = await api('/api/connect/sessions/' + id + '/end', {device: ''}, token);
    showAstroSummary(view);
  }
  function openAstroSession(session, token) {
    if (live && live.role === 'astrologer' && live.id === session.id && $('#connectLog')) {
      live.session = session;
      live.token = token;
      syncClock();
      paintMeter(session);
      showMessages([]);
      return;
    }
    stopPortalPoll();
    const body = $('#portalBody') || portalRoot(tr('Session', 'सत्र'));
    const who = seekerLabel(session);
    const asked = (session.question || '').trim() || tr('No question yet', 'अभी कोई प्रश्न नहीं');
    const born = chartLine(session.chart);
    const call = session.mode === 'call';
    body.innerHTML = `<div class="astro-session" id="astroSession">
      <div class="connect-head">
        <div class="connect-av" aria-hidden="true">${esc(who.slice(0, 1) || '?')}</div>
        <div class="grow"><b>${esc(who)}</b> <span class="tag">${tr('In a session', 'सत्र में')}</span>
          <span class="connect-meter"><span id="connectClock">00:00</span> · ${tr('Gross', 'कुल')} <span id="connectCost">${esc(session.cost_label || '$0.00')}</span> · ${tr('Fee', 'शुल्क')} <span id="connectFee">${esc(session.fee_label || '$0.00')}</span> · ${tr('Net', 'शुद्ध')} <span id="connectNet">${esc(session.net_label || '$0.00')}</span></span>
        </div>
      </div>
      <p>${esc(asked)}</p>
      ${born ? `<p class="sub">${esc(born)}</p>` : ''}
      ${call ? `<p class="sub" id="connectVoice"></p><div class="call-controls"><button type="button" class="btn quiet" id="connectMute" aria-pressed="false">${tr('Mute', 'म्यूट')}</button><button type="button" class="btn" id="connectEnd">${tr('End session', 'सत्र समाप्त')}</button></div>` : `<button type="button" class="btn" id="connectEnd">${tr('End session', 'सत्र समाप्त')}</button>`}
      <div class="connect-log" id="connectLog"></div>
      <form id="connectForm" class="ask-bar"><textarea id="connectInput" rows="1" maxlength="2000" aria-label="${tr('Message', 'संदेश')}"></textarea><button type="submit" class="btn" id="connectSend">${tr('Send', 'भेजें')}</button></form>
      <button type="button" class="btn quiet" id="connectReport">${tr('Report', 'रिपोर्ट')}</button>
      <button type="button" class="btn quiet" id="connectBlock">${tr('Block', 'ब्लॉक')}</button>
    </div>`;
    watch(session, 'astrologer', token);
    if (call) {
      setVoiceNote(tr('Waiting for the call to start.', 'कॉल शुरू होने की प्रतीक्षा।'));
      if (session.status === 'active') joinVoice();
      bindMute($('#connectMute'));
    }
    const end = $('#connectEnd');
    if (end) end.onclick = () => finishAstro(token).catch(showErr);
    const form = $('#connectForm');
    if (form) form.onsubmit = e => { e.preventDefault(); sendAstro(token).catch(showErr); };
    const reportBtn = $('#connectReport');
    if (reportBtn) reportBtn.onclick = () => api('/api/connect/report', {
      device: session.device, astrologer_id: session.astrologer_id, session_id: session.id, reason: tr('Report from the session', 'सत्र से रिपोर्ट'),
    }, token).then(() => { if (window.toast) toast(tr('Report sent', 'रिपोर्ट भेज दी गई')); }).catch(showErr);
    const blockBtn = $('#connectBlock');
    if (blockBtn) blockBtn.onclick = () => api('/api/connect/block', {device: session.device, astrologer_id: session.astrologer_id}, token).then(() => {
      if (window.toast) toast(tr('Seeker blocked', 'साधक ब्लॉक हो गए'));
      finishAstro(token).catch(showErr);
    }).catch(showErr);
    showMessages([]);
  }
  function stopPortalPoll() {
    if (portalPoll) clearInterval(portalPoll);
    portalPoll = 0;
  }
  function chime() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(660, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.18);
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.06, ctx.currentTime + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.45);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.48);
      osc.onended = () => { try { ctx.close(); } catch (e) { /* already closed */ } };
    } catch (e) { /* autoplay can block the chime */ }
    if (navigator.vibrate) { try { navigator.vibrate([120, 60, 120]); } catch (e) { /* no vibrator */ } }
  }
  function flashTitle() {
    if (titleFlash) clearInterval(titleFlash);
    const original = document.title;
    let on = false;
    let n = 0;
    titleFlash = setInterval(() => {
      on = !on;
      document.title = on ? tr('New request', 'नया अनुरोध') : original;
      if (++n >= 8) { clearInterval(titleFlash); titleFlash = 0; document.title = original; }
    }, 700);
  }
  function noteIncoming(sessions) {
    let fresh = false;
    (sessions || []).forEach(s => {
      if (!s || s.status !== 'ringing' || seenIncoming[s.id]) return;
      seenIncoming[s.id] = true;
      fresh = true;
    });
    if (!fresh) return;
    chime();
    flashTitle();
  }
  function paintIncoming(sessions, token) {
    const host = document.getElementById('incoming');
    if (!host) return;
    noteIncoming(sessions);
    const waiting = (sessions || []).filter(s => s && s.status === 'ringing');
    const key = waiting.map(s => s.id).join(',');
    if (host.dataset.key === key) return;
    host.dataset.key = key;
    host.innerHTML = waiting.map(s => {
      const who = s.seeker_name || tr('Seeker', 'साधक');
      const asked = (s.question || '').trim();
      const question = asked ? (asked.length > 80 ? asked.slice(0, 77) + '…' : asked) : tr('No question yet', 'अभी कोई प्रश्न नहीं');
      const mode = s.mode === 'call' ? tr('Call', 'कॉल') : tr('Chat', 'चैट');
      return `<div class="card incoming-banner"><b>${tr('New request', 'नया अनुरोध')}</b><p>${esc(mode)} · ${esc(who)} · ${esc(s.rate_label || '')}</p><p>${esc(question)}</p><button type="button" class="btn" data-accept="${esc(s.id)}">${tr('Accept', 'स्वीकार')}</button></div>`;
    }).join('')
      || `<p class="sub">${tr('No waiting chat or call.', 'कोई प्रतीक्षारत चैट या कॉल नहीं।')}</p>`;
    if (waiting.some(s => s.mode === 'call')) preloadLiveKit();
    host.querySelectorAll('[data-accept]').forEach(b => b.onclick = () => {
      const item = waiting.find(s => s.id === b.dataset.accept);
      if (item && item.mode === 'call') {
        if (window.drishtiMusicHold) window.drishtiMusicHold('call');
        primeCallAudio();
      }
      api('/api/connect/sessions/' + b.dataset.accept + '/accept', {}, token).then(view => openAstroSession(view, token)).catch(err => {
        if (item && item.mode === 'call' && window.drishtiMusicRelease) window.drishtiMusicRelease('call');
        showErr(err);
      });
    });
  }
  function watchIncoming(token) {
    stopPortalPoll();
    portalPoll = setInterval(() => {
      if (location.hash !== '#astro') { stopPortalPoll(); return; }
      api('/api/connect/astro/live', null, token).then(desk => {
        if (desk.session && desk.session.status === 'active') { openAstroSession(desk.session, token); return; }
        paintOnline(desk);
        paintIncoming(desk.ringing || [], token);
      }).catch(() => {});
    }, 5000);
  }
  function choosePayoutMethod(method, token) {
    if (method === 'razorpay') return Promise.resolve();
    if (method !== 'stripe') {
      showErr(new Error(tr('That payout method is not available yet.', 'यह भुगतान तरीका अभी उपलब्ध नहीं।')));
      return Promise.resolve();
    }
    sessionStorage.setItem('dr-astro-return', 'earn');
    return api('/api/connect/astro/stripe', {}, token).then(d => { if (d.url) location.href = d.url; });
  }
  function openPayoutChooser() {
    sessionStorage.setItem('dr-astro-tab', 'choose');
    if (typeof render === 'function') render();
  }
  function switchToAstro(tab) {
    stopUserPoll();
    sessionStorage.setItem('dr-astro-tab', tab || 'dash');
    store.set('app-mode', 'astro');
    const ping = document.getElementById('astroPing');
    if (ping) ping.remove();
    if (location.hash !== '#astro') location.hash = '#astro';
    else if (typeof render === 'function') render();
  }
  function switchToUser() {
    if (window.drishtiMusicRelease) window.drishtiMusicRelease('astro');
    store.set('app-mode', 'user');
    document.body.classList.remove('astro-mode');
    if (location.hash) location.hash = '';
    else if (typeof render === 'function') render();
  }
  function payoutMonday(iso) {
    const day = String(iso || '').slice(0, 10);
    const parts = day.split('-').map(Number);
    if (parts.length < 3 || parts.some(n => !n)) return '';
    const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    if (isNaN(date.getTime())) return '';
    const weekday = date.getUTCDay();
    const add = weekday === 1 ? 0 : (8 - weekday) % 7;
    date.setUTCDate(date.getUTCDate() + add);
    const months = (typeof S !== 'undefined' && S.lang === 'hi')
      ? ['जनवरी', 'फ़रवरी', 'मार्च', 'अप्रैल', 'मई', 'जून', 'जुलाई', 'अगस्त', 'सितंबर', 'अक्टूबर', 'नवंबर', 'दिसंबर']
      : ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const name = (typeof S !== 'undefined' && S.lang === 'hi') ? 'सोमवार' : 'Monday';
    const month = months[date.getUTCMonth()];
    const num = date.getUTCDate();
    return (typeof S !== 'undefined' && S.lang === 'hi') ? (name + ', ' + num + ' ' + month) : (name + ', ' + month + ' ' + num);
  }
  function todayNet(sessions) {
    const now = new Date();
    const cents = (sessions || []).reduce((sum, s) => {
      const d = new Date(s.ended_at || '');
      if (isNaN(d.getTime())) return sum;
      if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) return sum + (Number(s.astro_cents) || 0);
      return sum;
    }, 0);
    return '$' + (cents / 100).toFixed(2);
  }
  function payoutSetupHtml(money) {
    const privacy = `<p class="sub" id="payoutPrivacy">${tr('Bank and ID details go to Stripe. For PayPal, Drishti only keeps your PayPal email.', 'बैंक और पहचान के विवरण Stripe को जाते हैं। PayPal के लिए दृष्टि केवल आपका PayPal ईमेल रखती है।')}</p>`;
    if (money.payout_method === 'paypal' && money.payouts_ready) {
      return `<p>${tr('Payouts to: PayPal', 'भुगतान: PayPal')} · ${esc(money.payout_mask || '')}</p>
        <button type="button" class="btn quiet" id="payoutChange">${tr('Change', 'बदलें')}</button>${privacy}`;
    }
    const label = money.payouts_ready
      ? tr('Payouts to Stripe', 'भुगतान Stripe पर')
      : (money.payout_method === 'stripe' && money.has_payout_account ? tr('Finish payout setup', 'भुगतान सेटअप पूरा करें') : tr('Set up payouts', 'भुगतान सेट करें'));
    const quiet = money.payouts_ready ? ' quiet' : '';
    return `<button type="button" class="btn${quiet}" id="astroStripe" data-payout-method="stripe">${label}</button>${privacy}`;
  }
  function renderAstroMode(desk, money, me, token) {
    preloadLiveKit();
    if (sessionStorage.getItem('dr-astro-return') === 'earn') {
      sessionStorage.setItem('dr-astro-tab', 'earn');
      sessionStorage.removeItem('dr-astro-return');
    }
    const tab = sessionStorage.getItem('dr-astro-tab') || 'dash';
    stopUserPoll();
    store.set('app-mode', 'astro');
    document.body.classList.add('astro-mode');
    document.body.classList.remove('app-fit');
    const root = document.getElementById('root');
    const tabs = [
      ['dash', tr('Dashboard', 'डैशबोर्ड')],
      ['sessions', tr('Sessions', 'सत्र')],
      ['earn', tr('Earnings', 'कमाई')],
      ['settings', tr('Settings', 'सेटिंग')],
    ];
    root.innerHTML = `<div class="astro-app" id="astroApp">
      <header class="top"><span class="tag astro-badge">${tr('Astrologer mode', 'ज्योतिषी मोड')}</span><b>${esc(me.name || '')}</b></header>
      <div id="portalBody"></div>
      <button type="button" class="btn quiet astro-switch" id="switchUser">${tr('Switch to User mode', 'उपयोगकर्ता मोड पर जाएँ')}</button>
      <nav class="bottom astro-nav" aria-label="${tr('Astrologer', 'ज्योतिषी')}"><div class="in">${tabs.map(([k, label]) => `<button type="button" data-tab="${k}" ${k === tab ? 'aria-current="page"' : ''}>${label}</button>`).join('')}</div></nav>
    </div>`;
    const main = $('#portalBody');
    const plans = planCards();
    const nextWhen = money.next_payout_at ? payoutMonday(money.next_payout_at) : tr('Not scheduled', 'अभी निर्धारित नहीं');
    if (tab === 'choose') {
      main.innerHTML = `<h2>${tr('How do you want to get paid?', 'भुगतान कैसे चाहिए?')}</h2>
        <button type="button" class="pay-choice recommended" id="pickPayPal"><span class="tag rec">${tr('Recommended', 'सुझाव')}</span><b>PayPal</b><span>${tr('India & worldwide. Just your PayPal email. No ID needed.', 'भारत और दुनिया भर। केवल PayPal ईमेल। पहचान पत्र नहीं।')}</span></button>
        <button type="button" class="pay-choice" id="pickStripe" data-payout-method="stripe"><b>${tr('Bank deposit', 'बैंक जमा')}</b><span>${tr('US bank accounts only. Set up securely with Stripe.', 'केवल अमेरिकी बैंक खाते। Stripe से सुरक्षित सेटअप।')}</span></button>
        <button type="button" class="pay-choice" id="pickRazorpay" disabled><span class="tag">${tr('Coming soon', 'जल्द आ रहा है')}</span><b>Razorpay</b><span>${tr('India bank & UPI', 'भारत बैंक और UPI')}</span></button>
        <p class="sub">${tr('Payouts every Monday. Bank and ID details go to Stripe; for PayPal we only keep your PayPal email.', 'भुगतान हर सोमवार। बैंक और पहचान के विवरण Stripe को जाते हैं; PayPal के लिए हम केवल आपका PayPal ईमेल रखते हैं।')}</p>
        <button type="button" class="btn quiet" id="payoutBack">${tr('Back', 'वापस')}</button>`;
      $('#pickPayPal').onclick = () => { sessionStorage.setItem('dr-astro-tab', 'paypal'); renderAstroMode(desk, money, me, token); };
      $('#pickStripe').onclick = () => choosePayoutMethod('stripe', token).catch(showErr);
      $('#payoutBack').onclick = () => { sessionStorage.setItem('dr-astro-tab', 'earn'); renderAstroMode(desk, money, me, token); };
    } else if (tab === 'paypal') {
      main.innerHTML = `<h2>PayPal</h2><form class="card" id="paypalForm">
        <input class="input" id="paypalEmail" type="email" autocomplete="email" aria-label="${tr('PayPal email', 'PayPal ईमेल')}" placeholder="${tr('PayPal email', 'PayPal ईमेल')}">
        <input class="input" id="paypalConfirm" type="email" autocomplete="email" aria-label="${tr('Confirm PayPal email', 'PayPal ईमेल की पुष्टि')}" placeholder="${tr('Confirm PayPal email', 'PayPal ईमेल की पुष्टि')}">
        <p class="sub">${tr('Payouts are sent in USD. PayPal may charge a conversion fee.', 'भुगतान USD में भेजे जाते हैं। PayPal रूपांतरण शुल्क ले सकता है।')}</p>
        <button class="btn block" type="submit">${tr('Save PayPal', 'PayPal सहेजें')}</button></form>
        <button type="button" class="btn quiet" id="payoutBack">${tr('Back', 'वापस')}</button>`;
      $('#paypalForm').onsubmit = e => {
        e.preventDefault();
        const email = ($('#paypalEmail').value || '').trim();
        const confirm = ($('#paypalConfirm').value || '').trim();
        if (!email || email !== confirm) {
          showErr(new Error(tr('Enter the same PayPal email twice.', 'वही PayPal ईमेल दो बार लिखें।')));
          return;
        }
        api('/api/connect/astro/payout', {method: 'paypal', paypal_email: email, confirm_email: confirm}, token).then(() => {
          if (window.toast) toast(tr('PayPal saved', 'PayPal सहेजा गया'));
          sessionStorage.setItem('dr-astro-tab', 'earn');
          render();
        }).catch(showErr);
      };
      $('#payoutBack').onclick = () => { sessionStorage.setItem('dr-astro-tab', 'choose'); renderAstroMode(desk, money, me, token); };
    } else if (tab === 'sessions') {
      const live = desk.session && desk.session.status === 'active' ? `<div class="card"><b>${tr('Active', 'चल रहा')}</b><p>${esc(desk.session.mode || '')} · ${esc(desk.session.seeker_name || tr('Seeker', 'साधक'))}</p><button type="button" class="btn" id="openLive">${tr('Open session', 'सत्र खोलें')}</button></div>` : '';
      const past = (money.sessions || []).map(s => `<div class="card"><b>${esc(s.mode || '')}</b><p>${esc(String(s.ended_at || '').slice(0, 16).replace('T', ' '))} · ${esc(s.paid ? tr('Paid', 'भुगतान') : tr('Unpaid', 'बाकी'))}</p><p>${tr('Net', 'शुद्ध')} $${((Number(s.astro_cents) || 0) / 100).toFixed(2)}</p></div>`).join('')
        || `<p class="sub">${tr('No past sessions yet.', 'अभी कोई पुराना सत्र नहीं।')}</p>`;
      main.innerHTML = `<h2>${tr('Sessions', 'सत्र')}</h2>${live}${past}`;
      const open = $('#openLive');
      if (open) open.onclick = () => openAstroSession(desk.session, token);
    } else if (tab === 'earn') {
      main.innerHTML = `<h2>${tr('Earnings', 'कमाई')}</h2><div class="card" id="earnings"><h3>${tr('Summary', 'सारांश')}</h3>
        <p>${tr('Gross', 'कुल')} ${esc(money.gross_label || '$0.00')}</p>
        <p>${tr('Drishti fee', 'दृष्टि शुल्क')} ${esc(money.fee_label || '$0.00')}</p>
        <p><b>${tr('Net', 'शुद्ध')} ${esc(money.net_label || '$0.00')}</b></p>
        <p>${tr('Next payout', 'अगला भुगतान')} ${esc(nextWhen)} · ${esc(money.next_payout_label || '$0.00')}</p>
        ${payoutSetupHtml(money)}</div>`;
    } else if (tab === 'settings') {
      main.innerHTML = `<h2>${tr('Settings', 'सेटिंग')}</h2>
        <form class="card" id="rateForm"><h3>${tr('Rate per minute', 'प्रति मिनट शुल्क')}</h3>
          <input class="input" id="rateDollars" inputmode="decimal" value="${esc((Number(me.rate_cents) / 100).toFixed(2))}" aria-label="${tr('Rate per minute', 'प्रति मिनट शुल्क')}">
          <p class="sub">$${(plans.low / 100).toFixed(2)}–$${(plans.high / 100).toFixed(2)}</p>
          <button class="btn" type="submit">${tr('Save rate', 'शुल्क सहेजें')}</button></form>
        <div class="card"><h3>${tr('Payout method', 'भुगतान तरीका')}</h3><p>${money.payout_method === 'paypal' ? 'PayPal' : 'Stripe'}</p>
          <button type="button" class="btn quiet" id="payoutChange">${tr('Change', 'बदलें')}</button>
          <p class="sub">${tr('Another method can be added here later.', 'बाद में यहाँ दूसरा तरीका जोड़ा जा सकता है।')}</p></div>
        <div class="card"><h3>${tr('Plan', 'योजना')}</h3>
          <p>${plans.days} ${tr('day free trial, then', 'दिन का निःशुल्क परीक्षण, फिर')} ${esc(plans.month)} ${tr('a month or', 'माह या')} ${esc(plans.year)} ${tr('a year.', 'वर्ष।')}</p>
          <p class="sub">${tr('You keep 95%. Drishti keeps 5%.', 'आप 95% रखते हैं। दृष्टि 5% रखती है।')}</p>
          <p class="sub">${esc(trialNote(money))}</p>
          <button type="button" class="btn" id="managePlan">${tr('Manage plan', 'योजना प्रबंधित करें')}</button>
          <button type="button" class="btn quiet" id="planMonth">${tr('Monthly', 'मासिक')} ${esc(plans.month)}</button>
          <button type="button" class="btn quiet" id="planYear">${tr('Yearly', 'वार्षिक')} ${esc(plans.year)}</button></div>
        <button type="button" class="btn quiet astro-switch" id="switchUserSettings">${tr('Switch to User mode', 'उपयोगकर्ता मोड पर जाएँ')}</button>`;
      $('#rateForm').onsubmit = e => {
        e.preventDefault();
        const dollars = parseFloat(String($('#rateDollars').value).replace(/[^0-9.]/g, ''));
        if (!isFinite(dollars) || dollars < 0.3 || dollars > 5) {
          showErr(new Error(tr('Enter a per-minute rate between $0.30 and $5.00, for example 1.50', 'प्रति मिनट दर $0.30 से $5.00 के बीच डालें, जैसे 1.50')));
          return;
        }
        api('/api/connect/astro/rate', {rate_cents: Math.round(dollars * 100)}, token).then(() => {
          if (window.toast) toast(tr('Rate saved', 'शुल्क सहेजा गया'));
          render();
        }).catch(showErr);
      };
      const change = $('#payoutChange');
      if (change) change.onclick = openPayoutChooser;
      const settingsSwitch = $('#switchUserSettings');
      if (settingsSwitch) settingsSwitch.onclick = switchToUser;
      bindPaidButtons(token);
      const manage = $('#managePlan');
      if (manage) manage.onclick = () => {
        if (!money.has_billing_account) { revealPaidPlans(money, token); return; }
        api('/api/connect/astro/portal', {}, token).then(d => { if (d.url) location.href = d.url; }).catch(err => {
          if (err && err.code === 'no_billing_account') { revealPaidPlans(money, token); return; }
          showErr(err);
        });
      };
    } else {
      main.innerHTML = `<h2>${tr('Dashboard', 'डैशबोर्ड')}</h2>
        <div class="card"><label class="spread"><span id="onlineLabel">${tr('Available', 'उपलब्ध')}</span><input type="checkbox" id="astroOnline"></label></div>
        <div id="incoming"></div>
        <div class="card"><h3>${tr("Today's earnings", 'आज की कमाई')}</h3><p><b>${esc(todayNet(money.sessions))}</b></p>
          <p>${tr('Next payout', 'अगला भुगतान')} ${esc(nextWhen)} · ${esc(money.next_payout_label || '$0.00')}</p>
          ${payoutSetupHtml(money)}</div>`;
      paintOnline(desk);
      const box = $('#astroOnline');
      if (box) box.onchange = e => api('/api/connect/astro/online', {online: !!e.target.checked}, token).then(view => paintOnline({online: view.online, in_session: false})).catch(err => { paintOnline(desk); showErr(err); });
      paintIncoming(desk.ringing || [], token);
    }
    const pay = $('#astroStripe');
    if (pay) pay.onclick = () => {
      if (money.payout_method === 'stripe' && money.has_payout_account && !money.payouts_ready) choosePayoutMethod('stripe', token).catch(showErr);
      else openPayoutChooser();
    };
    const changePay = $('#payoutChange');
    if (changePay && !$('#rateForm')) changePay.onclick = openPayoutChooser;
    const back = $('#switchUser');
    if (back) back.onclick = switchToUser;
    $$('#astroApp nav button').forEach(b => b.onclick = () => {
      sessionStorage.setItem('dr-astro-tab', b.dataset.tab);
      renderAstroMode(desk, money, me, token);
    });
    if (desk.session && desk.session.status === 'active' && tab === 'dash') openAstroSession(desk.session, token);
    else watchIncoming(token);
  }
  let userPoll = 0;
  function stopUserPoll() {
    if (userPoll) clearInterval(userPoll);
    userPoll = 0;
  }
  function watchUserIncoming(token) {
    stopUserPoll();
    const beat = () => {
      if (location.hash === '#astro' || store.get('app-mode') === 'astro') return;
      api('/api/connect/astro/live', null, token).then(desk => {
        const waiting = (desk.ringing || []).filter(s => s && s.status === 'ringing');
        let host = document.getElementById('astroPing');
        if (!waiting.length) { if (host) host.remove(); return; }
        if (host) return;
        host = document.createElement('div');
        host.id = 'astroPing';
        host.className = 'astro-ping';
        host.innerHTML = `<b>${tr('New request', 'नया अनुरोध')}</b><p class="sub">${tr('Switch to Astrologer mode to accept it.', 'स्वीकार करने के लिए ज्योतिषी मोड पर जाएँ।')}</p><button type="button" class="btn" id="pingSwitch">${tr('Switch to Astrologer mode', 'ज्योतिषी मोड पर जाएँ')}</button>`;
        document.body.appendChild(host);
        const go = host.querySelector('#pingSwitch');
        if (go) go.onclick = () => switchToAstro('dash');
      }).catch(() => {});
    };
    beat();
    userPoll = setInterval(beat, 5000);
  }
  function bannerHtml() {
    return `<div class="astro-banner" id="astroModeBanner"><b>${tr("You're an approved astrologer", 'आप स्वीकृत ज्योतिषी हैं')}</b><button type="button" class="btn" id="switchAstro">${tr('Switch to Astrologer mode', 'ज्योतिषी मोड पर जाएँ')}</button></div>`;
  }
  async function paintBanner() {
    const slot = document.getElementById('astroModeSlot');
    if (!slot || !on()) return;
    const token = sessionStorage.getItem('dr-astro-token') || '';
    if (!token) { slot.hidden = true; slot.innerHTML = ''; stopUserPoll(); return; }
    let me;
    try { me = await api('/api/connect/astro/me', null, token); }
    catch (e) { slot.hidden = true; return; }
    sessionStorage.setItem('dr-astro-status', me.status || '');
    if (me.status !== 'approved') { slot.hidden = true; slot.innerHTML = ''; stopUserPoll(); return; }
    slot.hidden = false;
    slot.innerHTML = bannerHtml();
    const button = document.getElementById('switchAstro');
    if (button) button.onclick = () => switchToAstro('dash');
    watchUserIncoming(token);
  }
  async function renderPortal() {
    stopPortalPoll();
    const body = portalRoot(tr('Astrologer', 'ज्योतिषी'));
    const token = sessionStorage.getItem('dr-astro-token') || '';
    if (!on()) { body.innerHTML = `<p>${tr('This is not switched on yet.', 'यह अभी चालू नहीं है।')}</p>`; return; }
    if (token && sessionStorage.getItem('dr-astro-must-change') === '1') {
      showPasswordChange(body, token);
      return;
    }
    if (!token) {
      body.innerHTML = registerForm();
      $('#showLogin').onclick = () => {
        body.innerHTML = `<form class="card" id="astroLogin"><h3>${tr('Sign in', 'साइन इन')}</h3>
          <input class="input" id="astroEmail" type="email" autocomplete="username" aria-label="${tr('Email', 'ईमेल')}">
          <input class="input" id="astroPass" type="password" autocomplete="current-password" aria-label="${tr('Password', 'पासवर्ड')}">
          <button class="btn block" type="submit">${tr('Sign in', 'साइन इन')}</button>
          <button class="btn quiet" type="button" id="forgotPass">${tr('Forgot password?', 'पासवर्ड भूल गए?')}</button>
          <p class="sub" id="forgotNote" hidden>${tr('Contact Drishti support to reset your password.', 'पासवर्ड रीसेट करने के लिए दृष्टि सहायता से संपर्क करें।')} <a href="mailto:ikshanasolution123@gmail.com">ikshanasolution123@gmail.com</a></p></form>`;
        // TODO: when SMTP_HOST, SMTP_USER, and SMTP_PASSWORD are set, email a one-time reset link instead of asking the astrologer to contact support.
        $('#astroLogin').onsubmit = async e => {
          e.preventDefault();
          try {
            const got = await api('/api/connect/astro/login', {email: $('#astroEmail').value, password: $('#astroPass').value});
            rememberAstro(got);
            render();
          } catch (err) { showSignInErr(err); }
        };
        const forgot = $('#forgotPass');
        if (forgot) forgot.onclick = () => { const note = $('#forgotNote'); if (note) note.hidden = false; };
      };
      $('#astroRegister').onsubmit = async e => {
        e.preventDefault();
        const dollars = parseFloat(String($('#regRate').value).replace(/[^0-9.]/g, ''));
        if (!isFinite(dollars) || dollars < 0.3 || dollars > 5) {
          showErr(new Error(tr('Enter a per-minute rate between $0.30 and $5.00, for example 1.50', 'प्रति मिनट दर $0.30 से $5.00 के बीच डालें, जैसे 1.50')));
          return;
        }
        const photo = await readPhoto($('#regPhoto'));
        try {
          await api('/api/connect/apply', {
            email: $('#regEmail').value, password: $('#regPass').value, name: $('#regName').value,
            languages: $('#regLang').value.split(',').map(s => s.trim()).filter(Boolean),
            specialties: $('#regSpec').value.split(',').map(s => s.trim()).filter(Boolean),
            years: Number($('#regYears').value || 0), bio: $('#regBio').value, photo: photo,
            rate_cents: Math.round(dollars * 100),
          });
          const got = await api('/api/connect/astro/login', {email: $('#regEmail').value, password: $('#regPass').value});
          rememberAstro(got);
          render();
        } catch (err) { showErr(err); }
      };
      return;
    }
    let desk = {online: false, in_session: false, session: null, ringing: []};
    let money = {sessions: [], payouts: [], gross_label: '$0.00', fee_label: '$0.00', net_label: '$0.00', plan_status: 'none', renews_at: ''};
    let me = {status: ''};
    try {
      desk = await api('/api/connect/astro/live', null, token);
      money = await api('/api/connect/astro/earnings', null, token);
      me = await api('/api/connect/astro/me', null, token);
    } catch (e) {
      if (e && e.code === 'must_change') {
        sessionStorage.setItem('dr-astro-must-change', '1');
        render();
        return;
      }
      sessionStorage.removeItem('dr-astro-token');
      sessionStorage.removeItem('dr-astro-must-change');
      render();
      return;
    }
    sessionStorage.setItem('dr-astro-status', me.status || '');
    if (me.status === 'approved') {
      renderAstroMode(desk, money, me, token);
      return;
    }
    store.set('app-mode', 'user');
    const plans = planCards();
    const needsPlan = !money.plan_status || money.plan_status === 'none' || money.plan_status === 'canceled';
    body.innerHTML = `${needsPlan ? `<div class="card" id="planPicker"><h3>${tr('Choose a plan', 'योजना चुनें')}</h3>
      <button type="button" class="btn block" id="planTrial">${plans.days} ${tr('day free trial', 'दिन का निःशुल्क परीक्षण')}</button>
      <p class="sub">${plans.days} ${tr('days free. No card.', 'दिन मुफ़्त। कार्ड नहीं।')}</p>
      <button type="button" class="btn block" id="planMonth">${tr('Monthly', 'मासिक')} ${esc(plans.month)}</button>
      <button type="button" class="btn block" id="planYear">${tr('Yearly', 'वार्षिक')} ${esc(plans.year)}</button>
      <p class="sub">${tr('Monthly or yearly starts after the free trial ends. No lifetime plan.', 'मासिक या वार्षिक योजना निःशुल्क परीक्षण के बाद शुरू होती है। कोई आजीवन योजना नहीं।')}</p></div>` : ''}
      <div class="card"><label class="spread"><span id="onlineLabel">${tr('Online', 'ऑनलाइन')}</span><input type="checkbox" id="astroOnline"></label>
      <p class="sub">${tr('You keep 95%. Drishti keeps 5% of each session.', 'आप 95% रखते हैं। दृष्टि हर सत्र का 5% रखती है।')}</p>
      <p class="sub">${tr('Plan', 'योजना')}: ${esc(money.plan_status || 'none')}${money.renews_at ? ' · ' + esc(String(money.renews_at).slice(0, 10)) : ''}</p></div>
      <div id="incoming"></div>
      <div class="card" id="earnings"><h3>${tr('Earnings', 'कमाई')}</h3>
        <p>${tr('Gross', 'कुल')} ${esc(money.gross_label || '$0.00')}</p>
        <p>${tr('Drishti fee', 'दृष्टि शुल्क')} ${esc(money.fee_label || '$0.00')}</p>
        <p><b>${tr('Net', 'शुद्ध')} ${esc(money.net_label || '$0.00')}</b></p>
        <button type="button" class="btn" id="managePlan">${tr('Manage plan', 'योजना प्रबंधित करें')}</button>
        <button type="button" class="btn quiet" id="astroStripe">${money.payouts_ready ? tr('Payouts ready', 'भुगतान तैयार') : (money.has_payout_account ? tr('Finish payout setup', 'भुगतान सेटअप पूरा करें') : tr('Set up payouts', 'भुगतान सेट करें'))}</button>
        <p class="sub" id="payoutPrivacy">${tr('Your bank and ID details go straight to Stripe. Drishti never sees or stores them.', 'आपके बैंक और पहचान के विवरण सीधे Stripe को जाते हैं। दृष्टि उन्हें कभी नहीं देखती या सहेजती।')}</p></div>`;
    const trial = $('#planTrial');
    if (trial) trial.onclick = () => api('/api/connect/astro/trial', {}, token).then(() => render()).catch(showErr);
    bindPaidButtons(token);
    if (desk.session && desk.session.status === 'active') { openAstroSession(desk.session, token); return; }
    paintOnline(desk);
    $('#astroOnline').onchange = e => api('/api/connect/astro/online', {online: !!e.target.checked}, token).then(view => paintOnline({online: view.online, in_session: false})).catch(err => { paintOnline(desk); showErr(err); });
    paintIncoming(desk.ringing || [], token);
    watchIncoming(token);
    $('#astroStripe').onclick = () => choosePayoutMethod('stripe', token).catch(showErr);
    $('#managePlan').onclick = () => {
      if (!money.has_billing_account) { revealPaidPlans(money, token); return; }
      api('/api/connect/astro/portal', {}, token).then(d => { if (d.url) location.href = d.url; }).catch(err => {
        if (err && err.code === 'no_billing_account') { revealPaidPlans(money, token); return; }
        showErr(err);
      });
    };
  }
  function readPhoto(input) {
    const file = input && input.files && input.files[0];
    if (!file) return Promise.resolve('');
    return new Promise(resolve => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || '').slice(0, 200000));
      reader.onerror = () => resolve('');
      reader.readAsDataURL(file);
    });
  }
  function adminActions(status) {
    if (status === 'pending') return [
      {act: 'approve', label: ['Approve', 'स्वीकार'], quiet: false},
      {act: 'remove', label: ['Reject', 'अस्वीकार'], quiet: true},
    ];
    if (status === 'approved') return [
      {act: 'suspend', label: ['Suspend', 'रोकें'], quiet: true},
      {act: 'remove', label: ['Remove', 'हटाएँ'], quiet: true},
    ];
    if (status === 'suspended') return [
      {act: 'approve', label: ['Reinstate', 'बहाल'], quiet: false},
      {act: 'remove', label: ['Remove', 'हटाएँ'], quiet: true},
    ];
    return [];
  }
  function adminStatusLabel(status) {
    if (status === 'pending') return ['Pending', 'लंबित'];
    if (status === 'approved') return ['Approved', 'स्वीकृत'];
    if (status === 'suspended') return ['Suspended', 'रुका'];
    if (status === 'removed') return ['Removed', 'हटाया'];
    return [String(status || ''), String(status || '')];
  }
  function adminDone(action, from) {
    if (action === 'approve' && from === 'suspended') return ['Reinstated', 'बहाल किया गया'];
    if (action === 'approve') return ['Approved', 'स्वीकृत'];
    if (action === 'suspend') return ['Suspended', 'रोक दिया गया'];
    if (action === 'remove' && from === 'pending') return ['Rejected', 'अस्वीकार किया गया'];
    if (action === 'remove') return ['Removed', 'हटा दिया गया'];
    return ['Saved', 'सहेजा गया'];
  }
  function adminPill(status) {
    if (status === 'approved') return 'tag good';
    if (status === 'suspended') return 'tag warn';
    if (status === 'removed') return 'tag bad';
    return 'tag';
  }
  async function renderAdmin() {
    stopPortalPoll();
    const body = portalRoot(tr('Admin', 'एडमिन'));
    if (!on()) { body.innerHTML = `<p>${tr('This is not switched on yet.', 'यह अभी चालू नहीं है।')}</p>`; return; }
    const token = sessionStorage.getItem('dr-admin-token') || '';
    if (!token) {
      body.innerHTML = `<form class="card" id="adminLogin"><h3>${tr('Admin token', 'एडमिन टोकन')}</h3>
        <input class="input" id="adminToken" type="password" aria-label="${tr('Token', 'टोकन')}">
        <button class="btn" type="submit">${tr('Continue', 'आगे')}</button></form>`;
      $('#adminLogin').onsubmit = e => { e.preventDefault(); sessionStorage.setItem('dr-admin-token', $('#adminToken').value.trim()); render(); };
      return;
    }
    let data;
    try { data = await api('/api/connect/admin/astrologers', null, token); }
    catch (e) { sessionStorage.removeItem('dr-admin-token'); body.innerHTML = `<p>${tr('That token was not accepted.', 'वह टोकन स्वीकार नहीं हुआ।')}</p>`; return; }
    body.innerHTML = (data.astrologers || []).map(a => {
      const label = adminStatusLabel(a.status);
      const actions = adminActions(a.status).map(item =>
        `<button type="button" class="btn${item.quiet ? ' quiet' : ''}" data-act="${esc(item.act)}">${tr(item.label[0], item.label[1])}</button>`
      ).join('');
      const reset = a.status === 'removed' ? '' : `<button type="button" class="btn quiet" data-reset="${esc(a.id)}">${tr('Reset password', 'पासवर्ड रीसेट')}</button>`;
      return `<div class="card" data-id="${esc(a.id)}" data-status="${esc(a.status)}">
      <b>${esc(a.name)}</b> <span class="${adminPill(a.status)}">${esc(tr(label[0], label[1]))}</span>
      <p>${esc(a.email)} · ${esc(a.rate_label || '')} · ${esc(a.plan_status || '')}</p>
      <p>${esc((a.languages || []).join(', '))}</p>
      <div class="admin-acts">${actions}${reset}</div>
    </div>`;
    }).join('') || `<p>${tr('No applications.', 'कोई आवेदन नहीं।')}</p>`;
    document.querySelectorAll('#portalBody [data-reset]').forEach(b => b.onclick = async () => {
      const card = b.closest('[data-id]');
      try {
        const got = await api('/api/connect/admin/astrologers/' + b.dataset.reset + '/reset-password', {}, token);
        const note = document.createElement('p');
        note.className = 'sub';
        note.textContent = tr('Temporary password, shown once: ', 'अस्थायी पासवर्ड, एक बार दिखेगा: ') + (got.temporary_password || '');
        card.appendChild(note);
      } catch (err) { showErr(err); }
    });
    document.querySelectorAll('#portalBody [data-act]').forEach(b => b.onclick = async () => {
      const card = b.closest('[data-id]');
      if (!card || b.disabled) return;
      const from = card.dataset.status || '';
      const action = b.dataset.act;
      card.querySelectorAll('button').forEach(x => { x.disabled = true; });
      try {
        await api('/api/connect/admin/astrologers/' + card.dataset.id, {action: action}, token);
        const note = adminDone(action, from);
        if (window.toast) toast(tr(note[0], note[1]));
        render();
      } catch (err) {
        card.querySelectorAll('button').forEach(x => { x.disabled = false; });
        showErr(err);
      }
    });
  }
  function rememberAstro(got) {
    sessionStorage.setItem('dr-astro-token', got.token);
    if (got.must_change) sessionStorage.setItem('dr-astro-must-change', '1');
    else sessionStorage.removeItem('dr-astro-must-change');
  }
  function showSignInErr(err) {
    if (err && (err.code === 'unauthorized' || err.message === 'unauthorized')) {
      showErr(new Error(tr('Email or password is incorrect', 'ईमेल या पासवर्ड गलत है')));
      return;
    }
    showErr(err);
  }
  function showPasswordChange(body, token) {
    body.innerHTML = `<form class="card" id="changePass"><h3>${tr('Set a new password', 'नया पासवर्ड रखें')}</h3>
      <p class="sub">${tr('Choose at least 8 characters before using the portal.', 'पोर्टल इस्तेमाल करने से पहले कम से कम 8 अक्षर चुनें।')}</p>
      <input class="input" id="newPass" type="password" autocomplete="new-password" aria-label="${tr('New password', 'नया पासवर्ड')}">
      <button class="btn block" type="submit">${tr('Save password', 'पासवर्ड सहेजें')}</button></form>`;
    $('#changePass').onsubmit = async e => {
      e.preventDefault();
      const password = $('#newPass').value || '';
      if (password.length < 8) {
        showErr(new Error(tr('Use at least 8 characters.', 'कम से कम 8 अक्षर लिखें।')));
        return;
      }
      try {
        await api('/api/connect/astro/password', {password: password}, token);
        sessionStorage.removeItem('dr-astro-must-change');
        render();
      } catch (err) { showErr(err); }
    };
  }
  function takeover() {
    if (location.hash === '#astro') {
      if (window.drishtiMusicHold) window.drishtiMusicHold('astro');
      renderPortal();
      return true;
    }
    if (window.drishtiMusicRelease) window.drishtiMusicRelease('astro');
    if (location.hash === '#admin') { renderAdmin(); return true; }
    return false;
  }
  window.onAnswerClosed = function () { if (on()) offer(); };
  function walletWhen(iso) {
    const date = new Date(iso || '');
    if (isNaN(date.getTime())) return '';
    const hi = typeof S !== 'undefined' && S.lang === 'hi';
    const months = hi
      ? ['जनवरी', 'फ़रवरी', 'मार्च', 'अप्रैल', 'मई', 'जून', 'जुलाई', 'अगस्त', 'सितंबर', 'अक्टूबर', 'नवंबर', 'दिसंबर']
      : ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = months[date.getMonth()];
    const num = date.getDate();
    return hi ? (num + ' ' + month) : (month + ' ' + num);
  }
  function walletActivityText(item) {
    const kind = item && item.kind;
    if (kind === 'topup') return tr('Added money', 'पैसे जोड़े');
    if (kind === 'hold') return item.mode === 'call' ? tr('Voice call hold', 'वॉइस कॉल की होल्ड') : tr('Session hold', 'सत्र की होल्ड');
    if (kind === 'charge') return tr('Per-minute charge', 'प्रति मिनट शुल्क');
    if (kind === 'refund' && item.end_reason === 'unanswered') return tr('Hold returned · no one accepted', 'होल्ड लौटी · कोई स्वीकार नहीं किया');
    if (kind === 'refund') return tr('Hold returned', 'होल्ड लौटी');
    return tr('Wallet', 'वॉलेट');
  }
  function walletCardHtml(view) {
    const items = Array.isArray(view && view.activity) ? view.activity : [];
    const rows = items.map(item => {
      const cents = Number(item.cents) || 0;
      return `<li><span class="grow"><b>${esc(walletActivityText(item))}</b><span class="small muted">${esc(walletWhen(item.at))}</span></span><span class="wallet-amt ${cents < 0 ? 'out' : 'in'}">${esc(item.amount_label || '')}</span></li>`;
    }).join('');
    const list = items.length
      ? `<ul class="wallet-activity">${rows}</ul>`
      : `<p class="small muted wallet-empty">${tr('No wallet activity yet.', 'अभी कोई वॉलेट गतिविधि नहीं।')}</p>`;
    return `<div class="spread wallet-head"><div><span class="small muted">${tr('Wallet', 'वॉलेट')}</span><b class="wallet-bal">${esc((view && view.balance_label) || '$0.00')}</b></div><button type="button" class="btn" id="walletAdd">${tr('Add money', 'पैसे जोड़ें')}</button></div>${list}`;
  }
  function openWalletTopup() {
    sheet(`<div class="sheet connect" role="dialog" aria-modal="true" aria-labelledby="walletTitle">
      <button type="button" class="sheet-handle" aria-label="${tr('Drag down to close', 'बंद करने के लिए नीचे खींचें')}"><i></i></button>
      <h3 id="walletTitle">${tr('Add money', 'पैसे जोड़ें')}</h3>
      <p class="sub">${tr('Choose an amount. It is added to your wallet.', 'राशि चुनें। यह आपके वॉलेट में जुड़ जाएगी।')}</p>
      <div class="connect-packs"><button type="button" class="btn pack" data-pack="wallet_5">$5</button><button type="button" class="btn pack" data-pack="wallet_10">$10</button><button type="button" class="btn pack" data-pack="wallet_20">$20</button></div>
    </div>`);
    document.querySelectorAll('#sheet [data-pack]').forEach(b => b.onclick = () => {
      topup(b.dataset.pack).then(() => {
        if (window.closeSheet) closeSheet();
        paintWallet();
      }).catch(showErr);
    });
  }
  async function paintWallet() {
    const card = document.getElementById('walletCard');
    if (!card || !on() || document.body.classList.contains('astro-mode')) return;
    const gen = (paintWallet.gen = (paintWallet.gen || 0) + 1);
    try {
      const view = await api('/api/connect/wallet?device=' + encodeURIComponent(DEVICE));
      if (gen !== paintWallet.gen || !card.isConnected) return;
      card.innerHTML = walletCardHtml(view);
      const add = document.getElementById('walletAdd');
      if (add) add.onclick = () => openWalletTopup();
    } catch (err) {
      if (gen !== paintWallet.gen || !card.isConnected) return;
      card.innerHTML = `<p class="small muted">${tr('Wallet is unavailable right now.', 'वॉलेट अभी उपलब्ध नहीं है।')}</p>`;
    }
  }
  window.DrishtiConnect = {takeover: takeover, offer: offer, list: list, stop: stop, paintBanner: paintBanner, paintWallet: paintWallet, switchToAstro: switchToAstro};
  if (store.get('app-mode') === 'astro' && sessionStorage.getItem('dr-astro-token') && location.hash !== '#astro' && location.hash !== '#admin') {
    location.hash = '#astro';
  }
  window.addEventListener('hashchange', () => {
    if (location.hash !== '#astro' && live && live.role === 'astrologer') stop();
    if (typeof render === 'function') render();
  });
})();
