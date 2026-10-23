import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { checkSiteInformation } from "./check-site-information.mjs";

async function fixture(t) {
    const root = await mkdtemp(path.join(os.tmpdir(), "infernux-site-info-"));
    t.after(() => rm(root, { recursive: true, force: true }));
    const put = async (name, value) => {
        const target = path.join(root, name);
        await mkdir(path.dirname(target), { recursive: true });
        await writeFile(target, typeof value === "string" ? value : JSON.stringify(value, null, 2) + "\r\n");
    };
    const version = "0.4.1", tag = "v0.4.1-v3";
    const releaseUrl = `https://github.com/ChenlizheMe/Infernux/releases/tag/${tag}`;
    const asset = name => ({ name, size_bytes: 50, url: `https://downloads.infernux-engine.com/${name}`,
        fallback_url: `https://github.com/ChenlizheMe/Infernux/releases/download/${tag}/${name}` });
    const installer = { kind: "hub-installer", ...asset("InfernuxHubInstaller-0.4.1-3-windows-x64.exe") };
    const release = { version, tag, release_url: releaseUrl, assets: [installer,
        { kind: "python-wheel", ...asset("infernux-0.4.1-3-cp313-cp313-win_amd64.whl") }] };
    const symbols = [];
    for (const [folder, language, other] of [["en", "en", "zh"], ["zh", "zh-CN", "en"]]) {
        const source = `docs/wiki/docs/${folder}/api/Component.md`;
        const url = `/wiki/site/${folder}/api/Component.html`;
        symbols.push({ id: `${folder}:Component`, symbol_key: "Infernux.Component", language, source,
            url, counterpart_url: `/wiki/site/${other}/api/Component.html` });
        await put(source, "# Component\r\n");
        await put(`docs${url}`, "<html><body>New site layout</body></html>\r\n");
    }
    const catalog = { stable: "0.4.1-3", releases: [{ version: "0.4.1-3", release_url: releaseUrl,
        platforms: { "windows-x64": { installer } } }] };
    const index = { generated_for_release: version, symbol_count: 2, symbols };
    const documents = {
        "release.json": release, "release-notes.json": { version, tag, release_url: releaseUrl },
        "docs-manifest.json": { documented_release: version }, "api-index.json": index,
        "api-changes.json": { current_release: tag.slice(1) }, "hub-catalog.json": catalog,
        "platform-support.json": { development_version: version, released_version: version, python_abi: "cp313",
            platforms: [{ id: "windows-x64", released: true, editor: true }] },
        [`api-snapshots/${tag.slice(1)}.json`]: { release: tag.slice(1), symbols: [{ symbol_key: "Infernux.Component",
            canonical_url: "https://infernux-engine.com/wiki/site/en/api/Component.html" }] },
    };
    for (const [name, value] of Object.entries(documents)) await put(`docs/${name}`, value);
    await put("pyproject.toml", '[project]\r\nversion = "0.4.1"\r\n');
    return { root, put, release, catalog, index };
}

test("new layouts and CRLF do not change information validation", async t => {
    const { root, put } = await fixture(t);
    await put("docs/index.html", '<main class="a-completely-new-design">Current website</main>');
    assert.equal((await checkSiteInformation(root)).apiSymbols, 2);
});

