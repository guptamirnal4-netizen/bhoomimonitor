import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (err) => console.log("[pageerror]", err.message));

await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await page.click("text=31-District Monitor");
await page.waitForTimeout(2000);

// Click Check Now while still in cooldown from the earlier curl trigger
await page.click("text=Check Now");
await page.waitForTimeout(2000);
await page.screenshot({ path: "/tmp/dashboard_checknow_cooldown.png", clip: { x: 0, y: 220, width: 1400, height: 100 } });
console.log("captured cooldown message state");

// Exercise the check-type filter dropdown
await page.selectOption("select >> nth=1", "manual");
await page.waitForTimeout(1500);
await page.screenshot({ path: "/tmp/dashboard_checknow_filtered.png", clip: { x: 0, y: 1030, width: 1400, height: 250 } });
console.log("captured filtered log state");

await browser.close();
