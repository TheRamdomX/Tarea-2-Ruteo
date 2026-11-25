#!/usr/bin/env bash
# Prerrequisitos (una sola vez) en Ubuntu / WSL:
#   PostgreSQL + PostGIS + pgRouting + librerías Python del proyecto.
set -e
sudo apt-get update
PGV=$(ls /usr/lib/postgresql 2>/dev/null | sort -n | tail -1)
PGV=${PGV:-18}
sudo apt-get install -y postgresql postgresql-$PGV-postgis-3 postgresql-$PGV-pgrouting \
     python3-numpy python3-scipy python3-psycopg2 python3-pyshp
sudo service postgresql start || true
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
echo "Listo: PostgreSQL + PostGIS + pgRouting instalados."
