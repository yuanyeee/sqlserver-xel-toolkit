from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from .delete_utils import is_within


@dataclass
class DeleteReportChoice:
    delete_db: bool
    delete_files: bool


class DeleteReportDialog(QDialog):
    def __init__(self, workspace_root: str, title: str, paths: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Report を削除")
        self.resize(560, 260)

        self.workspace_root = workspace_root
        self.title = title
        self.paths = [p for p in paths if p]
        self.choice: Optional[DeleteReportChoice] = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("選択した Report を削除します。"))
        layout.addWidget(QLabel(f"Report: {title}"))

        if self.paths:
            layout.addWidget(QLabel("関連ファイル:"))
            for p in self.paths[:4]:
                layout.addWidget(QLabel(p))
            if len(self.paths) > 4:
                layout.addWidget(QLabel(f"... ({len(self.paths) - 4} more)"))

        self.rb_trash = QRadioButton("DB から削除し、関連ファイルをゴミ箱へ移動（推奨）")
        self.rb_trash.setChecked(True)
        self.rb_db_only = QRadioButton("DB からのみ削除（ファイルはそのまま）")

        layout.addWidget(self.rb_trash)
        layout.addWidget(self.rb_db_only)

        btns = QHBoxLayout()
        ok = QPushButton("実行")
        ok.clicked.connect(self.on_ok)
        btns.addWidget(ok)

        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addStretch(1)
        layout.addLayout(btns)

    def on_ok(self):
        for p in self.paths:
            if p and os.path.exists(p) and not is_within(self.workspace_root, p):
                QMessageBox.critical(self, "安全チェック", f"workspace 外のパスは削除できません: {p}")
                return

        if self.rb_trash.isChecked():
            self.choice = DeleteReportChoice(delete_db=True, delete_files=True)
        else:
            self.choice = DeleteReportChoice(delete_db=True, delete_files=False)

        self.accept()
