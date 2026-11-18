#!/usr/bin/env node

import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const moduleRoot = process.env.ARTIFACT_TOOL_MODULE_DIR;
if (!moduleRoot) throw new Error("ARTIFACT_TOOL_MODULE_DIR is required");
const { FileBlob, SpreadsheetFile } = await import(
  pathToFileURL(path.join(moduleRoot, "@oai", "artifact-tool", "dist", "artifact_tool.mjs"))
);

const inputPath = path.resolve(process.argv[2]);
const previewDir = path.resolve(process.argv[3]);
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const sheets = await workbook.inspect({ kind: "sheet", include: "id,name" });
const hitters = await workbook.inspect({
  kind: "table",
  sheetId: "Hitters",
  range: "A1:AP13",
  include: "values,formulas",
  tableMaxRows: 13,
  tableMaxCols: 42,
});
const pitchers = await workbook.inspect({
  kind: "table",
  sheetId: "Pitchers",
  range: "A1:AO13",
  include: "values,formulas",
  tableMaxRows: 13,
  tableMaxCols: 41,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "Phase 4 workbook formula error scan",
});

await fs.mkdir(previewDir, { recursive: true });
for (const [sheetName, range, filename] of [
  ["Hitters", "A1:AP13", "hitters.png"],
  ["Pitchers", "A1:AO13", "pitchers.png"],
]) {
  const rendered = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(
    path.join(previewDir, filename),
    new Uint8Array(await rendered.arrayBuffer()),
  );
}

workbook.recalculate();
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(inputPath);

console.log(sheets.ndjson);
console.log(hitters.ndjson);
console.log(pitchers.ndjson);
console.log(errors.ndjson);
