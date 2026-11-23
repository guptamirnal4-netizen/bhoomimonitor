import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1400 } });
page.on("pageerror", (err) => console.log("[pageerror]", err.message));

await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.click("text=Portal Reliability");
await page.waitForTimeout(1000);
await page.screenshot({ path: "/tmp/dashboard_reliability.png", fullPage: true });
console.log("done");

await browser.close();
