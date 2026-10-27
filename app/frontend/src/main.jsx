import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import { LiveMiniApp } from './LivePanel.jsx'
import './styles.css'

const liveMini = new URLSearchParams(window.location.search).has('liveMini')

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {liveMini ? <LiveMiniApp /> : <App />}
  </React.StrictMode>,
)
