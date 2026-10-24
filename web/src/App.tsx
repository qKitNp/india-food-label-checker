import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Home from './pages/Home'
import JobPage from './pages/Job'
import LabelPage from './pages/Label'
import ProductPage from './pages/Product'
import Scan from './pages/Scan'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/scan" element={<Scan />} />
        <Route path="/job/:id" element={<JobPage />} />
        <Route path="/p/:key" element={<ProductPage />} />
        <Route path="/label" element={<LabelPage />} />
        <Route path="*" element={<Home />} />
      </Routes>
    </BrowserRouter>
  )
}
