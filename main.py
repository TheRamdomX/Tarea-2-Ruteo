"""Tarea 2 - Resiliencia de Internet chilena frente al sismo M8.8 (27-02-2010).

Uso (un solo comando):
    python3 main.py                 # equivalente a 'all'
Subcomandos:
    all       pasos 1..8 y servir el mapa
    initdb    pasos 1..6 (carga, topologia, modelo de falla) + dump
    attr      solo ATTR (paso 7)
    mapa      solo exportar capas y servir el mapa (paso 8)
    dump      generar main.sql
    calibrar  calibracion de tolerancia de pgr_createTopology
Opciones:
    -n N        iteraciones Monte Carlo para ATTR (por defecto 10000)
    --no-serve  con 'all': ejecuta todo pero no sirve el mapa

Variables de entorno: PGHOST (localhost), PGPORT (5432), PGUSER (postgres),
PGPASSWORD (postgres), T2_DB (resiliencia).
"""
import argparse
import csv
import functools
import http.server
import json
import os
import subprocess
import sys
import urllib.request
import webbrowser
from datetime import datetime

import shapefile
from psycopg2 import sql
from psycopg2.extras import execute_values

RAIZ = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(RAIZ, "scripts")
SQL_DIR = os.path.join(RAIZ, "sql")
DATA_DIR = os.path.join(RAIZ, "data")
WEB_DIR = os.path.join(RAIZ, "web")
WEB_DATA = os.path.join(WEB_DIR, "data")
RESULT_DIR = os.path.join(RAIZ, "resultados")
sys.path.insert(0, SCRIPTS)

import attr as modulo_attr  # noqa: E402
import calibrar_tolerancia  # noqa: E402
import exportar_main_sql  # noqa: E402
from attr import conectar  # noqa: E402

# Parametros de topologia (metros).
TOLERANCIA_M = float(os.environ.get("T2_TOL", 5.0))      # snapping de pgr_createTopology (m); ver calibracion
GAP_M = float(os.environ.get("T2_GAP", 300.0))           # max salto de digitalizacion a cerrar con enlace 'gap'
ACCESO_M = 20000.0      # max distancia de enlace de acceso a un nodo clave

PUERTO_WEB = 8000
ITER_DEFECTO = 10000
SEED = 2010

CAPAS = [
    ("fibra_optica_detectada", "fibra_optica_detectada.shp"),
    ("trayecto_en_ferry", "trayecto_en_ferry.shp"),
    ("postacion_electrica_detectada", "postacion_electrica_detectada.shp"),
    ("camino_sin_postacion_detectado", "camino_sin_postacion_detectado.shp"),
]
SISMO_ARCHIVO = os.path.join(DATA_DIR, "usgs_usp000h7rf.geojson")
SISMO_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=usp000h7rf&format=geojson"


def titulo(texto):
    print("\n" + "=" * 70)
    print(texto)
    print("=" * 70)


def ejecutar_sql(conn, archivo, tokens=None):
    """Ejecuta un archivo SQL (sin meta-comandos psql), reemplazando tokens como {{TOL}}."""
    with open(os.path.join(SQL_DIR, archivo), encoding="utf-8") as f:
        texto = f.read()
    for clave, valor in (tokens or {}).items():
        texto = texto.replace(clave, valor)
    cur = conn.cursor()
    try:
        cur.execute(texto)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    print("  SQL ejecutado: sql/%s" % archivo)


def tokens_topologia():
    return {
        "{{TOL}}": repr(float(TOLERANCIA_M)),
        "{{GAP}}": repr(float(GAP_M)),
        "{{ACCESO}}": repr(float(ACCESO_M)),
    }


def consulta(conn, sql_texto, params=None):
    cur = conn.cursor()
    cur.execute(sql_texto, params)
    filas = cur.fetchall()
    cur.close()
    return filas


