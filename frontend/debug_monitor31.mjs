import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1600 } });
page.on("pageerror", (err) => console.log("[pageerror]", err.message));

await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);
await page.click("text=31-District Monitor");
await page.waitForTimeout(2500); // allow fetchMonitor31() to complete
await page.screenshot({ path: "/tmp/dashboard_monitor31.png", fullPage: true });
console.log("done");

await browser.close();
