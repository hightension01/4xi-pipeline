/**
 * list.auto.js — Non-interactive version of list.js for pipeline use.
 *
 * Changes from list.js:
 *  - No prompts: navigates directly to the In Progress drafts URL
 *  - Polls until drafts appear (nifty.ai needs time to generate them after upload)
 *  - Processes all available drafts, loops until none remain
 *  - Browser closes automatically when done
 *
 * Original list.js is untouched — this is a parallel copy.
 */

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const os = require("os");
const config = require("./config");
const vision = require("./vision");

const DRAFTS_URL      = "https://app.nifty.ai/inventory?filter=drafted_in_progress";
const POLL_INTERVAL   = 30000;  // ms between checks while waiting for drafts to appear
const POLL_TIMEOUT    = 600000; // 10 minutes max wait for nifty.ai to generate drafts
const ROW_SEL         = 'tr.MuiTableRow-root:not(.MuiTableRow-head)';
const SESSION_FILE    = path.join(__dirname, "pipeline_session.json");

// ─── Helpers (identical to list.js) ──────────────────────────────────────────

function log(msg)   { console.log(`  ${msg}`); }
function warn(msg)  { console.warn(`  WARNING: ${msg}`); }
function sep(label) { console.log(`\n${"─".repeat(60)}\n  ${label}\n${"─".repeat(60)}`); }

async function screenshot(page) {
  return (await page.screenshot({ type: "jpeg", quality: 85 })).toString("base64");
}

async function dismissSyncModal(page) {
  // Nifty's sync-complete modal is identified by its illustration image
  try {
    const syncModal = page.locator('div.MuiDialog-root').filter({
      has: page.locator('img[src*="sync-complete"]'),
    }).first();
    if (await syncModal.isVisible({ timeout: 3000 })) {
      const closeBtn = syncModal.locator('button[aria-label*="close" i]')
        .or(syncModal.locator('button').last());
      await closeBtn.click({ force: true, timeout: 2000 });
      await page.waitForTimeout(400);
      log("Dismissed sync modal");
    }
  } catch (_) {}
}

async function dismissNotifications(page) {
  await dismissSyncModal(page);
  const selectors = [
    '[class*="MuiSnackbar"] button[aria-label*="close" i]',
    '[class*="MuiAlert"] button[aria-label*="close" i]',
    '[class*="snackbar"] button[aria-label*="close" i]',
    '[class*="toast"] button[aria-label*="close" i]',
    '[class*="Toast"] button[aria-label*="close" i]',
    '[class*="notification"] button[aria-label*="close" i]',
    'button[aria-label="close" i]',
    'button[aria-label="Close"]',
    '[class*="sync" i] button[aria-label*="close" i]',
  ];
  for (const sel of selectors) {
    try {
      const btn = page.locator(sel).first();
      if (await btn.isVisible({ timeout: 150 })) {
        await btn.click({ timeout: 500, force: true });
        await page.waitForTimeout(300);
      }
    } catch (_) {}
  }
  try {
    const modal = page.locator('[role="presentation"].MuiModal-root').first();
    if (await modal.isVisible({ timeout: 150 })) {
      await page.keyboard.press('Escape');
      await page.waitForTimeout(400);
    }
  } catch (_) {}
}

function photoCards(page) {
  return page.locator('div.css-graizw[role="button"]');
}

async function getPhotoSrc(card) {
  return card.locator(".css-ffxgpt img").getAttribute("src");
}

async function deletePhotoAt(page, index) {
  const cards = photoCards(page);
  const card  = cards.nth(index);
  try {
    await card.hover();
    await page.waitForTimeout(300);
    await card.locator(".remove button").click({ timeout: 5000 });
    await page.waitForTimeout(600);
    const confirm = page.getByRole("button", { name: /confirm|yes|delete|remove/i }).first();
    if (await confirm.isVisible({ timeout: 1500 })) await confirm.click();
    log(`Deleted photo ${index + 1}`);
    return true;
  } catch (e) {
    warn(`Could not delete photo ${index + 1}: ${e.message}`);
    return false;
  }
}