def imprimir_tabla(cabecera, filas):
    anchos = [len(str(c)) for c in cabecera]
    for fila in filas:
        for i, v in enumerate(fila):
            anchos[i] = max(anchos[i], len(str(v)))
    formato = "  ".join("%%-%ds" % a for a in anchos)
    print(formato % tuple(cabecera))
    print(formato % tuple("-" * a for a in anchos))
    for fila in filas:
        print(formato % tuple(fila))


# ---------------------------------------------------------------- pasos

def paso_recrear_bd():
    titulo("Paso 1: recrear base de datos")
    nombre = os.environ.get("T2_DB", "resiliencia")
    admin = conectar(dbname="postgres")
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nombre)))
    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nombre)))
    cur.close()
    admin.close()
    print("  base de datos '%s' creada" % nombre)


def paso_esquema(conn):
    titulo("Paso 2: esquema y compatibilidad pgRouting")
    ejecutar_sql(conn, "01_esquema.sql")
    ejecutar_sql(conn, "00_compat_pgrouting.sql")


def paso_capas(conn):
    titulo("Paso 3: cargar shapefiles")
    cur = conn.cursor()
    for tabla, archivo in CAPAS:
        ruta = os.path.join(DATA_DIR, "shp", archivo)
        lector = shapefile.Reader(ruta)
        filas = []
        for sr in lector.iterShapeRecords():
            if not sr.shape.points:
                continue
            geo = json.dumps(sr.shape.__geo_interface__)
            filas.append((str(sr.record["id"]), geo))
        execute_values(
            cur,
            sql.SQL("INSERT INTO {} (id_origen, geom) VALUES %s").format(sql.Identifier(tabla)).as_string(conn),
            filas,
            template="(%s, ST_Multi(ST_SetSRID(ST_Force2D(ST_GeomFromGeoJSON(%s)), 4326)))",
        )
        cur.execute("SELECT count(*), coalesce(sum(ST_Length(geom::geography)), 0) / 1000.0 "
                    "FROM " + tabla)
        n, km = cur.fetchone()
        conn.commit()
        print("  %-32s %6d registros  %12.1f km" % (tabla, n, km))
    cur.close()


def paso_sismo(conn):
    titulo("Paso 4: cargar sismo (USGS usp000h7rf)")
    if not os.path.exists(SISMO_ARCHIVO):
        print("  archivo no encontrado, descargando desde USGS...")
        urllib.request.urlretrieve(SISMO_URL, SISMO_ARCHIVO)
    with open(SISMO_ARCHIVO, encoding="utf-8") as f:
        texto = f.read()
    cur = conn.cursor()
    cur.execute("INSERT INTO sismo_geojson (fuente_url, doc) VALUES (%s, %s::jsonb)",
                (SISMO_URL, texto))
    conn.commit()
    cur.close()
    ejecutar_sql(conn, "04_sismo.sql")
    filas = consulta(conn, "SELECT evento_id, lugar, mag, fecha_utc, profundidad_km, strike_deg, "
                           "ST_AsText(geom), ruptura_km FROM (SELECT s.*, "
                           "(SELECT largo_km FROM ruptura LIMIT 1) AS ruptura_km FROM sismo s) x")
    imprimir_tabla(["evento", "lugar", "mag", "fecha_utc", "prof_km", "strike", "geom", "ruptura_km"],
                   filas)


def paso_topologia(conn):
    titulo("Paso 5: topologia y nodos clave")
    ejecutar_sql(conn, "02_topologia.sql", tokens_topologia())
    ejecutar_sql(conn, "03_nodos_clave.sql", tokens_topologia())
    print("  Enlaces por tipo:")
    imprimir_tabla(["tipo", "n_aristas", "km"], consulta(conn,
        "SELECT tipo, count(*), round((sum(largo_m)/1000.0)::numeric, 1) FROM edges GROUP BY tipo ORDER BY tipo"))
    n_v = consulta(conn, "SELECT count(*) FROM edges_vertices_pgr")[0][0]
    print("  Vertices: %d" % n_v)
    comp = consulta(conn, "SELECT count(*), coalesce(max(n_vertices), 0) FROM resumen_componentes")[0]
    print("  Componentes: %d   mayor: %d vertices (%.1f %%)" % (
        comp[0], comp[1], 100.0 * comp[1] / n_v if n_v else 0.0))
    aislados = consulta(conn, "SELECT count(*) FROM nodos_clave WHERE vertex_id IS NULL")[0][0]
    print("  Nodos clave aislados (acceso > %.0f m): %d" % (ACCESO_M, aislados))
    print("  Nodos clave:")
    imprimir_tabla(["cod", "nombre", "rol", "vertex_id", "componente", "dist_acceso_m"], consulta(conn,
        "SELECT cod, nombre, rol, vertex_id, componente, round(dist_acceso_m::numeric, 1) "
        "FROM nodos_clave ORDER BY cod"))


