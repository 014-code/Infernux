import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("./r2-upload-worker.mjs", import.meta.url), "utf8");
const context = vm.createContext({ TextEncoder });
vm.runInContext(source.replace("export default", "globalThis.worker =") + "\nglobalThis.validateKey = requireKey;", context);
for (const key of [
  "hub/0.4.1/build-1/InfernuxHubInstaller-0.4.1-linux-x64",
  "hub/0.4.1/build-2/InfernuxHubInstaller-0.4.1-2-linux-x64",
  "hub/0.4.1/build-2/InfernuxHub-0.4.1-2-windows-x64-full.zip",
  "hub/0.4.1/build-2/InfernuxHub-linux-x64-manifest.json",
  "hub/0.4.0/build-2/InfernuxHubInstaller-0.4.0-linux-x64",
]) assert.equal(context.validateKey(key), key);
for (const key of [
  "hub/0.4.1/build-2/InfernuxHubInstaller-0.4.1-linux-x64",
  "hub/0.4.1/build-2/InfernuxHubInstaller-0.4.1-3-linux-x64",
  "hub/0.4.1/build-0/InfernuxHubInstaller-0.4.1-0-linux-x64",
  "hub/0.4.1/build-2/../../unrelated",
]) assert.throws(() => context.validateKey(key), /not authorized/);
console.log("Hub release upload keys verified.");