async function extractItemMeta(page) {
  return page.evaluate(() => {
    function inputNearLabel(keyword) {
      const els = [...document.querySelectorAll('label, p, span, h4, h5, h6, div')];
      for (const el of els) {
        if (el.children.length > 0) continue;
        if (!new RegExp(keyword, 'i').test(el.textContent.trim())) continue;
        let node = el;
        for (let i = 0; i < 6; i++) {
          node = node.parentElement;
          if (!node) break;
          const input = node.querySelector('input[type="text"], input:not([type]), textarea');
          if (input && input.value) return input.value;
        }
      }
      return null;
    }
    const title = inputNearLabel('title') ||
                  document.querySelector('h1, h2')?.textContent?.trim() ||
                  document.title.replace(/\s*[-|].*$/, '').trim();
    const brand = inputNearLabel('brand') ||
                  document.querySelector('input[placeholder*="brand" i]')?.value ||
                  null;
    return { title: title || null, brand: brand || null };
  });
}

const GARMENT_WORDS = /\b(hoodie|hoody|sweatshirt|jacket|coat|parka|windbreaker|bomber|vest|cardigan|sweater|pullover|fleece|dress|gown|sundress|romper|jumpsuit|skirt|pants|jeans|shorts|shirt|tee|t-shirt|top|blouse|leggings|joggers|tracksuit|blazer|suit|overalls|kimono|poncho|tunic)\b/i;
const GENDER_WORDS  = /\b(women|womens|men|mens|boys|girls|kids|youth|unisex|juniors)\b/i;
const SIZE_WORDS    = /\b(XXS|XS|SM|S|M|MD|L|LG|XL|XXL|2XL|3XL|4XL|5XL|\d+[WT]|\d+\/\d+|one size)\b/i;

function buildSearchQuery(brand, title) {
  if (!title && !brand) return null;

  const t = title || '';
  const garmentMatch = t.match(GARMENT_WORDS);
  const genderMatch  = t.match(GENDER_WORDS);
  const sizeMatch    = t.match(SIZE_WORDS);

  const garment = garmentMatch ? garmentMatch[0] : null;
  const gender  = genderMatch  ? genderMatch[0].replace(/s$/i, m => m === 'S' ? 's' : '').replace(/mens/i, 'Men').replace(/womens/i, 'Women') : null;
  const size    = sizeMatch    ? sizeMatch[0].toUpperCase() : null;

  const parts = [brand, garment, gender, size].filter(Boolean);
  // Fall back to a noise-stripped title if we couldn't extract enough
  if (parts.length < 2) {
    const clean = t.replace(/\b(excellent|very good|good|fair|like new|pre.?owned|nwt|nwot|used|condition)\b/gi, '').replace(/\s{2,}/g, ' ').trim();
    const full = [brand, clean].filter(Boolean).join(' ').trim();
    return { full, depop: full };
  }

  const full = parts.join(' ');
  return { full, depop: full };
}

async function getGoogleLensScreenshots(context, page) {
  const cards = photoCards(page);
  const count = await cards.count();
  if (count === 0) return [];

  const imgSrc = await getPhotoSrc(cards.first());
  if (!imgSrc) return [];

  const base64 = await page.evaluate(async (url) => {
    try {
      const res = await fetch(url);
      const buf = await res.arrayBuffer();
      const arr = new Uint8Array(buf);
      let b = "";
      arr.forEach((byte) => (b += String.fromCharCode(byte)));
      return btoa(b);
    } catch { return null; }
  }, imgSrc);

  if (!base64) return [];

  const tmpFile = path.join(os.tmpdir(), `4xi_lens_${Date.now()}.jpg`);
  fs.writeFileSync(tmpFile, Buffer.from(base64, "base64"));

  const screenshots = [];
  const lensPage = await context.newPage();

  try {
    await lensPage.goto("https://lens.google.com/", { waitUntil: "domcontentloaded", timeout: 15000 });
    await lensPage.waitForTimeout(1000);

    const fileInput = lensPage.locator('input[type="file"]').first();
    await fileInput.setInputFiles(tmpFile);

    await lensPage.waitForURL(/lens\.google\.com\/search/, { timeout: 20000 });
    await lensPage.waitForTimeout(3000);

    screenshots.push((await lensPage.screenshot({ type: "jpeg", quality: 85 })).toString("base64"));
    log("Google Lens results captured");

    try {
      const shoppingTab = lensPage.locator('[role="tab"]').filter({ hasText: /shopping/i }).first();
      if (await shoppingTab.isVisible({ timeout: 2000 })) {
        await shoppingTab.click();
        await lensPage.waitForTimeout(2000);
        screenshots.push((await lensPage.screenshot({ type: "jpeg", quality: 85 })).toString("base64"));
        log("Google Lens shopping tab captured");
      }
    } catch (_) {}

  } catch (e) {
    warn(`Google Lens search failed: ${e.message}`);
  } finally {
    await lensPage.close();
    try { fs.unlinkSync(tmpFile); } catch (_) {}
  }

  return screenshots;
}

