import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

export function hubReleaseInformationErrors(release, catalog) {
    const errors = [];
    const stable = catalog?.releases?.find(item => item.version === catalog.stable);
    if (!stable) return ["Hub catalog: stable release is missing"];
    const identity = catalog.stable?.match(/^(\d+\.\d+\.\d+)(?:-([1-9]\d*))?$/);
    if (identity?.[1] !== release.version) errors.push("Hub stable base version differs from engine release");
    const tag = `v${identity?.[1]}${identity?.[2] ? `-v${identity[2]}` : ""}`;
    if (stable.release_url !== release.release_url
        && ![tag, `hub-${tag}`].some(value => stable.release_url?.endsWith(`/releases/tag/${value}`))) {
        errors.push("Hub release destination differs from its own revision");
    }
    const platforms = Object.entries(stable.platforms || {});
    if (!platforms.length) errors.push("Hub catalog: platform downloads are missing");
    for (const [id, platform] of platforms) {
        const installer = platform.installer;
        if (!installer?.name?.startsWith(`InfernuxHubInstaller-${catalog.stable}-`)) {
            errors.push(`${id}: Hub installer revision differs from the catalog`);
        }
        const asset = release.assets?.find(item => item.kind === "hub-installer" && item.name === installer?.name);
        if (!asset || asset.url !== installer?.url || asset.fallback_url !== installer?.fallback_url) {
            errors.push(`${id}: catalog installer and release asset differ`);
        }
    }
    return errors;
}

