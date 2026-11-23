import { chromium } from "playwright";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });

page.on("console", (msg) => console.log(`[console.${msg.type()}]`, msg.text()));
page.on("pageerror", (err) => console.log("[pageerror]", err.message));
page.on("requestfailed", (req) => console.log("[requestfailed]", req.url(), req.failure()?.errorText));

await page.goto("http://127.0.0.1:5173/", { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await page.screenshot({ path: "/tmp/dashboard_screenshot2.png", fullPage: true });

const rootHtml = await page.evaluate(() => document.getElementById("root")?.innerHTML?.slice(0, 500));
console.log("[root innerHTML preview]", rootHtml);

await browser.close();