async function researchPrice(context, page) {
  log("Researching price...");
  await dismissNotifications(page);
  const pageSS = await screenshot(page);

  const { title, brand } = await extractItemMeta(page);
  const queries = buildSearchQuery(brand, title);
  if (!queries) return null;

  log(`Text search: "${queries.full.slice(0, 80)}"`);
  const searchScreenshots = [];
  const enc      = encodeURIComponent(queries.full);
  const encDepop = encodeURIComponent(queries.depop);
  const sites = [
    { name: 'eBay',     url: `https://www.ebay.com/sch/i.html?_nkw=${enc}&LH_Sold=1&LH_Complete=1&_sop=13` },
    { name: 'Poshmark', url: `https://poshmark.com/search?query=${enc}&availability=sold_out&sort_by=relevance` },
    { name: 'Depop',    url: `https://www.depop.com/search/?q=${encDepop}&sold=true` },
  ];
  for (const { name, url } of sites) {
    try {
      const sitePage = await context.newPage();
      await sitePage.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 });
      await sitePage.waitForTimeout(2000);
      searchScreenshots.push(await screenshot(sitePage));
      await sitePage.close();
      log(`${name} captured`);
    } catch (e) {
      warn(`${name} search: ${e.message}`);
    }
  }

  const price = await vision.suggestPrice(pageSS, searchScreenshots);
  if (price) log(`Suggested price: $${price} (text search)`);
  else       warn("Could not determine price from text search either");
  return price;
}

async function readAndFillSKU(page) {
  const cards = photoCards(page);
  const count = await cards.count();
  if (count === 0) return false;
  const lastCard = cards.nth(count - 1);
  try {
    const imgSrc = await getPhotoSrc(lastCard);
    if (!imgSrc) { warn("No src on last photo — skipping SKU read"); return false; }
    const base64 = await page.evaluate(async (url) => {
      const res  = await fetch(url);
      const buf  = await res.arrayBuffer();
      const arr  = new Uint8Array(buf);
      let binary = "";
      arr.forEach((b) => (binary += String.fromCharCode(b)));
      return btoa(binary);
    }, imgSrc).catch(() => null);
    if (!base64) { warn("Could not fetch last photo — skipping SKU read"); return false; }

    let sku = null, confident = false;
    for (let attempt = 1; attempt <= 3; attempt++) {
      ({ sku, confident } = await vision.readSKU(base64));
      if (sku && confident) break;
      if (sku && !confident) break;
      if (attempt < 3) { log(`SKU attempt ${attempt} unreadable, retrying...`); await page.waitForTimeout(500); }
    }

    if (!sku) { warn("SKU unreadable after 3 attempts — photo kept"); return false; }
    if (!confident) warn(`SKU low confidence ("${sku}") — filled but photo kept`);
    else            log(`SKU: ${sku}`);

    const skuInput = page.locator("h4")
      .filter({ hasText: /^SKU/ })
      .locator("xpath=ancestor::div[contains(@class,'MuiGrid-root')][1]")
      .locator("input.css-18xpxag")
      .first();
    await skuInput.click({ timeout: 4000 });
    await skuInput.fill(sku);
    log("SKU field filled");
    return confident;
  } catch (e) {
    warn(`SKU step error: ${e.message}`);
    return false;
  }
}

async function fillPrice(page, price) {
  await dismissNotifications(page);
  try {
    const priceInput = page.locator("input.css-lej1ph").first();
    await priceInput.click({ timeout: 5000 });
    await priceInput.fill(String(price));
    await page.keyboard.press("Tab");
    await page.waitForTimeout(500);
    log(`Price set to $${price}`);
  } catch (e) {
    warn(`Could not fill price: ${e.message}`);
  }
}

