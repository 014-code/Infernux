import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";
import test from "node:test";

async function checkFixture(change) {
    const installer = {
        name: "InfernuxHubInstaller-0.4.2-4-windows-x64.exe",
        url: "https://downloads.infernux-engine.com/hub/0.4.2/build-4/InfernuxHubInstaller-0.4.2-4-windows-x64.exe",
        fallback_url: "https://github.com/ChenlizheMe/Infernux/releases/download/hub-v0.4.2-v4/InfernuxHubInstaller-0.4.2-4-windows-x64.exe",
    };
    const release = { version: "0.4.2", tag: "v0.4.2-v2",
        release_url: "https://github.com/ChenlizheMe/Infernux/releases/tag/v0.4.2-v2",
        assets: [{ kind: "hub-installer", ...installer }] };
    const catalog = { stable: "0.4.2-4", releases: [{ version: "0.4.2-4",
        release_url: "https://github.com/ChenlizheMe/Infernux/releases/tag/hub-v0.4.2-v4",
        platforms: { "windows-x64": { installer } } }] };
    change?.(release, catalog);
    const documents = {
        "/release.json": release,
        "/release-notes.json": { version: release.version, tag: release.tag },
        "/api-index.json": { symbols: [], generated_for_release: release.version },
        "/docs-manifest.json": { documented_release: release.version },
        "/platform-support.json": { released_version: release.version },
        "/hub-catalog.json": catalog,
    };
    const server = createServer((request, response) => {
        response.end(documents[request.url] ? JSON.stringify(documents[request.url]) : "<main>Available</main>");
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    try {
        const child = spawn(process.execPath, [fileURLToPath(new URL("./check-deployed-site.mjs", import.meta.url)),
            "--base-url", `http://127.0.0.1:${server.address().port}`],
            { env: { ...process.env, GITHUB_STEP_SUMMARY: "" }, stdio: ["ignore", "pipe", "pipe"] });
        let output = "";
        child.stdout.on("data", chunk => { output += chunk; });
        child.stderr.on("data", chunk => { output += chunk; });
        const code = await new Promise((resolve, reject) => {
            child.on("error", reject);
            child.on("close", resolve);
        });
        return { code, output };
    } finally {
        server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
    }
}

test("deployed health accepts an independent Hub hotfix", async () => {
    const result = await checkFixture();
    assert.equal(result.code, 0, result.output);
});

test("deployed health reports inconsistent Hub download information", async () => {
    const result = await checkFixture(release => { release.assets[0].url += "?old-download"; });
    assert.equal(result.code, 1);
    assert.match(result.output, /FAIL release-consistency:.*catalog installer and release asset differ/);
});

test("deployed health rejects a Hub release link to another revision", async () => {
    const result = await checkFixture((_release, catalog) => {
        catalog.releases[0].release_url = catalog.releases[0].release_url.replace("-v4", "-v3");
    });
    assert.equal(result.code, 1);
    assert.match(result.output, /Hub release destination differs from its own revision/);
});
