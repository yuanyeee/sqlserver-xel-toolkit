from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QFileDialog,
)

from .settings import load_config, save_config, push_recent


class WorkspaceSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Workspace を選択")
        self.resize(560, 360)

        self.selected: str | None = None

        cfg = load_config()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Workspace ルートを選択してください。\n最近使ったWorkspaceから選べます。"))

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.list)

        for p in cfg.recent_workspaces:
            if p and Path(p).exists():
                self.list.addItem(QListWidgetItem(p))

        btns = QHBoxLayout()

        self.btn_browse = QPushButton("参照…")
        self.btn_browse.clicked.connect(self.browse)
        btns.addWidget(self.btn_browse)

        btns.addStretch(1)

        self.btn_ok = QPushButton("OK")
        self.btn_ok.clicked.connect(self.ok)
        btns.addWidget(self.btn_ok)

        self.btn_cancel = QPushButton("キャンセル")
        self.btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_cancel)

        layout.addLayout(btns)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose workspace directory")
        if not d:
            return
        self.selected = d
        self.accept()

    def ok(self):
        item = self.list.currentItem()
        if not item:
            QMessageBox.information(self, "Workspace", "Workspace を選択してください")
            return
        self.selected = item.text()
        self.accept()

    @staticmethod
    def select_workspace(parent=None) -> str | None:
        dlg = WorkspaceSelectDialog(parent)
        if dlg.exec() != QDialog.Accepted or not dlg.selected:
            return None

        ws = dlg.selected
        cfg = load_config()
        cfg.recent_workspaces = push_recent(cfg.recent_workspaces, ws)
        save_config(cfg)
        return ws

    @staticmethod
    def last_workspace() -> str | None:
        cfg = load_config()
        if cfg.recent_workspaces:
            p = cfg.recent_workspaces[0]
            if p and Path(p).exists():
                return p
        return None
