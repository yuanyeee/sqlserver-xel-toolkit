from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QRadioButton,
    QButtonGroup,
)

from .delete_utils import is_within, trash_paths


@dataclass
class DeleteRunChoice:
    delete_db: bool
    delete_files: bool


class DeleteRunDialog(QDialog):
    def __init__(self, workspace_root: str, run_out_dirs: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Run を削除")
        self.resize(560, 280)

        self.workspace_root = workspace_root
        self.run_out_dirs = [d for d in run_out_dirs if d]
        self.choice: Optional[DeleteRunChoice] = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Run（runs ディレクトリ）を削除、および/またはデータベースから削除します。\ninputMD は影響を受けません。"))
        layout.addWidget(QLabel(f"選択数: {len(self.run_out_dirs)}"))
        if self.run_out_dirs:
            layout.addWidget(QLabel("出力ディレクトリ（一部）:"))
            for d in self.run_out_dirs[:4]:
                layout.addWidget(QLabel(d))
            if len(self.run_out_dirs) > 4:
                layout.addWidget(QLabel(f"... ({len(self.run_out_dirs) - 4} more)"))

        self.rb_trash = QRadioButton("DB から削除し、runs ディレクトリをゴミ箱へ移動（推奨）")
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
        # safety
        for d in self.run_out_dirs:
            if d and os.path.exists(d) and not is_within(self.workspace_root, d):
                QMessageBox.critical(self, "安全チェック", f"workspace 外のパスは削除できません: {d}")
                return

        if self.rb_trash.isChecked():
            self.choice = DeleteRunChoice(delete_db=True, delete_files=True)
        else:
            self.choice = DeleteRunChoice(delete_db=True, delete_files=False)

        self.accept()
