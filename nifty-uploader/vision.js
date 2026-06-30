/**
 * vision.js — Claude API vision wrapper for all agentic decisions
 *
 * Responsibilities:
 *  - Classify each photo (product / tag / SKU bag)
 *  - Assess photo orientation (needs rotation?)
 *  - Read clothing tag (size, brand)
 *  - Read SKU label
 *  - Suggest resale price from product image (+ optional Google Lens results)
 */

const Anthropic = require("@anthropic-ai/sdk");
const config = require("./config");

const client = new Anthropic({ apiKey: config.anthropicApiKey });

// ─── Utilities ────────────────────────────────────────────────────────────────

function imageContent(base64, mimeType = "image/jpeg") {
  return { type: "image", source: { type: "base64", media_type: mimeType, data: base64 } };
}

function textContent(text) {
  return { type: "text", text };
}

async function ask(content, maxTokens = 512) {
  const response = await client.messages.create({
    model: config.visionModel || "claude-opus-4-5",
    max_tokens: maxTokens,
    messages: [{ role: "user", content }],
  });
  return response.content[0].text.trim();
}

// ─── Photo classification ─────────────────────────────────────────────────────

/**
 * Classify what kind of photo this is.
 * Returns: "PRODUCT" | "TAG" | "SKU_BAG" | "OTHER"
 */
async function classifyPhoto(screenshotBase64) {
  const result = await ask(
    [
      imageContent(screenshotBase64),
      textContent(`You are classifying a photo taken for a clothing resale listing.

Classify this image as exactly one of:
- PRODUCT: The clothing item itself (laid flat, worn by model, or on hanger)
- TAG: A close-up of a clothing tag showing brand/size info
- SKU_BAG: The item in a clear plastic bag with a barcode/label sticker on it
- OTHER: Something else (background, packaging, etc.)

Respond with ONLY the single word classification.`),
    ],
    50
  );

  const clean = result.toUpperCase().trim();
  if (["PRODUCT", "TAG", "SKU_BAG", "OTHER"].includes(clean)) return clean;
  if (clean.includes("PRODUCT")) return "PRODUCT";
  if (clean.includes("TAG")) return "TAG";
  if (clean.includes("SKU") || clean.includes("BAG")) return "SKU_BAG";
  return "OTHER";
}

// ─── Photo orientation ────────────────────────────────────────────────────────

/**
 * Check if a clothing product photo needs rotation.
 * Returns: "LOOKS_GOOD" | "ROTATE_RIGHT" | "ROTATE_LEFT" | "SKIP"
 */
async function getPhotoAdjustment(screenshotBase64) {
  const result = await ask(
    [
      imageContent(screenshotBase64),
      textContent(`You are reviewing a clothing product photo for a resale listing.

Assess the orientation of the clothing item:
1. Is it upright and readable (not sideways or upside-down)?
2. Is the main subject clearly the clothing item?

Respond with EXACTLY one of:
- LOOKS_GOOD: orientation is fine, no rotation needed
- ROTATE_RIGHT: needs 90° clockwise rotation
- ROTATE_LEFT: needs 90° counter-clockwise rotation
- SKIP: cannot determine or the image is already open in an editor — skip

Only the single token. No explanation.`),
    ],
    30
  );

  const clean = result.toUpperCase().trim();
  if (clean.includes("LOOKS_GOOD") || clean.includes("GOOD")) return "LOOKS_GOOD";
  if (clean.includes("ROTATE_RIGHT") || clean.includes("RIGHT")) return "ROTATE_RIGHT";
  if (clean.includes("ROTATE_LEFT") || clean.includes("LEFT")) return "ROTATE_LEFT";
  return "SKIP";
}

// ─── Tag reading ──────────────────────────────────────────────────────────────

/**
 * Read size and brand info from a clothing tag photo.
 * Returns: { size: string|null, brand: string|null }
 */