def paso_modelo(conn):
    titulo("Paso 6: modelo de falla")
    ejecutar_sql(conn, "05_modelo_falla.sql")
    cols = ("id", "tipo", "sintetico", "largo_km", "dist_ruptura_km", "pga_g", "pgv_cms", "p_falla")
    filas_sql = ("SELECT id, tipo, sintetico, round((largo_m/1000.0)::numeric, 2), "
                 "round(dist_ruptura_km::numeric, 1), round(pga_g::numeric, 4), "
                 "round(pgv_cms::numeric, 2), round(p_falla::numeric, 4) FROM edges "
                 "WHERE operativo AND largo_m >= 1000 ORDER BY p_falla %s, id LIMIT %s")
    print("  Top-10 (mayor p_falla, enlaces >= 1 km):")
    imprimir_tabla(cols, consulta(conn, filas_sql % ("DESC", 10)))
    print("  Bottom-5 (menor p_falla, enlaces >= 1 km):")
    imprimir_tabla(cols, consulta(conn, filas_sql % ("ASC", 5)))
    res = consulta(conn, "SELECT count(*) FILTER (WHERE p_falla > 0.5), avg(p_falla) "
                         "FROM edges WHERE operativo")[0]
    print("  Enlaces con p_falla > 0.5: %d   media p_falla: %.6f" % (res[0], res[1] or 0.0))
    print("  p_falla por banda de distancia a la ruptura:")
    imprimir_tabla(("banda_km", "n_enlaces", "km", "pga_media_g", "p_falla_media", "p_falla_max"), consulta(conn,
        "SELECT CASE WHEN dist_ruptura_km < 50 THEN '0-50' WHEN dist_ruptura_km < 100 THEN '50-100' "
        "WHEN dist_ruptura_km < 200 THEN '100-200' WHEN dist_ruptura_km < 400 THEN '200-400' ELSE '>400' END b, "
        "count(*), round(sum(largo_m)::numeric/1000, 0), round(avg(pga_g)::numeric, 3), "
        "round(avg(p_falla)::numeric, 4), round(max(p_falla)::numeric, 4) FROM edges WHERE operativo "
        "GROUP BY b ORDER BY min(dist_ruptura_km)"))


def paso_dump():
    titulo("Dump: generar main.sql")
    subprocess.run([sys.executable, os.path.join(SCRIPTS, "exportar_main_sql.py")], check=True, cwd=RAIZ)


