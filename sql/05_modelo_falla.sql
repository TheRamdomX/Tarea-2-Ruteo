-- Modelo de falla: PGA (Youngs et al. 1997, roca) -> PGV -> tasa de reparacion -> p_falla por enlace.
DROP TABLE IF EXISTS modelo_param CASCADE;

CREATE TABLE modelo_param (
    clave text PRIMARY KEY,
    valor double precision,
    descripcion text,
    fuente text
);

INSERT INTO modelo_param (clave, valor, descripcion, fuente) VALUES
('c_youngs_a', 0.2418, 'Youngs: termino constante', 'Youngs et al. 1997, SRL 68(1), roca, interfase'),
('c_youngs_b', 1.414, 'Youngs: pendiente en magnitud', 'Youngs et al. 1997, SRL 68(1), roca, interfase'),
('c_youngs_c3', -2.552, 'Youngs: coeficiente de ln(R)', 'Youngs et al. 1997, SRL 68(1), roca, interfase'),
('c_youngs_d', 1.7818, 'Youngs: saturacion de distancia', 'Youngs et al. 1997, SRL 68(1), roca, interfase'),
('c_youngs_e', 0.554, 'Youngs: exponente de saturacion', 'Youngs et al. 1997, SRL 68(1), roca, interfase'),
('c_youngs_h', 0.00607, 'Youngs: coeficiente de profundidad h (km)', 'Youngs et al. 1997, SRL 68(1), roca, interfase'),
('vel_acel', 121.92, 'cm/s por g (PGV = PGA * vel_acel)', 'Newmark & Hall 1982 (v/a = 48 in/s/g, suelo aluvial)'),
('k_rr', 0.3, 'Multiplicador ductil de tasa de reparacion', 'HAZUS ductile multiplier'),
('rr_coef', 0.0001, 'Coeficiente de tasa de reparacion', 'HAZUS-MH Tech. Manual cap. 8; O''Rourke & Ayala 1993'),
('rr_exp', 2.25, 'Exponente de PGV en tasa de reparacion', 'HAZUS-MH Tech. Manual cap. 8; O''Rourke & Ayala 1993');

CREATE OR REPLACE FUNCTION param_valor(clave text) RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT valor FROM modelo_param WHERE modelo_param.clave = param_valor.clave
$$;

-- PGA en g. r_km: distancia R usada por el modelo; h_km: profundidad.
CREATE OR REPLACE FUNCTION pga_youngs(m double precision, r_km double precision, h_km double precision)
RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT exp(
        param_valor('c_youngs_a')
        + param_valor('c_youngs_b') * m
        + param_valor('c_youngs_c3') * ln(r_km + param_valor('c_youngs_d') * exp(param_valor('c_youngs_e') * m))
        + param_valor('c_youngs_h') * h_km
    )
$$;

-- Probabilidad de falla de un enlace: 1 - exp(-k * 0.0001 * PGV^2.25 * largo_km).
CREATE OR REPLACE FUNCTION p_falla_enlace(pgv double precision, largo_km double precision, k double precision)
RETURNS double precision
LANGUAGE sql STABLE AS $$
    SELECT 1 - exp(-k * param_valor('rr_coef') * power(pgv, param_valor('rr_exp')) * largo_km)
$$;

-- Calculo por enlace.
WITH base AS (
    SELECT e.id,
           ST_Distance(e.geom, r.geom) / 1000.0 AS dist_km,
           s.mag AS m,
           s.profundidad_km AS h,
           e.largo_m
    FROM edges e
    CROSS JOIN ruptura r
    CROSS JOIN LATERAL (SELECT mag, profundidad_km FROM sismo LIMIT 1) s
),
calc AS (
    SELECT b.id,
           b.dist_km,
           sqrt(b.dist_km ^ 2 + b.h ^ 2) AS r_km,
           pga_youngs(b.m, sqrt(b.dist_km ^ 2 + b.h ^ 2), b.h) AS pga,
           b.largo_m
    FROM base b
),
tasas AS (
    SELECT c.id,
           c.dist_km,
           c.pga,
           c.pga * param_valor('vel_acel') AS pgv,
           param_valor('k_rr') * param_valor('rr_coef')
               * power(c.pga * param_valor('vel_acel'), param_valor('rr_exp')) AS rr_km,
           c.largo_m
    FROM calc c
)
UPDATE edges e
SET dist_ruptura_km = t.dist_km,
    pga_g = t.pga,
    pgv_cms = t.pgv,
    rr_km = t.rr_km,
    p_falla = least(1.0, greatest(0.0, 1 - exp(-t.rr_km * t.largo_m / 1000.0)))
FROM tasas t
WHERE t.id = e.id;

CREATE OR REPLACE VIEW v_probabilidad_falla AS
SELECT id, tipo, sintetico, largo_m / 1000.0 AS largo_km, dist_ruptura_km, pga_g, pgv_cms, rr_km, p_falla
FROM edges
ORDER BY p_falla DESC;
