import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import 'antd/dist/reset.css'
import App from './App'

const container = document.getElementById('root')
if (container === null) {
  throw new Error('找不到 #root 挂载点：index.html 被改坏了？')
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
