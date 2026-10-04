#!/bin/sh
# Wrap the artifact page in a full HTML document for GitHub Pages (docs/).
set -e
cd "$(dirname "$0")/.."
{ printf '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
  sed -n '1,/<\/style>/p' web/index.html
  printf '</head><body>\n'
  sed '1,/<\/style>/d' web/index.html
  printf '</body></html>\n'; } > docs/index.html
cp web/data.js docs/data.js
touch docs/.nojekyll
