const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const Anthropic = require("@anthropic-ai/sdk");

const strictMode = String(process.env.DOCS_STRICT_MODE || "false").toLowerCase() === "true";
const minConfidence = Number.parseFloat(process.env.DOCS_MIN_CONFIDENCE || "0.70");

function exitWithPolicy(message) {
  console.log(message);
  process.exit(strictMode ? 1 : 0);
}

if (!process.env.ANTHROPIC_API_KEY) {
  exitWithPolicy("ANTHROPIC_API_KEY is not set.");
}

const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

function run(cmd) {
  try {
    return execSync(cmd).toString();
  } catch {
    return "";
  }
}

function getDiff() {
  const commitWindow = parseInt(process.env.CLAUDE_DIFF_COMMITS || "5", 10);
  const safeWindow = Number.isFinite(commitWindow) ? Math.min(Math.max(commitWindow, 1), 30) : 5;
  const primary = run(`git diff --unified=2 HEAD~${safeWindow}..HEAD`);
  if (primary.trim()) {
    return primary;
  }
  return run("git show --pretty=format: --unified=2 HEAD");
}

function collectMarkdownFiles(rootDir) {
  const files = [];

  if (!fs.existsSync(rootDir)) {
    return files;
  }

  for (const entry of fs.readdirSync(rootDir, { withFileTypes: true })) {
    const fullPath = path.join(rootDir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectMarkdownFiles(fullPath));
    } else if (entry.isFile() && entry.name.endsWith(".md")) {
      files.push(fullPath);
    }
  }

  return files;
}

function extractJsonObject(text) {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) {
    throw new Error("No JSON object found in model output");
  }
  return text.slice(start, end + 1);
}

function isAllowedDocPath(filePath) {
  const normalized = path.posix.normalize(filePath.replace(/\\/g, "/"));
  if (path.isAbsolute(normalized) || normalized.startsWith("../")) {
    return false;
  }
  if (normalized === "README.md") {
    return true;
  }
  return normalized.startsWith("docs/") && normalized.endsWith(".md");
}

// -------- STEP 1: Diff --------
const diff = getDiff();

// -------- STEP 2: Read docs --------
function readDocs() {
  let output = "";

  if (fs.existsSync("README.md")) {
    output += `FILE: README.md\n${fs.readFileSync("README.md", "utf-8")}\n\n`;
  }

  for (const filePath of collectMarkdownFiles("docs")) {
    output += `FILE: ${filePath.replace(/\\/g, "/")}\n${fs.readFileSync(filePath, "utf-8")}\n\n`;
  }

  return output;
}

const docsContent = readDocs();

// -------- STEP 3: Prompt --------
const prompt = `
Act as a senior architect and technical writer.

Perform deep analysis of repository changes.

TASKS:
- Understand architecture impact
- Identify outdated or missing docs
- Improve ALL documentation

RULES:
- Be accurate and structured
- Do not hallucinate
- Update only relevant files

OUTPUT JSON:
{
  "CONFIDENCE": 0.0,
  "UPDATED_FILES": {
    "README.md": "...",
    "docs/file.md": "..."
  }
}

DIFF:
${diff}

DOCS:
${docsContent}
`;

async function main() {
  // -------- STEP 4: Call Claude --------
  const response = await client.messages.create({
    model: process.env.CLAUDE_MODEL || "claude-3-haiku-20240307",
    max_tokens: 4000,
    messages: [{ role: "user", content: prompt }]
  });

  // -------- STEP 5: Parse --------
  const textBlocks = (response.content || [])
    .filter((block) => block && block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n");

  let parsed;
  try {
    parsed = JSON.parse(extractJsonObject(textBlocks));
  } catch {
    exitWithPolicy("Parsing failed.");
  }

  if (!parsed || typeof parsed !== "object" || !parsed.UPDATED_FILES || typeof parsed.UPDATED_FILES !== "object") {
    exitWithPolicy("Model response shape invalid.");
  }

  const confidence = Number.parseFloat(parsed.CONFIDENCE);
  if (strictMode && !Number.isFinite(confidence)) {
    exitWithPolicy("Missing or invalid CONFIDENCE in model output.");
  }
  if (Number.isFinite(confidence) && confidence < minConfidence) {
    exitWithPolicy(`Model confidence ${confidence} below threshold ${minConfidence}.`);
  }

  if (strictMode && diff.trim() && Object.keys(parsed.UPDATED_FILES).length === 0) {
    exitWithPolicy("Empty UPDATED_FILES in strict mode with non-empty diff.");
  }

  // -------- STEP 6: Write --------
  let appliedWrites = 0;
  for (const file in parsed.UPDATED_FILES) {
    if (!isAllowedDocPath(file)) {
      console.log(`Skipping unsafe target path: ${file}`);
      continue;
    }

    const content = parsed.UPDATED_FILES[file];
    if (typeof content !== "string") {
      continue;
    }

    const dir = path.dirname(file);
    if (dir && dir !== "." && !fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    fs.writeFileSync(file, content);
    appliedWrites += 1;
  }

  if (strictMode && diff.trim() && appliedWrites === 0) {
    exitWithPolicy("No writable documentation changes applied in strict mode.");
  }

  console.log("Docs updated via Claude");
}

main().catch(() => {
  exitWithPolicy("Claude invocation failed.");
});