const HEAVY_GARMENT_REGEX  = /\b(hoodie|hoody|sweatshirt|crewneck|crew.?neck|zip.?up|fleece|jacket|coat|parka|windbreaker|anorak|bomber|vest|cardigan|sweater|pullover|quarter.?zip|half.?zip|outerwear)\b/i;
const DRESS_REGEX          = /\b(dress|gown|sundress|maxi|midi|mini dress|romper|jumpsuit)\b/i;
const EXTENDED_SIZE_REGEX  = /\b(XXL|2XL|3XL|4XL|5XL)\b/i;
const SKIRT_REGEX          = /\b(skirt|skorts?)\b/i;
const LOW_RELEVANCE_TAIL   = /\s*\b(excellent|very good|good|fair|like new|pre.?owned|nwt|nwot|used|condition|new with tags|new without tags|gently used)\b[\s,]*$/i;

function isHeavyGarment(title)  { return HEAVY_GARMENT_REGEX.test(title || ''); }
function isDress(title)          { return DRESS_REGEX.test(title || ''); }
function isExtendedSize(title)   { return EXTENDED_SIZE_REGEX.test(title || ''); }
function isSkirt(title)          { return SKIRT_REGEX.test(title || ''); }

async function setShippingWeight(page, weightLabel) {
  try {
    // Expand the INTERNAL section if it's collapsed (Shipping Preset lives there)
    try {
      const internalHeader = page.locator('div[role="button"], button, div')
        .filter({ hasText: /^internal$/i })
        .first();
      if (await internalHeader.isVisible({ timeout: 2000 })) {
        await internalHeader.click();
        await page.waitForTimeout(600);
        log('Expanded INTERNAL section');
      }
    } catch (_) {}

    // Scroll to bottom so the field is in view
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(400);

    // Find the combobox input whose nearby label text contains "shipping preset".
    // Walk up from the label and stop at the SMALLEST ancestor that contains exactly
    // one combobox — this avoids accidentally grabbing a sibling field's input.
    const combo = await page.evaluateHandle(() => {
      const allEls = [...document.querySelectorAll('label, p, span, h4, h5, h6, div')];
      for (const el of allEls) {
        if (!/shipping\s*preset/i.test(el.textContent.trim())) continue;
        if (el.textContent.trim().length > 100) continue;
        let node = el;
        for (let i = 0; i < 8; i++) {
          node = node.parentElement;
          if (!node) break;
          const inputs = node.querySelectorAll('input[role="combobox"]');
          if (inputs.length === 1) return inputs[0];
        }
      }
      return null;
    });

    const comboEl = combo.asElement();
    if (!comboEl) {
      warn(`Shipping Preset field not found on page`);
      const debugPath = `debug-shipping-${Date.now()}.jpg`;
      fs.writeFileSync(debugPath, await page.screenshot({ type: "jpeg", quality: 80 }));
      warn(`Debug screenshot: ${debugPath}`);
      return;
    }

    await comboEl.scrollIntoViewIfNeeded();
    await comboEl.click({ timeout: 3000 });
    await page.waitForTimeout(600);

    const option = page.locator('[role="option"], li[role="option"]')
      .filter({ hasText: weightLabel })
      .first();

    if (await option.isVisible({ timeout: 2000 })) {
      await option.click();
      log(`Shipping preset set to ${weightLabel}`);
      return;
    }

    // Log all visible options before closing, then screenshot
    const visibleOptions = await page.locator('[role="option"], li[role="option"]').allTextContents().catch(() => []);
    warn(`Available preset options: ${JSON.stringify(visibleOptions)}`);
    const debugPath = `debug-shipping-${Date.now()}.jpg`;
    fs.writeFileSync(debugPath, await page.screenshot({ type: "jpeg", quality: 80 }));
    warn(`Debug screenshot: ${debugPath}`);
    await page.keyboard.press('Escape');
    warn(`Could not find preset option "${weightLabel}" in Shipping Preset dropdown`);
  } catch (e) {
    warn(`setShippingWeight error: ${e.message}`);
  }
}

