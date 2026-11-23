import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1800 } });
page.on("pageerror", (err) => console.log("[pageerror]", err.message));

await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await page.click("text=31-District Monitor");
await page.waitForTimeout(2500);
await page.screenshot({ path: "/tmp/dashboard_checknow_before.png", fullPage: true });
console.log("captured 'before' state");

await browser.close();
