# ----------------------------------------------------------------------
# plprofiler_data
#
#   Class handling all the profiler data.
# ----------------------------------------------------------------------

import psycopg2
import json

from plprofiler_report import plprofiler_report
from sql_split import sql_split

__all__ = ['plprofiler', ]

class plprofiler:
    def __init__(self):
        self.dbconn = None

    def connect(self, conninfo):
        # ----
        # Connect to the database and get the plprofiler schema name.
        # ----
        self.dbconn = psycopg2.connect(conninfo)
        self.profiler_namespace = self.get_profiler_namespace()

    def get_profiler_namespace(self):
        # ----
        # Find out the namespace of the plprofiler extension.
        # ----
        cur = self.dbconn.cursor()
        cur.execute("""
                SELECT N.nspname
                FROM pg_catalog.pg_extension E
                JOIN pg_catalog.pg_namespace N ON N.oid = E.extnamespace
                WHERE E.extname = 'plprofiler'
            """);
        row = cur.next()
        cur.close()
        self.dbconn.rollback()
        if row is None:
            raise Exception("plprofiler extension not found")
        result = row[0]
        return result

    def save_dataset_from_data(self, opt_name, config, overwrite = False):
        # ----
        # Aggregate the existing data found in pl_profiler_linestats_data
        # and pl_profiler_callgraph_data into a new entry in *_saved.
        # ----
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s;""", (self.profiler_namespace, ))
        cur.execute("""SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;""")

        try:
            if overwrite:
                cur.execute("""DELETE FROM pl_profiler_saved
                                WHERE s_name = %s""", (opt_name, ))
            cur.execute("""INSERT INTO pl_profiler_saved
                                (s_name, s_options)
                            VALUES (%s, %s)""",
                        (opt_name, json.dumps(config)))
        except psycopg2.IntegrityError as err:
            self.dbconn.rollback()
            raise err

        cur.execute("""INSERT INTO pl_profiler_saved_functions
                            (f_s_id, f_funcoid, f_schema, f_funcname,
                             f_funcresult, f_funcargs)
                        SELECT currval('pl_profiler_saved_s_id_seq') as s_id,
                               P.oid, N.nspname, P.proname,
                               pg_catalog.pg_get_function_result(P.oid) as func_result,
                               pg_catalog.pg_get_function_arguments(P.oid) as func_args
                        FROM pg_catalog.pg_proc P
                        JOIN pg_catalog.pg_namespace N on N.oid = P.pronamespace
                        WHERE P.oid IN (SELECT DISTINCT func_oid
                                            FROM pl_profiler_linestats_data)
                        GROUP BY s_id, p.oid, nspname, proname
                        ORDER BY s_id, p.oid, nspname, proname""")
        if cur.rowcount == 0:
            self.dbconn.rollback()
            raise Exception("No function data to save found")

        cur.execute("""INSERT INTO pl_profiler_saved_linestats
                            (l_s_id, l_funcoid,
                             l_line_number, l_source, l_exec_count,
                             l_total_time, l_longest_time)
                        SELECT currval('pl_profiler_saved_s_id_seq') as s_id,
                               L.func_oid, L.line_number,
                               coalesce(S.source, '-- SOURCE NOT FOUND'),
                               sum(L.exec_count), sum(L.total_time),
                               max(L.longest_time)
                        FROM pl_profiler_linestats_data L
                        LEFT JOIN pl_profiler_all_source S
                            ON S.func_oid = L.func_oid
                            AND S.line_number = L.line_number
                        GROUP BY s_id, L.func_oid, L.line_number, S.source
                        ORDER BY s_id, L.func_oid, L.line_number""")
        if cur.rowcount == 0:
            self.dbconn.rollback()
            raise Exception("No plprofiler data to save")

        cur.execute("""INSERT INTO pl_profiler_saved_callgraph
                            (c_s_id, c_stack, c_call_count, c_us_total,
                             c_us_children, c_us_self)
                        SELECT currval('pl_profiler_saved_s_id_seq') as s_id,
                               pl_profiler_get_stack(stack) as stack,
                               sum(call_count), sum(us_total),
                               sum(us_children), sum(us_self)
                        FROM pl_profiler_callgraph_data
                        GROUP BY s_id, stack
                        ORDER BY s_id, stack;""")

        cur.execute("""RESET search_path""")
        cur.close()
        self.dbconn.commit()

    def save_dataset_from_current(self, opt_name, config, overwrite = False):
        # ----
        # Aggregate the existing data found in pl_profiler_linestats_current
        # and pl_profiler_callgraph_current into a new entry in *_saved.
        # ----
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s;""", (self.profiler_namespace, ))
        cur.execute("""SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;""")

        try:
            if overwrite:
                cur.execute("""DELETE FROM pl_profiler_saved
                                WHERE s_name = %s""", (opt_name, ))
            cur.execute("""INSERT INTO pl_profiler_saved
                                (s_name, s_options)
                            VALUES (%s, %s)""",
                        (opt_name, json.dumps(config)))
        except psycopg2.IntegrityError as err:
            self.dbconn.rollback()
            raise err

        cur.execute("""INSERT INTO pl_profiler_saved_functions
                            (f_s_id, f_funcoid, f_schema, f_funcname,
                             f_funcresult, f_funcargs)
                        WITH FL AS (
                            SELECT DISTINCT func_oid
                            FROM pl_profiler_linestats(false)
                        )
                        SELECT currval('pl_profiler_saved_s_id_seq') as s_id,
                               P.oid, N.nspname, P.proname,
                               pg_catalog.pg_get_function_result(P.oid) as func_result,
                               pg_catalog.pg_get_function_arguments(P.oid) as func_args
                        FROM pg_catalog.pg_proc P
                        JOIN pg_catalog.pg_namespace N on N.oid = P.pronamespace
                        JOIN FL ON FL.func_oid = P.oid
                        GROUP BY s_id, p.oid, nspname, proname
                        ORDER BY s_id, p.oid, nspname, proname""")
        if cur.rowcount == 0:
            self.dbconn.rollback()
            raise Exception("No function data to save found")

        cur.execute("""INSERT INTO pl_profiler_saved_linestats
                            (l_s_id, l_funcoid,
                             l_line_number, l_source, l_exec_count,
                             l_total_time, l_longest_time)
                        SELECT currval('pl_profiler_saved_s_id_seq') as s_id,
                               L.func_oid, L.line_number,
                               coalesce(L.line, '-- SOURCE NOT FOUND'),
                               sum(L.exec_count), sum(L.total_time),
                               max(L.longest_time)
                        FROM pl_profiler_linestats_current L
                        GROUP BY s_id, L.func_oid, L.line_number, L.line
                        ORDER BY s_id, L.func_oid, L.line_number""")
        if cur.rowcount == 0:
            self.dbconn.rollback()
            raise Exception("No plprofiler data to save")

        cur.execute("""INSERT INTO pl_profiler_saved_callgraph
                            (c_s_id, c_stack, c_call_count, c_us_total,
                             c_us_children, c_us_self)
                        SELECT currval('pl_profiler_saved_s_id_seq') as s_id,
                               stack,
                               sum(call_count), sum(us_total),
                               sum(us_children), sum(us_self)
                        FROM pl_profiler_callgraph_current
                        GROUP BY s_id, stack
                        ORDER BY s_id, stack;""")

        cur.execute("""RESET search_path""")
        cur.close()
        self.dbconn.commit()

    def get_dataset_list(self):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""SELECT s_name, s_options
                        FROM pl_profiler_saved
                        ORDER BY s_name""")
        result = cur.fetchall()
        cur.execute("""RESET search_path""")
        cur.close()
        self.dbconn.rollback()
        return result

    def get_dataset_config(self, opt_name):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""SELECT s_options
                        FROM pl_profiler_saved
                        WHERE s_name = %s""", (opt_name, ))
        if cur.rowcount == 0:
            self.dbconn.rollback()
            raise Exception("No saved data with name '" + opt_name + "' found")
        row = cur.fetchone()
        config = json.loads(row[0])
        config['name'] = opt_name
        cur.execute("""RESET search_path""")
        cur.close()
        self.dbconn.rollback()

        return config

    def update_dataset_config(self, opt_name, new_name, config):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""UPDATE pl_profiler_saved
                        SET s_name = %s,
                            s_options = %s
                        WHERE s_name = %s""",
                    (new_name, json.dumps(config), opt_name))
        if cur.rowcount != 1:
            self.dbconn.rollback()
            raise Exception("Data set with name '" + opt_name +
                             "' no longer exists")
        else:
            cur.execute("""RESET search_path""")
            self.dbconn.commit()
        cur.close()

    def delete_dataset(self, opt_name):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""DELETE FROM pl_profiler_saved
                        WHERE s_name = %s""",
                    (opt_name, ))
        if cur.rowcount != 1:
            self.dbconn.rollback()
            raise Exception("Data set with name '" + opt_name +
                             "' does not exists")
        else:
            cur.execute("""RESET search_path""")
            self.dbconn.commit()
        cur.close()

    def get_saved_report_data(self, opt_name, opt_top, func_oids):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))

        # ----
        # Get the config of the saved dataset.
        # ----
        cur.execute("""SELECT s_options
                        FROM pl_profiler_saved
                        WHERE s_name = %s""", (opt_name, ))
        if cur.rowcount == 0:
            self.dbconn.rollback()
            raise Exception("No saved data with name '" + opt_name + "' found")
        row = cur.fetchone()
        config = json.loads(row[0])
        config['name'] = opt_name

        # ----
        # If not specified, find the top N functions by self time.
        # ----
        found_more_funcs = False
        if func_oids is None or len(func_oids) == 0:
            func_oids_by_user = False
            func_oids = []
            cur.execute("""SELECT regexp_replace(c_stack[array_upper(c_stack, 1)],
                                  E'.* oid=\\([0-9]*\\)$', E'\\\\1') as func_oid,
                                sum(c_us_self) as us_self
                            FROM pl_profiler_saved S
                            JOIN pl_profiler_saved_callgraph C
                                ON C.c_s_id = S.s_id
                            WHERE S.s_name = %s
                            GROUP BY func_oid
                            ORDER BY us_self DESC
                            LIMIT %s""", (opt_name, opt_top + 1, ))
            for row in cur:
                func_oids.append(int(row[0]))
            if len(func_oids) > opt_top:
                func_oids = func_oids[:-1]
                found_more_funcs = True
        else:
            func_oids_by_user = True
            func_oids = [int(x) for x in func_oids]

        # ----
        # Get an alphabetically sorted list of the selected functions.
        # ----
        cur.execute("""SELECT f_funcoid, f_schema, f_funcname
                        FROM pl_profiler_saved S
                        JOIN pl_profiler_saved_functions F
                            ON F.f_s_id = S.s_id
                        WHERE S.s_name = %s
                        AND F.f_funcoid IN (SELECT * FROM unnest(%s))
                        ORDER BY upper(f_schema), f_schema,
                                 upper(f_funcname), f_funcname""", (opt_name,
                                                                    func_oids, ))
        func_list = []
        for row in cur:
            func_list.append({
                    'funcoid':  str(row[0]),
                    'schema': str(row[1]),
                    'funcname': str(row[2]),
                })

        # ----
        # Build a list of function definitions in the order, specified
        # by the func_oids list. This is either the oids, requested by
        # the user or the oids determined above in descending order of
        # self_time.
        # ----
        func_defs = []
        for func_oid in func_oids:
            # ----
            # First get the function definition and overall stats.
            # ----
            cur.execute("""WITH SELF AS (
                            SELECT regexp_replace(c_stack[array_upper(c_stack, 1)],
                                      E'.* oid=\\([0-9]*\\)$', E'\\\\1') as func_oid,
                                    sum(c_us_self) as us_self
                                FROM pl_profiler_saved S
                                JOIN pl_profiler_saved_callgraph C
                                    ON C.c_s_id = S.s_id
                                WHERE S.s_name = %s
                                GROUP BY func_oid)
                        SELECT l_funcoid, f_schema, f_funcname,
                            f_funcresult, f_funcargs,
                            coalesce(l_total_time, 0) as total_time,
                            coalesce(SELF.us_self, 0) as self_time
                            FROM pl_profiler_saved S
                            LEFT JOIN pl_profiler_saved_linestats L ON l_s_id = s_id
                            JOIN pl_profiler_saved_functions F ON f_funcoid = l_funcoid
                            LEFT JOIN SELF ON SELF.func_oid::bigint = f_funcoid
                            WHERE S.s_name = %s
                              AND L.l_funcoid = %s
                              AND L.l_line_number = 0""",
                        (opt_name, opt_name, func_oid, ))
            row = cur.fetchone()
            if row is None:
                raise Exception("function with Oid %d not found\n" %func_oid)

            # ----
            # With that we can start the definition.
            # ----
            func_def = {
                    'funcoid': func_oid,
                    'schema': row[1],
                    'funcname': row[2],
                    'funcresult': row[3],
                    'funcargs': row[4],
                    'total_time': int(row[5]),
                    'self_time': int(row[6]),
                    'source': [],
                }

            # ----
            # Add all the source code lines to that.
            # ----
            cur.execute("""SELECT l_line_number, l_source, l_exec_count,
                            l_total_time, l_longest_time
                            FROM pl_profiler_saved S
                            JOIN pl_profiler_saved_linestats L ON L.l_s_id = S.s_id
                            WHERE S.s_name = %s
                              AND L.l_funcoid = %s
                            ORDER BY l_s_id, l_funcoid, l_line_number""",
                            (opt_name, func_oid, ))
            for row in cur:
                func_def['source'].append({
                        'line_number': int(row[0]),
                        'source': row[1],
                        'exec_count': int(row[2]),
                        'total_time': int(row[3]),
                        'longest_time': int(row[4]),
                    })

            # ----
            # Add this function to the list of function definitions.
            # ----
            func_defs.append(func_def)

        # ----
        # Get the callgraph data for building the flamegraph.
        # ----
        cur.execute("""SELECT array_to_string(c_stack, ';'), c_us_self
                        FROM pl_profiler_saved S
                        JOIN pl_profiler_saved_callgraph C ON C.c_s_id = S.s_id
                        WHERE S.s_name = %s""",
                    (opt_name, ))
        flamedata = ""
        for row in cur:
            flamedata += str(row[0]) + " " + str(row[1]) + "\n"

        # ----
        # That is it. Reset things and return the report data.
        # ----
        cur.execute("""RESET search_path""");
        self.dbconn.rollback()

        return {
                'config': config,
                'func_list': func_list,
                'func_defs': func_defs,
                'flamedata': flamedata,
                'func_oids_by_user': func_oids_by_user,
                'found_more_funcs': found_more_funcs,
            }

    def get_current_report_data(self, opt_name, opt_top, func_oids):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))

        # ----
        # Create a default config.
        # ----
        config = {
                'name': opt_name,
                'title': 'PL Profiler Report for %s' %(opt_name, ),
                'tabstop': '8',
                'svg_width': '1200',
                'table_width': '80%',
                'desc': '<h1>PL Profiler Report for %s</h1>' %(opt_name, ),
            }

        # ----
        # If not specified, find the top N functions by self time.
        # ----
        found_more_funcs = False
        if func_oids is None or len(func_oids) == 0:
            func_oids_by_user = False
            func_oids = []
            cur.execute("""SELECT regexp_replace(stack[array_upper(stack, 1)],
                                  E'.* oid=\\([0-9]*\\)$', E'\\\\1') as func_oid,
                                sum(us_self) as us_self
                            FROM pl_profiler_callgraph_current C
                            GROUP BY func_oid
                            ORDER BY us_self DESC
                            LIMIT %s""", (opt_top + 1, ))
            for row in cur:
                func_oids.append(int(row[0]))
            if len(func_oids) > opt_top:
                func_oids = func_oids[:-1]
                found_more_funcs = True
        else:
            func_oids_by_user = True
            func_oids = [int(x) for x in func_oids]

        # ----
        # Get an alphabetically sorted list of the selected functions.
        # ----
        cur.execute("""SELECT func_oid, func_schema, func_name
                        FROM pl_profiler_all_functions F
                        WHERE F.func_oid IN (SELECT * FROM unnest(%s))
                        ORDER BY upper(func_schema), func_schema,
                                 upper(func_name), func_name""", (func_oids, ))

        func_list = []
        for row in cur:
            func_list.append({
                    'funcoid':  str(row[0]),
                    'schema': str(row[1]),
                    'funcname': str(row[2]),
                })

        # ----
        # The view for linestats is extremely inefficient. We select
        # all of it once and cache it in a hash table.
        # ----
        linestats = {}
        cur.execute("""SELECT func_oid, line_number, exec_count,
                            total_time, longest_time, line
                        FROM pl_profiler_linestats_current
                        ORDER BY func_oid, line_number""")
        for row in cur:
            if row[0] not in linestats:
                linestats[row[0]] = []
            linestats[row[0]].append(row)

        # ----
        # Build a list of function definitions in the order, specified
        # by the func_oids list. This is either the oids, requested by
        # the user or the oids determined above in descending order of
        # self_time.
        # ----
        func_defs = []
        for func_oid in func_oids:
            # ----
            # First get the function definition and overall stats.
            # ----
            cur.execute("""WITH SELF AS (
                                SELECT regexp_replace(stack[array_upper(stack, 1)],
                                      E'.* oid=\\([0-9]*\\)$', E'\\\\1') as func_oid,
                                    sum(us_self) as us_self
                                FROM pl_profiler_callgraph_current C
                                GROUP BY func_oid)
                        SELECT F.func_oid, F.func_schema, F.func_name,
                            F.func_result, F.func_arguments,
                            coalesce(SELF.us_self, 0) as self_time
                            FROM pl_profiler_all_functions F
                            LEFT JOIN SELF ON SELF.func_oid::bigint = F.func_oid
                            WHERE F.func_oid = %s""",
                        (func_oid, ))
            row = cur.fetchone()
            if row is None:
                raise Exception("function with Oid %d not found\n" %func_oid)

            # ----
            # With that we can start the definition.
            # ----
            func_def = {
                    'funcoid': func_oid,
                    'schema': row[1],
                    'funcname': row[2],
                    'funcresult': row[3],
                    'funcargs': row[4],
                    'total_time': linestats[func_oid][0][3],
                    'self_time': int(row[5]),
                    'source': [],
                }

            # ----
            # Add all the source code lines to that.
            # ----
            for row in linestats[func_oid]:
                func_def['source'].append({
                        'line_number': int(row[1]),
                        'source': row[5],
                        'exec_count': int(row[2]),
                        'total_time': int(row[3]),
                        'longest_time': int(row[4]),
                    })

            # ----
            # Add this function to the list of function definitions.
            # ----
            func_defs.append(func_def)

        # ----
        # Get the callgraph data for building the flamegraph.
        # ----
        cur.execute("""SELECT array_to_string(stack, ';'), us_self
                        FROM pl_profiler_callgraph_current C""")
        flamedata = ""
        for row in cur:
            flamedata += str(row[0]) + " " + str(row[1]) + "\n"

        # ----
        # That is it. Reset things and return the report data.
        # ----
        cur.execute("""RESET search_path""");
        self.dbconn.rollback()

        return {
                'config': config,
                'func_list': func_list,
                'func_defs': func_defs,
                'flamedata': flamedata,
                'func_oids_by_user': func_oids_by_user,
                'found_more_funcs': found_more_funcs,
            }

    def enable(self):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""SELECT pl_profiler_enable(true)""")
        cur.execute("""RESET search_path""")
        self.dbconn.commit()
        cur.close()

    def disable(self):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""SELECT pl_profiler_enable(false)""")
        cur.execute("""RESET search_path""")
        self.dbconn.commit()
        cur.close()

    def reset_current(self):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""SELECT pl_profiler_reset()""")
        cur.execute("""RESET search_path""")
        self.dbconn.commit()
        cur.close()

    def reset_data(self):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""DELETE FROM pl_profiler_linestats_data""")
        cur.execute("""DELETE FROM pl_profiler_callgraph_data""")
        cur.execute("""RESET search_path""")
        self.dbconn.commit()
        cur.close()

    def save_current_stats(self):
        cur = self.dbconn.cursor()
        cur.execute("""SET search_path TO %s""", (self.profiler_namespace, ))
        cur.execute("""SELECT pl_profiler_save_stats()""")
        cur.execute("""RESET search_path""")
        self.dbconn.commit()
        cur.close()

    def execute_sql(self, sql):
        try:
            self.dbconn.autocommit = True
            cur = self.dbconn.cursor()
            for query in sql_split(sql).get_statements():
                print query
                cur.execute(query)
                print cur.statusmessage
            self.dbconn.autocommit = False
        except Exception as err:
            self.dbconn.autocommit = False
            raise err
        self.dbconn.rollback()

    def report(self, report_data, output_fd):
        report = plprofiler_report()
        report.generate(report_data, output_fd)