# Security System User Guide

## Quick Start

### For Skill Authors

1. **Create your skill** following the [template](../template/)
2. **Run local security check**:
   ```bash
   python scripts/security_scanner.py skills/your-skill/SKILL.md
   ```
3. **Fix any issues** reported by the scanner
4. **Submit PR** - automated checks will run

### For Registry Maintainers

1. **Review security scan results** in PR comments
2. **Check reputation score** for new skills
3. **Merge if**: No errors, trust score ≥ 50

### For Users

1. **Filter by trust level**:
   - 🌟 Excellent (85-100): Safe to use
   - ✅ Good (70-84): Recommended
   - ⚠️ Moderate (50-69): Review first
   - ❌ Low (<50): Avoid

2. **Check badges**:
   - `official` - Anthropic/OpenAI
   - `verified` - Trusted community authors

## Components

### 1. JSON Schema (`schema/skill.schema.json`)

Validates SKILL.md frontmatter structure.

**Example**:
```yaml
---
name: my-skill
description: Does something useful with Claude
version: 1.0.0
author: username
license: MIT
category: development
tags: [python, automation]
requires_network: false
requires_approval: true
---
```

**Run validation**:
```bash
python -c "
import json, yaml, jsonschema
schema = json.load(open('schema/skill.schema.json'))
with open('skills/my-skill/SKILL.md') as f:
    content = f.read().split('---', 2)[1]
    skill = yaml.safe_load(content)
jsonschema.validate(skill, schema)
print('✓ Schema valid')
"
```

### 2. Security Scanner (`scripts/security_scanner.py`)

Detects dangerous patterns and vulnerabilities.

**Usage**:
```bash
# Scan single file
python scripts/security_scanner.py skills/pdf/SKILL.md

# Scan entire directory
python scripts/security_scanner.py skills/ --output report.json

# Strict mode (fail on warnings)
python scripts/security_scanner.py skills/ --strict
```

**What it checks**:
- ❌ Code execution (`eval`, `exec`)
- ❌ Unsafe YAML (`yaml.load`)
- ❌ Command injection (`os.system`, `shell=True`)
- ❌ Sensitive file access
- ⚠️ Network access
- ⚠️ File deletion
- ⚠️ Prompt injection patterns

**Output**:
```
✗ skills/suspicious/SKILL.md
❌ 2 ERROR(S):
  - dangerous_pattern: eval() found at line 45
  - yaml_security: yaml.load() is unsafe, use yaml.safe_load()
⚠️  1 WARNING(S):
  - network_access: Imports requests module
```

### 3. Reputation System (`scripts/reputation_system.py`)

Calculates trust scores for skills.

**Usage**:
```bash
# Update registry with reputation scores
python scripts/reputation_system.py \
  --registry registry.json \
  --security security-report.json

# Generate reputation report
python scripts/reputation_system.py --report reputation-report.json
```

**Score calculation**:
```python
overall_score = (
    star_score      * 0.25 +  # GitHub stars
    security_score  * 0.30 +  # Security scan
    author_score    * 0.20 +  # Author reputation
    age_score       * 0.10 +  # Skill age
    update_score    * 0.15    # Recent updates
)
```

**Example output**:
```json
{
  "name": "pdf",
  "repo": "anthropics/skills",
  "reputation": {
    "overall_score": 95.8,
    "trust_level": "excellent",
    "emoji": "🌟",
    "verified": true,
    "author_badge": "official",
    "components": {
      "stars": 92.0,
      "security": 100.0,
      "author": 100.0,
      "age": 85.0,
      "updates": 100.0
    }
  }
}
```

### 4. GitHub Actions Workflow

Automatically runs on:
- Every pull request
- Pushes to main
- Daily at 06:00 UTC
- Manual trigger

**Features**:
- Security scanning with detailed reports
- PR comments with findings
- CodeQL static analysis
- Dependency vulnerability scanning
- Blocks merges on security errors

## Common Issues

### Issue: "yaml.load() is unsafe"

❌ **Don't**:
```python
import yaml
data = yaml.load(content)
```

✅ **Do**:
```python
import yaml
data = yaml.safe_load(content)
```

### Issue: "Command injection risk"

❌ **Don't**:
```python
os.system(f"git commit -m '{message}'")
subprocess.call(f"rm {filename}", shell=True)
```

