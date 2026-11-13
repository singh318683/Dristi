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
        const err = new Error((data && (data.detail && data.detail.error || data.detail)) || ('HTTP ' + r.status));
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
  function showErr(e) {
    const msg = (e && e.message) || tr('Something went wrong', 'कुछ गड़बड़ हुई');
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
      ${rows || `<p class="sub">${tr('No astrologer is online right now.', 'अभी कोई ज्योतिषी ऑनलाइन नहीं है।')}</p>`}
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
    if (window.DrishtiStore && typeof DrishtiStore.purchase === 'function') {
      const bought = await DrishtiStore.purchase(product);
      const txn = bought && (bought.transactionId || bought.transaction_id);
      if (!txn) throw new Error('cancelled');
      await api('/api/connect/wallet/apple', {device: DEVICE, product_id: product, transaction_id: txn, signed_transaction: bought.signedTransaction || ''});
      return;
    }
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
    root.innerHTML = `<div class="portal"><div class="spread"><b>${esc(title)}</b><a href="./">${tr('App', 'ऐप')}</a></div><div id="portalBody"></div></div>`;
    document.body.classList.remove('app-fit');
    return $('#portalBody');
  }
  async function renderPortal() {
    const body = portalRoot(tr('Astrologer', 'ज्योतिषी'));
    const token = sessionStorage.getItem('dr-astro-token') || '';
    if (!on()) { body.innerHTML = `<p>${tr('This is not switched on yet.', 'यह अभी चालू नहीं है।')}</p>`; return; }
    if (!token) {
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
      return;
    }
    let incoming = {sessions: []};
    let money = {sessions: [], payouts: [], astro_cents: 0, platform_cents: 0};
    try {
      incoming = await api('/api/connect/astro/incoming', null, token);
      money = await api('/api/connect/astro/earnings', null, token);
    } catch (e) { sessionStorage.removeItem('dr-astro-token'); render(); return; }
    const rows = (incoming.sessions || []).map(s => `<div class="card"><b>${esc(s.mode)} · ${esc(s.status)}</b><p>${esc(s.question || '')}</p>
      <button type="button" class="btn" data-accept="${esc(s.id)}">${tr('Accept', 'स्वीकार')}</button></div>`).join('');
    const earned = (money.sessions || []).map(s => `<p>${esc(s.mode)} · ${tr('you', 'आप')} ${s.astro_cents} · ${tr('platform', 'प्लेटफ़ॉर्म')} ${s.platform_cents}</p>`).join('');
    const paid = (money.payouts || []).map(p => `<p>${esc(p.status)} · ${p.amount_cents}</p>`).join('');
    body.innerHTML = `<div class="card"><label class="spread">${tr('Online', 'ऑनलाइन')}<input type="checkbox" id="astroOnline"></label>
      <p class="sub">${tr('75% of the net session is yours. 25% is the platform.', 'शुद्ध राशि का 75% आपका है। 25% प्लेटफ़ॉर्म का।')}</p></div>
      ${rows || `<p class="sub">${tr('No waiting chat or call.', 'कोई प्रतीक्षारत चैट या कॉल नहीं।')}</p>`}
      <div class="card"><h3>${tr('Earnings', 'कमाई')}</h3>${earned || `<p>${tr('No sessions yet.', 'अभी कोई सत्र नहीं।')}</p>`}
        <h3>${tr('Payouts', 'भुगतान')}</h3>${paid || `<p>${tr('No payout yet. Stripe pays a week after the session.', 'अभी भुगतान नहीं। सत्र के एक सप्ताह बाद Stripe भेजता है।')}</p>`}
        <button type="button" class="btn quiet" id="astroStripe">${tr('Set up payouts', 'भुगतान सेट करें')}</button></div>`;
    $('#astroOnline').onchange = e => api('/api/connect/astro/online', {online: e.target.checked}, token).catch(showErr);
    document.querySelectorAll('[data-accept]').forEach(b => b.onclick = () => api('/api/connect/sessions/' + b.dataset.accept + '/accept', {}, token).then(() => render()).catch(showErr));
    $('#astroStripe').onclick = () => api('/api/connect/astro/stripe', {}, token).then(d => { if (d.url) location.href = d.url; }).catch(showErr);
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
    body.innerHTML = (data.astrologers || []).map(a => `<form class="card" data-id="${esc(a.id)}">
      <b>${esc(a.name)}</b> <span>${esc(a.status)}</span>
      <p>${esc(a.email)} · ${esc((a.languages || []).join(', '))}</p>
      <input class="input" name="rate" inputmode="numeric" value="${a.rate_cents || 100}" aria-label="${tr('Cents per minute', 'प्रति मिनट सेंट')}">
      <button class="btn" name="approve" value="1" type="submit">${tr('Approve', 'स्वीकार')}</button>
      <button class="btn quiet" name="approve" value="0" type="submit">${tr('Reject', 'अस्वीकार')}</button>
    </form>`).join('') || `<p>${tr('No applications.', 'कोई आवेदन नहीं।')}</p>`;
    document.querySelectorAll('#portalBody form').forEach(form => form.onsubmit = async e => {
      e.preventDefault();
      const approve = e.submitter && e.submitter.value === '1';
      try {
        await api('/api/connect/admin/astrologers/' + form.dataset.id, {approve: approve, rate_cents: Number(form.rate.value || 0), rating_tenths: 48}, token);
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
