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
    QRadioButton,
    QVBoxLayout,
)

from .delete_utils import is_within


@dataclass
class DeleteFileChoice:
    delete_db: bool
    delete_files: bool


class DeleteFileDialog(QDialog):
    def __init__(self, workspace_root: str, file_out_dir: str, file_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("删除 File")
        self.resize(560, 260)

        self.workspace_root = workspace_root
        self.file_out_dir = file_out_dir
        self.file_name = file_name
        self.choice: Optional[DeleteFileChoice] = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("删除当前选中的 File（run 内输出目录）与/或从数据库移除。\ninputMD 不会被影响。"))
        layout.addWidget(QLabel(f"File: {file_name}"))
        layout.addWidget(QLabel(f"输出目录: {file_out_dir}"))

        self.rb_trash = QRadioButton("从 DB 移除，并将该 File 输出目录移动到回收站（推荐）")
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
        if self.file_out_dir and os.path.exists(self.file_out_dir):
            if not is_within(self.workspace_root, self.file_out_dir):
                QMessageBox.critical(self, "安全检查", f"拒绝删除 workspace 外路径: {self.file_out_dir}")
                return

        if self.rb_trash.isChecked():
            self.choice = DeleteFileChoice(delete_db=True, delete_files=True)
        else:
            self.choice = DeleteFileChoice(delete_db=True, delete_files=False)

        self.accept()
