#!/usr/bin/env bash
# Instala LaTeX en Ubuntu / WSL para compilar informe/informe.tex (pide la clave sudo).
set -e
sudo apt-get install -y --no-install-recommends \
  texlive-latex-base texlive-latex-recommended texlive-latex-extra \
  texlive-fonts-recommended texlive-lang-spanish lmodern latexmk
echo "LaTeX instalado."
