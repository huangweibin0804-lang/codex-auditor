#!/usr/bin/env node
/** Render a local report HTML file to a downloadable PDF with bundled Playwright. */
const fs = require("fs");
const path = require("path");
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (error) {
  const modulePath = process.env.CODEX_PLAYWRIGHT_MODULE;
  if (!modulePath) {
    console.error("Playwright is required for PDF rendering. Install it with `npm install playwright`, or set CODEX_PLAYWRIGHT_MODULE to an existing Playwright module path.");
    process.exit(1);
  }
  ({ chromium } = require(modulePath));
}

async function main() {
  const [htmlPath, pdfPath] = process.argv.slice(2);
  if (!htmlPath || !pdfPath) {
    throw new Error("usage: render_report_pdf.js REPORT.html REPORT.pdf");
  }
  if (!fs.existsSync(htmlPath)) {
    throw new Error(`HTML report not found: ${htmlPath}`);
  }
  fs.mkdirSync(path.dirname(pdfPath), { recursive: true, mode: 0o700 });
  const bundledPath = chromium.executablePath();
  const executablePath = fs.existsSync(bundledPath)
    ? bundledPath
    : "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1200 }, deviceScaleFactor: 1 });
    await page.goto(`file://${path.resolve(htmlPath)}`, { waitUntil: "networkidle" });
    await page.emulateMedia({ media: "print" });
    await page.pdf({
      path: pdfPath,
      format: "A4",
      printBackground: true,
      preferCSSPageSize: true,
      margin: { top: "12mm", right: "12mm", bottom: "12mm", left: "12mm" },
    });
  } finally {
    await browser.close();
  }
  fs.chmodSync(pdfPath, 0o600);
  const stat = fs.statSync(pdfPath);
  if (stat.size < 1024) {
    throw new Error(`PDF output is unexpectedly small: ${pdfPath}`);
  }
  console.log(JSON.stringify({ html: htmlPath, pdf: pdfPath, bytes: stat.size }));
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
