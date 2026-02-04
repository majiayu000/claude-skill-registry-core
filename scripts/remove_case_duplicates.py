#!/usr/bin/env python3
"""Remove case-duplicate files from Git, keeping only lowercase versions."""

import subprocess
import sys
from collections import defaultdict

def get_all_files():
    """Get all files tracked by Git."""
    result = subprocess.run(
        ['git', 'ls-tree', '-r', 'HEAD', '--name-only'],
        capture_output=True, text=True
    )
    return result.stdout.strip().split('\n')

def find_case_duplicates(files):
    """Find files that differ only by case."""
    # Group files by their lowercase path
    groups = defaultdict(list)
    for f in files:
        groups[f.lower()].append(f)
    
    # Find groups with duplicates
    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    return duplicates

def main():
    files = get_all_files()
    duplicates = find_case_duplicates(files)
    
    if not duplicates:
        print("✅ 没有发现大小写重复的文件")
        return 0
    
    print(f"发现 {len(duplicates)} 组大小写重复的文件:")
    
    files_to_remove = []
    for lower_path, variants in sorted(duplicates.items()):
        # Keep the lowercase version, remove others
        lowercase_version = lower_path
        for v in variants:
            if v != lowercase_version and v.lower() == lowercase_version:
                files_to_remove.append(v)
                print(f"  删除: {v} (保留: {lowercase_version})")
    
    if not files_to_remove:
        # If no exact lowercase match, keep the first one
        for lower_path, variants in sorted(duplicates.items()):
            keep = min(variants, key=str.lower)  # Keep most lowercase-like
            for v in variants:
                if v != keep:
                    files_to_remove.append(v)
                    print(f"  删除: {v} (保留: {keep})")
    
    if '--dry-run' in sys.argv:
        print(f"\n[DRY RUN] 将删除 {len(files_to_remove)} 个文件")
        return 0
    
    # Remove files from Git
    for f in files_to_remove:
        print(f"git rm: {f}")
        subprocess.run(['git', 'rm', '--cached', f], check=True)
    
    print(f"\n✅ 已从 Git 索引中删除 {len(files_to_remove)} 个重复文件")
    print("请运行 git commit 提交更改")
    return 0

if __name__ == '__main__':
    sys.exit(main())
