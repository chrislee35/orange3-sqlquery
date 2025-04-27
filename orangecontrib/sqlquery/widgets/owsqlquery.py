from Orange.widgets import widget, gui, settings
from Orange.data.pandas_compat import table_from_frame, table_to_frame
from Orange.data import Table, Domain, DiscreteVariable, ContinuousVariable, StringVariable
import pandas as pd
import duckdb
from AnyQt.QtWidgets import QTextEdit, QLineEdit, QPushButton, QLabel, QVBoxLayout, QHBoxLayout, QWidget

class SQLQueryWidget(widget.OWWidget):
    name = "SQL Query Widget"
    description = "Perform SQL queries on multiple input tables"
    icon = "icons/sqlquery.svg"  # You can create a simple SQL icon later
    priority = 10
    want_control_area = True

    class Inputs:
        Input1 = widget.Input("Input1", Table)
        Input2 = widget.Input("Input2", Table)
        Input3 = widget.Input("Input3", Table)
        Input4 = widget.Input("Input4", Table)
        Input5 = widget.Input("Input5", Table)
        Input6 = widget.Input("Input6", Table)
        Input7 = widget.Input("Input7", Table)
        Input8 = widget.Input("Input8", Table)
        Input9 = widget.Input("Input9", Table)
        Input10 = widget.Input("Input10", Table)

    class Outputs:
        Output = widget.Output("Query Result", Table)

    table_names = settings.Setting([""] * 10)
    sql_query = settings.Setting("")

    def __init__(self):
        super().__init__()

        self.tables = [None] * 10

        self._init_gui()

    def _init_gui(self):
        layout = gui.hBox(self.controlArea)

        ## Left side: input ports status + variable names
        self.left_widget = QWidget()
        left_layout = QVBoxLayout()
        self.input_status_labels = []
        self.input_name_edits = []

        for idx in range(10):
            row = QHBoxLayout()
            label = QLabel(f"Input {idx+1}: Not connected")
            edit = QLineEdit()
            edit.setPlaceholderText(f"Variable name for Input {idx+1}")

            # Restore saved names
            edit.setText(self.table_names[idx])

            # When edited, update the stored name
            edit.textChanged.connect(lambda text, index=idx: self.update_table_name(index, text))

            self.input_status_labels.append(label)
            self.input_name_edits.append(edit)
            row.addWidget(label)
            row.addWidget(edit)
            left_layout.addLayout(row)

        self.left_widget.setLayout(left_layout)
        layout.layout().addWidget(self.left_widget)

        ## Right side: SQL box + execute button
        self.right_widget = QWidget()
        right_layout = QVBoxLayout()

        self.sql_textbox = QTextEdit()
        self.sql_textbox.setPlaceholderText("Enter your SQL query here...")
        self.sql_textbox.setText(self.sql_query)

        right_layout.addWidget(self.sql_textbox)

        self.execute_button = QPushButton("Execute Query")
        self.execute_button.clicked.connect(self.execute_query)
        right_layout.addWidget(self.execute_button)

        self.right_widget.setLayout(right_layout)
        self.mainArea.layout().addWidget(self.right_widget)

    def update_table_name(self, idx, text):
        self.table_names[idx] = text

    # -- Handlers for each input port --

    @Inputs.Input1
    def set_input1(self, table):
        self.tables[0] = table
        self.update_input_status(0)

    @Inputs.Input2
    def set_input2(self, table):
        self.tables[1] = table
        self.update_input_status(1)

    @Inputs.Input3
    def set_input3(self, table):
        self.tables[2] = table
        self.update_input_status(2)

    @Inputs.Input4
    def set_input4(self, table):
        self.tables[3] = table
        self.update_input_status(3)

    @Inputs.Input5
    def set_input5(self, table):
        self.tables[4] = table
        self.update_input_status(4)

    @Inputs.Input6
    def set_input6(self, table):
        self.tables[5] = table
        self.update_input_status(5)

    @Inputs.Input7
    def set_input7(self, table):
        self.tables[6] = table
        self.update_input_status(6)

    @Inputs.Input8
    def set_input8(self, table):
        self.tables[7] = table
        self.update_input_status(7)

    @Inputs.Input9
    def set_input9(self, table):
        self.tables[8] = table
        self.update_input_status(8)

    @Inputs.Input10
    def set_input10(self, table):
        self.tables[9] = table
        self.update_input_status(9)

    def update_input_status(self, idx):
        if self.tables[idx] is not None:
            self.input_status_labels[idx].setText(f"Input {idx+1}: Connected")
        else:
            self.input_status_labels[idx].setText(f"Input {idx+1}: Not connected")

        if self.sql_query and len([x for x in self.tables if x is not None]) == len([x for x in self.table_names if x != ""]):
            self.execute_query()

    def execute_query(self):
        con = duckdb.connect(database=':memory:')
        try:
            # Register each connected table
            for idx, table in enumerate(self.tables):
                if table is not None:
                    var_name = self.input_name_edits[idx].text().strip()
                    if not var_name:
                        var_name = f"input{idx+1}"
                    df = table_to_frame(table, include_metas=True)
                    con.register(var_name, df)

            # Execute user query
            query = self.sql_textbox.toPlainText()
            self.sql_query = query
            result_df = con.execute(query).fetchdf()

            # Convert back to Orange Table
            out_table = table_from_frame(result_df)

            self.Outputs.Output.send(out_table)

        except Exception as e:
            self.error(f"Query failed: {e}")

        finally:
            con.close()


