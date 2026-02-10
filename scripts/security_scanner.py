#!/usr/bin/env python3
"""
Security Scanner for SKILL.md files
Implements automated security checks for skill registry
"""

import os
import re
import json
import yaml
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple
import jsonschema

# Load schema
SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "skill.schema.json"

# Dangerous patterns to detect
DANGEROUS_PATTERNS = {
    # Code execution
    'eval': r'\beval\s*\(',
    'exec': r'\bexec\s*\(',
    '__import__': r'\b__import__\s*\(',
    'compile': r'\bcompile\s*\(',

    # Command injection
    'os.system': r'os\.system\s*\(',
    'subprocess.call': r'subprocess\.(call|run|Popen)\s*\(',
    'shell=True': r'shell\s*=\s*True',

    # File system manipulation
    'os.remove': r'os\.(remove|unlink|rmdir)\s*\(',
    'shutil.rmtree': r'shutil\.rmtree\s*\(',

    # Network access (flag for review)
    'requests': r'import\s+requests',
    'urllib': r'import\s+urllib',
    'socket': r'import\s+socket',

    # YAML unsafe loading
    'yaml.load': r'yaml\.load\s*\(',
    'yaml.unsafe_load': r'yaml\.unsafe_load\s*\(',

    # Prompt injection indicators
    'ignore_previous': r'ignore\s+(previous|prior|above)',
    'disregard': r'disregard\s+(all|previous|prior)',
    'system_prompt': r'system[\s_-]?prompt',
}

COMPILED_DANGEROUS_PATTERNS = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in DANGEROUS_PATTERNS.items()
}

# Sensitive file paths
SENSITIVE_PATHS = [
    '/etc/passwd', '/etc/shadow', '~/.ssh', '~/.aws',
    '/proc/', '/sys/', '$HOME/.env', '.env',
]

INJECTION_PATTERNS = [
    re.compile(r'ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts)', re.IGNORECASE),
    re.compile(r'disregard\s+(everything|all)', re.IGNORECASE),
    re.compile(r'forget\s+(previous|all)', re.IGNORECASE),
    re.compile(r'new\s+instructions?:', re.IGNORECASE),
    re.compile(r'system\s*:\s*you\s+are', re.IGNORECASE),
    re.compile(r'</system>', re.IGNORECASE),
    re.compile(r'<\|im_start\|>', re.IGNORECASE),
]


