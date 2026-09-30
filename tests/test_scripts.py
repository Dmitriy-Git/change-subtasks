"""Тесты скриптов скилла. Запуск из корня репозитория: python -m pytest tests"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
import check_coverage as cc  # noqa: E402
import parse_change as pc  # noqa: E402

SPEC = """## ADDED Requirements

### Requirement: Session expiry
Система SHALL считать токен недействительным через 30 минут.

#### Scenario: Просроченный токен
- **WHEN** токен старше 30 минут
- **THEN** 401
"""
DESIGN = "## Decisions\n\n### Token storage\nexp в JWT.\n"
PROPOSAL = "## Why\nx\n\n## What Changes\n- срок жизни\n"
TASKS = "## 1. Реализация\n- [ ] 1.1 Добавить exp\n- [ ] 1.2 Проверять срок\n"


def item(sid, source, deps="—", size="M", done=False, accept="WHEN a THEN b"):
    mark = "x" if done else " "
    return (
        f"- [{mark}] {sid} Сделать {sid}\n"
        f"  - Источник: {source}\n"
        f"  - Зависит от: {deps}\n"
        f"  - Приёмка: {accept}\n"
        f"  - Размер: {size}\n"
    )


def make_change(root, name="c1", spec=SPEC, design=DESIGN, proposal=PROPOSAL, tasks=TASKS, specs=None):
    d = root / "openspec" / "changes" / name
    (d / "specs" / "auth").mkdir(parents=True)
    (d / "specs" / "auth" / "spec.md").write_text(spec, encoding="utf-8")
    for cap, text in (specs or {}).items():
        (d / "specs" / cap).mkdir(parents=True, exist_ok=True)
        (d / "specs" / cap / "spec.md").write_text(text, encoding="utf-8")
    for fname, text in (("design.md", design), ("proposal.md", proposal), ("tasks.md", tasks)):
        if text is not None:
            (d / fname).write_text(text, encoding="utf-8")
    return d


def write_subs(d, body, matrix=True, edits="Нет"):
    text = "# Подзадачи\n\n## 1. Группа\n\n" + body
    text += f"\n## Внесённые правки в артефакты\n\n{edits}\n"
    if matrix:
        text += "\n## Матрица покрытия\n\n| Capability | Requirement | Дельта | Подзадачи |\n|---|---|---|---|\n"
        text += "| auth | Session expiry | ADDED | 1.1 |\n"
    p = d / "subtasks.md"
    p.write_text(text, encoding="utf-8")
    return p


def run_check(d, **kw):
    return cc.check(d, d / "subtasks.md", **kw)


def has(msgs, pattern):
    return any(re.search(pattern, m) for m in msgs)


OK_SOURCE = "spec:auth/Requirement:Session expiry; design:§Token storage; tasks:1.1, 1.2"


def test_clean_change_passes(tmp_path):
    d = make_change(tmp_path)
    write_subs(d, item("1.1", OK_SOURCE))
    errors, warnings, st = run_check(d)
    assert errors == []
    assert st["sizes"] == {"S": 0, "M": 1, "L": 0}


# --- лимит и размеры ---

def test_limit_exceeded(tmp_path):
    d = make_change(tmp_path)
    body = item("1.1", OK_SOURCE) + "".join(item(f"1.{i}", "design:§Token storage") for i in range(2, 12))
    write_subs(d, body)
    errors, _, st = run_check(d)
    assert st["subtasks"] == 11
    assert has(errors, r"Подзадач 11, лимит 10")
    errors, _, _ = run_check(d, max_subtasks=11)
    assert not has(errors, "лимит")


def test_too_many_small(tmp_path):
    d = make_change(tmp_path)
    body = item("1.1", OK_SOURCE, size="S") + item("1.2", "design:§Token storage", size="S") + item("1.3", "design:§Token storage", size="M")
    write_subs(d, body)
    _, warnings, _ = run_check(d)
    assert has(warnings, r"размера S: 2 из 3")


def test_single_small_is_fine(tmp_path):
    d = make_change(tmp_path)
    write_subs(d, item("1.1", OK_SOURCE, size="S"))
    _, warnings, _ = run_check(d)
    assert not has(warnings, "размера S")


# --- статусы ---

@pytest.mark.parametrize(
    "tasks,done,pattern",
    [
        ("- [x] 1.1 a\n- [ ] 1.2 b\n", False, "объединены выполненные"),
        ("- [x] 1.1 a\n- [x] 1.2 b\n", False, "не отмечена \\[x\\]"),
        ("- [ ] 1.1 a\n- [ ] 1.2 b\n", True, "пункты tasks.md не выполнены"),
    ],
)
def test_status_errors(tmp_path, tasks, done, pattern):
    d = make_change(tmp_path, tasks="## 1. R\n" + tasks)
    write_subs(d, item("1.1", OK_SOURCE, done=done))
    errors, _, _ = run_check(d)
    assert has(errors, pattern)


# --- ссылки и зависимости ---

def test_bad_refs_and_deps(tmp_path):
    d = make_change(tmp_path)
    body = item("1.1", OK_SOURCE)
    body += item("1.2", "spec:auth/Requirement:Session expiry. См. tasks:9.9", deps="1.1, abc")
    body += item("1.3", "design:§Nonexistent; proposal:§What Changes; tasks:7.7")
    write_subs(d, body)
    errors, _, _ = run_check(d)
    assert has(errors, r"1\.2.*разделить через «;»")
    assert has(errors, r"1\.2.*не id подзадачи: «abc»")
    assert has(errors, r"1\.3.*несуществующий раздел design\.md")
    assert not has(errors, "proposal")
    assert has(errors, r"1\.3.*tasks:7\.7")


def test_cycle(tmp_path):
    d = make_change(tmp_path)
    write_subs(d, item("1.1", OK_SOURCE, deps="1.2") + item("1.2", "design:§Token storage", deps="1.1"))
    errors, _, _ = run_check(d)
    assert has(errors, "Цикл")


def test_uncovered_requirement(tmp_path):
    d = make_change(tmp_path)
    write_subs(d, item("1.1", "design:§Token storage; tasks:1.1, 1.2"))
    errors, _, _ = run_check(d)
    assert has(errors, "не покрыто.*Session expiry")


# --- RENAMED и tasks без номера ---

RENAMED_SPEC = SPEC + "\n## RENAMED Requirements\n- FROM: `### Requirement: Old`\n- TO: `### Requirement: New`\n"


def test_renamed_requires_coverage(tmp_path):
    d = make_change(tmp_path, spec=RENAMED_SPEC)
    write_subs(d, item("1.1", OK_SOURCE))
    errors, _, _ = run_check(d)
    assert has(errors, "Переименование не отражено")


def test_renamed_no_work_in_matrix(tmp_path):
    d = make_change(tmp_path, spec=RENAMED_SPEC)
    p = write_subs(d, item("1.1", OK_SOURCE))
    p.write_text(p.read_text(encoding="utf-8") + "| auth | New | RENAMED | без работ |\n", encoding="utf-8")
    errors, warnings, _ = run_check(d)
    assert not has(errors, "Переименование")
    assert not has(warnings, "Нет в матрице")


def test_renamed_covered_by_ref(tmp_path):
    d = make_change(tmp_path, spec=RENAMED_SPEC)
    write_subs(d, item("1.1", OK_SOURCE + "; spec:auth/Requirement:New"))
    errors, _, _ = run_check(d)
    assert errors == []


def test_unnumbered_task_warning(tmp_path):
    d = make_change(tmp_path, tasks=TASKS + "- [ ] Без номера\n")
    write_subs(d, item("1.1", OK_SOURCE))
    _, warnings, _ = run_check(d)
    assert has(warnings, "без номера")


# --- базовые хэши ---

def test_baseline_detects_unconfirmed_edit(tmp_path):
    d = make_change(tmp_path)
    parsed = pc.parse_change(d)
    bp = pc.baseline_path(d)
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text(json.dumps({"files": pc.file_hashes(parsed)}), encoding="utf-8")
    try:
        (d / "design.md").write_text(DESIGN + "\nправка\n", encoding="utf-8")
        write_subs(d, item("1.1", OK_SOURCE))
        errors, _, _ = run_check(d)
        assert has(errors, "design.md изменён")
        write_subs(d, item("1.1", OK_SOURCE), edits="- design.md: добавлена строка (вопрос 1, D-1)")
        errors, warnings, _ = run_check(d)
        assert errors == []
        assert not has(warnings, "Базовые хэши")
    finally:
        bp.unlink()


# --- парсер ---

def test_scenario_step_variants_no_false_s2(tmp_path):
    spec = """## ADDED Requirements
