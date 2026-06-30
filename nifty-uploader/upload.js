const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const readline = require("readline");
const config = require("./config");

const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"]);

// ─── Helpers ────────────────────────────────────────────────────────────────

function loadProcessed() {
  if (!fs.existsSync(config.processedLog)) return new Set();
  return new Set(
    fs.readFileSync(config.processedLog, "utf8")
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
  );
}

function markProcessed(folderNames) {
  fs.appendFileSync(config.processedLog, folderNames.join("\n") + "\n", "utf8");
}

function getItemFolders() {
  return fs
    .readdirSync(config.itemsDir, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name);
}

function getImagesInFolder(folderName) {
  const fullPath = path.join(config.itemsDir, folderName);
  return fs
    .readdirSync(fullPath)
    .filter((f) => IMAGE_EXTENSIONS.has(path.extname(f).toLowerCase()))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }))
    .map((f) => path.join(fullPath, f));
}

function prompt(msg) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(msg, (answer) => { rl.close(); resolve(answer.trim()); });
  });
}

async function askBatchSize(pendingCount) {
  const defaultSize = config.batchSize || 5;
  const answer = await prompt(
    `\n📦 ${pendingCount} item(s) pending. How many this batch? (1-20, default ${defaultSize}): `
  );
  if (!answer) return defaultSize;
  const n = parseInt(answer, 10);
  if (isNaN(n) || n < 1) return defaultSize;
  return Math.min(n, 20);
}

async function waitForLogin(page) {
  console.log("\n🔐 Not logged in.");
  console.log("   → Log in to nifty.ai in the browser window.");
  console.log("   ⚠️  Do NOT close the browser.");
  console.log("   → Come back here and press ENTER once you're logged in...");
  await prompt("");
}

async function waitForBulkGeneratePage() {
  console.log("\n📍 Navigate to the Bulk Generate page in the browser.");
  console.log("   (Click 'Bulk generate' from your dashboard)");
  console.log("   ⚠️  Do NOT close the browser.");
  await prompt("   → Press ENTER once you're on the Bulk Generate page and see the upload area... ");
}

// ─── Run one batch ────────────────────────────────────────────────────────────

async function runBatch(page, batch) {
  await waitForBulkGeneratePage();
  console.log("\n🚀 Starting uploads...");

  for (let i = 0; i < batch.length; i++) {
    const folderName = batch[i];
    const images = getImagesInFolder(folderName);

    if (images.length === 0) {
      console.warn(`  ⚠️  No images found in ${folderName} — skipping.`);
      continue;
    }

    console.log(`\n  [${i + 1}/${batch.length}] Uploading ${folderName} (${images.length} image${images.length !== 1 ? "s" : ""})`);

    // Wait for the upload area
    const uploadArea = page.locator("text=Drop files here").or(
      page.locator("text=Browse files")
    ).last();
    await uploadArea.waitFor({ state: "visible", timeout: 15000 });

    // Trigger file chooser and set files
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      uploadArea.click(),
    ]);
    await fileChooser.setFiles(images);

    // Wait for uploads to finish
    await page.waitForTimeout(2000);
    try {
      const spinner = page.locator("[aria-label='loading'], .loading, .uploading").first();
      await spinner.waitFor({ state: "hidden", timeout: 30000 }).catch(() => {});
    } catch (_) {}
    await page.waitForTimeout(1000);

    // Select gender if configured
    if (config.gender) {
      try {
        const genderBtn = page.getByRole("button", { name: new RegExp(config.gender, "i") }).last();
        await genderBtn.click();
        await page.waitForTimeout(500);
      } catch (e) {
        console.warn(`  ⚠️  Could not select gender "${config.gender}": ${e.message}`);
      }
    }

    // "Add another item" between items, not after the last one
    if (i < batch.length - 1) {
      const addBtn = page.getByRole("button", { name: /add another item/i });
      await addBtn.click();
      await page.waitForTimeout(1000);
    }
  }

  // Click Generate
  console.log(`\n⚙️  Clicking "Generate ${batch.length} items"...`);
  const generateBtn = page
    .getByRole("button", { name: new RegExp(`generate ${batch.length}`, "i") })
    .or(page.getByRole("button", { name: /generate \d+ items?/i }));
  await generateBtn.click();
  await page.waitForTimeout(3000);

  // Mark as done
  markProcessed(batch);
  console.log(`\n✅ Batch complete! Uploaded ${batch.length} items:`);
  batch.forEach((f) => console.log(`  - ${f}`));
  console.log(`\n📝 Progress saved to ${config.processedLog}`);
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  // Launch browser once — it stays open across all batches
  const authExists = fs.existsSync(config.authState);
  const browser = await chromium.launch({ headless: config.headless });
  const context = authExists
    ? await browser.newContext({ storageState: config.authState })
    : await browser.newContext();
  const page = await context.newPage();

  // Check login
  await page.goto("https://app.nifty.ai/", { waitUntil: "networkidle" });
  const isLoggedIn = await page.evaluate(() =>
    !document.querySelector('input[type="password"]') &&
    !window.location.href.includes("/login") &&
    !window.location.href.includes("/signin")
  );

  if (!isLoggedIn) {
    await waitForLogin(page);
    await context.storageState({ path: config.authState });
    console.log("💾 Login saved to auth.json — future runs won't need manual login.");
  }

  // ── Session loop ───────────────────────────────────────────────────────────
  while (true) {
    // Re-read processed each loop so it reflects the previous batch
    const processed = loadProcessed();
    const allFolders = getItemFolders();
    const pending = allFolders.filter((f) => !processed.has(f));

    if (pending.length === 0) {
      console.log("\n✅ All items have been processed — nothing left to upload.");
      break;
    }

    const batchSize = await askBatchSize(pending.length);
    const batch = pending.slice(0, batchSize);

    console.log(`\n📋 Batch of ${batch.length}:`);
    batch.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));

    await runBatch(page, batch);

    // After each batch: ask whether to run another or stop
    const again = await prompt("\n▶  Run another batch? (ENTER to continue, q to quit): ");
    if (again.toLowerCase() === "q") break;
  }

  await prompt("\n   Press ENTER to close the browser... ");
  await browser.close();
  console.log("\n👋 Done for now.\n");
}

main().catch((err) => {
  console.error("\n❌ Error:", err.message);
  process.exit(1);
});
