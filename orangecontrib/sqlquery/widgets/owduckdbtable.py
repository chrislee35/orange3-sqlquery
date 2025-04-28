from Orange.widgets import widget, settings
from Orange.widgets.widget import Input, Output
from Orange.widgets.utils import filedialogs
from Orange.data.pandas_compat import table_from_frame, table_to_frame
from AnyQt.QtWidgets import QFileDialog, QComboBox, QPushButton, QVBoxLayout, QWidget
import duckdb
import pandas as pd
import Orange.data

class OWDuckDBTable(widget.OWWidget):
    name = "DuckDB Table"
    description = "Select a table from a DuckDB database file."
    icon = "icons/DuckDB_logo.svg"
    priority = 10
    want_control_area = False

    class Outputs:
        data = Output("Data", Orange.data.Table)

    # Settings (remembered between sessions)
    db_file = settings.Setting("")
    selected_table = settings.Setting("")

    def __init__(self):
        super().__init__()
        ## Right side: SQL box + execute button
        self.right_widget = QWidget()
        right_layout = QVBoxLayout()
        self.tables_loaded = False

        # Widgets
        self.file_button = QPushButton("Select DuckDB File...")
        self.file_button.clicked.connect(self.select_file)

        right_layout.addWidget(self.file_button)

        self.table_combo = QComboBox()
        self.table_combo.currentIndexChanged.connect(self.select_table)

        right_layout.addWidget(self.table_combo)
        self.right_widget.setLayout(right_layout)
        self.mainArea.layout().addWidget(self.right_widget)

        self.conn = None  # DuckDB connection

        if self.db_file:
            self.load_tables()

    def select_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select DuckDB File", "", "DuckDB files (*.duckdb);;All files (*)"
        )
        if filename:
            self.db_file = filename
            self.load_tables()

    def load_tables(self):
        if self.conn:
            self.conn.close()

        try:
            self.conn = duckdb.connect(self.db_file)
            result = self.conn.execute("SHOW TABLES").fetchall()
            tables = [row[0] for row in result]
            self.table_combo.clear()
            self.table_combo.addItems(tables)

            if tables:
                self.tables_loaded = True
                # Auto-select first table if none selected
                if self.selected_table not in tables:
                    self.selected_table = tables[0]
                index = self.table_combo.findText(self.selected_table)
                self.table_combo.setCurrentIndex(index)
                self.select_table()
            else:
                self.Outputs.data.send(None)
        except Exception as e:
            self.error(f"Failed to open DuckDB: {e}")
            self.Outputs.data.send(None)

    def select_table(self):
        table_name = self.table_combo.currentText()
        if not table_name or not self.conn:
            self.Outputs.data.send(None)
            return

        try:
            df = self.conn.execute(f"SELECT * FROM \"{table_name}\"").fetchdf()
            table = table_from_frame(df)
            self.Outputs.data.send(table)
            if self.tables_loaded:
                self.selected_table = table_name
        except Exception as e:
            self.error(f"Failed to load table: {e}")
            self.Outputs.data.send(None)

    def closeEvent(self, event):
        if self.conn:
            self.conn.close()
        event.accept()