async function getTitleInput(page) {
  // Named/placeholder strategies
  const strategies = [
    () => page.locator('input[name*="title" i], textarea[name*="title" i]').first(),
    () => page.locator('input[placeholder*="title" i], textarea[placeholder*="title" i]').first(),
    () => page.locator('label').filter({ hasText: /^title$/i })
           .locator('xpath=ancestor::div[3]').locator('input, textarea').first(),
    () => page.locator('label').filter({ hasText: /^title$/i })
           .locator('xpath=ancestor::div[5]').locator('input, textarea').first(),
    () => page.locator('h4, h5, label, p').filter({ hasText: /^title$/i })
           .locator('xpath=following::input[1]').first(),
  ];
  for (const fn of strategies) {
    try {
      const el = fn();
      const val = await el.inputValue({ timeout: 1500 });
      if (val && val.length > 5) return { el, val };
    } catch (_) {}
  }

  // Brute-force: find input with maxlength="80" — nifty enforces this on the title field only
  try {
    const result = await page.evaluate(() => {
      const inputs = [...document.querySelectorAll('input[maxlength="80"], textarea[maxlength="80"]')];
      if (inputs.length > 0) return { val: inputs[0].value, index: 0 };
      return null;
    });
    if (result) {
      const el = page.locator('input[maxlength="80"], textarea[maxlength="80"]').first();
      return { el, val: result.val };
    }
  } catch (_) {}

  // Last resort: find the input/textarea closest to a "Title" label by DOM order
  try {
    const result = await page.evaluate(() => {
      const all = [...document.querySelectorAll('input[type="text"], input:not([type]), textarea')];
      // Skip description (usually a textarea), price (short number), SKU
      const candidates = all.filter(el => {
        const v = el.value || '';
        return v.length > 5 && v.length < 200 && !/^\d+(\.\d+)?$/.test(v.trim());
      });
      if (candidates.length === 0) return null;
      const idx = all.indexOf(candidates[0]);
      return { val: candidates[0].value, index: idx };
    });
    if (result) {
      const inputs = page.locator('input[type="text"], input:not([type]), textarea');
      return { el: inputs.nth(result.index), val: result.val };
    }
  } catch (_) {}

  return null;
}

async function trimTitleIfNeeded(page) {
  const found = await getTitleInput(page);
  if (!found) { warn('Could not read title — skipping trim'); return false; }
  const { el: titleInput, val: current } = found;
  log(`Title (${current.length} chars): "${current.slice(0, 60)}${current.length > 60 ? '...' : ''}"`);
  if (current.length <= 80) return false;
  let trimmed = current;
  trimmed = trimmed.replace(/\bWomens\b/, 'Women').replace(/\bMens\b/, 'Men');
  while (trimmed.length > 80 && LOW_RELEVANCE_TAIL.test(trimmed))
    trimmed = trimmed.replace(LOW_RELEVANCE_TAIL, '').trim();
  while (trimmed.length > 80) {
    const lastSpace = trimmed.lastIndexOf(' ');
    if (lastSpace === -1) break;
    trimmed = trimmed.slice(0, lastSpace).trim();
  }
  if (trimmed === current) return false;
  log(`Trimmed title: "${trimmed}" (${trimmed.length} chars)`);
  await titleInput.click({ timeout: 3000 });
  await titleInput.fill(trimmed);
  await page.keyboard.press('Tab');
  await page.waitForTimeout(400);
  return true;
}

async function fillPrivateNote(page, note) {
  try {
    const field = page.locator('label, p, span, h4, h5, h6')
      .filter({ hasText: /private.?note|internal.?note|notes/i })
      .locator('xpath=ancestor::div[4]')
      .locator('textarea, input[type="text"]')
      .first();

    if (await field.isVisible({ timeout: 2000 })) {
      await field.click();
      await field.fill(note);
      log(`Private note set: "${note}"`);
      return;
    }

    const textareas = page.locator('textarea');
    const count = await textareas.count();
    for (let i = 0; i < count; i++) {
      const ta = textareas.nth(i);
      const placeholder = (await ta.getAttribute('placeholder') || '').toLowerCase();
      if (/note|internal|private/i.test(placeholder)) {
        await ta.click();
        await ta.fill(note);
        log(`Private note set via placeholder: "${note}"`);
        return;
      }
    }

    warn(`Could not find private note field — "${note}" not saved`);
  } catch (e) {
    warn(`fillPrivateNote error: ${e.message}`);
  }
}

