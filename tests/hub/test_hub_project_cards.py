from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QScrollArea

from ui_project_list import ProjectListPane


def test_long_project_identity_does_not_push_actions_outside_the_viewport(tmp_path):
    app = QApplication.instance() or QApplication([])
    record = SimpleNamespace(
        project_id="long", name="Long project name " * 20, created_at="",
        path=str(tmp_path / ("deep-" * 30)),
    )
    pane = ProjectListPane(SimpleNamespace(all_projects=lambda: [record]))
    pane.resize(700, 300)
    pane.show()
    app.processEvents()
    try:
        scroll = pane.findChild(QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0
        button = pane.project_cards["long"]._actions_button
        right = button.mapTo(scroll.viewport(), button.rect().topRight())
        assert scroll.viewport().rect().contains(right)
    finally:
        pane.close()


def test_refresh_hides_retired_widgets_before_opening_a_modal(tmp_path):
    app = QApplication.instance() or QApplication([])
    records = []
    pane = ProjectListPane(SimpleNamespace(all_projects=lambda: records))
    pane.show()
    app.processEvents()
    empty_state = pane.card_layout.itemAt(0).widget()
    try:
        assert empty_state.isVisible()
        records.append(SimpleNamespace(project_id="new", name="New project",
                                       created_at="", path=str(tmp_path)))
        pane.refresh()
        # The caller may enter a modal loop before deferred deletion runs.
        assert not empty_state.isVisible()
    finally:
        pane.close()