def paso_attr(conn, n_iter):
    titulo("Paso 7: ATTR (operacion vs sismo), N=%d, seed=%d" % (n_iter, SEED))
    resultados = {}
    for esc in ("operacion", "sismo"):
        src, tgt, p, claves = modulo_attr.cargar_grafo(conn, esc)
        print("  Escenario %s: %d enlaces, %d nodos clave" % (esc, len(src), len(claves)))
        resultados[esc] = modulo_attr.attr(src, tgt, p, claves, n_iter=n_iter, seed=SEED)
        r = resultados[esc]
        print("    ATTR = %.6f   IC95 [%.6f, %.6f]   %s" % (r["attr"], r["ic95"][0], r["ic95"][1], r["metodo"]))
    a = resultados["operacion"]["attr"]
    b = resultados["sismo"]["attr"]
    dif = 100.0 * (b - a) / a if a else float("nan")
    print("  Diferencia (sismo - operacion): %.6f   (%.2f %%)" % (b - a, dif))

    os.makedirs(RESULT_DIR, exist_ok=True)
    claves = modulo_attr.cargar_grafo(conn, "operacion")[3]
    with open(os.path.join(RESULT_DIR, "attr_pares.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["escenario", "cod_i", "cod_j", "p_conexion"])
        for esc in ("operacion", "sismo"):
            pares = resultados[esc]["pares"]
            for i, (ci, _) in enumerate(claves):
                for j, (cj, _) in enumerate(claves):
                    if i != j:
                        w.writerow([esc, ci, cj, "%.6f" % pares[i][j]])
    resumen = {}
    for esc in ("operacion", "sismo"):
        r = resultados[esc]
        resumen[esc] = {"attr": r["attr"], "ic95": list(r["ic95"]), "metodo": r["metodo"],
                        "n_iter": r["n_iter"]}
    resumen["diferencia_abs"] = b - a
    resumen["diferencia_pct"] = dif
    resumen["generado"] = datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(RESULT_DIR, "attr.json"), "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)

    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS resultados_attr (
            escenario text, attr double precision, ic95_inf double precision,
            ic95_sup double precision, n_iter integer, metodo text,
            ts timestamptz DEFAULT now()
        )""")
    cur.execute("DELETE FROM resultados_attr")
    for esc in ("operacion", "sismo"):
        r = resultados[esc]
        cur.execute("INSERT INTO resultados_attr (escenario, attr, ic95_inf, ic95_sup, n_iter, metodo) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (esc, r["attr"], r["ic95"][0], r["ic95"][1], r["n_iter"], r["metodo"]))
    conn.commit()
    cur.close()
    print("  Resultados: resultados/attr_pares.csv, resultados/attr.json, tabla resultados_attr")


def _feature(geom_texto, props):
    return {"type": "Feature", "geometry": json.loads(geom_texto), "properties": props}


def _escribir(nombre, obj):
    os.makedirs(WEB_DATA, exist_ok=True)
    with open(os.path.join(WEB_DATA, nombre), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    print("  web/data/%s (%d bytes)" % (nombre, os.path.getsize(os.path.join(WEB_DATA, nombre))))


def _conectividad_media():
    """Media de p_conexion de cada nodo con los demas nodos clave, por escenario."""
    suma = {}
    cuenta = {}
    ruta = os.path.join(RESULT_DIR, "attr_pares.csv")
    with open(ruta, encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            clave = (fila["escenario"], fila["cod_i"])
            suma[clave] = suma.get(clave, 0.0) + float(fila["p_conexion"])
            cuenta[clave] = cuenta.get(clave, 0) + 1
    return {k: suma[k] / cuenta[k] for k in suma}


def paso_mapa(conn):
    titulo("Paso 8: exportar capas del mapa")
    if not (os.path.exists(os.path.join(RESULT_DIR, "attr_pares.csv"))
            and os.path.exists(os.path.join(RESULT_DIR, "attr.json"))):
        print("  resultados ATTR no encontrados: ejecutando paso 7")
        paso_attr(conn, ITER_DEFECTO)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, tipo, sintetico, largo_m / 1000.0, pga_g, p_falla,
               ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(geom, 20), 4326), 6)
        FROM edges WHERE operativo""")
    enlaces = [_feature(g, {"id": i, "tipo": t, "sintetico": s, "largo_km": round(l, 4),
                            "pga_g": pg, "p_falla": pf})
               for i, t, s, l, pg, pf, g in cur.fetchall()]
    _escribir("enlaces.geojson", {"type": "FeatureCollection", "features": enlaces})

    cur.execute("""
        SELECT capa, id_origen,
               ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(ST_Transform(geom, 32719), 50), 4326), 6)
        FROM (
            SELECT 'trayecto_en_ferry' AS capa, id_origen, geom FROM trayecto_en_ferry
            UNION ALL
            SELECT 'postacion_electrica_detectada', id_origen, geom FROM postacion_electrica_detectada
            UNION ALL
            SELECT 'camino_sin_postacion_detectado', id_origen, geom FROM camino_sin_postacion_detectado
        ) x""")
    potencial = [_feature(g, {"capa": c, "id_origen": o}) for c, o, g in cur.fetchall()]
    _escribir("potencial.geojson", {"type": "FeatureCollection", "features": potencial})

    conect = _conectividad_media()
    cur.execute("SELECT cod, nombre, rol, tipo, fuente, vertex_id, componente, "
                "ST_AsGeoJSON(geom, 6) FROM nodos_clave ORDER BY cod")
    nodos = []
    for cod, nom, rol, tipo, fuente, vid, comp, g in cur.fetchall():
        nodos.append(_feature(g, {
            "cod": cod, "nombre": nom, "rol": rol, "tipo": tipo, "fuente": fuente,
            "vertex_id": vid, "componente": comp,
            "conectividad_operacion": conect.get(("operacion", cod), 0.0),
            "conectividad_sismo": conect.get(("sismo", cod), 0.0),
        }))
    _escribir("nodos_clave.geojson", {"type": "FeatureCollection", "features": nodos})

    cur.execute("SELECT evento_id, lugar, mag, fecha_utc, profundidad_km, ST_AsGeoJSON(geom, 6) "
                "FROM sismo")
    sismo = [_feature(g, {"evento_id": ev, "mag": m, "place": lu,
                          "time": f.isoformat() if f else None, "depth_km": p})
             for ev, lu, m, f, p, g in cur.fetchall()]
    _escribir("sismo.geojson", {"type": "FeatureCollection", "features": sismo})

    cur.execute("SELECT id, largo_km, ST_AsGeoJSON(ST_Transform(geom, 4326), 6) FROM ruptura")
    rupt = [_feature(g, {"id": i, "largo_km": round(l, 1)}) for i, l, g in cur.fetchall()]
    _escribir("ruptura.geojson", {"type": "FeatureCollection", "features": rupt})
    cur.close()

    with open(os.path.join(RESULT_DIR, "attr.json"), encoding="utf-8") as f:
        datos_attr = json.load(f)
    _escribir("attr.json", datos_attr)


def servir_mapa():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WEB_DIR)
    httpd = http.server.ThreadingHTTPServer(("localhost", PUERTO_WEB), handler)
    url = "http://localhost:%d" % PUERTO_WEB
    print("\nMapa disponible en %s  (Ctrl+C para detener)" % url)
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        httpd.server_close()


