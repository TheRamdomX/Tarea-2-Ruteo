"""Genera main.sql (raiz del repo): SQL autocontenido para cargar la base en una BD vacia.

Las definiciones de tablas se leen del catalogo de la BD (tipos, defaults, PK, indices GIST),
las funciones y la vista se exportan con pg_get_functiondef / pg_get_viewdef para que coincidan
exactamente con lo que se uso al calcular.

Uso: python scripts/exportar_main_sql.py   (tambien: main.py dump)
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from attr import conectar  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALIDA = os.path.join(RAIZ, "main.sql")
TABLAS = ["edges", "edges_vertices_pgr", "nodos_clave", "sismo", "ruptura", "modelo_param", "componentes"]
FUNCIONES = ["param_valor", "pga_youngs", "p_falla_enlace"]
VISTA = "v_probabilidad_falla"
LOTE = 500


def columnas(cur, tabla):
    """Columnas (nombre, tipo, not null, default) en orden de definicion."""
    cur.execute("""
        SELECT a.attname,
               format_type(a.atttypid, a.atttypmod),
               a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
    """, (tabla,))
    return cur.fetchall()


def restricciones_pk(cur, tabla):
    cur.execute("""
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        WHERE c.conrelid = %s::regclass AND c.contype = 'p'
    """, (tabla,))
    return [r[0] for r in cur.fetchall()]


def ddl_tabla(cur, tabla):
    cols = columnas(cur, tabla)
    lineas = []
    for nombre, tipo, notnull, default in cols:
        # Secuencias (serial) no se exportan: los ids se insertan explicitamente.
        if default and default.startswith("nextval("):
            default = None
        parte = '    "%s" %s' % (nombre, tipo)
        if default:
            parte += " DEFAULT %s" % default
        if notnull:
            parte += " NOT NULL"
        lineas.append(parte)
    for pk in restricciones_pk(cur, tabla):
        lineas.append("    " + pk)
    sql = "DROP TABLE IF EXISTS %s CASCADE;\nCREATE TABLE %s (\n%s\n);\n" % (
        tabla, tabla, ",\n".join(lineas))
    return sql, cols


def literal(cur, valor):
    return cur.mogrify("%s", (valor,)).decode("utf-8")


def inserts_tabla(cur, tabla, cols):
    nombres = [c[0] for c in cols]
    seleccion = []
    for nombre, tipo, _, _ in cols:
        if tipo.startswith("geometry"):
            seleccion.append('ST_AsEWKT("%s") AS "%s"' % (nombre, nombre))
        else:
            seleccion.append('"%s"' % nombre)
    cur.execute("SELECT %s FROM %s" % (", ".join(seleccion), tabla))
    filas = cur.fetchall()
    if not filas:
        return "", 0
    lista_cols = ", ".join('"%s"' % n for n in nombres)
    partes = []
    for i in range(0, len(filas), LOTE):
        lote = filas[i:i + LOTE]
        valores = []
        for fila in lote:
            trozos = []
            for valor, (_, tipo, _, _) in zip(fila, cols):
                if valor is None:
                    trozos.append("NULL")
                elif tipo.startswith("geometry"):
                    trozos.append("%s::geometry" % literal(cur, valor))
                else:
                    trozos.append(literal(cur, valor))
            valores.append("(" + ", ".join(trozos) + ")")
        partes.append("INSERT INTO %s (%s) VALUES\n%s;\n" % (tabla, lista_cols, ",\n".join(valores)))
    return "\n".join(partes), len(filas)


def indices_gist(cur, tabla, cols):
    sql = ""
    for nombre, tipo, _, _ in cols:
        if tipo.startswith("geometry"):
            sql += 'CREATE INDEX IF NOT EXISTS "%s_%s_gist" ON %s USING GIST ("%s");\n' % (
                tabla, nombre, tabla, nombre)
    return sql


def funciones(cur):
    sql = ""
    for nombre in FUNCIONES:
        cur.execute("""
            SELECT pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = %s
        """, (nombre,))
        for (definicion,) in cur.fetchall():
            sql += definicion.rstrip().rstrip(";") + ";\n\n"
    return sql


def vista(cur):
    cur.execute("SELECT pg_get_viewdef(%s::regclass, true)", (VISTA,))
    definicion = cur.fetchone()[0]
    return "CREATE OR REPLACE VIEW %s AS\n%s\n" % (VISTA, definicion.rstrip().rstrip(";") + ";")


def main():
    conn = conectar()
    cur = conn.cursor()
    partes = []
    partes.append(
        "-- main.sql generado por scripts/exportar_main_sql.py el %s\n"
        "-- Carga autocontenida de la red de resiliencia (fibra, sismo, modelo de falla).\n"
        "-- Requiere PostGIS y pgRouting disponibles. Ejecutar sobre una base de datos vacia.\n\n"
        % datetime.now().isoformat(timespec="seconds"))
    partes.append("CREATE EXTENSION IF NOT EXISTS postgis;\nCREATE EXTENSION IF NOT EXISTS pgrouting;\n\n")
    datos = []
    for tabla in TABLAS:
        ddl, cols = ddl_tabla(cur, tabla)
        partes.append("-- Tabla %s\n%s\n" % (tabla, ddl))
        partes.append(indices_gist(cur, tabla, cols) + "\n")
        ins, n = inserts_tabla(cur, tabla, cols)
        datos.append("-- %s: %d filas\n%s" % (tabla, n, ins))
        print("tabla %s: %d filas" % (tabla, n))
    partes.append("-- Datos\n" + "\n".join(datos) + "\n")
    partes.append("-- Funciones del modelo de falla\n" + funciones(cur))
    partes.append("-- Vista\n" + vista(cur) + "\n")
    partes.append(
        "-- Comprobaciones (descomentar para verificar la carga)\n"
        "-- SELECT count(*) FROM edges;\n"
        "-- SELECT count(*) FROM edges_vertices_pgr;\n"
        "-- SELECT count(*) FROM nodos_clave WHERE vertex_id IS NULL;\n"
        "-- SELECT count(*) FILTER (WHERE p_falla > 0.5), avg(p_falla) FROM edges;\n")
    cur.close()
    conn.close()
    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write("".join(partes))
    print("main.sql escrito en %s" % SALIDA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