async function clickUpdateDraft(page) {
  await page.getByRole("button", { name: /update draft/i }).first().click({ timeout: 8000 });
  log('"Update draft" clicked');
  await page.waitForTimeout(2000);
  const charError = page.locator('text=/80 character|title.*too long|must be.*80|exceed.*80/i').first();
  if (await charError.isVisible({ timeout: 1500 }).catch(() => false)) {
    warn('Title too long — trimming and retrying...');
    await trimTitleIfNeeded(page);
    await page.getByRole("button", { name: /update draft/i }).first().click({ timeout: 8000 });
    await page.waitForTimeout(2000);
  }
  await dismissNotifications(page);
}

async function tryToggleReady(page) {
  const readyLabel = page.locator("label").filter({ hasText: /Ready to list/i }).first();
  await readyLabel.waitFor({ timeout: 5000 });
  const isDisabled = await readyLabel.getAttribute("class")
    .then((c) => c?.includes("Mui-disabled")).catch(() => true);
  if (!isDisabled) {
    await readyLabel.click();
    await page.waitForTimeout(500);
    return true;
  }
  return false;
}

async function markReadyAndSave(page, itemNum) {
  await dismissNotifications(page);

  // 1. Trim title first — a long title disables "Ready to list"
  await trimTitleIfNeeded(page);
  await dismissNotifications(page);

  // 2. Try "Ready to list" normally
  let readyToggled = false;
  try { readyToggled = await tryToggleReady(page); }
  catch (e) { warn(`"Ready to list": ${e.message}`); }
  if (readyToggled) log('"Ready to list" toggled');
  else warn('"Ready to list" disabled — saving first, then retrying');

  // 3. Always save
  try { await clickUpdateDraft(page); }
  catch (e) { warn(`"Update draft": ${e.message}`); }

  if (readyToggled) return;

  // 4. Retry after save — saving sometimes re-enables it
  await dismissNotifications(page);
  try { readyToggled = await tryToggleReady(page); }
  catch (_) {}
  if (readyToggled) {
    log('"Ready to list" toggled (retry after save)');
    try { await clickUpdateDraft(page); } catch (e) { warn(`"Update draft": ${e.message}`); }
    return;
  }

  // 5. Last resort: JS force-click to bypass MUI disabled state
  warn('"Ready to list" still disabled — attempting JS force-click');
  try {
    const forceToggled = await page.evaluate(() => {
      const label = [...document.querySelectorAll('label')]
        .find(l => /ready to list/i.test(l.textContent));
      if (label) { label.click(); return true; }
      return false;
    });
    if (forceToggled) {
      await page.waitForTimeout(500);
      log('"Ready to list" force-toggled via JS');
      try { await clickUpdateDraft(page); } catch (e) { warn(`"Update draft": ${e.message}`); }
      return;
    }
  } catch (e) { warn(`JS force-click failed: ${e.message}`); }

  // 6. Truly stuck — save debug screenshot (private note already set)
  warn('"Ready to list" could not be toggled — item left as draft with private note');
  const ss = await page.screenshot({ type: "jpeg", quality: 80 });
  fs.writeFileSync(`debug-item-${itemNum}.jpg`, ss);
}