def paso_calibrar(conn):
    titulo("Calibracion de tolerancia")
    calibrar_tolerancia.calibrar(conn)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Resiliencia Internet chilena - sismo 8.8 (2010)")
    ap.add_argument("comando", nargs="?", default="all",
                    choices=["all", "initdb", "attr", "mapa", "dump", "calibrar"])
    ap.add_argument("-n", type=int, default=ITER_DEFECTO, help="iteraciones Monte Carlo")
    ap.add_argument("--no-serve", action="store_true", help="no servir el mapa (solo con 'all')")
    args = ap.parse_args()

    if args.comando == "dump":
        paso_dump()
        return 0

    conn = None
    try:
        if args.comando in ("all", "initdb"):
            paso_recrear_bd()
            conn = conectar()
            paso_esquema(conn)
            paso_capas(conn)
            paso_sismo(conn)
            paso_topologia(conn)
            paso_modelo(conn)
            paso_dump()
            if args.comando == "initdb":
                return 0
            paso_attr(conn, args.n)
            paso_mapa(conn)
            if args.no_serve:
                return 0
            servir_mapa()
            return 0

        conn = conectar()
        if args.comando == "attr":
            paso_attr(conn, args.n)
        elif args.comando == "mapa":
            paso_mapa(conn)
            servir_mapa()
        elif args.comando == "calibrar":
            paso_calibrar(conn)
    except Exception as exc:
        print("\nERROR: %s" % exc, file=sys.stderr)
        print("Verifique que PostgreSQL (con PostGIS y pgRouting) este en ejecucion y que las "
              "variables PGHOST/PGPORT/PGUSER/PGPASSWORD sean correctas.", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