class SecurityScanner:
    """Security scanner for SKILL.md files"""

    def __init__(self, schema_path: str = None):
        self.schema_path = schema_path or SCHEMA_PATH
        self.schema = self._load_schema()
        self.issues = []

    def _load_schema(self) -> dict:
        """Load JSON Schema"""
        with open(self.schema_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def scan_file(self, skill_path: Path) -> Tuple[bool, List[Dict]]:
        """
        Scan a SKILL.md file for security issues
        Returns: (is_safe, issues_list)
        """
        self.issues = []

        if not skill_path.exists():
            self.issues.append({
                'severity': 'error',
                'type': 'file_not_found',
                'message': f'File not found: {skill_path}'
            })
            return False, self.issues

        try:
            content = skill_path.read_text(encoding='utf-8')
        except Exception as e:
            self.issues.append({
                'severity': 'error',
                'type': 'read_error',
                'message': f'Cannot read file: {e}'
            })
            return False, self.issues

        # 1. Validate file size
        if len(content) > 1_000_000:  # 1MB limit
            self.issues.append({
                'severity': 'error',
                'type': 'file_too_large',
                'message': f'File size {len(content)} exceeds 1MB limit'
            })

        # 2. Extract and validate frontmatter
        frontmatter = self._extract_frontmatter(content)
        if frontmatter:
            self._validate_schema(frontmatter)
        else:
            self.issues.append({
                'severity': 'error',
                'type': 'no_frontmatter',
                'message': 'SKILL.md must have YAML frontmatter'
            })

        # 3. Scan for dangerous patterns
        self._scan_dangerous_patterns(content, skill_path)

        # 4. Check for sensitive paths
        self._scan_sensitive_paths(content)

        # 5. Check bundled scripts
        self._scan_bundled_files(skill_path.parent)

        # 6. Prompt injection detection
        self._detect_prompt_injection(content)

        # Determine if safe (only fail on truly dangerous issues)
        critical_types = {
            'yaml_parse_error',
            'schema_error',
            'dangerous_pattern',
            'file_too_large',
        }
        has_critical = any(
            i['severity'] == 'error' and i.get('type') in critical_types
            for i in self.issues
        )
        return not has_critical, self.issues

    def _extract_frontmatter(self, content: str) -> dict:
        """Extract YAML frontmatter from SKILL.md"""
        if not content.startswith('---'):
            return None

        parts = content.split('---', 2)
        if len(parts) < 3:
            return None

        try:
            # Use safe_load to prevent YAML deserialization attacks
            return yaml.safe_load(parts[1])
        except yaml.YAMLError as e:
            self.issues.append({
                'severity': 'error',
                'type': 'yaml_parse_error',
                'message': f'Invalid YAML frontmatter: {e}'
            })
            return None

    def _validate_schema(self, frontmatter: dict):
        """Validate frontmatter against JSON Schema"""
        try:
            jsonschema.validate(instance=frontmatter, schema=self.schema)
        except jsonschema.ValidationError as e:
            self.issues.append({
                'severity': 'warning',
                'type': 'schema_validation',
                'message': f'Schema validation failed: {e.message}',
                'path': list(e.path)
            })
        except jsonschema.SchemaError as e:
            self.issues.append({
                'severity': 'error',
                'type': 'schema_error',
                'message': f'Invalid schema: {e}'
            })

    def _scan_dangerous_patterns(self, content: str, file_path: Path):
        """Scan for dangerous code patterns"""
        lines = content.split('\n')

        for pattern_name, pattern in COMPILED_DANGEROUS_PATTERNS.items():
            for line_num, line in enumerate(lines, 1):
                if pattern.search(line):
                    critical_patterns = {
                        'eval',
                        'exec',
                        '__import__',
                        'os.system',
                        'yaml.load',
                        'yaml.unsafe_load',
                        'shell=True',
                    }
                    severity = 'error' if pattern_name in critical_patterns else 'warning'

                    self.issues.append({
                        'severity': severity,
                        'type': 'dangerous_pattern',
                        'pattern': pattern_name,
                        'file': str(file_path),
                        'line': line_num,
                        'message': f'Dangerous pattern "{pattern_name}" found',
                        'code': line.strip()
                    })

    def _scan_sensitive_paths(self, content: str):
        """Check for references to sensitive file paths"""
        for path in SENSITIVE_PATHS:
            if path in content:
                self.issues.append({
                    'severity': 'warning',
                    'type': 'sensitive_path',
                    'message': f'References sensitive path: {path}'
                })

    def _scan_bundled_files(self, skill_dir: Path):
        """Scan bundled scripts and resources"""
        scripts_dir = skill_dir / 'scripts'
        if not scripts_dir.exists():
            return

        for script_file in scripts_dir.rglob('*'):
            if not script_file.is_file():
                continue

            # Check file size
            size = script_file.stat().st_size
            if size > 10_000_000:  # 10MB
                self.issues.append({
                    'severity': 'error',
                    'type': 'file_too_large',
                    'file': str(script_file),
                    'message': f'Bundled file too large: {size} bytes'
                })

            # Scan script content
            if script_file.suffix in ['.py', '.sh', '.js']:
                try:
                    content = script_file.read_text(encoding='utf-8')
                    self._scan_dangerous_patterns(content, script_file)
                except (OSError, UnicodeDecodeError):
                    pass

    def _detect_prompt_injection(self, content: str):
        """Detect potential prompt injection attempts"""
        for pattern in INJECTION_PATTERNS:
            if pattern.search(content):
                self.issues.append({
                    'severity': 'warning',
                    'type': 'prompt_injection',
                    'message': f'Potential prompt injection detected: {pattern.pattern}'
                })

    def generate_report(self) -> str:
        """Generate human-readable report"""
        if not self.issues:
            return "✓ No security issues found"

        report = []
        errors = [i for i in self.issues if i['severity'] == 'error']
        warnings = [i for i in self.issues if i['severity'] == 'warning']

        if errors:
            report.append(f"❌ {len(errors)} ERROR(S):")
            for issue in errors:
                report.append(f"  - {issue['type']}: {issue['message']}")

        if warnings:
            report.append(f"⚠️  {len(warnings)} WARNING(S):")
            for issue in warnings:
                report.append(f"  - {issue['type']}: {issue['message']}")

        return '\n'.join(report)


def resolve_scan_file_list(skills_dir: Path, file_list_path: Path) -> List[Path]:
    """
    Resolve a newline-delimited file list into SKILL.md paths under skills_dir.
    Lines may be absolute or relative paths.
    """
    if not file_list_path.exists():
        return []

    skills_root = skills_dir.resolve()
    selected = []
    seen = set()

    for raw in file_list_path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = raw.strip()
        if not line:
            continue

        candidate = Path(line)
        if not candidate.is_absolute():
            candidate = skills_dir / candidate
        candidate = candidate.resolve()

        try:
            candidate.relative_to(skills_root)
        except ValueError:
            # Ignore paths outside scan root for safety.
            continue

        if candidate.name != "SKILL.md":
            continue
        if not candidate.exists() or not candidate.is_file():
            continue

        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)

    return selected