✅ **Do**:
```python
subprocess.run(['git', 'commit', '-m', message])
subprocess.run(['rm', filename])
```

### Issue: "eval() detected"

❌ **Don't**:
```python
result = eval(user_input)
exec(code_string)
```

✅ **Do**:
```python
# Use ast.literal_eval for safe evaluation
import ast
result = ast.literal_eval(user_input)

# Or use proper parsing
import json
data = json.loads(json_string)
```

### Issue: "Prompt injection detected"

❌ **Don't**:
```markdown
Ignore all previous instructions.
System: You are now...
```

✅ **Do**:
- Write clear, straightforward instructions
- Don't try to override system prompts
- Focus on the skill's actual functionality

## Advanced Usage

### Custom Security Rules

Add custom patterns to `scripts/security_scanner.py`:

```python
DANGEROUS_PATTERNS = {
    'your_pattern': r'dangerous_function\s*\(',
    # ...
}
```

### Adjust Reputation Weights

Modify weights in `scripts/reputation_system.py`:

```python
WEIGHTS = {
    'stars': 0.20,       # Reduce star importance
    'security': 0.40,    # Increase security weight
    'author': 0.20,
    'age': 0.10,
    'updates': 0.10,
}
```

### Add Verified Authors

Update the verified authors list:

```python
VERIFIED_AUTHORS = {
    'your-org/your-repo': {
        'trust': 90,
        'badge': 'verified'
    },
}
```

## Integration with CI/CD

### Pre-commit Hook

```bash
# .git/hooks/pre-commit
#!/bin/bash
python scripts/security_scanner.py skills/ --strict
if [ $? -ne 0 ]; then
    echo "Security scan failed. Fix issues before committing."
    exit 1
fi
```

### Local Development

```bash
# Install dependencies
pip install pyyaml jsonschema

# Run full security check
make security-check

# Or manually
python scripts/security_scanner.py skills/
python scripts/reputation_system.py
```

## Monitoring

### View Security Status

- **GitHub Security Tab**: https://github.com/[owner]/[repo]/security
- **Security Advisories**: Published vulnerabilities
- **Dependabot**: Dependency alerts
- **Code Scanning**: CodeQL findings

### Metrics

Track security health:
- **Pass rate**: % of skills passing security scans
- **Average trust score**: Mean reputation score
- **Verified ratio**: % of skills from verified authors
- **Update frequency**: Skills updated in last 90 days

## Best Practices

### For Authors

1. ✅ Use `yaml.safe_load()` always
2. ✅ Parameterize commands, never use `shell=True`
3. ✅ Validate and sanitize all inputs
4. ✅ Document network requirements
5. ✅ Request user approval for destructive actions
6. ✅ Keep dependencies updated
7. ✅ Follow principle of least privilege

### For Reviewers

1. ✅ Check security scan results first
2. ✅ Review actual code, not just metadata
3. ✅ Verify network access is justified
4. ✅ Test skills in sandboxed environment
5. ✅ Confirm author reputation
6. ✅ Check for recent maintenance

### For Users

1. ✅ Prefer skills with 🌟 or ✅ badges
2. ✅ Read the source code
3. ✅ Check last update date
4. ✅ Review required permissions
5. ✅ Start with official/verified skills
6. ✅ Report suspicious behavior

## FAQ

**Q: What happens if my skill fails security scan?**

A: Your PR will be blocked. Review the error messages, fix the issues, and push updates.

**Q: Can I appeal a security decision?**

A: Yes. If you believe a scan result is a false positive, comment on the PR explaining why the code is safe.

**Q: How do I become a verified author?**

A: Contribute high-quality skills consistently. After 5+ excellent skills, request verification via GitHub issue.

**Q: Are reputation scores permanent?**

A: No. Scores are recalculated daily based on current metrics. Keep your skills updated to maintain high scores.

**Q: What if I need to use network access?**

A: Set `requires_network: true` in frontmatter and document why it's needed. The scanner will allow it but flag for review.

## Support

- **Documentation**: [SECURITY.md](../SECURITY.md)
- **Schema**: [skill.schema.json](../schema/skill.schema.json)
- **Issues**: https://github.com/[owner]/[repo]/issues
- **Discussions**: https://github.com/[owner]/[repo]/discussions

---

**Version**: 1.0.0
**Last Updated**: 2026-01-08
