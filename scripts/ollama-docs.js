const fs = require("fs");
const path = require("path");
const { execSync, execFileSync } = require("child_process");

const strictMode = String(process.env.DOCS_STRICT_MODE || "false").toLowerCase() === "true";
const minConfidence = Number.parseFloat(process.env.DOCS_MIN_CONFIDENCE || "0.70");

function run(cmd) {
  try {
    return execSync(cmd, { stdio: "pipe" }).toString();
  } catch {
    return "";
  }
}

function getDiff() {
  const commitWindow = parseInt(process.env.OLLAMA_DIFF_COMMITS || "3", 10);
  const safeWindow = Number.isFinite(commitWindow) ? Math.min(Math.max(commitWindow, 1), 20) : 3;
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

function exitWithPolicy(message) {
  console.log(message);
  process.exit(strictMode ? 1 : 0);
}

// -------- STEP 1: Get diff --------
const diff = getDiff();

// -------- STEP 2: Read ALL docs --------
function readDocs() {
  let output = "";

  if (fs.existsSync("README.md")) {
    output += `FILE: README.md\n${fs.readFileSync("README.md", "utf-8")}\n\n`;
  }

  for (const filePath of collectMarkdownFiles("docs")) {
    const content = fs.readFileSync(filePath, "utf-8");
    const relPath = filePath.replace(/\\/g, "/");
    output += `FILE: ${relPath}\n${content}\n\n`;
  }

  return output;
}

const docsContent = readDocs();

// -------- STEP 3: Prompt --------
const prompt = `
You are a senior software architect and technical writer.

Analyze the git diff and update all documentation.

TASKS:
1. Understand code changes from diff
2. Identify impacted areas
3. Update documentation accordingly

RULES:
- Only update necessary files
- Preserve useful content
- Be precise
- Do NOT hallucinate

OUTPUT STRICT JSON:
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

// -------- STEP 4: Call Ollama --------
const model = process.env.OLLAMA_MODEL || "llama3";
let result = "";

try {
  result = execFileSync("ollama", ["run", model], {
    input: prompt,
    encoding: "utf-8",
    stdio: ["pipe", "pipe", "pipe"]
  });
} catch {
  exitWithPolicy("Ollama invocation failed.");
}

// -------- STEP 5: Parse safely --------
let parsed;

try {
  parsed = JSON.parse(extractJsonObject(result));
} catch {
  exitWithPolicy("Failed to parse output.");
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

// -------- STEP 6: Write files --------
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

console.log("Docs updated via Ollama");