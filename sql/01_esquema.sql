-- Extensiones y tablas crudas (capas de entrada y sismo).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

DROP TABLE IF EXISTS fibra_optica_detectada, trayecto_en_ferry, postacion_electrica_detectada,
    camino_sin_postacion_detectado, sismo_geojson CASCADE;

CREATE TABLE fibra_optica_detectada (
    gid serial PRIMARY KEY,
    id_origen text,
    geom geometry(MultiLineString,4326)
);
CREATE TABLE trayecto_en_ferry (
    gid serial PRIMARY KEY,
    id_origen text,
    geom geometry(MultiLineString,4326)
);
CREATE TABLE postacion_electrica_detectada (
    gid serial PRIMARY KEY,
    id_origen text,
    geom geometry(MultiLineString,4326)
);
CREATE TABLE camino_sin_postacion_detectado (
    gid serial PRIMARY KEY,
    id_origen text,
    geom geometry(MultiLineString,4326)
);

CREATE TABLE sismo_geojson (
    id serial PRIMARY KEY,
    fuente_url text,
    descargado timestamptz DEFAULT now(),
    doc jsonb
);
