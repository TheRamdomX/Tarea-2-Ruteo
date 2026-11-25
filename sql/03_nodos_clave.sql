-- Nodos clave (PIT, data centers, aterrizajes, troncales) y enlaces de acceso.
-- Tokens reemplazados por main.py: {{TOL}} (tolerancia m), {{ACCESO}} (max distancia de acceso m).

DROP TABLE IF EXISTS nodos_clave CASCADE;

CREATE TABLE nodos_clave (
    cod text PRIMARY KEY,
    nombre text,
    rol text,
    tipo text,
    fuente text,
    geom geometry(Point,4326),
    vertex_id int,
    dist_acceso_m double precision,
    componente bigint
);

INSERT INTO nodos_clave (cod, nombre, rol, tipo, fuente, geom) VALUES
('SCL_PIT', 'Santiago - PIT Chile / NAP Chile', 'intercambia', 'IXP', 'pitchile.cl; Estudio Yafun s7 (rec. 4: PITs y data centers)', ST_SetSRID(ST_MakePoint(-70.6100, -33.4250), 4326)),
('SCL_DC', 'Quilicura - cluster de data centers (Google y otros)', 'genera', 'data_center', 'Estudio Yafun s7 rec.4; anuncio data center Google Quilicura (2015)', ST_SetSRID(ST_MakePoint(-70.7300, -33.3600), 4326)),
('VAP', 'Valparaiso - aterrizaje SAm-1, SAC, Curie', 'intercambia', 'aterrizaje_cable', 'submarinecablemap.com (capa mapa_cables_submarinos_nodos del Estudio)', ST_SetSRID(ST_MakePoint(-71.6197, -33.0472), 4326)),
('ARI', 'Arica - aterrizaje SAm-1 y Prat', 'intercambia', 'aterrizaje_cable', 'submarinecablemap.com; GTD Prat', ST_SetSRID(ST_MakePoint(-70.3126, -18.4783), 4326)),
('IQQ', 'Iquique - aterrizaje Prat', 'intercambia', 'aterrizaje_cable', 'GTD Prat', ST_SetSRID(ST_MakePoint(-70.1503, -20.2141), 4326)),
('ANF', 'Antofagasta - aterrizaje Prat', 'intercambia', 'aterrizaje_cable', 'GTD Prat', ST_SetSRID(ST_MakePoint(-70.4000, -23.6500), 4326)),
('LSC', 'La Serena - aterrizaje Prat', 'intercambia', 'aterrizaje_cable', 'GTD Prat', ST_SetSRID(ST_MakePoint(-71.2489, -29.9078), 4326)),
('CCP', 'Concepcion/Talcahuano - aterrizaje Prat, hub regional', 'intercambia', 'aterrizaje_cable', 'GTD Prat', ST_SetSRID(ST_MakePoint(-73.0498, -36.8270), 4326)),
('PMC', 'Puerto Montt - aterrizaje Prat y FOA', 'intercambia', 'aterrizaje_cable', 'GTD Prat; SUBTEL Fibra Optica Austral', ST_SetSRID(ST_MakePoint(-72.9360, -41.4717), 4326)),
('PUQ', 'Punta Arenas - aterrizaje FOA', 'intercambia', 'aterrizaje_cable', 'SUBTEL Fibra Optica Austral', ST_SetSRID(ST_MakePoint(-70.9171, -53.1638), 4326)),
('CPO', 'Copiapo', 'recibe', 'troncal_capital', 'Estudio Yafun s5.3 (comunas como nodos)', ST_SetSRID(ST_MakePoint(-70.3314, -27.3665), 4326)),
('RCG', 'Rancagua', 'recibe', 'troncal_capital', 'idem', ST_SetSRID(ST_MakePoint(-70.7443, -34.1708), 4326)),
('TLC', 'Talca', 'recibe', 'troncal_capital', 'idem', ST_SetSRID(ST_MakePoint(-71.6554, -35.4264), 4326)),
('CHL', 'Chillan', 'recibe', 'troncal_capital', 'idem', ST_SetSRID(ST_MakePoint(-72.1033, -36.6066), 4326)),
('TCO', 'Temuco', 'recibe', 'troncal_capital', 'idem', ST_SetSRID(ST_MakePoint(-72.5904, -38.7359), 4326)),
('VLD', 'Valdivia', 'recibe', 'troncal_capital', 'idem', ST_SetSRID(ST_MakePoint(-73.2459, -39.8142), 4326)),
('COY', 'Coyhaique', 'recibe', 'troncal_capital', 'idem', ST_SetSRID(ST_MakePoint(-72.0667, -45.5752), 4326)),
('LAN', 'Los Andes - troncal internacional a Argentina (Cristo Redentor)', 'intercambia', 'troncal_internacional', 'Estudio Yafun s6 (8 puntos limitrofes con Argentina)', ST_SetSRID(ST_MakePoint(-70.5978, -32.8337), 4326));

-- Distancia de cada nodo al vertice de la red (fibra/gap) mas cercano.
DROP TABLE IF EXISTS kn_tmp, kn_near_tmp;
CREATE TEMP TABLE kn_tmp AS
SELECT cod, geom, ST_Transform(geom, 32719) AS g32
FROM nodos_clave;

CREATE TEMP TABLE kn_near_tmp AS
SELECT k.cod, v.id AS vertex_id, ST_Distance(k.g32, v.the_geom) AS dist
FROM kn_tmp k
CROSS JOIN LATERAL (
    SELECT id, the_geom
    FROM edges_vertices_pgr
    ORDER BY the_geom <-> k.g32
    LIMIT 1
) v;

UPDATE nodos_clave n
SET dist_acceso_m = kn.dist
FROM kn_near_tmp kn
WHERE kn.cod = n.cod;

-- Enlaces de acceso (solo si estan dentro de ACCESO y no son coincidentes).
INSERT INTO edges (tipo, sintetico, operativo, id_origen, geom, largo_m)
SELECT 'acceso', true, true, NULL,
       ST_MakeLine(k.g32, v.the_geom),
       n.dist
FROM kn_tmp k
JOIN kn_near_tmp n ON n.cod = k.cod
JOIN edges_vertices_pgr v ON v.id = n.vertex_id
WHERE n.dist <= {{ACCESO}}
  AND n.dist >= 0.5;

SELECT pgr_createTopology('edges', {{TOL}}, 'geom', 'id', 'source', 'target', rows_where := 'source IS NULL', clean := false);

-- Vertice de cada nodo clave: vertice mas cercano al punto (existe tras la topologia).
UPDATE nodos_clave n
SET vertex_id = (
    SELECT v.id
    FROM edges_vertices_pgr v
    ORDER BY v.the_geom <-> ST_Transform(n.geom, 32719)
    LIMIT 1
)
WHERE n.dist_acceso_m <= {{ACCESO}};

-- Recalcular componentes, resumen, componente de nodos clave y grado.
DELETE FROM componentes;
INSERT INTO componentes (vertex_id, componente)
SELECT node, component
FROM pgr_connectedComponents('SELECT id, source, target, 1 AS cost, 1 AS reverse_cost FROM edges WHERE source IS NOT NULL AND target IS NOT NULL');

DROP TABLE IF EXISTS resumen_componentes;
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

UPDATE nodos_clave n
SET componente = c.componente
FROM componentes c
WHERE c.vertex_id = n.vertex_id;

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
