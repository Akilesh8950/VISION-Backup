import React, { Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

// The desktop app and the floating Live mini are separate surfaces that
// never need each other's code at startup. Lazy entry points keep the
// mini window from parsing the full chat app, and keep Live's recorder
// code out of the main window's first paint.
const App = lazy(() => import('./App.jsx'))
const LiveMiniApp = lazy(() => import('./LivePanel.jsx').then((module) => ({ default: module.LiveMiniApp })))

const liveMini = new URLSearchParams(window.location.search).has('liveMini')

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Suspense fallback={null}>
      {liveMini ? <LiveMiniApp /> : <App />}
    </Suspense>
  </React.StrictMode>,
)
