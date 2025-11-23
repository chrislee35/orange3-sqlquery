import os
import sys
import json
import pprint
import pandas as pd
from AnyQt.QtCore import Qt, QSize, QThread, pyqtSignal
from AnyQt.QtWidgets import (
    QVBoxLayout, QPushButton, QListWidget, QListWidgetItem,
    QWidget, QFileDialog, QLabel, QHBoxLayout, QTextEdit, QFrame, QSizePolicy
)
from AnyQt.QtGui import QColor, QPainter, QBrush, QFont

from Orange.widgets.widget import OWWidget, Input, Output
from Orange.widgets.settings import Setting
from Orange.widgets.utils.concurrent import ConcurrentWidgetMixin, Task
from Orange.widgets import gui
from Orange.data import Table
from Orange.data.pandas_compat import table_from_frame

from orangecontrib.sqlquery.jsonl_schema_profiler import SchemaProfiler

# ----------------------------------------------------------------------
# Small inline frequency indicator widget
# ----------------------------------------------------------------------

class FrequencyBar(QWidget):
    """
    Draws a small horizontal bar colored from the 'Carrot gradient':
    light yellow → orange → red depending on presence percentage.
    """
    def __init__(self, fraction, parent=None):
        super().__init__(parent)
        self.fraction = fraction  # 0.0 – 1.0
        self.setFixedHeight(12)
        self.setFixedWidth(30)

    def sizeHint(self):
        return QSize(30, 12)

    def _gradient_color(self, f):
        # Carrot gradient: #FFE5A0 → #F6A03E → #C4470C
        c1 = QColor("#FFE5A0")
        c2 = QColor("#F6A03E")
        c3 = QColor("#C4470C")
        if f < 0.5:
            t = f * 2
            r = c1.red() + t * (c2.red() - c1.red())
            g = c1.green() + t * (c2.green() - c1.green())
            b = c1.blue() + t * (c2.blue() - c1.blue())
        else:
            t = (f - 0.5) * 2
            r = c2.red() + t * (c3.red() - c2.red())
            g = c2.green() + t * (c3.green() - c2.green())
            b = c2.blue() + t * (c3.blue() - c2.blue())
        return QColor(int(r), int(g), int(b))

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()

        # Background
        painter.fillRect(rect, QColor("#F0F0F0"))

        # Foreground bar
        if self.fraction > 0:
            width = int(rect.width() * self.fraction)
            color = self._gradient_color(self.fraction)
            painter.fillRect(rect.x(), rect.y(), width, rect.height(), QBrush(color))

        painter.end()


# ----------------------------------------------------------------------
# Expandable field widget
# ----------------------------------------------------------------------

class FieldWidget(QWidget):
    """
    Collapsible field summary + detail display.
    """
    def __init__(self, field_path, summary, details_callback, parent=None):
        super().__init__(parent)
        self.field_path = field_path
        self.summary = summary
        self.details_callback = details_callback
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        # Summary row
        self.summary_row = QWidget()
        h = QHBoxLayout(self.summary_row)
        h.setContentsMargins(0, 0, 0, 0)

        f = self.summary.get("presence_fraction", 0.0)
        self.freq_bar = FrequencyBar(f)

        field_label = QLabel(self.field_path)
        field_label.setFont(QFont("Arial", 10, QFont.Bold))

        type_label = QLabel(self._short_type_desc(self.summary))
        type_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        h.addWidget(self.freq_bar)
        h.addWidget(field_label)
        h.addWidget(type_label)

        self.summary_row.mousePressEvent = self.toggle
        layout.addWidget(self.summary_row)

    def _short_type_desc(self, s):
        types = s.get("types", {})
        # choose most frequent type
        if not types:
            return "unknown"
        t = max(types.items(), key=lambda x: x[1])[0]
        return t

    def toggle(self, event):
        self.details_callback(self.field_path, self.summary)
        

class DescriptionWorker(QThread):
    progress = pyqtSignal(int)
    result = pyqtSignal(dict)
    data = pyqtSignal(Table)

    def __init__(self, filename):
        super().__init__()
        self.filename = filename
        self._cancel = False

    def run(self):
        profiler = SchemaProfiler()
        prog = 0
        # Here we loop over its yielded progress states.
        for progress in profiler.process_file(self.filename):
            curr_prog = int(progress * 100)
            if curr_prog > prog:
                prog = curr_prog
                self.progress.emit(prog)
            if self._cancel:
                break

        self.result.emit(profiler.report())
        table = table_from_frame(profiler.dataframe)
        profiler.dataframe = None
        self.data.emit(table)