test("hotfix API snapshots preserve published bytes and sort revisions numerically", async t => {
    const { root, put, release, index } = await fixture(t);
    await rm(path.join(root, "docs/api-snapshots/0.4.1-v3.json"));
    const symbol = index.symbols[0];
    Object.assign(symbol, { module: "infernux", symbol: "Component", kind: "class",
        signatures: ["Component()"], status: "stable", since: "0.4.1",
        canonical_url: "https://infernux-engine.com/wiki/site/en/api/Component.html" });
    const build = () => execFileSync(process.execPath,
        [fileURLToPath(new URL("./build-api-diff.mjs", import.meta.url)), "--record-current"],
        { cwd: root, stdio: "pipe" });
    const record = async tag => {
        release.tag = tag;
        await put("docs/release.json", release);
        await put("docs/api-index.json", index);
        build();
    };
    const snapshot = name => readFile(path.join(root, `docs/api-snapshots/${name}.json`), "utf8");
    await record("v0.4.1");
    const base = await snapshot("0.4.1");
    symbol.signatures = ["Component(enabled=True)"];
    await record("v0.4.1-v2");
    const hotfix = await snapshot("0.4.1-v2");
    symbol.signatures = ["Component(enabled=True, owner=None)"];
    await record("v0.4.1-v10");
    const changes = JSON.parse(await readFile(path.join(root, "docs/api-changes.json"), "utf8"));
    assert.equal(changes.current_release, "0.4.1-v10");
    assert.equal(changes.previous_release, "0.4.1-v2");
    assert.equal(changes.counts.changed, 1);
    const latest = await snapshot("0.4.1-v10");
    symbol.signatures = ["Component(unpublished=True)"];
    await put("docs/api-index.json", index);
    assert.throws(build, /Refusing to overwrite immutable API snapshot/);
    assert.equal(await snapshot("0.4.1"), base);
    assert.equal(await snapshot("0.4.1-v2"), hotfix);
    assert.equal(await snapshot("0.4.1-v10"), latest);
});
test("reject mismatched published versions", async t => {
    const { root, put } = await fixture(t);
    await put("docs/docs-manifest.json", { documented_release: "0.3.4" });
    await assert.rejects(checkSiteInformation(root), /documentation: version/);
});
test("reject download/catalog destinations that disagree", async t => {
    const { root, put, catalog } = await fixture(t);
    catalog.releases[0].platforms["windows-x64"].installer.url = "https://example.com/wrong.exe";
    await put("docs/hub-catalog.json", catalog);
    await assert.rejects(checkSiteInformation(root), /catalog installer and release asset differ/);
});
test("reject a download targeting the wrong release revision", async t => {
    const { root, put, release } = await fixture(t);
    release.assets[0].fallback_url = release.assets[0].fallback_url.replace("v0.4.1-v3", "v0.4.1-v2");
    await put("docs/release.json", release);
    await assert.rejects(checkSiteInformation(root), /another release/);
});
test("reject omitted localized APIs even when the index count is adjusted", async t => {
    const { root, put, index } = await fixture(t);
    index.symbols.pop();
    index.symbol_count = 1;
    await put("docs/api-index.json", index);
    await assert.rejects(checkSiteInformation(root), /missing published API/);
});

test("Hub can publish its own revision without changing engine wheel links", async t => {
    const { root, put, release, catalog } = await fixture(t);
    catalog.stable = catalog.releases[0].version = "0.4.1-4";
    catalog.releases[0].release_url = "https://github.com/ChenlizheMe/Infernux/releases/tag/hub-v0.4.1-v4";
    const installer = release.assets[0];
    installer.name = installer.name.replace("0.4.1-3", "0.4.1-4");
    installer.url = installer.url.replace("0.4.1-3", "0.4.1-4");
    installer.fallback_url = installer.fallback_url.replace("v0.4.1-v3", "hub-v0.4.1-v4").replace("0.4.1-3", "0.4.1-4");
    await put("docs/release.json", release);
    await put("docs/hub-catalog.json", catalog);
    assert.equal((await checkSiteInformation(root)).tag, "v0.4.1-v3");
    catalog.releases[0].release_url = catalog.releases[0].release_url.replace("-v4", "-v5");
    await put("docs/hub-catalog.json", catalog);
    await assert.rejects(checkSiteInformation(root), /Hub release destination differs/);
});
test("bundled Hub and engine revisions can differ while downloads retain the Hub version", async t => {
    const { root, put, release, catalog } = await fixture(t);
    catalog.stable = catalog.releases[0].version = "0.4.1-4";
    const installer = release.assets[0];
    for (const field of ["name", "url", "fallback_url"]) {
        installer[field] = installer[field].replace("0.4.1-3", "0.4.1-4");
    }
    await put("docs/release.json", release);
    await put("docs/hub-catalog.json", catalog);
    assert.equal((await checkSiteInformation(root)).tag, "v0.4.1-v3");
    installer.name = installer.name.replace("0.4.1-4", "0.4.1-3");
    await put("docs/release.json", release);
    await put("docs/hub-catalog.json", catalog);
    await assert.rejects(checkSiteInformation(root), /Hub installer revision differs/);
});

test("reject an API link whose rendered page is missing", async t => {
    const { root } = await fixture(t);
    await rm(path.join(root, "docs/wiki/site/en/api/Component.html"));
    await assert.rejects(checkSiteInformation(root), /API destination missing/);
});
