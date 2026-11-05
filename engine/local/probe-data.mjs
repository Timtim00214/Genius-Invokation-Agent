import { pathToFileURL } from "node:url";
import path from "node:path";

const repo = path.resolve(import.meta.dirname, "..");
const dataUrl = pathToFileURL(path.join(repo, "packages/data/dist/index.js")).href;
const coreUrl = pathToFileURL(path.join(repo, "packages/core/dist/index.js")).href;

const { default: getData } = await import(dataUrl);
const core = await import(coreUrl);

const data = getData("v7.1.0");
console.log("data keys:", Object.keys(data));
const ent = data.entities.get(311501);
console.log("entity 311501 keys:", Object.keys(ent));
console.log("name:", ent.name, "type:", ent.type, "tags:", ent.tags);
console.log("skills:", ent.skills.length);
for (const s of ent.skills) {
  console.log("--- skill", s.id, s.skillType, "keys:", Object.keys(s));
  console.log("action source:\n", String(s.action).slice(0, 1200));
}
const ch = data.characters.get(1101);
console.log("character 1101:", ch?.name, "varConfigs:", Object.fromEntries(Object.entries(ch?.varConfigs ?? {}).map(([k, v]) => [k, v.initialValue])));
