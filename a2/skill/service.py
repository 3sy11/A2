"""SkillService — progressive disclosure."""

from __future__ import annotations

import os
import re
from pathlib import Path

from a2.kernel import A2Service


class SkillService(A2Service):
    domain = 'skill'
    commands = ['commands']

    skill_dirs: list = ['.a2/skills']
    max_body_chars: int = 40000

    def scan(self) -> int:
        count = 0
        for d in self.skill_dirs:
            root = Path(d)
            if not root.exists():
                continue
            for skill_md in root.rglob('SKILL.md'):
                meta = self._parse_frontmatter(skill_md)
                if meta.get('name'):
                    count += 1
        return count

    def catalog(self, enabled_only: bool = True) -> list:
        skills = []
        for d in self.skill_dirs:
            root = Path(d)
            if not root.exists():
                continue
            for skill_md in root.rglob('SKILL.md'):
                meta = self._parse_frontmatter(skill_md)
                if meta.get('name'):
                    if enabled_only and not meta.get('enabled', True):
                        continue
                    skills.append({
                        'name': meta['name'],
                        'description': meta.get('description', ''),
                        'keywords': meta.get('keywords', []),
                        'body_path': str(skill_md),
                        'enabled': meta.get('enabled', True),
                    })
        return skills

    def match(self, query: str, top_k: int = 3) -> list:
        query_lower = query.lower()
        scored = []
        for skill in self.catalog():
            score = 0
            if query_lower in skill['name'].lower():
                score += 3
            if query_lower in skill['description'].lower():
                score += 2
            for kw in skill.get('keywords', []):
                if kw.lower() in query_lower:
                    score += 1
            if score > 0:
                scored.append({**skill, 'score': score})
        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:top_k]

    def body(self, name: str) -> str:
        for skill in self.catalog(enabled_only=False):
            if skill['name'] == name:
                path = Path(skill['body_path'])
                text = path.read_text(encoding='utf-8')
                return text[: self.max_body_chars]
        return ''

    def render_metadata(self, skills: list) -> str:
        lines = ['Available skills:']
        for s in skills:
            lines.append(f'- {s["name"]}: {s.get("description", "")}')
        return '\n'.join(lines)

    def _parse_frontmatter(self, path: Path) -> dict:
        text = path.read_text(encoding='utf-8')
        match = re.match(r'^---\s*\n(.*?)\n---', text, re.DOTALL)
        if not match:
            return {'name': path.parent.name, 'description': text[:100]}
        meta = {}
        for line in match.group(1).splitlines():
            if ':' in line:
                key, val = line.split(':', 1)
                meta[key.strip()] = val.strip().strip('"')
        if 'name' not in meta:
            meta['name'] = path.parent.name
        return meta