async function readTag(screenshotBase64) {
  const result = await ask(
    [
      imageContent(screenshotBase64),
      textContent(`This is a clothing tag photo. Extract the following information:

Respond in this EXACT format (fill in UNKNOWN if you cannot read it):
SIZE: [size value, e.g. M, L, XL, 32, 8]
BRAND: [brand name]`),
    ],
    150
  );

  const sizeMatch = result.match(/SIZE:\s*(.+)/i);
  const brandMatch = result.match(/BRAND:\s*(.+)/i);

  const size = sizeMatch ? sizeMatch[1].trim().replace(/unknown/i, "") || null : null;
  const brand = brandMatch ? brandMatch[1].trim().replace(/unknown/i, "") || null : null;

  return { size, brand };
}

// ─── SKU reading ──────────────────────────────────────────────────────────────

/**
 * Read the SKU/item identifier from a bagged SKU photo.
 * Returns: string | null
 */
async function readSKU(screenshotBase64) {
  const result = await ask(
    [
      imageContent(screenshotBase64),
      textContent(`This is a photo of a clothing item in a bag with a SKU label or barcode sticker.

Read the SKU number or item identifier from the label.

Respond in this EXACT format:
SKU: [the SKU value, e.g. 0361, APC_1089, SKU-AB001]
CONFIDENCE: [HIGH or LOW]

Use HIGH only if you can read every character clearly and are certain.
Use LOW if there is any blur, ambiguity, or uncertainty.
If completely unreadable, respond with:
SKU: UNREADABLE
CONFIDENCE: LOW`),
    ],
    120
  );

  const skuMatch  = result.match(/SKU:\s*(.+)/i);
  const confMatch = result.match(/CONFIDENCE:\s*(HIGH|LOW)/i);

  const sku  = skuMatch  ? skuMatch[1].trim()          : null;
  const conf = confMatch ? confMatch[1].toUpperCase()  : 'LOW';

  if (!sku || sku.toUpperCase() === 'UNREADABLE') return { sku: null, confident: false };
  return { sku, confident: conf === 'HIGH' };
}

// ─── Price suggestion ─────────────────────────────────────────────────────────

/**
 * Suggest a resale price based on product image + optional Google Lens screenshot.
 * Returns: number | null
 */
async function suggestPrice(productImageBase64, searchScreenshots = []) {
  const content = [imageContent(productImageBase64)];

  if (searchScreenshots.length > 0) {
    for (const ss of searchScreenshots) content.push(imageContent(ss));
    content.push(
      textContent(`The first image is the clothing item to price. The next ${searchScreenshots.length} image(s) show sold listings from eBay, Poshmark, and/or Depop for comparable items.

You are an aggressive resale pricing expert. Your goal is to price to sell fast — within days.

Steps:
1. Scan the search result images for actual sold/completed prices
2. Find the cluster where most similar items actually sold (ignore outliers)
3. Suggest a price $1 below the lowest common sold price in that cluster
4. If sold prices vary widely, anchor to the lower end minus $1
5. If NO relevant results appear in any of the images (wrong items, empty results, no sold prices visible), fall back to:
   - $12 if the shirt has a graphic/print on one side only
   - $14 if the shirt has graphics/print on both front and back
   - $15 for pants/bottoms/skirts (no results fallback)
   - $20 for jackets/outerwear (no results fallback)

Rules:
- Minimum $10 for any item
- Round to nearest whole dollar
- Do not include the $ sign
- Prioritize selling speed over margin

Respond with ONLY the number.`)
    );
  } else {
    content.push(
      textContent(`You are an aggressive resale pricing expert for secondhand clothing.

Look at this clothing item and suggest a price in USD. No market data is available, so use these defaults:
- Graphic tee / shirt with print on ONE side: $12
- Graphic tee / shirt with print on BOTH sides: $14
- Plain top or unbranded top: $10
- Pants/bottoms/skirts: $15
- Jackets/outerwear: $20
- Well-known brand: add $2–5

Round to nearest whole dollar. Do not include the $ sign.

Respond with ONLY the number.`)
    );
  }

  const result = await ask(content, 50);
  const price = parseFloat(result.replace(/[^0-9.]/g, ""));
  return isNaN(price) ? null : Math.round(price);
}

module.exports = { classifyPhoto, getPhotoAdjustment, readTag, readSKU, suggestPrice };
