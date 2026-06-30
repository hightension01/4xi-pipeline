module.exports = {
  // ── Upload (upload.js) ──────────────────────────────────────────────────────

  // Path to the folder containing one subfolder per clothing item
  itemsDir: "G:\\My Drive\\processed_output",

  // How many items to bulk-upload per run (keep at 5-10 to avoid bugs on nifty.ai)
  batchSize: 5,

  // Gender to select per item during bulk upload. Set to "Men", "Women", or null (unspecified)
  gender: null,

  // File that tracks which folders have already been uploaded (prevents duplicates)
  processedLog: "./processed.txt",

  // ── Listing (list.js) ───────────────────────────────────────────────────────

  // How many drafted items to process per listing session
  listBatchSize: 5,

  // ── Vision AI ──────────────────────────────────────────────────────────────

  // Your Anthropic API key — get one at https://console.anthropic.com
  // Tip: set this as an env variable instead: ANTHROPIC_API_KEY=sk-ant-...
  anthropicApiKey: process.env.ANTHROPIC_API_KEY || "",

  // Claude model to use for vision tasks
  visionModel: "claude-opus-4-5",

  // ── Browser ────────────────────────────────────────────────────────────────

  // Show the browser window while running (recommended so you can monitor)
  headless: false,

  // File that stores your login session so you don't have to log in every time
  authState: "./auth.json",
};
