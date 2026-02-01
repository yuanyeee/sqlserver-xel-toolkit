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
    def __init__(self, workspace_root: str, run_out_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("删除 Run")
        self.resize(520, 240)

        self.workspace_root = workspace_root
        self.run_out_dir = run_out_dir
        self.choice: Optional[DeleteRunChoice] = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("删除 Run（runs 目录）与/或从数据库移除。inputMD 不会被影响。"))
        layout.addWidget(QLabel(f"Run 输出目录: {run_out_dir}"))

        self.rb_trash = QRadioButton("从 DB 移除，并将 runs 目录移动到回收站（推荐）")
        self.rb_trash.setChecked(True)
        self.rb_db_only = QRadioButton("仅从 DB 移除（不动文件）")

        layout.addWidget(self.rb_trash)
        layout.addWidget(self.rb_db_only)

        btns = QHBoxLayout()
        ok = QPushButton("执行")
        ok.clicked.connect(self.on_ok)
        btns.addWidget(ok)

        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addStretch(1)
        layout.addLayout(btns)

    def on_ok(self):
        # safety
        if self.run_out_dir and os.path.exists(self.run_out_dir):
            if not is_within(self.workspace_root, self.run_out_dir):
                QMessageBox.critical(self, "安全检查", f"拒绝删除 workspace 外路径: {self.run_out_dir}")
                return

        if self.rb_trash.isChecked():
            self.choice = DeleteRunChoice(delete_db=True, delete_files=True)
        else:
            self.choice = DeleteRunChoice(delete_db=True, delete_files=False)

        self.accept()
