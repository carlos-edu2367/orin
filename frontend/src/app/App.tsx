import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { routes } from './routes'

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        {routes.map((route) => <Route key={route.path} path={route.path} element={route.element} />)}
        <Route path="*" element={routes[0].element} />
      </Routes>
    </BrowserRouter>
  )
}