### Requirement: A
t
#### Scenario: numbered
1. **WHEN** x
2. **THEN** y

#### Scenario: plain
WHEN x
THEN y

#### Scenario: russian
- **КОГДА** x
- **ТОГДА** y

#### Scenario: broken
- **GIVEN** x
"""
    d = make_change(tmp_path, spec=spec)
    parsed = pc.parse_change(d)
    s2 = [w for w in parsed["warnings"] if w["code"] == "S-2"]
    assert len(s2) == 1 and "broken" in s2[0]["msg"]


def test_breaking_detection():
    text = "## What Changes\n- Non-BREAKING refactor\n- **BREAKING** удалено поле\n"
    b = pc.parse_proposal(text)["breaking"]
    assert [x["line"] for x in b] == [3]


def test_open_questions_nested():
    text = "## Open Questions\n1. CSV или XLSX?\n   - уточнение\n2. Второй\n## Next\n- не вопрос\n"
    oq = pc.parse_design(text)["open_questions"]
    assert [q["text"] for q in oq] == ["CSV или XLSX? / уточнение", "Второй"]


# --- CLI и --root ---

def test_cli_root_from_other_cwd(tmp_path):
    root = tmp_path / "repo"
    d = make_change(root)
    write_subs(d, item("1.1", OK_SOURCE))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    scripts = SKILL / "scripts"
    r = subprocess.run([sys.executable, scripts / "parse_change.py", "--list", "--root", str(root)],
                       cwd=elsewhere, capture_output=True, text=True)
    assert r.stdout.split() == ["c1"]
    r = subprocess.run([sys.executable, scripts / "parse_change.py", "--list"],
                       cwd=elsewhere, capture_output=True, text=True)
    assert r.returncode == 2 and "--root" in r.stderr
    r = subprocess.run([sys.executable, scripts / "check_coverage.py", "c1", "--root", str(root)],
                       cwd=elsewhere, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


# --- пример 1 из examples.md остаётся валидным ---

def test_example_1_is_valid(tmp_path):
    text = (SKILL / "examples.md").read_text(encoding="utf-8")
    ex1 = text.split("## Пример 2")[0]
    blocks = dict(re.findall(r"`([^`\n]+)`\n```markdown\n(.*?)\n```", ex1, re.S))
    result = re.search(r"### Результат.*?```markdown\n(.*?)\n```", ex1, re.S).group(1)
    d = tmp_path / "openspec" / "changes" / "add-session-expiry"
    for name in ("proposal.md", "design.md", "tasks.md", "specs/auth/spec.md"):
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(blocks[name] + "\n", encoding="utf-8")
    (d / "subtasks.md").write_text(result + "\n", encoding="utf-8")
    errors, warnings, st = run_check(d)
    assert errors == []
    assert [w for w in warnings if "Базовые хэши" not in w] == []
    assert st["subtasks"] <= 10 and st["sizes"]["S"] == 0