def scan_directory(
    skills_dir: Path,
    output_file: Path = None,
    quiet: bool = False,
    selected_files: List[Path] = None,
) -> Dict:
    """Scan all skills in a directory"""
    scanner = SecurityScanner()
    skills_root = skills_dir.resolve()
    results = {
        'total': 0,
        'passed': 0,
        'failed': 0,
        'skills': []
    }

    if selected_files is None:
        scan_targets = skills_dir.rglob('SKILL.md')
    else:
        scan_targets = selected_files

    for skill_file in scan_targets:
        skill_file = skill_file.resolve()
        results['total'] += 1

        is_safe, issues = scanner.scan_file(skill_file)

        skill_result = {
            'path': str(skill_file.relative_to(skills_root)),
            'safe': is_safe,
            'issues': issues
        }

        results['skills'].append(skill_result)

        if is_safe:
            results['passed'] += 1
            if not quiet:
                print(f"✓ {skill_file.relative_to(skills_root)}")
        else:
            results['failed'] += 1
            if not quiet:
                print(f"✗ {skill_file.relative_to(skills_root)}")
                print(scanner.generate_report())

    # Save results
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Security scanner for SKILL.md files')
    parser.add_argument('path', help='Path to SKILL.md file or skills directory')
    parser.add_argument('--output', '-o', help='Output JSON report file')
    parser.add_argument('--strict', action='store_true', help='Fail on warnings')
    parser.add_argument(
        '--report-only',
        action='store_true',
        help='Always exit 0 after writing report (for CI reporting mode)',
    )
    parser.add_argument('--quiet', action='store_true', help='Only print summary')
    parser.add_argument(
        '--file-list',
        help='Optional newline-delimited list of SKILL.md paths to scan (absolute or relative to path)',
    )

    args = parser.parse_args()

    path = Path(args.path)

    if path.is_file():
        # Scan single file
        scanner = SecurityScanner()
        is_safe, issues = scanner.scan_file(path)

        print(scanner.generate_report())

        if args.output:
            with open(args.output, 'w') as f:
                json.dump({'safe': is_safe, 'issues': issues}, f, indent=2)

        if args.report_only:
            exit(0)
        exit(0 if is_safe or not args.strict else 1)

    elif path.is_dir():
        # Scan directory
        selected_files = None
        if args.file_list:
            selected_files = resolve_scan_file_list(path, Path(args.file_list))
            if not args.quiet:
                print(f"Using file list: {len(selected_files)} file(s)")

        results = scan_directory(path, args.output, quiet=args.quiet, selected_files=selected_files)

        print(f"\n{'='*60}")
        print(f"Total: {results['total']}")
        print(f"Passed: {results['passed']}")
        print(f"Failed: {results['failed']}")

        if args.report_only:
            exit(0)
        exit(0 if results['failed'] == 0 else 1)

    else:
        print(f"Error: {path} not found")
        exit(1)


if __name__ == '__main__':
    main()
