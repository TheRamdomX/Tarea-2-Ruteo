"""Calibracion de la tolerancia de pgr_createTopology sobre los segmentos ya noded (fibra_noded).

Para cada tolerancia se reconstruye una copia de trabajo cal_edges, se calcula la topologia
y se miden vertices, componentes, tamano relativo de la componente mayor y extremos muertos.
Los resultados quedan en la tabla calibracion_tolerancia; las tablas cal_ se eliminan al final.

Uso: python scripts/calibrar_tolerancia.py   (tambien: main.py calibrar)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from attr import conectar  # noqa: E402

TOLERANCIAS = [0.1, 0.5, 1, 2, 5, 10, 25, 50]

SQL_CREAR_TABLA = """
CREATE TABLE IF NOT EXISTS calibracion_tolerancia (
    tolerancia_m double precision PRIMARY KEY,
    n_vertices bigint,
    n_componentes bigint,
    pct_mayor_componente double precision,
    n_extremos_muertos bigint,
    ts timestamptz DEFAULT now()
)
"""


def calibrar(conn):
    cur = conn.cursor()
    cur.execute(SQL_CREAR_TABLA)
    cur.execute("DELETE FROM calibracion_tolerancia")
    conn.commit()
    filas = []
    for tol in TOLERANCIAS:
        cur.execute("DROP TABLE IF EXISTS cal_edges, cal_edges_vertices_pgr CASCADE")
        cur.execute("""
            CREATE TABLE cal_edges (
                id serial PRIMARY KEY,
                geom geometry(LineString,32719),
                largo_m double precision,
                source integer,
                target integer
            )
        """)
        cur.execute("""
            INSERT INTO cal_edges (geom, largo_m)
            SELECT geom, ST_Length(geom) FROM fibra_noded WHERE ST_Length(geom) > 0
        """)
        cur.execute("SELECT pgr_createTopology('cal_edges', %s, 'geom', 'id', 'source', 'target', clean := true)",
                    (tol,))
        cur.execute("SELECT count(*) FROM cal_edges_vertices_pgr")
        n_vertices = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM cal_edges_vertices_pgr WHERE cnt = 1")
        n_muertos = cur.fetchone()[0]
        cur.execute("""
            SELECT component, count(*) FROM pgr_connectedComponents(
                'SELECT id, source, target, 1 AS cost, 1 AS reverse_cost FROM cal_edges
                 WHERE source IS NOT NULL AND target IS NOT NULL')
            GROUP BY component
        """)
        comps = [r[1] for r in cur.fetchall()]
        n_comp = len(comps)
        pct = 100.0 * max(comps) / sum(comps) if comps else 0.0
        cur.execute("""
            INSERT INTO calibracion_tolerancia
                (tolerancia_m, n_vertices, n_componentes, pct_mayor_componente, n_extremos_muertos)
            VALUES (%s, %s, %s, %s, %s)
        """, (tol, n_vertices, n_comp, pct, n_muertos))
        conn.commit()
        filas.append((tol, n_vertices, n_comp, pct, n_muertos))
    cur.execute("DROP TABLE IF EXISTS cal_edges, cal_edges_vertices_pgr CASCADE")
    conn.commit()
    cur.close()
    print("%10s %12s %14s %18s %16s" % ("tol_m", "vertices", "componentes",
                                         "%mayor_comp", "extremos_muertos"))
    for tol, nv, nc, pct, nm in filas:
        print("%10.2f %12d %14d %18.2f %16d" % (tol, nv, nc, pct, nm))
    return filas


def main():
    conn = conectar()
    try:
        calibrar(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
