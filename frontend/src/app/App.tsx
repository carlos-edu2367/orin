import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { routes } from './routes'
import { UpdateBanner } from '../components/UpdateBanner'

export function App() {
  return (
    <>
      <UpdateBanner />
      <BrowserRouter>
        <Routes>
          {routes.map((route) => <Route key={route.path} path={route.path} element={route.element} />)}
          <Route path="*" element={routes[0].element} />
        </Routes>
      </BrowserRouter>
    </>
  )
}
