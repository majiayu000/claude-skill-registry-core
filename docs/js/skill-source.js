// Skill source links and install commands, shared by search cards and the detail modal.

// Split an install path ("owner/repo/path[/SKILL.md]") into encoded GitHub parts
function parseSkillSource(install, branch = 'main') {
    if (!install) return null;
    const parts = install.split('/');
    if (parts.length < 2) return null;

    let path = parts.slice(2).join('/').replace(/\/+$/, '');
    if (/\/SKILL\.md$/i.test(path)) {
        path = path.replace(/\/SKILL\.md$/i, '');
    } else if (/^SKILL\.md$/i.test(path)) {
        path = '';
    }
    return {
        base: `https://github.com/${encodeURIComponent(parts[0])}/${encodeURIComponent(parts[1])}`,
        branch: encodeURIComponent(branch),
        path: path ? path.split('/').map(encodeURIComponent).join('/') : ''
    };
}

// Generate proper GitHub URL from install path and branch
function getGitHubUrl(install, branch = 'main') {
    const source = parseSkillSource(install, branch);
    if (!source) return '#';
    return `${source.base}/blob/${source.branch}/${source.path ? source.path + '/' : ''}SKILL.md`;
}

function shellQuote(value) {
    if (/^[A-Za-z0-9._~:\/%-]+$/.test(value)) return value;
    return "'" + value.split("'").join("'\\''") + "'";
}

// skills CLI command (github.com/vercel-labs/skills); it prompts for target agents
function getInstallCommand(install, branch = 'main') {
    const source = parseSkillSource(install, branch);
    if (!source) return '';
    const tree = `${source.base}/tree/${source.branch}${source.path ? '/' + source.path : ''}`;
    return `npx skills add ${shellQuote(tree)}`;
}
