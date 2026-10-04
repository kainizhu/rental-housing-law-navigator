// Scripted 60 s product-demo capture of the static site (no human mouse, reproducible).
//   cd docs && python3 -m http.server 8766 &   then   node video/record_demo.js
// Output: video/out/demo_raw.webm (1920x1080). Beats follow video/scripts.md section 2.
const { chromium } = require(process.env.PW_MODULE || 'playwright');
const path = require('path');
const URL = process.env.DEMO_URL || 'http://localhost:8766/index.html';
const OUT = path.join(__dirname, 'out');
const W = 1920, H = 1080, Z = 1.3333;   // full-HD frame, page laid out as if 1440x810
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME || '/opt/pw-browsers/chromium' });
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, colorScheme: 'light',
    recordVideo: { dir: OUT, size: { width: 1920, height: 1080 } } });
  const p = await ctx.newPage();
  const t0 = Date.now(); const mark = s => console.log(((Date.now() - t0) / 1000).toFixed(1).padStart(5), s);
  await p.goto(URL + '#A0002'); await p.waitForSelector('.rrow');
  await p.evaluate(z => { document.documentElement.style.zoom = z }, Z);
  // visible cursor + smooth scrolling
  await p.addStyleTag({ content: `#fc{position:fixed;z-index:99;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
      background:rgba(14,90,99,.25);border:2px solid #0E5A63;pointer-events:none;transition:transform .12s}
      #fc.down{transform:scale(.7)} html{scroll-behavior:smooth}` });
  await p.evaluate(() => { const c = document.createElement('div'); c.id = 'fc'; document.body.appendChild(c);
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px' });
    addEventListener('mousedown', () => c.classList.add('down')); addEventListener('mouseup', () => c.classList.remove('down')); });
  let mx = W / 2, my = H / 2;
  const move = async (x, y, ms = 700) => { const n = Math.max(8, Math.round(ms / 16)); await p.mouse.move(x, y, { steps: n }); mx = x; my = y; };
  const to = async (sel, ms = 700) => { const b = await p.locator(sel).first().boundingBox(); await move(b.x + Math.min(b.width / 2, 160), b.y + b.height / 2, ms); return b; };
  const click = async (sel, ms) => { await to(sel, ms); await sleep(150); await p.mouse.down(); await sleep(90); await p.mouse.up(); };
  const scrollTo = async (sel, block = 'center') => { await p.locator(sel).first().evaluate((e, b) => e.scrollIntoView({ behavior: 'smooth', block: b }), block); await sleep(900); };
  const wheel = async (dy, ms) => { const n = Math.round(ms / 40); for (let i = 0; i < n; i++) { await p.mouse.wheel(0, dy / n); await sleep(40); } };

  // 0–5  address open
  mark('open'); await sleep(1200); await move(700, 300, 1200); await sleep(2400);
  // 5–17 summary pills, then facts (observed / inferred / not in data)
  mark('facts'); await to('.summary', 900); await sleep(1800);
  await to('.fact:nth-child(1)', 900); await sleep(1800);
  await to('.fact:nth-child(2)', 700); await sleep(1500);
  await to('.fact:nth-child(4)', 700); await sleep(2000);
  // 17–32 open the Hoboken algorithmic-pricing rule and walk the proof chain
  mark('proof'); await scrollTo('.rrow[data-r="NJ-ALG-01"]', 'start'); await wheel(-90, 300);
  await click('.rrow[data-r="NJ-ALG-01"]', 900); await sleep(1300);
  for (const n of [2, 3, 4, 6]) { await scrollTo(`.proof ol>li:nth-child(${n})`, n === 6 ? 'center' : 'center'); await to(`.proof ol>li:nth-child(${n}) h4`, 350); await sleep(n === 6 ? 2400 : 800); }
  // 32–43 drag the as-of slider to 2027-07-01; NJ FAIR Act applies + conflict flag
  mark('slider'); await p.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' })); await sleep(400);
  const s = await p.locator('#asof').boundingBox();
  const n = await p.evaluate(() => window.NAV.dates.length), from = await p.evaluate(() => window.NAV.dates.indexOf(window.NAV.as_of));
  const target = await p.evaluate(() => window.NAV.dates.indexOf('2027-07-01'));
  const xAt = i => s.x + 8 + (s.width - 16) * i / (n - 1);
  await move(xAt(from), s.y + s.height / 2, 800); await p.mouse.down(); await sleep(120);
  for (let i = from + 1; i <= target; i++) {   // step the range input the way a drag would
    await move(xAt(i), s.y + s.height / 2, 200);
    await p.evaluate(v => { const r = document.getElementById('asof'); r.value = v; r.dispatchEvent(new Event('input', { bubbles: true })); }, i);
  }
  await p.mouse.up(); await sleep(700);
  await scrollTo('.rrow[data-r="NJ-ALG-01"]', 'start'); await wheel(-60, 200); await to('.rrow[data-r="NJ-ALG-01"] .rs', 700); await sleep(1600);
  await to('.proof ol>li:nth-child(2) h4', 600); await sleep(1500);
  await to('.rrow[data-r="HOB-ALG-01"] .rs', 600); await sleep(1400);
  // 43–53 uncertainty
  mark('uncertainty'); await p.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' })); await sleep(300); await click('nav.tabs button[data-tab="uncertainty"]', 800); await sleep(1500);
  await to('.hbar', 600); await sleep(1500);
  await scrollTo('.cards .card:nth-child(2)'); await to('.cards .card:nth-child(2) .big', 700); await sleep(2600);
  // 53–60 coverage map
  mark('coverage'); await p.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' })); await sleep(300); await click('nav.tabs button[data-tab="coverage"]', 700); await sleep(1200);
  await scrollTo('table.grid', 'start'); await move(900, 500, 600); await wheel(420, 2600); await sleep(1400);
  mark('end');
  await ctx.close(); await browser.close();
})();
