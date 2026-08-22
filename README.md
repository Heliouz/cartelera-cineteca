# cartelera

La programación de las tres sedes de Cineteca Nacional (México, Chapultepec, Las
Artes) en un solo lugar: agenda por día, agrupada por sede, con posters y boletos
a un clic. Sitio estático, sin backend — se actualiza dos veces al día vía GitHub
Actions.

Sitio: `docs/` (GitHub Pages, `/docs` en `main`).

## Estructura

```
scraper/        scraper en Python (requests + BeautifulSoup)
docs/           sitio estático (GitHub Pages root)
docs/data/      schedule.json, generado por el scraper
docs/og.png     imagen de preview al compartir cualquier URL del sitio
.github/workflows/scrape.yml   cron 2x/día + workflow_dispatch
```

## Scraper

```bash
cd scraper
pip install -r requirements.txt
python scrape.py
```

Escribe `docs/data/schedule.json`. Aborta (exit 1) sin escribir si más del 20% de
las páginas de detalle fallan, o si el conteo de películas cae por debajo del 50%
de la corrida anterior — ver `scrape.py` para los detalles de las salvaguardas.

### Tests

```bash
cd scraper
pip install pytest
python -m pytest test_parse.py -v
```

Corren offline contra fixtures guardadas en `scraper/fixtures/` (HTML/JSON reales,
capturados de cinetecanacional.net). No hacen requests de red.

## Frontend

Sin build step. Para probar localmente:

```bash
cd docs
python -m http.server 8000
```

Todo el filtrado (día, sede, ciclo, búsqueda) ocurre en el cliente contra el JSON
ya cargado — cero requests adicionales por interacción.

## Despliegue

GitHub Pages, deploy desde `main` / `/docs`. El workflow de scraping commitea
`schedule.json` solo si cambió; ese commit es el deploy — no hay paso de build.

Dominio propio: `cartelera.heliouz.com`, vía registro CNAME en el registrador →
`heliouz.github.io`. `docs/CNAME` ya está commiteado con ese hostname, y
Settings → Pages → Custom domain lo debe reflejar (GitHub añade el check DNS y
provisiona HTTPS automáticamente una vez que el registro propaga).

### Checklist de lanzamiento

- [x] Rama local renombrada a `main`.
- [x] Remote configurado (`github.com/Heliouz/cartelera-cineteca`) y pusheado.
- [ ] Settings → Pages → source `main` / `/docs`.
- [ ] En el registrador: registro CNAME `cartelera` → `heliouz.github.io`.
- [ ] Settings → Pages → Custom domain → `cartelera.heliouz.com`, esperar el
      check DNS y "Enforce HTTPS".
- [x] `docs/index.html`: `og:image`, `twitter:image`, `og:url` y
      `<link rel="canonical">` ya apuntan a `https://cartelera.heliouz.com/`.
- [ ] Verificar la preview con el debugger de cada red antes de compartir.

El workflow abre un issue etiquetado `scrape-failure` si una corrida falla, así
que un scrape roto no pasa desapercibido.

## No oficial

Este sitio no es operado por Cineteca Nacional. Toda compra de boletos ocurre en
el sistema oficial (`cinetecanacional.net` / `rbvfcn.cinetecanacional.net`); este
proyecto solo agrega y presenta la información pública de la cartelera.
