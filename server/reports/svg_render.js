// Rasterise stand-alone SVG figures to PNG with headless Chromium (playwright).
// usage: node svg_render.js <scale> <in1.svg> <out1.png> [<in2.svg> <out2.png> ...]
// One browser launch serves every pair. Exit 0 = every PNG written.
const fs = require('fs');
const { chromium } = require('playwright');

(async () => {
  const [, , scaleArg, ...rest] = process.argv;
  const scale = Math.max(1, Math.min(4, parseFloat(scaleArg) || 2));
  if (rest.length < 2 || rest.length % 2) {
    console.error('usage: node svg_render.js <scale> <in.svg> <out.png> ...');
    process.exit(2);
  }
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const ctx = await browser.newContext({ deviceScaleFactor: scale });
    const page = await ctx.newPage();
    for (let i = 0; i < rest.length; i += 2) {
      const svg = fs.readFileSync(rest[i], 'utf8');
      await page.setContent(
        '<!doctype html><html><body style="margin:0;background:#fff">' +
        svg + '</body></html>');
      const el = await page.$('svg.sm-chart');
      if (!el) throw new Error('no figure in ' + rest[i]);
      await el.screenshot({ path: rest[i + 1] });
    }
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
