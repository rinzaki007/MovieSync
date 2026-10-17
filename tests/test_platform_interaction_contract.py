"""Regression contracts for decoupled and race-safe frontend interactions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_task_center_uses_storage_target_registry_not_quark_configuration():
    script = (ROOT / "static" / "js" / "task-center.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "tasks.html").read_text(encoding="utf-8")

    assert "/api/storage-targets" in script
    assert "/api/storage-targets/' + encodeURIComponent(targetId) + '/destinations" in script
    assert "/api/cards/quark/config" not in script
    assert "storage_target_id:" in script
    assert 'id="task-storage-target"' in template
    assert "默认转存位置不可用" in script
    assert "系统不会自动改存到其他网盘" in script


def test_async_frontend_lists_ignore_stale_responses():
    main_script = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    task_script = (ROOT / "static" / "js" / "task-center.js").read_text(encoding="utf-8")
    resource_script = (ROOT / "static" / "js" / "resource-select.js").read_text(encoding="utf-8")
    admin_template = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")

    assert "let movieViewRequestSeq = 0;" in main_script
    assert "requestId !== movieViewRequestSeq" in main_script
    assert "let taskLoadSequence = 0;" in task_script
    assert "requestId !== taskLoadSequence" in task_script
    assert "requestId !== candidateSearchSequence" in task_script
    assert "let destinationRequestSeq=0;" in resource_script
    assert "requestId!==destinationRequestSeq" in resource_script
    assert "let cardsLoadSequence = 0;" in admin_template
    assert "requestId !== cardsLoadSequence" in admin_template
    assert "let genericCardRequestSequence = 0;" in admin_template
    assert "requestId !== genericCardRequestSequence" in admin_template


def test_smart_follow_search_renders_paginated_candidates_progressively():
    script = (ROOT / "static" / "js" / "task-center.js").read_text(encoding="utf-8")

    assert "const TASK_CANDIDATE_SCAN_LIMIT = 10;" in script
    assert "scan_limit: TASK_CANDIDATE_SCAN_LIMIT" in script
    assert "candidate_pages" in script
    assert "taskCandidateKeys" in script
    assert "data-task-candidate-more" in script
    assert "searchTaskCandidates(true)" in script
    assert "还有更多资源可查询" in script
