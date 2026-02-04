# Security Policy

## Overview

This skill registry implements a multi-layered security approach to protect users from malicious skills:

1. **Schema Validation** - Strict YAML frontmatter validation
2. **Automated Scanning** - Pattern-based security checks
3. **Reputation System** - Trust scores based on multiple factors

## Security Architecture

### Layer 1: Schema Validation

All SKILL.md files must conform to our [JSON Schema](schema/skill.schema.json):

- ✅ Required fields: `name`, `description`
- ✅ Name pattern: lowercase alphanumeric with hyphens only
- ✅ Description length: 10-500 characters
- ✅ Allowed licenses: MIT, Apache-2.0, GPL-3.0, etc.
- ✅ Maximum file size: 1MB per SKILL.md

### Layer 2: Automated Security Scanning

Our scanner checks for:

#### Dangerous Patterns (ERRORS - Fail Build)
- `eval()`, `exec()`, `__import__()`
- `os.system()`, `subprocess.call()` with `shell=True`
- `yaml.load()` (unsafe deserialization)
- Sensitive file path access (`/etc/passwd`, `~/.ssh`, etc.)

#### Suspicious Patterns (WARNINGS - Review Required)
- Network access (`requests`, `urllib`, `socket`)
- File deletion (`os.remove`, `shutil.rmtree`)
- Prompt injection indicators

#### Prompt Injection Detection
We scan for attempts to override system instructions:
- "ignore previous instructions"
- "disregard all"
- "system: you are"
- Hidden tokens (`<|im_start|>`, `</system>`)

### Layer 3: Reputation System

Each skill receives a trust score (0-100) based on:

| Factor | Weight | Description |
|--------|--------|-------------|
| Security Scan | 30% | No errors = 100, warnings deduct 5pts each |
| GitHub Stars | 25% | 0-10★=0-30, 10-50★=30-50, 500+★=85-100 |
| Author Reputation | 20% | Official=100, Verified=85, Unknown=50 |
| Updates | 15% | <30 days=100, >365 days=20 |
| Age | 10% | Older skills are more battle-tested |

#### Trust Levels

- 🌟 **Excellent (85-100)**: Highly trusted, official or verified authors
- ✅ **Good (70-84)**: Trustworthy, recommended for use
- ⚠️ **Moderate (50-69)**: Use with caution, review code first
- ❌ **Low (<50)**: Not recommended, security issues found

#### Verified Badges

- `official` - From anthropics/skills or openai/skills
- `verified` - Well-known community contributors
- `organization` - From major tech companies

## For Skill Authors

### Best Practices

1. **Use `yaml.safe_load()`** instead of `yaml.load()`
2. **Avoid shell=True** in subprocess calls
3. **Sanitize all user inputs** before use
4. **Minimize network access** - declare `requires_network: true`
5. **Request approval for writes** - set `requires_approval: true`
6. **Include a LICENSE** - Use standard open source licenses
7. **Keep skills updated** - Maintain within 90 days

### Submission Checklist

Before submitting a skill:

```bash
# Run security scanner locally
python scripts/security_scanner.py skills/your-skill/SKILL.md

# Validate schema
python -c "
import json, yaml, jsonschema
schema = json.load(open('schema/skill.schema.json'))
skill = yaml.safe_load(open('skills/your-skill/SKILL.md').read().split('---')[1])
jsonschema.validate(skill, schema)
print('✓ Valid')
"
```

### What Gets Flagged?

❌ **Will Fail**:
```python
# Unsafe YAML loading
import yaml
data = yaml.load(content)  # ❌ Use yaml.safe_load()

# Command injection
os.system(f"rm {user_input}")  # ❌ Never use user input directly

# Code execution
eval(user_code)  # ❌ Extremely dangerous
```

⚠️ **Will Warn**:
```python
# Network access (declare in frontmatter)
import requests
requests.get(url)  # ⚠️ Add requires_network: true

# File deletion (needs justification)
os.remove(temp_file)  # ⚠️ Document why this is needed
```

## For Users

### How to Evaluate Skills

1. **Check the trust score** - Look for 🌟 or ✅ badges
2. **Review the author** - Official and verified sources are safer
3. **Read the code** - Skills are transparent, inspect before use
4. **Check recent updates** - Active maintenance = better security
5. **Look for GitHub stars** - Community validation matters

### Red Flags

🚩 **Avoid skills that**:
- Have trust scores below 50
- Haven't been updated in over a year
- Come from unknown authors with no stars
- Have security warnings you don't understand
- Request excessive permissions

### Safe Usage

Even with high-trust skills:
- Review what the skill does before running
- Use in sandboxed environments when possible
- Keep skills updated to latest versions
- Report suspicious behavior immediately

## Reporting Security Issues

If you discover a security vulnerability:

1. **DO NOT** open a public issue
2. Email: security@[your-domain] (if available)
3. Or open a private security advisory on GitHub
4. Include:
   - Skill name and repo
   - Description of the vulnerability
   - Proof of concept (if possible)
   - Suggested fix

We aim to respond within 48 hours.

## Security Scan Results

All skills undergo automated security scans:
- Every PR triggers a security scan
- Daily scans of all skills at 06:00 UTC
- Results posted to GitHub Security tab
- Failed scans block merges

View scan results:
- [Security Advisories](../../security/advisories)
- [Dependabot Alerts](../../security/dependabot)
- [Code Scanning](../../security/code-scanning)

## Automated Protection

Our GitHub Actions workflows provide:

1. **Pull Request Scanning**
   - Blocks PRs with security errors
   - Comments with detailed findings
   - Suggests fixes when possible

2. **CodeQL Analysis**
   - Scans Python and JavaScript code
   - Detects 200+ vulnerability patterns
   - Runs on every push

3. **Dependency Scanning**
   - Trivy scans for CVEs
   - Alerts on vulnerable dependencies
   - Automated security updates

## Incident Response

If a malicious skill is discovered:

1. Skill is immediately removed from registry
2. Security advisory is published
3. Affected users are notified
4. Author is blocked if intentional
5. Post-mortem report is published

## Security Updates

- Schema updates: Announced in changelog
- Scanner updates: Automatic, no action needed
- Policy changes: 30-day notice period

## References

- [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [Anthropic Prompt Injection Defenses](https://www.anthropic.com/research/prompt-injection-defenses)
- [Microsoft LLM Security](https://www.microsoft.com/en-us/msrc/blog/2025/07/how-microsoft-defends-against-indirect-prompt-injection-attacks)
- [Agent Skills Security](https://skywork.ai/blog/ai-agent/claude-skills-security-threat-model-permissions-best-practices-2025/)

## License

This security policy and related tools are provided as-is under the MIT License.

---

**Last Updated**: 2026-01-08
**Version**: 1.0.0
