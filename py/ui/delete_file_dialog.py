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
    def __init__(self, workspace_root: str, file_out_dirs: list[str], file_names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("删除 File")
        self.resize(560, 280)

        self.workspace_root = workspace_root
        self.file_out_dirs = [d for d in file_out_dirs if d]
        self.file_names = [n for n in file_names if n]
        self.choice: Optional[DeleteFileChoice] = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("删除选中的 File（run 内输出目录）与/或从数据库移除。\ninputMD 不会被影响。"))
        layout.addWidget(QLabel(f"选中数量: {len(self.file_out_dirs)}"))
        if self.file_names:
            layout.addWidget(QLabel("File(部分):"))
            for n in self.file_names[:4]:
                layout.addWidget(QLabel(n))
            if len(self.file_names) > 4:
                layout.addWidget(QLabel(f"... ({len(self.file_names) - 4} more)"))

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
        for d in self.file_out_dirs:
            if d and os.path.exists(d) and not is_within(self.workspace_root, d):
                QMessageBox.critical(self, "安全检查", f"拒绝删除 workspace 外路径: {d}")
                return

        if self.rb_trash.isChecked():
            self.choice = DeleteFileChoice(delete_db=True, delete_files=True)
        else:
            self.choice = DeleteFileChoice(delete_db=True, delete_files=False)

        self.accept()
