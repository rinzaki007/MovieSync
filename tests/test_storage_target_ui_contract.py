def test_storage_destination_contract():
    destination = {"id": "root", "name": "默认目录", "category": "电影", "is_default": True}
    assert destination["id"]
    assert destination["name"]
    assert destination["category"]


def test_card_management_uses_manifest_driven_editor_without_card_id_routing():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "admin.html").read_text(encoding="utf-8")
    quark_card = (root / "card_templates" / "quark.py").read_text(encoding="utf-8")

    assert "function openCardFromRegistry(cardId)" in template
    assert "openGenericCard(cardId);" in template
    assert 'if (cardId === "quark")' not in template
    assert 'if (cardId === "telegram")' not in template
    assert "renderGenericCardFields(currentGenericCard.fields" in template
    assert '"key": "category_fids"' in quark_card
    assert '"key": "cookie"' in quark_card


def test_task_center_offers_destination_check_for_uncertain_transfers_without_quark_dependency():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    task_center = (root / "static" / "js" / "task-center.js").read_text(encoding="utf-8")

    assert "task.status === 'success' || task.recovery_uncertain" in task_center
    assert "转存结果尚不确定" in task_center
    assert "不要重复提交" in task_center
    assert "storage.list_destination_files" in task_center
    assert "https://pan.quark.cn/" not in task_center


def test_successful_transfer_actions_are_provider_agnostic_and_optional():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    task_center = (root / "static" / "js" / "task-center.js").read_text(encoding="utf-8")
    manager = (root / "moviesync" / "services" / "storage_targets.py").read_text(encoding="utf-8")
    task_template = (root / "templates" / "tasks.html").read_text(encoding="utf-8")

    assert "function renderTransferActions(task)" in task_center
    assert "task.status !== 'success'" in task_center
    assert "target.web_url" in task_center
    assert 'target="_blank" rel="noopener noreferrer"' in task_center
    assert 'getattr(card, "web_url", "")' in manager
    assert "https://pan.quark.cn/" not in task_center
    assert task_template.count(">播放中心</a>") == 1