// Check published facts and destinations, independently of the site's design.
export async function checkSiteInformation(repoRoot = process.cwd()) {
    const docs = path.join(repoRoot, "docs");
    const json = async name => JSON.parse(await readFile(path.join(docs, name), "utf8"));
    const [release, notes, manifest, index, changes, catalog, platforms] = await Promise.all(
        ["release.json", "release-notes.json", "docs-manifest.json", "api-index.json",
            "api-changes.json", "hub-catalog.json", "platform-support.json"].map(json)
    );
    const errors = [];
    const require = (condition, message) => { if (!condition) errors.push(message); };
    const version = release.version;
    require(/^\d+\.\d+\.\d+$/.test(version), "release.json: invalid release version");
    const tag = release.tag?.match(/^v(\d+\.\d+\.\d+)(?:-v([1-9]\d*))?$/);
    require(tag?.[1] === version, "release.json: tag/version mismatch");
    for (const [label, actual] of Object.entries({
        "release notes": notes.version, "documentation": manifest.documented_release,
        "API index": index.generated_for_release,
        "platform release": platforms.released_version,
    })) require(actual === version, `${label}: version ${actual} differs from ${version}`);
    const apiRelease = release.tag.slice(1);
    require(changes.current_release === apiRelease, "API changes: wrong release revision");
    require(notes.tag === release.tag, "release notes: tag mismatch");
    const project = await readFile(path.join(repoRoot, "pyproject.toml"), "utf8");
    const engineVersion = project.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
    require(platforms.development_version === engineVersion, "platform development version differs from pyproject.toml");
    if (engineVersion !== version) {
        const parts = value => String(value).split(".").map(Number);
        const current = parts(engineVersion), published = parts(version);
        const firstDifference = current.findIndex((value, i) => value !== published[i]);
        require(current.length === 3 && firstDifference >= 0 && current[firstDifference] > published[firstDifference],
            "development version must not precede the published version");
        for (const name of ["UpdateLog.md", "UpdateLog-zh.md"]) {
            const text = await readFile(path.join(repoRoot, name), "utf8");
            require(text.startsWith(`# Infernux v${engineVersion} `), `${name}: missing upcoming version heading`);
        }
    }

    function downloadUrl(value, name, label) {
        try {
            const url = new URL(value);
            require(url.protocol === "https:" && !url.username && !url.password,
                `${label}: download must use HTTPS without credentials`);
            require(decodeURIComponent(url.pathname.split("/").pop()) === name,
                `${label}: URL filename differs from ${name}`);
        } catch { errors.push(`${label}: invalid download URL`); }
    }
    const stable = catalog.releases?.find(item => item.version === catalog.stable);
    errors.push(...hubReleaseInformationErrors(release, catalog));
    const hubTag = stable?.release_url?.split("/").pop();
    const assets = release.assets || [];
    require(assets.length > 0, "release.json: download assets are empty");
    require(new Set(assets.map(asset => asset.name)).size === assets.length, "release.json: duplicate asset names");
    for (const asset of assets) {
        require(asset.name?.includes(version), `${asset.name}: wrong release version`);
        require(Number.isInteger(asset.size_bytes) && asset.size_bytes > 0, `${asset.name}: missing download size`);
        downloadUrl(asset.url, asset.name, asset.name);
        if (asset.fallback_url) {
            downloadUrl(asset.fallback_url, asset.name, `${asset.name} alternate`);
            const assetTag = asset.kind === "hub-installer" ? hubTag : release.tag;
            require(new URL(asset.fallback_url).pathname.includes(`/releases/download/${assetTag}/`),
                `${asset.name}: alternate download targets another release`);
        }
    }
    require(notes.release_url === release.release_url, "Engine release notes destination differs");
    require(release.release_url?.endsWith(`/releases/tag/${release.tag}`), "release URL differs from release tag");
    for (const platform of platforms.platforms || []) {
        if (!platform.released || !platform.editor) continue;
        const installer = stable?.platforms?.[platform.id]?.installer;
        const asset = assets.find(item => item.kind === "hub-installer" && item.name === installer?.name);
        require(Boolean(asset) && asset.url === installer?.url && asset.fallback_url === installer?.fallback_url,
            `${platform.id}: catalog installer and release asset differ`);
        const wheel = platform.id === "windows-x64" ? "win_amd64" : platform.id === "linux-x64" ? "manylinux" : platform.id;
        require(assets.some(item => item.kind === "python-wheel" && item.name.includes(wheel)
            && item.name.includes(platforms.python_abi)), `${platform.id}: released editor wheel/ABI missing`);
    }

    const snapshot = await json(`api-snapshots/${apiRelease}.json`);
    require(snapshot.release === apiRelease, "API snapshot: wrong release revision");
    const symbols = index.symbols || [];
    require(symbols.length > 0 && index.symbol_count === symbols.length, "API index: invalid symbol count");
    require(new Set(symbols.map(symbol => symbol.id)).size === symbols.length, "API index: duplicate identifiers");
    const exists = async file => stat(file).then(info => info.isFile()).catch(() => false);
    const code = version.replaceAll(".", "");
    for (const name of ["index.html", "css/style.css"]) {
        const file = path.join(docs, name);
        if (!await exists(file)) continue;
        const text = await readFile(file, "utf8");
        for (const match of text.matchAll(/data-release-code="([^"]+)"[^>]*>([^<]*)</g)) {
            require(match[2] === match[1].replaceAll("{code}", code), `${name}: visible release code differs from ${version}`);
        }
        for (const match of text.matchAll(/--release-code:\s*"([^"]+)"/g)) {
            require(match[1] === code, `${name}: decorative release code differs from ${version}`);
        }
    }
    for (const [folder, language] of [["en", "en"], ["zh", "zh-CN"]]) {
        const localized = symbols.filter(symbol => symbol.language === language);
        const keys = new Set(localized.map(symbol => symbol.symbol_key));
        const routes = new Set(localized.map(symbol => symbol.url));
        const sourceRoot = path.join(docs, "wiki/docs", folder, "api");
        const files = (await readdir(sourceRoot)).filter(name => name.endsWith(".md") && name !== "index.md");
        require(files.length === localized.length && keys.size === localized.length,
            `${language}: API sources and index entries are incomplete or duplicated`);
        for (const symbol of snapshot.symbols || []) {
            // A published API is identified by its document route. Import
            // spelling in prose may change without removing the API itself.
            const route = new URL(symbol.canonical_url).pathname.replace("/en/api/", `/${folder}/api/`);
            require(routes.has(route), `${language}: missing published API ${symbol.symbol_key}`);
        }
        const sources = new Set();
        for (const symbol of localized) {
            sources.add(path.basename(symbol.source || ""));
            require(symbol.source?.startsWith(`docs/wiki/docs/${folder}/api/`), `${symbol.id}: invalid source location`);
            require(await exists(path.resolve(repoRoot, symbol.source || "")), `${symbol.id}: source document missing`);
            for (const route of [symbol.url, symbol.counterpart_url]) {
                require(/^\/wiki\/site\/(en|zh)\/api\/[^/]+\.html$/.test(route || ""), `${symbol.id}: invalid API route`);
                require(await exists(path.join(docs, String(route).replace(/^\//, ""))), `${symbol.id}: API destination missing: ${route}`);
            }
        }
        require(files.every(file => sources.has(file)), `${language}: API documents missing from the index`);
    }
    if (errors.length) throw new Error(`Website information errors:\n${errors.join("\n")}`);
    return { version, tag: release.tag, apiSymbols: symbols.length, downloads: assets.length };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
    console.log("Website information verified:", await checkSiteInformation());
}
