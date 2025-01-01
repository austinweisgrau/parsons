import sqlite3
from typing import Optional
from collections.abc import Iterator
from parsons.utilities import files
from parsons.etl.table import Table
import pickle
import petl
from parsons.databases.database_connector import DatabaseConnector
from contextlib import contextmanager

import logging

# Max number of rows that we query at a time, so we can avoid loading huge
# data sets into memory.
# 100k rows per batch at ~1k bytes each = ~100MB per batch.
QUERY_BATCH_SIZE = 100000

logger = logging.getLogger(__name__)


class Sqlite(DatabaseConnector):
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        except sqlite3.Error:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def cursor(self, connection) -> Iterator[sqlite3.Cursor]:
        cur = connection.cursor()

        try:
            yield cur
        finally:
            cur.close()

    def query(self, sql: str, parameters: Optional[list] = None) -> Optional[Table]:
        with self.connection() as connection:
            return self.query_with_connection(sql, connection, parameters=parameters)

    def query_with_connection(self, sql, connection, parameters=None, commit=True):
        """
        Execute a query against the database, with an existing connection. Useful for batching
        queries together. Will return ``None`` if the query returns zero rows.

        `Args:`
            sql: str
                A valid SQL statement
            connection: obj
                A connection object obtained from ``redshift.connection()``
            parameters: list
                A list of python variables to be converted into SQL values in your query
            commit: boolean
                Whether to commit the transaction immediately. If ``False`` the transaction will
                be committed when the connection goes out of scope and is closed (or you can
                commit manually with ``connection.commit()``).

        `Returns:`
            Parsons Table
                See :ref:`parsons-table` for output options.
        """

        with self.cursor(connection) as cursor:
            logger.debug(f"SQL Query: {sql}")
            cursor.execute(sql, parameters)

            if commit:
                connection.commit()

            # Fetch the data in batches, and "pickle" the rows to a temp file.
            # (We pickle rather than writing to, say, a CSV, so that we maintain
            # all the type information for each field.)

            temp_file = files.create_temp_file()

            with open(temp_file, "wb") as f:
                # Grab the header
                header = [i[0] for i in cursor.description]
                pickle.dump(header, f)

                while True:
                    batch = cursor.fetchmany(QUERY_BATCH_SIZE)
                    if not batch:
                        break

                    logger.debug(f"Fetched {len(batch)} rows.")
                    for row in batch:
                        pickle.dump(list(row), f)

            # Load a Table from the file
            final_tbl = Table(petl.frompickle(temp_file))

            logger.debug(f"Query returned {final_tbl.num_rows} rows.")
            return final_tbl

    def copy(
        self,
        tbl: Table,
        table_name: str,
        if_exists: str = "fail",
        strict_length: bool = False,
    ):
        """
        Copy a :ref:`parsons-table` to Postgres.

        `Args:`
            tbl: parsons.Table
                A Parsons table object
            table_name: str
                The destination schema and table (e.g. ``my_schema.my_table``)
            if_exists: str
                If the table already exists, either ``fail``, ``append``, ``drop``
                or ``truncate`` the table.
            strict_length: bool
                If the database table needs to be created, strict_length determines whether
                the created table's column sizes will be sized to exactly fit the current data,
                or if their size will be rounded up to account for future values being larger
                then the current dataset. Defaults to ``False``.
        """

        with self.connection() as connection:
            # Auto-generate table
            if self._create_table_precheck(connection, table_name, if_exists):
                # Create the table
                # To Do: Pass in the advanced configuration parameters.
                sql = self.create_statement(tbl, table_name, strict_length=strict_length)

                self.query_with_connection(sql, connection, commit=False)
                logger.info(f"{table_name} created.")

            sql = f"""COPY "{table_name}" ("{'","'.join(tbl.columns)}") FROM STDIN CSV HEADER;"""

            with self.cursor(connection) as cursor:
                cursor.copy_expert(sql, open(tbl.to_csv(), "r"))
                logger.info(f"{tbl.num_rows} rows copied to {table_name}.")

    def _create_table_precheck(self, connection, table_name, if_exists):
        """
        Helper to determine what to do when you need a table that may already exist.

        `Args:`
            connection: obj
                A connection object obtained from ``redshift.connection()``
            table_name: str
                The table to check
            if_exists: str
                If the table already exists, either ``fail``, ``append``, ``drop``,
                or ``truncate`` the table.
        `Returns:`
            bool
                True if the table needs to be created, False otherwise.
        """

        if if_exists not in ["fail", "truncate", "append", "drop"]:
            raise ValueError("Invalid value for `if_exists` argument")

        # If the table exists, evaluate the if_exists argument for next steps.
        if self.table_exists_with_connection(table_name, connection):
            if if_exists == "fail":
                raise ValueError("Table already exists.")

            if if_exists == "truncate":
                truncate_sql = f"TRUNCATE TABLE {table_name};"
                logger.info(f"Truncating {table_name}.")
                self.query_with_connection(truncate_sql, connection, commit=False)

            if if_exists == "drop":
                logger.info(f"Dropping {table_name}.")
                drop_sql = f"DROP TABLE {table_name};"
                self.query_with_connection(drop_sql, connection, commit=False)
                return True

            return False

        else:
            return True

    def table_exists(self, table_name: str, view: bool = True) -> bool:
        """
        Check if a table or view exists in the database.

        `Args:`
            table_name: str
                The table name and schema (e.g. ``myschema.mytable``).
            view: boolean
                Check to see if a view exists by the same name. Defaults to ``True``.

        `Returns:`
            boolean
                ``True`` if the table exists and ``False`` if it does not.
        """
        with self.connection() as connection:
            return self.table_exists_with_connection(table_name, connection, view)

    def table_exists_with_connection(self, table_name, connection, view=True):
        # Extract the table and schema from this. If no schema is detected then
        # will default to the public schema.
        try:
            schema, table = table_name.split(".", 1)
        except ValueError:
            schema, table = "public", table_name

        with self.cursor(connection) as cursor:
            # Check in pg tables for the table
            sql = f"""select count(*) from pg_tables where schemaname='{schema}' and
                     tablename='{table}';"""

            cursor.execute(sql)
            result = cursor.fetchone()[0]

            # Check in the pg_views if it is a view
            if view:
                sql = f"""select count(*) from pg_views where schemaname='{schema}' and
                         viewname='{table}';"""
                cursor.execute(sql)
                result += cursor.fetchone()[0]

        # If in either, return boolean
        if result >= 1:
            return True
        else:
            return False
