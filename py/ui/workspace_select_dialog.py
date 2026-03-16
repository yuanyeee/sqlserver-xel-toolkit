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

import shutil

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

        self.btn_delete = QPushButton("削除…")
        self.btn_delete.clicked.connect(self.delete_workspace)
        btns.addWidget(self.btn_delete)

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

    def delete_workspace(self):
        item = self.list.currentItem()
        if not item:
            QMessageBox.information(self, "Workspace 削除", "削除する Workspace を選択してください")
            return
        ws_path = item.text()

        # フォルダごと削除するか確認
        reply = QMessageBox.question(
            self,
            "Workspace 削除",
            f"以下の Workspace をリストから削除しますか？\n\n{ws_path}\n\n"
            "「はい」: リストから削除のみ\n"
            "「No」: キャンセル",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # リストから削除
        cfg = load_config()
        cfg.recent_workspaces = [p for p in cfg.recent_workspaces if p != ws_path]
        save_config(cfg)
        row = self.list.row(item)
        self.list.takeItem(row)

        # フォルダ自体も削除するか確認
        if Path(ws_path).exists():
            reply2 = QMessageBox.question(
                self,
                "フォルダの削除",
                f"Workspace フォルダ自体も削除しますか？\n\n{ws_path}\n\n※ この操作は元に戻せません。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply2 == QMessageBox.StandardButton.Yes:
                try:
                    shutil.rmtree(ws_path)
                    QMessageBox.information(self, "Workspace 削除", f"フォルダを削除しました:\n{ws_path}")
                except Exception as e:
                    QMessageBox.critical(self, "削除エラー", f"フォルダの削除に失敗しました:\n{e}")

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
