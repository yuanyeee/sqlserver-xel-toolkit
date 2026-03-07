from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QLineEdit,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class RangeItem:
    start: str  # "YYYY-mm-dd HH:MM" (JST)
    end: str

    def duration_label(self) -> str:
        """Return human-readable duration string."""
        try:
            fmt = "%Y-%m-%d %H:%M"
            s = datetime.strptime(self.start, fmt)
            e = datetime.strptime(self.end, fmt)
            mins = int((e - s).total_seconds() // 60)
            if mins < 0:
                return ""
            h, m = divmod(mins, 60)
            return f"{h}h{m:02d}m" if m else f"{h}h"
        except Exception:
            return ""


def load_ranges(path: str) -> List[RangeItem]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f) or {}
        items = obj.get("ranges", []) if isinstance(obj, dict) else []
        out: List[RangeItem] = []
        for it in items:
            if isinstance(it, dict) and it.get("start") and it.get("end"):
                out.append(RangeItem(start=str(it["start"]).strip(), end=str(it["end"]).strip()))
        out.sort(key=lambda x: x.start)
        return out
    except Exception:
        return []


def save_ranges(path: str, items: List[RangeItem]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ranges": [{"start": i.start, "end": i.end} for i in items]}, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _time_choices() -> List[str]:
    """Generate HH:MM choices in 30-min steps."""
    choices = []
    for h in range(0, 24):
        choices.append(f"{h:02d}:00")
        choices.append(f"{h:02d}:30")
    return choices


_HIGHLIGHT_FORMAT = QTextCharFormat()
_HIGHLIGHT_FORMAT.setBackground(QColor("#b3d9ff"))
_HIGHLIGHT_FORMAT.setForeground(QColor("#003366"))

_REGISTERED_FORMAT = QTextCharFormat()
_REGISTERED_FORMAT.setBackground(QColor("#c8f7c5"))
_REGISTERED_FORMAT.setForeground(QColor("#1a5c1a"))

_CLEAR_FORMAT = QTextCharFormat()


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class TimeRangeWidget(QWidget):
    """Embeddable time-range editor.

    Left pane  : Calendar (click to toggle date selection; green = has ranges)
    Right pane : Registered ranges list + template + manual input + save
    """

    ranges_saved = Signal()

    def __init__(self, ranges_path: Optional[str], parent=None, *, show_dialog_buttons: bool = False):
        super().__init__(parent)
        self.ranges_path: Optional[str] = ranges_path
        self.items: List[RangeItem] = load_ranges(ranges_path) if ranges_path else []
        self.selected_dates: List[QDate] = []   # dates staged for template application
        self._show_dialog_buttons = show_dialog_buttons

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ============================================================
        # Left pane: calendar
        # ============================================================
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 4, 0)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setMinimumWidth(280)
        self.calendar.clicked.connect(self._on_calendar_clicked)
        left.addWidget(self.calendar)

        # Quick-date buttons
        quick_row = QHBoxLayout()
        for label, fn in [
            ("今日",  self._add_today),
            ("昨日",  self._add_yesterday),
            ("今週",  self._add_this_week),
            ("先週",  self._add_last_week),
        ]:
            b = QPushButton(label)
            b.setMaximumWidth(52)
            b.clicked.connect(fn)
            quick_row.addWidget(b)
        quick_row.addStretch()
        left.addLayout(quick_row)

        # Selected-date count + clear
        sel_row = QHBoxLayout()
        self._sel_label = QLabel("0 日選択中")
        btn_clear_sel = QPushButton("選択クリア")
        btn_clear_sel.clicked.connect(self._clear_selected_dates)
        sel_row.addWidget(self._sel_label)
        sel_row.addStretch()
        sel_row.addWidget(btn_clear_sel)
        left.addLayout(sel_row)

        splitter.addWidget(left_w)

        # ============================================================
        # Right pane: range list + controls
        # ============================================================
        right_w = QWidget()
        right = QVBoxLayout(right_w)
        right.setContentsMargins(4, 0, 0, 0)

        right.addWidget(QLabel("登録済み時間帯:"))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right.addWidget(self.list)

        # ---- Template section ----
        tpl_label = QLabel("テンプレート（選択した日に追加）:")
        right.addWidget(tpl_label)

        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel("開始"))
        self._start_combo = QComboBox()
        self._start_combo.setEditable(True)
        self._start_combo.addItems(_time_choices())
        self._start_combo.setCurrentText("08:00")
        self._start_combo.setMinimumWidth(72)
        tpl_row.addWidget(self._start_combo)

        tpl_row.addWidget(QLabel("終了"))
        self._end_combo = QComboBox()
        self._end_combo.setEditable(True)
        self._end_combo.addItems(_time_choices())
        self._end_combo.setCurrentText("17:00")
        self._end_combo.setMinimumWidth(72)
        tpl_row.addWidget(self._end_combo)

        self._btn_apply = QPushButton("選択した日に追加")
        self._btn_apply.clicked.connect(self._apply_template)
        tpl_row.addWidget(self._btn_apply)
        tpl_row.addStretch()
        right.addLayout(tpl_row)

        # Quick-template presets
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("プリセット:"))
        for label, sh, eh in [
            ("08-17時", "08:00", "17:00"),
            ("07-21時", "07:00", "21:00"),
            ("09-18時", "09:00", "18:00"),
            ("00-24時", "00:00", "23:59"),
        ]:
            b = QPushButton(label)
            b.setMaximumWidth(64)
            start_h, end_h = sh, eh
            b.clicked.connect(lambda _, s=start_h, e=end_h: self._apply_preset(s, e))
            preset_row.addWidget(b)
        preset_row.addStretch()
        right.addLayout(preset_row)

        # ---- Manual input ----
        manual_label = QLabel("手動入力:")
        right.addWidget(manual_label)
        form = QFormLayout()
        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("2026-01-15 09:00")
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("2026-01-15 18:00")
        self.end_edit.returnPressed.connect(self.add_item)
        form.addRow("開始:", self.start_edit)
        form.addRow("終了:", self.end_edit)
        btn_manual_add = QPushButton("追加")
        btn_manual_add.clicked.connect(self.add_item)
        form.addRow("", btn_manual_add)
        right.addLayout(form)

        # ---- Bottom buttons ----
        bot = QHBoxLayout()
        self.btn_del = QPushButton("選択削除")
        self.btn_del.clicked.connect(self.delete_selected)
        self.btn_clear = QPushButton("全クリア")
        self.btn_clear.clicked.connect(self.clear_all)
        bot.addWidget(self.btn_del)
        bot.addWidget(self.btn_clear)
        bot.addStretch()

        if show_dialog_buttons:
            self.btn_ok = QPushButton("OK")
            self.btn_cancel = QPushButton("キャンセル")
            bot.addWidget(self.btn_ok)
            bot.addWidget(self.btn_cancel)
        else:
            self.btn_save = QPushButton("💾 保存")
            self.btn_save.clicked.connect(self.save)
            bot.addWidget(self.btn_save)

        right.addLayout(bot)
        splitter.addWidget(right_w)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 5)

        self._refresh_list()
        self._refresh_calendar_marks()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_ranges_path(self, path: Optional[str]) -> None:
        self.ranges_path = path
        self.items = load_ranges(path) if path else []
        self.selected_dates = []
        self._refresh_list()
        self._refresh_calendar_marks()
        self._refresh_sel_label()

    def save(self) -> bool:
        if not self.ranges_path:
            QMessageBox.warning(self, "保存", "ワークスペースを先に開いてください")
            return False
        try:
            save_ranges(self.ranges_path, self.items)
            self.ranges_saved.emit()
            return True
        except Exception as e:
            QMessageBox.critical(self, "保存失敗", str(e))
            return False

    # ------------------------------------------------------------------
    # Calendar interaction
    # ------------------------------------------------------------------

    def _on_calendar_clicked(self, date: QDate) -> None:
        """Toggle clicked date in/out of selection."""
        if date in self.selected_dates:
            self.selected_dates.remove(date)
        else:
            self.selected_dates.append(date)
            self.selected_dates.sort(key=lambda d: d.toJulianDay())
        self._refresh_calendar_marks()
        self._refresh_sel_label()

    def _refresh_calendar_marks(self) -> None:
        """Color-code calendar: selected=blue, has-registered-range=green."""
        # Clear all custom formats
        self.calendar.setDateTextFormat(QDate(), _CLEAR_FORMAT)

        # Mark dates with registered ranges (green)
        registered = set()
        for it in self.items:
            try:
                d = QDate.fromString(it.start[:10], "yyyy-MM-dd")
                if d.isValid():
                    registered.add(d)
            except Exception:
                pass
        for d in registered:
            self.calendar.setDateTextFormat(d, _REGISTERED_FORMAT)

        # Mark selected dates (blue — overrides green)
        for d in self.selected_dates:
            self.calendar.setDateTextFormat(d, _HIGHLIGHT_FORMAT)

    def _refresh_sel_label(self) -> None:
        n = len(self.selected_dates)
        self._sel_label.setText(f"{n} 日選択中")
        self._btn_apply.setText(
            f"選択した {n} 日に追加" if n else "選択した日に追加"
        )

    def _clear_selected_dates(self) -> None:
        self.selected_dates = []
        self._refresh_calendar_marks()
        self._refresh_sel_label()

    # ------------------------------------------------------------------
    # Quick-date helpers
    # ------------------------------------------------------------------

    def _add_today(self) -> None:
        self._toggle_date(QDate.currentDate())

    def _add_yesterday(self) -> None:
        self._toggle_date(QDate.currentDate().addDays(-1))

    def _add_this_week(self) -> None:
        today = QDate.currentDate()
        dow = today.dayOfWeek()  # 1=Mon … 7=Sun
        for offset in range(1 - dow, 8 - dow):
            self._toggle_date(today.addDays(offset))

    def _add_last_week(self) -> None:
        today = QDate.currentDate()
        dow = today.dayOfWeek()
        for offset in range(1 - dow - 7, 8 - dow - 7):
            self._toggle_date(today.addDays(offset))

    def _toggle_date(self, date: QDate) -> None:
        if date in self.selected_dates:
            self.selected_dates.remove(date)
        else:
            self.selected_dates.append(date)
            self.selected_dates.sort(key=lambda d: d.toJulianDay())
        self._refresh_calendar_marks()
        self._refresh_sel_label()

    # ------------------------------------------------------------------
    # Template / add
    # ------------------------------------------------------------------

    def _apply_preset(self, start_hm: str, end_hm: str) -> None:
        """Set combo values and apply to selected dates immediately."""
        self._start_combo.setCurrentText(start_hm)
        self._end_combo.setCurrentText(end_hm)
        self._apply_template()

    def _apply_template(self) -> None:
        if not self.selected_dates:
            QMessageBox.warning(self, "日付未選択",
                                "カレンダーで日付をクリックして選択してください")
            return
        start_hm = self._start_combo.currentText().strip()
        end_hm = self._end_combo.currentText().strip()
        if not start_hm or not end_hm:
            QMessageBox.warning(self, "時刻未入力", "開始・終了時刻を入力してください")
            return

        added = 0
        for d in self.selected_dates:
            date_str = d.toString("yyyy-MM-dd")
            s = f"{date_str} {start_hm}"
            e = f"{date_str} {end_hm}"
            if s >= e:
                continue
            # Avoid exact duplicate
            if any(it.start == s and it.end == e for it in self.items):
                continue
            self.items.append(RangeItem(start=s, end=e))
            added += 1

        if added:
            self.items.sort(key=lambda x: x.start)
            self._refresh_list()
            self._refresh_calendar_marks()

    def add_item(self) -> None:
        s = self.start_edit.text().strip()
        e = self.end_edit.text().strip()
        if not s or not e:
            QMessageBox.warning(self, "入力", "開始/終了を入力してください")
            return
        if s >= e:
            QMessageBox.warning(self, "入力", "開始は終了より前にしてください")
            return
        if any(it.start == s and it.end == e for it in self.items):
            self.start_edit.clear()
            self.end_edit.clear()
            return
        self.items.append(RangeItem(start=s, end=e))
        self.items.sort(key=lambda x: x.start)
        self.start_edit.clear()
        self.end_edit.clear()
        self._refresh_list()
        self._refresh_calendar_marks()

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _refresh_list(self) -> None:
        self.list.clear()
        for it in self.items:
            dur = it.duration_label()
            label = f"{it.start}  〜  {it.end}"
            if dur:
                label += f"  ({dur})"
            item = QListWidgetItem(label)
            self.list.addItem(item)

    def delete_selected(self) -> None:
        rows = sorted({i.row() for i in self.list.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            if 0 <= r < len(self.items):
                self.items.pop(r)
        self._refresh_list()
        self._refresh_calendar_marks()

    def clear_all(self) -> None:
        if not self.items:
            return
        if QMessageBox.question(self, "確認", f"{len(self.items)} 件すべて削除しますか？") != QMessageBox.StandardButton.Yes:
            return
        self.items = []
        self._refresh_list()
        self._refresh_calendar_marks()

    # ------------------------------------------------------------------
    # Legacy compat (called from old code paths)
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Alias kept for backward compatibility."""
        self._refresh_list()
        self._refresh_calendar_marks()
        self._refresh_sel_label()


# ---------------------------------------------------------------------------
# Dialog wrapper
# ---------------------------------------------------------------------------

class TimeRangeDialog(QDialog):
    """Thin dialog wrapper around TimeRangeWidget."""

    def __init__(self, ranges_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("出力時間範囲")
        self.resize(900, 560)

        layout = QVBoxLayout(self)
        self._widget = TimeRangeWidget(ranges_path, self, show_dialog_buttons=True)
        self._widget.btn_ok.clicked.connect(self._ok)
        self._widget.btn_cancel.clicked.connect(self.reject)
        layout.addWidget(self._widget)

    def _ok(self) -> None:
        if self._widget.save():
            self.accept()
