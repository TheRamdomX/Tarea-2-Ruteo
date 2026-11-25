-- Sismo (USGS usp000h7rf) extraido desde sismo_geojson.doc y ruptura estimada.
DROP TABLE IF EXISTS sismo, ruptura CASCADE;

CREATE TABLE sismo (
    evento_id text PRIMARY KEY,
    lugar text,
    mag double precision,
    fecha_utc timestamptz,
    profundidad_km double precision,
    strike_deg double precision,
    fuente_url text,
    geom geometry(Point,4326)
);

INSERT INTO sismo (evento_id, lugar, mag, fecha_utc, profundidad_km, strike_deg, fuente_url, geom)
SELECT
    s.doc->>'id',
    s.doc->'properties'->>'place',
    (s.doc->'properties'->>'mag')::double precision,
    to_timestamp((s.doc->'properties'->>'time')::bigint / 1000.0),
    (s.doc->'geometry'->'coordinates'->>2)::double precision,
    COALESCE(
        (s.doc->'properties'->'products'->'moment-tensor'->0->'properties'->>'nodal-plane-1-strike')::double precision,
        18.0
    ),
    s.fuente_url,
    ST_SetSRID(ST_Force2D(ST_GeomFromGeoJSON(s.doc->>'geometry')), 4326)
FROM sismo_geojson s
ORDER BY s.id DESC
LIMIT 1;

-- Ruptura (Wells & Coppersmith 1994, SRL todos los tipos de falla):
-- log10(L_km) = -2.44 + 0.59 M, centrada en el epicentro (32719), orientada segun el rumbo (strike).
CREATE TABLE ruptura (
    id int PRIMARY KEY,
    largo_km double precision,
    geom geometry(LineString,32719)
);

INSERT INTO ruptura (id, largo_km, geom)
SELECT
    1,
    L_km,
    ST_MakeLine(
        ST_MakePoint(
            ST_X(epi) - L_km / 2.0 * 1000.0 * sin(radians(strike_deg)),
            ST_Y(epi) - L_km / 2.0 * 1000.0 * cos(radians(strike_deg))
        ),
        ST_MakePoint(
            ST_X(epi) + L_km / 2.0 * 1000.0 * sin(radians(strike_deg)),
            ST_Y(epi) + L_km / 2.0 * 1000.0 * cos(radians(strike_deg))
        )
    )
FROM (
    SELECT sm.strike_deg,
           ST_Transform(sm.geom, 32719) AS epi,
           10 ^ (-2.44 + 0.59 * sm.mag) AS L_km
    FROM sismo sm
) s;

CREATE INDEX ruptura_geom_gist ON ruptura USING GIST (geom);
