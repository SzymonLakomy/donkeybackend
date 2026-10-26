"""Straznik sekretow: zaden plik zrodlowy w repo nie moze zawierac klucza API.

Ten test jest tania siatka na powrot wycieku (klucz OpenAI byl kiedys wpisany na twardo
w donkey_ai/openai_test.py). Nie zastepuje skanera historii gita - sprawdza tylko
aktualne drzewo robocze.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# Wzorce uzywane przez popularnych dostawcow; celowo dosc szerokie.
SECRET_PATTERNS = [
    ("OpenAI / sekret typu sk-...", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("klucz Google API", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("klucz Stripe", re.compile(r"\b(sk|rk)_(live|test)_[0-9A-Za-z]{16,}")),
    ("prywatny klucz PEM", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("przypisanie api_key z dlugim literatem", re.compile(r"api_key\s*=\s*[\"'][^\"']{20,}[\"']")),
]

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "staticfiles",
    "htmlcov", ".next", ".idea", ".expo", "android",
}
SKIP_FILES = {".env", ".env.local", ".env.production"}
TEXT_SUFFIXES = {".py", ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".js", ".ts"}

# Pliki, w ktorych wzorzec moze wystapic legalnie (opis procedury, nie sekret).
ALLOWLIST = {
    "docs/runbook.md",
    "accounts/tests_secrets.py",
}


def _iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name in SKIP_FILES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


class NoSecretsInRepoTests(SimpleTestCase):
    def test_no_api_keys_committed_in_working_tree(self):
        root = Path(settings.BASE_DIR)
        findings = []

        for path in _iter_files(root):
            rel = path.relative_to(root).as_posix()
            if rel in ALLOWLIST:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for label, pattern in SECRET_PATTERNS:
                match = pattern.search(content)
                if not match:
                    continue
                line_no = content[: match.start()].count("\n") + 1
                # Nie wypisujemy wartosci - tylko miejsce, zeby nie powielac sekretu.
                findings.append(f"{rel}:{line_no} ({label})")

        self.assertEqual(findings, [], "Znaleziono potencjalne sekrety w repo: " + "; ".join(findings))
