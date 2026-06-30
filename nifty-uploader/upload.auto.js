/**
 * upload.auto.js — Non-interactive version of upload.js for pipeline use.
 *
 * Changes from upload.js:
 *  - No prompts: auto-navigates to bulk-add page, processes all pending items
 *  - Batches automatically using config.batchSize until nothing is left
 *  - Browser closes automatically when done
 *
 * Original upload.js is untouched — this is a parallel copy.
 */

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const config = require("./config");

const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"]);
const BULK_ADD_URL   = "https://app.nifty.ai/inventory/bulk-add";
const SESSION_FILE   = path.join(__dirname, "pipeline_session.json");

// ─── Helpers (identical to upload.js) ────────────────────────────────────────

function loadProcessed() {
  if (!fs.existsSync(config.processedLog)) return new Set();
  return new Set(
    fs.readFileSync(config.processedLog, "utf8")
      .split("\n").map((l) => l.trim()).filter(Boolean)
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

// ─── Run one batch (auto version — navigates to page instead of prompting) ───

async function runBatch(page, batch) {
  console.log(`\n  Navigating to bulk-add page...`);
  await page.goto(BULK_ADD_URL, { waitUntil: "networkidle", timeout: 20000 });
  await page.waitForTimeout(1500);

  console.log(`\n  Uploading ${batch.length} item(s)...`);

  for (let i = 0; i < batch.length; i++) {
    const folderName = batch[i];
    const images = getImagesInFolder(folderName);

    if (images.length === 0) {
      console.warn(`  WARNING: No images in ${folderName} — skipping.`);
      continue;
    }

    console.log(`  [${i + 1}/${batch.length}] ${folderName} (${images.length} image(s))`);

    const uploadArea = page.locator("text=Drop files here").or(
      page.locator("text=Browse files")
    ).last();
    await uploadArea.waitFor({ state: "visible", timeout: 15000 });

    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      uploadArea.click(),
    ]);
    await fileChooser.setFiles(images);

    await page.waitForTimeout(2000);
    try {
      const spinner = page.locator("[aria-label='loading'], .loading, .uploading").first();
      await spinner.waitFor({ state: "hidden", timeout: 30000 }).catch(() => {});
    } catch (_) {}
    await page.waitForTimeout(1000);

    if (config.gender) {
      try {
        const genderBtn = page.getByRole("button", { name: new RegExp(config.gender, "i") }).last();
        await genderBtn.click();
        await page.waitForTimeout(500);
      } catch (e) {
        console.warn(`  WARNING: Could not select gender: ${e.message}`);
      }
    }

    if (i < batch.length - 1) {
      const addBtn = page.getByRole("button", { name: /add another item/i });
      await addBtn.click();
      await page.waitForTimeout(1000);
    }
  }

  console.log(`\n  Clicking "Generate ${batch.length} items"...`);
  const generateBtn = page
    .getByRole("button", { name: new RegExp(`generate ${batch.length}`, "i") })
    .or(page.getByRole("button", { name: /generate \d+ items?/i }));
  await generateBtn.click();
  await page.waitForTimeout(3000);

  markProcessed(batch);
  console.log(`  Batch complete: ${batch.join(", ")}`);
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const authExists = fs.existsSync(config.authState);
  const browser = await chromium.launch({ headless: config.headless });
  const context = authExists
    ? await browser.newContext({ storageState: config.authState })
    : await browser.newContext();
  const page = await context.newPage();

  // Login check — if not logged in, save state and bail (user must run manually first)
  await page.goto("https://app.nifty.ai/", { waitUntil: "networkidle" });
  const isLoggedIn = await page.evaluate(() =>
    !document.querySelector('input[type="password"]') &&
    !window.location.href.includes("/login") &&
    !window.location.href.includes("/signin")
  );

  if (!isLoggedIn) {
    console.error("\nERROR: Not logged in to nifty.ai.");
    console.error("Run upload.js manually once to log in, then re-run the pipeline.");
    await browser.close();
    process.exit(1);
  }

  // Resolve items: session file > scan itemsDir; optionally limit by --count arg
  const cliCount = (() => {
    const i = process.argv.indexOf("--count");
    return i !== -1 ? parseInt(process.argv[i + 1], 10) : null;
  })();

  let sessionItems;
  if (fs.existsSync(SESSION_FILE)) {
    const session = JSON.parse(fs.readFileSync(SESSION_FILE, "utf8"));
    sessionItems = session.items || [];
    console.log(`\n[Upload] Session file: ${sessionItems.length} item(s) to upload`);
  } else {
    const processed = loadProcessed();
    sessionItems = getItemFolders().filter((f) => !processed.has(f));
    console.log(`\n[Upload] No session file — scanning itemsDir: ${sessionItems.length} unprocessed item(s) found`);
  }

  if (cliCount && cliCount > 0 && cliCount < sessionItems.length) {
    sessionItems = sessionItems.slice(0, cliCount);
    console.log(`[Upload] Limiting to ${cliCount} item(s) per --count argument`);
  }

  const batchSize = config.batchSize || 5;
  let totalUploaded = 0;
  const remaining = [...sessionItems];

  while (remaining.length > 0) {
    const batch = remaining.splice(0, batchSize);
    // Filter to folders that actually exist and haven't been uploaded yet
    const processed = loadProcessed();
    const toUpload = batch.filter((f) => {
      const folderPath = path.join(config.itemsDir, f);
      return fs.existsSync(folderPath) && !processed.has(f);
    });

    if (toUpload.length === 0) {
      console.log(`  Skipping batch — all already uploaded or missing.`);
      continue;
    }

    console.log(`\n[Upload] Uploading batch of ${toUpload.length}...`);
    await runBatch(page, toUpload);
    totalUploaded += toUpload.length;
    await page.waitForTimeout(2000);
  }

  await browser.close();
  console.log(`\n[Upload] Done. ${totalUploaded} item(s) uploaded.`);
}

main().catch((err) => {
  console.error("\nFatal error in upload.auto.js:", err.message);
  process.exit(1);
});
