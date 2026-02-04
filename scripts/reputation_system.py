#!/usr/bin/env python3
"""
Reputation System for Skills
Calculates trust scores based on multiple factors
"""

import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List


class ReputationSystem:
    """Calculate and manage skill reputation scores"""

    # Scoring weights
    WEIGHTS = {
        'stars': 0.25,           # GitHub stars
        'security': 0.30,        # Security scan results
        'author': 0.20,          # Author reputation
        'age': 0.10,             # Time since creation
        'updates': 0.15,         # Recent updates
    }

    # Trusted authors (verified accounts)
    VERIFIED_AUTHORS = {
        'anthropics/skills': {'trust': 100, 'badge': 'official'},
        'openai/skills': {'trust': 100, 'badge': 'official'},
        'obra/superpowers': {'trust': 90, 'badge': 'verified'},
        'alirezarezvani': {'trust': 85, 'badge': 'verified'},
    }

    def __init__(self, registry_path: str = 'registry.json',
                 security_report: str = 'security-report.json'):
        self.registry_path = Path(registry_path)
        self.security_report_path = Path(security_report)
        self.skills = []
        self.security_data = {}

    def load_data(self):
        """Load registry and security data"""
        if self.registry_path.exists():
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.skills = data.get('skills', [])

        if self.security_report_path.exists():
            with open(self.security_report_path, 'r', encoding='utf-8') as f:
                report = json.load(f)
                self.security_data = {
                    s['path']: s for s in report.get('skills', [])
                }

    def calculate_star_score(self, stars: int) -> float:
        """
        Calculate score based on GitHub stars
        0-10: 0-30
        10-50: 30-50
        50-100: 50-70
        100-500: 70-85
        500+: 85-100
        """
        if stars < 10:
            return min(30, stars * 3)
        elif stars < 50:
            return 30 + (stars - 10) * 0.5
        elif stars < 100:
            return 50 + (stars - 50) * 0.4
        elif stars < 500:
            return 70 + (stars - 100) * 0.0375
        else:
            return min(100, 85 + (stars - 500) * 0.01)

    def calculate_security_score(self, skill_path: str) -> float:
        """
        Calculate security score
        No issues: 100
        Warnings only: 70-90
        Errors: 0-50
        """
        security = self.security_data.get(skill_path)
        if not security:
            return 50  # No scan data = medium trust

        if security.get('safe'):
            issues = security.get('issues', [])
            warnings = len([i for i in issues if i['severity'] == 'warning'])
            if warnings == 0:
                return 100
            else:
                # Deduct 5 points per warning
                return max(70, 100 - warnings * 5)
        else:
            # Has errors
            issues = security.get('issues', [])
            errors = len([i for i in issues if i['severity'] == 'error'])
            # Severe penalty for errors
            return max(0, 50 - errors * 10)

    def calculate_author_score(self, repo: str) -> Dict[str, any]:
        """Calculate author reputation score"""
        for verified_repo, info in self.VERIFIED_AUTHORS.items():
            if repo.startswith(verified_repo):
                return {
                    'score': info['trust'],
                    'badge': info['badge'],
                    'verified': True
                }

        # Check if from known organization
        if repo.startswith(('microsoft/', 'google/', 'facebook/')):
            return {'score': 80, 'badge': 'organization', 'verified': False}

        # Default for unknown authors
        return {'score': 50, 'badge': None, 'verified': False}

    def calculate_age_score(self, created_at: str) -> float:
        """
        Calculate score based on age
        Older skills are more battle-tested
        < 1 month: 50
        1-3 months: 60
        3-6 months: 70
        6-12 months: 80
        > 12 months: 90-100
        """
        if not created_at:
            return 50

        try:
            created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            age = (datetime.now(created.tzinfo) - created).days

            if age < 30:
                return 50
            elif age < 90:
                return 60
            elif age < 180:
                return 70
            elif age < 365:
                return 80
            else:
                return min(100, 90 + (age - 365) / 365 * 10)
        except (ValueError, TypeError):
            return 50

    def calculate_update_score(self, updated_at: str) -> float:
        """
        Calculate score based on recent updates
        Updated < 30 days: 100
        30-90 days: 80
        90-180 days: 60
        180-365 days: 40
        > 365 days: 20
        """
        if not updated_at:
            return 50

        try:
            updated = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            days = (datetime.now(updated.tzinfo) - updated).days

            if days < 30:
                return 100
            elif days < 90:
                return 80
            elif days < 180:
                return 60
            elif days < 365:
                return 40
            else:
                return max(20, 40 - (days - 365) / 365 * 20)
        except (ValueError, TypeError):
            return 50

    def calculate_reputation(self, skill: Dict) -> Dict:
        """Calculate overall reputation score for a skill"""
        repo = skill.get('repo', '')
        skill_path = skill.get('path', '')

        # Component scores
        star_score = self.calculate_star_score(skill.get('stars', 0))
        security_score = self.calculate_security_score(skill_path)
        author_info = self.calculate_author_score(repo)
        age_score = self.calculate_age_score(skill.get('created_at'))
        update_score = self.calculate_update_score(skill.get('updated_at'))

        # Weighted average
        overall_score = (
            star_score * self.WEIGHTS['stars'] +
            security_score * self.WEIGHTS['security'] +
            author_info['score'] * self.WEIGHTS['author'] +
            age_score * self.WEIGHTS['age'] +
            update_score * self.WEIGHTS['updates']
        )

        # Determine trust level
        if overall_score >= 85:
            trust_level = 'excellent'
            emoji = '🌟'
        elif overall_score >= 70:
            trust_level = 'good'
            emoji = '✅'
        elif overall_score >= 50:
            trust_level = 'moderate'
            emoji = '⚠️'
        else:
            trust_level = 'low'
            emoji = '❌'

        return {
            'overall_score': round(overall_score, 1),
            'trust_level': trust_level,
            'emoji': emoji,
            'components': {
                'stars': round(star_score, 1),
                'security': round(security_score, 1),
                'author': round(author_info['score'], 1),
                'age': round(age_score, 1),
                'updates': round(update_score, 1),
            },
            'author_badge': author_info.get('badge'),
            'verified': author_info.get('verified', False),
        }

    def update_registry(self, output_path: str = None):
        """Add reputation scores to registry"""
        output_path = output_path or self.registry_path

        for skill in self.skills:
            reputation = self.calculate_reputation(skill)
            skill['reputation'] = reputation

        # Sort by reputation score
        self.skills.sort(key=lambda s: s.get('reputation', {}).get('overall_score', 0), reverse=True)

        # Save updated registry
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'updated_at': datetime.utcnow().isoformat() + 'Z',
                'total_count': len(self.skills),
                'skills': self.skills,
            }, f, indent=2, ensure_ascii=False)

        # Generate summary
        trust_levels = {'excellent': 0, 'good': 0, 'moderate': 0, 'low': 0}
        for skill in self.skills:
            level = skill.get('reputation', {}).get('trust_level', 'low')
            trust_levels[level] = trust_levels.get(level, 0) + 1

        print(f"✓ Updated registry with reputation scores")
        print(f"  🌟 Excellent: {trust_levels['excellent']}")
        print(f"  ✅ Good: {trust_levels['good']}")
        print(f"  ⚠️  Moderate: {trust_levels['moderate']}")
        print(f"  ❌ Low: {trust_levels['low']}")

    def generate_report(self, output_path: str = 'reputation-report.json'):
        """Generate detailed reputation report"""
        report = {
            'generated_at': datetime.utcnow().isoformat() + 'Z',
            'total_skills': len(self.skills),
            'top_skills': [],
            'low_trust_skills': [],
            'statistics': {
                'average_score': 0,
                'median_score': 0,
                'verified_count': 0,
                'trust_distribution': {'excellent': 0, 'good': 0, 'moderate': 0, 'low': 0}
            }
        }

        scores = [s.get('reputation', {}).get('overall_score', 0) for s in self.skills]
        if scores:
            report['statistics']['average_score'] = round(sum(scores) / len(scores), 1)
            report['statistics']['median_score'] = round(sorted(scores)[len(scores) // 2], 1)

        for skill in self.skills:
            rep = skill.get('reputation', {})
            level = rep.get('trust_level', 'low')
            report['statistics']['trust_distribution'][level] += 1

            if rep.get('verified'):
                report['statistics']['verified_count'] += 1

        # Top skills
        report['top_skills'] = [
            {
                'name': s['name'],
                'repo': s['repo'],
                'score': s['reputation']['overall_score'],
                'trust_level': s['reputation']['trust_level'],
            }
            for s in self.skills[:20]
        ]

        # Low trust skills
        report['low_trust_skills'] = [
            {
                'name': s['name'],
                'repo': s['repo'],
                'score': s['reputation']['overall_score'],
                'issues': s['reputation']['components'],
            }
            for s in self.skills if s.get('reputation', {}).get('overall_score', 100) < 50
        ]

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"✓ Reputation report saved to {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Calculate skill reputation scores')
    parser.add_argument('--registry', default='registry.json', help='Registry file')
    parser.add_argument('--security', default='security-report.json', help='Security report')
    parser.add_argument('--output', '-o', help='Output registry file (default: update in place)')
    parser.add_argument('--report', help='Generate reputation report')

    args = parser.parse_args()

    system = ReputationSystem(
        registry_path=args.registry,
        security_report=args.security
    )

    system.load_data()
    system.update_registry(output_path=args.output)

    if args.report:
        system.generate_report(output_path=args.report)


if __name__ == '__main__':
    main()