async function waitForListingReady(page) {
  // Nifty generates title/description via AI after upload — wait for title to be populated
  log("Waiting for AI listing to generate...");
  const deadline = Date.now() + 120000; // 2 min max
  while (Date.now() < deadline) {
    const found = await getTitleInput(page);
    if (found && found.val.length > 5 && !/^New item \(/i.test(found.val)) {
      log("Listing ready.");
      return;
    }
    await page.waitForTimeout(3000);
  }
  warn("Timed out waiting for AI listing — proceeding anyway");
}

async function processDraft(context, page, itemNum, total) {
  sep(`Item ${itemNum} of ${total}`);
  log(`Editing: ${page.url()}`);
  await dismissNotifications(page);
  await waitForListingReady(page);
  await page.waitForTimeout(500);

  const photoCount = await photoCards(page).count();
  log(`${photoCount} photo(s)`);

  let skuPhotoSafeToDelete = false;
  if (photoCount > 0) skuPhotoSafeToDelete = await readAndFillSKU(page);

  const FALLBACK_PRICE = 12;
  let price = await researchPrice(context, page);
  if (!price) {
    warn(`Price lookup failed — defaulting to $${FALLBACK_PRICE}`);
    price = FALLBACK_PRICE;
  }
  await fillPrice(page, price);

  // Set shipping preset based on garment type
  const titleFound = await getTitleInput(page);
  if (titleFound && isDress(titleFound.val)) {
    log(`Dress detected: "${titleFound.val.slice(0, 60)}" — setting shipping to 12oz`);
    await setShippingWeight(page, "12oz");
  } else if (titleFound && isHeavyGarment(titleFound.val)) {
    log(`Heavy garment detected: "${titleFound.val.slice(0, 60)}" — setting shipping to 1lb`);
    await setShippingWeight(page, "1lb");
  } else if (titleFound && isSkirt(titleFound.val)) {
    log(`Skirt detected: "${titleFound.val.slice(0, 60)}" — setting shipping to 8oz`);
    await setShippingWeight(page, "8oz");
  } else if (titleFound && isExtendedSize(titleFound.val)) {
    log(`Extended size detected: "${titleFound.val.slice(0, 60)}" — setting shipping to 8oz`);
    await setShippingWeight(page, "8oz");
  } else {
    const label = titleFound ? `"${titleFound.val.slice(0, 60)}"` : '(no title)';
    log(`Standard item ${label} — setting shipping to 4oz`);
    await setShippingWeight(page, "4oz");
  }

  await dismissNotifications(page);
  if (skuPhotoSafeToDelete) {
    const currentCount = await photoCards(page).count();
    if (currentCount > 0) await deletePhotoAt(page, currentCount - 1);
  } else {
    log("SKU photo kept");
    await fillPrivateNote(page, "Needs SKU");
  }

  await markReadyAndSave(page, itemNum);
  log(`Item ${itemNum} done`);
}

async function openDraftAt(page, rowIndex = 0) {
  await dismissSyncModal(page);
  const rows = page.locator(ROW_SEL);
  const totalRows = await rows.count();
  if (rowIndex >= totalRows) return null;
  const row = rows.nth(rowIndex);
  const hasRow = await row.isVisible().catch(() => false);
  if (!hasRow) return null;
  const dotsBtn = row.locator('i.bx-dots-vertical-rounded').locator('xpath=..');
  await dotsBtn.click();
  const editDraftItem = page.getByRole('menuitem', { name: /edit draft/i });
  await editDraftItem.waitFor({ timeout: 3000 });
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'commit', timeout: 8000 }),
    editDraftItem.click(),
  ]);
  return page.url();
}

// ─── Poll until drafts appear ─────────────────────────────────────────────────

