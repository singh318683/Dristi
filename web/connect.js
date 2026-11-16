/* Personal astrologers. The server turns this on with ASTROLOGER_CONNECT_ENABLED. */
(function () {
  'use strict';
  const $ = (s, root) => (root || document).querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tr = (en, hi) => (typeof S !== 'undefined' && S.lang === 'hi') ? hi : en;
  let timer = 0;
  let clock = 0;
  let live = null;

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
    if (live && live.room && live.room.disconnect) live.room.disconnect();
    live = null;
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
    no_billing_account: ['Choose a monthly or yearly plan to continue after the free trial.', 'निःशुल्क परीक्षण के बाद जारी रखने के लिए मासिक या वार्षिक योजना चुनें।'],
    trial_unavailable: ['The free trial is not available.', 'निःशुल्क परीक्षण उपलब्ध नहीं है।'],
    unauthorized: ['That sign-in was not accepted.', 'यह साइन-इन स्वीकार नहीं हुआ।'],
    invalid_rate: ['Enter a per-minute rate between $0.30 and $5.00.', 'प्रति मिनट दर $0.30 से $5.00 के बीच डालें।'],
    bad_rate: ['Enter a per-minute rate between $0.30 and $5.00.', 'प्रति मिनट दर $0.30 से $5.00 के बीच डालें।'],
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
  function lastQuestion() {
    try {
      const hist = store.get(chatKey(), []);
      for (let i = hist.length - 1; i >= 0; i--) if (hist[i].role === 'user') return hist[i].content || '';
    } catch (e) { /* no chart yet */ }
    return '';
  }
  async function checkout(id, mode) {
    const q = await api('/api/connect/quote', {device: DEVICE, astrologer_id: id});
    const title = mode === 'call' ? tr('Voice call', 'वॉइस कॉल') : tr('Chat', 'चैट');
    const short = q.balance_cents < q.hold_cents;
    sheet(`<div class="sheet connect" role="dialog" aria-modal="true" aria-labelledby="checkTitle">
      <button type="button" class="sheet-handle" aria-label="${tr('Drag down to close', 'बंद करने के लिए नीचे खींचें')}"><i></i></button>
      <h3 id="checkTitle">${esc(title)} · ${esc(q.name)}</h3>
      <p>${esc(q.rate_label)} · ${tr('the same rate for chat and call', 'चैट और कॉल दोनों का एक ही शुल्क')}</p>
      <p>${tr('A 5-minute hold', '5 मिनट की होल्ड')} · ${esc(q.hold_label)}. ${tr('Wallet balance', 'वॉलेट')} ${esc(q.balance_label)}.</p>
      <p class="sub">${tr('Your question and birth chart are shared with the astrologer. Billed per minute. End anytime. Unused hold returns to your wallet.', 'आपका प्रश्न और जन्म कुंडली ज्योतिषी के साथ साझा होती है। शुल्क प्रति मिनट। कभी भी समाप्त करें। बची होल्ड वॉलेट में लौटती है।')}</p>
      <p class="sub">${tr('Guidance for reflection, not medical, legal or financial advice.', 'यह चिंतन हेतु मार्गदर्शन है, चिकित्सा, कानूनी या वित्तीय सलाह नहीं।')}</p>
      ${short ? `<div class="connect-actions">
        <button type="button" class="btn quiet" data-pack="wallet_5">$5</button>
        <button type="button" class="btn quiet" data-pack="wallet_10">$10</button>
        <button type="button" class="btn quiet" data-pack="wallet_20">$20</button>
      </div>` : ''}
      <button type="button" class="btn block" id="connectStart" ${short ? 'disabled' : ''}>${mode === 'call' ? tr('Start call', 'कॉल शुरू करें') : tr('Start chat', 'चैट शुरू करें')}</button>
    </div>`);
    document.querySelectorAll('#sheet [data-pack]').forEach(b => b.onclick = () => topup(b.dataset.pack).then(() => checkout(id, mode)).catch(showErr));
    const start = $('#connectStart');
    if (start) start.onclick = () => begin(id, mode).catch(showErr);
  }
  async function topup(product) {
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
    const stripe = window.Stripe(key);
    const elements = stripe.elements({clientSecret: intent.client_secret});
    const box = document.createElement('div');
    box.id = 'stripePay';
    const host = document.querySelector('#sheet .sheet');
    host.appendChild(box);
    elements.create('payment').mount('#stripePay');
    const pay = document.createElement('button');
    pay.type = 'button';
    pay.className = 'btn block';
    pay.textContent = tr('Pay', 'भुगतान');
    host.appendChild(pay);
    await new Promise((resolve, reject) => {
      pay.onclick = async () => {
        const result = await stripe.confirmPayment({elements: elements, redirect: 'if_required'});
        if (result.error) reject(new Error(result.error.message));
        else resolve(result);
      };
    });
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
  function paintClock() {
    if (!live) return;
    const sec = live.elapsed + Math.floor((Date.now() - live.at) / 1000);
    const m = String(Math.floor(sec / 60)).padStart(2, '0');
    const s = String(sec % 60).padStart(2, '0');
    const node = $('#connectClock');
    const cost = $('#connectCost');
    if (node) node.textContent = m + ':' + s;
    if (cost) cost.textContent = live.cost;
  }
  function watch(session) {
    stop();
    live = {id: session.id, elapsed: session.elapsed_seconds || 0, at: Date.now(), cost: session.cost_label || '', room: null, after: 0};
    clock = setInterval(paintClock, 1000);
    paintClock();
    timer = setInterval(() => beat().catch(() => {}), 3000);
  }
  async function beat() {
    if (!live) return;
    const view = await api('/api/connect/sessions/' + live.id + '/heartbeat', {role: 'user', device: DEVICE});
    live.elapsed = view.elapsed_seconds || 0;
    live.at = Date.now();
    live.cost = view.cost_label || live.cost;
    paintClock();
    if (view.status === 'active') {
      const wait = document.querySelector('#connectLog .them');
      if (wait && wait.textContent.indexOf('Waiting') === 0) wait.remove();
    }
    if (view.status === 'ended') {
      if (window.toast) toast(tr('Session ended', 'सत्र समाप्त') + ' · ' + (view.cost_label || ''));
      stop();
      closeSheet();
      return;
    }
    if ($('#connectLog')) {
      const msgs = await api('/api/connect/sessions/' + live.id + '/messages?device=' + encodeURIComponent(DEVICE) + '&after=' + live.after);
      (msgs.messages || []).forEach(addMsg);
    }
  }
  function addMsg(m) {
    const log = $('#connectLog');
    if (!log || !m) return;
    live.after = Math.max(live.after || 0, m.id || 0);
    const p = document.createElement('p');
    p.className = m.sender === 'user' ? 'me' : 'them';
    p.textContent = m.body;
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
  }
  function openChat(session) {
    watch(session);
    sheet(`<div class="sheet connect-live" role="dialog" aria-modal="true" aria-label="${esc(session.astrologer_name)}">
      <div class="connect-head">
        <div class="connect-av" aria-hidden="true">${esc((session.astrologer_name || '?').slice(0, 1))}</div>
        <div class="grow"><b>${esc(session.astrologer_name)}</b><span class="connect-meter"><span id="connectClock">00:00</span> · <span id="connectCost">${esc(session.cost_label)}</span></span></div>
        <button type="button" class="btn quiet" id="connectEnd">${tr('End', 'समाप्त')}</button>
      </div>
      <div class="connect-log" id="connectLog"><p class="them">${tr('Waiting for them to join.', 'उनके जुड़ने की प्रतीक्षा।')}</p></div>
      <form id="connectForm" class="ask-bar"><textarea id="connectInput" rows="1" maxlength="2000" aria-label="${tr('Message', 'संदेश')}"></textarea>
        <button type="submit" class="btn">${tr('Send', 'भेजें')}</button></form>
      <button type="button" class="btn quiet" id="connectReport">${tr('Report', 'रिपोर्ट')}</button>
      <button type="button" class="btn quiet" id="connectBlock">${tr('Block', 'ब्लॉक')}</button>
    </div>`, {live: true});
    $('#connectEnd').onclick = () => finish().catch(showErr);
    $('#connectForm').onsubmit = e => { e.preventDefault(); send().catch(showErr); };
    $('#connectReport').onclick = () => report(session).catch(showErr);
    $('#connectBlock').onclick = () => block(session).catch(showErr);
    paintClock();
  }
  async function send() {
    const input = $('#connectInput');
    const text = (input.value || '').trim();
    if (!text || !live) return;
    input.value = '';
    const waiting = document.querySelector('#connectLog .them');
    if (waiting) waiting.remove();
    await api('/api/connect/sessions/' + live.id + '/messages', {sender: 'user', body: text, device: DEVICE});
    addMsg({id: 0, sender: 'user', body: text});
  }
  async function openCall(session) {
    watch(session);
    let note = '';
    try {
      const token = await api('/api/connect/sessions/' + session.id + '/call-token', {device: DEVICE});
      live.token = token;
      note = '';
      attachVoice(token);
    } catch (e) {
      note = tr('Voice is not ready yet. Ending still stops the bill.', 'वॉइस अभी तैयार नहीं। समाप्त करने पर शुल्क रुक जाता है।');
    }
    sheet(`<div class="sheet connect-live" role="dialog" aria-modal="true" aria-label="${esc(session.astrologer_name)}">
      <div class="call-screen">
        <div class="connect-av" aria-hidden="true">${esc((session.astrologer_name || '?').slice(0, 1))}</div>
        <b>${esc(session.astrologer_name)}</b>
        <span class="connect-meter"><span id="connectClock">00:00</span> · <span id="connectCost">${esc(session.cost_label)}</span></span>
        <p class="sub" id="connectVoice">${esc(note)}</p>
        <div class="call-controls">
          <button type="button" class="btn quiet" id="connectMute" aria-pressed="false">${tr('Mute', 'म्यूट')}</button>
          <button type="button" class="btn quiet" id="connectSpeaker" aria-pressed="false">${tr('Speaker', 'स्पीकर')}</button>
          <button type="button" class="btn" id="connectEnd">${tr('End', 'समाप्त')}</button>
        </div>
      </div>
    </div>`, {live: true});
    $('#connectEnd').onclick = () => finish().catch(showErr);
    $('#connectMute').onclick = ev => {
      const pressed = ev.currentTarget.getAttribute('aria-pressed') === 'true';
      ev.currentTarget.setAttribute('aria-pressed', pressed ? 'false' : 'true');
      const room = live && live.room;
      if (room && room.localParticipant) room.localParticipant.setMicrophoneEnabled(pressed);
    };
    $('#connectSpeaker').onclick = ev => {
      const pressed = ev.currentTarget.getAttribute('aria-pressed') !== 'true';
      ev.currentTarget.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    };
    paintClock();
  }
  async function attachVoice(info) {
    try {
      const mod = await import('https://cdn.jsdelivr.net/npm/livekit-client@2.9.0/+esm');
      const room = new mod.Room();
      await room.connect(info.url, info.token);
      await room.localParticipant.setMicrophoneEnabled(true);
      if (live) live.room = room;
    } catch (e) {
      const note = $('#connectVoice');
      if (note && !note.textContent) note.textContent = tr('Waiting for the voice room.', 'वॉइस रूम की प्रतीक्षा।');
    }
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
  async function renderPortal() {
    const body = portalRoot(tr('Astrologer', 'ज्योतिषी'));
    const token = sessionStorage.getItem('dr-astro-token') || '';
    if (!on()) { body.innerHTML = `<p>${tr('This is not switched on yet.', 'यह अभी चालू नहीं है।')}</p>`; return; }
    if (!token) {
      body.innerHTML = registerForm();
      $('#showLogin').onclick = () => {
        body.innerHTML = `<form class="card" id="astroLogin"><h3>${tr('Sign in', 'साइन इन')}</h3>
          <input class="input" id="astroEmail" type="email" autocomplete="username" aria-label="${tr('Email', 'ईमेल')}">
          <input class="input" id="astroPass" type="password" autocomplete="current-password" aria-label="${tr('Password', 'पासवर्ड')}">
          <button class="btn block" type="submit">${tr('Sign in', 'साइन इन')}</button></form>`;
        $('#astroLogin').onsubmit = async e => {
          e.preventDefault();
          try {
            const got = await api('/api/connect/astro/login', {email: $('#astroEmail').value, password: $('#astroPass').value});
            sessionStorage.setItem('dr-astro-token', got.token);
            render();
          } catch (err) { showErr(err); }
        };
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
          sessionStorage.setItem('dr-astro-token', got.token);
          render();
        } catch (err) { showErr(err); }
      };
      return;
    }
    let incoming = {sessions: []};
    let money = {sessions: [], payouts: [], gross_label: '$0.00', fee_label: '$0.00', net_label: '$0.00', plan_status: 'none', renews_at: ''};
    try {
      incoming = await api('/api/connect/astro/incoming', null, token);
      money = await api('/api/connect/astro/earnings', null, token);
    } catch (e) { sessionStorage.removeItem('dr-astro-token'); render(); return; }
    const plans = planCards();
    const needsPlan = !money.plan_status || money.plan_status === 'none' || money.plan_status === 'canceled';
    const rows = (incoming.sessions || []).map(s => `<div class="card"><b>${esc(s.mode)} · ${esc(s.status)}</b><p>${esc(s.question || '')}</p>
      <button type="button" class="btn" data-accept="${esc(s.id)}">${tr('Accept', 'स्वीकार')}</button></div>`).join('');
    body.innerHTML = `${needsPlan ? `<div class="card" id="planPicker"><h3>${tr('Choose a plan', 'योजना चुनें')}</h3>
      <button type="button" class="btn block" id="planTrial">${plans.days} ${tr('day free trial', 'दिन का निःशुल्क परीक्षण')}</button>
      <p class="sub">${plans.days} ${tr('days free. No card.', 'दिन मुफ़्त। कार्ड नहीं।')}</p>
      <button type="button" class="btn block" id="planMonth">${tr('Monthly', 'मासिक')} ${esc(plans.month)}</button>
      <button type="button" class="btn block" id="planYear">${tr('Yearly', 'वार्षिक')} ${esc(plans.year)}</button>
      <p class="sub">${tr('Monthly or yearly starts after the free trial ends. No lifetime plan.', 'मासिक या वार्षिक योजना निःशुल्क परीक्षण के बाद शुरू होती है। कोई आजीवन योजना नहीं।')}</p></div>` : ''}
      <div class="card"><label class="spread">${tr('Online', 'ऑनलाइन')}<input type="checkbox" id="astroOnline"></label>
      <p class="sub">${tr('You keep 95%. Drishti keeps 5% of each session.', 'आप 95% रखते हैं। दृष्टि हर सत्र का 5% रखती है।')}</p>
      <p class="sub">${tr('Plan', 'योजना')}: ${esc(money.plan_status || 'none')}${money.renews_at ? ' · ' + esc(String(money.renews_at).slice(0, 10)) : ''}</p></div>
      ${rows || `<p class="sub">${tr('No waiting chat or call.', 'कोई प्रतीक्षारत चैट या कॉल नहीं।')}</p>`}
      <div class="card" id="earnings"><h3>${tr('Earnings', 'कमाई')}</h3>
        <p>${tr('Gross', 'कुल')} ${esc(money.gross_label || '$0.00')}</p>
        <p>${tr('Drishti fee', 'दृष्टि शुल्क')} ${esc(money.fee_label || '$0.00')}</p>
        <p><b>${tr('Net', 'शुद्ध')} ${esc(money.net_label || '$0.00')}</b></p>
        <button type="button" class="btn" id="managePlan">${tr('Manage plan', 'योजना प्रबंधित करें')}</button>
        <button type="button" class="btn quiet" id="astroStripe">${tr('Set up payouts', 'भुगतान सेट करें')}</button></div>`;
    const trial = $('#planTrial');
    if (trial) trial.onclick = () => api('/api/connect/astro/trial', {}, token).then(() => render()).catch(showErr);
    bindPaidButtons(token);
    $('#astroOnline').onchange = e => api('/api/connect/astro/online', {online: e.target.checked}, token).catch(showErr);
    document.querySelectorAll('[data-accept]').forEach(b => b.onclick = () => api('/api/connect/sessions/' + b.dataset.accept + '/accept', {}, token).then(() => render()).catch(showErr));
    $('#astroStripe').onclick = () => api('/api/connect/astro/stripe', {}, token).then(d => { if (d.url) location.href = d.url; }).catch(showErr);
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
  async function renderAdmin() {
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
    body.innerHTML = (data.astrologers || []).map(a => `<div class="card" data-id="${esc(a.id)}">
      <b>${esc(a.name)}</b> <span>${esc(a.status)}</span>
      <p>${esc(a.email)} · ${esc(a.rate_label || '')} · ${esc(a.plan_status || '')}</p>
      <p>${esc((a.languages || []).join(', '))}</p>
      <button type="button" class="btn" data-act="approve">${tr('Approve', 'स्वीकार')}</button>
      <button type="button" class="btn quiet" data-act="suspend">${tr('Suspend', 'रोकें')}</button>
      <button type="button" class="btn quiet" data-act="remove">${tr('Remove', 'हटाएँ')}</button>
    </div>`).join('') || `<p>${tr('No applications.', 'कोई आवेदन नहीं।')}</p>`;
    document.querySelectorAll('#portalBody [data-act]').forEach(b => b.onclick = async () => {
      const card = b.closest('[data-id]');
      try {
        await api('/api/connect/admin/astrologers/' + card.dataset.id, {action: b.dataset.act}, token);
        render();
      } catch (err) { showErr(err); }
    });
  }
  function takeover() {
    if (location.hash !== '#astro' && location.hash !== '#admin') return false;
    if (location.hash === '#admin') renderAdmin();
    else renderPortal();
    return true;
  }
  window.onAnswerClosed = function () { if (on()) offer(); };
  window.DrishtiConnect = {takeover: takeover, offer: offer, list: list};
  window.addEventListener('hashchange', () => { if (typeof render === 'function') render(); });
})();
