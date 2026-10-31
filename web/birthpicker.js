// Birth date and time for the onboarding wheels.
// Stored values stay YYYY-MM-DD and 24-hour HH:MM, same as the old inputs.
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.BirthPicker = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const MONTHS_EN = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const MONTHS_HI = ['जनवरी', 'फ़रवरी', 'मार्च', 'अप्रैल', 'मई', 'जून', 'जुलाई', 'अगस्त', 'सितंबर', 'अक्टूबर', 'नवंबर', 'दिसंबर'];
  const YEAR_FLOOR = 1900;

  function pad2(n) { return String(n).padStart(2, '0'); }

  function daysInMonth(year, month) {
    return new Date(year, month, 0).getDate();
  }

  function todayParts(today) {
    const d = today || new Date();
    return {y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate()};
  }

  function parseDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || '').trim());
    if (!m) return null;
    const y = +m[1], mo = +m[2], d = +m[3];
    if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo)) return null;
    return {y, m: mo, d};
  }

  function formatDate(p) {
    return p.y + '-' + pad2(p.m) + '-' + pad2(p.d);
  }

  // Drop days the month does not have, and do not allow a date after today.
  function clampDate(parts, today) {
    const t = todayParts(today);
    let y = parts.y, m = parts.m, d = parts.d;
    if (y > t.y) y = t.y;
    if (m < 1) m = 1;
    if (m > 12) m = 12;
    if (y === t.y && m > t.m) m = t.m;
    let dim = daysInMonth(y, m);
    if (d < 1) d = 1;
    if (d > dim) d = dim;
    if (y === t.y && m === t.m && d > t.d) d = t.d;
    dim = daysInMonth(y, m);
    if (d > dim) d = dim;
    return {y, m, d};
  }

  function resolveDate(iso, today) {
    const parsed = parseDate(iso);
    if (!parsed) return {y: 1990, m: 6, d: 15};
    return clampDate(parsed, today);
  }

  function yearOptions(today, minYear) {
    const max = todayParts(today).y;
    const min = Math.min(YEAR_FLOOR, minYear || YEAR_FLOOR);
    const years = [];
    for (let y = max; y >= min; y--) years.push(y);
    return years;
  }

  function monthOptions(year, today, lang) {
    const t = todayParts(today);
    const last = year === t.y ? t.m : 12;
    const names = lang === 'hi' ? MONTHS_HI : MONTHS_EN;
    const out = [];
    for (let m = 1; m <= last; m++) out.push({value: m, label: names[m - 1]});
    return out;
  }

  function dayOptions(year, month, today) {
    const t = todayParts(today);
    const dim = daysInMonth(year, month);
    const last = year === t.y && month === t.m ? Math.min(dim, t.d) : dim;
    const out = [];
    for (let d = 1; d <= last; d++) out.push({value: d, label: String(d)});
    return out;
  }

  function labelDate(p, lang) {
    const names = lang === 'hi' ? MONTHS_HI : MONTHS_EN;
    return p.d + ' ' + names[p.m - 1] + ' ' + p.y;
  }

  function parseTime(hhmm) {
    const m = /^(\d{2}):(\d{2})$/.exec(String(hhmm || '').trim());
    if (!m) return null;
    const h = +m[1], min = +m[2];
    if (h > 23 || min > 59) return null;
    const ap = h >= 12 ? 'PM' : 'AM';
    let h12 = h % 12;
    if (h12 === 0) h12 = 12;
    return {h12, min, ap};
  }

  function formatTime(t) {
    let h = t.h12 % 12;
    if (t.ap === 'PM') h += 12;
    return pad2(h) + ':' + pad2(t.min);
  }

  function resolveTime(hhmm) {
    return parseTime(hhmm) || {h12: 12, min: 0, ap: 'PM'};
  }

  function labelTime(t, lang) {
    const ap = t.ap === 'AM' ? (lang === 'hi' ? 'पूर्वाह्न' : 'AM') : (lang === 'hi' ? 'अपराह्न' : 'PM');
    return t.h12 + ':' + pad2(t.min) + ' ' + ap;
  }

  function hourOptions() {
    const out = [];
    for (let h = 1; h <= 12; h++) out.push({value: h, label: String(h)});
    return out;
  }

  function minuteOptions() {
    const out = [];
    for (let m = 0; m <= 59; m++) out.push({value: m, label: pad2(m)});
    return out;
  }

  function ampmOptions(lang) {
    return [
      {value: 'AM', label: lang === 'hi' ? 'पूर्वाह्न' : 'AM'},
      {value: 'PM', label: lang === 'hi' ? 'अपराह्न' : 'PM'},
    ];
  }

  return {
    YEAR_FLOOR, MONTHS_EN, MONTHS_HI, daysInMonth, parseDate, formatDate, clampDate, resolveDate,
    yearOptions, monthOptions, dayOptions, labelDate, parseTime, formatTime, resolveTime, labelTime,
    hourOptions, minuteOptions, ampmOptions,
  };
});