async function waitForDrafts(page) {
  const deadline = Date.now() + POLL_TIMEOUT;
  let attempt = 0;

  while (Date.now() < deadline) {
    attempt++;
    await page.goto(DRAFTS_URL, { waitUntil: "domcontentloaded", timeout: 20000 });
    await dismissNotifications(page);
    await page.waitForTimeout(1500);

    const hasRows = await page.locator(ROW_SEL).first().isVisible({ timeout: 3000 }).catch(() => false);
    if (hasRows) {
      const count = await page.locator(ROW_SEL).count();
      console.log(`\n[List] Found ${count} draft(s) ready to process.`);
      return true;
    }

    const remaining = Math.round((deadline - Date.now()) / 1000);
    console.log(`[List] No drafts yet (attempt ${attempt}) — waiting ${POLL_INTERVAL / 1000}s... (${remaining}s left)`);
    await page.waitForTimeout(POLL_INTERVAL);
  }

  console.error("[List] Timed out waiting for drafts to appear.");
  return false;
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const authExists = fs.existsSync(config.authState);
  const browser    = await chromium.launch({ headless: config.headless ?? false });
  const context    = authExists
    ? await browser.newContext({ storageState: config.authState })
    : await browser.newContext();
  const page = await context.newPage();

  page.on("dialog", async (dialog) => {
    log(`[dialog accepted] ${dialog.message()}`);
    await dialog.accept().catch(() => {});
  });

  // Login check
  await page.goto("https://app.nifty.ai/inventory", { waitUntil: "domcontentloaded", timeout: 30000 });
  const loggedIn = await page.evaluate(
    () => !document.querySelector('input[type="password"]') &&
          !location.href.includes("/login") &&
          !location.href.includes("/signin")
  );
  if (!loggedIn) {
    console.error("\nERROR: Not logged in to nifty.ai.");
    console.error("Run list.js manually once to log in, then re-run the pipeline.");
    await browser.close();
    process.exit(1);
  }

  // Resolve count: CLI arg > session file > error
  const cliCount = (() => {
    const i = process.argv.indexOf("--count");
    return i !== -1 ? parseInt(process.argv[i + 1], 10) : null;
  })();

  let expectedCount;
  if (cliCount && cliCount > 0) {
    expectedCount = cliCount;
    console.log(`\n[List] Count from argument: ${expectedCount} item(s)`);
  } else if (fs.existsSync(SESSION_FILE)) {
    const session = JSON.parse(fs.readFileSync(SESSION_FILE, "utf8"));
    expectedCount = session.count || 0;
    console.log(`\n[List] Session file: expecting ${expectedCount} item(s) to list`);
  } else {
    console.error("\nERROR: No --count argument and no session file found.");
    console.error("Pass a count: node list.auto.js --count 10");
    await browser.close();
    process.exit(1);
  }

  // Wait for drafts to be ready (nifty.ai generates them after upload)
  const draftsReady = await waitForDrafts(page);
  if (!draftsReady) {
    await browser.close();
    process.exit(1);
  }

  // Fresh reload before processing — ensures page isn't stale after the wait
  console.log("[List] Refreshing drafts page before starting...");
  await page.goto(DRAFTS_URL, { waitUntil: "domcontentloaded", timeout: 20000 });
  await dismissSyncModal(page);
  await page.waitForTimeout(2000);

  // Process exactly the number of items from this session
  let totalProcessed = 0;
  const visitedUrls = new Set();
  let stuckSkipCount = 0; // how many leading rows to skip (stuck items that can't be listed)

  while (totalProcessed < expectedCount) {
    await page.goto(DRAFTS_URL, { waitUntil: "domcontentloaded", timeout: 20000 });
    await dismissNotifications(page);
    await page.waitForTimeout(1000);

    const totalRows = await page.locator(ROW_SEL).count();
    if (totalRows === 0) {
      console.log("\n[List] No more drafts on page — stopping.");
      break;
    }
    if (stuckSkipCount >= totalRows) {
      console.log(`\n[List] All ${totalRows} remaining draft(s) are stuck — stopping.`);
      break;
    }

    console.log(`\n[List] ${totalProcessed}/${expectedCount} done — processing next draft...`);

    const editUrl = await openDraftAt(page, stuckSkipCount);
    if (!editUrl) break;

    // If this URL already appeared, it's stuck — skip past it without counting it
    if (visitedUrls.has(editUrl)) {
      warn(`Row ${stuckSkipCount} is stuck (Ready to list failed) — skipping to next row`);
      stuckSkipCount++;
      continue;
    }
    visitedUrls.add(editUrl);

    totalProcessed++;
    try {
      await processDraft(context, page, totalProcessed, expectedCount);
    } catch (e) {
      warn(`Item ${totalProcessed} error: ${e.message} — skipping to next`);
    }
    try {
      // Clear nifty's warning redirect by hitting base inventory first, then the correct filter.
      await page.goto("https://app.nifty.ai/inventory", { waitUntil: "domcontentloaded", timeout: 20000 });
      await page.waitForTimeout(1500);
      await page.goto(DRAFTS_URL, { waitUntil: "domcontentloaded", timeout: 20000 });
      await page.waitForTimeout(1500);
      await page.locator(ROW_SEL).first().waitFor({ timeout: 10000 }).catch(() => {});
      await dismissNotifications(page);
    } catch (e) {
      warn(`Could not return to drafts: ${e.message}`);
      break;
    }
  }

  console.log(`\n[List] Done. ${totalProcessed} item(s) listed.`);
  console.log(`[List] Browser staying open for review. Press Ctrl+C to close.`);
  await new Promise(() => {});  // keep process alive so browser stays open
}

main().catch((err) => {
  console.error("\nFatal error in list.auto.js:", err.message);
  console.error(err.stack);
  process.exit(1);
});
