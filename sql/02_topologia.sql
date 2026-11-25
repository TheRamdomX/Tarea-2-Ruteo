-- Topologia de la red de fibra en EPSG:32719 (UTM 19S).
-- Tokens reemplazados por main.py: {{TOL}} (tolerancia m), {{GAP}} (max salto de digitalizacion m).

-- 1. Fibra terrestre: partes (LineString) en 32719, excluyendo Isla de Pascua (lon < -100).
DROP TABLE IF EXISTS fibra_lineas, fibra_noded, edges, edges_vertices_pgr, componentes,
    resumen_componentes CASCADE;

CREATE TABLE fibra_lineas (
    id serial PRIMARY KEY,
    id_origen text,
    geom geometry(LineString,32719)
);

INSERT INTO fibra_lineas (id_origen, geom)
SELECT d.id_origen, ST_Transform(d.geom, 32719)
FROM (
    SELECT f.id_origen, (ST_Dump(f.geom)).geom AS geom
    FROM fibra_optica_detectada f
) d
WHERE ST_X(ST_StartPoint(d.geom)) >= -100
  AND ST_Length(ST_Transform(d.geom, 32719)) > 0;

-- 2. Noding exacto con PostGIS: divide lineas en todas las intersecciones (cruces y T).
-- ({{TOL}} no aplica aqui; solo se usa para el snapping de pgr_createTopology.)
CREATE TABLE fibra_noded AS
SELECT (ST_Dump(ST_Node(ST_Collect(geom)))).geom::geometry(LineString,32719) AS geom
FROM fibra_lineas;

CREATE TABLE edges (
    id serial PRIMARY KEY,
    tipo text,
    sintetico boolean DEFAULT false,
    operativo boolean DEFAULT true,
    id_origen text,
    geom geometry(LineString,32719),
    largo_m double precision,
    source integer,
    target integer,
    p_falla double precision NOT NULL DEFAULT 0,
    dist_ruptura_km double precision,
    pga_g double precision,
    pgv_cms double precision,
    rr_km double precision
);

-- id_origen recuperado por el segmento original mas cercano al punto medio del tramo noded.
INSERT INTO edges (tipo, sintetico, operativo, id_origen, geom, largo_m)
SELECT 'fibra', false, true, o.id_origen, n.geom, ST_Length(n.geom)
FROM fibra_noded n
CROSS JOIN LATERAL (
    SELECT l.id_origen
    FROM fibra_lineas l
    ORDER BY l.geom <-> ST_LineInterpolatePoint(n.geom, 0.5)
    LIMIT 1
) o
WHERE ST_Length(n.geom) > 0;

CREATE INDEX edges_geom_gist ON edges USING GIST (geom);

-- 3. Topologia inicial (source/target).
SELECT pgr_createTopology('edges', {{TOL}}, 'geom', 'id', 'source', 'target', clean := true);

-- 4. Componentes conexas actuales (temporal para buscar huecos).
DROP TABLE IF EXISTS comp_tmp;
CREATE TEMP TABLE comp_tmp AS
SELECT node, component
FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost, 1 AS reverse_cost FROM edges WHERE source IS NOT NULL AND target IS NOT NULL');

CREATE INDEX comp_tmp_node_idx ON comp_tmp (node);

-- 5. Enlaces 'gap': para cada vertice de grado 1 (fin de linea), el vertice mas cercano
--    de OTRA componente a distancia <= GAP. Un enlace por par de vertices no ordenado.
CREATE TEMP TABLE deg_tmp AS
SELECT v AS node, count(*) AS d
FROM (
    SELECT source AS v FROM edges WHERE source IS NOT NULL
    UNION ALL
    SELECT target AS v FROM edges WHERE target IS NOT NULL
) x
GROUP BY v;

CREATE TEMP TABLE gap_pares AS
SELECT DISTINCT LEAST(de.node, nn.node) AS a, GREATEST(de.node, nn.node) AS b
FROM deg_tmp de
JOIN edges_vertices_pgr v ON v.id = de.node
JOIN comp_tmp cv ON cv.node = de.node
CROSS JOIN LATERAL (
    SELECT w.id AS node
    FROM edges_vertices_pgr w
    JOIN comp_tmp cw ON cw.node = w.id
    WHERE cw.component <> cv.component
      AND ST_DWithin(w.the_geom, v.the_geom, {{GAP}})
    ORDER BY w.the_geom <-> v.the_geom
    LIMIT 1
) nn
WHERE de.d = 1;

INSERT INTO edges (tipo, sintetico, operativo, id_origen, geom, largo_m)
SELECT 'gap', true, true, NULL,
       ST_MakeLine(va.the_geom, vb.the_geom),
       ST_Distance(va.the_geom, vb.the_geom)
FROM gap_pares g
JOIN edges_vertices_pgr va ON va.id = g.a
JOIN edges_vertices_pgr vb ON vb.id = g.b;

SELECT pgr_createTopology('edges', {{TOL}}, 'geom', 'id', 'source', 'target', rows_where := 'source IS NULL', clean := false);

-- 6. Componentes finales y resumen.
CREATE TABLE componentes (
    vertex_id int PRIMARY KEY,
    componente bigint
);

INSERT INTO componentes (vertex_id, componente)
SELECT node, component
FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost, 1 AS reverse_cost FROM edges WHERE source IS NOT NULL AND target IS NOT NULL');

CREATE TABLE resumen_componentes AS
SELECT v.componente, v.n_vertices, coalesce(a.n_aristas, 0) AS n_aristas, coalesce(a.km, 0) AS km
FROM (
    SELECT componente, count(*) AS n_vertices FROM componentes GROUP BY componente
) v
LEFT JOIN (
    SELECT c.componente, count(*) AS n_aristas, sum(e.largo_m) / 1000.0 AS km
    FROM edges e
    JOIN componentes c ON c.vertex_id = e.source
    GROUP BY c.componente
) a USING (componente);

-- 7. Grado de cada vertice.
ALTER TABLE edges_vertices_pgr ADD COLUMN IF NOT EXISTS grado integer;
UPDATE edges_vertices_pgr vp
SET grado = d.d
FROM (
    SELECT v AS id, count(*) AS d
    FROM (
        SELECT source AS v FROM edges WHERE source IS NOT NULL
        UNION ALL
        SELECT target AS v FROM edges WHERE target IS NOT NULL
    ) x
    GROUP BY v
) d
WHERE d.id = vp.id;
