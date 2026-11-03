// Print a local HTML file to PDF with headless Chromium (playwright).
// usage: node pdf_render.js <in.html> <out.pdf>
// Exit 0 = PDF written; nonzero = failure (message on stderr).
const { chromium } = require('playwright');

(async () => {
  const [, , inPath, outPath] = process.argv;
  if (!inPath || !outPath) {
    console.error('usage: node pdf_render.js <in.html> <out.pdf>');
    process.exit(2);
  }
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.goto('file://' + inPath, { waitUntil: 'load' });
    await page.emulateMedia({ media: 'print' });
    await page.pdf({
      path: outPath,
      format: 'A4',
      printBackground: true,
      preferCSSPageSize: true,
    });
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
