# Local vendor bundle

Downloaded once at development time. The running app imports only these local
files and makes no CDN requests.

| Library | Version | Local entry point | Notes |
|---|---:|---|---|
| three.js | 0.186.1 | `three.module.js` | Also requires its local `three.core.js` split build. It is not loaded by the current 2D UI because the design brief forbids 3D unless explicitly requested. |
| anime.js | 4.5.0 | `anime.esm.min.js` | Used for the 120–200 ms UI motion budget, with reduced-motion support. |
| Chart.js | 4.6.1 | `chart.js` | Uses local `chunks/index.js` and the local `@kurkle/color` ESM dependency below. |
| @kurkle/color | 0.3.4 | `kurkle-color.esm.js` | Required dependency of Chart.js 4.6.1; mapped locally by the import map in `index.html`. |

License texts are in `licenses/`.
