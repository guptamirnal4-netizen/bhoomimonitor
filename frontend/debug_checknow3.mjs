import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (err) => console.log("[pageerror]", err.message));

await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await page.click("text=31-District Monitor");
await page.waitForTimeout(2000);

// Wait for the "Checking all 31..." button to return to its normal label
// (the real sweep triggered a moment ago in the previous script)
await page.waitForFunction(
  () => !document.body.innerText.includes("Checking all 31"),
  { timeout: 60000 }
);
await page.waitForTimeout(500);
await page.screenshot({ path: "/tmp/dashboard_checknow_done.png", clip: { x: 0, y: 220, width: 1400, height: 100 } });
console.log("captured completed state");

await browser.close();
