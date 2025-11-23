# Orange3-SQLQuery

This allows use DuckDB to perform SQL queries on multiple input tables from duckdb, json, json line (jsonl) files, and anything else that DuckDB supports.

## Installation

Within the Add-ons installer, click on "Add more..." and type in orange3-sqlquery

## Provided Widgets

This addon provides 4 widgets

![Widget Line Up](img/sqlquery-widgets-lineup.png)

#### DuckDB Table

![DuckDB Table Widget](img/duckdb-table.png)

#### JSON Loader

![JSON Loader Widget](img/json-loader.png)

#### JSON Lines Loader (and description)

![JSON Lines Loader Widget](img/json-lines-loader.png)

#### SQL Query Widget

![SQL Query Widget](img/sql-query-widget.png)

### Example Workflow

![Example Orange3 workflow using SQLQuery to join 2 tables, one with Iranian rainfall per city and another with the coordinates of Iranian cities, the SQL statement also limits to cities whose latitude is less than 30.](img/sqlquery-example1.png?raw=true)

* [The Rainfall of Iranian Cities dataset](https://www.kaggle.com/datasets/mohammadrahdanmofrad/average-monthly-precipitation-of-iranian-cities)
* [Iranian City Locations](https://github.com/chrislee35/orange3-sqlquery/blob/main/datasets/iranian_city_locations.csv?raw=true)