# ----------------------------------------------------------------------
# OWJSONDescription Widget
# ----------------------------------------------------------------------

class OWJSONDescription(OWWidget):
    name = "JSON Lines Loader"
    description = "Loadsa JSONL structure and provides a summary report"
    icon = "icons/jsonl.svg"
    want_main_area = True
    want_control_area = False

    json_path = Setting("")

    class Outputs:
        data = Output("Table", Table)

    def __init__(self):
        OWWidget.__init__(self)
        self.worker = None

        # ---------------------
        # Main area
        # ---------------------
        self.control_widget = QWidget()
        layout = QHBoxLayout()
        self.control_widget.setLayout(layout)
        gui.button(self.control_widget, self, "Choose JSON-Lines File…",
                   callback=self.choose_file)

        self.info_label = gui.label(self.control_widget, self, "No file loaded.")
        self.info_label.setWordWrap(True)

        self.mainArea.layout().addWidget(self.control_widget)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.NoSelection)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.setAttribute(Qt.WA_NoMousePropagation, True)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.mainArea.layout().addWidget(self.list_widget)
        self.mainArea.layout().addWidget(self.details)

        self.report = None

        if self.json_path:
            self._load_json(self.json_path)

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def choose_file(self):
        fn, _ = QFileDialog.getOpenFileName(
            self,
            "Select JSON/JSONL File",
            "",
            "JSON Files (*.json *.jsonl)"
        )
        if not fn:
            return

        self.json_path = fn
        self.info_label.setText(f"Loading: {os.path.basename(fn)}")

        # run in background
        self._load_json(fn)

    def stop_worker(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
            self.progressBarInit()

    def _load_json(self, filename):
        if not filename:
            return
        self.stop_worker()

        self.progressBarInit()
        self.worker = DescriptionWorker(filename)
        self.worker.result.connect(self.on_done)
        self.worker.data.connect(self.table_callback)
        self.worker.progress.connect(self.progressBarSet)
        self.worker.start()


    # ------------------------------------------------------------------
    # After task finishes
    # ------------------------------------------------------------------

    def on_done(self, result):
        """Called when worker completes successfully."""
        self.report = result
        self.info_label.setText("Loaded.")
        self.populate_list()

    def table_callback(self, result):
        self.Outputs.data.send(result)
        self.progressBarFinished()

    def on_exception(self, task, exc):
        self.error(str(exc))

    def on_cancel(self, task):
        self.info("Cancelled.")

    def details_classback(self, field_path, summary):
        report = f"""Field: {field_path}
Records with field: {summary['present_count']} ({summary['presence_fraction']*100:0.1f}%)
Records without:    {summary['missing_count']}
Value Types:
"""
        for t in sorted(summary['types']):
            report += f"  {t} ({summary['types'][t]} rows)\n"
        
        if 'number' in summary['types']:
            report += f"""Number type statistics
  Count: {summary['number']['count']}
  Range: {summary['number']['min']} to {summary['number']['max']}
  Mean:  {summary['number']['mean']}
  Var:   {summary['number']['variance']}
"""

        if 'string' in summary['types']:
            report += f"""String type statistics
  Unique:     {summary['string']['unique_tracked']}
  Enum:       {summary['string']['enum_like']}
  Top Values: {summary['string']['top_values']}
"""
        if 'array' in summary['types']:
            report += f"""Array type statistics
  Count: {summary['array']['count']}
  Min length: {summary['array']['min']}
  Max length: {summary['array']['max']}
  Mean length: {summary['array']['mean']}
  Var. length: {summary['array']['variance']}
"""

        self.details.setText(report)

    # ------------------------------------------------------------------
    # Populate UI
    # ------------------------------------------------------------------

    def populate_list(self):
        self.list_widget.clear()
        if not self.report:
            return

        fields = self.report.get("fields", {})

        for path, summary in fields.items():
            w = FieldWidget(path, summary, details_callback=self.details_classback)
            item = QListWidgetItem()
            item.setSizeHint(w.sizeHint())

            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, w)

if __name__ == "__main__":
    from Orange.widgets.utils.widgetpreview import WidgetPreview
    WidgetPreview(OWJSONDescription).run()